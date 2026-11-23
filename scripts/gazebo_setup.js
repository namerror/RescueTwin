export function mapCell(x, y, viewport, grid) {
  const column = Math.floor((x - viewport.left) * grid.width / viewport.width);
  const screenRow = Math.floor((y - viewport.top) * grid.height / viewport.height);
  if (column < 0 || screenRow < 0 || column >= grid.width || screenRow >= grid.height) return null;
  return [column, grid.height - 1 - screenRow];
}

if (typeof document !== 'undefined') {
  const byId = (id) => document.getElementById(id);
  const floorList = byId('floor');
  const map = byId('map');
  const ctx = map.getContext('2d');
  const launch = byId('launch');
  const stop = byId('stop');
  const clear = byId('clear');
  const demo = byId('demo');
  let floors = [];
  let floor;
  let cursor;
  let busy = false;
  let pending = false;
  let ignitions = new Map();

  async function request(path, body) {
    const response = await fetch(path, body === undefined ? undefined : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }

  function draw() {
    if (!floor) return;
    const {width, height} = floor.grid;
    ctx.setTransform(map.width / width, 0, 0, map.height / height, 0, 0);
    ctx.fillStyle = '#788e88';
    ctx.fillRect(0, 0, width, height);
    if (map.getBoundingClientRect().width / width >= 7) {
      ctx.strokeStyle = '#a8bab455';
      ctx.lineWidth = .035;
      ctx.beginPath();
      for (let x = 1; x < width; x++) { ctx.moveTo(x, 0); ctx.lineTo(x, height); }
      for (let y = 1; y < height; y++) { ctx.moveTo(0, y); ctx.lineTo(width, y); }
      ctx.stroke();
    }
    ctx.fillStyle = '#374751';
    for (const [x, y] of floor.blocked) ctx.fillRect(x, height - 1 - y, 1, 1);
    function marker([x, y], color) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x + .5, height - .5 - y, .38, 0, 2 * Math.PI);
      ctx.fill();
    }
    for (const exit of floor.exits) marker(exit, '#84dbaa');
    for (const ignition of ignitions.values()) marker(ignition, '#ff8d60');
    marker(floor.spawn, '#73cfe5');
    ctx.save();
    ctx.translate(floor.spawn[0] + .5, height - .5 - floor.spawn[1]);
    ctx.rotate(-floor.yaw);
    ctx.fillStyle = '#17333a';
    ctx.beginPath();
    ctx.moveTo(.33, 0);
    ctx.lineTo(-.22, -.17);
    ctx.lineTo(-.22, .17);
    ctx.fill();
    ctx.restore();
    if (document.activeElement === map && cursor) {
      ctx.strokeStyle = '#fff4d4';
      ctx.lineWidth = .13;
      ctx.strokeRect(cursor[0] + .07, height - .93 - cursor[1], .86, .86);
    }
    byId('count').textContent = `${ignitions.size} ignition${ignitions.size === 1 ? '' : 's'}`;
  }

  function controls() {
    const locked = busy || pending;
    floorList.disabled = locked || !floor;
    launch.disabled = locked || !floor;
    stop.hidden = !busy;
    stop.disabled = pending;
    demo.hidden = !floor?.preset;
    demo.disabled = locked;
    clear.disabled = locked || !floor;
    map.setAttribute('aria-disabled', String(locked));
  }

  function placement(message, error = false) {
    const element = byId('placement');
    element.textContent = message;
    element.classList.toggle('error', error);
  }

  function choose() {
    floor = floors.find((item) => item.id === floorList.value);
    if (!floor) return;
    floor.blockedKeys = new Set(floor.blocked.map(String));
    floor.exitKeys = new Set(floor.exits.map(String));
    cursor = [...floor.spawn];
    ignitions = new Map(floor.ignitions.map((cell) => [String(cell), cell]));
    const {width, height} = floor.grid;
    const scale = Math.min(48, 2200 / Math.max(width, height));
    map.width = Math.max(1, Math.round(width * scale));
    map.height = Math.max(1, Math.round(height * scale));
    // Bound the initial view by height as well as width and allow scrolling on small screens.
    map.style.width = `min(100%, ${Math.min(1024, 440 * width / height)}px)`;
    map.style.minWidth = `${Math.min(1024, width * 5)}px`;
    placement('');
    controls();
    draw();
  }

  function toggle(cell) {
    if (!cell || busy || pending) return;
    cursor = cell;
    const key = String(cell);
    if (key === String(floor.spawn)) placement('Choose a cell away from the robot spawn.', true);
    else if (floor.exitKeys.has(key)) placement('Keep the exit clear.', true);
    else if (floor.blockedKeys.has(key)) placement('Choose an open floor cell.', true);
    else if (ignitions.delete(key)) placement('Ignition removed.');
    else if (ignitions.size === 20) placement('Use up to 20 ignition locations.', true);
    else {
      ignitions.set(key, cell);
      placement('Ignition added.');
    }
    draw();
  }

  function showStatus(state) {
    busy = ['starting', 'running', 'stopping'].includes(state.phase);
    const status = byId('status');
    if (status.textContent !== state.message) status.textContent = state.message;
    status.classList.toggle('error', state.phase === 'error');
    const client = byId('client');
    client.hidden = !state.client;
    client.querySelector('code').textContent = state.client ?? '';
    controls();
  }

  async function submit(path, body) {
    pending = true;
    placement('');
    controls();
    try {
      showStatus(await request(path, body));
      if (path === '/api/launch') floor.ignitions = [...ignitions.values()];
    } catch (error) {
      placement(error.message, true);
    } finally {
      pending = false;
      controls();
    }
  }

  map.addEventListener('click', (event) => {
    if (floor) toggle(mapCell(event.clientX, event.clientY, map.getBoundingClientRect(), floor.grid));
  });
  map.addEventListener('keydown', (event) => {
    if (!floor || busy || pending) return;
    if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      toggle(cursor);
      return;
    }
    const move = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]}[event.key];
    if (!move) return;
    event.preventDefault();
    cursor = cursor.map((value, axis) => Math.max(0, Math.min(value + move[axis],
      (axis === 0 ? floor.grid.width : floor.grid.height) - 1)));
    const key = String(cursor);
    const kind = key === String(floor.spawn) ? 'Robot spawn' : floor.exitKeys.has(key) ? 'Exit'
      : floor.blockedKeys.has(key) ? 'Blocked cell' : ignitions.has(key) ? 'Ignition' : 'Open floor cell';
    placement(`${kind} · column ${cursor[0]}, row ${cursor[1]}`);
    draw();
  });
  map.addEventListener('focus', draw);
  map.addEventListener('blur', draw);
  floorList.addEventListener('change', choose);
  clear.addEventListener('click', () => { ignitions.clear(); placement(''); draw(); });
  demo.addEventListener('click', () => {
    ignitions = new Map(floor.preset.map((cell) => [String(cell), cell]));
    placement('');
    draw();
  });
  stop.addEventListener('click', () => void submit('/api/stop', {}));
  launch.addEventListener('click', () => void submit('/api/launch', {
    floor: floor.id, revision: floor.revision, ignitions: [...ignitions.values()],
  }));
  async function poll() {
    if (!pending) {
      try {
        const status = await request('/api/status');
        if (!pending) showStatus(status);
      }
      catch { showStatus({phase: 'error', message: 'The local setup process is unavailable.'}); }
    }
    window.setTimeout(poll, 750);
  }
  request('/api/floors').then((catalog) => {
    floors = catalog.floors;
    for (const item of floors) floorList.add(new Option(item.name, item.id));
    floorList.value = catalog.selected ?? 'public';
    byId('private-help').hidden = floors.length > 1;
    choose();
    void poll();
  }).catch((error) => placement(error.message, true));
}
