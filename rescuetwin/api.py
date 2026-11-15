"""Local-only API. /state is operator truth; /observations is the agent boundary."""
import asyncio
import importlib.util
import math
import os
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import models, presets
from .environment import parse_environment
from .core import CAMERA_RANGE_M, HAZARD_PREVIEW_LIMIT_S, Simulation, compare
from .scenes import asset_file, available, default_scene

FRAME_TIMEOUT_S = float(os.environ.get('RESCUETWIN_FRAME_TIMEOUT', '10'))
AGENT_PERIOD_S = float(os.environ.get('RESCUETWIN_AGENT_PERIOD', '5'))
AGENT_TIMEOUT_S = 10.0   # ~3 models x 3 s budget; decisions older than 10 s of mission time are rejected anyway
# Hosted agent status for the operator UI (no secrets). Excluded from paired comparisons by design.
# Selecting Gemini enables hosted decisions for that strategy only.
agent_status = dict(state='starting', enabled=os.environ.get('RESCUETWIN_AGENT', '0') == '1', model=None,
                    accepted=0, rejected=0, failed=0, latency_s=None, last=None, last_error=None,
                    # Decision-quality mode: freeze the mission clock (robot and fire) while the model is thinking.
                    pause_clock=True, thinking=False)
PAUSE_LABEL = 'Decision-quality mode: mission clock and fire paused during model inference; not real-time performance.'
VISION_AVAILABLE = importlib.util.find_spec('ultralytics') is not None
TICK = .2
RUN_SPEED = float(os.environ.get('RESCUETWIN_RUN_SPEED', '1'))
if not math.isfinite(RUN_SPEED) or RUN_SPEED <= 0:
    raise ValueError('RESCUETWIN_RUN_SPEED must be a positive, finite number')
# Wall-clock pace only: every tick still advances the same TICK of simulated time, so results are unchanged.
# The operator can change it at runtime (POST /api/speed); the env value is the startup default.
SPEED_CHOICES = (1, 2, 4, 8)
run_speed = RUN_SPEED

scenes = available()


def default_perception(scene):
    return 'image-yolo' if scene.source != 'synthetic' and VISION_AVAILABLE else 'analytic-development'


uploaded_scenarios = {}


def new_simulation(scene, scenario=None, mode='adaptive', perception=None, strategy='greedy'):
    if scenario is None:
        scenario = uploaded_scenarios.get(scene.id)
    if scenario is None:
        scenario = presets.build(scene, 'standard')['scenario']
    return Simulation(scene, scenario, mode, perception or default_perception(scene), strategy)


sim = new_simulation(default_scene(scenes))
detector = None
detector_error = None
frame_requested_at = None
paired = None   # live paired comparison (image perception), see /api/compare


class Paired:
    """Baseline then adaptive as live runs from one frozen scenario, so both use the same rendered perception."""

    def __init__(self, scene, scenario, perception):
        self.scene, self.scenario, self.perception = scene, deepcopy(scenario), perception
        self.order, self.reports, self.status, self.started = ['baseline', 'adaptive'], {}, 'running', time.time()

    def next_run(self):
        global sim
        mode = self.order[len(self.reports)]
        sim = Simulation(self.scene, self.scenario, mode, self.perception)
        sim.status = 'running'
        from .recording import Recorder
        sim.recorder=Recorder(sim,dict(kind='paired'))
        sim.log(f'Paired comparison: {mode} run ({len(self.reports) + 1}/2) from frozen scenario.')

    def record(self, finished):
        self.reports[finished.mode] = dict(status=finished.status, time=round(finished.time, 2), degraded=finished.degraded,
                                           **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in finished.metrics.items()})
        if len(self.reports) == 2:
            self.status = 'complete'
        else:
            self.next_run()

    def public(self):
        return dict(status=self.status, scene_id=self.scene.id, seed=self.scenario['seed'], perception=self.perception,
                    experiment=presets.experiment_context(self.scene,self.scenario),
                    reports=self.reports, running=None if self.status != 'running' else self.order[len(self.reports)],
                    note=('Live paired runs: same frozen scenario, hazard model, sensors and safety rules; YOLO on rendered '
                          'frames. Illustrative exposure units; hosted agent excluded; differences are not attributable to an LLM.'))


