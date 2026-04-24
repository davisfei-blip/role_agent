import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from services.case_content_service import CaseContentService
from config import Config
from services.dataset_content_store import DatasetContentStore
from services.task_execution_store import TaskExecutionStore
from student_agent import create_all_students, refresh_runtime_state as refresh_student_state


class TaskExecutionService:
    DEFAULT_MAX_WORKERS = 3

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.config = Config(self.base_dir / "config.yaml")
        self.dataset_store = DatasetContentStore(self.base_dir)
        self.case_content_service = CaseContentService(self.base_dir)
        self.execution_store = TaskExecutionStore(self.base_dir)
        self._state_lock = threading.Lock()

    def list_executions(self, limit=20):
        return self.execution_store.list(limit=limit)

    def get(self, execution_id):
        return self.execution_store.get(execution_id)

    def start(self, config_key, gids):
        normalized_gids = self._normalize_gids(gids)
        if not normalized_gids:
            raise ValueError("请至少输入一个 gid")

        self.config.reload()
        refresh_student_state()
        student = self._get_student(config_key)
        if not student:
            raise ValueError(f"未找到 agent：{config_key}")

        execution = self._build_execution(config_key, student.name, normalized_gids)
        self.execution_store.save(execution)

        worker = threading.Thread(
            target=self._run_execution,
            args=(execution["execution_id"],),
            daemon=True,
        )
        worker.start()
        return execution

    def _get_student(self, config_key):
        for key, student in create_all_students():
            if key == config_key:
                return student
        return None

    def _build_execution(self, config_key, student_name, gids):
        items = []
        for index, gid in enumerate(gids, start=1):
            items.append({
                "index": index,
                "gid": gid,
                "title": "",
                "content": "",
                "judge_raw": "",
                "judge": "",
                "reason": "",
                "category": "",
                "status": "pending",
                "stage": "waiting",
                "message": "等待执行",
                "error": "",
            })

        return {
            "execution_id": self.execution_store.create_execution_id(config_key),
            "config_key": config_key,
            "student_name": student_name,
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "progress_percent": 0,
            "input_count": len(gids),
            "summary": {
                "pending": len(gids),
                "running": 0,
                "completed": 0,
                "failed": 0,
            },
            "items": items,
            "download_path": "",
            "error": "",
        }

    def _run_execution(self, execution_id):
        execution = self.execution_store.get(execution_id)
        if not execution:
            return

        config_key = execution["config_key"]
        refresh_student_state()
        student = self._get_student(config_key)
        if not student:
            self._finish_execution(execution_id, "failed", error=f"未找到 agent：{config_key}")
            return

        items = execution.get("items", [])
        max_workers = min(self.DEFAULT_MAX_WORKERS, max(1, len(items)))

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(self._process_item, execution_id, config_key, student, item)
                    for item in items
                ]
                for future in as_completed(futures):
                    future.result()
            self._finish_execution(execution_id, "completed")
        except Exception as exc:
            self._finish_execution(execution_id, "failed", error=str(exc))

    def _process_item(self, execution_id, config_key, student, item):
        index = item["index"]
        gid = item["gid"]

        try:
            self._update_item(execution_id, index, {
                "status": "running",
                "stage": "resolving",
                "message": "正在解析内容",
            })

            pipeline_result = self.case_content_service.resolve(config_key, gid=gid)
            title = pipeline_result["title"] or f"GID: {gid}"
            content = pipeline_result["content"] or ""

            self._update_item(execution_id, index, {
                "title": title,
                "content": content,
                "stage": "judging",
                "message": "正在生成判定",
            })

            if not content:
                raise ValueError(
                    pipeline_result["error"]
                    or "当前 gid 未能解析出可用于判断的内容"
                )

            judge_raw = student.judge_case(title, content)
            judge, reason, category = self._parse_judge(judge_raw)

            self._update_item(execution_id, index, {
                "judge_raw": judge_raw,
                "judge": judge,
                "reason": reason,
                "category": category,
                "status": "completed",
                "stage": "finished",
                "message": "执行完成",
            })
        except Exception as exc:
            self._update_item(execution_id, index, {
                "status": "failed",
                "stage": "failed",
                "error": str(exc),
                "message": f"执行失败：{exc}",
            })

    def _parse_judge(self, text):
        judge = self._extract_line_value(text, ["你的判断", "判断"])
        reason = self._extract_line_value(text, ["判断理由", "理由"])
        category = self._extract_line_value(text, ["问题分类", "分类"])
        return judge, reason, category

    def _extract_line_value(self, text, labels):
        for label in labels:
            pattern = rf"{re.escape(label)}[：:]\s*(.*)"
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    def _update_item(self, execution_id, item_index, patch):
        with self._state_lock:
            execution = self.execution_store.get(execution_id)
            if not execution:
                return

            for item in execution.get("items", []):
                if item.get("index") == item_index:
                    item.update(patch)
                    break

            self._recompute_execution(execution)
            self.execution_store.save(execution)

    def _finish_execution(self, execution_id, status, error=""):
        with self._state_lock:
            execution = self.execution_store.get(execution_id)
            if not execution:
                return

            execution["status"] = status
            execution["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            execution["error"] = error
            self._recompute_execution(execution)

            if execution["summary"]["failed"] > 0 and status == "completed":
                execution["status"] = "completed_with_errors"

            rows = []
            for item in execution.get("items", []):
                rows.append({
                    "gid": item.get("gid", ""),
                    "title": item.get("title", ""),
                    "content": item.get("content", ""),
                    "judge": item.get("judge", ""),
                    "reason": item.get("reason", ""),
                    "category": item.get("category", ""),
                    "status": item.get("status", ""),
                    "error": item.get("error", ""),
                })
            csv_path = self.execution_store.save_csv(execution_id, rows)
            execution["download_path"] = str(csv_path.relative_to(self.base_dir))
            self.execution_store.save(execution)

    def _recompute_execution(self, execution):
        summary = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        items = execution.get("items", [])
        for item in items:
            status = item.get("status")
            if status in summary:
                summary[status] += 1

        execution["summary"] = summary
        total = len(items)
        finished = summary["completed"] + summary["failed"]
        execution["progress_percent"] = round((finished / total) * 100, 1) if total else 0
        if total and finished == total:
            execution["progress_percent"] = 100
        return execution

    def _normalize_gids(self, gids):
        items = []
        for raw in gids:
            value = str(raw or "").strip()
            if not value:
                continue
            if value not in items:
                items.append(value)
        return items
