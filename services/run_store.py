import json
import threading
from datetime import datetime
from pathlib import Path


class RunStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.runs_dir = self.base_dir / "runs"
        self.runs_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()

    def create_run_id(self, config_key, mode):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{timestamp}_{config_key}_{mode}"

    def save(self, run_data):
        path = self.runs_dir / f"{run_data['run_id']}.json"
        with self._lock:
            path.write_text(json.dumps(run_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def get(self, run_id):
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self, config_key=None, limit=50):
        runs = []
        for path in sorted(self.runs_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if config_key and data.get("config_key") != config_key:
                continue
            runs.append(data)
            if len(runs) >= limit:
                break
        return runs

    def latest_run(self, config_key):
        runs = self.list_runs(config_key=config_key, limit=1)
        return runs[0] if runs else None

    def event_path(self, run_id):
        return self.runs_dir / f"{run_id}.events.jsonl"

    def append_event(self, run_id, event):
        path = self.event_path(run_id)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path

    def load_events(self, run_id):
        path = self.event_path(run_id)
        if not path.exists():
            return []

        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
        return events
