import os
import json
from datetime import datetime
from dotenv import load_dotenv
from config import Config
from search_tool import SearchTool
from memory import MemoryManager
from services.responses_api_client import ResponsesAPIClient

load_dotenv()

config = Config()
search_tool = SearchTool()
memory_manager = MemoryManager(config.memory_dir)


def refresh_runtime_state():
    global memory_manager
    config.reload()
    memory_manager = MemoryManager(config.memory_dir)


class StudentAgent:
    def __init__(self, name, role, prompt_file, default_prompt=""):
        self.name = name
        self.role = role
        self.prompt_file = prompt_file
        self.default_prompt = default_prompt
        self.knowledge_base = []
        self.current_prompt = self._load_prompt()
        self.last_feedback = None
        
        # 加载记忆
        if config.memory_enabled:
            self._load_memory()

    def _load_prompt(self):
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, 'r', encoding='utf-8') as f:
                prompt = f.read()
                if prompt and prompt.strip():
                    return prompt
        return self._get_default_prompt()

    def _save_prompt(self, prompt):
        # 确保目录存在
        os.makedirs(os.path.dirname(self.prompt_file), exist_ok=True)
        with open(self.prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        self.current_prompt = prompt

    def _get_default_prompt(self):
        if self.default_prompt:
            return self.default_prompt
        return f"""你是一名{self.role}。请根据题目要求回答问题，力求准确、专业、有深度。"""

    def _get_effective_prompt(self):
        prompt = self.current_prompt if isinstance(self.current_prompt, str) else ""
        if prompt.strip():
            return prompt
        return self._get_default_prompt()

    def _load_memory(self):
        """从文件加载记忆"""
        loaded_memory = memory_manager.load_memory(self.name)
        if loaded_memory:
            self.knowledge_base = loaded_memory
            print(f"📚 已恢复 {len(loaded_memory)} 条学习记忆")

    def _save_memory(self):
        """保存记忆到文件"""
        if config.memory_enabled and config.memory_auto_save:
            memory_manager.save_structured_memory(self.name, self.knowledge_base)

    def _chat(self, messages, on_delta=None):
        client = ResponsesAPIClient(config.model_name)
        text, _ = client.generate_from_messages(messages, on_delta=on_delta)
        return text

    def learn(self, topic_description, teacher_feedback=None, on_delta=None):
        # 如果启用搜索功能，先搜索相关信息
        search_info = ""
        search_results = None
        if config.search_enabled:
            search_query = f"{self.role} {topic_description}"
            search_results = search_tool.search(search_query, config.search_num_results)
            if search_results:
                search_info = search_tool.format_search_results(search_results)

        learning_prompt = f"""请学习以下知识领域：{topic_description}

{'='*50}
{search_info}
{'='*50}

如果有老师的反馈，请根据反馈改进你的知识体系。
老师反馈：{teacher_feedback if teacher_feedback else '无'}

请总结你学到的关键点，结合搜索到的信息（如果有）进行学习。"""

        knowledge = self._chat(
            [
                {"role": "system", "content": self._get_effective_prompt()},
                {"role": "user", "content": learning_prompt}
            ],
            on_delta=on_delta,
        )
        
        # 保存学习记录到记忆
        memory_item = {
            "topic": topic_description,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "knowledge": knowledge,
            "search_results": search_results,
            "teacher_feedback": teacher_feedback
        }
        self.knowledge_base.append(memory_item)
        
        # 保存到文件
        self._save_memory()
        
        return knowledge

    def take_exam(self, question, on_delta=None):
        learned_knowledge = [
            {"topic": item["topic"], "knowledge": item["knowledge"]}
            for item in self.knowledge_base
            if (item.get("knowledge") or "").strip()
        ]
        exam_prompt = f"""请回答以下问题：
{question}
请基于你已学的知识进行回答。
已学知识：
{json.dumps(learned_knowledge, ensure_ascii=False, indent=2)}"""

        return self._chat(
            [
                {"role": "system", "content": self._get_effective_prompt()},
                {"role": "user", "content": exam_prompt}
            ],
            on_delta=on_delta,
        )

    def iterate_prompt(self, teacher_feedback, on_delta=None):
        self.last_feedback = teacher_feedback
        
        iteration_prompt = f"""请根据老师的以下反馈，优化你的系统prompt，以便更好地回答问题：
老师反馈：
{teacher_feedback}

当前系统prompt：
{self._get_effective_prompt()}

请提供优化后的系统prompt，只输出prompt内容，不要其他解释。"""

        new_prompt = self._chat(
            [
                {"role": "user", "content": iteration_prompt}
            ],
            on_delta=on_delta,
        )
        if new_prompt and new_prompt.strip():
            new_prompt = new_prompt.strip()
            self._save_prompt(new_prompt)
            return new_prompt
        return self._get_effective_prompt()

    def judge_case(self, case_title, case_content, on_delta=None):
        """判断案例是否有问题"""
        judge_prompt = f"""请判断以下内容是否有问题：

标题：{case_title}
内容：{case_content}

请按照以下格式回答：
1. 你的判断：有问题/没问题
2. 判断理由：（详细说明原因）
3. 问题分类：（如虚假宣传、低俗内容等）"""

        return self._chat(
            [
                {"role": "system", "content": self._get_effective_prompt()},
                {"role": "user", "content": judge_prompt}
            ],
            on_delta=on_delta,
        )

    def learn_from_user_feedback(self, case_title, case_content, user_judgment, user_reason, student_judgment, on_delta=None):
        """根据用户反馈学习纠偏"""
        learn_prompt = f"""请通过以下案例学习纠偏：

案例：
标题：{case_title}
内容：{case_content}

你的判断：
{student_judgment}

用户的正确判断：
判断：{user_judgment}
理由：{user_reason}

请分析你和用户判断的差异，并总结你应该如何改进你的判断标准。"""

        knowledge = self._chat(
            [
                {"role": "system", "content": self._get_effective_prompt()},
                {"role": "user", "content": learn_prompt}
            ],
            on_delta=on_delta,
        )

        # 保存学习记录到记忆
        memory_item = {
            "topic": f"案例学习：{case_title}",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "knowledge": knowledge,
            "search_results": None,
            "teacher_feedback": f"用户判断：{user_judgment}，理由：{user_reason}"
        }
        self.knowledge_base.append(memory_item)

        # 保存到文件
        self._save_memory()

        return knowledge


def create_student_from_config(student_config):
    """从配置创建学生Agent"""
    return StudentAgent(
        name=student_config["name"],
        role=student_config["role"],
        prompt_file=student_config["prompt_file"],
        default_prompt=student_config.get("default_prompt", "")
    )


def create_all_students():
    """从配置创建所有学生Agent"""
    students = []
    for student_config in config.students:
        student = create_student_from_config(student_config)
        students.append((student_config["config_key"], student))
    return students
