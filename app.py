import json
import os
import time
import hashlib
import hmac
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.auth_store import AuthStore
from services.case_processing_service import CaseProcessingService
from services.config_store import ConfigStore
from services.dataset_content_store import DatasetContentStore
from services.douyin_media_store import DouyinMediaStore
from services.douyin_resolver import DouyinResolver
from services.douyin_understander import DouyinUnderstander
from services.run_store import RunStore
from services.task_execution_service import TaskExecutionService
from services.training_runner import TrainingRunner


BASE_DIR = Path(__file__).resolve().parent
AUTH_EXEMPT_PATHS = {"/login", "/register", "/auth/login", "/auth/register"}
AUTH_EXEMPT_PREFIXES = ("/static",)
AUTH_COOKIE_NAME = "agent_session"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 14

app = FastAPI(title="Agent 训练台")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
store = ConfigStore(BASE_DIR / "config.yaml")
auth_store = AuthStore(BASE_DIR)
dataset_store = DatasetContentStore(BASE_DIR)
douyin_resolver = DouyinResolver()
douyin_media_store = DouyinMediaStore(dataset_store)
douyin_understander = DouyinUnderstander(store.get_settings()["model_name"])
run_store = RunStore(BASE_DIR)
training_runner = TrainingRunner(BASE_DIR)
training_runner.run_store = run_store
case_processing_service = CaseProcessingService(BASE_DIR)
task_execution_service = TaskExecutionService(BASE_DIR)


def _bool_from_form(value):
    return value == "on"


def _cookie_secret():
    return os.getenv("APP_SECRET_KEY", "agent-training-dev-secret").encode("utf-8")


def _redirect_with_query(path: str, **params):
    payload = {key: value for key, value in params.items() if value not in (None, "")}
    if payload:
        return RedirectResponse(f"{path}?{urlencode(payload, doseq=True)}", status_code=303)
    return RedirectResponse(path, status_code=303)


def _safe_next_path(raw_path: Optional[str]):
    path = str(raw_path or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/learning"
    return path or "/learning"


def _sign_session_token(user_id):
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}"
    signature = hmac.new(_cookie_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def _decode_session_token(token):
    try:
        user_id_text, timestamp_text, signature = str(token or "").split(":", 2)
        payload = f"{user_id_text}:{timestamp_text}"
        expected = hmac.new(_cookie_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if int(time.time()) - int(timestamp_text) > AUTH_COOKIE_MAX_AGE:
            return None
        return int(user_id_text)
    except Exception:
        return None


def _auth_settings():
    auth_config = store.load().get("auth", {}) or {}
    usernames = auth_config.get("admin_usernames") or []
    return {
        "admin_usernames": usernames,
    }


def _sync_expert_registry():
    config = store.load() or {}
    students = config.get("students", []) or []
    auth_store.sync_experts([item.get("config_key") for item in students])


@app.on_event("startup")
def startup_sync():
    _sync_expert_registry()
    run_store.reconcile_incomplete_runs()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    user = None
    user_id = _decode_session_token(request.cookies.get(AUTH_COOKIE_NAME))
    if user_id:
        user = auth_store.get_user_by_id(user_id)
    request.state.user = user

    path = request.url.path
    is_exempt = path in AUTH_EXEMPT_PATHS or any(path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES)
    if is_exempt:
        return await call_next(request)

    if not user:
        accept = request.headers.get("accept", "")
        if "application/json" in accept or "text/event-stream" in accept:
            return JSONResponse({"error": "请先登录"}, status_code=401)
        return _redirect_with_query("/login", next=path)

    return await call_next(request)


def current_user(request: Request):
    return getattr(request.state, "user", None)


def _user_is_admin(user):
    return bool(user and user.get("role") == "admin")


def _expert_permissions(user, config_key, owner_record=None):
    owner_record = owner_record or auth_store.get_expert_record(config_key) or {}
    can_edit = auth_store.can_edit_expert(user, config_key)
    return {
        "can_edit": can_edit,
        "is_owner": bool(user and owner_record.get("owner_user_id") == user.get("id")),
        "is_admin": _user_is_admin(user),
        "read_only_reason": None if can_edit else "你可以查看这个专家，但只有创建者或管理员可以修改。",
    }


def _decorate_students(students, user):
    _sync_expert_registry()
    records = auth_store.list_expert_records([item.get("config_key") for item in students])
    decorated = []
    for item in students:
        config_key = item.get("config_key")
        owner = records.get(config_key) or {
            "owner_label": "系统",
            "owner_user_id": None,
            "owner_username": None,
            "owner_role": None,
            "created_source": "system",
        }
        payload = dict(item)
        payload["owner"] = owner
        payload["can_edit"] = auth_store.can_edit_expert(user, config_key)
        payload["is_owner"] = bool(user and owner.get("owner_user_id") == user.get("id"))
        decorated.append(payload)
    return decorated


def _decorate_runs(runs):
    records = auth_store.list_expert_records([item.get("config_key") for item in runs])
    decorated = []
    for item in runs:
        payload = dict(item)
        payload["owner"] = records.get(item.get("config_key")) or {"owner_label": "系统"}
        decorated.append(payload)
    return decorated


def _decorate_executions(executions):
    records = auth_store.list_expert_records([item.get("config_key") for item in executions])
    decorated = []
    for item in executions:
        payload = dict(item)
        payload["owner"] = records.get(item.get("config_key")) or {"owner_label": "系统"}
        decorated.append(payload)
    return decorated


def render_page(request: Request, template_name: str, **context):
    user = current_user(request)
    students = _decorate_students(store.list_student_workspaces(), user)
    payload = {
        "request": request,
        "current_user": user,
        "is_admin": _user_is_admin(user),
        "nav_students": students,
        "active_config_key": context.get("active_config_key"),
    }
    payload.update(context)
    return templates.TemplateResponse(template_name, payload)


def _require_admin_page(request: Request):
    user = current_user(request)
    if _user_is_admin(user):
        return None
    return _redirect_with_query("/learning", error="仅管理员可以操作系统设置")


def _require_expert_editor_page(request: Request, config_key: str):
    user = current_user(request)
    if auth_store.can_edit_expert(user, config_key):
        return None
    return _redirect_with_query(f"/students/{config_key}", error="你只能修改自己创建的专家")


def _require_expert_editor_api(request: Request, config_key: str):
    user = current_user(request)
    if auth_store.can_edit_expert(user, config_key):
        return None
    return JSONResponse({"error": "你只能修改自己创建的专家"}, status_code=403)


@app.get("/login")
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/learning", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "page_title": "登录",
            "error": request.query_params.get("error"),
            "next": _safe_next_path(request.query_params.get("next")),
        },
    )