def load_detector():
    global detector, detector_error
    if detector is not None or not VISION_AVAILABLE:
        return detector
    try:
        from PIL import Image
        from .perception import PersonDetector
        model = PersonDetector()
        model.detect(Image.new('RGB', (512, 384), 'gray'))   # warm-up: first GPU inference is slow
        detector = model
        detector_error = None
    except Exception as error:   # weights unavailable, etc.
        detector_error = f'{type(error).__name__}: {error}'[:300]
    return detector


def frame_timeout_check():
    """Never block the mission on the renderer: degrade visibly instead."""
    global frame_requested_at
    if sim.frame_request is None:
        frame_requested_at = None
        return
    if frame_requested_at is None or frame_requested_at[0] != sim.frame_request['seq']:
        frame_requested_at = (sim.frame_request['seq'], time.monotonic())
    elif time.monotonic() - frame_requested_at[1] > FRAME_TIMEOUT_S:
        if not sim.degraded:
            sim.log('No rendered frame received in time; image perception degraded (no new detections).')
        sim.degraded = True
        sim.frame_request = None
        frame_requested_at = None


async def simulation_clock():
    global sim
    next_due = 0.   # wall-clock time the next paced simulation step may run
    paced_sim = None
    while True:
        tick_started = time.monotonic()
        fast = paired is not None and paired.status == 'running'
        current = sim
        if current is not paced_sim:
            paced_sim = current
            next_due = 0.
        if current.status == 'running' and inference_hold(current):
            current.metrics['inference_pause_s'] += TICK      # labelled pause, reported with the run
            await asyncio.sleep(TICK)
            continue
        if current.status == 'running':
            frame_timeout_check()
            # Pace complete steps, including supervision and recording; frame waits overlap the deadline.
            if not current.needs_frame() and (fast or tick_started >= next_due):
                supervisor = getattr(current, 'supervisor', None)
                if supervisor:
                    await asyncio.to_thread(supervisor.step)
                    if current is not sim:
                        continue
                if current.status == 'running':
                    current.tick(TICK)
                    if getattr(current, 'recorder', None):
                        current.recorder.capture(current)
                next_due = tick_started + TICK / run_speed
        elif current.hazards_running:
            # Upstream PR #1: hazards can be previewed while the robot mission is idle or paused.
            current.hazards(TICK)
        if current.status != 'running' and current.hazard_time >= HAZARD_PREVIEW_LIMIT_S - 1e-8:
            current.hazards_running = False
        if fast and current is sim and current.status not in ('running', 'paused'):
            paired.record(current)
        if fast:
            await asyncio.sleep(TICK / 8 if current.needs_frame() else 0)
        else:
            now = time.monotonic()
            if current.status == 'running':
                # Waiting on a frame: re-check often so the clock resumes as soon as it lands.
                wait = TICK / 8 if current.needs_frame() else min(TICK, max(0., next_due - now))
            else:
                interval = TICK / run_speed if current.hazards_running else TICK
                wait = max(0., interval - (now - tick_started))
            await asyncio.sleep(wait)


