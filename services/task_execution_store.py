import csv
import json
from datetime import datetime
from pathlib import Path


class TaskExecutionStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.execution_dir = self.base_dir / "task_executions"
        self.execution_dir.mkdir(exist_ok=True)

    def create_execution_id(self, config_key):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{timestamp}_{config_key}_task_execution"

    def save(self, payload):
        path = self.execution_dir / f"{payload['execution_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def get(self, execution_id):
        path = self.execution_dir / f"{execution_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, limit=50):
        items = []
        for path in sorted(self.execution_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append(payload)
            if len(items) >= limit:
                break
        return items

    def csv_path(self, execution_id):
        return self.execution_dir / f"{execution_id}.csv"

    def save_csv(self, execution_id, rows):
        path = self.csv_path(execution_id)
        fieldnames = [
            "gid",
            "title",
            "content",
            "judge",
            "reason",
            "category",
            "status",
            "error",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        return path
