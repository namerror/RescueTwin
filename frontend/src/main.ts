import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import './styles.css';

type Cell = [number, number];
type Mode = 'adaptive' | 'baseline';
type Tool = 'inspect' | 'survivor' | 'fire' | 'remove';
interface Scenario { survivors: Cell[]; ignitions: Cell[]; seed: number }
interface SimState {
  scene: { id: string; name: string; width: number; height: number; resolution: number; walls: Cell[]; exits: Cell[]; rooms: { id: string; name: string; cells: Cell[] }[]; source: string };
  scenario: Scenario;
  time: number;
  status: string;
  robot: Cell;
  survivors: { id: string | number; position: Cell; status: string }[];
  heat: number[][];
  smoke: number[][];
  observed: number[][];
  route: Cell[];
  events: { time: number; message: string }[];
  metrics: { evacuated: number; exposure: number; distance: number };
  mode: Mode;
}

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing interface element: ${id}`);
  return element as T;
};
const escape = (value: unknown): string => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const number = (value: number, digits = 1): string => Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
const same = (a: Cell, b: Cell): boolean => a[0] === b[0] && a[1] === b[1];
const copyScenario = (value: Scenario): Scenario => ({ survivors: value.survivors.map(p => [...p]), ignitions: value.ignitions.map(p => [...p]), seed: value.seed });
const key = (p: Cell): string => `${p[0]},${p[1]}`;

$('app').innerHTML = `
  <a class="skip-link" href="#workspace">Skip to simulation</a>
  <header class="app-header">
    <a class="brand" href="./" aria-label="RescueTwin home"><span class="brand-mark" aria-hidden="true">R<span>+</span></span><span>Rescue<span class="brand-light">Twin</span><small>EMERGENCY SIMULATION LAB</small></span></a>
    <div class="header-right"><span class="environment-tag">LOCAL WORKBENCH</span><span id="connection" class="connection" role="status"><i></i>Connecting</span></div>
  </header>
  <main>
    <section class="page-intro"><div><p class="eyebrow">OPERATIONS / SCENARIO WORKBENCH</p><h1>Plan the rescue. <span>Test the response.</span></h1><p class="subtitle">Explore hazard propagation and compare rescue strategies in a synthetic environment.</p></div><div class="run-badge"><span id="run-status">AWAITING BACKEND</span><strong id="clock">—<small> SIM TIME</small></strong></div></section>
    <aside class="disclosure" aria-label="Simulation data disclosure"><span class="disclosure-icon" aria-hidden="true">i</span><div><strong>Synthetic fixture · Analytic perception</strong><p>This view uses simulated geometry, people and hazard fields. Human figures are simulation proxies. Camera-based detection is not connected yet.</p></div><span class="outline-tag">SIMULATION ONLY</span></aside>
    <div class="metrics" aria-label="Mission metrics">
      <article class="metric"><span class="metric-label">EVACUATED</span><div><strong id="evacuated">—</strong><span id="population" class="metric-unit">people</span></div><small>Backend-reported rescues</small></article>
      <article class="metric"><span class="metric-label">EXPOSURE</span><div><strong id="exposure">—</strong><span class="metric-unit">normalized units</span></div><small>Illustrative hazard exposure</small></article>
      <article class="metric"><span class="metric-label">DISTANCE</span><div><strong id="distance">—</strong><span class="metric-unit">metres</span></div><small>Robot travel distance</small></article>
      <article class="metric"><span class="metric-label">PERCEPTION MODEL</span><div><strong class="text-value">Analytic</strong><span class="tiny-dot"></span></div><small>Observed cells from simulation state</small></article>
    </div>
    <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
    <div class="workspace-grid" id="workspace" tabindex="-1">
      <section class="panel scene-panel" aria-labelledby="scene-heading">
        <div class="panel-heading"><div><p class="eyebrow">DIGITAL TWIN / CUTAWAY</p><h2 id="scene-heading">Waiting for scene</h2></div><span id="scene-size" class="subtle-label">— × — cells</span></div>
        <div class="scene-toolbar"><div class="layer-controls" aria-label="Map layers"><label><input type="checkbox" id="heat-layer" checked /><span class="swatch heat"></span>Heat</label><label><input type="checkbox" id="smoke-layer" checked /><span class="swatch smoke"></span>Smoke</label><label><input type="checkbox" id="observed-layer" /><span class="swatch observed"></span>Unobserved</label><label><input type="checkbox" id="route-layer" checked /><span class="swatch route"></span>Route</label></div><button id="recenter" class="button subtle small" title="Reset map camera">Recenter</button></div>
        <div id="viewport" class="viewport"><div id="scene-empty" class="scene-empty"><span class="empty-symbol" aria-hidden="true">⌘</span><strong>Waiting for simulation state</strong><p>Start the backend on port 8000 to load your scene.</p></div><div class="view-label">TOP-DOWN <span>3D CUTAWAY</span></div><div id="cell-readout" class="cell-readout">Select a cell to inspect</div></div>
        <div class="scene-footer"><div class="legend"><span><i class="legend-person"></i>Survivor proxy</span><span><i class="legend-robot"></i>Robot</span><span><i class="legend-exit"></i>Exit</span><span><i class="legend-fire"></i>Ignition</span></div><span class="map-help">Drag to orbit · Scroll to zoom · Right-drag to pan</span></div>
        <details class="keyboard-editor"><summary>Cell inspector & keyboard editing</summary><div class="cell-inputs"><label>Column x<input id="cell-x" type="number" min="0" step="1" value="0" /></label><label>Row y<input id="cell-y" type="number" min="0" step="1" value="0" /></label><button id="apply-cell" class="button secondary">Inspect cell</button></div><p>Coordinates start at (0, 0). Choose an editing tool in Scenario setup, then apply it here or click the map. Heat and smoke display with intensity clamped to 0–1; inspection shows raw values.</p></details>
      </section>
      <aside class="sidebar">
        <section class="panel" aria-labelledby="controls-heading"><div class="panel-heading compact"><div><p class="eyebrow">MISSION CONTROL</p><h2 id="controls-heading">Run simulation</h2></div><span class="step-number">01</span></div>
          <fieldset id="mode-fieldset"><legend>Planning strategy</legend><div class="segmented"><label><input type="radio" name="mode" value="adaptive" checked /><span>Adaptive</span></label><label><input type="radio" name="mode" value="baseline" /><span>Baseline</span></label></div></fieldset><p id="mode-description" class="field-hint">Strategy sent to the backend when starting a run.</p>
          <div class="control-buttons"><button id="start" class="button primary">▶ Start run</button><button id="pause" class="button secondary">Ⅱ Pause</button><button id="reset" class="button icon-button" title="Reset simulation" aria-label="Reset simulation">↺</button></div><p id="control-hint" class="field-hint">Connect to the backend to begin.</p>
        </section>
        <section class="panel" aria-labelledby="scenario-heading"><div class="panel-heading compact"><div><p class="eyebrow">SCENARIO SETUP</p><h2 id="scenario-heading">Place the conditions</h2></div><span class="step-number">02</span></div>
          <p id="edit-hint" class="field-hint">Editing is available when the simulation is idle.</p><fieldset id="tool-fieldset"><legend class="sr-only">Map editing tool</legend><div class="tool-grid"><label><input name="tool" type="radio" value="inspect" checked /><span><b aria-hidden="true">⌖</b>Inspect</span></label><label><input name="tool" type="radio" value="survivor" /><span><b aria-hidden="true">♙</b>Survivor</span></label><label><input name="tool" type="radio" value="fire" /><span><b aria-hidden="true">♨</b>Ignition</span></label><label><input name="tool" type="radio" value="remove" /><span><b aria-hidden="true">−</b>Remove</span></label></div></fieldset>
          <div class="scenario-counts"><span><i class="tiny-dot blue"></i><strong id="survivor-count">—</strong> survivors</span><span><i class="tiny-dot orange"></i><strong id="fire-count">—</strong> ignitions</span></div>
          <label class="seed-label" for="seed">Random seed <input id="seed" type="number" step="1" value="7" /></label>
          <div class="save-row"><button id="apply" class="button secondary">Apply scenario</button><button id="discard" class="button subtle small">Discard</button></div><div class="file-actions"><button id="export" class="button subtle small">↓ Save JSON</button><button id="import" class="button subtle small">↑ Load JSON</button><input id="scenario-file" type="file" accept=".json,application/json" hidden /></div>
        </section>
        <section class="panel compare-callout"><p class="eyebrow">STRATEGY BENCHMARK</p><h2>Same scenario. Two strategies.</h2><p>Request a paired report for adaptive and baseline planning from the backend.</p><button id="compare" class="button secondary full-width">Compare strategies <span aria-hidden="true">↗</span></button><p class="field-hint">Apply scenario changes before comparing.</p></section>
      </aside>
      <section class="panel event-panel" aria-labelledby="events-heading"><div class="panel-heading compact"><div><p class="eyebrow">MISSION TELEMETRY</p><h2 id="events-heading">Event timeline</h2></div><span class="subtle-label">5 Hz state polling</span></div><ol id="events" class="event-list"><li class="empty-line">Events appear here as the backend reports them.</li></ol></section>
      <section id="comparison" class="panel comparison-panel" aria-labelledby="comparison-heading" hidden><div class="panel-heading compact"><div><p class="eyebrow">BACKEND REPORT</p><h2 id="comparison-heading">Strategy comparison</h2></div><button id="close-comparison" class="button subtle small">Close</button></div><p id="comparison-context" class="field-hint"></p><div id="comparison-body"></div></section>
    </div>
    <footer class="app-footer"><span><strong>RescueTwin</strong> / Emergency response research</span><span>Synthetic fixture · Analytic perception · No camera feed or detections</span></footer>
  </main>`;

let state: SimState | null = null;
let draft: Scenario | null = null;
let dirty = false;
let busy = false;
let connected = false;
let selectedTool: Tool = 'inspect';
let sceneIdentity = '';
let eventSignature = '';
let selectedCell: Cell | null = null;
let pollInFlight = false;
let comparisonAbort: AbortController | null = null;
let renderer: THREE.WebGLRenderer | null = null;
let controls: OrbitControls | null = null;
let camera: THREE.PerspectiveCamera | null = null;
const world = new THREE.Scene();
world.background = new THREE.Color('#0d151f');
const staticLayer = new THREE.Group();
const dynamicLayer = new THREE.Group();
world.add(staticLayer, dynamicLayer);
world.add(new THREE.HemisphereLight(0xd6ebff, 0x24344a, 2.5));
const sunlight = new THREE.DirectionalLight(0xffffff, 3);
sunlight.position.set(-10, 30, 15);
world.add(sunlight);
const raycaster = new THREE.Raycaster();
const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const pointer = new THREE.Vector2();
const layers = { heat: true, smoke: true, observed: false, route: true };

function notify(message: string, error = false): void {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = false;
}

async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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
  if (!s || !s.scene || !Number.isInteger(s.scene.width) || !Number.isInteger(s.scene.height) || s.scene.width < 1 || s.scene.height < 1 || !Number.isFinite(s.scene.resolution) || s.scene.resolution <= 0 || !s.metrics || !s.scenario || !Number.isSafeInteger(s.scenario.seed) || !Number.isFinite(s.time) || typeof s.status !== 'string' || !validCell(s.robot) || !['adaptive', 'baseline'].includes(s.mode)) throw new Error('The backend returned an invalid simulation state.');
  const cellLists = [s.scene.walls, s.scene.exits, s.scenario.survivors, s.scenario.ignitions, s.route];
  if (cellLists.some(list => !Array.isArray(list) || !list.every(validCell)) || !Array.isArray(s.scene.rooms) || !s.scene.rooms.every(r => typeof r.name === 'string' && Array.isArray(r.cells) && r.cells.every(validCell)) || !Array.isArray(s.survivors) || !s.survivors.every(p => validCell(p.position) && typeof p.status === 'string') || !Array.isArray(s.events) || !s.events.every(e => Number.isFinite(e.time) && typeof e.message === 'string') || ![s.heat, s.smoke, s.observed].every(matrix => Array.isArray(matrix) && matrix.every(row => Array.isArray(row) && row.every(n => typeof n === 'number' && Number.isFinite(n))))) throw new Error('The backend returned malformed scene or telemetry data.');
}

function validateScenario(value: unknown): Scenario {
  if (!state) throw new Error('Load a backend scene before importing a scenario.');
  if (!value || typeof value !== 'object') throw new Error('Expected a scenario JSON object.');
  const candidate = value as Scenario;
  if (!Number.isSafeInteger(candidate.seed)) throw new Error('Seed must be a safe integer.');
  const walls = new Set(state.scene.walls.map(key));
  for (const field of ['survivors', 'ignitions'] as const) {
    if (!Array.isArray(candidate[field])) throw new Error(`${field} must be an array of [x, y] cells.`);
    const seen = new Set<string>();
    for (const cell of candidate[field]) {
      if (!validCell(cell) || !cell.every(Number.isInteger) || !inBounds(cell)) throw new Error(`${field} contains an invalid or out-of-bounds cell.`);
      if (walls.has(key(cell))) throw new Error(`${field} cannot be placed inside walls.`);
      if (seen.has(key(cell))) throw new Error(`${field} contains duplicate cells.`);
      seen.add(key(cell));
    }
  }
  return copyScenario(candidate);
}

function inBounds(cell: Cell): boolean {
  return !!state && cell[0] >= 0 && cell[1] >= 0 && cell[0] < state.scene.width && cell[1] < state.scene.height;
}

function idle(): boolean { return connected && state?.status.toLowerCase() === 'idle'; }

function syncUI(): void {
  const editable = idle() && !busy;
  const status = state?.status.toLowerCase();
  $('connection').className = `connection ${connected ? 'online' : 'offline'}`;
  $('connection').innerHTML = `<i></i>${connected ? 'Backend connected' : state ? 'Connection lost' : 'Backend offline'}`;
  $('run-status').textContent = state ? state.status.toUpperCase() : 'AWAITING BACKEND';
  $('run-status').classList.toggle('running', status === 'running');
  $('start') .textContent = status === 'paused' ? '▶ Resume run' : '▶ Start run';
  ($('start') as HTMLButtonElement).disabled = !connected || busy || dirty || !['idle', 'paused'].includes(status ?? '');
  ($('pause') as HTMLButtonElement).disabled = !connected || busy || status !== 'running';
  ($('reset') as HTMLButtonElement).disabled = !connected || busy || !state;
  ($('mode-fieldset') as HTMLFieldSetElement).disabled = !editable;
  document.querySelectorAll<HTMLInputElement>('input[name="tool"]').forEach(input => { input.disabled = input.value !== 'inspect' && !editable; });
  ($('seed') as HTMLInputElement).disabled = !editable;
  ($('apply') as HTMLButtonElement).disabled = !editable || !dirty;
  ($('discard') as HTMLButtonElement).disabled = busy || !dirty;
  ($('import') as HTMLButtonElement).disabled = !editable;
  ($('export') as HTMLButtonElement).disabled = !draft;
  ($('compare') as HTMLButtonElement).disabled = !connected || busy || dirty || !['idle', 'paused', 'completed', 'complete', 'finished'].includes(status ?? '');
  $('control-hint').textContent = !connected ? 'Backend unavailable. Showing the last received state, if any.' : busy ? 'Waiting for the backend…' : dirty ? 'Apply or discard scenario changes before starting.' : status === 'idle' ? 'Ready. Select a strategy and start your run.' : status === 'running' ? `Running ${state?.mode} strategy. Pause to inspect.` : status === 'paused' ? 'Simulation paused. Resume or reset to edit.' : 'Reset the simulation to edit and start a new run.';
  $('edit-hint').textContent = !idle() ? 'Scenario editing is locked. Reset to idle to edit.' : dirty ? 'Unsaved changes · Apply to send to the backend.' : 'Select a tool, then click a floor cell. Changes stay local until applied.';
  $('apply').textContent = dirty ? 'Apply scenario •' : 'Scenario applied';
  $('survivor-count').textContent = draft ? String(draft.survivors.length) : '—';
  $('fire-count').textContent = draft ? String(draft.ignitions.length) : '—';
  $('apply-cell').textContent = selectedTool === 'inspect' ? 'Inspect cell' : selectedTool === 'remove' ? 'Remove from cell' : `Place ${selectedTool === 'fire' ? 'ignition' : 'survivor'}`;
}

function acceptState(next: SimState): void {
  const identity = JSON.stringify([next.scene.id, next.scene.width, next.scene.height, next.scene.resolution, next.scene.walls, next.scene.exits, next.scene.rooms]);
  const changed = identity !== sceneIdentity;
  const lostEdits = dirty && (changed || next.status.toLowerCase() !== 'idle');
  state = next;
  connected = true;
  if (!draft || !dirty || lostEdits) {
    draft = copyScenario(next.scenario);
    dirty = false;
    ($('seed') as HTMLInputElement).value = String(draft.seed);
  }
  if (lostEdits) notify('Local edits were discarded because the backend scene or run status changed.');
  if (next.status.toLowerCase() !== 'idle' && selectedTool !== 'inspect') {
    selectedTool = 'inspect';
    document.querySelector<HTMLInputElement>('input[name="tool"][value="inspect"]')!.checked = true;
  }
  if (!dirty && next.status.toLowerCase() !== 'idle') document.querySelector<HTMLInputElement>(`input[name="mode"][value="${next.mode}"]`)!.checked = true;
  $('scene-heading').textContent = next.scene.name;
  $('scene-size').textContent = `${next.scene.width} × ${next.scene.height} cells · ${number(next.scene.resolution, 3)} m/cell`;
  $('clock').innerHTML = `${number(next.time)}<small> s SIM TIME</small>`;
  $('evacuated').textContent = number(next.metrics.evacuated, 0);
  $('population').textContent = `/ ${next.scenario.survivors.length} people`;
  $('exposure').textContent = number(next.metrics.exposure, 2);
  $('distance').textContent = number(next.metrics.distance, 2);
  ($('cell-x') as HTMLInputElement).max = String(next.scene.width - 1);
  ($('cell-y') as HTMLInputElement).max = String(next.scene.height - 1);
  if (changed) {
    sceneIdentity = identity;
    selectedCell = null;
    buildStaticScene();
    recenter();
  }
  if (renderer) $('scene-empty').hidden = true;
  drawDynamic();
  if (selectedCell) inspectCell(selectedCell);
  const signature = JSON.stringify(next.events);
  if (signature !== eventSignature) {
    eventSignature = signature;
    $('events').innerHTML = next.events.length ? next.events.slice(-60).reverse().map(e => `<li><time>${escape(number(e.time))} s</time><span>${escape(e.message)}</span></li>`).join('') : '<li class="empty-line">No mission events yet. Start a run to collect telemetry.</li>';
  }
  syncUI();
}

async function poll(): Promise<void> {
  if (pollInFlight || document.hidden) return;
  pollInFlight = true;
  try {
    const next = await api<unknown>('state', undefined, AbortSignal.timeout(4000));
    validateState(next);
    acceptState(next);
  } catch (error) {
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
    if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach(material => material.dispose());
    }
  });
  group.clear();
}

function position(cell: Cell, altitude = 0): THREE.Vector3 {
  return new THREE.Vector3(cell[0] - (state!.scene.width - 1) / 2, altitude, cell[1] - (state!.scene.height - 1) / 2);
}

function box(group: THREE.Group, cell: Cell, width: number, height: number, depth: number, color: THREE.ColorRepresentation, altitude: number, opacity = 1): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), new THREE.MeshStandardMaterial({ color, roughness: 0.7, transparent: opacity < 1, opacity, depthWrite: opacity === 1 }));
  mesh.position.copy(position(cell, altitude));
  group.add(mesh);
  return mesh;
}

function buildStaticScene(): void {
  clearGroup(staticLayer);
  if (!state) return;
  const { width, height, walls, exits, rooms } = state.scene;
  const floor = new THREE.Mesh(new THREE.BoxGeometry(width, 0.16, height), new THREE.MeshStandardMaterial({ color: '#182633', roughness: 1 }));
  floor.position.y = -0.12;
  staticLayer.add(floor);
  const roomColors = ['#233746', '#253b47', '#203746', '#2b3946'];
  rooms.forEach((room, i) => room.cells.forEach(cell => box(staticLayer, cell, 0.98, 0.025, 0.98, roomColors[i % roomColors.length], -0.025)));
  // A rectangular grid keeps x/y coordinates consistent with backend cell arrays.
  const points: THREE.Vector3[] = [];
  for (let x = 0; x <= width; x++) points.push(new THREE.Vector3(x - width / 2, 0.005, -height / 2), new THREE.Vector3(x - width / 2, 0.005, height / 2));
  for (let y = 0; y <= height; y++) points.push(new THREE.Vector3(-width / 2, 0.005, y - height / 2), new THREE.Vector3(width / 2, 0.005, y - height / 2));
  staticLayer.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: '#3b5062', transparent: true, opacity: 0.3 })));
  walls.forEach(cell => {
    box(staticLayer, cell, 0.97, 0.72, 0.97, '#62778a', 0.36);
    box(staticLayer, cell, 0.97, 0.035, 0.97, '#8ba1b1', 0.735);
  });
  exits.forEach(cell => {
    box(staticLayer, cell, 0.9, 0.045, 0.9, '#2bcba1', 0.045);
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), position(cell, 0.1).add(new THREE.Vector3(0, 0, 0.28)), 0.6, '#d0fff0', 0.22, 0.22);
    staticLayer.add(arrow);
  });
}

function human(cell: Cell, rescued: boolean): void {
  const color = rescued ? '#5ee3ae' : '#78bcff';
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.13, 0.2, 3, 6), new THREE.MeshStandardMaterial({ color }));
  body.position.copy(position(cell, 0.42));
  dynamicLayer.add(body);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 8, 6), new THREE.MeshStandardMaterial({ color: '#e0efff' }));
  head.position.copy(position(cell, 0.77));
  dynamicLayer.add(head);
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.28, 0.34, 20), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.copy(position(cell, 0.075));
  dynamicLayer.add(ring);
}

function drawDynamic(): void {
  clearGroup(dynamicLayer);
  if (!state) return;
  const walls = new Set(state.scene.walls.map(key));
  for (let y = 0; y < state.scene.height; y++) {
    for (let x = 0; x < state.scene.width; x++) {
      if (walls.has(`${x},${y}`)) continue;
      const cell: Cell = [x, y];
      const heat = THREE.MathUtils.clamp(state.heat[y]?.[x] ?? 0, 0, 1);
      const smoke = THREE.MathUtils.clamp(state.smoke[y]?.[x] ?? 0, 0, 1);
      if (layers.heat && heat > 0.01) box(dynamicLayer, cell, 0.96, 0.045 + heat * 0.2, 0.96, new THREE.Color('#efb145').lerp(new THREE.Color('#f04f35'), heat), 0.07 + heat * 0.1, 0.25 + heat * 0.65);
      if (layers.smoke && smoke > 0.01) box(dynamicLayer, cell, 0.94, 0.04 + smoke * 0.35, 0.94, '#9ea6b5', 0.18 + smoke * 0.12, smoke * 0.5);
      if (layers.observed && !(state.observed[y]?.[x] > 0)) box(dynamicLayer, cell, 0.98, 0.02, 0.98, '#050a13', 0.39, 0.78);
    }
  }
  if (layers.route && state.route.length > 1) {
    // Thin tubes remain visible on high-DPI displays, unlike WebGL line widths.
    const material = new THREE.MeshBasicMaterial({ color: '#55e3c2' });
    for (let i = 1; i < state.route.length; i++) {
      const a = position(state.route[i - 1], 0.49);
      const b = position(state.route[i], 0.49);
      const delta = b.clone().sub(a);
      if (delta.length() === 0) continue;
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, delta.length(), 5), material.clone());
      tube.position.copy(a.add(b).multiplyScalar(0.5));
      tube.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
      dynamicLayer.add(tube);
    }
    material.dispose();
  }
  const editing = state.status.toLowerCase() === 'idle';
  if (editing && draft) draft.survivors.forEach(cell => human(cell, false));
  else state.survivors.forEach(person => {
    if (!['evacuated', 'dead', 'deceased'].includes(person.status.toLowerCase())) human(person.position, person.status.toLowerCase() === 'rescued');
  });
  (editing && draft ? draft.ignitions : state.scenario.ignitions).forEach(cell => {
    const flame = new THREE.Mesh(new THREE.ConeGeometry(0.24, 0.65, 6), new THREE.MeshStandardMaterial({ color: '#ff9a46', emissive: '#da3d13', emissiveIntensity: 0.7 }));
    flame.position.copy(position(cell, 0.43));
    dynamicLayer.add(flame);
  });
  box(dynamicLayer, state.robot, 0.53, 0.27, 0.6, '#5ce0ca', 0.24);
  box(dynamicLayer, state.robot, 0.26, 0.13, 0.24, '#d9fff7', 0.44);
  for (const side of [-0.29, 0.29]) {
    const wheel = box(dynamicLayer, state.robot, 0.11, 0.18, 0.58, '#111c2a', 0.17);
    wheel.position.x += side;
  }
  if (selectedCell && inBounds(selectedCell)) {
    const outline = new THREE.Mesh(new THREE.RingGeometry(0.39, 0.46, 4), new THREE.MeshBasicMaterial({ color: '#ffffff', side: THREE.DoubleSide, depthTest: false }));
    outline.rotation.set(-Math.PI / 2, 0, Math.PI / 4);
    outline.position.copy(position(selectedCell, 0.85));
    outline.renderOrder = 10;
    dynamicLayer.add(outline);
  }
}

function recenter(): void {
  if (!camera || !controls) return;
  const extent = state ? Math.max(state.scene.height, state.scene.width / Math.max(camera.aspect, 0.25)) : 20;
  const distance = extent * 1.48;
  camera.position.set(0, distance, distance * 0.35);
  camera.near = 0.1;
  camera.far = Math.max(1000, distance * 10);
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.maxDistance = distance * 3;
  controls.minDistance = Math.max(3, extent * 0.25);
  controls.update();
}

function setupRenderer(): void {
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const canvas = renderer.domElement;
    canvas.setAttribute('aria-label', 'Synthetic scene: walls, robot, survivor proxies, hazards and planned route. Use the cell inspector below for keyboard access.');
    canvas.setAttribute('role', 'img');
    $('viewport').prepend(canvas);
    camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI / 2.4;
    controls.minPolarAngle = 0;
    const observer = new ResizeObserver(() => {
      const { width, height } = $('viewport').getBoundingClientRect();
      renderer!.setSize(width, height);
      camera!.aspect = width / Math.max(height, 1);
      camera!.updateProjectionMatrix();
    });
    observer.observe($('viewport'));
    recenter();
    renderer.setAnimationLoop(() => {
      if (document.hidden || !renderer || !camera) return;
      controls?.update();
      renderer.render(world, camera);
    });
    let down: { x: number; y: number } | null = null;
    canvas.addEventListener('pointerdown', event => { if (event.button === 0) down = { x: event.clientX, y: event.clientY }; });
    canvas.addEventListener('pointercancel', () => { down = null; });
    canvas.addEventListener('pointerup', event => {
      if (!down || event.button !== 0 || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) { down = null; return; }
      down = null;
      if (!state || !camera) return;
      const bounds = canvas.getBoundingClientRect();
      pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const point = new THREE.Vector3();
      if (!raycaster.ray.intersectPlane(groundPlane, point)) return;
      const cell: Cell = [Math.floor(point.x + state.scene.width / 2), Math.floor(point.z + state.scene.height / 2)];
      applyCell(cell);
    });
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      notify('The 3D graphics context was lost. Reload to restore the map. The keyboard cell editor remains available.', true);
    });
  } catch {
    renderer?.dispose();
    renderer = null;
    $('scene-empty').innerHTML = '<strong>3D rendering unavailable</strong><p>This browser could not initialize WebGL. You can still use mission controls and the keyboard cell inspector below.</p>';
  }
}

function inspectCell(cell: Cell): void {
  if (!state) return;
  const [x, y] = cell;
  const room = state.scene.rooms.find(r => r.cells.some(p => same(p, cell)));
  const labels = [state.scene.walls.some(p => same(p, cell)) ? 'Wall' : 'Floor', state.scene.exits.some(p => same(p, cell)) ? 'Exit' : '', room?.name].filter(Boolean).join(' · ');
  $('cell-readout').textContent = `(${x}, ${y}) ${labels} | Heat ${number(state.heat[y]?.[x], 3)} · Smoke ${number(state.smoke[y]?.[x], 3)} · ${state.observed[y]?.[x] > 0 ? 'Observed' : 'Unobserved'}`;
  ($('cell-x') as HTMLInputElement).value = String(x);
  ($('cell-y') as HTMLInputElement).value = String(y);
}

function applyCell(cell: Cell): void {
  if (!state || !inBounds(cell) || !cell.every(Number.isInteger)) { notify('Choose an integer cell coordinate within the scene.', true); return; }
  selectedCell = cell;
  inspectCell(cell);
  if (selectedTool !== 'inspect') {
    if (!idle() || busy || !draft) { notify('Scenario editing is available only while idle and connected.', true); return; }
    if (state.scene.walls.some(p => same(p, cell))) { notify('Choose a floor cell; walls cannot contain survivors or ignitions.', true); return; }
    if (selectedTool === 'remove') {
      draft.survivors = draft.survivors.filter(p => !same(p, cell));
      draft.ignitions = draft.ignitions.filter(p => !same(p, cell));
    } else {
      const target = selectedTool === 'survivor' ? draft.survivors : draft.ignitions;
      if (!target.some(p => same(p, cell))) target.push(cell);
    }
    dirty = JSON.stringify(draft) !== JSON.stringify(state.scenario);
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

for (const action of ['start', 'pause', 'reset'] as const) {
  $(action).addEventListener('click', () => void mutation(async () => {
    if (action === 'start' && dirty) throw new Error('Apply or discard your scenario changes first.');
    const mode = document.querySelector<HTMLInputElement>('input[name="mode"]:checked')!.value as Mode;
    await api('control', action === 'start' ? { action, mode } : { action });
    if (action === 'reset') { dirty = false; draft = null; }
    notify(action === 'reset' ? 'Reset requested. Waiting for the backend state.' : `${action === 'start' ? 'Start' : 'Pause'} requested.`);
  }));
}

document.querySelectorAll<HTMLInputElement>('input[name="tool"]').forEach(input => input.addEventListener('change', () => {
  selectedTool = input.value as Tool;
  $('viewport').classList.toggle('editing', selectedTool !== 'inspect');
  syncUI();
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
$('apply').addEventListener('click', () => void mutation(async () => {
  if (!idle()) throw new Error('The backend must be idle before applying a scenario.');
  const scenario = validateScenario(draft);
  await api('scenario', scenario);
  dirty = false;
  notify('Scenario applied to the backend.');
}));
$('discard').addEventListener('click', () => {
  if (!state) return;
  draft = copyScenario(state.scenario);
  dirty = false;
  ($('seed') as HTMLInputElement).value = String(draft.seed);
  drawDynamic();
  syncUI();
  notify('Local changes discarded.');
});
$('export').addEventListener('click', () => {
  try {
    const scenario = validateScenario(draft);
    const url = URL.createObjectURL(new Blob([JSON.stringify(scenario, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `rescuetwin-scenario-${scenario.seed}.json`;
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
    syncUI();
    notify(`Loaded ${file.name}. Review the map, then apply the scenario.`);
  } catch (error) { notify(`Could not load scenario: ${(error as Error).message}`, true); }
});

// The paired-report schema is deliberately open: render backend keys and values
// without inventing metrics, strategy outcomes, or a winner.
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
  comparisonAbort = new AbortController();
  const timeout = window.setTimeout(() => comparisonAbort?.abort(), 120000);
  $('comparison').hidden = false;
  $('comparison-body').textContent = 'Computing paired report on the backend…';
  $('comparison-body').setAttribute('aria-busy', 'true');
  $('comparison-context').textContent = `Requested for scene ${state!.scene.name}, seed ${state!.scenario.seed}. Results remain a snapshot of this request.`;
  try {
    const report = await api<unknown>('compare', {}, comparisonAbort.signal);
    if (report === undefined) throw new Error('The backend returned an empty comparison report.');
    $('comparison-body').innerHTML = renderReport(report);
    $('comparison').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'nearest' });
    notify('Paired strategy report received from the backend.');
  } catch (error) {
    $('comparison-body').textContent = 'Comparison did not complete. You can request another report.';
    throw error;
  } finally {
    clearTimeout(timeout);
    comparisonAbort = null;
    $('comparison-body').removeAttribute('aria-busy');
  }
}));
$('close-comparison').addEventListener('click', () => { $('comparison').hidden = true; });
$('recenter').addEventListener('click', recenter);
for (const name of ['heat', 'smoke', 'observed', 'route'] as const) {
  $(`${name}-layer`).addEventListener('change', () => { layers[name] = ($(`${name}-layer`) as HTMLInputElement).checked; drawDynamic(); });
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });
setupRenderer();
syncUI();
void poll();
window.setInterval(() => void poll(), 200);