@asynccontextmanager
async def lifespan(app):
    async def reason():
        """Hosted supervisor: proposes goals; the controller validates and executes. Any failure or rejection falls
        back to the deterministic policy for that decision; optional inference holds are labelled."""
        from .agent import BACKOFF_FAILED_S,BACKOFF_REJECTED_S,apply_decision,decision_options,scrub,wants_decision
        if agent_status['enabled']:
            try: prepare_agent()
            except HTTPException: pass
        else:
            agent_status.update(state='disabled')
        while True:
            await asyncio.sleep(TICK/2 if agent_status['pause_clock'] else AGENT_PERIOD_S)
            current=sim
            if not agent_status['enabled'] or current.strategy != 'gemini' or busy() or not wants_decision(current):continue
            try: agent=prepare_agent()
            except HTTPException: continue
            if agent_status['pause_clock'] and not getattr(current,'pause_logged',False):
                current.pause_logged=True;current.log(PAUSE_LABEL)
            observation=deepcopy(current.observations());options=decision_options(current)
            started=time.time();agent_status['thinking']=True
            try:
                decision=await asyncio.wait_for(asyncio.to_thread(agent.decide,observation,current.scene,options),timeout=AGENT_TIMEOUT_S)
                agent_status['latency_s']=round(time.time()-started,2)
                if current is not sim or not agent_status['enabled'] or current.strategy != 'gemini' or busy():continue
                apply_decision(current,decision,observation['time'])
                agent_status['model']=agent.model;agent_status['accepted']+=1;agent_status['last']=dict(kind=decision.get('kind'),id=decision.get('id'),reason=str(decision.get('reason',''))[:300],time=round(current.time,1))
            except Exception as e:
                agent_status['latency_s']=round(time.time()-started,2)
                kind='rejected' if isinstance(e,ValueError) else 'failed'
                agent_status[kind]+=1;agent_status['last_error']=scrub(f'{type(e).__name__}: {e}' if str(e) else type(e).__name__)
                if current is sim:
                    current.agent_backoff_until=current.time+(BACKOFF_REJECTED_S if kind=='rejected' else BACKOFF_FAILED_S)
                    current.log(f"Hosted decision {kind} ({agent_status['last_error']}); deterministic policy continues.")
            finally:
                agent_status['thinking']=False

    if VISION_AVAILABLE and os.environ.get('RESCUETWIN_PRELOAD_DETECTOR', '1') == '1':
        asyncio.get_running_loop().run_in_executor(None, load_detector)
    tasks = [asyncio.create_task(reason()), asyncio.create_task(simulation_clock())]
    yield
    if getattr(sim,'recorder',None):sim.recorder.close('server shutdown')
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:await task
        except asyncio.CancelledError:pass

app=FastAPI(title='RescueTwin local development API',lifespan=lifespan)

class Scenario(BaseModel):
    survivors:list[list[int]]=Field(max_length=20)
    ignitions:list[list[int]]=Field(max_length=20)
    seed:int=7
    scene_id:str|None=None
    building_use:str|None=None
    immobile:list[list[int]]=Field(default_factory=list,max_length=20)
    comms_outages:list[list[float]]=Field(default_factory=list,max_length=10)
    room_risk:dict[str,str]=Field(default_factory=dict,max_length=200)
    incident_room:str|None=None
    human_variant:str|None=None

class Control(BaseModel):
    action:str
    mode:str|None=None
    perception:str|None=None
    strategy:str|None=None

class SceneChoice(BaseModel):
    id:str

class CompareRequest(BaseModel):
    perception:str|None=None

class Frame(BaseModel):
    seq:int
    renderer_id:str|None=None
    width:int=Field(gt=0,le=1280)
    height:int=Field(gt=0,le=960)
    rgb:str=Field(max_length=8_000_000)
    depth:str=Field(max_length=8_000_000)
    intrinsics:list[float]=Field(min_length=4,max_length=4)
    pose:dict[str,float]

def busy():
    return paired is not None and paired.status=='running'

@app.get('/api/state')
def state():
    s=sim.state();s['paired']=paired.public() if paired else None
    supervisor=getattr(sim,'supervisor',None)
    s['agent_supervisor']=dict(architecture=supervisor.architecture,calls=supervisor.calls,active=supervisor.tools.active) if supervisor else None
    s['vision_installed'] = VISION_AVAILABLE
    s['run_speed'] = run_speed
    return s

@app.get('/api/observations')
def observations():return sim.observations()

@app.get('/api/scenes')
def scene_list():return [s.summary() for s in scenes.values()]

@app.get('/api/models')
def model_list():
    return [models.public(entry) for entry in models.catalog(scenes).values()]

@app.get('/api/models/{model_id}/files/{name}')
def model_file(model_id: str, name: str):
    entry = models.catalog(scenes).get(model_id)
    if entry is None:
        raise HTTPException(404, 'No such prepared model')
    try:
        path = models.asset(entry, name)
    except ValueError:
        raise HTTPException(404, 'No such model file')
    return FileResponse(path)

@app.get('/api/scene')
def scene():return sim.base_scene.public()

