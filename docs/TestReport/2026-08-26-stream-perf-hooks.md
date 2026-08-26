# composer-stats 首 token / TPS 准确性修复测试报告

| 项 | 内容 |
|----|------|
| 测试日期 | 2026-08-26 |
| 测试对象 | `tui_gateway/stream_perf_hooks.py`（新增，官方 hook 聚合）+ `tui_gateway/server.py`（挂载）+ `run_agent.py`（delta_at 时间戳）+ `~/.hermes/desktop-plugins/composer-stats/plugin.js`（消费端） |
| 测试类型 | pytest 纯逻辑单测 + Node 插件单测 + **真实 LLM 端到端验证**（cass-code / deepseek-v4-flash 真实调用） |
| 测试工具 | hermes-agent venv Python 3.11（pytest 9.1.1）/ Node.js（esbuild 0.28.1） |
| 测试代码 | `tests/test_tui_gateway_stream_perf.py`（13 用例）<br>`~/.hermes/desktop-plugins/composer-stats/test_plugin.js`（63 断言）<br>`/tmp/e2e_stream_perf.py`（真实端到端） |
| 结论 | **PASS**（pytest 13/13 + 插件 63/63 + 真实调用 TTFT 1.55s 验证通过） |

---

## 一、背景与根因（源码实证）

用户反馈统计条「首 token 平均时间、TPS」不准确，要求首 token 必须是大模型**返回第一个 token**的时间。

### 根因 1：首 token 是端到端感知延迟，不是 TTFT

旧实现（plugin.js `message.delta` 分支）：`首 token = 首个 message.delta 到达时刻 − message.start 时刻 − 工具耗时`。

- `message.start` 在**用户消息被 gateway 接受**时发出（`tui_gateway/server.py:11418`），此时 LLM 请求**尚未发出**
- 差值包含：上下文压缩、prompt 构建、provider 排队、TTFT、网络传输
- 实测截图 14s 即被前置耗时污染（DeepSeek 缓存命中 99% 场景纯 TTFT 应远小于此）

### 根因 2：TPS 被工具/排队时间系统性稀释

旧实现（plugin.js `session.usage` 分支）：`TPS = Δoutput / Δt(墙钟)`。

- `agent.session_output_tokens` 只在 **API 调用完成时**一次性累加（`agent/conversation_loop.py:4253`）
- usage ticker 在计数器冻结时**跳过不发帧**（`tui_gateway/server.py:11337`）
- 帧间 Δt 包含工具执行、API 排队等非生成时间 → 速率被稀释（截图 23 步工具调用场景尤其严重）

### 根因 3（实测发现）：observer 回调时刻失真

官方 `on_stream_delta` hook 经**异步 worker 队列**派发（`agent/plugin_stream_hooks.py`），回调执行时刻相对真实 token 到达有不可控延迟（实测拖到流结束）。因此时间戳必须在 agent 同步 token 路径（enqueue 时刻）记录，随 payload 下发 `delta_at`。

### 根因 4（实测发现）：聚合键不匹配

`message.start` 的 UI 侧 `sid`（`agent:main:cli:chat:xxx`）与 hook 的 `session_id`（`agent.session_id` 内部会话 id）不同，聚合键必须统一用 `agent.session_id`。

### 根因 5（实测发现）：批量返回 provider 无生成窗口

cass-code（deepseek-v4-flash）实测为**批量返回**（2 个 delta 间隔 22ms，token 一次性到达），客户端观测不到逐 token 生成窗口 → 生成窗口≈0 会虚假放大 TPS。处理：生成窗口 < API 总时长 30% 时退化用 API 总时长（端到端吞吐口径）。

## 二、修复方案

