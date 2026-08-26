"""stream_perf_hooks 聚合逻辑单元测试。

覆盖 StreamPerfCollector 的 turn 聚合语义：
  - TTFT = first_delta_at − started_at（仅对有首包记录的 API 调用）
  - 生成窗口 gen_ms = ended_at − first_delta_at
  - TPS 口径：只累计"有生成窗口"的调用的 output_tokens，避免工具轮/排队时间稀释
  - turn 边界与 session 隔离
"""

import pytest

from tui_gateway.stream_perf_hooks import StreamPerfCollector


def _collector():
    return StreamPerfCollector()


class TestTurnLifecycle:
    def test_begin_turn_returns_empty(self):
        c = _collector()
        c.begin_turn("s1")
        assert c.end_turn("s1") is None

    def test_end_turn_without_begin_is_none(self):
        c = _collector()
        assert c.end_turn("s1") is None

    def test_end_turn_clears_state(self):
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 1.0)
        c.on_api_done("s1", 0, 0.5, 10.5, 200)
        summary = c.end_turn("s1")
        assert summary is not None
        assert summary["calls"] == 1
        # 二次 end 应为 None（已清理）
        assert c.end_turn("s1") is None


class TestTtft:
    def test_ttft_is_first_delta_minus_started_at(self):
        c = _collector()
        c.begin_turn("s1")
        # started_at = 100.0，首个 delta 在 114.0 → TTFT = 14.0s
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        summary = c.end_turn("s1")
        assert summary["ttft_calls"] == 1
        assert summary["ttft_ms"] == pytest.approx(14000.0)

    def test_first_delta_only_recorded_once(self):
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_first_delta("s1", 0, 200.0)  # 后续 delta 不覆盖
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        summary = c.end_turn("s1")
        assert summary["ttft_ms"] == pytest.approx(14000.0)

    def test_call_without_first_delta_skips_ttft(self):
        # 工具轮：无文本 delta → 不贡献 TTFT
        c = _collector()
        c.begin_turn("s1")
        c.on_api_done("s1", 0, 100.0, 120.0, 50)
        summary = c.end_turn("s1")
        assert summary["calls"] == 1
        assert summary["ttft_calls"] == 0
        assert summary["ttft_ms"] == 0.0

    def test_multiple_calls_accumulate(self):
        c = _collector()
        c.begin_turn("s1")
        # call 0: started=100, delta=114, ended=130, out=300
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        # call 1: started=131, delta=133, ended=145, out=400
        c.on_first_delta("s1", 1, 133.0)
        c.on_api_done("s1", 1, 131.0, 145.0, 400)
        summary = c.end_turn("s1")
        assert summary["calls"] == 2
        assert summary["ttft_calls"] == 2
        assert summary["ttft_ms"] == pytest.approx((14.0 + 2.0) * 1000)
        assert summary["gen_ms"] == pytest.approx((16.0 + 12.0) * 1000)
        assert summary["output_tokens"] == 700


class TestGenWindow:
    def test_gen_window_is_ended_minus_first_delta(self):
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        summary = c.end_turn("s1")
        # 生成窗口 = 130 − 114 = 16s
        assert summary["gen_ms"] == pytest.approx(16000.0)

    def test_output_tokens_only_counted_for_window_calls(self):
        # 无 delta 的调用（工具轮）不计 output_tokens，避免稀释 TPS
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        c.on_api_done("s1", 1, 131.0, 150.0, 500)  # 工具轮，无 delta
        summary = c.end_turn("s1")
        assert summary["calls"] == 2
        assert summary["ttft_calls"] == 1
        assert summary["output_tokens"] == 300  # 只累计有窗口的调用

    def test_batch_provider_degrades_gen_to_api_duration(self):
        # 批量返回（provider 攒批，首包≈末包）：gen 窗口 ≈ 0，退化用 API 总时长，
        # 避免 TPS 虚假放大。started=100, first=129.9, ended=130 → gen=0.1s < 30%*30s
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 129.9)
        c.on_api_done("s1", 0, 100.0, 130.0, 400)
        summary = c.end_turn("s1")
        assert summary is not None
        assert summary["ttft_ms"] == pytest.approx((129.9 - 100.0) * 1000)
        # gen 退化为 API 总时长 30s
        assert summary["gen_ms"] == pytest.approx(30000.0)
        # TPS = 400 / 30 = 13.3 tok/s（端到端吞吐口径）
        assert summary["output_tokens"] / (summary["gen_ms"] / 1000) == pytest.approx(400 / 30)

    def test_streaming_provider_keeps_gen_window(self):
        # 真流式：首包早，生成窗口占比大，不退化为 API 总时长
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        summary = c.end_turn("s1")
        assert summary is not None
        assert summary["gen_ms"] == pytest.approx(16000.0)  # 16s 窗口，不退化


class TestRealtimeUpdate:
    def test_on_update_fires_increment_after_api_done(self):
        updates = []
        c = StreamPerfCollector(on_update=lambda sid, perf: updates.append((sid, dict(perf))))
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        assert len(updates) == 1
        sid, perf = updates[0]
        assert sid == "s1"
        assert perf["calls"] == 1
        assert perf["ttft_calls"] == 1
        assert perf["ttft_ms"] == pytest.approx(14000.0)
        assert perf["gen_ms"] == pytest.approx(16000.0)
        assert perf["output_tokens"] == 300

    def test_on_update_not_fired_for_tool_only_call(self):
        updates = []
        c = StreamPerfCollector(on_update=lambda sid, perf: updates.append(perf))
        c.begin_turn("s1")
        c.on_api_done("s1", 0, 100.0, 130.0, 50)  # 无 delta（工具轮）
        assert updates == []

    def test_on_update_fires_per_call(self):
        updates = []
        c = StreamPerfCollector(on_update=lambda sid, perf: updates.append(dict(perf)))
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        c.on_first_delta("s1", 1, 133.0)
        c.on_api_done("s1", 1, 131.0, 145.0, 400)
        assert len(updates) == 2
        # 增量语义：每次调用独立推送
        assert updates[0]["ttft_ms"] == pytest.approx(14000.0)
        assert updates[1]["ttft_ms"] == pytest.approx(2000.0)

    def test_set_on_update_replaces_callback(self):
        first = []
        second = []
        c = StreamPerfCollector(on_update=lambda sid, perf: first.append(perf))
        c.set_on_update(lambda sid, perf: second.append(perf))
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        assert first == []
        assert len(second) == 1


class TestSessionIsolation:
    def test_sessions_do_not_cross_talk(self):
        c = _collector()
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 114.0)
        c.on_api_done("s1", 0, 100.0, 130.0, 300)
        # s2 有自己独立的 pending
        c.begin_turn("s2")
        c.on_first_delta("s2", 0, 8.0)
        c.on_api_done("s2", 0, 6.0, 20.0, 100)
        s1 = c.end_turn("s1")
        s2 = c.end_turn("s2")
        assert s1["ttft_ms"] == pytest.approx(14000.0)
        assert s2["ttft_ms"] == pytest.approx(2000.0)
        assert s1["output_tokens"] == 300
        assert s2["output_tokens"] == 100

    def test_api_done_without_first_delta_does_not_leak_pending(self):
        c = _collector()
        c.begin_turn("s1")
        c.on_api_done("s1", 3, 100.0, 120.0, 50)
        summary = c.end_turn("s1")
        assert summary is not None
        # 下一个 turn 不应残留 pending
        c.begin_turn("s1")
        c.on_first_delta("s1", 0, 10.0)
        c.on_api_done("s1", 0, 8.0, 30.0, 200)
        s = c.end_turn("s1")
        assert s["ttft_ms"] == pytest.approx(2000.0)
