## 1. 匹配与标记核心

- [x] 1.1 重构 `bold_member_names`：按名字生成带词边界条件的 pattern（含 ASCII 字母/数字的名字加 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`），按长度降序合并为单交替表达式；新增 `<<署名>>` 标记处理——内部文本与可信名单精确匹配的标记转为 `**名字**`，未通过校验的标记剥壳保留文字；用 pytest 验证 `nt` 不命中 `break/continue`、命中独立 `nt`。验证：`uv run pytest tests/test_analyze_transcript.py -k member`
- [x] 1.2 新增别名派生：取群名片第一个 `(` 或 `（` 之前的非空主干作为别名，与其他成员规范名/别名冲突时放弃；`with_member_highlights` 的出现判定与 `member_styles` 同步覆盖别名（别名指向规范名同一颜色）。验证：单测覆盖 `祥子(ut 不重要了健康才重要）` → `祥子` 命中且颜色一致、冲突别名被放弃

## 2. 生成侧要求

- [x] 2.1 更新 `_minutes_instruction`：要求提及成员逐字使用消息行署名中的完整群名片、不得缩写改写，并说明可用 `<<完整署名>>` 标注成员。验证：检查 prompt 文案与归一化兼容（`_normalize_minutes_text` 不剥离 `<<>>`）

## 3. 测试与回归

- [x] 3.1 补充 `tests/test_analyze_transcript.py` 用例：标记合法转换、非法标记剥壳、别名与规范名同色、词边界、`appeared` 别名判定。验证：`uv run pytest tests/test_analyze_transcript.py`
- [x] 3.2 回归验证：`uv run pyright` 与 `uv run python -m compileall -q src` 全部通过

## 4. 特殊字符成员名修复（生成报告后追加）

- [x] 4.1 包裹粗体前对姓名内 Markdown/HTML 特殊字符转义（`&`、`<`、`>` 实体转义，`` \ ` * _ [ ] ( ) # ! `` 反斜杠转义），词边界类扩展 `[A-Za-z0-9_]`，`appeared` 判定改用转义后形式。验证：单测覆盖 `*new LS_Hower`、`_` 成员名，并用 python-markdown 渲染确认配对完整
- [x] 4.2 `_normalize_minutes_text` 剥除全部星号残留；`_minutes_instruction` 补充纯文本要求（除 `<<完整署名>>` 外不得输出任何标记符号）。验证：单测覆盖单星号剥除与 prompt 文案
