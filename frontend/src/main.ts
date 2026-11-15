import { hazardSprites } from './hazardSprites';
import { setupModelLibrary } from './modelLibrary';
import { loadHumans, humanReady, validHumanVariant, makeHuman as createHuman, type HumanVariant } from './humans';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { MTLLoader } from 'three/addons/loaders/MTLLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import './styles.css';

type Cell = [number, number];
type Mode = 'adaptive' | 'baseline';
type Perception = 'image-yolo' | 'analytic-development';
type Tool = 'inspect' | 'survivor' | 'immobile' | 'fire' | 'remove';
interface Scenario { survivors: Cell[]; ignitions: Cell[]; seed: number; scene_id?: string; building_use?: string; immobile?: Cell[]; comms_outages?: [number, number][]; room_risk?: Record<string, string>; incident_room?: string; human_variant?: HumanVariant }
interface CostarField { key: string; label: string; value: unknown; units: string | null; field_path: string; source_id: number; dataset: string; derivation: string; note?: string }
interface SceneData {
  id: string; name: string; width: number; height: number; resolution: number; origin: [number, number];
  walls: Cell[]; exterior: Cell[]; exits: Cell[]; source: string; robot_start: Cell; time_limit: number;
  rooms: { id: string; name: string; label?: string; cells: Cell[]; observe?: Cell[]; polygon?: [number, number][] }[];
  mesh?: { file: string; source: string; wall_height: number; format?: string; materials?: string[]; textured?: boolean; frame?: string; transform?: number[][] } | null;
  floor_image?: { file: string; origin: [number, number]; pixels_per_metre: number; width: number; height: number; source: string } | null;
  costar?: { fields: CostarField[]; max_occupants: number; disclaimer: string; property_id: number; floor_plan_id: number } | null;
  checks?: Record<string, unknown> | null;
  exit_info?: { id: string; label: string; opening_width_m: number; evidence: string[]; beyond: string }[];
  assumptions?: string[]; provenance?: { item: string; source: string; derivation: string }[];
  obstacle_overlay?: { furniture?: Cell[]; source_walls?: Cell[] } | null;
}
interface Pose { x: number; y: number; z: number; yaw: number }
interface DetectionEntry { id: string; position: [number, number]; cell: Cell; confidence: number; hits: number; status: string; source: string }
interface FrameResult { seq: number; time: number; accepted: { box: number[]; confidence: number; world?: number[]; detection?: string }[]; rejected: { box: number[]; confidence: number; reason: string }[] }
interface Paired { experiment?: Record<string, unknown>; status: string; reports: Record<string, Record<string, unknown>>; running: string | null; note: string; seed: number; perception: string }
interface SimState {
  agent_supervisor?: { architecture: string; calls: number; active: unknown } | null;
  incident?: { origin: Cell; debris: Cell | null; breached?: Cell[]; time: number } | null;
  vision_installed?: boolean;
  sprinklers?: { origin: Cell; cells: Cell[] }[];
  fire?: number[][];
  searched_rooms?: string[];
  hazard_sensor?: { radius_m: number; source_distance_m: number | null;
    source_heat_intensity: number | null; smoke_intensity: number };
  scene_id: string; scenario: Scenario; time: number; status: string; robot: Cell; heading: number; pose: Pose;
  survivors: { id: string; position: Cell; status: string; injured?: boolean }[];
  heat: number[][]; smoke: number[][]; observed: number[][]; route: Cell[];
  events: { time: number; message: string }[]; metrics: Record<string, number>; mode: Mode; strategy: 'greedy' | 'mission-astar' | 'mission-astar-cached' | 'gemini'; perception_mode: Perception;
  detections: DetectionEntry[]; frame_request: { seq: number; time: number; pose: Pose } | null; degraded: boolean; scanning: boolean;
  last_frame: FrameResult | null; paired: Paired | null;
  hazard_time: number; hazards_running: boolean; holding?: boolean;
  reports?: Report[]; rooms_state?: RoomState[]; decisions?: Decision[]; withdraw_reason?: string | null; comms_up?: boolean;
}
interface Report { id: string; kind: string; time: number; floor: string; room: string | null; cell: Cell; confirmed: number; possible: number;
  mobility: string | null; hazards: { heat: number; smoke: number }; last_usable_approach: Cell; action: string; status: string; attempts: number;
  unresolved_detections?: string[]; outstanding_rooms?: string[]; unsent_reports?: number }
interface RoomState { id: string; name: string; label: string | null; status: string; observed_fraction: number; reason: string | null }
interface Decision { time: number; decision: string; target: Cell | null; trigger: string; options: { target: Cell; cost: number }[]; reason: string }
interface Observation { heat: number[][]; smoke: number[][]; observed: number[][]; survivors: DetectionEntry[] }
interface Preset { id: string; label: string; note: string; scenario?: Scenario; unavailable?: boolean; capacity?: number }

