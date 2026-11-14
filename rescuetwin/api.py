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
from . import presets
from .environment import parse_environment
from .core import CAMERA_RANGE_M, HAZARD_PREVIEW_LIMIT_S, Simulation, compare
from .scenes import asset_file, available, default_scene

FRAME_TIMEOUT_S = float(os.environ.get('RESCUETWIN_FRAME_TIMEOUT', '10'))
AGENT_PERIOD_S = float(os.environ.get('RESCUETWIN_AGENT_PERIOD', '5'))
AGENT_TIMEOUT_S = 10.0   # ~3 models x 3 s budget; decisions older than 10 s of mission time are rejected anyway
# Hosted agent status for the operator UI (no secrets). Excluded from paired comparisons by design.
agent_status = dict(state='starting', enabled=os.environ.get('RESCUETWIN_AGENT', '1') != '0', model=None,
                    accepted=0, rejected=0, failed=0, latency_s=None, last=None, last_error=None,
                    # Decision-quality mode: freeze the mission clock (robot and fire) while the model is thinking.
                    pause_clock=False, thinking=False)
PAUSE_LABEL = 'Decision-quality mode: mission clock and fire paused during model inference; not real-time performance.'
VISION_AVAILABLE = importlib.util.find_spec('ultralytics') is not None
TICK = .2
RUN_SPEED = float(os.environ.get('RESCUETWIN_RUN_SPEED', '1'))
if not math.isfinite(RUN_SPEED) or RUN_SPEED <= 0:
    raise ValueError('RESCUETWIN_RUN_SPEED must be a positive, finite number')

scenes = available()


def default_perception(scene):
    return 'image-yolo' if scene.source != 'synthetic' and VISION_AVAILABLE else 'analytic-development'


uploaded_scenarios = {}


def new_simulation(scene, scenario=None, mode='adaptive', perception=None):
    if scenario is None:
        scenario = uploaded_scenarios.get(scene.id)
    if scenario is None:
        scenario = presets.build(scene, 'standard')['scenario']
    return Simulation(scene, scenario, mode, perception or default_perception(scene))


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


@asynccontextmanager
async def lifespan(app):
    async def clock():
        global sim
        while True:
            tick_started = time.monotonic()
            fast = paired is not None and paired.status == 'running'
            current = sim
            if current.status == 'running' and inference_hold(current):
                current.metrics['inference_pause_s'] += TICK      # labelled pause, reported with the run
                await asyncio.sleep(TICK)
                continue
            if current.status == 'running':
                frame_timeout_check()
                current.tick(TICK)
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
                active = current.status == 'running' or current.hazards_running
                interval = TICK / RUN_SPEED if active and not current.needs_frame() else TICK
                await asyncio.sleep(max(0., interval - (time.monotonic() - tick_started)))

    async def reason():
        """Hosted supervisor: proposes goals; the controller validates and executes. Any failure or rejection falls
        back to the deterministic policy for that decision (the mission never pauses on the model's account)."""
        from .agent import BACKOFF_FAILED_S,BACKOFF_REJECTED_S,MissionAgent,apply_decision,decision_options,load_agent_env,scrub,wants_decision
        load_agent_env()
        if not os.environ.get('GEMINI_API_KEY'):
            agent_status.update(state='no-key');return
        try:agent=MissionAgent()
        except Exception as e:
            agent_status.update(state='unavailable',last_error=scrub(type(e).__name__+': '+str(e)))
            sim.log('Hosted agent unavailable; deterministic development policy active.');return
        agent_status.update(state='ready',model=agent.model)
        while True:
            await asyncio.sleep(TICK/2 if agent_status['pause_clock'] else AGENT_PERIOD_S)
            current=sim
            if not agent_status['enabled'] or busy() or not wants_decision(current):continue
            if agent_status['pause_clock'] and not getattr(current,'pause_logged',False):
                current.pause_logged=True;current.log(PAUSE_LABEL)
            observation=deepcopy(current.observations());options=decision_options(current)
            started=time.time();agent_status['thinking']=True
            try:
                decision=await asyncio.wait_for(asyncio.to_thread(agent.decide,observation,current.scene,options),timeout=AGENT_TIMEOUT_S)
                agent_status['latency_s']=round(time.time()-started,2)
                if current is not sim:continue
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
    tasks = [asyncio.create_task(reason()), asyncio.create_task(clock())]
    yield
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

class Control(BaseModel):
    action:str
    mode:str|None=None
    perception:str|None=None

class SceneChoice(BaseModel):
    id:str

class CompareRequest(BaseModel):
    perception:str|None=None

class Frame(BaseModel):
    seq:int
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
    s['vision_installed'] = VISION_AVAILABLE
    return s

@app.get('/api/observations')
def observations():return sim.observations()

@app.get('/api/scenes')
def scene_list():return [s.summary() for s in scenes.values()]

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
        next_sim = new_simulation(scene, scenario, sim.mode, 'analytic-development')
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
    sim=new_simulation(scenes[body.id]);return sim.state()

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
            and current is sim and (agent_status['thinking'] or wants_decision(current)))

@app.post('/api/agent')
def agent_toggle(body:AgentToggle):
    if body.enabled is not None:
        agent_status['enabled']=body.enabled
        sim.log(f"Hosted agent {'enabled' if body.enabled else 'disabled'} by operator.")
    if body.pause_clock is not None:
        agent_status['pause_clock']=body.pause_clock
        sim.log(PAUSE_LABEL if body.pause_clock else 'Real-time mode: the mission clock runs during model inference.')
    return agent_info()

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
    for key in ('building_use','immobile','comms_outages','room_risk','incident_room'):
        if not data.get(key):data.pop(key,None)   # unset fields stay out, so existing scenario hashes are unchanged
    try:sim=Simulation(sim.scene,data,sim.mode,sim.perception_mode)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    return sim.state()

@app.post('/api/control')
def control(body:Control):
    global sim
    if busy() and body.action!='reset':raise HTTPException(409,'Paired comparison in progress')
    if body.mode is not None and body.mode not in ('adaptive','baseline'):raise HTTPException(422,'Invalid mode')
    if body.perception is not None and body.perception not in ('image-yolo','analytic-development'):raise HTTPException(422,'Invalid perception mode')
    if body.perception=='image-yolo' and not VISION_AVAILABLE:raise HTTPException(422,'Vision extra not installed')
    if body.action=='reset':
        cancel_paired()
        sim=Simulation(sim.base_scene,sim.scenario,body.mode or sim.mode,body.perception or sim.perception_mode)
    elif body.action=='start':
        if sim.status not in ('idle','paused'):raise HTTPException(409,'Reset a finished run first')
        if sim.status!='idle' and ((body.mode and body.mode!=sim.mode) or (body.perception and body.perception!=sim.perception_mode)):
            raise HTTPException(409,'Reset before changing policy or perception')
        if sim.status=='idle' and body.perception and body.perception!=sim.perception_mode:
            sim=Simulation(sim.scene,sim.scenario,body.mode or sim.mode,body.perception)
        sim.mode=body.mode or sim.mode;sim.status='running';sim.hazards_running=True
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
