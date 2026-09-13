## 1. 退化署名匹配排除

- [x] 1.1 在 `src/qqstalker_cli/discussion_analysis.py` 新增退化署名判定（剥离 `[\W_]` 后为空，或纯 ASCII 字母数字且长度为 1），并使 `bold_member_names`、`member_aliases` 派生别名、`find_uncovered_members` 的正文匹配面跳过退化署名；退化署名的覆盖仅由 `<<完整署名>>` 精确标记满足。验证：`uv run pyright` 通过
- [x] 1.2 在 `tests/test_analyze_transcript.py` 新增用例：署名 `。` 不命中正文句号、署名 `2` 不命中"砂金2命/2.5倍"、`<<。>>`/`<<2>>` 显式标记正常加粗着色、退化署名不被句号/数字满足覆盖、单字中文名（简/懒）仍按子串命中。验证：`uv run pytest tests/test_analyze_transcript.py -q` 通过

## 2. 未闭合标记残片清理

- [x] 2.1 在 `_sanitize_minutes_paragraph` 中按 `<<[^<>]+>>` 保护完整标记后，剥离非标记片段中的 `<<`/`>>` 字面量并保留相邻文字。验证：`uv run pyright` 与既有纪要相关用例通过
- [x] 2.2 新增用例：`"讨论，<<珂神神了！简认为"` 归一化后不含字面 `<<` 且后续文字保留；完整合法标记、非法完整标记（内部不在名单）行为不变。验证：新增用例通过

## 3. 截断边界屏蔽署名与标记

- [x] 3.1 新增等长掩码边界探测辅助（完整 `<<...>>` 标记与 `_bounded_name_pattern` 署名匹配区替换为等长占位符，退化署名除外），并接入 `truncate_at_sentence`、`_split_complete_sentences`、`truncate_minutes`；硬切落在掩码区内时回退到该区起始之前。验证：`uv run pyright` 通过
- [x] 3.2 新增用例：含 `<<珂神神了！（不打深塔）>>` 的超长纪要截断不在署名内 `！` 处切分、不产生 `<<珂神神了！` 结尾；无限内句界的长句硬切落在标记/署名内部时回退到其起始之前；覆盖选句路径对含标记句子的取舍不变。验证：新增用例通过
- [x] 3.3 在 `_minutes_instruction` 中追加"标记必须成对完整"的自查要求。验证：`uv run python -m compileall -q src` 通过

## 4. 整体验证

- [x] 4.1 运行 `uv run pytest tests/ -q`、`uv run pyright`、`uv run python -m compileall -q src` 全部通过
- [x] 4.2 用鸣潮萌新交流群当期导出重新生成一次分析（不提交产物），确认三张 card 不再出现数字 2/句号被高亮、字面 `<<`、`<<珂神神了！` 残片，参与者名单成员在正文中的缺失情况符合规格预期。验证：人工核对生成的 HTML 纪要段落
