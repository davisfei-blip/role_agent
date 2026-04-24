import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_RESPONSES_BASE_URL = "https://api.openai.com/v1/responses"


def get_responses_config(model_name: str = ""):
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_RESPONSES_BASE_URL).strip()
    model = model_name or os.getenv("OPENAI_MODEL", "")

    if not base_url:
        raise ValueError("缺少 OPENAI_BASE_URL，无法调用 Responses API")
    if not model:
        raise ValueError("缺少模型名，无法调用 Responses API")

    headers = {
        "Content-Type": "application/json",
    }
    if api_key and "ak=" not in base_url:
        headers["Authorization"] = f"Bearer {api_key}"

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "headers": headers,
    }


class ResponsesAPIClient:
    def __init__(self, model_name: str = "", timeout: int = 300, max_empty_retries: int = 2):
        self.model_name = model_name
        self.timeout = timeout
        self.max_empty_retries = max(0, int(max_empty_retries))

    def generate_from_messages(self, messages, on_delta=None):
        instructions, input_items = self._convert_messages(messages)
        return self.generate(input_items, instructions=instructions, on_delta=on_delta)

    def generate(self, input_items, instructions: Optional[str] = None, on_delta=None):
        config = get_responses_config(self.model_name)
        payload = {
            "model": config["model"],
            "input": self._normalize_input_items(input_items),
            "text": {
                "format": {"type": "text"},
                "verbosity": "medium",
            },
            "reasoning": {"effort": "medium"},
        }
        if instructions:
            payload["instructions"] = instructions

        last_response_payload = None
        max_attempts = self.max_empty_retries + 1

        for attempt in range(1, max_attempts + 1):
            if on_delta:
                stream_payload = dict(payload)
                stream_payload["stream"] = True
                text, response_payload = self._stream_response(config, stream_payload, on_delta)
            else:
                text, response_payload = self._single_response(config, payload)

            last_response_payload = response_payload
            if self._has_meaningful_text(text):
                return text, response_payload

            if attempt < max_attempts:
                print(
                    f"⚠️ Responses API 返回空文本，准备重试（第 {attempt}/{max_attempts} 次）。"
                    f" payload摘要: {self._summarize_response_payload(response_payload)}"
                )
                time.sleep(min(attempt, 2))
                continue

        raise ValueError(
            "模型返回空内容，已终止本次调用。"
            f" 响应摘要: {self._summarize_response_payload(last_response_payload)}"
        )

    def _single_response(self, config, payload):
        response = requests.post(
            config["base_url"],
            headers=config["headers"],
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        response_payload = response.json()
        return self._extract_output_text(response_payload), response_payload

    def _stream_response(self, config, payload, on_delta):
        response_payload = None
        parts = []
        with requests.post(
            config["base_url"],
            headers=config["headers"],
            json=payload,
            timeout=self.timeout,
            stream=True,
        ) as response:
            response.raise_for_status()
            for event_name, data in self._iter_sse_events(response):
                event_type = data.get("type") or event_name
                if event_type == "response.output_text.delta":
                    delta = data.get("delta", "")
                    if delta:
                        parts.append(delta)
                        on_delta(delta)
                elif event_type == "response.completed":
                    response_payload = data.get("response")

        if response_payload is None:
            response_payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "".join(parts)}]}]}
        streamed_text = "".join(parts)
        completed_text = self._extract_output_text(response_payload)

        if completed_text:
            if not streamed_text:
                final_text = completed_text
            elif completed_text.startswith(streamed_text):
                final_text = completed_text
            else:
                final_text = completed_text if len(completed_text) >= len(streamed_text) else streamed_text
        else:
            final_text = streamed_text

        return final_text, response_payload

    def _iter_sse_events(self, response):
        current_event = None
        data_chunks = []
        interested_events = {"response.output_text.delta", "response.completed"}

        for raw_line in response.iter_lines(decode_unicode=False):
            line = raw_line or b""

            if line == b"":
                if current_event in interested_events and data_chunks:
                    payload = b"".join(data_chunks).strip()
                    if payload and payload != b"[DONE]":
                        yield current_event, json.loads(payload.decode("utf-8", errors="replace"))
                current_event = None
                data_chunks = []
                continue

            if line.startswith(b"event:"):
                current_event = line[6:].strip().decode("utf-8", errors="replace")
                continue

            if current_event not in interested_events:
                continue

            if line.startswith(b"data:"):
                data_chunks.append(line[5:].lstrip())
            elif data_chunks:
                data_chunks.append(line)

        if current_event in interested_events and data_chunks:
            payload = b"".join(data_chunks).strip()
            if payload and payload != b"[DONE]":
                yield current_event, json.loads(payload.decode("utf-8", errors="replace"))

    def _convert_messages(self, messages) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        instructions = []
        input_items = []

        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                text = self._content_to_text(content)
                if text:
                    instructions.append(text)
                continue
            input_items.append({
                "role": role,
                "content": self._normalize_content(content),
            })

        return ("\n\n".join(part for part in instructions if part) or None), input_items

    def _normalize_input_items(self, input_items):
        normalized = []
        for item in input_items:
            normalized.append({
                "role": item.get("role", "user"),
                "content": self._normalize_content(item.get("content", "")),
            })
        return normalized

    def _normalize_content(self, content):
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}]

        if not isinstance(content, list):
            return [{"type": "input_text", "text": str(content or "")}]

        normalized = []
        for item in content:
            if isinstance(item, str):
                normalized.append({"type": "input_text", "text": item})
                continue

            item_type = item.get("type")
            if item_type in {"input_text", "text"}:
                normalized.append({"type": "input_text", "text": item.get("text", "")})
                continue

            if item_type in {"image_url", "input_image"}:
                image_payload = item.get("image_url") or item.get("input_image") or {}
                image_url = image_payload.get("url") if isinstance(image_payload, dict) else image_payload
                if image_url:
                    normalized.append({"type": "input_image", "image_url": image_url})
                continue

            if item_type in {"video_url", "input_file"}:
                video_payload = item.get("video_url") or item.get("input_file") or {}
                file_data = video_payload.get("url") if isinstance(video_payload, dict) else video_payload
                if file_data:
                    normalized.append({
                        "type": "input_file",
                        "file_data": file_data,
                        "filename": self._guess_filename(file_data),
                    })
                continue

        return normalized or [{"type": "input_text", "text": ""}]

    def _content_to_text(self, content):
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                text = item.get("text")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def _extract_output_text(self, response_payload):
        if not isinstance(response_payload, dict):
            return ""

        direct_text = response_payload.get("output_text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        parts = []
        for item in response_payload.get("output", []) or []:
            item_type = item.get("type")
            if item_type == "message":
                for content_item in item.get("content", []) or []:
                    content_type = content_item.get("type")
                    if content_type in {"output_text", "text"}:
                        text = content_item.get("text", "")
                        if text:
                            parts.append(text)
                    elif content_type == "refusal":
                        refusal = content_item.get("refusal", "")
                        if refusal:
                            parts.append(refusal)
                continue
            if item_type in {"output_text", "text"}:
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def _has_meaningful_text(self, text):
        return isinstance(text, str) and bool(text.strip())

    def _summarize_response_payload(self, response_payload):
        if response_payload is None:
            return "无 response payload"
        if not isinstance(response_payload, dict):
            return f"payload 类型={type(response_payload).__name__}"

        summary = {
            "keys": list(response_payload.keys())[:8],
            "status": response_payload.get("status"),
            "output_len": len(response_payload.get("output", []) or []),
            "output_text_len": len((response_payload.get("output_text") or "").strip()),
        }
        return json.dumps(summary, ensure_ascii=False)

    def _guess_filename(self, data_url):
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            return "input.bin"
        mime_type = data_url.split(";", 1)[0].replace("data:", "", 1)
        extension = mimetypes.guess_extension(mime_type) or ".bin"
        return f"input{extension}"
