import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from config import Config
from services.case_process_store import CaseProcessStore
from services.dataset_content_store import DatasetContentStore
from services.douyin_media_store import DouyinMediaStore
from services.douyin_resolver import DouyinResolver
from services.douyin_understander import DouyinUnderstander


class CaseProcessingService:
    DEFAULT_MAX_WORKERS = 4

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.config = Config(self.base_dir / "config.yaml")
        self.dataset_store = DatasetContentStore(self.base_dir)
        self.process_store = CaseProcessStore(self.base_dir)
        self.douyin_resolver = DouyinResolver()
        self._state_lock = threading.Lock()
        self._active_jobs = {}

    def latest(self, config_key):
        return self.process_store.latest_for_student(config_key)

    def start(self, config_key):
        with self._state_lock:
            active_job_id = self._active_jobs.get(config_key)
            if active_job_id:
                active_job = self.process_store.get(active_job_id)
                if active_job and active_job.get("status") == "running":
                    return active_job

            case_studies = self.config.reload().get_case_studies(config_key)
            job = self._build_job(config_key, case_studies)
            self.process_store.save(job)

            worker = threading.Thread(
                target=self._run_job,
                args=(job["job_id"],),
                daemon=True,
            )
            self._active_jobs[config_key] = job["job_id"]
            worker.start()
            return job

    def _build_job(self, config_key, case_studies):
        cases = []
        total_work_units = 0

        for index, case in enumerate(case_studies, start=1):
            has_gid = bool(case.get("gid"))
            stages = {
                "resolve": "pending" if has_gid else "skipped",
                "assets": "pending" if has_gid else "skipped",
                "understand": "pending" if has_gid else "skipped",
            }

            if has_gid:
                total_work_units += 3

            cases.append({
                "index": index,
                "gid": case.get("gid", ""),
                "title": case.get("title", "") or f"案例 {index}",
                "content": case.get("content", ""),
                "config_key": config_key,
                "user_judgment": case.get("user_judgment", ""),
                "user_reason": case.get("user_reason", ""),
                "status": "pending",
                "stage": "waiting",
                "message": "等待处理",
                "error": None,
                "stages": stages,
                "extract": None,
                "understanding": None,
                "assets": {
                    "video_path": None,
                    "audio_path": None,
                    "cover_path": None,
                    "image_paths": [],
                },
            })

        job = {
            "job_id": self.process_store.create_job_id(config_key),
            "config_key": config_key,
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "total_cases": len(case_studies),
            "total_work_units": total_work_units,
            "progress_percent": 0,
            "summary": {
                "pending_cases": len(case_studies),
                "running_cases": 0,
                "completed_cases": 0,
                "failed_cases": 0,
                "resolved_cases": 0,
                "assets_ready_cases": 0,
                "understood_cases": 0,
            },
            "cases": cases,
        }
        return self._recompute_job(job)

    def _run_job(self, job_id):
        job = self.process_store.get(job_id)
        if not job:
            return

        cases = job.get("cases", [])
        max_workers = min(self.DEFAULT_MAX_WORKERS, max(1, len(cases) or 1))

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(self._process_case, job_id, case)
                    for case in cases
                ]
                for future in as_completed(futures):
                    future.result()

            self._finish_job(job_id, "completed")
        except Exception as exc:
            self._finish_job(job_id, "failed", error=str(exc))
        finally:
            latest = self.process_store.get(job_id) or {}
            with self._state_lock:
                config_key = latest.get("config_key")
                if config_key and self._active_jobs.get(config_key) == job_id:
                    self._active_jobs.pop(config_key, None)

    def _process_case(self, job_id, case):
        index = case.get("index")
        gid = case.get("gid", "")
        title = case.get("title") or f"案例 {index}"
        content = case.get("content", "")

        if not gid:
            self._update_case(
                job_id,
                index,
                {
                    "status": "completed",
                    "stage": "finished",
                    "message": "没有 gid，直接使用表格原始内容",
                },
            )
            return

        try:
            self._update_case(job_id, index, {
                "status": "running",
                "stage": "resolving",
                "message": "正在拉取基础内容",
            })

            extracted = self.dataset_store.load_extract(case["config_key"], gid) if case.get("config_key") else None
            if not extracted:
                extracted = self.douyin_resolver.resolve_gid(gid)
                self.dataset_store.save_extract(case["config_key"], gid, extracted)

            self._update_case(job_id, index, {
                "title": extracted.get("title") or title,
                "content": extracted.get("content") or content,
                "extract": extracted,
                "stages.resolve": "completed",
                "message": "基础内容已就绪，正在下载素材",
                "stage": "downloading",
            })

            media_store = DouyinMediaStore(self.dataset_store)
            assets = media_store.ensure_local_assets(case["config_key"], gid, extracted)
            self._update_case(job_id, index, {
                "assets": assets,
                "stages.assets": "completed",
                "message": "素材已就绪，正在做模型理解",
                "stage": "understanding",
            })

            understanding = self.dataset_store.load_understanding(case["config_key"], gid)
            if not understanding or understanding.get("status") != "completed":
                understander = DouyinUnderstander(self.config.reload().model_name)
                understanding = understander.understand(extracted, material_bundle=assets)
                self.dataset_store.save_understanding(case["config_key"], gid, understanding)

            parsed = (understanding or {}).get("parsed") or {}
            self._update_case(job_id, index, {
                "title": parsed.get("title") or extracted.get("title") or title,
                "content": parsed.get("content") or extracted.get("content") or content,
                "understanding": understanding,
                "stages.understand": "completed",
                "status": "completed",
                "stage": "finished",
                "message": "处理完成",
            })
        except Exception as exc:
            self._update_case(job_id, index, {
                "status": "failed",
                "stage": "failed",
                "error": str(exc),
                "message": f"处理失败：{exc}",
            })

    def _update_case(self, job_id, case_index, patch):
        with self._state_lock:
            job = self.process_store.get(job_id)
            if not job:
                return None

            for item in job.get("cases", []):
                if item.get("index") != case_index:
                    continue
                self._apply_patch(item, patch)
                break

            job = self._recompute_job(job)
            self.process_store.save(job)
            return job

    def _finish_job(self, job_id, status, error=None):
        with self._state_lock:
            job = self.process_store.get(job_id)
            if not job:
                return

            job["status"] = status
            job["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if error:
                job["error"] = error

            if status == "completed" and job["summary"]["failed_cases"] > 0:
                job["status"] = "completed_with_errors"

            job = self._recompute_job(job)
            self.process_store.save(job)

    def _recompute_job(self, job):
        cases = job.get("cases", [])
        summary = {
            "pending_cases": 0,
            "running_cases": 0,
            "completed_cases": 0,
            "failed_cases": 0,
            "resolved_cases": 0,
            "assets_ready_cases": 0,
            "understood_cases": 0,
        }
        completed_units = 0

        for item in cases:
            status = item.get("status")
            if status == "pending":
                summary["pending_cases"] += 1
            elif status == "running":
                summary["running_cases"] += 1
            elif status == "completed":
                summary["completed_cases"] += 1
            elif status == "failed":
                summary["failed_cases"] += 1

            stages = item.get("stages", {})
            if stages.get("resolve") == "completed":
                summary["resolved_cases"] += 1
                completed_units += 1
            if stages.get("assets") == "completed":
                summary["assets_ready_cases"] += 1
                completed_units += 1
            if stages.get("understand") == "completed":
                summary["understood_cases"] += 1
                completed_units += 1

        total_work_units = job.get("total_work_units") or 0
        if total_work_units <= 0:
            finished_cases = summary["completed_cases"] + summary["failed_cases"]
            progress_percent = 100 if (not cases or finished_cases == len(cases)) else 0
        else:
            progress_percent = round(completed_units / total_work_units * 100, 1)

        if cases and summary["pending_cases"] == 0 and summary["running_cases"] == 0:
            progress_percent = 100

        job["summary"] = summary
        job["progress_percent"] = progress_percent
        return job

    def _apply_patch(self, target, patch):
        for key, value in patch.items():
            if "." not in key:
                target[key] = value
                continue

            cursor = target
            parts = key.split(".")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = value
