"""Request AI group-member portraits for an exported Markdown transcript."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import bleach
import httpx
import markdown

PROMPT = """分析聊天记录中出现的每个群员的画像。

任务目标：仅基于本批次给出的聊天记录，为成员清单中的每一位成员分别写出画像；不能遗漏、合并或新增名单外的人。即使某成员只有一条消息，也必须保留其独立条目，并明确标注“证据不足”。

对每位成员严格使用以下 Markdown 结构（成员姓名必须与清单完全一致）：

### 成员姓名
- **活跃度与参与方式**：发言频率、时段、消息长度、主动发起或跟随讨论的倾向。
- **性格与互动风格**：从可观察行为提炼的性格特点、情绪表达、边界感与互动模式。
- **语言风格**：常用词、语气、句式、玩梗/表情/引用习惯、论证或吐槽方式。
- **兴趣话题与观点**：反复出现的影视、游戏、生活、社会或其他话题，以及表达立场的方式。
- **群内角色定位**：例如话题发起者、知识补充者、气氛组、评论者、旁观者、协调者等；说明依据。
- **关系与影响**：只描述记录中直接可见的互动对象、协作、调侃或分歧，不猜测现实关系。
- **一句话画像**：简洁、可读、不过度标签化的总结。
- **证据与置信度**：列出 1—3 条简短的可验证线索；区分“明确事实”和“合理推测”。

约束：
1. 覆盖清单中的每一位成员，按清单顺序输出，不要只写活跃成员。
2. 不把昵称、性别、年龄、职业、住址、健康或现实关系等敏感信息当作事实；没有直接证据时写“无法判断”或“推测”。
3. 不杜撰聊天记录中不存在的经历、观点或关系；避免侮辱性、诊断式或绝对化标签。
4. 输出只包含成员画像，不要说明你的推理过程、任务说明或结语。
"""
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOKENS = 4_096
DEFAULT_MEMBERS_PER_REQUEST = 6
DEFAULT_MAX_INPUT_CHARACTERS = 24_000
MESSAGE_BLOCK_PATTERN = re.compile(
    r"(?ms)^## [^\n]+\n\n> \*\*(?P<member>.+?)\*\*\n>\n.*?(?=^## |\Z)"
)
ALLOWED_MARKDOWN_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "del",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
ALLOWED_MARKDOWN_ATTRIBUTES = {"a": ["href", "title"]}


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


def positive_integer_setting(name: str, default: int) -> int:
    """Read an optional positive integer setting from the environment."""

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是正整数") from error
    if parsed <= 0:
        raise RuntimeError(f"{name} 必须是正整数")
    return parsed


def extract_member_messages(transcript: str) -> list[tuple[str, list[str]]]:
    """Group every exported transcript message by its displayed group card."""

    members: dict[str, list[str]] = {}
    for match in MESSAGE_BLOCK_PATTERN.finditer(transcript):
        member = match.group("member").strip()
        if member:
            members.setdefault(member, []).append(match.group(0).strip())
    if not members:
        raise RuntimeError("未能从消息记录中识别成员；请使用 export_markdown.py 生成的文件")
    return list(members.items())


def build_member_prompt(member_batch: tuple[tuple[str, list[str]], ...]) -> str:
    """Build one bounded, evidence-focused request for a fixed member list."""

    roster = "\n".join(
        f"- {member}（本批次 {len(messages)} 条消息）"
        for member, messages in member_batch
    )
    evidence = "\n\n".join(
        f"## 成员：{member}\n\n" + "\n\n".join(messages)
        for member, messages in member_batch
    )
    return f"""{PROMPT}

本批次必须覆盖的成员清单：
{roster}

以下为本批次成员的原始消息记录：

