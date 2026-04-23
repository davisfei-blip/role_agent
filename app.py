import json
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
app = FastAPI(title="Agent 训练台")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
store = ConfigStore(BASE_DIR / "config.yaml")
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


def render_page(request: Request, template_name: str, **context):
    payload = {
        "request": request,
        "nav_students": store.list_student_workspaces(),
        "active_config_key": context.get("active_config_key"),
    }
    payload.update(context)
    return templates.TemplateResponse(template_name, payload)


@app.get("/")
def home():
    return RedirectResponse("/learning", status_code=303)


@app.get("/learning")
def learning_page(request: Request):
    students = store.list_student_workspaces()
    stats = {
        "student_count": len(students),
        "students_with_cases": len([item for item in students if item["case_count"] > 0]),
        "recent_updates": len([item for item in students if item["recent_training_time"]]),
    }
    return render_page(
        request,
        "index.html",
        students=students,
        stats=stats,
        page_title="学习中心",
    )


@app.get("/tasks")
def tasks_page(request: Request):
    executions = task_execution_service.list_executions(limit=20)
    return render_page(
        request,
        "tasks.html",
        students=store.list_student_workspaces(),
        executions=executions,
        latest_execution=executions[0] if executions else None,
        page_title="任务执行",
    )


@app.get("/settings")
def settings_page(request: Request):
    return render_page(
        request,
        "settings.html",
        settings=store.get_settings(),
        saved=request.query_params.get("saved"),
        page_title="系统设置",
    )


@app.get("/runs")
def runs_page(request: Request):
    runs = run_store.list_runs()
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
        return RedirectResponse("/runs?missing=1", status_code=303)
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
    model_name: str = Form(...),
    search_enabled: Optional[str] = Form(None),
    search_num_results: int = Form(...),
    memory_enabled: Optional[str] = Form(None),
    memory_auto_save: Optional[str] = Form(None),
    memory_dir: str = Form(...),
):
    store.update_settings({
        "model_name": model_name,
        "search_enabled": _bool_from_form(search_enabled),
        "search_num_results": search_num_results,
        "memory_enabled": _bool_from_form(memory_enabled),
        "memory_auto_save": _bool_from_form(memory_auto_save),
        "memory_dir": memory_dir,
    })
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/students/{config_key}")
def student_workspace(request: Request, config_key: str):
    workspace = store.get_student_workspace(config_key)
    if not workspace:
        return RedirectResponse("/?missing=1", status_code=303)

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


@app.post("/tasks/execute")
async def start_task_execution(
    config_key: str = Form(...),
    gids_text: str = Form(...),
):
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
        return RedirectResponse("/tasks?missing=1", status_code=303)

    if not execution.get("download_path"):
        return RedirectResponse("/tasks?missing=1", status_code=303)

    path = BASE_DIR / execution["download_path"]
    if not path.exists():
        return RedirectResponse("/tasks?missing=1", status_code=303)

    filename = f"{execution_id}.csv"
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.post("/students/{config_key}/basic")
async def save_student_basic(
    config_key: str,
    name: str = Form(...),
    role: str = Form(...),
    prompt_file: str = Form(...),
    default_prompt: str = Form(""),
):
    store.update_student_basic(config_key, {
        "name": name,
        "role": role,
        "prompt_file": prompt_file,
        "default_prompt": default_prompt,
    })
    return RedirectResponse(f"/students/{config_key}?saved=basic", status_code=303)


@app.post("/students/{config_key}/training")
async def save_training_config(
    config_key: str,
    role: str = Form(...),
    max_iterations: int = Form(...),
    topic_name: List[str] = Form([]),
    topic_description: List[str] = Form([]),
    topic_questions: List[str] = Form([]),
):
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
    return RedirectResponse(f"/students/{config_key}?saved=training", status_code=303)


@app.post("/students/{config_key}/cases")
async def upload_cases(config_key: str, case_file: UploadFile = File(...)):
    try:
        store.update_case_file(config_key, case_file)
    except Exception as exc:
        return RedirectResponse(f"/students/{config_key}?error={str(exc)}", status_code=303)

    return RedirectResponse(f"/students/{config_key}?uploaded=1", status_code=303)


@app.post("/students/{config_key}/cases/delete")
async def delete_cases(config_key: str):
    try:
        workspace = store.get_student_workspace(config_key)
        if not workspace:
            raise ValueError("未找到 student")
        store.delete_case_file(config_key)
    except Exception as exc:
        return RedirectResponse(f"/students/{config_key}?error={str(exc)}", status_code=303)

    return RedirectResponse(f"/students/{config_key}?deleted=1", status_code=303)


@app.post("/students/{config_key}/cases/process")
async def start_case_process(config_key: str):
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
async def run_standard_training(config_key: str):
    try:
        run = training_runner.start_standard_training(config_key)
        return RedirectResponse(f"/runs/{run['run_id']}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/students/{config_key}?error={str(exc)}", status_code=303)


@app.post("/students/{config_key}/run/case")
async def run_case_training(config_key: str):
    try:
        run = training_runner.start_case_training(config_key)
        return RedirectResponse(f"/runs/{run['run_id']}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/students/{config_key}?error={str(exc)}", status_code=303)
