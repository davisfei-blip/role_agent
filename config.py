from pathlib import Path

import yaml
from case_loader import load_case_studies


class Config:
    _instance = None

    def __new__(cls, config_file="config.yaml"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config(config_file)
        return cls._instance

    def _load_config(self, config_file):
        self._config_file = Path(config_file).resolve()
        self._case_studies_cache = {}

        with self._config_file.open('r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)

    def reload(self):
        self._load_config(self._config_file)
        return self

    def _resolve_path(self, file_path):
        path = Path(file_path)
        if not path.is_absolute():
            path = self._config_file.parent / path
        return path

    def _load_case_studies_from_file(self, file_path):
        resolved_path = self._resolve_path(file_path)
        cache_key = str(resolved_path)

        if cache_key in self._case_studies_cache:
            return self._case_studies_cache[cache_key]

        if not resolved_path.exists():
            raise FileNotFoundError(f"未找到案例表格文件：{resolved_path}")

        if resolved_path.suffix.lower() not in {".csv", ".tsv", ".xlsx"}:
            raise ValueError(
                f"暂不支持的案例表格格式：{resolved_path.suffix}，目前仅支持 .csv / .tsv / .xlsx"
            )

        case_studies = load_case_studies(resolved_path)
        self._case_studies_cache[cache_key] = case_studies
        return case_studies

    @property
    def model_name(self):
        return self._config.get("model", {}).get("name", "gpt-3.5-turbo")

    @property
    def search_enabled(self):
        return self._config.get("search", {}).get("enabled", True)

    @property
    def search_num_results(self):
        return self._config.get("search", {}).get("num_results", 5)

    @property
    def memory_enabled(self):
        return self._config.get("memory", {}).get("enabled", True)

    @property
    def memory_auto_save(self):
        return self._config.get("memory", {}).get("auto_save", True)

    @property
    def memory_dir(self):
        return self._config.get("memory", {}).get("memory_dir", "memory")

    @property
    def students(self):
        """获取学生Agent配置列表"""
        return self._config.get("students", [])

    def get_student_config(self, config_key):
        """根据config_key获取学生考点配置"""
        return self._config.get(config_key)

    def get_case_studies(self, config_key):
        """根据config_key获取案例数据"""
        case_studies_config = self._config.get("case_studies", {}).get(config_key, [])

        if isinstance(case_studies_config, list):
            return case_studies_config

        if isinstance(case_studies_config, str):
            return self._load_case_studies_from_file(case_studies_config)

        if isinstance(case_studies_config, dict):
            file_path = case_studies_config.get("file")
            if not file_path:
                return []
            return self._load_case_studies_from_file(file_path)

        return []