@app.post("/auth/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/learning"),
):
    auth_settings = _auth_settings()
    user = auth_store.authenticate(username, password, admin_usernames=auth_settings["admin_usernames"])
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "page_title": "登录",
                "error": "用户名或密码错误",
                "next": _safe_next_path(next),
            },
            status_code=400,
        )
    response = RedirectResponse(_safe_next_path(next), status_code=303)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _sign_session_token(user["id"]),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/register")
def register_page(request: Request):
    if current_user(request):
        return RedirectResponse("/learning", status_code=303)
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "page_title": "注册",
            "error": request.query_params.get("error"),
            "next": _safe_next_path(request.query_params.get("next")),
        },
    )


@app.post("/auth/register")
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    next: str = Form("/learning"),
):
    if password != confirm_password:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "page_title": "注册",
                "error": "两次输入的密码不一致",
                "next": _safe_next_path(next),
            },
            status_code=400,
        )

    auth_settings = _auth_settings()
    try:
        user = auth_store.register_user(
            username,
            password,
            admin_usernames=auth_settings["admin_usernames"],
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "page_title": "注册",
                "error": str(exc),
                "next": _safe_next_path(next),
            },
            status_code=400,
        )

    response = RedirectResponse(_safe_next_path(next), status_code=303)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _sign_session_token(user["id"]),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response


@app.get("/")
def home():
    return RedirectResponse("/learning", status_code=303)


@app.get("/learning")
def learning_page(request: Request):
    user = current_user(request)
    students = _decorate_students(store.list_student_workspaces(), user)
    stats = {
        "student_count": len(students),
        "students_with_cases": len([item for item in students if item["case_count"] > 0]),
        "recent_updates": len([item for item in students if item["recent_training_time"]]),
        "editable_count": len([item for item in students if item["can_edit"]]),
    }
    return render_page(
        request,
        "index.html",
        students=students,
        stats=stats,
        created=request.query_params.get("created"),
        deleted=request.query_params.get("deleted"),
        error=request.query_params.get("error"),
        page_title="学习中心",
    )


@app.get("/tasks")
def tasks_page(request: Request):
    user = current_user(request)
    students = _decorate_students(store.list_student_workspaces(), user)
    executions = _decorate_executions(task_execution_service.list_executions(limit=20))
    editable_students = [item for item in students if item["can_edit"]]
    return render_page(
        request,
        "tasks.html",
        students=students,
        editable_students=editable_students,
        executions=executions,
        latest_execution=executions[0] if executions else None,
        page_title="任务执行",
    )