{evidence}
"""


def has_member_heading(analysis: str, member: str) -> bool:
    """Check that the response contains the exact required heading for a member."""

    return re.search(rf"(?m)^###\s+{re.escape(member)}(?:\s|$)", analysis) is not None


def batch_members(
    members: list[tuple[str, list[str]]],
    *,
    max_members: int,
    max_input_characters: int,
) -> list[tuple[tuple[str, list[str]], ...]]:
    """Pack members into bounded prompts while keeping every member intact."""

    batches: list[tuple[tuple[str, list[str]], ...]] = []
    current_batch: list[tuple[str, list[str]]] = []
    current_size = 0
    for member, messages in members:
        member_size = sum(len(message) for message in messages)
        should_start_new_batch = current_batch and (
            len(current_batch) >= max_members
            or current_size + member_size > max_input_characters
        )
        if should_start_new_batch:
            batches.append(tuple(current_batch))
            current_batch = []
            current_size = 0
        current_batch.append((member, messages))
        current_size += member_size
    if current_batch:
        batches.append(tuple(current_batch))
    return batches


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
    prompt: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    max_tokens: int,
    timeout_seconds: float,
) -> tuple[str, str | None]:
    """Call an OpenAI-compatible chat-completions endpoint and return its text."""

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
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
        choices = response_data.get("choices")
        finish_reason: str | None = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            candidate = choices[0].get("finish_reason")
            if isinstance(candidate, str):
                finish_reason = candidate
        return content, finish_reason
    raise RuntimeError(
        "大模型响应中没有可用的文本内容；"
        f"响应结构：{response_shape_summary(response_data)}"
    )


def analyze_member_batch(
    member_batch: tuple[tuple[str, list[str]], ...],
    *,
    base_url: str,
    model: str,
    api_key: str,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
    """Analyze one member batch and retry missing or truncated profiles individually."""

    analysis, finish_reason = request_portraits(
        build_member_prompt(member_batch),
        base_url=base_url,
        model=model,
        api_key=api_key,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    missing_members = [
        member for member, _ in member_batch if not has_member_heading(analysis, member)
    ]
    if finish_reason == "length":
        missing_members = [member for member, _ in member_batch]

    if not missing_members:
        return analysis

    recovered_profiles: list[str] = []
    for member, messages in member_batch:
        if member not in missing_members:
            continue
        print(f"正在补充群员画像：{member}", flush=True)
        recovered, recovered_reason = request_portraits(
            build_member_prompt(((member, messages),)),
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        if recovered_reason == "length" or not has_member_heading(recovered, member):
            raise RuntimeError(
                f"群员 {member} 的画像仍不完整；请提高 LLM_MAX_TOKENS 后重试"
            )
        recovered_profiles.append(recovered)

    if finish_reason == "length":
        return "\n\n".join(recovered_profiles)
    return "\n\n".join((analysis, *recovered_profiles))


def analyze_all_members(
    transcript: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    max_tokens: int,
    timeout_seconds: float,
    members_per_request: int,
    max_input_characters: int,
) -> str:
    """Produce a complete portrait section for every member in the transcript."""

    members = extract_member_messages(transcript)
    sections = [
        "# 群员画像分析",
        "",
        f"> 已识别 {len(members)} 位在消息记录中出现的群员，按成员分批分析并逐批校验。",
        "",
        "---",
    ]
    member_batches = batch_members(
        members,
        max_members=members_per_request,
        max_input_characters=max_input_characters,
    )
    for index, member_batch in enumerate(member_batches, 1):
        message_count = sum(len(messages) for _, messages in member_batch)
        print(
            f"正在分析群员批次 {index}/{len(member_batches)}"
            f"（{len(member_batch)} 人、{message_count} 条消息）",
            flush=True,
        )
        batch_analysis = analyze_member_batch(
            member_batch,
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        sections.extend(("", f"## 群员批次 {index}", "", batch_analysis))
    return "\n".join(sections)


def render_html(analysis: str, *, source_path: Path, model: str) -> str:
    """Wrap untrusted model text in a safe, readable standalone HTML document."""

    rendered_markdown = markdown.markdown(
        analysis,
        extensions=("extra", "sane_lists", "nl2br"),
        output_format="html",
    )
    analysis_html = bleach.clean(
        rendered_markdown,
        tags=ALLOWED_MARKDOWN_TAGS,
        attributes=ALLOWED_MARKDOWN_ATTRIBUTES,
        protocols=("http", "https", "mailto"),
        strip=True,
    )
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
    .analysis {{ overflow-wrap: anywhere; font: 1rem/1.9 "Microsoft YaHei", "Noto Sans SC", sans-serif; }}
    .analysis > :first-child {{ margin-top: 0; }}
    .analysis > :last-child {{ margin-bottom: 0; }}
    .analysis h1, .analysis h2, .analysis h3 {{ color: #172554; line-height: 1.35; }}
    .analysis h1 {{ margin: 0 0 1.25rem; font-size: 1.85rem; }}
    .analysis h2 {{ margin: 2.6rem 0 1rem; padding-bottom: .55rem; border-bottom: 2px solid #dbeafe; font-size: 1.45rem; }}
    .analysis h3 {{ margin: 2rem 0 .7rem; padding-left: .8rem; border-left: 4px solid #6366f1; font-size: 1.14rem; }}
    .analysis p {{ margin: .75rem 0; }}
    .analysis ul, .analysis ol {{ margin: .9rem 0; padding-left: 1.5rem; }}
    .analysis li {{ margin: .42rem 0; padding-left: .15rem; }}
    .analysis blockquote {{ margin: 1.2rem 0; padding: .85rem 1rem; color: #475569; background: #f8fafc; border-left: 4px solid #818cf8; border-radius: 0 12px 12px 0; }}
    .analysis blockquote p {{ margin: 0; }}
    .analysis strong {{ color: #312e81; }}
    .analysis hr {{ margin: 2rem 0; border: 0; border-top: 1px solid #e2e8f0; }}
    .analysis table {{ display: block; width: 100%; margin: 1.25rem 0; overflow-x: auto; border-collapse: collapse; border: 1px solid #dbeafe; border-radius: 12px; }}
    .analysis th, .analysis td {{ padding: .7rem .85rem; text-align: left; vertical-align: top; border: 1px solid #dbeafe; }}
    .analysis th {{ color: #1e3a8a; background: #eff6ff; font-weight: 700; }}
    .analysis tr:nth-child(even) {{ background: #f8fafc; }}
    .analysis code {{ padding: .12rem .35rem; color: #7c2d12; background: #fff7ed; border-radius: 5px; font-family: Consolas, monospace; }}
    .analysis pre {{ padding: 1rem; overflow-x: auto; color: #e2e8f0; background: #172554; border-radius: 12px; }}
    .analysis pre code {{ padding: 0; color: inherit; background: transparent; }}
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
      <div class="analysis">{analysis_html}</div>
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
        analysis = analyze_all_members(
            args.input_markdown.read_text(encoding="utf-8"),
            base_url=required_setting("LLM_BASE_URL"),
            model=model,
            api_key=required_setting("LLM_API_KEY"),
            max_tokens=positive_integer_setting("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            timeout_seconds=float(
                positive_integer_setting("LLM_TIMEOUT_SECONDS", int(DEFAULT_TIMEOUT_SECONDS))
            ),
            members_per_request=positive_integer_setting(
                "LLM_MEMBERS_PER_REQUEST",
                DEFAULT_MEMBERS_PER_REQUEST,
            ),
            max_input_characters=positive_integer_setting(
                "LLM_MAX_INPUT_CHARACTERS",
                DEFAULT_MAX_INPUT_CHARACTERS,
            ),
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
