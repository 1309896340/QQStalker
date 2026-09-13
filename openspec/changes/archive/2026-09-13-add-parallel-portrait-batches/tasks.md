## 1. 配置与并发原语

- [x] 1.1 新增 `LLM_PORTRAIT_CONCURRENCY` 读取与校验（`main()` 中 `positive_integer_setting`，默认 2，报错格式对齐 `LLM_DISCUSSION_CONCURRENCY`），并在 `.env.example` 补充变量说明；验证：设置 `LLM_PORTRAIT_CONCURRENCY=0` 时命令在分析开始前终止并说明变量名与原因
- [x] 1.2 按 design.md 决策 2 落实保序并发原语：优先从 `discussion_analysis._run_items` 提取共享工具，否则在 `analyze_transcript.py` 就地实现等价语义（保序、`min(max_workers, len(items))`、并发数 ≤1 时顺序执行）；验证：相关单测覆盖顺序保持与退化路径

## 2. 画像批次并发执行

- [x] 2.1 将 `analyze_all_members` 的批处理循环改为线程池提交：每批次任务封装"构建上下文 → `analyze_member_batch` → `normalize_member_portraits`"，返回该批次画像文本与跳过成员名单，主线程按批次顺序汇总；验证：单测模拟多批次断言最终画像顺序与串行一致
- [x] 2.2 改造 `analyze_member_batch` 的跳过记录：由共享列表 `append` 改为按任务返回（或线程安全收集），确保并发下跳过名单完整并继续从群像速览统计口径排除；验证：单测模拟两批次均有成员补偿失败，断言名单含全部跳过成员
- [x] 2.3 批处理循环中的裸 `print`（批次标题、本批次群员、上下文构建统计）改经 `reporter.print` 输出，并在并发数大于 1 时提示启用的并发数（对齐讨论纪要阶段提示风格）；验证：重定向 stdout 运行时输出无交错，逐行可读

## 3. 测试与回归验证

- [x] 3.1 新增配置校验用例：`LLM_PORTRAIT_CONCURRENCY` 未设置（默认 2）、合法值、0/负数/非整数报错信息（`tests/test_analyze_transcript.py`）；验证：`uv run pytest tests/test_analyze_transcript.py`
- [x] 3.2 新增并发行为用例：并发数受限（记录同时进行中的请求峰值 ≤ 并发数）、批次结果保序、批内补偿请求不并发放大；验证：`uv run pytest tests/test_analyze_transcript.py`
- [x] 3.3 回归验证：`uv run pyright`、`uv run python -m compileall -q src` 与全量 `uv run pytest` 通过；以 `LLM_PORTRAIT_CONCURRENCY=1` 和默认并发各跑一次真实画像分析，确认报告结构与成员清单一致