const rendererId=crypto.randomUUID();
let rendererOwned=false;
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/renderer',new Blob([JSON.stringify({id:rendererId,release:true})],{type:'application/json'})));
let replayId: string | null = null;
let replayScene: SceneData | null = null;
let replayPlaying = false, replayCursor = 0;
let sensorWorker: HTMLIFrameElement | null = null;
let sceneReady = false, sceneLoadVersion = 0;
const sourceCutaway = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1.6);
let cutawayOn = true, cutawayHeight = 1.6;
const NO_CUT = 1e6;  // plane constant that clips nothing (robot camera, cutaway off)
const CAM_W = 512, CAM_H = 384, CAM_HFOV = Math.PI / 2;
const CAM_VFOV = 2 * Math.atan(Math.tan(CAM_HFOV / 2) * CAM_H / CAM_W);
const FX = (CAM_W / 2) / Math.tan(CAM_HFOV / 2), FY = (CAM_H / 2) / Math.tan(CAM_VFOV / 2);
const OPERATOR_LAYER = 1, CAMERA_ONLY_LAYER = 2;

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing interface element: ${id}`);
  return element as T;
};
const escape = (value: unknown): string => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const number = (value: number, digits = 1): string => Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
const same = (a: Cell, b: Cell): boolean => a[0] === b[0] && a[1] === b[1];
const copyScenario = (value: Scenario): Scenario => ({ survivors: value.survivors.map(p => [...p] as Cell), ignitions: value.ignitions.map(p => [...p] as Cell), seed: value.seed, scene_id: value.scene_id, ...(value.building_use ? { building_use: value.building_use } : {}), ...(value.immobile?.length ? { immobile: value.immobile.map(p => [...p] as Cell) } : {}), ...(value.comms_outages?.length ? { comms_outages: value.comms_outages.map(o => [...o] as [number, number]) } : {}), ...(value.room_risk && Object.keys(value.room_risk).length ? { room_risk: { ...value.room_risk } } : {}), ...(value.incident_room ? { incident_room: value.incident_room } : {}), ...(value.human_variant ? { human_variant: value.human_variant } : {}) });
const key = (p: Cell): string => `${p[0]},${p[1]}`;

$('app').innerHTML = `
  <a class="skip-link" href="#workspace">Skip to simulation</a>
  <header class="app-header">
    <a class="brand" href="./">RescueTwin</a>
    <div class="header-right"><span id="connection" class="connection" role="status"><i></i>Connecting</span></div>
  </header>
  <main>
    <section class="page-intro">
      <div><h1 id="scene-heading">Waiting for scene</h1><p id="scene-size" class="subtle-label"></p></div>
      <div class="run-badge"><span id="run-status">Awaiting backend</span><strong id="clock">—</strong></div>
    </section>
    <p class="disclosure-line" role="note"><b id="disclosure-tag">Loading</b> <span id="disclosure-body">Waiting for the backend.</span></p>
    <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
    <div class="workspace-grid" id="workspace" tabindex="-1">
      <section class="panel scene-panel" aria-labelledby="scene-heading">
        <div class="scene-toolbar">
          <div class="camera-controls" aria-label="Camera">
            <button id="orbit" class="button small" aria-pressed="true">3D</button>
            <button id="plan" class="button small" aria-pressed="false">Plan</button>
            <button id="recenter" class="button subtle small">Fit</button>
            <button id="preview-hazards" class="button small" disabled>Preview hazards</button>
            <button id="focus-survivor" class="button subtle small" disabled title="Zoom to each simulated survivor in Operator truth">Find survivor</button>
            <button id="focus-fire" class="button subtle small" disabled>Find fire</button>
            <button id="expand" class="button subtle small">Fullscreen</button>
          </div>
          <div class="segmented view-toggle" role="radiogroup" aria-label="Knowledge view"><label><input type="radio" name="view" value="truth" checked /><span>Operator truth</span></label><label><input type="radio" name="view" value="belief" /><span>Robot belief</span></label></div>
          <div class="layer-controls" aria-label="Scene layers">
            <label><input type="checkbox" id="walls-layer" checked />Walls</label>
            <label id="cutaway-control" hidden title="Operator view clips the scanned mesh above this height; robot camera always sees full geometry">Cutaway <select id="cutaway-height"><option value="1.0">1.0 m</option><option value="1.6" selected>1.6 m</option><option value="2.2">2.2 m</option><option value="off">Off</option></select></label>
            <label title="Room fire-risk map: gas source (searched first), sprinkler (slightly later); fixture tags, not sensor data"><input type="checkbox" id="risk-layer" checked /><span class="swatch risk"></span>Risk map</label>
            <label id="furniture-control" hidden title="Blocked cells derived from scan geometry: furniture/fixtures vs walls and door frames"><input type="checkbox" id="furniture-layer" /><span class="swatch furniture"></span>Scan obstacles</label>
            <label><input type="checkbox" id="fire-layer" checked />Flames</label>
            <label><input type="checkbox" id="heat-layer" /><span class="swatch heat"></span>Heat map</label>
            <label><input type="checkbox" id="smoke-layer" checked title="Smoke bands start at 0.015, 0.20, and 0.50 normalized intensity" /><span class="swatch smoke"></span>Smoke</label>
            <label><input type="checkbox" id="observed-layer" />Unobserved</label>
            <label><input type="checkbox" id="route-layer" checked /><span class="swatch route"></span>Route</label>
          </div>
        </div>
        <div id="viewport" class="viewport"><div id="scene-empty" class="scene-empty"><strong>Waiting for simulation state</strong><p>Start the backend on port 8000 to load your scene.</p></div><div id="hazard-readout" class="hazard-readout"></div><div id="cell-readout" class="cell-readout">Select a cell to inspect</div></div>
        <div class="scene-footer"><div class="legend"><span><i class="legend-person"></i>Survivor (simulated; blue through walls)</span><span><i class="legend-robot"></i>Robot</span><span><i class="legend-exit"></i>Verified exit</span><span><i class="legend-fire"></i>Ignition</span><span><i class="legend-detection"></i>Detection</span><span><i class="legend-risk gas"></i>Gas source</span><span><i class="legend-risk sprinkler"></i>Sprinkler risk tag</span><span><i class="legend-risk both"></i>Both</span><span><i class="legend-risk sprinkler"></i>Simulated ceiling sprinkler</span></div><span id="map-help" class="map-help">Drag to orbit · Scroll to zoom · Right-drag to pan</span></div>
        <details class="keyboard-editor"><summary>Cell inspector & keyboard editing</summary><div class="cell-inputs"><label>Column x<input id="cell-x" type="number" min="0" step="1" value="0" /></label><label>Row y<input id="cell-y" type="number" min="0" step="1" value="0" /></label><button id="apply-cell" class="button secondary">Inspect cell</button></div><p>Coordinates are grid cells; the scene's origin and resolution convert them to metres in the scan frame. Choose an editing tool in Scenario setup, then apply it here or click the map.</p></details>
      </section>
      <aside class="sidebar">
        <section class="panel" aria-labelledby="property-heading"><div class="panel-heading compact"><div><h2 id="property-heading">Property context</h2></div></div>
          <label class="seed-label" for="scene-select">Scene <select id="scene-select"></select></label>
            <button id="prepared-models" class="button small">Browse prepared models</button>
            <button id="load-map" class="button small">Import custom floor plan JSON…</button>
            <p class="field-hint">Choose a validated scene above, or browse every downloaded model. Plan shows the active scene from above. Import accepts annotated metric JSON, not images or PDFs.</p>
            <input id="map-file" type="file" accept=".json,application/json" hidden />
            <p id="sensor-readings" class="field-hint">Waiting for robot hazard sensor</p>
          <div id="property-card" class="property-card"><p class="field-hint">No CoStar record for this scene.</p></div>
          <details class="details-block"><summary>Provenance & scene checks</summary><div id="provenance"></div></details>
        </section>
        <section class="panel" aria-labelledby="controls-heading"><div class="panel-heading compact"><div><h2 id="controls-heading">Run simulation</h2></div></div>
          <fieldset id="mode-fieldset"><legend>Planning strategy</legend><div class="segmented"><label><input type="radio" name="mode" value="adaptive" checked /><span>Risk-aware</span></label><label><input type="radio" name="mode" value="baseline" /><span>Distance-only</span></label></div></fieldset>
          <fieldset id="perception-fieldset"><legend>Survivor perception</legend><div class="segmented"><label><input type="radio" name="perception" value="image-yolo" /><span>YOLO · RGB-D</span></label><label><input type="radio" name="perception" value="analytic-development" checked /><span>Analytic fixture</span></label></div></fieldset>
          <fieldset id="strategy-fieldset"><legend>Mission algorithm</legend><div class="segmented"><label title="Baseline: searches the nearest unsearched room next and escorts each person straight to the exit."><input type="radio" name="strategy" value="greedy" checked /><span>Baseline</span></label><label title="Plans the whole remaining mission in one A* pass, ranking rooms by survivor likelihood and observed fire/smoke urgency, and re-plans each second. Changes only the room ordering; the local safety interlock is unchanged."><input type="radio" name="strategy" value="mission-astar" /><span>Mission A*</span></label><label title="Gemini mission planner: placeholder, not yet available."><input type="radio" name="strategy" value="gemini" disabled /><span>Gemini</span></label></div></fieldset>
          <p id="strategy-description" class="field-hint">Baseline: nearest unsearched room first.</p>
          <p id="agent-status" class="field-hint">Checking hosted agent…</p>
          <p id="mode-description" class="field-hint">Analytic fixture uses simulator truth and is not image perception.</p>
          <div class="control-buttons"><button id="start" class="button primary">▶ Start run</button><button id="pause" class="button secondary">Ⅱ Pause</button><button id="reset" class="button icon-button" title="Reset simulation" aria-label="Reset simulation">↺</button><button id="withdraw" class="button subtle small" title="Operator stop: the robot finishes any escort, returns to the exit and files a mission-end report">Withdraw</button></div><p id="control-hint" class="field-hint">Connect to the backend to begin.</p>
        </section>
        <section class="panel" aria-labelledby="scenario-heading"><div class="panel-heading compact"><div><h2 id="scenario-heading">Place the conditions</h2></div></div>
          <label class="seed-label" for="building-use" title="Static building information only; it changes where the robot searches first, never what it knows about people">Building use <select id="building-use"><option value="">Auto</option></select></label>
          <p id="building-use-hint" class="field-hint"></p>
          <label class="seed-label" for="human-variant">Survivor appearance <select id="human-variant"><option value="adult-male">Adult man</option><option value="adult-female">Adult woman</option><option value="child-male">Boy</option><option value="child-female">Girl</option><option value="older-male">Older man</option><option value="older-female">Older woman</option><option value="mixed">Mixed: one of each, repeated</option></select></label>
          <p class="field-hint">Synthetic character appearances. Apply to update the people in the scene; the selection stays fixed during runs and comparisons.</p>
          <div class="room-risk"><span class="seed-label" title="Pre-incident fixture tags per room. Gas source 1.5x search priority, sprinkler 0.8x, neither/unknown 1x">Room fire risk</span><div id="room-risk-list"></div><p id="room-risk-hint" class="field-hint"></p></div>
          <div id="presets" class="preset-list"></div>
          <p id="edit-hint" class="field-hint">Editing is available when the simulation is idle.</p><fieldset id="tool-fieldset"><legend class="sr-only">Map editing tool</legend><div class="tool-grid"><label><input name="tool" type="radio" value="inspect" checked /><span><b aria-hidden="true">⌖</b>Inspect</span></label><label><input name="tool" type="radio" value="survivor" /><span><b aria-hidden="true">♙</b>Survivor</span></label><label title="Mark or unmark a placed survivor as unable to follow the robot (checked on contact)"><input name="tool" type="radio" value="immobile" /><span><b aria-hidden="true">♿</b>Can't walk</span></label><label><input name="tool" type="radio" value="fire" /><span><b aria-hidden="true">♨</b>Ignition</span></label><label><input name="tool" type="radio" value="remove" /><span><b aria-hidden="true">−</b>Remove</span></label></div></fieldset>
          <div class="scenario-counts"><span><i class="tiny-dot blue"></i><strong id="survivor-count">—</strong> survivors</span><span><i class="tiny-dot orange"></i><strong id="fire-count">—</strong> ignitions</span></div>
          <label class="seed-label" for="seed">Random seed <input id="seed" type="number" step="1" value="7" /></label>
          <label class="seed-label" for="incident-room">Kitchen incident location <select id="incident-room"><option value="">Off</option></select></label>
          <div class="save-row"><button id="apply" class="button secondary">Apply scenario</button><button id="discard" class="button subtle small">Discard</button></div><div class="file-actions"><button id="export" class="button subtle small">↓ Save JSON</button><button id="import" class="button subtle small">↑ Load JSON</button><input id="scenario-file" type="file" accept=".json,application/json" hidden /></div>
        </section>
        <section class="panel compare-callout"><h2>Compare runs</h2><p id="compare-text" class="field-hint">Distance-only vs risk-aware from the same frozen scenario, sensors, hazard model and safety rules.</p><button id="compare" class="button secondary full-width">Compare strategies <span aria-hidden="true">↗</span></button><p class="field-hint">For a CoStar workload, select a capacity preset, Apply it, then compare. The report includes its source field and scenario hash.</p></section>
      </aside>
      <dl class="run-summary" aria-label="Mission metrics">
        <div><dt>Evacuated</dt><dd><strong id="evacuated">—</strong> <span id="population"></span> <span id="unresolved-note" class="subtle-label"></span></dd></div>
        <div><dt>Exposure (normalized)</dt><dd id="exposure">—</dd></div>
        <div><dt>Distance (m)</dt><dd id="distance">—</dd></div>
        <div><dt>Route</dt><dd id="route-note">—</dd></div>
        <div><dt>Perception</dt><dd><span class="tiny-dot" id="perception-dot"></span> <span id="perception-label">—</span> · <span id="perception-note">—</span></dd></div>
      </dl>
      <section class="panel camera-panel" aria-labelledby="camera-heading"><div class="panel-heading compact"><div><h2 id="camera-heading">Person detection</h2></div><span id="camera-status" class="subtle-label">No frame yet</span></div>
        <div class="camera-body"><canvas id="robot-feed" width="${CAM_W}" height="${CAM_H}" aria-label="Rendered robot camera frame with YOLO detection boxes"></canvas>
          <div class="camera-side"><p class="field-hint" id="camera-explainer">Frames are rendered from the scanned-geometry scene at the simulator's camera pose and sent to the backend, where YOLO runs on the RGB image and depth places each box in the scan frame. Two consistent frames confirm a potential survivor.</p><ol id="detections" class="event-list detection-list"><li class="empty-line">No detections yet.</li></ol></div></div></section>
      <section class="panel event-panel" aria-labelledby="team-heading"><div class="panel-heading compact"><div><h2 id="team-heading">Outside team</h2><p class="subtle-label">Reports the robot sends to responders. You acknowledge on their behalf. <span id="comms-status"></span></p></div></div>
        <ol id="reports" class="event-list"><li class="empty-line">No reports yet.</li></ol>
        <details class="details-block"><summary>Rooms (<span id="rooms-summary">—</span>)</summary><ol id="rooms" class="event-list"></ol></details>
        <details class="details-block"><summary>Decision record</summary><ol id="decisions" class="event-list"></ol></details></section>
      <section class="panel event-panel" aria-labelledby="events-heading"><div class="panel-heading compact"><div><h2 id="events-heading">Event timeline</h2></div></div><ol id="events" class="event-list"><li class="empty-line">Events appear here as the backend reports them.</li></ol></section>
      <section id="comparison" class="panel comparison-panel" aria-labelledby="comparison-heading" hidden><div class="panel-heading compact"><div><h2 id="comparison-heading">Strategy comparison</h2></div><button id="close-comparison" class="button subtle small">Close</button></div><p id="comparison-context" class="field-hint"></p><div id="comparison-body"></div></section>
    </div>
  </main>`;

let state: SimState | null = null;
let scene: SceneData | null = null;
let belief: Observation | null = null;
let draft: Scenario | null = null;
let dirty = false;
let strategySynced = false;   // adopt the backend's strategy once on load, then keep the operator's idle selection
let busy = false;
let connected = false;
let selectedTool: Tool = 'inspect';
let eventSignature = '';
let selectedCell: Cell | null = null;
let pollInFlight = false;
let renderer: THREE.WebGLRenderer | null = null;
let controls: OrbitControls | null = null;
let camera: THREE.PerspectiveCamera | null = null;
let view: 'truth' | 'belief' = 'truth';
let frameInFlight = false;
let lastFrameSeq = -1;
let lastFrameImage: ImageData | null = null;
let shownPairedStatus = '';
const world = new THREE.Scene();
const background = new THREE.Color('#0d151f');
world.background = background;
const staticLayer = new THREE.Group();
const dynamicLayer = new THREE.Group();
const incidentEffect = operatorOnly(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
  transparent: true, depthWrite: false,
  uniforms: { age: { value: 0 } },
  vertexShader: `
    varying vec2 p;
    void main() {
      p = uv * 2.0 - 1.0;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: `
    varying vec2 p;
    uniform float age;
    void main() {
      float r = length(p);
      float flash = exp(-14.0 * r * r - 5.0 * age);
      float shell = (r - 0.62) * (r - 0.62);
      float wave = exp(-105.0 * shell - 2.5 * age);
      vec2 cloud = p + 0.12 * vec2(sin(p.y * 19.0), cos(p.x * 15.0));
      float smoke = exp(-5.0 * dot(cloud, cloud)) * smoothstep(0.0, 0.5, age);
      float edge = 1.0 - smoothstep(0.75, 1.0, r);
      vec3 color = mix(vec3(0.40, 0.42, 0.44), vec3(1.0, 0.55, 0.12), clamp(wave + flash, 0.0, 1.0));
      gl_FragColor = vec4(mix(color, vec3(1.0, 0.96, 0.8), flash),
        edge * max(max(flash, wave), smoke * 0.55) * (1.0 - smoothstep(0.65, 1.0, age)));
    }`,
})));
incidentEffect.visible = false;
incidentEffect.renderOrder = 6;
let incidentStarted = 0;
let shownIncident = '';
const avatarLayer = new THREE.Group();
world.add(staticLayer, dynamicLayer, avatarLayer, incidentEffect);
const hemi = new THREE.HemisphereLight(0xf2f6ff, 0x5b5248, 2.2);
const sunlight = new THREE.DirectionalLight(0xffffff, 1.6);
sunlight.position.set(-10, 30, 15);
world.add(hemi, sunlight);
for (const light of [hemi, sunlight]) { light.layers.enableAll(); }
const raycaster = new THREE.Raycaster();
raycaster.layers.enableAll();
const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const pointer = new THREE.Vector2();
const layers = { fire: true, heat: false, smoke: true, observed: false, route: true };
// Upstream PR #1 hazard rendering: textured heat (floor) and banded smoke (near ceiling) fields,
// instanced smoke clouds and flame cones. Fields/clouds are operator-only; flames are physical (camera sees them).
const wallLayer = new THREE.Group();
let gridWallMesh: THREE.InstancedMesh | undefined;
let gridWallIndices = new Map<string, number>();
let shownBreach = '';
const obstacleLayer = new THREE.Group();  // operator-only furniture/source-wall cells
world.add(obstacleLayer);
const riskLayer = new THREE.Group();  // operator-only room fire-risk tint
world.add(riskLayer);
function createField(order: number) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d')!;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  mesh.rotation.x = -Math.PI / 2;
  mesh.renderOrder = order;
  mesh.layers.set(OPERATOR_LAYER);
  return { canvas, context, texture, mesh };
}
const heatField = createField(1);
const smokeField = createField(2);
const smokeLimit = 320;
const flameLimit = 256;
const smokeClouds = hazardSprites(smokeLimit, true);
smokeClouds.layers.set(OPERATOR_LAYER);
smokeClouds.renderOrder = 3;
const smokeTransform = new THREE.Object3D();
const heatColor = new THREE.Color();
const flames = hazardSprites(flameLimit, false);
flames.renderOrder = 4;
const flameTransform = new THREE.Object3D();
let focusCell: Cell | null = null;
const hotColor = new THREE.Color('#e6451d');
let cameraView: 'orbit' | 'plan' = 'orbit';
let blockedCells = new Set<string>();
const FLAME_HEAT = 0.3;   // matches the backend's FLAME_VISIBLE_HEAT
world.add(wallLayer, heatField.mesh, smokeField.mesh, smokeClouds, flames);
const avatars = new Map<string, THREE.Group>();
const robotCamera = new THREE.PerspectiveCamera(THREE.MathUtils.radToDeg(CAM_VFOV), CAM_W / CAM_H, 0.05, 40);
robotCamera.layers.set(0);
robotCamera.layers.enable(CAMERA_ONLY_LAYER);
const rgbTarget = new THREE.WebGLRenderTarget(CAM_W, CAM_H, { type: THREE.UnsignedByteType, colorSpace: THREE.SRGBColorSpace });
const depthTarget = new THREE.WebGLRenderTarget(CAM_W, CAM_H, { type: THREE.FloatType, format: THREE.RGBAFormat });
const depthMaterial = new THREE.ShaderMaterial({
  vertexShader: `#include <common>
    #include <skinning_pars_vertex>
    varying float vDepth;
    void main() {
      #include <skinbase_vertex>
      #include <begin_vertex>
      #include <skinning_vertex>
      vec4 local = vec4(transformed, 1.0);
      #ifdef USE_INSTANCING
        local = instanceMatrix * local;
      #endif
      vec4 mv = modelViewMatrix * local;
      vDepth = -mv.z;
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: `varying float vDepth; void main() { gl_FragColor = vec4(vDepth, 0.0, 0.0, 1.0); }`,
  side: THREE.DoubleSide,
});

function notify(message: string, error = false): void {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = false;
}

async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (replayId && body !== undefined) throw new Error('Recorded runs are read only. Return to Live first.');
  if (replayId && path === 'scene') return replayScene as T;
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ?? AbortSignal.timeout(15000),
    cache: 'no-store',
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`${response.status} ${response.statusText}${detail ? `: ${detail}` : ''}`);
  }
  const content = await response.text();
  return content ? JSON.parse(content) as T : undefined as T;
}

function validCell(value: unknown): value is Cell {
  return Array.isArray(value) && value.length === 2 && value.every(n => typeof n === 'number' && Number.isFinite(n));
}

function validateState(value: unknown): asserts value is SimState {
  const s = value as SimState | null;
  if (!s || typeof s.scene_id !== 'string' || !s.metrics || !s.scenario || !Number.isSafeInteger(s.scenario.seed) || !Number.isFinite(s.time) || typeof s.status !== 'string' || !validCell(s.robot) || !['adaptive', 'baseline'].includes(s.mode)) throw new Error('The backend returned an invalid simulation state.');
  if (![s.scenario.survivors, s.scenario.ignitions, s.route].every(list => Array.isArray(list) && list.every(validCell)) || !Array.isArray(s.survivors) || !Array.isArray(s.events) || ![s.heat, s.smoke, s.observed].every(m => Array.isArray(m) && m.every(Array.isArray))) throw new Error('The backend returned malformed telemetry.');
}

function validateScene(value: unknown): asserts value is SceneData {
  const s = value as SceneData | null;
  if (!s || !Number.isInteger(s.width) || !Number.isInteger(s.height) || !(s.resolution > 0) || !Array.isArray(s.origin) || ![s.walls, s.exterior, s.exits].every(l => Array.isArray(l) && l.every(validCell)) || !Array.isArray(s.rooms)) throw new Error('The backend returned an invalid scene.');
}

function blocked(cell: Cell): boolean {
  return !!scene && (scene.walls.some(p => same(p, cell)) || scene.exterior.some(p => same(p, cell)));
}

function validateScenario(value: unknown): Scenario {
  if (!state || !scene) throw new Error('Load a backend scene before importing a scenario.');
  if (!value || typeof value !== 'object') throw new Error('Expected a scenario JSON object.');
  const candidate = value as Scenario;
  if (!Number.isSafeInteger(candidate.seed)) throw new Error('Seed must be a safe integer.');
  if (candidate.human_variant !== undefined && !validHumanVariant(candidate.human_variant)) throw new Error('Unknown survivor appearance.');
  if (candidate.scene_id !== undefined && candidate.scene_id !== scene.id) throw new Error(`This scenario belongs to scene ${candidate.scene_id}; select that scene first.`);
  const walls = new Set([...scene.walls, ...scene.exterior].map(key));
  for (const field of ['survivors', 'ignitions'] as const) {
    if (!Array.isArray(candidate[field])) throw new Error(`${field} must be an array of [x, y] cells.`);
    const seen = new Set<string>();
    for (const cell of candidate[field]) {
      if (!validCell(cell) || !cell.every(Number.isInteger) || !inBounds(cell)) throw new Error(`${field} contains an invalid or out-of-bounds cell.`);
      if (walls.has(key(cell))) throw new Error(`${field} cannot be placed inside walls or outside the floor.`);
      if (seen.has(key(cell))) throw new Error(`${field} contains duplicate cells.`);
      seen.add(key(cell));
    }
  }
  if (candidate.incident_room !== undefined && !scene.rooms.some(room => room.id === candidate.incident_room)) throw new Error('Select a room in the current floor plan.');
  return { ...copyScenario(candidate), scene_id: scene.id };
}

function inBounds(cell: Cell): boolean {
  return !!scene && cell[0] >= 0 && cell[1] >= 0 && cell[0] < scene.width && cell[1] < scene.height;
}

function idle(): boolean { return connected && state?.status.toLowerCase() === 'idle' && !pairedRunning(); }
function pairedRunning(): boolean { return state?.paired?.status === 'running'; }
function chosen<T extends string>(name: string): T { return document.querySelector<HTMLInputElement>(`input[name="${name}"]:checked`)!.value as T; }

function syncUI(): void {
  const editable = idle() && !busy && !replayId;
  const status = state?.status.toLowerCase();
  $('connection').className = `connection ${connected ? 'online' : 'offline'}`;
  $('connection').innerHTML = `<i></i>${connected ? 'Backend connected' : state ? 'Connection lost' : 'Backend offline'}`;
  $('run-status').textContent = state ? (pairedRunning() ? `PAIRED · ${state.paired!.running?.toUpperCase()}` : state.status.toUpperCase()) : 'AWAITING BACKEND';
  $('run-status').classList.toggle('running', status === 'running');
  const preview = $<HTMLButtonElement>('preview-hazards');
  preview.disabled = !connected || busy || dirty || status === 'running' || pairedRunning() || !state || state.hazard_time >= 600;
  preview.textContent = state?.hazards_running && status !== 'running' ? 'Pause hazards' : 'Preview hazards';
  $('start').textContent = status === 'paused' ? '▶ Resume run' : '▶ Start run';
  $('mode-description').textContent = chosen<Perception>('perception') === 'image-yolo'
    ? 'YOLO detects people in browser-rendered RGB-D frames. Appearance labels are scenario inputs, not detector predictions.'
    : 'Analytic fixture uses simulator truth and is not image perception.';
  ($('start') as HTMLButtonElement).disabled = !sceneReady || !humanReady(state?.scenario.human_variant) || !connected || busy || dirty || pairedRunning() || !['idle', 'paused'].includes(status ?? '');
  ($('pause') as HTMLButtonElement).disabled = !connected || busy || status !== 'running' || pairedRunning();
  ($('withdraw') as HTMLButtonElement).disabled = !connected || busy || !['running', 'paused'].includes(status ?? '') || pairedRunning() || !!state?.withdraw_reason;
  ($('reset') as HTMLButtonElement).disabled = !connected || busy || !state;
  ($('mode-fieldset') as HTMLFieldSetElement).disabled = !editable;
  ($('perception-fieldset') as HTMLFieldSetElement).disabled = !editable;
  ($('strategy-fieldset') as HTMLFieldSetElement).disabled = !editable;
  if (state && (!idle() || !strategySynced)) { strategySynced = true; document.querySelectorAll<HTMLInputElement>('input[name="strategy"]').forEach(input => { input.checked = input.value === (state!.strategy === 'mission-astar-cached' ? 'mission-astar' : state!.strategy); }); }
  $('strategy-description').textContent = STRATEGY_DESCRIPTION[chosen<Strategy>('strategy')];
  $<HTMLButtonElement>('load-map').disabled = !connected || busy || dirty || status === 'running' || !!state?.hazards_running || pairedRunning();
  ($('scene-select') as HTMLSelectElement).disabled = !connected || busy || status === 'running' || pairedRunning();
  document.querySelectorAll<HTMLInputElement>('input[name="tool"]').forEach(input => { input.disabled = input.value !== 'inspect' && !editable; });
  document.querySelectorAll<HTMLButtonElement>('#presets button').forEach(b => { b.disabled = !editable || b.dataset.unavailable === '1'; });
  ($('seed') as HTMLInputElement).disabled = !editable;
  $<HTMLSelectElement>('human-variant').disabled = !editable;
  $<HTMLSelectElement>('human-variant').value = draft?.human_variant ?? 'adult-male';
  const incidentRoom = $<HTMLSelectElement>('incident-room');
  incidentRoom.disabled = !editable || !scene?.rooms.length;
  incidentRoom.value = draft?.incident_room ?? '';
  ($('apply') as HTMLButtonElement).disabled = !editable || !dirty;
  ($('discard') as HTMLButtonElement).disabled = busy || !dirty;
  ($('import') as HTMLButtonElement).disabled = !editable;
  ($('export') as HTMLButtonElement).disabled = !draft;
  ($('compare') as HTMLButtonElement).disabled = !sceneReady || !humanReady(state?.scenario.human_variant) || !connected || busy || dirty || pairedRunning() || status === 'running';
  const missionLabel = state?.agent_supervisor ? ` · agent ${state.agent_supervisor.architecture} · ${state.agent_supervisor.calls} requests` : state?.strategy?.startsWith('mission-astar') ? ' · mission-level A*' : ' · baseline';
  $('control-hint').textContent = !sceneReady && connected ? 'Scene assets loading or unavailable; mission start is disabled.' : !connected ? 'Backend unavailable. Showing the last received state, if any.' : busy ? 'Waiting for the backend…' : pairedRunning() ? 'Paired comparison running; keep this tab open (it renders the robot camera).' : dirty ? 'Apply or discard scenario changes before starting.' : status === 'idle' ? 'Ready. Select a strategy and start your run.' : status === 'running' ? (state?.holding ? 'Robot holding: no viable route yet; waiting for hazards to change.' : `Running ${state?.mode === 'adaptive' ? 'risk-aware' : 'distance-only'} strategy${missionLabel}. Pause to inspect.`) : status === 'paused' ? 'Simulation paused. Resume or reset to edit.' : 'Reset the simulation to edit and start a new run.';
  $('edit-hint').textContent = !idle() ? 'Scenario editing is locked. Reset to idle to edit.' : dirty ? 'Unsaved changes · Apply to send to the backend.' : 'Pick a preset or a tool, then click a floor cell. Changes stay local until applied.';
  $('apply').textContent = dirty ? 'Apply scenario •' : 'Scenario applied';
  $('survivor-count').textContent = draft ? String(draft.survivors.length) : '—';
  $('fire-count').textContent = draft ? String(draft.ignitions.length) : '—';
  $('apply-cell').textContent = selectedTool === 'inspect' ? 'Inspect cell' : selectedTool === 'remove' ? 'Remove from cell' : `Place ${selectedTool === 'fire' ? 'ignition' : 'survivor'}`;
}

async function loadScene(): Promise<void> {
  const next = await api<unknown>('scene');
  validateScene(next);
  scene = next;
  selectedCell = null;
  buildStaticScene();
  recenter();
  renderSceneInfo();
  await loadBuildingUses();
  await loadRiskMap();
  await loadPresets();
  const list = await api<{ id: string; name: string; source: string; checks_passed: boolean | null }[]>('scenes');
  ($('scene-select') as HTMLSelectElement).innerHTML = list.map(s => `<option value="${escape(s.id)}"${s.id === scene!.id ? ' selected' : ''}>${escape(s.name)}${s.source === 'synthetic' ? ' (synthetic)' : ''}</option>`).join('');
  $<HTMLSelectElement>('incident-room').replaceChildren(new Option('Off', ''),
    ...scene.rooms.filter(room => room.cells.length).map(room => new Option(room.label ?? room.name, room.id)));
}

interface BuildingUses { inferred: string; source: string; profiles: { id: string; label: string; rationale: string }[] }
let buildingUses: BuildingUses | null = null;

async function loadBuildingUses(): Promise<void> {
  buildingUses = await api<BuildingUses>('building-uses');
  const auto = buildingUses.profiles.find(p => p.id === buildingUses!.inferred);
  ($('building-use') as HTMLSelectElement).innerHTML = `<option value="">Auto: ${escape(auto?.label ?? buildingUses.inferred)}</option>` +
    buildingUses.profiles.map(p => `<option value="${escape(p.id)}">${escape(p.label)}</option>`).join('');
  syncBuildingUse();
}

function syncBuildingUse(): void {
  const select = $('building-use') as HTMLSelectElement;
  if (!buildingUses) return;
  if (document.activeElement !== select) select.value = draft?.building_use ?? '';
  select.disabled = !idle() || busy;
  const chosen = buildingUses.profiles.find(p => p.id === (select.value || buildingUses!.inferred));
  $('building-use-hint').textContent = chosen
    ? `${chosen.rationale} ${select.value ? 'Operator selection.' : `From ${buildingUses.source}.`} Search order only; not occupancy data.`
    : '';
}

interface RiskRoom { default_tag: string; simulated_sprinkler: boolean; id: string; name: string; tag: string; tag_label: string; factor: number; source: string; provenance: string | null }
interface RiskMap { rooms: RiskRoom[]; building_default: string; building_source: string; tags: { id: string; label: string; factor: number }[]; note: string }
let riskMap: RiskMap | null = null;
const RISK_COLORS: Record<string, string> = { gas_heater: '#ff8a3d', sprinkler: '#4aa8ff', both: '#b07cff' };
const RISK_LABELS: Record<string, string> = { gas_heater: 'Gas source', sprinkler: 'Fire sprinkler', both: 'Gas source + sprinkler', neither: 'Neither', unknown: 'Unknown' };

async function loadRiskMap(): Promise<void> {
  riskMap = await api<RiskMap>('risk-map');
  $('room-risk-list').innerHTML = riskMap.rooms.map(r => `<label class="room-risk-row"><span>${escape(r.name)}</span><select data-room="${escape(r.id)}"><option value="">Default (${escape(RISK_LABELS[r.default_tag] ?? r.default_tag)})</option>` +
    riskMap!.tags.map(t => `<option value="${escape(t.id)}">${escape(t.label)} · ${t.factor}×</option>`).join('') + '</select></label>').join('');
  syncRoomRisk();
}

function roomRiskTag(roomId: string): string {
  const room = riskMap?.rooms.find(room => room.id === roomId);
  const override = draft?.room_risk?.[roomId];
  if (!override) return room?.default_tag ?? 'unknown';
  if (!room?.simulated_sprinkler) return override;
  return override === 'gas_heater' || override === 'both' ? 'both' : 'sprinkler';
}

function syncRoomRisk(): void {
  if (!riskMap) return;
  document.querySelectorAll<HTMLSelectElement>('#room-risk-list select').forEach(select => {
    if (document.activeElement !== select) select.value = draft?.room_risk?.[select.dataset.room!] ?? '';
    select.disabled = !idle() || busy;
  });
  const tagged = Object.keys(draft?.room_risk ?? {}).length;
  $('room-risk-hint').textContent = `Simulated sprinkler heads count toward room risk. Building default: ${RISK_LABELS[riskMap.building_default] ?? riskMap.building_default} (${riskMap.building_source}). ` +
    `${tagged ? `${tagged} operator tag${tagged > 1 ? 's' : ''}. ` : ''}Gas source rooms are searched first, sprinklered rooms slightly later. Fixture tags, not sensor data.`;
  drawRiskLayer();
}

function drawRiskLayer(): void {
  clearGroup(riskLayer);
  if (!scene) return;
  const r = scene.resolution;
  for (const room of scene.rooms) {
    const color = RISK_COLORS[roomRiskTag(room.id)];
    if (!color || !room.cells.length) continue;
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(r, 0.02, r), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28, depthWrite: false }), room.cells.length);
    const matrix = new THREE.Matrix4();
    room.cells.forEach((cell, i) => mesh.setMatrixAt(i, matrix.makeTranslation(position(cell, 0.03))));
    riskLayer.add(operatorOnly(mesh));
  }
  riskLayer.visible = ($('risk-layer') as HTMLInputElement).checked;
}

async function loadPresets(): Promise<void> {
  const use = draft?.building_use ? `&building_use=${encodeURIComponent(draft.building_use)}` : '';
  const presets = await api<Preset[]>(`presets?seed=${draft?.seed ?? 7}${use}`);
  $('presets').innerHTML = presets.map((p, i) => `<button class="button subtle small preset" data-index="${i}" data-unavailable="${p.unavailable ? 1 : 0}" title="${escape(p.note)}">${escape(p.label)}</button>`).join('') +
    presets.filter(p => p.id.startsWith('capacity-')).slice(0, 1).map(p => `<p class="field-hint capacity-note">${escape(p.note)}</p>`).join('');
  document.querySelectorAll<HTMLButtonElement>('#presets button').forEach(b => b.addEventListener('click', () => {
    const preset = presets[Number(b.dataset.index)];
    if (!preset.scenario || !idle()) return;
    draft = validateScenario({ ...preset.scenario, ...(draft?.room_risk ? { room_risk: draft.room_risk } : {}), ...(draft?.human_variant ? { human_variant: draft.human_variant } : {}) });
    ($('seed') as HTMLInputElement).value = String(draft.seed);
    dirty = JSON.stringify(draft) !== JSON.stringify({ ...state!.scenario, scene_id: scene!.id });
    drawDynamic();
    syncUI();
    notify(`${preset.label}. ${preset.note} Apply to use it.`);
  }));
  syncUI();
}

function renderSceneInfo(): void {
  if (!scene) return;
  const real = scene.source !== 'synthetic';
  $('scene-heading').textContent = scene.name;
  $('scene-size').textContent = `${number(scene.width * scene.resolution)} × ${number(scene.height * scene.resolution)} m · ${number(scene.resolution, 3)} m/cell`;
  $('disclosure-body').textContent = real
    ? `Real: CoStar record. Geometry: ${scene.mesh?.source ?? scene.source}. Synthetic: fire, smoke, people, robot. ${scene.exits.length === 1 ? 'Single verified exit; corridor beyond assumed available. ' : ''}Not a fire-safety assessment.`
    : 'Simulated geometry, people and hazard fields. Not a property.';
  $('disclosure-tag').textContent = real ? 'Scanned property, synthetic emergency.' : 'Synthetic fixture.';
  const c = scene.costar;
  const pick = (k: string): CostarField | undefined => c?.fields.find(f => f.key === k);
  const show = (f: CostarField | undefined): string => {
    if (!f) return '';
    const value = f.value === null || f.value === undefined ? 'Unknown (null)' : `${f.value}${f.units && !['bedrooms', 'bathrooms', 'stories', 'units', 'year'].includes(f.units) ? ` ${f.units}` : ''}`;
    return `<div class="property-row"><dt>${escape(f.label)}</dt><dd>${escape(value)}${f.note ? `<small>${escape(f.note)}</small>` : ''}</dd></div>`;
  };
  $('property-card').innerHTML = c
    ? `<dl class="property-list">${['address', 'property_type', 'stories', 'total_units', 'year_built', 'floor_plan', 'beds', 'baths', 'area', 'max_occupants', 'sprinkler_type', 'has_sprinklers', 'smoke_detector', 'gas_heating', 'gas_water_heater', 'gas_range', 'gas_mixed_range', 'gas_oven', 'gas_cooktop', 'gas_fireplace', 'gas_cooling', 'construction_material'].map(k => show(pick(k))).join('')}</dl><p class="field-hint">${escape(c.disclaimer)}</p>`
    : '<p class="field-hint">No CoStar record for this scene; capacity presets are unavailable.</p>';
  const checks = scene.checks ? Object.entries(scene.checks).filter(([k]) => !['failed'].includes(k)) : [];
  $('provenance').innerHTML = `
    ${c ? `<h3>CoStar fields</h3><ul class="provenance-list">${c.fields.map(f => `<li><b>${escape(f.label)}</b> · ${escape(f.dataset)} #${escape(f.source_id)} · <code>${escape(f.field_path)}</code> · ${escape(f.derivation)}</li>`).join('')}</ul>` : ''}
    ${scene.provenance ? `<h3>Scene sources</h3><ul class="provenance-list">${scene.provenance.map(p => `<li><b>${escape(p.item)}</b>: ${escape(p.source)} — ${escape(p.derivation)}</li>`).join('')}</ul>` : ''}
    ${scene.exit_info ? `<h3>Exits</h3><ul class="provenance-list">${scene.exit_info.map(e => `<li><b>${escape(e.label)}</b> (${number(e.opening_width_m, 2)} m opening): ${e.evidence.map(escape).join('; ')}. Beyond: ${escape(e.beyond)}.</li>`).join('')}</ul>` : ''}
    ${checks.length ? `<h3>Alignment checks</h3><ul class="provenance-list">${checks.map(([k, v]) => `<li>${escape(k.replace(/_/g, ' '))}: <b>${escape(v)}</b></li>`).join('')}</ul>` : ''}
    ${scene.assumptions ? `<h3>Assumptions</h3><ul class="provenance-list">${scene.assumptions.map(a => `<li>${escape(a)}</li>`).join('')}</ul>` : ''}`;
}

function acceptState(next: SimState, newSceneSelection = false): void {
  const firstState = !connected;
  const yolo = document.querySelector<HTMLInputElement>('input[name="perception"][value="image-yolo"]')!;
  yolo.disabled = next.vision_installed === false;
  yolo.title = yolo.disabled ? 'YOLO requires the backend vision extra.' : '';
  const lostEdits = dirty && (next.status.toLowerCase() !== 'idle' || (draft?.scene_id && draft.scene_id !== next.scene_id));
  const sensor = next.hazard_sensor;
  const heat = sensor?.source_heat_intensity;
  $('sensor-readings').textContent = sensor
    ? `Robot sensor · Heat ${heat == null ? '—' : number(heat * 100) + '%'} · Distance ${sensor.source_distance_m == null ? '—' : number(sensor.source_distance_m, 3) + ' m'} · Smoke ${number(sensor.smoke_intensity * 100)}%`
    : 'Sensor data unavailable';
  state = next;
  connected = true;
  if (!draft || !dirty || lostEdits) {
    draft = { ...copyScenario(next.scenario), scene_id: next.scene_id };
    dirty = false;
    ($('seed') as HTMLInputElement).value = String(draft.seed);
  }
  if (lostEdits) notify('Local edits were discarded because the backend scene or run status changed.');
  if (next.status.toLowerCase() !== 'idle' && selectedTool !== 'inspect') {
    selectedTool = 'inspect';
    document.querySelector<HTMLInputElement>('input[name="tool"][value="inspect"]')!.checked = true;
  }
  if ((firstState || newSceneSelection || (yolo.disabled && yolo.checked)) || (!dirty && next.status.toLowerCase() !== 'idle')) {
    document.querySelector<HTMLInputElement>(`input[name="mode"][value="${next.mode}"]`)!.checked = true;
    document.querySelector<HTMLInputElement>(`input[name="perception"][value="${yolo.disabled ? 'analytic-development' : next.perception_mode}"]`)!.checked = true;
  }
  $('clock').textContent = `${number(next.time)} s`;
  $('evacuated').textContent = number(next.metrics.evacuated, 0);
  $('population').textContent = `/ ${next.scenario.survivors.length} people`;
  const casualties = next.survivors.filter(person => person.status === 'casualty').length;
  const searched = next.searched_rooms?.length ?? next.metrics.rooms_inspected ?? 0;
  const missionNote = next.status === 'unresolved' ? ' · Unresolved' : next.holding ? ' · Holding' : '';
  $('unresolved-note').textContent = `· ${searched} rooms searched${casualties ? ` · ${casualties} casualties` : ''}${missionNote}`;

  $('exposure').textContent = number(next.metrics.exposure, 2);
  $('distance').textContent = number(next.metrics.distance, 2);
  $('route-note').textContent = `${next.metrics.route_changes ?? 0} changes · ${next.metrics.rooms_inspected ?? 0} rooms inspected` +
    (next.metrics.robot_hazard_s ? ` · ${number(next.metrics.robot_hazard_s, 1)} s robot in fire/smoke` : '') +
    (next.metrics.assistance_requests ? ` · ${next.metrics.assistance_requests} assistance request${next.metrics.assistance_requests > 1 ? 's' : ''}` : '') +
    (next.metrics.inference_pause_s ? ` · clock paused ${number(next.metrics.inference_pause_s, 1)} s for Gemini (not real time)` : '');
  syncBuildingUse();
  syncRoomRisk();
  const image = next.perception_mode === 'image-yolo';
  $('perception-label').textContent = image ? 'YOLO · RGB-D' : 'Analytic fixture';
  $('perception-dot').classList.toggle('warning', !image || next.degraded);
  $('perception-note').textContent = image ? (next.degraded ? 'Degraded: frames missed; no new detections' : `${next.metrics.detections ?? 0} confirmed · ${next.metrics.false_detections ?? 0} retired`) : 'Labelled fixture: driven by simulator truth, not images';
  if (scene) {
    drawDynamic();
    if (selectedCell) inspectCell(selectedCell);
  }
  const signature = JSON.stringify(next.events);
  if (signature !== eventSignature) {
    eventSignature = signature;
    $('events').innerHTML = next.events.length ? next.events.slice(-60).reverse().map(e => `<li><time>${escape(number(e.time))} s</time><span>${escape(e.message)}</span></li>`).join('') : '<li class="empty-line">No mission events yet. Start a run to collect telemetry.</li>';
  }
  renderDetections();
  renderTeam(next);
  renderPaired();
  syncUI();
}

async function poll(): Promise<void> {
  if (replayId || pollInFlight || document.hidden) return;
  pollInFlight = true;
  try {
    const next = await api<unknown>('state', undefined, AbortSignal.timeout(4000));
    if (replayId) return;
    validateState(next);
    const newSceneSelection = !scene || scene.id !== next.scene_id;
    if (newSceneSelection) { state = next; await loadScene(); }
    if (view === 'belief') belief = await api<Observation>('observations', undefined, AbortSignal.timeout(4000));
    if (replayId) return;
    acceptState(next, newSceneSelection);
    rendererOwned=(await api<{owner:boolean}>('renderer',{id:rendererId})).owner;
    if (rendererOwned && next.frame_request && next.frame_request.seq !== lastFrameSeq && !frameInFlight) void sendFrame(next);
  } catch (error) {
    if(replayId)return;
    if (connected || !state) {
      $('scene-empty').querySelector('p')!.textContent = `Cannot load /api/state. Run the backend on port 8000. ${error instanceof Error ? error.message : ''}`;
      if (connected) notify('Connection lost. The map shows the last received state; controls are disabled.', true);
    }
    connected = false;
    syncUI();
  } finally { pollInFlight = false; }
}

function clearGroup(group: THREE.Group): void {
  group.traverse(object => {
    if (object instanceof THREE.InstancedMesh) object.dispose();
    if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach(material => {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
        material.dispose();
      });
    }
  });
  group.clear();
}

function cellWorld(cell: Cell): [number, number] {
  return [scene!.origin[0] + (cell[0] + 0.5) * scene!.resolution, scene!.origin[1] + (cell[1] + 0.5) * scene!.resolution];
}
/** Model frame (x, y, z up) -> three.js (x, z, -y): the scene bundle's declared viewer transform. */
function toThree(x: number, y: number, z = 0): THREE.Vector3 { return new THREE.Vector3(x, z, -y); }
function position(cell: Cell, altitude = 0): THREE.Vector3 { const [x, y] = cellWorld(cell); return toThree(x, y, altitude); }
function operatorOnly<T extends THREE.Object3D>(object: T): T { object.traverse(o => o.layers.set(OPERATOR_LAYER)); return object; }

function cellBox(group: THREE.Group, cell: Cell, size: number, height: number, color: THREE.ColorRepresentation, altitude: number, opacity = 1): THREE.Mesh {
  const r = scene!.resolution;
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(size * r, height, size * r), new THREE.MeshStandardMaterial({ color, roughness: 0.7, transparent: opacity < 1, opacity, depthWrite: opacity === 1 }));
  mesh.position.copy(position(cell, altitude));
  group.add(mesh);
  return mesh;
}

function wallHeightOf(): number { return scene?.mesh?.wall_height ?? 1.15; }

/** Walls toggle hides walls from the operator only; the robot camera always sees them. */
function applyWallLayer(): void {
  const shown = ($('walls-layer') as HTMLInputElement).checked;
  wallLayer.traverse(o => o.layers.set(shown ? 0 : CAMERA_ONLY_LAYER));
}

function disposeObject(object: THREE.Object3D): void {
  object.traverse(o => {
    if (!(o instanceof THREE.Mesh)) return;
    o.geometry.dispose();
    for (const material of Array.isArray(o.material) ? o.material : [o.material]) {
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
      material.dispose();
    }
  });
}

async function loadSceneMesh(current: SceneData, version: number): Promise<void> {
  const mesh = current.mesh!;
  const base = replayId ? `/api/runs/${replayId}/artifacts/` : `/api/scenes/${encodeURIComponent(current.id)}/files/`;
  let failed = false, mounted = false, complete = false;
  const manager = new THREE.LoadingManager();
  const ready = () => { if (version === sceneLoadVersion && mounted && complete && !failed) { sceneReady = true; syncUI(); } };
  manager.onLoad = () => { complete = true; ready(); };
  manager.onError = () => { failed = true; if (version === sceneLoadVersion) { sceneReady = false; notify('A scene asset failed to load. Mission start is disabled.', true); syncUI(); } };
  try {
    let object: THREE.Group;
    if (mesh.format === 'obj') {
      const loader = new OBJLoader(manager);
      if (mesh.materials?.length) {
        const texts = await Promise.all(mesh.materials.map(async name => {
          const response = await fetch(base + encodeURIComponent(name));
          if (!response.ok) throw new Error('Material unavailable');
          return response.text();
        }));
        const materials = new MTLLoader(manager).parse(texts.join('\n'), base);
        materials.preload(); loader.setMaterials(materials);
      }
      object = await loader.loadAsync(base + encodeURIComponent(mesh.file));
      // Untextured scans (e.g. the defurnished 50k OBJ) ship without normals: compute them and shade
      // flat in a neutral plaster tone so walls, floor and fixtures read as 3D instead of flat white.
      if (!mesh.textured) object.traverse(o => {
        if (!(o instanceof THREE.Mesh)) return;
        o.geometry.computeVertexNormals();
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
        o.material = new THREE.MeshLambertMaterial({ color: '#d9d2c6', flatShading: true, side: THREE.DoubleSide });
      });
      // Scanned textures already contain the room's lighting; render them unlit so they are not lit twice.
      if (mesh.textured) object.traverse(o => {
        if (!(o instanceof THREE.Mesh)) return;
        const swap = (m: THREE.Material) => {
          const map = (m as THREE.MeshPhongMaterial).map ?? null;
          if (map) map.colorSpace = THREE.SRGBColorSpace;
          const basic = new THREE.MeshBasicMaterial({ map, side: THREE.DoubleSide, name: m.name });
          m.dispose(); return basic;
        };
        o.material = Array.isArray(o.material) ? o.material.map(swap) : swap(o.material);
      });
      const registration = new THREE.Matrix4();
      if (mesh.transform) registration.set(...mesh.transform.flat() as [number,number,number,number,number,number,number,number,number,number,number,number,number,number,number,number]);
      const viewer = new THREE.Matrix4().set(1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1);
      object.applyMatrix4(viewer.multiply(registration));
    } else object = (await new GLTFLoader(manager).loadAsync(base + encodeURIComponent(mesh.file))).scene;
    if (version !== sceneLoadVersion) { disposeObject(object); return; }
    // Clip only the scan mesh (per-material), so smoke and flames near the ceiling stay visible in the cutaway.
    if (mesh.format === 'obj') object.traverse(o => {
      if (o instanceof THREE.Mesh) for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.clippingPlanes = [sourceCutaway];
    });
    wallLayer.add(object); applyWallLayer(); mounted = true; ready();
  } catch {
    if (version === sceneLoadVersion) { sceneReady = false; notify('Scene geometry or materials unavailable. Mission start is disabled.', true); syncUI(); }
  }
}

function buildStaticScene(): void {
  const version = ++sceneLoadVersion;
  sceneReady = false;
  lastFrameImage = null;
  drawFeed(null);
  $('camera-status').textContent = 'Waiting for this scene’s camera frame';
  clearGroup(staticLayer);
  clearGroup(wallLayer);
  gridWallMesh = undefined;
  gridWallIndices = new Map();
  shownBreach = '';
  avatars.forEach(a => avatarLayer.remove(a));
  avatars.clear();
  if (!scene) return;
  const { width, height, resolution: res, origin } = scene;
  const real = !!scene.mesh;
  const sourceMesh = scene.mesh?.format === 'obj';
  if (!real) sceneReady = true;
  const wallHeight = wallHeightOf();
  const gridW = width * res, gridH = height * res;
  const cx = origin[0] + gridW / 2, cy = origin[1] + gridH / 2;
  // Floor: scanned floor-plan image in its own georeference, else a plain slab.
  const fi = scene.floor_image;
  if (fi) {
    const w = fi.width / fi.pixels_per_metre, h = fi.height / fi.pixels_per_metre;
    const texture = new THREE.TextureLoader().load(replayId ? `/api/runs/${replayId}/artifacts/${fi.file}` : `/api/scenes/${encodeURIComponent(scene.id)}/files/${fi.file}`);
    texture.colorSpace = THREE.SRGBColorSpace;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map: texture, transparent: true, roughness: 0.95 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.copy(toThree(fi.origin[0] + w / 2, fi.origin[1] + h / 2, 0));
    staticLayer.add(floor);
    const base = new THREE.Mesh(new THREE.PlaneGeometry(gridW, gridH), new THREE.MeshStandardMaterial({ color: '#a89f92', roughness: 1 }));
    base.rotation.x = -Math.PI / 2;
    base.position.copy(toThree(cx, cy, -0.01));
    base.layers.set(CAMERA_ONLY_LAYER);
    staticLayer.add(base);
  } else if (!sourceMesh) {
    const floor = new THREE.Mesh(new THREE.BoxGeometry(gridW, 0.16, gridH), new THREE.MeshStandardMaterial({ color: '#182633', roughness: 1 }));
    floor.position.copy(toThree(cx, cy, -0.08));
    staticLayer.add(floor);
  }
  if (real) {
    void loadSceneMesh(scene, version);
    // Ceiling for the robot camera only, so the operator keeps the cutaway view.
    if (!sourceMesh) {
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(gridW, gridH), new THREE.MeshStandardMaterial({ color: '#eceae6', emissive: '#bdbab4', emissiveIntensity: 0.55, side: THREE.DoubleSide }));
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.copy(toThree(cx, cy, wallHeight));
    ceiling.layers.set(CAMERA_ONLY_LAYER);
    staticLayer.add(ceiling);
    }
    for (const room of scene.rooms) {
      if (!room.polygon) continue;
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(room.polygon.map(([x, y]) => toThree(x, y, 0.02))), new THREE.LineBasicMaterial({ color: '#4fc3a1', transparent: true, opacity: 0.55 }));
      staticLayer.add(operatorOnly(line));
    }
  } else {
    // Upstream: one instanced box per wall cell that borders open floor.
    const blockedSet = new Set(scene.walls.map(key));
    const exposed = scene.walls.filter(([x, y]) => [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]].some(([c, r]) => c >= 0 && c < width && r >= 0 && r < height && !blockedSet.has(key([c, r]))));
    if (exposed.length) {
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(res, wallHeight, res), new THREE.MeshStandardMaterial({ color: '#acb4b8', roughness: 0.85 }), exposed.length);
      const matrix = new THREE.Matrix4();
      exposed.forEach((cell, i) => {
        gridWallIndices.set(key(cell), i);
        mesh.setMatrixAt(i, matrix.makeTranslation(position(cell, wallHeight / 2)));
      });
      gridWallMesh = mesh;
      wallLayer.add(mesh);
    }
    applyWallLayer();
    scene.rooms.forEach(room => (room.observe ?? room.cells).forEach(cell => operatorOnly(cellBox(staticLayer, cell, 0.98, 0.025, '#2b3946', 0.01))));
  }
  scene.exits.forEach(cell => {
    operatorOnly(cellBox(staticLayer, cell, 1.6, 0.05, '#2bcba1', 0.03));
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), position(cell, 0.1), 1.2, '#d0fff0', 0.3, 0.2);
    staticLayer.add(operatorOnly(arrow));
  });
  for (const field of [heatField, smokeField]) {
    field.canvas.width = width * 4;
    field.canvas.height = height * 4;
    field.mesh.scale.set(gridW, gridH, 1);
  }
  heatField.mesh.position.copy(toThree(cx, cy, 0.03));
  smokeField.mesh.position.copy(toThree(cx, cy, wallHeight * 0.9));
  blockedCells = new Set([...scene.walls, ...scene.exterior].map(key));
  clearGroup(obstacleLayer);
  const overlay = scene.obstacle_overlay ?? {};
  const kinds: [Cell[] | undefined, string][] = [[overlay.furniture, '#f0a640'], [overlay.source_walls, '#7f8fa3']];
  for (const [cells, color] of kinds) {
    if (!cells?.length) continue;
    const marks = new THREE.InstancedMesh(new THREE.BoxGeometry(res * 0.9, 0.04, res * 0.9), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.7, depthWrite: false }), cells.length);
    const matrix = new THREE.Matrix4();
    cells.forEach((cell, i) => marks.setMatrixAt(i, matrix.makeTranslation(position(cell, 0.05))));
    obstacleLayer.add(marks);
  }
  operatorOnly(obstacleLayer);
  obstacleLayer.visible = ($('furniture-layer') as HTMLInputElement).checked;
  $('furniture-control').hidden = !(overlay.furniture?.length || overlay.source_walls?.length);
  $('cutaway-control').hidden = !sourceMesh;
}

/** Authored appearance stays in operator scenario state, outside agent observations. */
void loadHumans(() => {
  avatars.forEach(a => avatarLayer.remove(a)); avatars.clear();
  if (state) drawDynamic();
  syncUI();
}, message => notify(message, true));

function makeHuman(id: string): THREE.Group | null {
  return createHuman(id, state?.scenario.human_variant);
}
let renderedAppearance: HumanVariant | undefined;

function hashAngle(text: string): number { let h = 0; for (const c of text) h = (h * 31 + c.charCodeAt(0)) >>> 0; return (h % 360) * Math.PI / 180; }

/** Procedural standing person (~1.72 m) built from primitives; a simulated survivor, not a real person. */
function makeAvatar(id: string): THREE.Group {
  const palette = ['#c0392b', '#2e86c1', '#27ae60', '#8e44ad', '#d35400', '#16a085', '#b7950b', '#5d6d7e'];
  const index = Number(id.replace(/\D/g, '')) || 0;
  const shirt = new THREE.MeshStandardMaterial({ color: palette[index % palette.length], roughness: 0.8 });
  const trousers = new THREE.MeshStandardMaterial({ color: index % 2 ? '#2c3e50' : '#3b3b3b', roughness: 0.9 });
  const skin = new THREE.MeshStandardMaterial({ color: ['#e0ac69', '#c68642', '#8d5524', '#f1c27d'][index % 4], roughness: 0.7 });
  const hair = new THREE.MeshStandardMaterial({ color: '#2b1d14', roughness: 0.9 });
  const shoes = new THREE.MeshStandardMaterial({ color: '#1b1b1b' });
  const group = new THREE.Group();
  const part = (geometry: THREE.BufferGeometry, material: THREE.Material, x: number, y: number, z = 0, rz = 0): void => {
    const mesh = new THREE.Mesh(geometry, material); mesh.position.set(x, y, z); mesh.rotation.z = rz; group.add(mesh);
  };
  for (const side of [-1, 1]) {
    part(new THREE.CapsuleGeometry(0.075, 0.72, 4, 10), trousers, side * 0.1, 0.47);
    part(new THREE.BoxGeometry(0.12, 0.07, 0.24), shoes, side * 0.1, 0.035, 0.04);
    part(new THREE.CapsuleGeometry(0.055, 0.52, 4, 10), shirt, side * 0.255, 1.12, 0, side * 0.12);
    part(new THREE.SphereGeometry(0.055, 10, 8), skin, side * 0.29, 0.8);
  }
  part(new THREE.CapsuleGeometry(0.17, 0.42, 6, 14), shirt, 0, 1.17);
  part(new THREE.CylinderGeometry(0.05, 0.06, 0.1, 10), skin, 0, 1.5);
  part(new THREE.SphereGeometry(0.11, 16, 12), skin, 0, 1.62);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.115, 16, 12, 0, Math.PI * 2, 0, Math.PI / 2), hair); cap.position.set(0, 1.64, -0.01); group.add(cap);
  group.scale.setScalar(0.98 + (index % 3) * 0.03);
  group.rotation.y = hashAngle(id);
  group.userData.id = id;
  return group;
}

function drawHazardField(): void {
  if (!scene || !state) return;
  const useBelief = view === 'belief' && !!belief;
  const heatGrid = useBelief ? belief!.heat : state.heat;
  const smokeGrid = useBelief ? belief!.smoke : state.smoke;
  const observedGrid = useBelief ? belief!.observed : state.observed;
  const { width, height, resolution: res } = scene;
  const heatPixels = heatField.context.createImageData(width * 4, height * 4);
  const smokePixels = smokeField.context.createImageData(width * 4, height * 4);
  let burning = 0, smoky = 0, flameCount = 0, hottest = 0;
  const denseSmoke: { cell: Cell; density: number }[] = [];
  focusCell = null;
  const smokeBand = (value: number) => value >= 0.5 ? 3 : value >= 0.2 ? 2 : value >= 0.015 ? 1 : 0;
  const smokeColors = [[0, 0, 0, 0], [212, 221, 229, 130], [173, 186, 201, 180], [129, 144, 161, 225]];
  const breached = new Set(state.incident?.breached?.map(key));
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const cellKey = key([col, row]);
      if (blockedCells.has(cellKey) && !breached.has(cellKey)) continue;
      const heat = heatGrid[row]?.[col] ?? 0;
      const density = smokeGrid[row]?.[col] ?? 0;
      const smoke = smokeBand(density);
      if (layers.smoke && density >= 0.06) denseSmoke.push({ cell: [col, row], density });
      if (smoke) smoky++;
      const fire = useBelief ? heat : state.fire?.[row]?.[col] ?? heat;
      if (fire >= (useBelief || !state.fire ? FLAME_HEAT : .12)) {
        burning++;
        if (fire > hottest) { hottest = fire; focusCell = [col, row]; }
        if (flameCount < flameLimit) {
          const flameHeight = (0.75 + Math.min(fire, 1) * 1.6) * 0.5;   // upstream proportions at 0.5 m cells
          flameTransform.position.copy(position([col, row], flameHeight / 2));
          flameTransform.scale.set(Math.max(0.26, 1.1 * res), flameHeight, 1);
          flameTransform.updateMatrix();
          flames.setMatrixAt(flameCount++, flameTransform.matrix);
        }
      }
      const band = layers.smoke ? smoke : 0;
      let color = smokeColors[0];
      if (layers.observed && !observedGrid[row]?.[col] && !band) color = [7, 11, 17, 155];
      if (layers.heat && heat >= 0.04) {
        heatColor.set('#f7ba57').lerp(hotColor, THREE.MathUtils.clamp(heat, 0, 1));
        const hex = heatColor.getHex();
        color = [hex >> 16, (hex >> 8) & 255, hex & 255, Math.max(color[3], 90 + 150 * heat)];
      }
      // Texture row 0 is the top of the plane = max model y, so the texture row above a cell is row + 1.
      const left = band >= 1 && smokeBand(smokeGrid[row]?.[col - 1] ?? 0) < band;
      const right = band >= 1 && smokeBand(smokeGrid[row]?.[col + 1] ?? 0) < band;
      const top = band >= 1 && smokeBand(smokeGrid[row + 1]?.[col] ?? 0) < band;
      const bottom = band >= 1 && smokeBand(smokeGrid[row - 1]?.[col] ?? 0) < band;
      if (!color[3] && !band) continue;
      const texRow = height - 1 - row;
      for (let y = 0; y < 4; y++) {
        for (let x = 0; x < 4; x++) {
          const edge = (left && x === 0) || (right && x === 3) || (top && y === 0) || (bottom && y === 3);
          const offset = ((texRow * 4 + y) * heatPixels.width + col * 4 + x) * 4;
          if (color[3]) heatPixels.data.set(color, offset);
          if (band) smokePixels.data.set(edge ? [236, 242, 248, 225] : smokeColors[band], offset);
        }
      }
    }
  }
  const stride = Math.max(1, Math.ceil(denseSmoke.length / smokeLimit));
  let cloudCount = 0;
  for (let i = 0; i < denseSmoke.length; i += stride) {
    const { cell, density } = denseSmoke[i];
    const spread = (0.48 + Math.min(1, density) * 0.42) * 0.5 * Math.sqrt(stride);
    smokeTransform.position.copy(position(cell, smokeField.mesh.position.y - 0.225));
    smokeTransform.scale.set(spread * 2.4, spread * 1.3, 1);
    smokeTransform.updateMatrix();
    smokeClouds.setMatrixAt(cloudCount++, smokeTransform.matrix);
  }
  smokeClouds.count = cloudCount;
  smokeClouds.instanceMatrix.needsUpdate = true;
  flames.count = flameCount;
  flames.instanceMatrix.needsUpdate = true;
  ($('focus-fire') as HTMLButtonElement).disabled = focusCell === null;
  const area = res * res;
  $('hazard-readout').textContent = `${useBelief ? 'Robot belief · ' : ''}${number(state.hazard_time ?? state.time)} s hazards · ${number(burning * area)} m² fire · ${number(smoky * area)} m² smoke`;
  heatField.context.putImageData(heatPixels, 0, 0);
  smokeField.context.putImageData(smokePixels, 0, 0);
  heatField.texture.needsUpdate = true;
  smokeField.texture.needsUpdate = true;
}

/** Truth-only aids: occluded body silhouette and a readable location label.
 * Never rendered into RGB-D or exposed in the belief view. */
function survivorOverlay(avatar: THREE.Group, id: string): THREE.Group {
  const overlay = new THREE.Group();
  overlay.name = 'survivor-operator-overlay';
  const silhouette = SkeletonUtils.clone(avatar);
  silhouette.traverse(o => {
    if (!(o instanceof THREE.Mesh)) return;
    o.material = new THREE.MeshBasicMaterial({ color: '#58c4ff', transparent: true, opacity: 0.65,
      depthFunc: THREE.GreaterDepth, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
    o.renderOrder = 20;
  });
  overlay.add(silhouette);
  const canvas = document.createElement('canvas');
  canvas.width = 320; canvas.height = 64;
  const context = canvas.getContext('2d')!;
  context.fillStyle = '#102938'; context.fillRect(0, 0, 320, 64);
  context.strokeStyle = '#58c4ff'; context.lineWidth = 4; context.strokeRect(2, 2, 316, 60);
  context.fillStyle = '#e9f7ff'; context.font = 'bold 26px sans-serif'; context.textAlign = 'center';
  context.fillText(`Survivor ${(Number(id.replace(/\D/g, '')) || 0) + 1} · simulated`, 160, 42);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, depthWrite: false, sizeAttenuation: false }));
  label.position.y = 2.05; label.scale.set(0.12, 0.024, 1); label.renderOrder = 21;
  overlay.add(label);
  return operatorOnly(overlay);
}

let focusedSurvivor = 0;
const casualtyMaterial = new THREE.MeshBasicMaterial({ color: '#ed3445', side: THREE.DoubleSide });

function placeAvatars(): void {
  const appearance = state?.scenario.human_variant ?? 'adult-male';
  if (appearance !== renderedAppearance) {
    avatars.forEach(a => avatarLayer.remove(a)); avatars.clear(); renderedAppearance = appearance;
  }
  if (!scene || !state) return;
  const editing = state.status.toLowerCase() === 'idle';
  const people: SimState['survivors'] = editing && draft
    ? draft.survivors.map((p, i) => ({ id: `person-${i}`, position: p, status: 'unobserved' }))
    : state.survivors;
  const keep = new Set<string>();
  for (const person of people) {
    if (person.status === 'evacuated') continue;
    keep.add(person.id);
    let avatar = avatars.get(person.id);
    if (!avatar) {
      avatar = makeHuman(person.id) ?? makeAvatar(person.id);
      avatar.add(survivorOverlay(avatar, person.id));
      avatars.set(person.id, avatar); avatarLayer.add(avatar);
    }
    const [x, y] = cellWorld(person.position);
    if (person.status === 'escorting') {
      // Follower stands behind the robot along its heading.
      avatar.position.copy(toThree(x - Math.cos(state.heading) * 0.55, y - Math.sin(state.heading) * 0.55, 0));
      avatar.rotation.y = state.heading - Math.PI / 2;
    } else {
      avatar.position.copy(toThree(x, y, 0));
      avatar.rotation.y = hashAngle(person.id);
    }
    if ((person.status === 'casualty' || person.injured) && view !== 'belief') {
      const material = casualtyMaterial.clone();
      if (person.status !== 'casualty') material.color.set('#fbb946');
      const marker = new THREE.Mesh(new THREE.RingGeometry(.18, .25, 24), material);
      marker.rotation.x = -Math.PI / 2;
      marker.position.copy(toThree(x, y, .035));
      dynamicLayer.add(operatorOnly(marker));
    }
    // Operator view hides people in robot-belief mode; the rendered camera always sees them.
    avatar.traverse(o => { o.layers.set(0); if (view === 'belief') o.layers.disable(0), o.layers.enable(CAMERA_ONLY_LAYER); });
    const overlay = avatar.getObjectByName('survivor-operator-overlay')!;
    overlay.visible = view === 'truth';
    operatorOnly(overlay);
  }
  for (const [id, avatar] of avatars) if (!keep.has(id)) { avatarLayer.remove(avatar); avatars.delete(id); }
  ($('focus-survivor') as HTMLButtonElement).disabled = view !== 'truth' || avatars.size === 0;
}

function drawDynamic(): void {
  clearGroup(dynamicLayer);
  if (!state || !scene) return;
  const breach = state.incident?.breached ?? [];
  const signature = breach.map(key).join('|');
  if (gridWallMesh && signature !== shownBreach) {
    const removed = new Set(breach.map(key));
    const matrix = new THREE.Matrix4();
    for (const cell of scene.walls) {
      const index = gridWallIndices.get(key(cell));
      if (index === undefined) continue;
      if (removed.has(key(cell))) matrix.makeScale(0, 0, 0);
      else matrix.makeTranslation(position(cell, wallHeightOf() / 2));
      gridWallMesh.setMatrixAt(index, matrix);
    }
    gridWallMesh.instanceMatrix.needsUpdate = true;
    shownBreach = signature;
  }
  drawHazardField();
  placeAvatars();
  for (const cell of breach) operatorOnly(cellBox(dynamicLayer, cell, .9, .025, '#c68948', .04));
  if (state.incident?.debris) {
    const debris = new THREE.Mesh(new THREE.BoxGeometry(scene.resolution * .85, .38, scene.resolution * .85),
      new THREE.MeshStandardMaterial({ color: '#78594a', roughness: .95 }));
    debris.position.copy(position(state.incident.debris, .19));
    debris.rotation.y = .35;
    dynamicLayer.add(debris);
  }
  if (!state.incident) {
    shownIncident = '';
    incidentEffect.visible = false;
  } else {
    const incidentKey = `${state.scene_id}:${state.incident.time}:${key(state.incident.origin)}`;
    if (shownIncident !== incidentKey) {
      shownIncident = incidentKey;
      incidentStarted = performance.now();
      incidentEffect.position.copy(position(state.incident.origin, 1));
      incidentEffect.visible = state.time - state.incident.time <= 1.5;
    }
  }
  if (layers.route && state.route.length > 0) {
    const material = new THREE.MeshBasicMaterial({ color: '#55e3c2' });
    const points = [state.robot, ...state.route];
    for (let i = 1; i < points.length; i++) {
      const a = position(points[i - 1], 0.12), b = position(points[i], 0.12);
      const delta = b.clone().sub(a);
      if (delta.length() === 0) continue;
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, delta.length(), 5), material.clone());
      tube.position.copy(a.add(b).multiplyScalar(0.5));
      tube.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
      dynamicLayer.add(operatorOnly(tube));
    }
    material.dispose();
  }
  const editing = state.status.toLowerCase() === 'idle';
  (editing ? draft?.ignitions ?? state.scenario.ignitions : []).forEach(cell => {
    const marker = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.5, 6), new THREE.MeshStandardMaterial({ color: '#ff9a46', emissive: '#da3d13', emissiveIntensity: 0.7 }));
    marker.position.copy(position(cell, 0.25));
    dynamicLayer.add(operatorOnly(marker));
  });
  if (!editing) {
    for (const d of state.detections) {
      if (!['candidate', 'confirmed', 'escorting'].includes(d.status)) continue;
      const pin = new THREE.Mesh(new THREE.TorusGeometry(0.32, d.status === 'candidate' ? 0.02 : 0.045, 6, 24), new THREE.MeshBasicMaterial({ color: d.status === 'candidate' ? '#a3b5c9' : '#59d7ff' }));
      pin.rotation.x = -Math.PI / 2;
      pin.position.copy(toThree(d.position[0], d.position[1], 0.08));
      dynamicLayer.add(operatorOnly(pin));
    }
  }
  const robot = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.3, 0.45), new THREE.MeshStandardMaterial({ color: '#5ce0ca' }));
  body.position.y = 0.25;
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.7, 8), new THREE.MeshStandardMaterial({ color: '#d9fff7' }));
  mast.position.set(0.15, 0.7, 0);
  const lens = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.08, 0.14), new THREE.MeshStandardMaterial({ color: '#10202c' }));
  lens.position.set(0.22, 1.0, 0);
  robot.add(body, mast, lens);
  robot.position.copy(position(state.robot, 0));
  robot.rotation.y = state.heading;
  dynamicLayer.add(operatorOnly(robot));
  const sprinklers = state.sprinklers ?? [];
  if (sprinklers.length) {
    const height = wallHeightOf() - 0.1;
    const heads = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.09, 0.12, 0.08, 12),
      new THREE.MeshBasicMaterial({ color: '#55ceff' }), sprinklers.length,
    );
    const matrix = new THREE.Matrix4();
    sprinklers.forEach(({ origin }, index) => heads.setMatrixAt(index, matrix.makeTranslation(position(origin, height))));
    dynamicLayer.add(operatorOnly(heads));
  }
  for (const sprinkler of sprinklers) {
    if (!sprinkler.cells.length) continue;
    const nozzle = position(sprinkler.origin, wallHeightOf() - 0.15);
    const vertices: number[] = [];
    const stride = Math.max(1, Math.ceil(sprinkler.cells.length / 32));
    for (const [index, cell] of sprinkler.cells.entries()) {
      if (index % stride !== 0) continue;
      const ground = position(cell, 0.06);
      const arc = nozzle.clone().lerp(ground, 0.55);
      arc.y += 0.25;
      vertices.push(...nozzle.toArray(), ...arc.toArray(), ...arc.toArray(), ...ground.toArray());
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    const material = new THREE.LineBasicMaterial({ color: '#55ceff', transparent: true, opacity: 0.65 });
    dynamicLayer.add(operatorOnly(new THREE.LineSegments(geometry, material)));
  }
  if (selectedCell && inBounds(selectedCell)) {
    const outline = new THREE.Mesh(new THREE.RingGeometry(0.39 * scene.resolution * 2, 0.46 * scene.resolution * 2, 4), new THREE.MeshBasicMaterial({ color: '#ffffff', side: THREE.DoubleSide, depthTest: false }));
    outline.rotation.set(-Math.PI / 2, 0, Math.PI / 4);
    outline.position.copy(position(selectedCell, 0.3));
    outline.renderOrder = 10;
    dynamicLayer.add(operatorOnly(outline));
  }
}

/** Render RGB and linear depth from the requested camera pose and send them for YOLO inference. */
async function sendFrame(snapshot: SimState): Promise<void> {
  const request = snapshot.frame_request;
  if (replayId || !renderer || !scene || !request || !sceneReady) return;
  const frameSceneVersion = sceneLoadVersion;
  frameInFlight = true;
  lastFrameSeq = request.seq;
  try {
    const pose = request.pose;
    robotCamera.position.copy(toThree(pose.x, pose.y, pose.z));
    robotCamera.lookAt(toThree(pose.x + Math.cos(pose.yaw), pose.y + Math.sin(pose.yaw), pose.z));
    robotCamera.updateMatrixWorld();
    const smokeHere = snapshot.smoke[snapshot.robot[1]]?.[snapshot.robot[0]] ?? 0;
    const previousBackground = world.background;
    // Smoke attenuates the rendered image (not depth); simple exponential fog at the robot's local density.
    world.fog = smokeHere > 0.02 ? new THREE.FogExp2(0x8d9299, 0.15 + smokeHere * 1.6) : null;
    world.background = new THREE.Color('#e9e7e3');
    sourceCutaway.constant = NO_CUT;   // the robot camera always sees the complete geometry
    renderer.setRenderTarget(rgbTarget);
    renderer.render(world, robotCamera);
    const rgba = new Uint8Array(CAM_W * CAM_H * 4);
    renderer.readRenderTargetPixels(rgbTarget, 0, 0, CAM_W, CAM_H, rgba);
    world.fog = null;
    world.background = null;
    world.overrideMaterial = depthMaterial;
    const clear = renderer.getClearColor(new THREE.Color()); const clearAlpha = renderer.getClearAlpha();
    renderer.setClearColor(0x000000, 0);
    renderer.setRenderTarget(depthTarget);
    renderer.render(world, robotCamera);
    const depthRGBA = new Float32Array(CAM_W * CAM_H * 4);
    renderer.readRenderTargetPixels(depthTarget, 0, 0, CAM_W, CAM_H, depthRGBA);
    world.overrideMaterial = null;
    renderer.setRenderTarget(null);
    renderer.setClearColor(clear, clearAlpha);
    world.background = previousBackground;
    // GL rows are bottom-up; the frame contract is row 0 = top.
    const image = new ImageData(CAM_W, CAM_H);
    const depth = new Float32Array(CAM_W * CAM_H);
    for (let row = 0; row < CAM_H; row++) {
      const src = (CAM_H - 1 - row) * CAM_W;
      image.data.set(rgba.subarray(src * 4, (src + CAM_W) * 4), row * CAM_W * 4);
      for (let col = 0; col < CAM_W; col++) depth[row * CAM_W + col] = depthRGBA[(src + col) * 4];
    }
    const canvas = document.createElement('canvas'); canvas.width = CAM_W; canvas.height = CAM_H;
    canvas.getContext('2d')!.putImageData(image, 0, 0);
    const rgb = canvas.toDataURL('image/jpeg', 0.92);
    const bytes = new Uint8Array(depth.buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    lastFrameImage = image;
    const result = await api<FrameResult>('frames', { renderer_id: rendererId, seq: request.seq, width: CAM_W, height: CAM_H, rgb, depth: btoa(binary), intrinsics: [FX, FY, CAM_W / 2, CAM_H / 2], pose }, AbortSignal.timeout(20000));
    if (replayId || frameSceneVersion !== sceneLoadVersion) return;
    drawFeed(result);
    $('camera-status').textContent = `Frame ${result.seq} · t=${number(result.time)} s · ${result.accepted.length} person${result.accepted.length === 1 ? '' : 's'}`;
    void poll();
  } catch (error) {
    if (replayId || frameSceneVersion !== sceneLoadVersion) return;
    $('camera-status').textContent = `Frame not accepted: ${error instanceof Error ? error.message.slice(0, 120) : 'error'}`;
  } finally {
    frameInFlight = false;
  }
}

function drawFeed(result: FrameResult | null): void {
  const canvas = $('robot-feed') as HTMLCanvasElement;
  const ctx = canvas.getContext('2d')!;
  if (lastFrameImage) ctx.putImageData(lastFrameImage, 0, 0);
  else ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!result) return;
  ctx.lineWidth = 2;
  ctx.font = '12px DM Sans, sans-serif';
  for (const d of result.rejected) {
    ctx.strokeStyle = '#9aa5b1'; ctx.setLineDash([5, 4]);
    ctx.strokeRect(d.box[0], d.box[1], d.box[2] - d.box[0], d.box[3] - d.box[1]);
  }
  ctx.setLineDash([]);
  for (const d of result.accepted) {
    ctx.strokeStyle = '#59d7ff';
    ctx.strokeRect(d.box[0], d.box[1], d.box[2] - d.box[0], d.box[3] - d.box[1]);
    const label = `${d.detection ?? 'person'} ${(d.confidence * 100).toFixed(0)}%`;
    ctx.fillStyle = '#59d7ff'; ctx.fillRect(d.box[0], Math.max(0, d.box[1] - 16), ctx.measureText(label).width + 8, 16);
    ctx.fillStyle = '#07202b'; ctx.fillText(label, d.box[0] + 4, Math.max(12, d.box[1] - 4));
  }
}

function renderDetections(): void {
  if (!state) return;
  const image = state.perception_mode === 'image-yolo';
  $('camera-explainer').hidden = !image;
  if (!image) $('camera-status').textContent = 'Analytic fixture mode: no camera frames';
  const items = state.detections.slice().reverse();
  $('detections').innerHTML = items.length ? items.map(d => `<li><time>${escape(d.id)}</time><span>${escape(d.status)} · ${d.hits} obs · ${(d.confidence * 100).toFixed(0)}% · (${number(d.position[0], 2)}, ${number(d.position[1], 2)}) m · ${escape(d.source)}</span></li>`).join('') : '<li class="empty-line">No detections yet. Unobserved rooms are not confirmed empty.</li>';
}

function renderPaired(): void {
  const paired = state?.paired;
  if (!paired) return;
  const signature = `${paired.status}:${Object.keys(paired.reports).join(',')}:${paired.running}`;
  if (signature === shownPairedStatus) return;
  shownPairedStatus = signature;
  $('comparison').hidden = false;
  $('comparison-context').textContent = `Paired live runs on ${scene?.name ?? ''}, seed ${paired.seed}, ${paired.perception}. ${paired.status === 'running' ? `Running ${paired.running}…` : paired.status}.`;
  $('comparison-body').innerHTML = renderReport({ reports: paired.reports, experiment: paired.experiment, note: paired.note });
}

function recenter(): void {
  if (!camera || !controls || !scene) return;
  const direction = cameraView === 'plan' ? new THREE.Vector3(0, 1, 0.0001) : new THREE.Vector3(0.72, 0.8, 1);
  direction.normalize();
  const w = scene.width * scene.resolution, h = scene.height * scene.resolution;
  const x0 = scene.origin[0], y0 = scene.origin[1];
  const target = toThree(x0 + w / 2, y0 + h / 2, 0.3);
  camera.position.copy(target).add(direction);
  camera.lookAt(target);
  const inverse = camera.quaternion.clone().invert();
  const slopeY = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 0.88;
  const slopeX = slopeY * Math.max(camera.aspect, 0.1);
  let distance = 2;
  for (const x of [x0, x0 + w]) for (const y of [y0, y0 + h]) for (const z of [0, wallHeightOf()]) {
    const corner = toThree(x, y, z).sub(target).applyQuaternion(inverse);
    distance = Math.max(distance, corner.z + Math.max(Math.abs(corner.x) / slopeX, Math.abs(corner.y) / slopeY));
  }
  camera.position.copy(target).addScaledVector(direction, distance);
  camera.near = 0.05;
  camera.far = Math.max(1000, distance * 10);
  camera.updateProjectionMatrix();
  controls.target.copy(target);
  controls.maxDistance = distance * 4;
  controls.minDistance = 0.8;
  controls.update();
}

function setView(next: typeof cameraView): void {
  cameraView = next;
  smokeClouds.visible = next === 'orbit';
  smokeField.mesh.visible = next === 'plan';
  if (controls) {
    controls.enableRotate = next === 'orbit';
    controls.mouseButtons.LEFT = next === 'orbit' ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.touches.ONE = next === 'orbit' ? THREE.TOUCH.ROTATE : THREE.TOUCH.PAN;
  }
  $('orbit').setAttribute('aria-pressed', String(next === 'orbit'));
  $('plan').setAttribute('aria-pressed', String(next === 'plan'));
  $('map-help').textContent = next === 'plan' ? 'Drag to pan · Scroll to zoom' : 'Drag to orbit · Scroll to zoom · Right-drag to pan';
  recenter();
}

function setupRenderer(): void {
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.localClippingEnabled = true;   // scan-mesh cutaway is per material
    const canvas = renderer.domElement;
    canvas.setAttribute('aria-label', 'Scene: walls, robot, simulated survivors, hazards and planned route. Use the cell inspector below for keyboard access.');
    canvas.setAttribute('role', 'img');
    $('viewport').prepend(canvas);
    camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    camera.layers.enable(OPERATOR_LAYER);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI / 2.2;
    const resize = () => {
      const { width, height } = $('viewport').getBoundingClientRect();
      renderer!.setSize(width, height);
      camera!.aspect = width / Math.max(height, 1);
      camera!.updateProjectionMatrix();
    };
    resize();
    new ResizeObserver(resize).observe($('viewport'));
    setView(cameraView);
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    renderer.setAnimationLoop((now) => {
      const clock = reducedMotion.matches ? 0 : now * .001;
      flames.material.uniforms.clock.value = clock;
      smokeClouds.material.uniforms.clock.value = clock;
      if (document.hidden || !renderer || !camera) return;
      controls?.update();
      if (incidentEffect.visible) {
        const age = Math.max(0, (now - incidentStarted) / 1900);
        incidentEffect.visible = age < 1;
        incidentEffect.quaternion.copy(camera.quaternion);
        incidentEffect.scale.setScalar(reducedMotion.matches ? 2 : .8 + 3.5 * age);
        incidentEffect.material.uniforms.age.value = age;
      }
      sourceCutaway.constant = cutawayOn ? cutawayHeight : NO_CUT;
      flames.visible = layers.fire;
      try { renderer.render(world, camera); }
      finally { flames.visible = true; }  // The robot camera always renders physical flames.
    });
    $('scene-empty').hidden = true;
    let down: { x: number; y: number } | null = null;
    canvas.addEventListener('pointerdown', event => { if (event.button === 0) down = { x: event.clientX, y: event.clientY }; });
    canvas.addEventListener('pointercancel', () => { down = null; });
    canvas.addEventListener('pointerup', event => {
      if (!down || event.button !== 0 || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) { down = null; return; }
      down = null;
      if (!scene || !camera) return;
      const bounds = canvas.getBoundingClientRect();
      pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const point = new THREE.Vector3();
      if (!raycaster.ray.intersectPlane(groundPlane, point)) return;
      applyCell([Math.floor((point.x - scene.origin[0]) / scene.resolution), Math.floor((-point.z - scene.origin[1]) / scene.resolution)]);
    });
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      notify('The 3D graphics context was lost. Reload to restore the map and robot camera.', true);
    });
  } catch {
    renderer?.dispose();
    renderer = null;
    $('scene-empty').innerHTML = '<strong>3D rendering unavailable</strong><p>This browser could not initialize WebGL, so the robot camera cannot render frames. Use the analytic fixture mode and the keyboard cell inspector.</p>';
  }
}

function inspectCell(cell: Cell): void {
  if (!state || !scene) return;
  const [x, y] = cell;
  const room = scene.rooms.find(r => r.cells.some(p => same(p, cell)));
  const exterior = scene.exterior.some(p => same(p, cell));
  const labels = [exterior ? 'Outside floor' : scene.walls.some(p => same(p, cell)) ? 'Wall / blocked' : 'Floor', scene.exits.some(p => same(p, cell)) ? 'Exit' : '', room?.name].filter(Boolean).join(' · ');
  const [wx, wy] = cellWorld(cell);
  const source = view === 'belief' && belief ? belief : state;
  $('cell-readout').textContent = `(${x}, ${y}) ≈ (${number(wx, 2)}, ${number(wy, 2)}) m ${labels} | ${view === 'belief' ? 'Belief' : 'Truth'} heat ${number(source.heat[y]?.[x], 3)} · smoke ${number(source.smoke[y]?.[x], 3)} · ${source.observed[y]?.[x] > 0 ? 'Observed' : 'Unobserved'}`;
  ($('cell-x') as HTMLInputElement).value = String(x);
  ($('cell-y') as HTMLInputElement).value = String(y);
}

function applyCell(cell: Cell): void {
  if (!state || !inBounds(cell) || !cell.every(Number.isInteger)) { notify('Choose an integer cell coordinate within the scene.', true); return; }
  selectedCell = cell;
  inspectCell(cell);
  if (selectedTool !== 'inspect') {
    if (!idle() || busy || !draft) { notify('Scenario editing is available only while idle and connected.', true); return; }
    if (blocked(cell)) { notify('Choose a floor cell; walls and outside cells cannot contain survivors or ignitions.', true); return; }
    if (scene!.exits.some(p => same(p, cell))) { notify('Keep egress endpoints clear.', true); return; }
    if (selectedTool === 'remove') {
      draft.survivors = draft.survivors.filter(p => !same(p, cell));
      draft.ignitions = draft.ignitions.filter(p => !same(p, cell));
      if (draft.immobile) { draft.immobile = draft.immobile.filter(p => !same(p, cell)); if (!draft.immobile.length) delete draft.immobile; }
    } else {
      if (selectedTool === 'immobile') {
        if (!draft.survivors.some(p => same(p, cell))) { notify('Place a survivor first, then mark them as unable to walk.', true); return; }
        const marked = (draft.immobile ?? []).some(p => same(p, cell));
        draft.immobile = marked ? draft.immobile!.filter(p => !same(p, cell)) : [...(draft.immobile ?? []), cell];
        if (!draft.immobile.length) delete draft.immobile;
      } else {
        const target = selectedTool === 'survivor' ? draft.survivors : draft.ignitions;
        if (!target.some(p => same(p, cell))) target.push(cell);
      }
    }
    dirty = JSON.stringify(draft) !== JSON.stringify({ ...state.scenario, scene_id: scene!.id });
    syncUI();
  }
  drawDynamic();
}

async function mutation(work: () => Promise<void>): Promise<void> {
  if (busy || !connected) return;
  busy = true;
  syncUI();
  try { await work(); }
  catch (error) { notify(error instanceof Error ? error.message : 'The request failed.', true); }
  finally { busy = false; syncUI(); await poll(); }
}

const REPORT_LABEL: Record<string, string> = { 'confirmed-person': 'Person confirmed', 'possible-detection': 'Possible person', 'assistance-request': 'Assistance needed',
  'escort-started': 'Escort started', 'mission-end': 'Mission end' };
let teamSignature = '';
function renderTeam(next: SimState): void {
  const signature = JSON.stringify([next.reports, next.rooms_state, next.decisions?.length, next.comms_up]);
  if (signature === teamSignature) return;
  teamSignature = signature;
  $('comms-status').textContent = next.comms_up === false ? 'Communications down: reports are queued.' : '';
  const reports = next.reports ?? [];
  $('reports').innerHTML = reports.length ? reports.slice().reverse().map(r => {
    const where = `${r.floor}${r.room ? ' · ' + r.room : ''} · cell [${r.cell.join(', ')}]`;
    const counts = r.kind === 'mission-end'
      ? `Unresolved: ${(r.unresolved_detections ?? []).join(', ') || 'none'} · outstanding rooms: ${(r.outstanding_rooms ?? []).join(', ') || 'none'} · unsent reports: ${r.unsent_reports ?? 0}`
      : `${r.confirmed ? r.confirmed + ' confirmed' : ''}${r.possible ? r.possible + ' possible' : ''}${r.mobility ? ' · mobility: ' + r.mobility : ''} · heat ${number(r.hazards.heat * 100)}% smoke ${number(r.hazards.smoke * 100)}%`;
    const ack = r.status === 'delivered' ? `<button class="button subtle small" data-ack="${escape(r.id)}">Acknowledge</button>` : '';
    return `<li class="report ${escape(r.status)}"><time>${escape(number(r.time))} s</time><span><b>${escape(REPORT_LABEL[r.kind] ?? r.kind)}</b> · <span class="report-status">${escape(r.status)}</span>${r.status === 'queued' && r.attempts > 1 ? ` (retry ${r.attempts})` : ''}<br>${escape(where)}<br>${escape(counts)}<br>${escape(r.action)}${r.kind !== 'mission-end' ? ` · last usable approach [${r.last_usable_approach.join(', ')}]` : ''} ${ack}</span></li>`;
  }).join('') : '<li class="empty-line">No reports yet.</li>';
  document.querySelectorAll<HTMLButtonElement>('#reports button[data-ack]').forEach(b => b.addEventListener('click', () => void mutation(async () => {
    await api(`reports/${encodeURIComponent(b.dataset.ack!)}/ack`, {});
    notify('Report acknowledged for the outside team.');
  })));
  const rooms = next.rooms_state ?? [];
  const counts = rooms.reduce<Record<string, number>>((acc, r) => { acc[r.status] = (acc[r.status] ?? 0) + 1; return acc; }, {});
  $('rooms-summary').textContent = Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(' · ') || '—';
  $('rooms').innerHTML = rooms.map(r => `<li><time>${escape(number(r.observed_fraction * 100))}%</time><span><b>${escape(r.name)}</b> · ${escape(r.status === 'unvisited' && r.observed_fraction > 0 ? 'not yet visited (seen in passing)' : r.status)}${r.reason ? ' · ' + escape(r.reason) : ''}</span></li>`).join('');
  $('decisions').innerHTML = (next.decisions ?? []).slice().reverse().map(d => `<li><time>${escape(number(d.time))} s</time><span><b>${escape(d.decision)}</b>${d.target ? ' → [' + d.target.join(', ') + ']' : ''} · ${escape(d.reason)} · trigger: ${escape(d.trigger)} · options: ${d.options.map(o => '[' + o.target.join(', ') + '] ' + number(o.cost)).join('; ')}</span></li>`).join('') || '<li class="empty-line">No decisions yet.</li>';
}

type Strategy = 'greedy' | 'mission-astar' | 'gemini';
const STRATEGY_DESCRIPTION: Record<Strategy, string> = {
  greedy: 'Baseline: nearest unsearched room first, then straight to the exit.',
  'mission-astar': 'Mission A*: whole-mission plan ranked by survivor likelihood and observed fire/smoke urgency; re-plans every second.',
  gemini: 'Gemini planner: placeholder, not yet available.',
};
interface AgentInfo { state: string; enabled: boolean; pause_clock: boolean; thinking: boolean; model: string | null; accepted: number; rejected: number; failed: number;
  latency_s: number | null; last: { kind: string; id: string; reason: string; time: number } | null; last_error: string | null }
async function refreshAgent(): Promise<void> {
  try {
    const info = await api<AgentInfo>('agent');
    const states: Record<string, string> = { 'no-key': 'No GEMINI_API_KEY on the server; built-in planner only.', unavailable: 'Hosted agent unavailable; built-in planner only.', starting: 'Starting hosted agent…' };
    $('agent-status').textContent = info.state === 'ready'
      ? `${info.model} · ${info.enabled ? 'on' : 'off'}${info.thinking ? ' · thinking…' : ''}${info.pause_clock ? ' · clock paused while thinking' : ' · real time'} · ${info.accepted} accepted · ${info.rejected} rejected · ${info.failed} failed${info.latency_s != null ? ` · ${number(info.latency_s, 1)} s` : ''}` +
        (info.last ? ` · last: ${info.last.kind} ${info.last.id} (“${info.last.reason}”)` : '') + (info.last_error ? ` · last problem: ${info.last_error}` : '') +
        ' · Paired comparisons exclude it.'
      : `${states[info.state] ?? info.state}${info.last_error ? ' ' + info.last_error : ''}`;
  } catch { $('agent-status').textContent = 'Hosted agent status unavailable.'; }
}
document.querySelectorAll<HTMLInputElement>('input[name="strategy"]').forEach(input => input.addEventListener('change', () => {
  $('strategy-description').textContent = STRATEGY_DESCRIPTION[chosen<Strategy>('strategy')];
}));
window.setInterval(() => { if (connected) void refreshAgent(); }, 1000);

$('withdraw').addEventListener('click', () => void mutation(async () => {
  await api('control', { action: 'withdraw' });
  notify('Withdraw requested: the robot finishes any escort, returns to the exit and files a mission-end report.');
}));

for (const action of ['start', 'pause', 'reset'] as const) {
  $(action).addEventListener('click', () => void mutation(async () => {
    if (action === 'start' && dirty) throw new Error('Apply or discard your scenario changes first.');
    const strategy = chosen<Strategy>('strategy');
    if (strategy === 'gemini') throw new Error('The Gemini planner is a placeholder and not yet available.');
    await api('control', action === 'start' ? { action, mode: chosen<Mode>('mode'), perception: chosen<Perception>('perception'), strategy } : action === 'reset' ? { action, perception: chosen<Perception>('perception'), strategy } : { action, strategy });
    if (action === 'reset') { dirty = false; draft = null; lastFrameSeq = -1; }
    notify(action === 'reset' ? 'Reset requested. Waiting for the backend state.' : `${action === 'start' ? 'Start' : 'Pause'} requested.`);
  }));
}

$('load-map').addEventListener('click', () => $<HTMLInputElement>('map-file').click());
setupModelLibrary(async id => {
  if (busy || dirty || pairedRunning() || state?.status === 'running' || state?.hazards_running) throw new Error('Apply or discard edits and pause the run and hazards before switching scenes.');
  await api('scene', { id });
  scene = null; draft = null; dirty = false; lastFrameSeq = -1;
  await poll();
});
$<HTMLInputElement>('map-file').addEventListener('change', event => {
  const input = event.currentTarget as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  void mutation(async () => {
    if (file.size > 2_000_000) throw new Error('Floor plan JSON must be under 2 MB.');
    await api('environment', JSON.parse(await file.text()));
    scene = null; draft = null; dirty = false; lastFrameSeq = -1;
    notify('Floor plan loaded.');
  });
});
$('scene-select').addEventListener('change', () => void mutation(async () => {
  await api('scene', { id: ($('scene-select') as HTMLSelectElement).value });
  scene = null; draft = null; dirty = false; lastFrameSeq = -1;
  notify('Scene loaded with its standard preset.');
}));
document.querySelectorAll<HTMLInputElement>('input[name="tool"]').forEach(input => input.addEventListener('change', () => {
  selectedTool = input.value as Tool;
  $('viewport').classList.toggle('editing', selectedTool !== 'inspect');
  syncUI();
}));
document.querySelectorAll<HTMLInputElement>('input[name="view"]').forEach(input => input.addEventListener('change', () => {
  view = input.value as 'truth' | 'belief';
  belief = null;
  void poll();
}));
$('apply-cell').addEventListener('click', () => {
  const x = $('cell-x') as HTMLInputElement;
  const y = $('cell-y') as HTMLInputElement;
  if (!x.reportValidity() || !y.reportValidity()) return;
  applyCell([x.valueAsNumber, y.valueAsNumber]);
});
$('seed').addEventListener('input', () => {
  if (!draft || !idle() || busy) return;
  draft.seed = ($('seed') as HTMLInputElement).valueAsNumber;
  dirty = true;
  syncUI();
});
$('incident-room').addEventListener('change', () => {
  if (!draft || !idle() || busy) return;
  const room = $<HTMLSelectElement>('incident-room').value;
  if (room) draft.incident_room = room;
  else delete draft.incident_room;
  dirty = true;
  syncUI();
});
$('apply').addEventListener('click', () => void mutation(async () => {
  if (!idle()) throw new Error('The backend must be idle before applying a scenario.');
  const scenario = validateScenario(draft);
  await api('scenario', scenario);
  dirty = false;
  notify('Scenario applied to the backend.');
}));
$('discard').addEventListener('click', () => {
  if (!state) return;
  draft = { ...copyScenario(state.scenario), scene_id: state.scene_id };
  dirty = false;
  ($('seed') as HTMLInputElement).value = String(draft.seed);
  drawDynamic();
  syncRoomRisk();
  syncUI();
  notify('Local changes discarded.');
});
$('export').addEventListener('click', () => {
  try {
    const scenario = validateScenario(draft);
    const url = URL.createObjectURL(new Blob([JSON.stringify(scenario, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `rescuetwin-${scenario.scene_id}-${scenario.seed}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Scenario JSON saved locally.');
  } catch (error) { notify((error as Error).message, true); }
});
$('import').addEventListener('click', () => ($('scenario-file') as HTMLInputElement).click());
$('scenario-file').addEventListener('change', async () => {
  const input = $('scenario-file') as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error('Scenario files must be smaller than 2 MB.');
    const value: unknown = JSON.parse(await file.text());
    if (!idle() || busy) throw new Error('Reset to idle before loading a scenario.');
    draft = validateScenario(value);
    dirty = true;
    ($('seed') as HTMLInputElement).value = String(draft.seed);
    drawDynamic();
    syncRoomRisk();
    syncUI();
    notify(`Loaded ${file.name}. Review the map, then apply the scenario.`);
  } catch (error) { notify(`Could not load scenario: ${(error as Error).message}`, true); }
});