@app.post('/api/environment')
def environment(body: dict):
    global sim
    if sim.status == 'running' or sim.hazards_running or busy():
        raise HTTPException(409, 'Pause or reset before loading a floor plan')
    try:
        scene, scenario = parse_environment(body)
        if scene.id in scenes:
            from dataclasses import replace
            from uuid import uuid4
            scene = replace(scene, id=f'uploaded-{uuid4().hex}')
        next_sim = new_simulation(scene, scenario, sim.mode, 'analytic-development', sim.strategy)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise HTTPException(422, f'Invalid floor plan: {error}') from error
    scenes[scene.id] = scene
    uploaded_scenarios[scene.id] = scenario
    sim = next_sim
    return sim.state()


@app.post('/api/scene')
def choose_scene(body:SceneChoice):
    global sim
    if body.id not in scenes:raise HTTPException(404,'Unknown scene')
    if sim.status=='running' or sim.hazards_running or busy():raise HTTPException(409,'Pause or reset before switching scenes')
    sim=new_simulation(scenes[body.id], strategy=sim.strategy);return sim.state()

@app.get('/api/scenes/{scene_id}/files/{name}')
def scene_file(scene_id:str,name:str):
    scene=scenes.get(scene_id)
    if scene is None:raise HTTPException(404,'No such scene file')
    try:path,mime=asset_file(scene,name)
    except ValueError:raise HTTPException(404,'No such scene file')
    return FileResponse(path,media_type=mime)

@app.get('/api/presets')
def preset_list(seed:int=7,building_use:str|None=None):
    try:return presets.available(sim.scene,seed,building_use)
    except ValueError as e:raise HTTPException(422,str(e)) from e

@app.get('/api/building-uses')
def building_uses():
    from . import occupancy
    use,source=occupancy.resolve(sim.scene)
    return dict(inferred=use,source=source,profiles=[dict(id=k,label=v['label'],rationale=v['rationale']) for k,v in occupancy.PROFILES.items()])

@app.get('/api/risk-map')
def risk_map():
    from . import risk
    return risk.public(sim.scene,sim.risk)

@app.get('/api/agent')
def agent_info():
    return dict(agent_status,note='Gemini proposes room/survivor/exit goals under docs/ROBOT_CONSTITUTION.md; the controller validates and executes. '
                'Failures fall back to the deterministic policy. Paired comparisons exclude the hosted agent.')

class AgentToggle(BaseModel):
    enabled:bool|None=None
    pause_clock:bool|None=None

def inference_hold(current):
    """Freeze the clock only in decision-quality mode, for the live run, while the robot waits on the model."""
    from .agent import wants_decision
    return (agent_status['pause_clock'] and agent_status['enabled'] and agent_status['state']=='ready' and not busy()
            and current is sim and current.strategy == 'gemini' and (agent_status['thinking'] or wants_decision(current)))

def prepare_agent():
    """Initialize the interactive planner locally; no hosted request is made until a mission runs."""
    from .agent import MissionAgent, load_agent_env, scrub
    agent = getattr(app.state, 'mission_agent', None)
    if agent is not None:
        agent_status.update(state='ready', model=agent.model)
        return agent
    load_agent_env()
    if not os.environ.get('GEMINI_API_KEY'):
        agent_status.update(state='no-key')
        raise HTTPException(503, 'Set GEMINI_API_KEY on the server to use Gemini.')
    try:
        agent = MissionAgent()
    except Exception as error:
        agent_status.update(state='unavailable', last_error=scrub(f'{type(error).__name__}: {error}'))
        raise HTTPException(503, 'Gemini unavailable; install the agent extra and check server configuration.') from error
    app.state.mission_agent = agent
    agent_status.update(state='ready', model=agent.model, last_error=None)
    return agent


@app.post('/api/agent')
def agent_toggle(body:AgentToggle):
    if body.enabled:
        prepare_agent()
    if body.enabled is not None:
        agent_status['enabled']=body.enabled
        sim.log(f"Hosted agent {'enabled' if body.enabled else 'disabled'} by operator.")
    if body.pause_clock is not None:
        agent_status['pause_clock']=body.pause_clock
        sim.log(PAUSE_LABEL if body.pause_clock else 'Real-time mode: the mission clock runs during model inference.')
    return agent_info()

class Speed(BaseModel):
    speed: float

@app.post('/api/speed')
def set_speed(body: Speed):
    """Playback pace (1x, 2x, 4x, 8x). Changes wall-clock pace only, never the simulated timestep."""
    global run_speed
    if body.speed not in SPEED_CHOICES:
        raise HTTPException(422, f'speed must be one of {SPEED_CHOICES}')
    run_speed = float(body.speed)
    return dict(run_speed=run_speed, choices=SPEED_CHOICES,
                note='Wall-clock pace only; camera frames, inference and compute can limit the achieved pace.')

@app.get('/api/perception')
def perception_status():
    return dict(vision_installed=VISION_AVAILABLE,detector_loaded=detector is not None,error=detector_error,
                weights=getattr(detector,'weights',None),device=getattr(detector,'device',None),
                modes=['image-yolo','analytic-development'] if VISION_AVAILABLE else ['analytic-development'],
                note='analytic-development is a labelled fixture detector driven by simulator truth; it is not image-based.')

@app.post('/api/scenario')
def scenario(body:Scenario):
    global sim
    if sim.status!='idle' or sim.hazards_running or busy():raise HTTPException(409,'Reset before editing a scenario')
    data=body.model_dump();data['scene_id']=data['scene_id'] or sim.scene.id
    if data['human_variant'] is None:data.pop('human_variant')
    for key in ('building_use','immobile','comms_outages','room_risk','incident_room'):
        if not data.get(key):data.pop(key,None)   # unset fields stay out, so existing scenario hashes are unchanged
    try:sim=Simulation(sim.scene,data,sim.mode,sim.perception_mode,sim.strategy)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    return sim.state()

@app.post('/api/control')
def control(body:Control):
    global sim
    if busy() and body.action!='reset':raise HTTPException(409,'Paired comparison in progress')
    if body.mode is not None and body.mode not in ('adaptive','baseline'):raise HTTPException(422,'Invalid mode')
    if body.perception is not None and body.perception not in ('image-yolo','analytic-development'):raise HTTPException(422,'Invalid perception mode')
    if body.strategy is not None and body.strategy not in ('greedy','mission-astar','mission-astar-cached','gemini'):
        raise HTTPException(422,'Invalid mission strategy')
    if body.perception=='image-yolo' and not VISION_AVAILABLE:raise HTTPException(422,'Vision extra not installed')
    if body.action == 'start' and (body.strategy or sim.strategy) == 'gemini':
        prepare_agent()
    if body.action=='reset':
        if getattr(sim,'recorder',None):sim.recorder.close()
        cancel_paired()
        sim=Simulation(sim.base_scene,sim.scenario,body.mode or sim.mode,body.perception or sim.perception_mode,body.strategy or sim.strategy)
    elif body.action=='start':
        if sim.status not in ('idle','paused'):raise HTTPException(409,'Reset a finished run first')
        if sim.status!='idle' and ((body.mode and body.mode!=sim.mode) or (body.perception and body.perception!=sim.perception_mode) or (body.strategy and body.strategy!=sim.strategy)):
            raise HTTPException(409,'Reset before changing policy, mission strategy or perception')
        if sim.status=='idle' and (body.perception and body.perception!=sim.perception_mode or body.strategy and body.strategy!=sim.strategy):
            sim=Simulation(sim.scene,sim.scenario,body.mode or sim.mode,body.perception or sim.perception_mode,body.strategy or sim.strategy)
        sim.mode=body.mode or sim.mode;sim.strategy=body.strategy or sim.strategy;sim.status='running';sim.hazards_running=True
        agent_status['enabled'] = sim.strategy == 'gemini'
        if recording_enabled and not getattr(sim,'recorder',None):
            from .recording import Recorder
            sim.recorder=Recorder(sim)
    elif body.action=='withdraw':
        if sim.status not in ('running','paused'):raise HTTPException(409,'No active mission to withdraw')
        sim.withdraw('operator stop');sim.status='running';sim.hazards_running=True
    elif body.action=='pause':
        if sim.status!='running':raise HTTPException(409,'No active run')
        sim.status='paused';sim.hazards_running=False
    elif body.action=='toggle_hazards':
        if sim.status=='running':raise HTTPException(409,'Pause the robot mission before previewing hazards separately')
        if sim.hazard_time>=HAZARD_PREVIEW_LIMIT_S-1e-8:raise HTTPException(409,'Reset before previewing hazards again')
        sim.hazards_running=not sim.hazards_running
        if sim.status=='idle':sim.status='paused'
    else:raise HTTPException(422,'Unknown control action')
    return sim.state()

