## 1. 缓存核心

- [x] 1.1 新增 `src/qqstalker_cli/llm_cache.py`：键计算（sha256 阶段+模型+参数+prompt）、`lookup`/`store`（原子写、损坏文件视为未命中清除）、进程级 `install`/`uninstall` 与查询接口。验证：单测覆盖写入后命中、损坏文件清除、不同键隔离
- [x] 1.2 `request_portraits` 入口接入缓存：请求前查询，命中直接返回 `(响应, finish_reason)`；网络成功后写入。验证：单测用 mock transport 断言命中时零网络调用

## 2. 入口接线

- [x] 2.1 `generate_portrait` 与独立分析入口新增 `--no-cache`；输出目录确定后安装缓存（`--no-cache` 时不安装），并在并发画像批次下可用。验证：单测覆盖开关语义；入口 `--help` 出现该参数

## 3. 测试与回归

- [x] 3.1 失败不缓存用例：请求抛异常后无缓存文件，重跑再次发起请求。验证：`uv run python -m unittest tests.test_analyze_transcript`
- [x] 3.2 回归验证：`uv run pyright` 与 `uv run python -m compileall -q src` 全部通过
