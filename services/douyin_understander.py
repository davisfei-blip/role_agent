import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from services.responses_api_client import ResponsesAPIClient


DEFAULT_DOUBAO_BASE_URL = "https://api.openai.com/v1/responses"
DEFAULT_PROMPT = """你在为一个 teacher-student 训练系统生成案例材料。
请基于输入的抖音内容，输出严格 JSON：
{
  "title": "一句话概括这条内容",
  "content": "2-4句描述这条内容真正讲了什么、呈现了什么、是否有明显引导/宣传/擦边/卖点表达",
  "risk_points": ["可能的风险点1", "可能的风险点2"]
}
要求：
1. 只输出 JSON，不要加解释。
2. content 要尽量接近人工写 case 时的“内容描述”风格，而不是元数据罗列。
3. 如果风险点不明显，risk_points 返回空数组。"""


load_dotenv()


class DouyinUnderstander:
    def __init__(self, model_name=""):
        self.model_name = model_name

    def understand(self, extracted, material_bundle=None, prompt=None):
        prompt = (prompt or DEFAULT_PROMPT).strip()
        attempted_modes = []
        last_error = None

        material_bundle = material_bundle or {}
        for mode in self._candidate_modes(extracted, material_bundle):
            try:
                message_items = self._build_message_items(extracted, prompt, mode, material_bundle)
                raw_text, raw_response = self._call_chat_completion(message_items)
                parsed = self._parse_json(raw_text)
                return {
                    "status": "completed",
                    "mode": mode,
                    "prompt": prompt,
                    "parsed": parsed,
                    "raw_text": raw_text,
                    "raw_response": raw_response,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "attempted_modes": attempted_modes + [mode],
                }
            except Exception as exc:
                attempted_modes.append(mode)
                last_error = exc

        raise last_error or ValueError("理解失败")

    def _candidate_modes(self, extracted, material_bundle):
        modes = []
        if material_bundle.get("video_path"):
            modes.append("video")
        if material_bundle.get("image_paths"):
            modes.append("image_text")
        modes.append("text_only")
        return modes

    def _build_message_items(self, extracted, prompt, mode, material_bundle):
        lines = [
            "下面是一个抖音内容的结构化信息，请结合多模态内容进行理解。",
            f"内容类型: {extracted.get('content_type', 'unknown')}",
            f"标题/文案: {extracted.get('title', '')}",
            f"作者: {extracted.get('author', '')}",
            f"页面链接: {extracted.get('web_url', '')}",
            f"基础摘要: {extracted.get('content', '')}",
            "用户要求:",
            prompt,
        ]
        text_item = {"type": "text", "text": "\n".join(lines)}

        cover_path = material_bundle.get("cover_path")
        image_paths = material_bundle.get("image_paths") or []
        video_path = material_bundle.get("video_path")
        items = [text_item]
        if cover_path:
            items.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(cover_path)}})

        if mode == "video" and video_path:
            items.append({"type": "video_url", "video_url": {"url": self._file_to_data_url(video_path)}})
            return items

        if mode == "image_text":
            seen = set()
            for image_path in image_paths:
                image_path = str(image_path)
                if image_path == str(cover_path):
                    continue
                if image_path in seen:
                    continue
                seen.add(image_path)
                items.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(image_path)}})
        return items

    def _file_to_data_url(self, filepath):
        filepath = Path(filepath)
        mime_type, _ = mimetypes.guess_type(str(filepath))
        if not mime_type:
            mime_type = "application/octet-stream"
        encoded = base64.b64encode(filepath.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _call_chat_completion(self, message_items):
        client = ResponsesAPIClient(self.model_name)
        return client.generate([{"role": "user", "content": message_items}])

    def _parse_json(self, text):
        text = text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = ""
            for part in parts:
                stripped = part.strip()
                if stripped and not stripped.lower().startswith("json"):
                    text = stripped
                    break
            text = text or parts[-1].strip()

        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise
