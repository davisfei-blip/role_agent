from pathlib import Path

from config import Config
from services.dataset_content_store import DatasetContentStore
from services.douyin_media_store import DouyinMediaStore
from services.douyin_resolver import DouyinResolver
from services.douyin_understander import DouyinUnderstander


class CaseContentService:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.config = Config(self.base_dir / "config.yaml")
        self.dataset_store = DatasetContentStore(self.base_dir)
        self.douyin_resolver = DouyinResolver()
        self.douyin_media_store = DouyinMediaStore(self.dataset_store)

    def resolve(self, config_key, gid="", title="", content=""):
        gid = str(gid or "").strip()
        current_title = title or (f"GID: {gid}" if gid else "")
        current_content = content or ""
        extracted = None
        understanding = None
        assets = {}
        warnings = []
        error = None

        if gid:
            extracted = self.dataset_store.load_extract(config_key, gid)
            understanding = self.dataset_store.load_understanding(config_key, gid)

            if not extracted:
                try:
                    extracted = self.douyin_resolver.resolve_gid(gid)
                    self.dataset_store.save_extract(config_key, gid, extracted)
                except Exception as exc:
                    error = str(exc)

            if extracted and not error and (not understanding or understanding.get("status") != "completed"):
                try:
                    assets = self.douyin_media_store.ensure_local_assets(config_key, gid, extracted)
                except Exception as exc:
                    warnings.append(f"素材准备失败，回退文本理解：{exc}")
                    assets = {}

                try:
                    understander = DouyinUnderstander(self.config.reload().model_name)
                    understanding = understander.understand(extracted, material_bundle=assets)
                    self.dataset_store.save_understanding(config_key, gid, understanding)
                except Exception as exc:
                    warnings.append(f"内容理解失败，回退基础解析：{exc}")
                    understanding = understanding if isinstance(understanding, dict) else {
                        "status": "failed",
                        "error": str(exc),
                    }

            parsed = (understanding or {}).get("parsed") or {}
            if parsed:
                current_title = parsed.get("title") or current_title
                current_content = parsed.get("content") or current_content
            elif extracted and not error:
                current_title = extracted.get("title") or current_title
                current_content = extracted.get("content", "") or current_content

        return {
            "gid": gid,
            "title": current_title,
            "content": current_content,
            "extract": extracted,
            "understanding": understanding,
            "assets": assets,
            "warnings": warnings,
            "error": error,
        }
