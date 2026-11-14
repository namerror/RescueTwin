import * as THREE from 'three';

const vertex = `
  varying vec2 vUv;
  varying float vSeed;
  attribute float phase;
  uniform float clock;
  uniform float isSmoke;
  void main() {
    vUv = uv;
    vSeed = phase;
    vec4 center = modelViewMatrix * instanceMatrix * vec4(0., 0., 0., 1.);
    vec2 size = vec2(length(instanceMatrix[0].xyz), length(instanceMatrix[1].xyz));
    float sway = sin(clock * mix(4., 0.8, isSmoke) + phase * 20.);
    center.xy += position.xy * size + vec2(sway * size.x * 0.09, 0.);
    gl_Position = projectionMatrix * center;
  }
`;

const fragment = `
  varying vec2 vUv;
  varying float vSeed;
  uniform float clock;
  uniform float isSmoke;
  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f*f*(3.-2.*f);
    return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),
               mix(hash(i+vec2(0.,1.)),hash(i+1.),f.x),f.y);
  }
  void main() {
    vec2 uv = vUv;
    float t = clock * mix(2.5, 0.45, isSmoke);
    vec2 p = vec2(uv.x*5. + vSeed*30., uv.y*6. - t);
    float n = .65*noise(p) + .35*noise(p*2.1);
    if (isSmoke > .5) {
      vec2 q = uv*2.-1.;
      float density = 1. - smoothstep(.23, 1., length(q) + (n-.5)*.5);
      vec3 shade = mix(vec3(.13,.15,.17),vec3(.45,.49,.53),n);
      gl_FragColor = vec4(shade, density * (.20 + n*.25));
    } else {
      float tip = 1. - uv.y;
      float axis = abs(uv.x - .5 + (n-.5)*uv.y*.48);
      float shape = 1. - smoothstep(.06, .37, axis / max(.12, tip));
      float opacity = shape * smoothstep(0.,.13,uv.y) * (1.-smoothstep(.72,1.,uv.y));
      vec3 edge = mix(vec3(.95,.055,.006),vec3(1.,.38,.018),tip);
      vec3 core = vec3(1.,.86,.36);
      gl_FragColor = vec4(mix(edge, core, shape*tip*(.5+n*.5)), opacity);
    }
    #include <colorspace_fragment>
  }
`;

export function hazardSprites(capacity: number, smoke: boolean) {
  const geometry = new THREE.PlaneGeometry();
  const phases = Float32Array.from({ length: capacity }, (_, index) => (index * 0.61803398875) % 1);
  geometry.setAttribute('phase', new THREE.InstancedBufferAttribute(phases, 1));
  const material = new THREE.ShaderMaterial({
    vertexShader: vertex, fragmentShader: fragment,
    uniforms: { clock: { value: 0 }, isSmoke: { value: Number(smoke) } },
    transparent: true, depthWrite: false,
  });
  const mesh = new THREE.InstancedMesh(geometry, material, capacity);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.count = 0;
  mesh.frustumCulled = false;
  return mesh;
}
