"""官方 observer hook 消费：每轮 LLM 流式性能指标（TTFT / 生成窗口 / 输出 token）。

数据来源全部是 Hermes 官方 hook，不侵入 agent 热路径：
  - ``post_api_request``（同步，conversation_loop.py 触发）：
      ``session_id / api_call_count / started_at / ended_at / usage.output_tokens``
  - ``on_stream_delta``（异步 observer worker，run_agent.py 触发）：
      ``session_id / iteration(api_call_count)``，回调时刻 ≈ 该 token 到达时刻

指标口径：
  - TTFT（首 token 延迟）= 首个 delta 回调时刻 − ``started_at``（API 调用开始），
    即"大模型从请求发出到返回第一个 token"的耗时（同进程墙钟，误差仅 observer
    队列的毫秒级延迟）。
  - 生成窗口 gen_ms = ``ended_at`` − 首个 delta 时刻，纯 LLM 输出时间，
    不含工具执行 / API 排队，避免稀释 TPS。
  - TPS 口径：只累计"有首包记录"的调用（文本轮），工具轮（无文本 delta）
    不贡献 output_tokens，防止把工具参数生成混入文本速率。

注册方式照第一方先例（agent/outbound_webhooks.py、agent/shell_hooks.py）：
直接向 ``get_plugin_manager()._hooks`` 追加回调。模块 import 时注册一次。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_TURN_KEYS = ("calls", "ttft_calls", "ttft_ms", "gen_ms", "output_tokens")


class StreamPerfCollector:
    """按 session 聚合"进行中一轮"的流式性能指标（纯逻辑，可单测）。

    - ``begin_turn(sid)``       ：message.start 时开新轮（重置聚合）
    - ``on_first_delta(sid, call, at)``：首个 delta 时刻（首次记录，幂等）
    - ``on_api_done(sid, call, started_at, ended_at, output_tokens)``：API 完成聚合
    - ``end_turn(sid)``         ：message.complete 时取汇总并清理（无调用返回 None）

    线程安全：on_stream_delta 在 observer worker 线程回调，post_api_request 在
    agent 线程同步回调，两者可能并发，统一走锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: Dict[str, Dict[str, Any]] = {}
        # (session_id, api_call_count) -> 首个 delta 时刻（epoch 秒）
        self._pending: Dict[Tuple[str, int], float] = {}

    def begin_turn(self, sid: str) -> None:
        if not sid:
            return
        with self._lock:
            self._turns[sid] = {k: (0 if k != "ttft_ms" else 0.0) for k in _TURN_KEYS}

    def on_first_delta(self, sid: str, call: int, at: float) -> None:
        if not sid:
            return
        key = (sid, call)
        with self._lock:
            # 首个 delta 记录一次；重试/后续 delta 不覆盖
            self._pending.setdefault(key, at)

    def on_api_done(
        self,
        sid: str,
        call: int,
        started_at: float,
        ended_at: float,
        output_tokens: int,
    ) -> None:
        if not sid:
            return
        key = (sid, call)
        with self._lock:
            first = self._pending.pop(key, None)
            turn = self._turns.get(sid)
            if turn is None:
                return  # 无进行中的轮（晚到事件）→ 丢弃，绝不跨轮污染
            turn["calls"] += 1
            if first is None:
                # 工具轮（无文本 delta）：不贡献 TTFT / 生成窗口 / output_tokens，
                # 防止工具参数生成与排队时间稀释 TPS。
                return
            turn["ttft_calls"] += 1
            turn["ttft_ms"] += max(0.0, first - float(started_at)) * 1000
            api_dur = max(0.0, float(ended_at) - float(started_at))
            gen = max(0.0, float(ended_at) - first)
            if gen < 0.3 * api_dur + 0.05:
                # 批量返回特征（provider 攒批，首包≈末包）：客户端观测不到
                # 逐 token 生成窗口，生成贯穿整个 API 调用，退化用 API 总时长，
                # 避免 gen≈0 导致 TPS 虚假放大。
                gen = api_dur
            turn["gen_ms"] += gen * 1000
            turn["output_tokens"] += max(0, int(output_tokens or 0))

    def end_turn(self, sid: str) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        with self._lock:
            turn = self._turns.pop(sid, None)
            if turn is None or not turn["calls"]:
                # 无调用（空轮）或未知 session → 视为无统计
                if turn is None:
                    for key in [k for k in self._pending if k[0] == sid]:
                        self._pending.pop(key, None)
                return None
            # 防御：清理该 session 残留 pending（正常流程 end_turn 前应已配对）
            for key in [k for k in self._pending if k[0] == sid]:
                self._pending.pop(key, None)
            return {
                "calls": turn["calls"],
                "ttft_calls": turn["ttft_calls"],
                "ttft_ms": round(turn["ttft_ms"], 1),
                "gen_ms": round(turn["gen_ms"], 1),
                "output_tokens": turn["output_tokens"],
            }


def _make_post_api_request_cb(collector: StreamPerfCollector):
    """post_api_request：API 调用完成，聚合 TTFT / 生成窗口 / 输出 token。"""

    def _cb(**kwargs: Any) -> None:
        try:
            sid = str(kwargs.get("session_id") or "")
            call = int(kwargs.get("api_call_count") or 0)
            started_at = kwargs.get("started_at")
            ended_at = kwargs.get("ended_at")
            if not sid or started_at is None or ended_at is None:
                return
            usage = kwargs.get("usage") or {}
            out = (
                int(usage.get("output_tokens") or 0)
                if isinstance(usage, dict)
                else 0
            )
            collector.on_api_done(sid, call, float(started_at), float(ended_at), out)
        except Exception:
            logger.debug("stream_perf post_api_request hook failed", exc_info=True)

    return _cb


def _make_on_stream_delta_cb(collector: StreamPerfCollector):
    """on_stream_delta：首个 delta 时刻即首 token 时刻。

    优先使用 payload 的 ``delta_at``（agent 同步 token 路径记录，精确）；
    旧版后端无该字段时回退到回调时刻（异步 worker，仅用于兼容）。
    """

    def _cb(**kwargs: Any) -> None:
        try:
            sid = str(kwargs.get("session_id") or "")
            call = int(kwargs.get("iteration") or 0)
            if not sid:
                return
            delta_at = kwargs.get("delta_at")
            at = float(delta_at) if delta_at is not None else time.time()
            collector.on_first_delta(sid, call, at)
        except Exception:
            logger.debug("stream_perf on_stream_delta hook failed", exc_info=True)

    return _cb


_registered = False


def register_stream_perf_hooks() -> StreamPerfCollector:
    """注册官方 observer hook 并返回全局 collector（幂等，import 时调用一次）。"""
    global _registered
    if _registered:
        return _COLLECTOR
    try:
        from hermes_cli.plugins import get_plugin_manager

        manager = get_plugin_manager()
        manager._hooks.setdefault("post_api_request", []).append(
            _make_post_api_request_cb(_COLLECTOR)
        )
        manager._hooks.setdefault("on_stream_delta", []).append(
            _make_on_stream_delta_cb(_COLLECTOR)
        )
        _registered = True
        logger.debug("stream_perf hooks registered")
    except Exception:
        logger.debug("stream_perf hooks registration failed", exc_info=True)
    return _COLLECTOR


_COLLECTOR = StreamPerfCollector()
