"""Exercise pacing with a virtual clock, without model calls or real sleeps."""
import asyncio
from types import SimpleNamespace
import pytest
from rescuetwin import api


class StopClock(Exception):
    pass


@pytest.mark.parametrize('speed', [1, 2, 4, 8])
def test_pacing_runs_supervisor_and_recorder_once_per_step(monkeypatch, speed):
    now = [0.]
    ticks, captures, decisions = [], [], []
    sim = SimpleNamespace(status='running', hazards_running=True, hazard_time=0.,
                          metrics={}, needs_frame=lambda: False)
    sim.tick = lambda dt: ticks.append((now[0], dt))
    sim.recorder = SimpleNamespace(capture=lambda current: captures.append(current))
    sim.supervisor = SimpleNamespace(step=lambda: decisions.append(now[0]))
    monkeypatch.setattr(api, 'sim', sim)
    monkeypatch.setattr(api, 'paired', None)
    monkeypatch.setattr(api, 'run_speed', speed)
    monkeypatch.setattr(api, 'inference_hold', lambda current: False)
    monkeypatch.setattr(api, 'frame_timeout_check', lambda: None)
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    async def thread(fn): fn()
    async def sleep(delay):
        now[0] += delay
        if len(ticks) == 4: raise StopClock
    monkeypatch.setattr(api.asyncio, 'to_thread', thread)
    monkeypatch.setattr(api.asyncio, 'sleep', sleep)
    with pytest.raises(StopClock): asyncio.run(api.simulation_clock())
    assert [dt for _, dt in ticks] == [api.TICK] * 4
    assert [t for t, _ in ticks] == pytest.approx([i * api.TICK / speed for i in range(4)])
    assert len(captures) == len(decisions) == 4


def test_waiting_frames_and_replaced_simulation_never_record_stale_ticks(monkeypatch):
    now, waiting = [0.], [True]
    old_ticks, old_captures, new_ticks = [], [], []
    replacement = SimpleNamespace(status='running', hazards_running=True, hazard_time=0.,
                                  metrics={}, needs_frame=lambda: False,
                                  tick=lambda dt: new_ticks.append(dt))
    old = SimpleNamespace(status='running', hazards_running=True, hazard_time=0.,
                          metrics={}, needs_frame=lambda: waiting[0],
                          tick=lambda dt: old_ticks.append(dt),
                          recorder=SimpleNamespace(capture=lambda s: old_captures.append(s)))
    old.supervisor = SimpleNamespace(step=lambda: setattr(api, 'sim', replacement))
    monkeypatch.setattr(api, 'sim', old)
    monkeypatch.setattr(api, 'paired', None)
    monkeypatch.setattr(api, 'inference_hold', lambda s: False)
    monkeypatch.setattr(api, 'frame_timeout_check', lambda: None)
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    async def thread(fn): fn()
    async def sleep(delay):
        now[0] += delay
        if waiting[0]:
            assert not old_ticks and not old_captures
            waiting[0] = False
        if new_ticks: raise StopClock
    monkeypatch.setattr(api.asyncio, 'to_thread', thread)
    monkeypatch.setattr(api.asyncio, 'sleep', sleep)
    with pytest.raises(StopClock): asyncio.run(api.simulation_clock())
    assert not old_ticks and not old_captures
    assert new_ticks == [api.TICK]


def test_pause_and_inference_hold_do_not_advance_or_record(monkeypatch):
    now, phase, ticks, captures = [0.], [0], [], []
    sim = SimpleNamespace(status='paused', hazards_running=False, hazard_time=0.,
                          metrics={'inference_pause_s': 0.}, needs_frame=lambda: False,
                          tick=lambda dt: ticks.append(dt),
                          recorder=SimpleNamespace(capture=lambda s: captures.append(s)))
    monkeypatch.setattr(api, 'sim', sim)
    monkeypatch.setattr(api, 'paired', None)
    monkeypatch.setattr(api, 'inference_hold', lambda s: phase[0] == 1)
    monkeypatch.setattr(api, 'frame_timeout_check', lambda: None)
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    async def sleep(delay):
        now[0] += delay
        if phase[0] < 2:
            assert not ticks and not captures
            sim.status = 'running'
            phase[0] += 1
        else: raise StopClock
    monkeypatch.setattr(api.asyncio, 'sleep', sleep)
    with pytest.raises(StopClock): asyncio.run(api.simulation_clock())
    assert ticks == [api.TICK] and len(captures) == 1
    assert sim.metrics['inference_pause_s'] == api.TICK
