"""Request AI group-member portraits for an exported Markdown transcript."""

from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

PROMPT = "分析聊天记录中出现的每个群员的画像"
DEFAULT_TIMEOUT_SECONDS = 120.0


def load_dotenv(env_path: Path) -> None:
    """Load simple KEY=VALUE settings without overwriting explicit environment values."""

    if not env_path.is_file():
        raise FileNotFoundError(f"环境配置文件不存在：{env_path}")

    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{env_path}:{line_number} 不是 KEY=VALUE 格式")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{env_path}:{line_number} 的变量名不能为空")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def required_setting(name: str) -> str:
    """Read a required LLM setting without ever writing secrets to output files."""

    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"请先在 .env 中设置 {name}")
    return value


def extract_text(value: object) -> str:
    """Extract text from a string or OpenAI-compatible content-part collection."""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [extract_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        for key in ("content", "output_text", "value"):
            extracted = extract_text(value.get(key))
            if extracted:
                return extracted
    return ""


def extract_response_text(response_data: dict[str, Any]) -> str:
    """Support Chat Completions, reasoning fields, and Responses API-style output."""

    choices = response_data.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            if isinstance(message, dict):
                for key in ("content", "reasoning_content"):
                    extracted = extract_text(message.get(key))
                    if extracted:
                        return extracted
            extracted = extract_text(first_choice.get("text"))
            if extracted:
                return extracted

    for key in ("output_text", "output"):
        extracted = extract_text(response_data.get(key))
        if extracted:
            return extracted
    data = response_data.get("data")
    if isinstance(data, dict):
        for key in ("answer", "content"):
            extracted = extract_text(data.get(key))
            if extracted:
                return extracted
    return ""


def response_shape_summary(response_data: dict[str, Any]) -> str:
    """Describe response structure without including model output or credentials."""

    root_keys = ", ".join(sorted(response_data.keys())) or "<empty>"
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return f"根字段：{root_keys}；choices 为空或不存在"
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return f"根字段：{root_keys}；choices[0] 类型为 {type(first_choice).__name__}"
    message = first_choice.get("message")
    message_keys = ", ".join(sorted(message.keys())) if isinstance(message, dict) else "<none>"
    return (
        f"根字段：{root_keys}；finish_reason={first_choice.get('finish_reason')!r}；"
        f"message 字段：{message_keys}"
    )


def request_portraits(
    transcript: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
) -> str:
    """Call an OpenAI-compatible chat-completions endpoint and return its text."""

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    request_content = f"{PROMPT}\n\n以下是聊天记录：\n\n{transcript}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": request_content}],
        "stream": False,
    }
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = error.response.text[:1_000]
        raise RuntimeError(f"大模型请求失败（HTTP {error.response.status_code}）：{detail}") from error
    except httpx.HTTPError as error:
        raise RuntimeError(f"无法连接大模型服务：{error}") from error

    response_data: dict[str, Any]
    try:
        response_data = response.json()
    except json.JSONDecodeError as error:
        raise RuntimeError("大模型响应不是 JSON 格式") from error
    if not isinstance(response_data, dict):
        raise RuntimeError("大模型响应根对象不是 JSON 对象")

    content = extract_response_text(response_data)
    if content:
        return content
    raise RuntimeError(
        "大模型响应中没有可用的文本内容；"
        f"响应结构：{response_shape_summary(response_data)}"
    )


def render_html(analysis: str, *, source_path: Path, model: str) -> str:
    """Wrap untrusted model text in a safe, readable standalone HTML document."""

    escaped_analysis = html.escape(analysis)
    escaped_source = html.escape(str(source_path.resolve()))
    escaped_model = html.escape(model)
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>群员画像分析</title>
  <style>
    :root {{ color-scheme: light; font-family: "Microsoft YaHei", "Noto Sans SC", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; color: #1e293b; background: #f0f4ff; }}
    main {{ box-sizing: border-box; width: min(960px, calc(100% - 32px)); margin: 48px auto; }}
    header {{ padding: 36px 40px; color: #fff; border-radius: 24px 24px 0 0;
      background: linear-gradient(135deg, #312e81, #2563eb); box-shadow: 0 24px 64px #c7d2fe; }}
    h1 {{ margin: 0 0 12px; font-size: clamp(1.8rem, 5vw, 2.7rem); letter-spacing: .03em; }}
    .subtitle {{ margin: 0; color: #dbeafe; line-height: 1.65; }}
    article {{ padding: 34px 40px 42px; background: #fff; border-radius: 0 0 24px 24px;
      box-shadow: 0 24px 64px #cbd5e155; }}
    .analysis {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 1rem/1.9 "Microsoft YaHei", "Noto Sans SC", sans-serif; }}
    footer {{ margin-top: 24px; padding: 0 8px; color: #64748b; font-size: .85rem; line-height: 1.7; }}
    code {{ word-break: break-all; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>群员画像分析</h1>
      <p class="subtitle">基于指定消息记录生成。内容为模型分析结果，应结合原始上下文审慎解读。</p>
    </header>
    <article>
      <pre class="analysis">{escaped_analysis}</pre>
    </article>
    <footer>
      <div>生成时间：{html.escape(generated_at)}</div>
      <div>模型：<code>{escaped_model}</code></div>
      <div>消息记录：<code>{escaped_source}</code></div>
    </footer>
  </main>
</body>
</html>
"""


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_markdown", type=Path, help="待分析的消息记录 Markdown 文件")
    parser.add_argument("output_html", type=Path, help="群员画像 HTML 输出路径")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="包含 LLM 配置的文件（默认：.env）",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if not args.input_markdown.is_file():
        raise SystemExit(f"消息记录文件不存在：{args.input_markdown}")

    try:
        load_dotenv(args.env_file)
        model = required_setting("LLM_MODEL")
        analysis = request_portraits(
            args.input_markdown.read_text(encoding="utf-8"),
            base_url=required_setting("LLM_BASE_URL"),
            model=model,
            api_key=required_setting("LLM_API_KEY"),
        )
        args.output_html.parent.mkdir(parents=True, exist_ok=True)
        args.output_html.write_text(
            render_html(analysis, source_path=args.input_markdown, model=model),
            encoding="utf-8",
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(f"群员画像已写入：{args.output_html.resolve()}")


if __name__ == "__main__":
    main()
