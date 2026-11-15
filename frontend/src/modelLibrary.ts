import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { MTLLoader } from 'three/addons/loaders/MTLLoader.js';

interface PreparedModel {
  id: string; name: string; status: 'simulation-ready' | 'preview-only'; scene_id: string | null;
  file: string; materials: string[]; textured: boolean; transform: number[][] | null; source: string; reason: string;
}

/** A separate viewer keeps unverified geometry out of the robot's simulation and sensors. */
export function setupModelLibrary(openScene: (id: string) => Promise<void>): void {
  document.body.insertAdjacentHTML('beforeend', `
    <dialog id="model-library" aria-labelledby="model-library-title">
      <div class="model-library-header"><h2 id="model-library-title">Prepared models</h2><button id="model-library-close" class="button small">Close</button></div>
      <label for="model-library-select">Model <select id="model-library-select"></select></label>
      <p id="model-library-info" class="field-hint" role="status"></p>
      <div id="model-library-view" aria-label="Prepared scan preview"></div>
      <div class="model-library-actions"><button id="model-library-fit" class="button small">Fit model</button>
        <label><input id="model-library-cutaway" type="checkbox" checked /> Cutaway</label>
        <button id="model-library-use" class="button small">Use in simulation</button></div>
      <p class="field-hint">Drag to orbit · Scroll to zoom. Geometry previews may be incomplete or contain multiple floors. Previewing does not change the active mission.</p>
    </dialog>`);
  const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const dialog = element<HTMLDialogElement>('model-library');
  const select = element<HTMLSelectElement>('model-library-select');
  const info = element<HTMLParagraphElement>('model-library-info');
  const use = element<HTMLButtonElement>('model-library-use');
  const host = element<HTMLDivElement>('model-library-view');
  let models: PreparedModel[] = [], version = 0;
  let renderer: THREE.WebGLRenderer | null = null, controls: OrbitControls | null = null;
  let camera: THREE.PerspectiveCamera | null = null, object: THREE.Group | null = null;
  let loaded = false;
  const world = new THREE.Scene();
  world.background = new THREE.Color('#0d151f');
  world.add(new THREE.HemisphereLight('#ffffff', '#687c90', 2.4));
  const light = new THREE.DirectionalLight('#ffffff', 2); light.position.set(5, 10, 4); world.add(light);
  const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1.6);

  function dispose(group: THREE.Group): void {
    group.traverse(o => {
      if (!(o instanceof THREE.Mesh)) return;
      o.geometry.dispose();
      for (const material of Array.isArray(o.material) ? o.material : [o.material]) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
        material.dispose();
      }
    });
  }
  function clear(): void { if (object) { world.remove(object); dispose(object); object = null; } }
  function fit(): void {
    if (!object || !camera || !controls) return;
    const box = new THREE.Box3().setFromObject(object), center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
    const angle = Math.min(camera.fov * Math.PI / 360, Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect));
    const distance = radius / Math.sin(angle) * 1.1;
    camera.position.copy(center).addScaledVector(new THREE.Vector3(.6, .9, 1).normalize(), distance);
    camera.far = Math.max(distance * 10, 1000); camera.updateProjectionMatrix();
    controls.target.copy(center); controls.maxDistance = distance * 5; controls.update();
    // Validated bundles normalize the navigation floor to z=0; scans may include
    // a balcony underside far below it, which must not set the clipping height.
    clip.constant = models.find(m => m.id === select.value)?.scene_id ? 1.6 : box.min.y + 1.6;
  }
  function resize(): void {
    if (!renderer || !camera) return;
    renderer.setSize(host.clientWidth, host.clientHeight);
    camera.aspect = host.clientWidth / Math.max(host.clientHeight, 1); camera.updateProjectionMatrix();
  }
  async function show(): Promise<void> {
    const current = models.find(m => m.id === select.value);
    const request = ++version;
    clear(); loaded = false; use.disabled = true;
    if (!current) return;
    info.textContent = `Loading ${current.name}…`;
    const base = `/api/models/${encodeURIComponent(current.id)}/files/`;
    let pending: THREE.Group | null = null;
    let materials: ReturnType<MTLLoader['parse']> | null = null;
    try {
      let failed = false;
      const manager = new THREE.LoadingManager();
      let finish!: () => void;
      const assetsDone = new Promise<void>(resolve => { finish = resolve; });
      manager.onLoad = () => finish(); manager.onError = () => { failed = true; };
      const loader = new OBJLoader(manager);
      if (current.materials.length) {
        const texts = await Promise.all(current.materials.map(async name => {
          const response = await fetch(base + encodeURIComponent(name));
          if (!response.ok) throw new Error('Material unavailable');
          return response.text();
        }));
        materials = new MTLLoader(manager).parse(texts.join('\n'), base);
        // Start texture requests after OBJ loading so onLoad covers the whole batch.
        loader.setMaterials(materials);
      }
      pending = await loader.loadAsync(base + encodeURIComponent(current.file));
      await assetsDone;
      if (failed) throw new Error('A model asset failed to load');
      if (request !== version || !dialog.open) { dispose(pending); return; }
      pending.traverse(o => {
        if (!(o instanceof THREE.Mesh)) return;
        o.geometry.computeVertexNormals();
        const convert = (m: THREE.Material) => {
          const map = current.textured ? (m as THREE.MeshPhongMaterial).map : null;
          if (map) map.colorSpace = THREE.SRGBColorSpace;
          const next = map ? new THREE.MeshBasicMaterial({ map, side: THREE.DoubleSide })
            : new THREE.MeshLambertMaterial({ color: '#d9d2c6', flatShading: true, side: THREE.DoubleSide });
          next.clippingPlanes = element<HTMLInputElement>('model-library-cutaway').checked ? [clip] : [];
          m.dispose(); return next;
        };
        o.material = Array.isArray(o.material) ? o.material.map(convert) : convert(o.material);
      });
      if (current.transform) pending.applyMatrix4(new THREE.Matrix4().fromArray(current.transform.flat()).transpose());
      pending.applyMatrix4(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
      object = pending; world.add(object); fit(); loaded = true;
      use.disabled = !current.scene_id;
      info.textContent = `${current.name} · ${current.id} · ${current.status === 'simulation-ready' ? 'Simulation ready' : 'Preview only'} · ${current.textured ? 'Textured scan' : 'Untextured geometry'}. ${current.reason}`;
      host.dataset.loadedModel = current.id;
    } catch (error) {
      if (pending) dispose(pending);
      else if (materials) for (const material of Object.values(materials.materials)) material.dispose();
      if (request === version) info.textContent = `Unable to preview ${current.id}: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  element<HTMLButtonElement>('prepared-models').addEventListener('click', async () => {
    const request = ++version;
    dialog.showModal(); info.textContent = 'Loading prepared model catalog…'; use.disabled = true;
    select.disabled = true; select.replaceChildren();
    clear(); loaded = false; delete host.dataset.loadedModel;
    try {
      if (!renderer) {
        renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.localClippingEnabled = true;
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); host.append(renderer.domElement);
        camera = new THREE.PerspectiveCamera(45, 1, .05, 1000);
        controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
        renderer.setAnimationLoop(() => { if (dialog.open) { controls!.update(); renderer!.render(world, camera!); } });
        new ResizeObserver(resize).observe(host);
      }
      resize();
      const response = await fetch('/api/models');
      if (!response.ok) throw new Error('Model catalog unavailable');
      const next: PreparedModel[] = await response.json();
      if (!dialog.open || request !== version) return;
      models = next;
      select.replaceChildren(...models.map(m => new Option(`${m.name} · ${m.status === 'simulation-ready' ? 'Simulation ready' : 'Preview only'}`, m.id)));
      select.disabled = false;
      const ready = models.filter(m => m.scene_id).length;
      element<HTMLHeadingElement>('model-library-title').textContent = `Prepared models (${models.length}) · ${ready} simulation ready`;
      if (models.length) await show(); else info.textContent = 'No prepared models found.';
    } catch (error) { info.textContent = error instanceof Error ? error.message : String(error); }
  });
  select.addEventListener('change', () => { delete host.dataset.loadedModel; void show(); });
  element<HTMLButtonElement>('model-library-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { ++version; clear(); delete host.dataset.loadedModel; loaded = false; });
  element<HTMLButtonElement>('model-library-fit').addEventListener('click', fit);
  element<HTMLInputElement>('model-library-cutaway').addEventListener('change', event => {
    object?.traverse(o => {
      if (o instanceof THREE.Mesh) for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        m.clippingPlanes = (event.target as HTMLInputElement).checked ? [clip] : []; m.needsUpdate = true;
      }
    });
  });
  use.addEventListener('click', async () => {
    const current = models.find(m => m.id === select.value);
    if (!loaded || !current?.scene_id) return;
    use.disabled = true;
    try { await openScene(current.scene_id); dialog.close(); }
    catch (error) { info.textContent = error instanceof Error ? error.message : String(error); use.disabled = false; }
  });
}