@app.get("/settings")
def settings_page(request: Request):
    forbidden = _require_admin_page(request)
    if forbidden:
        return forbidden
    auth_settings = _auth_settings()
    return render_page(
        request,
        "settings.html",
        settings=store.get_settings(),
        saved=request.query_params.get("saved"),
        auth_settings=auth_settings,
        page_title="系统设置",
    )


@app.get("/runs")
def runs_page(request: Request):
    runs = _decorate_runs(run_store.list_runs())
    return render_page(
        request,
        "runs.html",
        runs=runs,
        page_title="训练记录",
    )


@app.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str):
    run = run_store.get(run_id)
    if not run:
        return _redirect_with_query("/runs", missing=1)
    run["owner"] = auth_store.get_expert_record(run.get("config_key")) or {"owner_label": "系统"}
    return render_page(
        request,
        "run_detail.html",
        run=run,
        page_title=f"训练详情 · {run.get('student_name', '')}",
        active_config_key=run.get("config_key"),
    )


@app.get("/runs/{run_id}/status")
async def run_status(run_id: str):
    run = run_store.get(run_id)
    if not run:
        return JSONResponse({"error": "未找到训练任务"}, status_code=404)
    return JSONResponse({"run": run})


@app.get("/runs/{run_id}/events/stream")
async def run_events_stream(run_id: str):
    run = run_store.get(run_id)
    if not run:
        return JSONResponse({"error": "未找到训练任务"}, status_code=404)

    def event_generator():
        sent_count = 0
        while True:
            events = run_store.load_events(run_id)
            while sent_count < len(events):
                event = events[sent_count]
                sent_count += 1
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            current_run = run_store.get(run_id) or {}
            if current_run.get("status") in {"completed", "failed"}:
                break
            time.sleep(0.5)

        yield f"data: {json.dumps({'event': 'stream_closed', 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/settings")
async def save_settings(
    request: Request,
    model_name: str = Form(...),
    search_enabled: Optional[str] = Form(None),
    search_num_results: int = Form(...),
    memory_enabled: Optional[str] = Form(None),
    memory_auto_save: Optional[str] = Form(None),
    memory_dir: str = Form(...),
):
    forbidden = _require_admin_page(request)
    if forbidden:
        return forbidden
    store.update_settings({
        "model_name": model_name,
        "search_enabled": _bool_from_form(search_enabled),
        "search_num_results": search_num_results,
        "memory_enabled": _bool_from_form(memory_enabled),
        "memory_auto_save": _bool_from_form(memory_auto_save),
        "memory_dir": memory_dir,
    })
    return _redirect_with_query("/settings", saved=1)


@app.get("/students/{config_key}")
def student_workspace(request: Request, config_key: str):
    workspace = store.get_student_workspace(config_key)
    if not workspace:
        return _redirect_with_query("/learning", error="未找到 expert")

    owner = auth_store.get_expert_record(config_key) or {
        "owner_label": "系统",
        "owner_user_id": None,
        "owner_username": None,
        "created_source": "system",
    }
    workspace["owner"] = owner
    workspace["permissions"] = _expert_permissions(current_user(request), config_key, owner)

    return render_page(
        request,
        "student_workspace.html",
        workspace=workspace,
        saved=request.query_params.get("saved"),
        uploaded=request.query_params.get("uploaded"),
        deleted=request.query_params.get("deleted"),
        error=request.query_params.get("error"),
        page_title=workspace["student"]["name"],
        active_config_key=config_key,
    )


@app.post("/experts/create")
async def create_expert(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    config_key: str = Form(...),
    default_prompt: str = Form(""),
):
    user = current_user(request)
    try:
        student = store.create_student({
            "name": name,
            "role": role,
            "config_key": config_key,
            "default_prompt": default_prompt,
        })
        auth_store.assign_expert_owner(student["config_key"], user["id"], created_source="user")
        return _redirect_with_query(f"/students/{student['config_key']}", saved="created")
    except Exception as exc:
        return _redirect_with_query("/learning", error=str(exc))


@app.post("/experts/{config_key}/delete")
async def delete_expert(request: Request, config_key: str):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    try:
        store.delete_student(config_key)
        auth_store.delete_expert(config_key)
        return _redirect_with_query("/learning", deleted=1)
    except Exception as exc:
        return _redirect_with_query("/learning", error=str(exc))