def cancel_paired():
    global paired
    if paired is not None and paired.status=='running':paired.status='cancelled'

@app.post('/api/reports/{report_id}/ack')
def acknowledge_report(report_id:str):
    """The operator acknowledges a delivered report on behalf of the outside response team."""
    try:sim.acknowledge(report_id)
    except ValueError as e:raise HTTPException(409,str(e)) from e
    return sim.state()

@app.post('/api/compare')
async def comparison(body:CompareRequest|None=None):
    global paired,sim
    perception=(body.perception if body else None) or sim.perception_mode
    scene,scenario=sim.base_scene,deepcopy(sim.scenario)
    if perception=='analytic-development':
        report=await asyncio.to_thread(compare,scene,scenario,perception)
        report['experiment']=presets.experiment_context(scene,scenario)
        return report
    if sim.status=='running' or busy():raise HTTPException(409,'Pause or reset before a paired comparison')
    paired=Paired(scene,scenario,perception);paired.next_run()
    return paired.public()

@app.get('/api/compare')
def comparison_status():
    if paired is None:raise HTTPException(404,'No paired comparison started')
    return paired.public()

@app.post('/api/frames')
async def frame(body:Frame):
    current=sim
    if renderer_lease['until']>time.monotonic() and body.renderer_id!=renderer_lease['id']:
        raise HTTPException(409,'Another renderer owns the camera')
    request=current.frame_request
    if current.perception_mode!='image-yolo' or request is None or body.seq!=request['seq']:
        raise HTTPException(409,'Stale or unrequested frame')
    expected=request['pose']
    if any(abs(body.pose.get(k,math.inf)-expected[k])>1e-3 for k in ('x','y','z')) or abs(math.remainder(body.pose.get('yaw',math.inf)-expected['yaw'],2*math.pi))>1e-3:
        raise HTTPException(409,'Frame pose does not match the requested pose')
    from .perception import decode_depth,decode_rgb,process_frame
    try:
        image=decode_rgb(body.rgb);depth=decode_depth(body.depth,body.width,body.height)
    except (ValueError,OSError) as error:raise HTTPException(422,f'Bad frame: {error}') from error
    if image.size!=(body.width,body.height):raise HTTPException(422,'RGB size does not match declared dimensions')
    if getattr(current,'recorder',None):current.recorder.frame(body.model_dump(),dict(status='submitted'),current.time)
    model=await asyncio.to_thread(load_detector)
    if model is None:
        raise HTTPException(503,f'Detector unavailable: {detector_error or "vision extra not installed"}')
    accepted,rejected=await asyncio.to_thread(process_frame,model,image,depth,body.intrinsics,expected,CAMERA_RANGE_M)
    if current is not sim or current.frame_request is not request:
        raise HTTPException(409,'Simulation moved on while the frame was processed')
    matched = set()
    for item in accepted:
        d=current.add_evidence(item['world'][:2],item['confidence'],'yolo',matched=matched)
        item['detection']=d['id'] if d else None
        if d:
            matched.add(d['id'])
    current.last_frame=dict(seq=body.seq,time=request['time'],accepted=accepted,rejected=rejected)
    if getattr(current,'recorder',None):current.recorder.event('frame-result',current.last_frame,current.time)
    current.frame_request=None
    return current.last_frame

@app.websocket('/api/stream')
async def stream(ws:WebSocket):
    await ws.accept()
    from starlette.websockets import WebSocketDisconnect
    try:
        while True:
            await ws.send_json(state());await asyncio.sleep(.2)
    except WebSocketDisconnect:pass

# Recording access is read-only; these endpoints never run a policy or inference.
recording_enabled=True
class RecordingToggle(BaseModel):
    enabled:bool
