import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

export const HUMAN_VARIANTS = ['adult-male','adult-female','child-male','child-female','older-male','older-female'] as const;
export type HumanVariant = typeof HUMAN_VARIANTS[number] | 'mixed';
interface Template { scene: THREE.Group; scale: number; offset: THREE.Vector3 }
const templates = new Map<string, Template>();
let loadGeneration=0;

export function validHumanVariant(value: unknown): value is HumanVariant {
  return value === 'mixed' || HUMAN_VARIANTS.includes(value as typeof HUMAN_VARIANTS[number]);
}

export function humanReady(value = 'adult-male'): boolean {
  return value === 'mixed' ? HUMAN_VARIANTS.every(id => templates.has(id)) : templates.has(value);
}

export async function loadHumans(changed: () => void, failed: (message: string) => void, base = "/models/", prefix = ""): Promise<void> {
  const generation=++loadGeneration;templates.clear();
  try {
    const response = await fetch(base+prefix+'humans.json');
    if (!response.ok) throw new Error('Human mesh manifest unavailable; run ./setup.sh.');
    const manifest: { variants: { id: string; file: string; height_m: number }[] } = await response.json();
    await Promise.allSettled(manifest.variants.map(async variant => {
      try {
        if (!HUMAN_VARIANTS.includes(variant.id as typeof HUMAN_VARIANTS[number]) || !/^[A-Za-z]+\.glb$/.test(variant.file) || !(variant.height_m > 0)) throw new Error('Invalid human mesh manifest');
        const gltf = await new GLTFLoader().loadAsync(base+prefix+variant.file);
        gltf.scene.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(gltf.scene, true);
        const scale = variant.height_m / Math.max(box.max.y-box.min.y,1e-3);
        if (generation!==loadGeneration) return;
        templates.set(variant.id,{ scene:gltf.scene,scale,
          offset:new THREE.Vector3(-(box.min.x+box.max.x)/2,-box.min.y,-(box.min.z+box.max.z)/2).multiplyScalar(scale) });
        changed();
      } catch { failed(`Human mesh ${variant.id} unavailable. Run ./setup.sh before using this appearance.`); }
    }));
  } catch (error) { failed(error instanceof Error ? error.message : String(error)); }
}

export function makeHuman(id: string, appearance: HumanVariant = 'adult-male'): THREE.Group | null {
  const index = Number(id.replace(/\D/g,'')) || 0;
  const variant = appearance === 'mixed' ? HUMAN_VARIANTS[index % HUMAN_VARIANTS.length] : appearance;
  const template = templates.get(variant);
  if (!template) return null;
  const body = SkeletonUtils.clone(template.scene) as THREE.Group;
  body.traverse(o => { if (o instanceof THREE.Mesh) o.frustumCulled=false; });
  const normalized = new THREE.Group(); normalized.scale.setScalar(template.scale);
  normalized.position.copy(template.offset); normalized.add(body);
  const group = new THREE.Group(); group.add(normalized); group.userData.id=id; group.userData.humanVariant=variant;
  return group;
}
