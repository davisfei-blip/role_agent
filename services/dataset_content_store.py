import json
import re
from pathlib import Path


class DatasetContentStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)

    def dataset_dir(self, config_key):
        path = self.base_dir / "case_studies" / config_key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def dataset_file_path(self, config_key, extension=".csv"):
        return self.dataset_dir(config_key) / f"dataset{extension}"

    def items_dir(self, config_key):
        path = self.dataset_dir(config_key) / "items"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def item_dir(self, config_key, gid):
        safe_gid = self._safe_gid(gid)
        path = self.items_dir(config_key) / safe_gid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_extract(self, config_key, gid, payload):
        path = self.item_dir(config_key, gid) / "extract.json"
        self._write_json(path, payload)
        return path

    def save_understanding(self, config_key, gid, payload):
        path = self.item_dir(config_key, gid) / "understanding.json"
        self._write_json(path, payload)
        return path

    def load_extract(self, config_key, gid):
        return self._read_json(self.items_dir(config_key) / self._safe_gid(gid) / "extract.json")

    def load_understanding(self, config_key, gid):
        return self._read_json(self.items_dir(config_key) / self._safe_gid(gid) / "understanding.json")

    def source_video_path(self, config_key, gid):
        return self.item_dir(config_key, gid) / "source.mp4"

    def audio_path(self, config_key, gid):
        return self.item_dir(config_key, gid) / "audio.mp3"

    def cover_path(self, config_key, gid):
        return self.item_dir(config_key, gid) / "cover.jpg"

    def images_dir(self, config_key, gid):
        path = self.item_dir(config_key, gid) / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def image_paths(self, config_key, gid):
        image_dir = self.images_dir(config_key, gid)
        return sorted([path for path in image_dir.iterdir() if path.is_file()])

    def has_source_video(self, config_key, gid):
        return self.source_video_path(config_key, gid).exists()

    def has_audio(self, config_key, gid):
        return self.audio_path(config_key, gid).exists()

    def has_cover(self, config_key, gid):
        return self.cover_path(config_key, gid).exists()

    def merge_rows(self, config_key, rows, limit=None):
        merged = []
        for index, row in enumerate(rows, start=1):
            if limit and index > limit:
                break

            gid = row.get("gid", "")
            extract = self.load_extract(config_key, gid) if gid else None
            understanding = self.load_understanding(config_key, gid) if gid else None

            merged.append({
                **row,
                "extract": extract,
                "understanding": understanding,
                "display_title": self._display_title(row, extract, understanding),
                "display_content": self._display_content(row, extract, understanding),
                "resolve_status": self._resolve_status(extract, understanding),
                "has_source_video": self.has_source_video(config_key, gid) if gid else False,
                "has_audio": self.has_audio(config_key, gid) if gid else False,
                "has_cover": self.has_cover(config_key, gid) if gid else False,
                "source_video_relpath": str(self.source_video_path(config_key, gid).relative_to(self.base_dir)) if gid and self.has_source_video(config_key, gid) else "",
                "audio_relpath": str(self.audio_path(config_key, gid).relative_to(self.base_dir)) if gid and self.has_audio(config_key, gid) else "",
                "cover_relpath": str(self.cover_path(config_key, gid).relative_to(self.base_dir)) if gid and self.has_cover(config_key, gid) else "",
                "image_relpaths": [str(path.relative_to(self.base_dir)) for path in self.image_paths(config_key, gid)] if gid else [],
            })
        return merged

    def dataset_stats(self, config_key, rows):
        resolved = 0
        understood = 0
        downloaded = 0
        audio_ready = 0
        covered = 0
        image_ready = 0
        for row in rows:
            gid = row.get("gid", "")
            if not gid:
                continue
            if self.load_extract(config_key, gid):
                resolved += 1
            understanding = self.load_understanding(config_key, gid)
            if understanding and understanding.get("status") == "completed":
                understood += 1
            if self.has_source_video(config_key, gid):
                downloaded += 1
            if self.has_audio(config_key, gid):
                audio_ready += 1
            if self.has_cover(config_key, gid):
                covered += 1
            if self.image_paths(config_key, gid):
                image_ready += 1
        return {
            "resolved_count": resolved,
            "understood_count": understood,
            "downloaded_count": downloaded,
            "audio_count": audio_ready,
            "covered_count": covered,
            "image_count": image_ready,
        }

    def _display_title(self, row, extract, understanding):
        parsed = (understanding or {}).get("parsed") or {}
        return (
            parsed.get("title")
            or row.get("title")
            or (extract or {}).get("title")
            or f"GID: {row.get('gid', '未知')}"
        )

    def _display_content(self, row, extract, understanding):
        parsed = (understanding or {}).get("parsed") or {}
        return (
            parsed.get("content")
            or row.get("content")
            or (extract or {}).get("content")
            or ""
        )

    def _resolve_status(self, extract, understanding):
        if understanding and understanding.get("status") == "completed":
            return "understood"
        if extract:
            return "resolved"
        return "raw"

    def _safe_gid(self, gid):
        value = str(gid or "").strip() or "unknown"
        return re.sub(r"[^0-9A-Za-z._-]+", "_", value)

    def _read_json(self, path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_json(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
