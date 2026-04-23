import json
from datetime import datetime
from pathlib import Path


class CaseProcessStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.process_dir = self.base_dir / "case_processes"
        self.process_dir.mkdir(exist_ok=True)

    def create_job_id(self, config_key):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{timestamp}_{config_key}_case_process"

    def save(self, payload):
        path = self.process_dir / f"{payload['job_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def get(self, job_id):
        path = self.process_dir / f"{job_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def latest_for_student(self, config_key):
        for path in sorted(self.process_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("config_key") == config_key:
                return payload
        return None

    def delete_for_student(self, config_key):
        deleted = 0
        for path in self.process_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("config_key") != config_key:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        return deleted