@app.post('/api/recording')
def recording_toggle(body:RecordingToggle):
    global recording_enabled
    if sim.status=='running':raise HTTPException(409,'Choose recording before starting')
    recording_enabled=body.enabled
    return dict(enabled=recording_enabled)
@app.get('/api/runs')
def runs():
    from .recording import listing
    return listing()
@app.get('/api/runs/{identity}')
def run_manifest(identity:str):
    from .recording import manifest
    try:return manifest(identity)
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Recording not found')
@app.get('/api/runs/{identity}/timeline')
def run_timeline(identity:str,start:int=0,limit:int=25):
    from .recording import timeline
    try:return timeline(identity,max(0,start),min(100,max(1,limit)))
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Recording not found')
@app.get('/api/runs/{identity}/artifacts/{name}')
def run_artifact(identity:str,name:str):
    from .recording import artifact
    try:return FileResponse(artifact(identity,name))
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Artifact unavailable')
@app.get('/api/runs/{identity}/download')
def run_download(identity:str):
    from .recording import archive
    try:return FileResponse(archive(identity),filename=identity+'.zip')
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Recording not found')

class ExperimentRun(BaseModel):
    scene_id:str
    scenario:dict
    policy:str='mission-astar'
    perception:str='image-yolo'
@app.post('/api/experiments/run')
def experiment_run(body:ExperimentRun):
    global sim
    if sim.status=='running' or busy():raise HTTPException(409,'A mission is active')
    if body.scene_id not in scenes:raise HTTPException(422,'Unknown scene')
    provider=None
    if body.policy.startswith('agent-'):
        from .reasoning import GeminiProvider, ARCHITECTURES
        architecture=body.policy.removeprefix('agent-')
        if architecture not in ARCHITECTURES:raise HTTPException(422,'Unknown architecture')
        campaign=os.environ.get('RESCUETWIN_APPROVED_CAMPAIGN')
        if not campaign:raise HTTPException(403,'Explicit approved campaign required')
        if not getattr(app.state,'campaign_provider',None):app.state.campaign_provider=GeminiProvider(campaign)
        provider=app.state.campaign_provider
        from .recording import digest
        allowed=provider.config.get('scenarios',[])
        if architecture not in provider.config.get('architectures',[]) or not any(r['scene_id']==body.scene_id and digest(r['scenario'])==digest(body.scenario) for r in allowed):
            raise HTTPException(403,'Scene/scenario/architecture outside the approved campaign')
    elif body.policy not in ('distance','greedy','mission-astar'):raise HTTPException(422,'Unknown policy')
    if body.perception not in ('image-yolo','analytic-development'):raise HTTPException(422,'Unknown perception')
    if body.scenario.get('incident_room'):raise HTTPException(422,'Structural incidents excluded from experiment contract')
    from .recording import Recorder
    try:sim=Simulation(scenes[body.scene_id],body.scenario,'baseline' if body.policy=='distance' else 'adaptive',body.perception,'greedy' if body.policy!='mission-astar' else 'mission-astar')
    except ValueError as error:raise HTTPException(422,str(error))
    sim.status='running'
    sim.recorder=Recorder(sim,dict(policy=body.policy,latency_excluded=True))
    if provider:
        from .reasoning import Supervisor
        sim.supervisor=Supervisor(sim,provider,architecture,request_limit=provider.config.get('requests_per_mission',24))
    return dict(run_id=sim.recorder.id)

@app.get('/api/runs/{identity}/snapshot')
def recorded_snapshot(identity:str,seq:int=0):
    from .recording import snapshot
    try:return snapshot(identity,max(0,seq))
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Recording not found')

renderer_lease=dict(id=None,until=0.)
class RendererLease(BaseModel):
    id:str=Field(min_length=1,max_length=64)
    release:bool=False
@app.post('/api/renderer')
def renderer_claim(body:RendererLease):
    now=time.monotonic()
    if body.release:
        if renderer_lease['id']==body.id:renderer_lease.update(id=None,until=0.)
        return dict(owner=False)
    if renderer_lease['id']==body.id or renderer_lease['until']<=now:
        renderer_lease.update(id=body.id,until=now+6.)
        return dict(owner=True)
    return dict(owner=False)