// The report schema is open: render backend keys and values without inventing a winner.
function renderReport(value: unknown, depth = 0): string {
  if (depth > 8) return `<pre>${escape(JSON.stringify(value, null, 2))}</pre>`;
  if (value === null || typeof value !== 'object') return `<span class="report-value">${escape(value ?? 'null')}</span>`;
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="subtle-label">Empty list</span>';
    return `<ol class="report-array">${value.map(item => `<li>${renderReport(item, depth + 1)}</li>`).join('')}</ol>`;
  }
  return `<dl class="report-grid">${Object.entries(value).map(([label, item]) => `<div class="report-entry"><dt>${escape(label.replace(/_/g, ' '))}</dt><dd>${renderReport(item, depth + 1)}</dd></div>`).join('')}</dl>`;
}
$('compare').addEventListener('click', () => void mutation(async () => {
  if (dirty) throw new Error('Apply your scenario changes before comparing.');
  const perception = chosen<Perception>('perception');
  $('comparison').hidden = false;
  $('comparison-body').textContent = perception === 'image-yolo' ? 'Starting paired live runs; this tab renders the robot camera for both.' : 'Computing paired report on the backend…';
  $('comparison-context').textContent = `Requested for ${scene!.name}, seed ${state!.scenario.seed}, ${perception}.`;
  const report = await api<unknown>('compare', { perception }, AbortSignal.timeout(180000));
  if (perception !== 'image-yolo') {
    $('comparison-body').innerHTML = renderReport(report);
    notify('Paired strategy report received from the backend.');
  } else {
    shownPairedStatus = '';
    notify('Paired live comparison started.');
  }
}));
$('close-comparison').addEventListener('click', () => { $('comparison').hidden = true; });
$('recenter').addEventListener('click', recenter);
$('preview-hazards').addEventListener('click', () => void mutation(async () => {
  const next = await api<unknown>('control', { action: 'toggle_hazards' });
  validateState(next);
  acceptState(next);
}));
$('focus-survivor').addEventListener('click', () => {
  if (view !== 'truth' || !camera || !controls || !avatars.size) return;
  const avatar = [...avatars.values()][focusedSurvivor++ % avatars.size];
  const target = avatar.position.clone().add(new THREE.Vector3(0, 0.85, 0));
  const offset = camera.position.clone().sub(controls.target).setLength(4.5);
  controls.target.copy(target);
  camera.position.copy(target).add(offset);
  controls.update();
});
$('focus-fire').addEventListener('click', () => {
  if (!focusCell || !camera || !controls) return;
  const target = position(focusCell);
  const offset = camera.position.clone().sub(controls.target).setLength(7);
  controls.target.copy(target);
  camera.position.copy(target).add(offset);
  controls.update();
});
$('orbit').addEventListener('click', () => setView('orbit'));
$('plan').addEventListener('click', () => setView('plan'));
$('walls-layer').addEventListener('change', applyWallLayer);
$('human-variant').addEventListener('change', () => {
  if (!draft || !idle() || busy) return;
  const value = $<HTMLSelectElement>('human-variant').value;
  if (!validHumanVariant(value)) return;
  if (value === 'adult-male') delete draft.human_variant; else draft.human_variant = value;
  dirty = JSON.stringify(draft) !== JSON.stringify({ ...state!.scenario, scene_id: scene!.id });
  syncUI();
});