@app.post("/tasks/execute")
async def start_task_execution(
    request: Request,
    config_key: str = Form(...),
    gids_text: str = Form(...),
):
    forbidden = _require_expert_editor_api(request, config_key)
    if forbidden:
        return forbidden
    try:
        gids = [item.strip() for item in gids_text.splitlines() if item.strip()]
        execution = task_execution_service.start(config_key, gids)
        return JSONResponse(execution)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/tasks/executions/{execution_id}")
async def get_task_execution(execution_id: str):
    execution = task_execution_service.get(execution_id)
    if not execution:
        return JSONResponse({"error": "未找到执行任务"}, status_code=404)
    return JSONResponse({"execution": execution})


@app.get("/tasks/executions/{execution_id}/download")
async def download_task_execution(execution_id: str):
    execution = task_execution_service.get(execution_id)
    if not execution:
        return _redirect_with_query("/tasks", missing=1)

    if not execution.get("download_path"):
        return _redirect_with_query("/tasks", missing=1)

    path = BASE_DIR / execution["download_path"]
    if not path.exists():
        return _redirect_with_query("/tasks", missing=1)

    filename = f"{execution_id}.csv"
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.post("/students/{config_key}/basic")
async def save_student_basic(
    request: Request,
    config_key: str,
    name: str = Form(...),
    role: str = Form(...),
    prompt_file: str = Form(...),
    default_prompt: str = Form(""),
):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    store.update_student_basic(config_key, {
        "name": name,
        "role": role,
        "prompt_file": prompt_file,
        "default_prompt": default_prompt,
    })
    return _redirect_with_query(f"/students/{config_key}", saved="basic")


@app.post("/students/{config_key}/training")
async def save_training_config(
    request: Request,
    config_key: str,
    role: str = Form(...),
    max_iterations: int = Form(...),
    topic_name: List[str] = Form([]),
    topic_description: List[str] = Form([]),
    topic_questions: List[str] = Form([]),
):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    topics = []
    for index, name in enumerate(topic_name):
        title = name.strip()
        description = topic_description[index].strip() if index < len(topic_description) else ""
        questions_text = topic_questions[index] if index < len(topic_questions) else ""
        questions = [item.strip() for item in questions_text.splitlines() if item.strip()]

        if not title and not description and not questions:
            continue

        topics.append({
            "name": title or f"Topic {index + 1}",
            "description": description,
            "questions": questions,
        })
    store.update_training_config(config_key, {
        "role": role,
        "max_iterations": max_iterations,
        "topics": topics,
    })
    return _redirect_with_query(f"/students/{config_key}", saved="training")


@app.post("/students/{config_key}/cases")
async def upload_cases(request: Request, config_key: str, case_file: UploadFile = File(...)):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    try:
        store.update_case_file(config_key, case_file)
    except Exception as exc:
        return _redirect_with_query(f"/students/{config_key}", error=str(exc))

    return _redirect_with_query(f"/students/{config_key}", uploaded=1)


@app.post("/students/{config_key}/cases/delete")
async def delete_cases(request: Request, config_key: str):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    try:
        workspace = store.get_student_workspace(config_key)
        if not workspace:
            raise ValueError("未找到 student")
        store.delete_case_file(config_key)
    except Exception as exc:
        return _redirect_with_query(f"/students/{config_key}", error=str(exc))

    return _redirect_with_query(f"/students/{config_key}", deleted=1)


@app.post("/students/{config_key}/cases/process")
async def start_case_process(request: Request, config_key: str):
    forbidden = _require_expert_editor_api(request, config_key)
    if forbidden:
        return forbidden
    try:
        workspace = store.get_student_workspace(config_key)
        if not workspace:
            raise ValueError("未找到 student")
        job = case_processing_service.start(config_key)
        return JSONResponse(job)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/students/{config_key}/cases/process/latest")
async def latest_case_process(config_key: str):
    job = case_processing_service.latest(config_key)
    if not job:
        return JSONResponse({"job": None})
    return JSONResponse({"job": job})


@app.post("/students/{config_key}/run/standard")
async def run_standard_training(request: Request, config_key: str):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    try:
        run = training_runner.start_standard_training(config_key)
        return RedirectResponse(f"/runs/{run['run_id']}", status_code=303)
    except Exception as exc:
        return _redirect_with_query(f"/students/{config_key}", error=str(exc))


@app.post("/students/{config_key}/run/case")
async def run_case_training(request: Request, config_key: str):
    forbidden = _require_expert_editor_page(request, config_key)
    if forbidden:
        return forbidden
    try:
        run = training_runner.start_case_training(config_key)
        return RedirectResponse(f"/runs/{run['run_id']}", status_code=303)
    except Exception as exc:
        return _redirect_with_query(f"/students/{config_key}", error=str(exc))
