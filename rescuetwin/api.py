"""Local-only API. /state is operator truth; /observations is the agent boundary."""
import asyncio
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import BaseModel, Field
from .core import Simulation, compare

sim=Simulation()

@asynccontextmanager
async def lifespan(app):
    async def clock():
        while True:
            sim.tick();await asyncio.sleep(.2)
    async def reason():
        if not os.environ.get('GEMINI_API_KEY'):return
        from .agent import MissionAgent,apply_decision
        try:agent=MissionAgent()
        except Exception:
            sim.log('Hosted agent unavailable; deterministic development policy active.');return
        while True:
            await asyncio.sleep(5)
            current=sim
            if current.status!='running' or current.escort:continue
            observation=deepcopy(current.observations())
            try:
                decision=await asyncio.wait_for(asyncio.to_thread(agent.decide,observation,current.scene),timeout=8)
                if current is sim:apply_decision(current,decision,observation['time'])
            except Exception:
                if current is sim:current.log('Hosted decision failed/rejected; pausing for operator review.');current.status='paused'
    agent_task=asyncio.create_task(reason())
    task=asyncio.create_task(clock())
    yield
    agent_task.cancel()
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass

app=FastAPI(title='RescueTwin local development API',lifespan=lifespan)

class Scenario(BaseModel):
    survivors:list[list[int]]=Field(max_length=20)
    ignitions:list[list[int]]=Field(max_length=20)
    seed:int=7

class Control(BaseModel):
    action:str
    mode:str|None=None

@app.get('/api/state')
def state():return sim.state()

@app.get('/api/observations')
def observations():return sim.observations()

@app.get('/api/scenes')
def scenes():return [sim.scene.public()]

@app.post('/api/scenario')
def scenario(body:Scenario):
    global sim
    if sim.status!='idle':raise HTTPException(409,'Reset before editing a scenario')
    try:sim=Simulation(sim.scene,body.model_dump(),sim.mode)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    return sim.state()

@app.post('/api/control')
def control(body:Control):
    global sim
    if body.mode is not None and body.mode not in ('adaptive','baseline'):raise HTTPException(422,'Invalid mode')
    if body.action=='reset':sim=Simulation(sim.scene,sim.scenario,body.mode or sim.mode)
    elif body.action=='start':
        if sim.status not in ('idle','paused'):raise HTTPException(409,'Reset a finished run first')
        if body.mode and body.mode!=sim.mode and sim.status!='idle':raise HTTPException(409,'Reset before changing policy')
        sim.mode=body.mode or sim.mode;sim.status='running'
    elif body.action=='pause':
        if sim.status!='running':raise HTTPException(409,'No active run')
        sim.status='paused'
    else:raise HTTPException(422,'Unknown control action')
    return sim.state()

@app.post('/api/compare')
async def comparison():
    scene,scenario=deepcopy(sim.scene),deepcopy(sim.scenario)
    return await asyncio.to_thread(compare,scene,scenario)

@app.websocket('/api/stream')
async def stream(ws:WebSocket):
    await ws.accept()
    from starlette.websockets import WebSocketDisconnect
    try:
        while True:
            await ws.send_json(sim.state());await asyncio.sleep(.2)
    except WebSocketDisconnect:pass