| 文件 | 改动 |
|------|------|
| `tui_gateway/stream_perf_hooks.py`（新增） | 注册官方 hook（`post_api_request` + `on_stream_delta`，照 `agent/outbound_webhooks.py:194` 第一方先例），按 `(session_id, api_call_count)` 聚合 TTFT / 生成窗口 / 输出 token；批量返回自适应退化 |
| `tui_gateway/server.py` | import + 幂等注册（3 行）；`message.start` 处 `begin_turn(agent.session_id)`（2 行）；`message.complete` 附 `stream_perf`（5 行） |
| `run_agent.py` | `on_stream_delta` enqueue 补 `delta_at=time.time()`（同步 token 路径，精确时间戳，4 行） |
| `plugin.js`（桌面插件） | 删除旧的首 token 事件差计算与 usage 帧差 TPS 采样；`message.complete` 消费 `stream_perf`：首 token 平均 = ΣTTFT/有首包调用数，TPS = Σ输出 token/Σ生成窗口 |

**指标口径（最终）**：
- 首 token 平均 = Σ(首个 delta 时刻 − started_at) / 有首包记录的调用数 = **大模型请求发出 → 返回第一个 token** 的纯 TTFT
- TPS = Σ输出 token / Σ生成窗口（真流式 provider 用生成窗口；批量返回退化用 API 总时长，不含工具/排队）

## 三、测试用例与执行结果

### 3.1 聚合逻辑 pytest 单测（13 用例，`tests/test_tui_gateway_stream_perf.py`）

测试方法：`StreamPerfCollector` 纯逻辑直测（turn 生命周期 / TTFT / 生成窗口 / 会话隔离 / 批量退化）。

| # | 用例 | 覆盖点 | 结果 |
|---|------|--------|------|
| 1 | begin_turn 后直接 end_turn → None | 空轮语义 | ✅ PASS |
| 2 | 未 begin 的 end_turn → None | 防御 | ✅ PASS |
| 3 | end_turn 后状态清理（二次 end → None） | turn 生命周期 | ✅ PASS |
| 4 | TTFT = first_delta − started_at（14s） | **首 token 核心口径** | ✅ PASS |
| 5 | 首个 delta 只记录一次（重复 delta 不覆盖） | 幂等 | ✅ PASS |
| 6 | 无 delta 的调用（工具轮）不贡献 TTFT | 工具轮隔离 | ✅ PASS |
| 7 | 多调用累计（TTFT/gen/output 累加） | 跨调用累计 | ✅ PASS |
| 8 | 生成窗口 = ended − first_delta（16s） | **TPS 生成窗口口径** | ✅ PASS |
| 9 | 无 delta 调用不计 output_tokens（防稀释 TPS） | 口径一致性 | ✅ PASS |
| 10 | 批量返回退化：gen 用 API 总时长（30s） | **批量 provider 自适应** | ✅ PASS |
| 11 | 真流式不退化：保留 16s 生成窗口 | 流式口径保持 | ✅ PASS |
| 12 | 两会话互不串数据（TTFT/output 独立） | 会话隔离 | ✅ PASS |
| 13 | 无 delta 调用不泄漏 pending（下轮干净） | 状态清理 | ✅ PASS |

**测试统计：13 passed, 0 failed**

```
$ venv/bin/python -m pytest tests/test_tui_gateway_stream_perf.py -q
13 passed in 0.59s
```

### 3.2 桌面插件单测（63 断言，`test_plugin.js`）

测试方法：esbuild bundle + SDK/React stub，覆盖 stream_perf 消费、token 累计、持久化、会话隔离、格式化。

| 场景 | 覆盖点 | 结果 |
|------|--------|------|
| 完整一轮（2 次 LLM 调用 + 2 次工具） | stream_perf 累计（TTFT 14s/调用数 2/生成窗口 16s/输出 700） | ✅ PASS |
| 第二轮 | 跨轮累计（TTFT 14.6s / gen 17.2s / genOut 1000） | ✅ PASS |
| 格式化函数 | fmtDur/fmtFirstToken/fmtTps/fmtCache 全部 | ✅ PASS |
| session.usage 仅更新 token | **旧 TPS 采样字段已移除**（无 rateCount） | ✅ PASS |
| 无 session_id 事件忽略 | 事件隔离 | ✅ PASS |
| 会话隔离 | A/B 互不串数据、无焦点空状态 | ✅ PASS |
| 持久化 | 首 token / 生成窗口字段恢复保留 | ✅ PASS |
| stream_perf 消费 | TTFT 累计、首 token 平均 = 2s、TPS = 33.3 tok/s | ✅ PASS |
| 无 stream_perf 的 complete | 不污染指标（兼容旧后端） | ✅ PASS |
| register + render 真实调用链 | jsx 双参数不崩（回归） | ✅ PASS |