$('building-use').addEventListener('change', async () => {
  if (!draft || !idle() || busy || !state || !scene) return;
  const value = ($('building-use') as HTMLSelectElement).value;
  if (value) draft.building_use = value; else delete draft.building_use;
  dirty = JSON.stringify(draft) !== JSON.stringify({ ...state.scenario, scene_id: scene.id });
  syncBuildingUse(); syncUI();
  await loadPresets();
  notify('Building use changed. Pick a preset or Apply to use it; presets now place people for this use.');
});
$('room-risk-list').addEventListener('change', event => {
  const select = event.target as HTMLSelectElement;
  if (!select.dataset.room || !draft || !idle() || busy || !state || !scene) return;
  const next = { ...(draft.room_risk ?? {}) };
  if (select.value) next[select.dataset.room] = select.value; else delete next[select.dataset.room];
  if (Object.keys(next).length) draft.room_risk = next; else delete draft.room_risk;
  dirty = JSON.stringify(draft) !== JSON.stringify({ ...state.scenario, scene_id: scene.id });
  syncRoomRisk(); syncUI();
  notify('Room fire-risk tag changed. Apply to use it; the robot searches gas-heater rooms first and sprinklered rooms slightly later.');
});
$('risk-layer').addEventListener('change', () => { riskLayer.visible = ($('risk-layer') as HTMLInputElement).checked; });
$('furniture-layer').addEventListener('change', () => { obstacleLayer.visible = ($('furniture-layer') as HTMLInputElement).checked; });
$('cutaway-height').addEventListener('change', () => {
  const value = ($('cutaway-height') as HTMLSelectElement).value;
  cutawayOn = value !== 'off';
  if (cutawayOn) cutawayHeight = Number(value);
});
$('expand').addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $('viewport').closest('section')?.requestFullscreen();
  } catch {
    notify('Fullscreen is unavailable in this browser.', true);
  }
});
document.addEventListener('fullscreenchange', () => {
  $('expand').textContent = document.fullscreenElement ? 'Exit fullscreen' : 'Fullscreen';
  requestAnimationFrame(recenter);
});
for (const name of ['fire', 'heat', 'smoke', 'observed', 'route'] as const) {
  $(`${name}-layer`).addEventListener('change', () => { layers[name] = ($(`${name}-layer`) as HTMLInputElement).checked; drawDynamic(); });
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });
setupRenderer();
syncUI();
void poll();
window.setInterval(() => void poll(), 200);
// Exposed for automated end-to-end checks (headless browser); read-only accessors.
(window as unknown as { rescuetwin: object }).rescuetwin = { state: () => state, scene: () => scene, lastFrameSeq: () => lastFrameSeq };

