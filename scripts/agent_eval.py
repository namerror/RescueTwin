"""Headless evaluation of the Gemini supervisor against the built-in planner.

Three arms per scenario, same frozen scenario, analytic-development perception (labelled fixture detector):
  built-in   deterministic planner only
  realtime   Gemini asked every AGENT_PERIOD_S when the robot has no committed goal; the mission keeps running
             for the measured inference latency (built-in planner steers meanwhile), as in the live app
  paused     decision-quality mode: Gemini asked as soon as a goal is reached; mission clock and fire frozen
             during inference (NOT real-time performance)

Model output is not deterministic and calls depend on service availability; treat results as illustrative.
Usage: python scripts/agent_eval.py --scene LOCAL_SCENE_ID --seeds 1 2 3 --uses residential assembly --output data/validation/agent-eval.json
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuetwin import presets  # noqa: E402
from rescuetwin.agent import (BACKOFF_FAILED_S, BACKOFF_REJECTED_S, MissionAgent, apply_decision,  # noqa: E402
                              decision_options, load_agent_env, scrub, wants_decision)
from rescuetwin.core import Simulation  # noqa: E402
from rescuetwin.scenes import available  # noqa: E402

TICK = .2
AGENT_PERIOD_S = 5.0


def run(scene, scenario, arm, agent):
    sim = Simulation(scene, scenario, 'adaptive'); sim.status = 'running'
    calls = dict(accepted=0, rejected=0, failed=0); latencies = []; paused = 0.
    next_poll = 0.
    while sim.status == 'running':
        ask = arm == 'paused'
        if arm == 'realtime' and sim.time >= next_poll:   # live app: the agent loop wakes every AGENT_PERIOD_S
            ask = True; next_poll += AGENT_PERIOD_S
        if agent is not None and ask and wants_decision(sim):
            observation, options = sim.observations(), decision_options(sim)
            started = time.time()
            try:
                decision = agent.decide(observation, sim.scene, options)
                latency = time.time() - started
            except Exception as error:  # overloaded/quota/timeout on every model
                latency = time.time() - started; decision = None
                calls['failed'] += 1; sim.agent_backoff_until = sim.time + BACKOFF_FAILED_S
                sim.log(f'Hosted decision failed ({scrub(error)[:80]}); deterministic policy continues.')
            latencies.append(latency)
            if arm == 'paused':
                paused += latency
            else:   # real time: the mission keeps going while the model thinks
                for _ in range(max(1, round(latency / TICK))):
                    if sim.status != 'running': break
                    sim.tick(TICK)
            if decision is not None and sim.status == 'running':
                try:
                    apply_decision(sim, decision, observation['time']); calls['accepted'] += 1
                except ValueError:
                    calls['rejected'] += 1; sim.agent_backoff_until = sim.time + BACKOFF_REJECTED_S
            if arm == 'paused': continue
        sim.tick(TICK)
    m = sim.metrics
    return dict(arm=arm, status=sim.status, time=round(sim.time, 1), evacuated=m['evacuated'], people=len(scenario['survivors']),
                exposure=round(m['exposure'], 2), distance=m['distance'], handoffs=m['handoffs'], calls=calls,
                inference_pause_s=round(paused, 1),
                latency_median_s=round(sorted(latencies)[len(latencies) // 2], 2) if latencies else None,
                models=agent.model if agent else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3])
    parser.add_argument('--uses', nargs='+', default=['residential', 'assembly'])
    parser.add_argument('--arms', nargs='+', default=['built-in', 'realtime', 'paused'])
    parser.add_argument('--output', type=Path, default=Path('data/validation/agent-eval.json'))
    args = parser.parse_args()
    load_agent_env()
    scene = available()[args.scene]
    agent = MissionAgent() if {'realtime', 'paused'} & set(args.arms) else None
    results = []
    for use in args.uses:
        for seed in args.seeds:
            scenario = presets.build(scene, 'standard', seed, None if use == 'residential' else use)['scenario']
            for arm in args.arms:
                row = dict(use=use, seed=seed, **run(scene, scenario, arm, agent if arm != 'built-in' else None))
                results.append(row); print(json.dumps(row), flush=True)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(dict(scene=args.scene, perception='analytic-development', results=results,
                    note='paused arm freezes the mission clock during inference: decision quality, not real-time performance'), indent=1))


if __name__ == '__main__':
    main()