**测试统计：63 passed, 0 failed**

```
$ node ~/.hermes/desktop-plugins/composer-stats/test_plugin.js
前端统计逻辑：63 通过 / 0 失败
```

### 3.3 真实 LLM 端到端验证（`/tmp/e2e_stream_perf.py`）

测试方法：真实初始化 agent（cass-code / deepseek-v4-flash，即当前会话同款 provider），走 `run_conversation` 完整 turn，验证官方 hook 在真实运行中触发并聚合。

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | hook 注册成功且幂等（重复注册返回同一 collector） | ✅ PASS |
| 2 | `post_api_request`（同步 invoke）触发聚合 | ✅ PASS |
| 3 | `on_stream_delta`（异步 worker）触发，`delta_at` 精确时间戳生效 | ✅ PASS |
| 4 | 聚合键统一用 `agent.session_id`（hook 侧 session_id 与 UI sid 不同，已修复） | ✅ PASS |
| 5 | 真实 TTFT：`stream_perf.ttft_ms = 1550.0` → **首 token 平均 = 1.55s**（请求发出→第一个 token） | ✅ PASS |
| 6 | 批量返回退化：`gen_ms = 1551.2`（= API 总时长），TPS = 6.4 tok/s（10 token / 1.55s，口径正确） | ✅ PASS |

```
真实 turn 完成 wall=2.08s agent.session_id='20260826_105857_663603'
stream_perf = {'calls': 1, 'ttft_calls': 1, 'ttft_ms': 1550.0, 'gen_ms': 1551.2, 'output_tokens': 10}
→ 首 token 平均 = 1.55s；TPS = 6.4 tok/s
端到端验证：PASS
```

### 3.4 回归测试

| 范围 | 结果 |
|------|------|
| `tests/test_tui_gateway_server.py` + `test_tui_gateway_ws.py` + `test_tui_gateway_queue_on_busy.py` + `test_tui_gateway_event_replay.py` + `test_tui_gateway_loop_noise.py` | 674 passed / 2 failed（`test_load_enabled_toolsets_*` 为 **预存在环境失败**，已在未改动 main 分支复现，与本次改动无关） |
| `tui_gateway.server` import | ✅ OK |

## 四、验证结论

### 已验证 ✅
- 首 token 平均 = **真实 TTFT**（请求发出 → 第一个 token），真实调用实测 1.55s
- TPS = 输出 token / 纯生成窗口（真流式）或 API 总时长（批量返回），不含工具/排队时间
- 官方 hook 方案：`run_agent.py` 仅补 `delta_at` 时间戳字段，未改任何热路径逻辑；`chat_completion_helpers.py` / `plugins.py` 零改动
- 聚合键修复：统一 `agent.session_id`，多会话隔离不串数据

### 待验证（需桌面 App 重启后确认）
| 项 | 说明 |
|----|------|
| 桌面状态栏真实渲染 | 需重启桌面 App（gateway 加载新 hook + server.py 改动）后，发一条消息观察 `首 token` 与 `tok/s` 显示 |
| 真流式 provider 表现 | 当前 cass-code 为批量返回（退化口径）；若换真流式 provider（逐 token），TPS 将使用生成窗口口径 |

### 更新影响说明
- 核心改动仅 3 处小增量（新文件 + server.py 10 行 + run_agent.py 4 行），全部为附加代码
- 改动提交于 `feat/stream-perf-metrics` 分支并推 fork；`hermes update` 后需重新 merge 该分支生效（commit 不会丢失）
- 桌面插件部分（`~/.hermes/desktop-plugins/`）完全不受更新影响