// An independent same-origin page renders live sensors while this viewer reads history.
if (new URLSearchParams(location.search).has('sensorOnly')) {
  document.body.style.opacity = '0';
} else {
  const panel = document.createElement('section');
  panel.className = 'panel recordings';
  panel.innerHTML = `<h2>Recorded runs</h2><label>Record next run <input id="record-next" type="checkbox" checked></label>
    <input id="runs-filter" aria-label="Filter recorded scenes, policies and outcomes" placeholder="Filter scene, policy, outcome"><button id="runs-refresh">Refresh</button><select id="runs-list"><option value="">Select a recording</option></select>
    <button id="replay-open">Replay</button><button id="replay-live">Live</button>
    <button id="replay-next-event">Next decision / detection</button><button id="replay-play">Play / pause</button><button id="replay-step">Step</button>
    <select id="replay-speed"><option>1</option><option>2</option><option>4</option></select> ×
    <input id="replay-seek" aria-label="Recording event" type="range" min="0" max="0" value="0">
    <a id="replay-download">Download archive</a><p id="replay-label">Live view</p>
    <details><summary>Recorded evidence, tools and decisions</summary><pre id="replay-inspector" style="max-height:350px;overflow:auto;white-space:pre-wrap"></pre></details>`;
  panel.querySelectorAll('button').forEach(b=>b.className='button small');
  document.querySelector('main')?.prepend(panel);
  if (!panel.isConnected) document.body.prepend(panel);
  const refresh = async () => {
    const rows = await api<{id:string;scene_name:string;strategy:string;status:string}[]>('runs');
    const list = document.getElementById('runs-list') as HTMLSelectElement;
    list.replaceChildren(new Option('Select a recording', ''), ...rows.filter(r=>`${r.scene_name} ${r.strategy} ${r.status}`.toLowerCase().includes((document.getElementById('runs-filter') as HTMLInputElement).value.toLowerCase())).map(r => new Option(`${r.scene_name} · ${r.strategy} · ${r.status}`, r.id)));
  };
  document.getElementById('runs-filter')!.oninput=()=>void refresh();
  document.getElementById('runs-refresh')!.onclick = () => void refresh();
  document.getElementById('record-next')!.onchange = () => void api('recording', {enabled:(document.getElementById('record-next') as HTMLInputElement).checked});
  const show = async (cursor:number) => {
    if (!replayId) return;
    const id = replayId;
    const rows = await api<{seq:number;time:number;kind:string;data:any}[]>(`runs/${id}/timeline?start=${cursor}&limit=1`);
    if (!rows.length || replayId !== id) { replayPlaying=false; return; }
    const row = rows[0]; replayCursor=row.seq;
    (document.getElementById('replay-seek') as HTMLInputElement).value=String(row.seq);
    document.getElementById('replay-label')!.textContent=`READ ONLY · ${row.time.toFixed(1)} s · ${row.kind}`;
    document.getElementById('replay-inspector')!.textContent=JSON.stringify(row.data,null,2);
    const historical=await api<{snapshot:any;frame:any}>(`runs/${id}/snapshot?seq=${row.seq}`);
    const display=historical.snapshot?.data;
    if (display) {
      riskMap={rooms:display.state.risk_map??[],building_default:'unknown',building_source:'Recorded room tags',tags:[],note:''};
      $('room-risk-list').innerHTML=riskMap.rooms.map(r=>`<p>${escape(r.name)} · ${escape(r.tag_label)}</p>`).join('');
      buildingUses=null;$('building-use-hint').textContent='Recorded building-use settings; search prior, not occupancy.';
      drawRiskLayer();
      belief={...display.observation,heat:display.state.heat,smoke:display.state.smoke,survivors:display.observation.tracks};
      // Reconstruct belief grids exclusively from recorded measurements, never truth.
      belief!.heat=belief!.heat.map(r => r.map(() => 0));belief!.smoke=belief!.smoke.map(r => r.map(() => 0));
      for (const sample of display.observation.samples) belief![sample.modality as 'heat'|'smoke'][sample.cell[1]][sample.cell[0]]=sample.value;
      acceptState({...display.state,paired:null});
      for (const id of ['start','pause','reset','withdraw','preview-hazards','load-map','scene-select']) (document.getElementById(id) as HTMLButtonElement).disabled=true;
    }
    if (historical.frame) {
      const img = new Image();img.src=`/api/runs/${id}/artifacts/${historical.frame.data.rgb}`;
      img.onload=() => { const canvas=document.querySelector('#robot-feed') as HTMLCanvasElement|null;canvas?.getContext('2d')?.drawImage(img,0,0,canvas.width,canvas.height); };
    }
  };
  document.getElementById('replay-open')!.onclick=() => void (async () => {
    const id=(document.getElementById('runs-list') as HTMLSelectElement).value;if(!id)return;
    replayPlaying=false;
    if (!sensorWorker) {sensorWorker=document.createElement('iframe');sensorWorker.src='/?sensorOnly=1';sensorWorker.style.cssText='position:fixed;width:2px;height:2px;left:-10px';document.body.append(sensorWorker);}
    await api('renderer',{id:rendererId,release:true});rendererOwned=false;
    replayId=id;
    const manifest=await api<{entries:number}>(`runs/${id}`);
    replayScene=await api<SceneData>(`runs/${id}/artifacts/scene.json`);
    // Older recordings lack avatar assets; retain an explicit partial replay warning.
    const avatarManifest=await fetch(`/api/runs/${id}/artifacts/avatar-humans.json`);
    if(avatarManifest.ok) await loadHumans(()=>{avatars.forEach(a=>avatarLayer.remove(a));avatars.clear();},m=>notify(m,true),`/api/runs/${id}/artifacts/`,'avatar-');
    else notify('Older recording: avatar assets were not archived; using installed appearance assets.');
    scene=replayScene;buildStaticScene();renderSceneInfo();recenter();await loadSceneMesh(scene,++sceneLoadVersion);sceneReady=true;
    (document.getElementById('replay-seek') as HTMLInputElement).max=String(manifest.entries-1);
    (document.getElementById('replay-download') as HTMLAnchorElement).href=`/api/runs/${id}/download`;
    await show(0);
  })().catch(e=>notify(String(e),true));
  document.getElementById('replay-live')!.onclick=() => {replayPlaying=false;replayId=null;replayScene=null;sensorWorker?.remove();sensorWorker=null;scene=null;void loadHumans(()=>{avatars.forEach(a=>avatarLayer.remove(a));avatars.clear();},m=>notify(m,true));void poll();document.getElementById('replay-label')!.textContent='Live view';};
  document.getElementById('replay-next-event')!.onclick=()=>void (async()=>{if(!replayId)return;const m=await api<{highlights?:{seq:number;kind:string}[]}>(`runs/${replayId}`);const next=m.highlights?.find(e=>e.seq>replayCursor&&['decision','detection','model','tool','report'].includes(e.kind));if(next)await show(next.seq);})();
  document.getElementById('replay-play')!.onclick=()=>{replayPlaying=!replayPlaying;};
  document.getElementById('replay-step')!.onclick=()=>void show(replayCursor+1);
  document.getElementById('replay-seek')!.oninput=()=>void show(Number((document.getElementById('replay-seek') as HTMLInputElement).value));
  let showing=false;
  setInterval(()=>{if(replayPlaying&&!showing){showing=true;void show(replayCursor+Number((document.getElementById('replay-speed') as HTMLSelectElement).value)).finally(()=>{showing=false;});}},200);
  void refresh();
}
