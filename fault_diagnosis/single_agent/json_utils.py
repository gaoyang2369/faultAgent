"""Model JSON extraction and repair prompt helpers."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from .errors import SingleAgentExecutionError
from .serialization import preview

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_text(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise SingleAgentExecutionError("模型未返回有效 JSON")
    block_match = _JSON_BLOCK_RE.search(stripped)
    if block_match:
        return block_match.group(1).strip()
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        return stripped[first_brace : last_brace + 1]
    raise SingleAgentExecutionError("模型返回内容中未找到 JSON 对象")


def loads_json_object(text: str) -> dict[str, Any]:
    parse_errors: list[str] = []
    for parser_name, parser in (("json", json.loads), ("literal", ast.literal_eval)):
        try:
            payload = parser(text)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{parser_name}: {exc}")
            continue
        if isinstance(payload, dict):
            return payload
        parse_errors.append(f"{parser_name}: 返回值不是 JSON 对象")
    raise SingleAgentExecutionError("模型 JSON 解析失败：" + "；".join(parse_errors))


def build_json_repair_prompt(raw_text: str, error_message: str) -> str:
    return f"""
你是 JSON 修复器。
下面内容原本应该是一个 JSON 对象，但解析失败。
请只输出修复后的 JSON 对象，不要输出解释、Markdown 或代码块。

解析错误：{error_message}

待修复内容：
{preview(raw_text, 6000)}
""".strip()
