import os
import yaml
from openai import OpenAI
from dotenv import load_dotenv
from config import Config

load_dotenv()

config = Config()


def refresh_runtime_state():
    config.reload()


def get_openai_client():
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )


class TeacherAgent:
    def __init__(self, config_file="config.yaml"):
        self.config = self._load_config(config_file)

    def _load_config(self, config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def get_exam_topics(self, role_type):
        if role_type not in self.config:
            raise ValueError(f"未找到角色类型: {role_type}")
        return self.config[role_type]

    def assign_task(self, topic):
        return {
            "topic_name": topic["name"],
            "description": topic["description"],
            "questions": topic["questions"]
        }

    def _chat(self, messages, on_delta=None):
        client = get_openai_client()

        if on_delta:
            stream = client.chat.completions.create(
                model=config.model_name,
                messages=messages,
                stream=True,
            )
            parts = []
            for chunk in stream:
                choices = chunk.choices or []
                if not choices:
                    continue
                delta = choices[0].delta.content or ""
                if not delta:
                    continue
                parts.append(delta)
                on_delta(delta)
            return "".join(parts)

        response = client.chat.completions.create(
            model=config.model_name,
            messages=messages,
        )
        return response.choices[0].message.content

    def evaluate_answer(self, question, student_answer, role, on_delta=None):
        evaluation_prompt = f"""你是一名严格的{role}面试官/老师。请评估以下回答：

问题：{question}

学生回答：
{student_answer}

请从以下维度进行评估：
1. 准确性（0-100分）
2. 完整性（0-100分）
3. 深度（0-100分）
4. 总体评分（0-100分）

然后给出详细的改进建议。格式要求：
评分：
- 准确性：X分
- 完整性：X分
- 深度：X分
- 总体评分：X分

改进建议：
[具体建议]"""

        return self._chat(
            [
                {"role": "system", "content": "你是一名严格但专业的老师，负责评估学生的回答并给出建设性反馈。"},
                {"role": "user", "content": evaluation_prompt}
            ],
            on_delta=on_delta,
        )

    def extract_score(self, evaluation):
        try:
            lines = evaluation.split('\n')
            for line in lines:
                if '总体评分' in line or '总分' in line:
                    import re
                    score_match = re.search(r'(\d+)分', line)
                    if score_match:
                        return int(score_match.group(1))
        except:
            pass
        return 60

    def is_pass(self, score, pass_score=70):
        return score >= pass_score

    def give_feedback(self, evaluation, on_delta=None):
        feedback_prompt = f"""基于以下评估结果，给学生提供具体、可操作的反馈建议，帮助其改进：

{evaluation}

请重点强调：
1. 做得好的地方
2. 需要重点改进的地方
3. 具体的学习建议"""

        return self._chat(
            [
                {"role": "system", "content": "你是一名富有经验的导师，善于给出建设性的反馈。"},
                {"role": "user", "content": feedback_prompt}
            ],
            on_delta=on_delta,
        )
