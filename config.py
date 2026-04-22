import yaml
import os


class Config:
    _instance = None

    def __new__(cls, config_file="config.yaml"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config(config_file)
        return cls._instance

    def _load_config(self, config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)

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
        return self._config.get("case_studies", {}).get(config_key, [])
