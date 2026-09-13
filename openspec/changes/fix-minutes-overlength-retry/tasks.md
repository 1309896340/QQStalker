## 1. 重写循环实现

- [x] 1.1 重写 `_bounded_minutes`：自管至多三次请求（首次 + 两次反馈重写），失败反馈包含校验错误原文与压缩要求（合并同类发言、保留全部主要成员核心观点与讨论结果、300 字内完整概括）；全部失败后再请求一次并按句子边界截断兜底。验证：单测覆盖"两次超长后第三次完整通过则采用完整文本"与"三次均超长则截断兜底"
- [x] 1.2 校验失败反馈中的字数信息可量化（沿用 `normalize_minutes` 的"当前 X 字"错误文案）。验证：单测断言重写 prompt 含失败原因与"300"字样

## 2. 测试与回归

- [x] 2.1 更新既有 `test_minutes_length_cap_retries_then_truncates` 以匹配新请求次数；新增压缩反馈与完整内容保留用例。验证：`uv run python -m unittest tests.test_analyze_transcript`
- [x] 2.2 回归验证：`uv run pyright` 与 `uv run python -m compileall -q src` 全部通过
