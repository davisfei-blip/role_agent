import csv
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

from case_loader import load_case_studies, read_case_columns
from services.dataset_content_store import DatasetContentStore
from services.run_store import RunStore


class ConfigStore:
    def __init__(self, config_file="config.yaml"):
        self.config_file = Path(config_file).resolve()
        self.base_dir = self.config_file.parent
        self.run_store = RunStore(self.base_dir)
        self.dataset_store = DatasetContentStore(self.base_dir)

    def load(self):
        with self.config_file.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save(self, config_data):
        with self.config_file.open("w", encoding="utf-8") as f:
            yaml.safe_dump(config_data, f, allow_unicode=True, sort_keys=False)

    def get_settings(self):
        config = self.load()
        return {
            "model_name": config.get("model", {}).get("name", ""),
            "search_enabled": config.get("search", {}).get("enabled", True),
            "search_num_results": config.get("search", {}).get("num_results", 5),
            "memory_enabled": config.get("memory", {}).get("enabled", True),
            "memory_auto_save": config.get("memory", {}).get("auto_save", True),
            "memory_dir": config.get("memory", {}).get("memory_dir", "memory"),
        }

    def update_settings(self, payload):
        config = self.load()
        config.setdefault("model", {})["name"] = payload["model_name"]
        config.setdefault("search", {})["enabled"] = payload["search_enabled"]
        config.setdefault("search", {})["num_results"] = payload["search_num_results"]
        config.setdefault("memory", {})["enabled"] = payload["memory_enabled"]
        config.setdefault("memory", {})["auto_save"] = payload["memory_auto_save"]
        config.setdefault("memory", {})["memory_dir"] = payload["memory_dir"]
        self.save(config)

    def list_student_workspaces(self):
        config = self.load()
        students = config.get("students", [])
        return [self._build_workspace_summary(config, student) for student in students]

    def get_student_workspace(self, config_key):
        config = self.load()
        student = self._get_student(config, config_key)
        if not student:
            return None

        training_config = deepcopy(config.get(config_key, {}))
        case_bundle = self._get_case_bundle(config, config_key)
        recent_memories = self._load_recent_memories(student.get("name", ""))

        return {
            "student": student,
            "training_config": training_config,
            "case_bundle": case_bundle,
            "recent_memories": recent_memories,
            "stats": {
                "case_count": len(case_bundle["rows"]),
                "recent_training_time": recent_memories[0]["timestamp"] if recent_memories else None,
            }
        }

    def update_student_basic(self, config_key, payload):
        config = self.load()
        student = self._get_student(config, config_key)
        if not student:
            raise ValueError(f"未找到 student: {config_key}")

        student["name"] = payload["name"]
        student["role"] = payload["role"]
        student["prompt_file"] = payload["prompt_file"]
        student["default_prompt"] = payload["default_prompt"]
        self.save(config)

    def update_training_config(self, config_key, payload):
        config = self.load()
        config[config_key] = {
            "role": payload["role"],
            "max_iterations": payload["max_iterations"],
            "topics": payload["topics"],
        }
        self.save(config)

    def update_case_file(self, config_key, upload_file):
        config = self.load()
        ext = Path(upload_file.filename or "").suffix.lower() or ".csv"
        if ext not in {".csv", ".tsv", ".xlsx"}:
            raise ValueError("仅支持上传 csv / tsv / xlsx 文件")

        target_path = self.dataset_store.dataset_file_path(config_key, ext)
        content = upload_file.file.read()
        target_path.write_bytes(content)

        # 提前校验文件能否读通
        load_case_studies(target_path)

        case_studies = config.setdefault("case_studies", {})
        current = case_studies.get(config_key)

        if isinstance(current, dict):
            current["file"] = str(target_path.relative_to(self.base_dir))
        else:
            case_studies[config_key] = {"file": str(target_path.relative_to(self.base_dir))}

        self.save(config)
        return str(target_path.relative_to(self.base_dir))

    def _get_student(self, config, config_key):
        for student in config.get("students", []):
            if student.get("config_key") == config_key:
                return student
        return None

    def _build_workspace_summary(self, config, student):
        config_key = student.get("config_key")
        case_bundle = self._get_case_bundle(config, config_key)
        recent_memories = self._load_recent_memories(student.get("name", ""))
        latest = recent_memories[0] if recent_memories else {}
        latest_run = self.run_store.latest_run(config_key)
        return {
            "name": student.get("name", ""),
            "role": student.get("role", ""),
            "config_key": config_key,
            "prompt_file": student.get("prompt_file", ""),
            "case_count": len(case_bundle["rows"]),
            "case_file": case_bundle["file"],
            "recent_training_time": (latest_run or {}).get("finished_at") or latest.get("timestamp"),
            "recent_topic": latest.get("topic"),
            "recent_feedback": latest.get("teacher_feedback", ""),
            "recent_run": latest_run,
        }

    def _get_case_bundle(self, config, config_key):
        case_entry = config.get("case_studies", {}).get(config_key)
        file_path = None
        if isinstance(case_entry, str):
            file_path = case_entry
        elif isinstance(case_entry, dict):
            file_path = case_entry.get("file")

        rows = []
        columns = []
        error = None
        if file_path:
            resolved_path = self.base_dir / file_path
            if resolved_path.exists():
                try:
                    rows = load_case_studies(resolved_path)
                    columns = read_case_columns(resolved_path)
                except Exception as exc:
                    error = str(exc)
            else:
                error = f"文件不存在：{file_path}"

        preview_rows = self.dataset_store.merge_rows(config_key, rows, limit=10)
        dataset_stats = self.dataset_store.dataset_stats(config_key, rows)

        return {
            "file": file_path,
            "rows": rows,
            "preview_rows": preview_rows,
            "columns": columns or self._infer_columns(rows),
            "error": error,
            "dataset_dir": str(self.dataset_store.dataset_dir(config_key).relative_to(self.base_dir)),
            "resolved_count": dataset_stats["resolved_count"],
            "understood_count": dataset_stats["understood_count"],
            "downloaded_count": dataset_stats["downloaded_count"],
            "audio_count": dataset_stats["audio_count"],
            "covered_count": dataset_stats["covered_count"],
            "image_count": dataset_stats["image_count"],
        }

    def _infer_columns(self, rows):
        if not rows:
            return []
        raw = rows[0].get("raw", {})
        return list(raw.keys()) or ["gid", "title", "content", "user_judgment", "user_reason"]

    def _load_recent_memories(self, student_name, limit=5):
        if not student_name:
            return []

        memory_dir = self.base_dir / self.get_settings()["memory_dir"]
        json_path = memory_dir / f"{student_name}_memory.json"
        if not json_path.exists():
            return []

        try:
            import json

            items = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        normalized = []
        for item in items:
            normalized.append({
                "topic": item.get("topic", ""),
                "timestamp": item.get("timestamp", ""),
                "teacher_feedback": item.get("teacher_feedback", ""),
                "knowledge": item.get("knowledge", ""),
            })

        normalized.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return normalized[:limit]
