## Why

一次完整的画像生成涉及几十到上百次大模型请求（画像批次、纪要分段/归并/逐题、语录精选、群像总览），但系统没有任何持久化缓存：任务中途失败或局部失败后重跑时，所有已成功的请求全部重新计费、重新执行。实测中"仅两个议题纪要失败"的重跑也需要全价重跑全部阶段，既昂贵又缓慢，也让局部失败的重试调试成本极高。

## What Changes

- 新增 LLM 响应文件缓存：请求成功后把响应文本与 finish_reason 以小 JSON 文件写入输出目录下的 `.llm-cache/`；缓存键由阶段标签、模型与请求参数、完整 prompt 的哈希构成。
- 缓存命中时直接返回缓存响应，不发起网络请求；失败的请求（任何异常，含校验失败、内容审查拦截）不写缓存，重跑时自然重试。
- 提供命令行开关 `--no-cache` 跳过缓存读写；默认启用缓存。
- 缓存目录随输出目录走（`analysis/` 已被 git 忽略）；并发写入使用临时文件原子替换；同一进程内各阶段（串行或并发线程）均可安全读写。

## Capabilities

### New Capabilities

- `llm-response-cache`: 大模型响应的文件缓存——命中复用、失败不缓存、开关控制与目录/键隔离规则。

### Modified Capabilities

（无——既有规格只约束"必须发起并校验请求"的行为语义，不约束请求是否可从缓存复用；并发、预算与降级语义不变。）

## Impact

- 新增 `src/qqstalker_cli/llm_cache.py`（缓存读写与键计算、进程内安装接口）。
- `src/qqstalker_cli/analyze_transcript.py`：`request_portraits` 入口挂缓存读写；`analyze_all_members`/入口安装缓存、解析 `--no-cache`。
- `src/qqstalker_cli/generate_portrait.py` 与独立分析入口：传递 `--no-cache` 并安装缓存。
- 测试：`tests/test_analyze_transcript.py` 新增缓存命中、失败不缓存、开关与键隔离用例。
