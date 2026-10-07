import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export const DUR = 60;
const BEAT = 0.5;
const $ = (id) => document.getElementById(id);
const cl = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const P = (t, a, b) => cl((t - a) / (b - a));
const lerp = (a, b, k) => a + (b - a) * k;
const E = {
  out: (x) => 1 - Math.pow(1 - x, 3),
  out5: (x) => 1 - Math.pow(1 - x, 5),
  io: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  back: (x) => { const c1 = 2.2, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  el: (x) => (x === 0 || x === 1 ? x : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI) / 3) + 1),
};
const pulse = (t, at, d = 8) => (t >= at ? Math.exp(-(t - at) * d) : 0);
const C = { w: '#ffffff', g: '#202020', d: '#141414', o: '#ff682c', iv: '#ebe6dd', mist: '#efefef' };
const tr = ({ x = 0, y = 0, s = 1, sx = 1, sy = 1, r = 0, o = 1 } = {}) =>
  `opacity:${o};transform:translate(${x}px,${y}px) rotate(${r}deg) scale(${s * sx},${s * sy})`;
function rng(seed) { return () => ((seed = (seed * 16807) % 2147483647) / 2147483647); }
const ICON = {
  orbit: 'M12 3a9 9 0 1 0 9 9M21 3v6h-6', layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
  finish: 'M12 3a9 9 0 1 0 0 18c1 0 2-1 1.5-2-.6-1.2.3-2.5 1.6-2.5H17a4 4 0 0 0 4-4c0-5-4-9.5-9-9.5zM7.5 11h.01M10 7h.01M15 7.5h.01',
  section: 'M4 20L20 4M4 4h16v16H4z', measure: 'M3 17L17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2',
  label: 'M20 12l-8 8-9-9V3h8zM7.5 7.5h.01', light: 'M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6L4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  scenes: 'M4 5h16v11H4zM8 20h8M12 16v4', slice: 'M3 12h18M7 7c3 2 7 2 10 0M7 17c3-2 7-2 10 0',
  image: 'M3 5h18v14H3zM3 15l5-5 5 5 3-3 5 5M15 9h.01', ar: 'M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2',
  qr: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM15 15h2M19 15v4M15 19h4',
};
const TOOLS = Object.keys(ICON);
const icon = (d, s = 28) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;

/* ================================================================ 3D */
const G = {};
let QR = null;
function shadowTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const g = c.getContext('2d'); const gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  gr.addColorStop(0, 'rgba(0,0,0,0.5)'); gr.addColorStop(0.6, 'rgba(0,0,0,0.16)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256); return new THREE.CanvasTexture(c);
}
async function init3D() {
  const gltf = await new GLTFLoader().loadAsync('assets/calcaneus.glb');
  let src; gltf.scene.traverse((o) => { if (o.isMesh && !src) src = o; });
  src.updateMatrixWorld(true);
  const g = src.geometry.clone(); g.applyMatrix4(src.matrixWorld);
  g.deleteAttribute('color'); g.computeBoundingBox();
  const bb = g.boundingBox, c = bb.getCenter(new THREE.Vector3()), sz = bb.getSize(new THREE.Vector3());
  g.translate(-c.x, -c.y, -c.z);
  const ax = [0, 1, 2].sort((a, b) => [sz.x, sz.y, sz.z][b] - [sz.x, sz.y, sz.z][a]);
  const rows = ax.map((i) => [0, 1, 2].map((j) => (j === i ? 1 : 0)));
  const m = new THREE.Matrix4().set(...rows[0], 0, ...rows[1], 0, ...rows[2], 0, 0, 0, 0, 1);
  if (m.determinant() < 0) m.elements[2] *= -1, m.elements[6] *= -1, m.elements[10] *= -1;
  g.applyMatrix4(m);
  const s = 2 / Math.max(sz.x, sz.y, sz.z); g.scale(s, s, s);
  if (!g.attributes.normal) g.computeVertexNormals();
  g.computeBoundingBox(); G.geo = g; const B = g.boundingBox; G.B = B;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1); renderer.setSize(1920, 1080);
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.localClippingEnabled = true; $('gl').appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(30, 1920 / 1080, 0.1, 100);
  const head = new THREE.DirectionalLight(0xffffff, 0.9); head.position.set(-1, 1.5, 0); camera.add(head); scene.add(camera);
  const key = new THREE.DirectionalLight(0xffffff, 1.3); key.position.set(3, 5, 4); scene.add(key);
  Object.assign(G, { renderer, scene, camera, head, key });
  const root = new THREE.Group(); scene.add(root); G.root = root;

  G.plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 9);
  const clip = [G.plane];
  G.boneMat = new THREE.MeshPhysicalMaterial({ color: 0xe6dfd0, roughness: 0.55, clippingPlanes: clip, transparent: true, thickness: 0.6, ior: 1.45 });
  G.capMat = new THREE.MeshStandardMaterial({ color: 0xff682c, roughness: 0.85, side: THREE.BackSide, clippingPlanes: clip, emissive: 0x7a2a0a, emissiveIntensity: 0.7 });
  G.bone = new THREE.Mesh(g, G.boneMat); G.bone.renderOrder = 2;
  G.cap = new THREE.Mesh(g, G.capMat); G.cap.renderOrder = 1;
  G.shadowMat = new THREE.MeshBasicMaterial({ map: shadowTex(), transparent: true, depthWrite: false });
  const sh = new THREE.Mesh(new THREE.PlaneGeometry(3.6, 2.4), G.shadowMat); sh.rotation.x = -Math.PI / 2; sh.position.y = B.min.y - 0.05;
  root.add(G.bone, G.cap, sh); root.updateMatrixWorld(true);
  const rc = new THREE.Raycaster();
  const hits = (o, d) => { rc.set(o, d); return rc.intersectObject(G.bone, false); };
  const screwMat = new THREE.MeshStandardMaterial({ color: 0xff682c, metalness: 0.35, roughness: 0.4, clippingPlanes: clip });
  const plateMat = new THREE.MeshStandardMaterial({ color: 0x5a5a5a, metalness: 0.8, roughness: 0.35, clippingPlanes: clip });
  const screws = []; let plateZ = -9, plateY = 0;
  for (const x of [-0.52, -0.2, 0.12, 0.44]) for (const y of [0, 0.12, -0.12, 0.24, -0.24]) {
    const h = hits(new THREE.Vector3(x, y, 5), new THREE.Vector3(0, 0, -1));
    if (h.length >= 2) { screws.push({ x, y, z2: h[h.length - 1].point.z, z1: h[0].point.z }); break; }
  }
  screws.forEach((sc) => { plateZ = Math.max(plateZ, sc.z1); plateY += sc.y / screws.length; }); plateZ += 0.03;
  G.screws = screws.map((sc) => {
    const grp = new THREE.Group(); const len = plateZ - sc.z2 - 0.08;
    const cyl = new THREE.Mesh(new THREE.CylinderGeometry(0.034, 0.03, len, 20), screwMat); cyl.rotation.x = Math.PI / 2; cyl.position.set(sc.x, plateY, plateZ - len / 2);
    const hd = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.045, 24), screwMat); hd.rotation.x = Math.PI / 2; hd.position.set(sc.x, plateY, plateZ + 0.035);
    grp.add(cyl, hd); root.add(grp); return grp;
  });
  G.plateG = new THREE.Group();
  if (screws.length) {
    const x0 = screws[0].x - 0.14, x1 = screws[screws.length - 1].x + 0.14;
    const pl = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, 0.22, 0.035), plateMat); pl.position.set((x0 + x1) / 2, plateY, plateZ); G.plateG.add(pl);
  }
  root.add(G.plateG);
  G.guideMat = new THREE.MeshBasicMaterial({ color: 0xff682c, transparent: true, opacity: 0.14, side: THREE.DoubleSide, depthWrite: false });
  G.edgeMat = new THREE.LineBasicMaterial({ color: 0xff682c, transparent: true });
  const pg = new THREE.PlaneGeometry(2.0, 2.0);
  G.guide = new THREE.Group(); G.guide.add(new THREE.Mesh(pg, G.guideMat), new THREE.LineSegments(new THREE.EdgesGeometry(pg), G.edgeMat)); root.add(G.guide);
  const surf = (x, z, dirY = -1) => {
    for (const dz of [0, 0.1, -0.1, 0.2, -0.2, 0.3]) { const h = hits(new THREE.Vector3(x, 5 * -dirY, z + dz), new THREE.Vector3(0, dirY, 0)); if (h.length) return h[0].point.clone(); }
    return new THREE.Vector3(x, B.max.y, z);
  };
  G.m1 = [surf(-0.8, 0), surf(0.82, 0)];
  G.m2 = [surf(-0.3, 0.2), surf(0.3, 0.25)];
  G.pins = [['Posterior facet', surf(0.15, -0.1)], ['Sustentaculum tali', surf(-0.55, -0.25)], ['Lateral plate', new THREE.Vector3(screws[1] ? screws[1].x + 0.15 : 0, plateY + 0.11, plateZ + 0.03)],
    ['Screw 3 · 42 mm', new THREE.Vector3(screws[2] ? screws[2].x : 0.1, plateY, plateZ + 0.06)]];
  // positions for slice contours
  const pos = g.attributes.position.array, idx = g.index ? g.index.array : null;
  G.tri = { pos, idx, n: idx ? idx.length / 3 : pos.length / 9 };
}
const FIN = {
  Matte: { color: 0xe6dfd0, roughness: 0.95, metalness: 0, clearcoat: 0, transmission: 0 },
  Plastic: { color: 0xece6da, roughness: 0.42, metalness: 0, clearcoat: 0.3, transmission: 0 },
  Ceramic: { color: 0xf4f1ea, roughness: 0.18, metalness: 0, clearcoat: 1, transmission: 0 },
  Glossy: { color: 0xe6dfd0, roughness: 0.06, metalness: 0, clearcoat: 1, transmission: 0 },
  Metal: { color: 0xb9b9b9, roughness: 0.32, metalness: 1, clearcoat: 0, transmission: 0 },
  Gold: { color: 0xe7b65a, roughness: 0.22, metalness: 1, clearcoat: 0, transmission: 0 },
  Chrome: { color: 0xffffff, roughness: 0.03, metalness: 1, clearcoat: 0, transmission: 0 },
  Glass: { color: 0xffe6dc, roughness: 0.04, metalness: 0, clearcoat: 1, transmission: 0.95 },
  Default: { color: 0xe6dfd0, roughness: 0.55, metalness: 0, clearcoat: 0, transmission: 0 },
};
const LIGHT = {
  Studio: { key: 1.3, head: 0.9, env: 1.0, exp: 1.0, sh: 1 },
  Soft: { key: 0.5, head: 0.7, env: 1.3, exp: 1.1, sh: 0.5 },
  Dramatic: { key: 3.2, head: 0.1, env: 0.25, exp: 0.8, sh: 1.6, side: true },
  Bright: { key: 1.6, head: 1.2, env: 1.4, exp: 1.5, sh: 0.8 },
  Flat: { key: 0, head: 0.3, env: 1.6, exp: 1.0, sh: 0 },
  Dim: { key: 0.8, head: 0.3, env: 0.5, exp: 0.55, sh: 1.2 },
};
function default3D() {
  return { on: false, az: 0.6, el: 0.35, r: 5.2, ty: -0.05, offx: 0, offy: 0, fov: 30, rotY: 0, op: 1, fin: 'Default', boneColor: null, light: 'Studio',
    explode: 0, screwStag: null, clipAxis: 'x', clip: 9, cap: false, guide: 0, implants: true, scale: 1, spinX: 0 };
}
function apply3D(S) {
  const { renderer, camera, root } = G;
  renderer.domElement.style.display = S.on ? 'block' : 'none';
  if (!S.on) return;
  const f = FIN[S.fin] || FIN.Default, m = G.boneMat;
  m.color.setHex(S.boneColor ?? f.color); m.roughness = f.roughness; m.metalness = f.metalness; m.clearcoat = f.clearcoat; m.transmission = f.transmission;
  m.opacity = S.op; m.depthWrite = S.op > 0.99;
  const tp = S.op < 0.999 && f.transmission === 0; if (m.transparent !== tp) { m.transparent = tp; m.needsUpdate = true; }
  const L = LIGHT[S.light];
  G.key.intensity = L.key; G.head.intensity = L.head; G.scene.environmentIntensity = L.env; renderer.toneMappingExposure = L.exp;
  G.key.position.set(L.side ? 6 : 3, L.side ? 2 : 5, L.side ? -1 : 4); G.shadowMat.opacity = Math.min(1, L.sh);
  root.rotation.set(S.spinX, S.rotY, 0); root.scale.setScalar(S.scale);
  G.plateG.visible = S.implants; G.plateG.position.z = S.explode * 0.7;
  G.screws.forEach((sc, i) => { sc.visible = S.implants; sc.position.z = (S.screwStag ? S.screwStag[i] : S.explode) * 1.35; });
  const n = { x: [-1, 0, 0], y: [0, -1, 0], z: [0, 0, -1] }[S.clipAxis];
  G.plane.normal.set(...n); G.plane.constant = S.clip;
  G.cap.visible = S.cap;
  G.guide.visible = S.guide > 0; G.guideMat.opacity = 0.16 * S.guide; G.edgeMat.opacity = S.guide;
  G.guide.rotation.set(S.clipAxis === 'y' ? Math.PI / 2 : 0, S.clipAxis === 'x' ? Math.PI / 2 : 0, 0);
  const gp = Math.min(S.clip, 2); G.guide.position.set(S.clipAxis === 'x' ? gp : 0, S.clipAxis === 'y' ? gp : 0, S.clipAxis === 'z' ? gp : 0);
  G.guide.scale.set(S.clipAxis === 'x' ? 1.1 : 1.6, S.clipAxis === 'y' ? 1.1 : 1.0, 1);
  camera.fov = S.fov; camera.updateProjectionMatrix();
  camera.position.set(S.r * Math.cos(S.el) * Math.sin(S.az), S.r * Math.sin(S.el) + S.ty, S.r * Math.cos(S.el) * Math.cos(S.az));
  camera.lookAt(0, S.ty, 0);
  camera.setViewOffset(1920, 1080, S.offx, S.offy, 1920, 1080);
  camera.updateMatrixWorld(); root.updateMatrixWorld(true);
}
const proj = (v) => { const p = v.clone().applyMatrix4(G.root.matrixWorld).project(G.camera); return { x: (p.x + 1) / 2 * 1920, y: (1 - p.y) / 2 * 1080, z: p.z }; };

/* ================================================================ persistent DOM */
const PD = {};
function setupPersistent() {
  const ui = $('fx').parentElement;
  const mk = (html, z) => { const d = document.createElement('div'); d.className = 'L'; d.style.zIndex = z; d.innerHTML = html; d.style.display = 'none'; ui.appendChild(d); return d; };
  PD.slice = mk('<canvas id="slc" width="760" height="760" style="position:absolute;left:1060px;top:180px"></canvas>', 3);
  PD.phone = mk(`<div id="ph" style="position:absolute;left:880px;top:90px;width:484px;height:860px;transform-origin:50% 50%">
    <img src="assets/ar-phone-mockup.webp" style="width:484px;height:860px;display:block">
    <div id="phs" style="position:absolute;left:92px;top:78px;width:300px;height:678px;border-radius:38px;overflow:hidden;background:radial-gradient(120% 80% at 50% 40%,#5d5a55,#2c2b29)">
      <svg width="300" height="678" style="position:absolute;left:0;top:0"><g stroke="#fff" stroke-width="5" fill="none" stroke-linecap="round"><path d="M70 250v-40h40M230 250v-40h-40M70 410v40h40M230 410v40h-40"/></g>
      <rect id="phl" x="76" y="220" width="148" height="3" fill="#ff682c"/></svg></div></div>`, 4);
  PD.fig = mk('<img id="figimg" style="display:none">', 3);
}

/* ================================================================ sections */
const SEC = [];
const sec = (a, b, fn) => SEC.push({ a, b, fn });
const ctx = () => ({ bg: C.w, text: '', ui: '', fx: '', g: default3D(), slice: null, phone: null });

function bigWord(txt, { size, color = C.g, style = '', cls = 'big' }) {
  return `<div class="L cen"><div class="${cls}" style="font-size:${size}px;color:${color};${style}">${txt}</div></div>`;
}
const fit = (txt, max = 560, w = 1700) => Math.min(max, w / (txt.length * 0.66));
function toolbar(active, dark = true) {
  return `<div class="tbar" style="${dark ? '' : 'background:rgba(255,255,255,.9);border-color:#e8e8e8'}">${TOOLS.map((k, i) =>
    `<span class="tb ${i === active ? 'on' : ''}" style="${!dark && i !== active ? 'color:#6b6b6b' : ''}">${icon(ICON[k], 26)}</span>`).join('')}</div>`;
}
function head(l, n, title, sub, color = '#fff') {
  const k = E.out5(P(l, 0, 0.35));
  return `<div class="eb" style="${tr({ o: k, x: (1 - k) * -40 })}">VIEWER · ${String(n).padStart(2, '0')}</div>
    <div class="ttl" style="color:${color};clip-path:inset(0 0 ${(1 - k) * 100}% 0);${tr({ y: (1 - k) * 60 })}">${title}</div>
    ${sub ? `<div class="sub" style="top:${title.includes('<br>') ? 420 : 300}px;color:${color === '#fff' ? 'rgba(255,255,255,.7)' : '#4d4d4d'};${tr(inn(l, 0.3))}">${sub}</div>` : ''}`;
}
const inn = (l, a, d = 0.4, dy = 30) => { const k = E.out(P(l, a, a + d)); return { o: k, y: (1 - k) * dy }; };
const ring = (x, y, k, color = C.o, maxR = 700, w = 6) => k > 0 && k < 1 ?
  `<div style="position:absolute;left:${x - maxR * k}px;top:${y - maxR * k}px;width:${2 * maxR * k}px;height:${2 * maxR * k}px;border-radius:50%;border:${w * (1 - k) + 1}px solid ${color};opacity:${1 - k}"></div>` : '';

/* ---- A: hook 0–2 */
sec(0, 2, (l, c) => {
  if (l < 0.5) {
    c.bg = C.w;
    const k = pulse(l, 0, 6);
    c.text = bigWord('FLAT.', { size: 600, color: C.g, style: tr({ s: 1 + 0.06 * k }) });
    c.ui = `<div style="position:absolute;left:0;right:0;top:150px;text-align:center;font:600 30px/1 Int;letter-spacing:.3em;color:#6b6b6b">YOUR 3D RESEARCH, IN A PDF</div>`;
  } else if (l < 1.0) {
    c.bg = C.o;
    const k = E.out5(P(l, 0.5, 0.7)), q = E.io(P(l, 0.8, 1.0));
    c.text = bigWord('NOT ANYMORE.', { size: 215, color: C.w, style: tr({ s: lerp(1.8, 1, k), sy: 1 - q * 0.95, sx: 1 + q * 0.3 }) });
  } else {
    c.bg = C.d;
    const k = E.out5(P(l, 1.0, 1.6));
    c.text = bigWord('3D', { size: 900, cls: 'big outline', style: tr({ s: lerp(0.6, 1.15, E.out(P(l, 1, 2))) }) });
    Object.assign(c.g, { on: true, scale: Math.max(0.001, E.back(P(l, 1.0, 1.4))), rotY: -6 * (1 - k) + l * 0.6, az: 0.7, r: 5.4, el: 0.3 });
    c.fx = ring(960, 560, P(l, 1.0, 1.6)) + ring(960, 560, P(l, 1.15, 1.9), '#fff', 900, 3);
  }
});

/* ---- B: kinetic words 2–8 */
const WORDS = [[2.0, 'UPLOAD', 'w', 'g', 'slam'], [2.5, 'ANY', 'g', 'w', 'up'], [3.0, '3D', 'o', 'w', 'stretch'], [3.5, 'MODEL.', 'w', 'g', 'slam'],
  [4.0, 'STL', 'g', 'o', 'cut'], [4.25, 'OBJ', 'w', 'g', 'cut'], [4.5, 'FBX', 'o', 'w', 'cut'], [4.75, 'STEP', 'g', 'w', 'cut'], [5.0, 'DICOM', 'w', 'o', 'cut'], [5.25, 'NIfTI', 'g', 'w', 'cut'],
  [5.5, 'GET', 'w', 'g', 'up'], [6.0, 'A LIVE', 'g', 'w', 'slam'], [6.5, '3D VIEWER', 'w', 'g', 'stretch'], [7.0, '+ AR', 'o', 'w', 'spin'], [7.5, '+ QR', 'g', 'o', 'slam']];
sec(2, 8, (l, c, t) => {
  let i = 0; for (let j = 0; j < WORDS.length; j++) if (t >= WORDS[j][0]) i = j;
  const [t0, w, bg, fg, an] = WORDS[i], k = t - t0, size = fit(w);
  c.bg = C[bg];
  let st = '';
  if (an === 'slam') { const e = E.out5(P(k, 0, 0.18)); st = tr({ s: lerp(1.7, 1, e) }); }
  if (an === 'up') { const e = E.out5(P(k, 0, 0.2)); st = tr({ y: (1 - e) * 500 }) + ';clip-path:inset(0 0 0 0)'; }
  if (an === 'stretch') { const e = E.out5(P(k, 0, 0.25)); st = tr({ sx: lerp(2.8, 1, e), sy: lerp(0.4, 1, e) }); }
  if (an === 'cut') st = tr({ x: (i % 2 ? 1 : -1) * 18 * pulse(k, 0, 20), r: (i % 2 ? 2 : -2) });
  if (an === 'spin') { const e = E.back(P(k, 0, 0.3)); st = tr({ r: (1 - e) * -120, s: Math.max(0.01, e) }); }
  c.text = `<div class="L cen" style="overflow:hidden">${bigWord(w, { size, color: C[fg], style: st })}</div>`;
  // decorative accent shapes
  const acc = fg === 'o' || bg === 'o' ? (bg === 'o' ? C.w : C.o) : C.o;
  const bx = (i * 377) % 1500 + 120, ex = E.out5(P(k, 0, 0.3));
  c.ui = `<div style="position:absolute;left:${bx}px;top:${i % 2 ? 860 : 150}px;width:${lerp(0, 260, ex)}px;height:16px;background:${acc}"></div>
    <div style="position:absolute;right:80px;top:80px;font:600 24px/1 Int;letter-spacing:.2em;color:${C[fg]};opacity:.6">${String(i + 1).padStart(2, '0')} / ${WORDS.length}</div>
    <div style="position:absolute;left:80px;bottom:70px;display:flex;gap:10px">${Array.from({ length: 12 }, (_, b) => `<span style="width:28px;height:6px;background:${C[fg]};opacity:${b <= Math.floor((t - 2) / 0.5) ? 0.9 : 0.15}"></span>`).join('')}</div>`;
});

/* ---- C: logo build + QR 8–14 */
let QRD = null;
sec(8, 14, (l, c) => {
  c.bg = C.w; const R = rng(11);
  if (l < 3) {
    // 16 squares fly in → tile → wordmark
    const tileK = E.out5(P(l, 1.0, 1.3)), mv = E.io(P(l, 1.5, 2.0));
    const tw = 300, word = 'AcademicAR', fs = 190;
    const total = tw + 50 + 1060, sx = (1920 - total) / 2;
    const tx = lerp(960 - tw / 2, sx, mv), ty = 540 - tw / 2 - 10;
    let h = '';
    for (let i = 0; i < 16; i++) {
      const gx = 960 - 138 + (i % 4) * 72, gy = 540 - 138 + Math.floor(i / 4) * 72;
      const fx = R() * 2400 - 240, fy = R() < 0.5 ? -200 : 1280, d = 0.04 * i;
      const k = E.out5(P(l, d, d + 0.5));
      const x = lerp(fx, gx, k), y = lerp(fy, gy, k);
      const o = 1 - P(l, 1.0, 1.15);
      h += `<div style="position:absolute;left:0;top:0;width:60px;height:60px;border-radius:12px;background:${i === 5 || i === 10 ? C.o : C.g};${tr({ x, y, o, r: (1 - k) * 180 })}"></div>`;
    }
    h += `<div class="tile" style="position:absolute;left:${tx}px;top:${ty}px;width:${tw}px;height:${tw}px;border-radius:64px;font-size:190px;${tr({ o: P(l, 1.0, 1.05), s: lerp(0.96, 1, tileK) })}">A</div>`;
    const letters = word.split('').map((ch, i) => {
      const k = E.back(P(l, 1.6 + i * 0.125, 1.85 + i * 0.125));
      return `<span style="display:inline-block;color:${i >= 8 ? C.o : C.g};${tr({ y: (1 - k) * -260, o: P(l, 1.6 + i * 0.125, 1.65 + i * 0.125) })}">${ch}</span>`;
    }).join('');
    h += `<div class="big" style="position:absolute;left:${sx + tw + 50}px;top:${ty + 52}px;font-size:${fs}px;line-height:1">${letters}</div>`;
    h += `<div style="position:absolute;left:0;right:0;top:${ty + tw + 70}px;text-align:center;font:500 40px/1 Int;color:#4d4d4d;${tr(inn(l, 2.4, 0.4))}">Interactive 3D &amp; AR for research.</div>`;
    c.ui = h + ring(960, 540, P(l, 1.0, 1.7), C.o, 600);
  } else {
    if (!QR) return;
    const n = QR.n, size = 640, cell = size / n, x0 = 960 - size / 2, y0 = 540 - size / 2;
    if (!QRD) {
      const r2 = rng(5); QRD = [];
      for (let i = 0; i < n * n; i++) if (QR.d[i]) QRD.push({ x: i % n, y: Math.floor(i / n), fx: r2() * 2200 - 140, fy: r2() * 1300 - 110, d: r2() * 0.5 });
      let best = 1e9; QRD.forEach((q) => { const dd = (q.x - n / 2) ** 2 + (q.y - n / 2) ** 2; if (dd < best) { best = dd; QRD.c = q; } });
    }
    const zl = l - 3, zk = E.io(P(zl, 2.0, 3.0)), zs = Math.pow(80, zk);
    const cx = x0 + (QRD.c.x + 0.5) * cell, cy = y0 + (QRD.c.y + 0.5) * cell;
    const rotY = Math.sin(P(zl, 1.0, 2.0) * Math.PI) * 24, rotX = Math.sin(P(zl, 1.0, 2.0) * Math.PI) * -10;
    let h = '';
    for (const q of QRD) {
      const k = E.out5(P(zl, q.d * 0.8, q.d * 0.8 + 0.5));
      h += `<rect x="${lerp(q.fx, x0 + q.x * cell, k)}" y="${lerp(q.fy, y0 + q.y * cell, k)}" width="${cell + 0.5}" height="${cell + 0.5}" fill="${q === QRD.c ? C.g : C.g}" opacity="${P(zl, q.d * 0.8, q.d * 0.8 + 0.1)}"/>`;
    }
    c.ui = `<div class="L" style="transform-origin:${cx}px ${cy}px;transform:perspective(1600px) translate(${(960 - cx) * zk}px,${(540 - cy) * zk}px) scale(${zs}) rotateY(${rotY}deg) rotateX(${rotX}deg)">
      <svg width="1920" height="1080" style="position:absolute;left:0;top:0">${h}</svg></div>
      <div style="position:absolute;left:0;right:0;top:${y0 + size + 40}px;text-align:center;font:500 34px/1 Int;color:#202020;${tr({ o: P(zl, 0.9, 1.1) * (1 - P(zl, 1.9, 2.1)) })}">academicar.com/m/7Q2KX9 <span style="color:#ff682c">· stable QR</span></div>`;
    if (zk > 0.85) c.bg = C.d;
  }
});

/* ---- viewer sections share a dark base */
function viewerBase(c, l) {
  c.bg = `radial-gradient(70% 90% at 62% 55%, #2a2a2a, ${C.d})`;
  Object.assign(c.g, { on: true, offx: -260, r: 5.0 });
}
/* D orbit 14–18 */
sec(14, 18, (l, c) => {
  viewerBase(c, l);
  const sw = E.out5(P(l, 0, 1.2));
  Object.assign(c.g, { az: lerp(-3.4, 0.4, sw) + l * 0.25, r: lerp(10, 4.8, sw) - 0.25 * pulse(l % 1, 0, 6), el: lerp(1.0, 0.32, sw) + 0.08 * Math.sin(l * 2) });
  c.text = `<div class="big outline" style="position:absolute;top:300px;left:${600 - l * 160}px;font-size:520px">ORBIT ORBIT</div>`;
  c.ui = head(l, 1, 'Orbit.', '360° in any browser. No login, no app.') + toolbar(0);
});
/* E layers 18–22 */
sec(18, 22, (l, c) => {
  viewerBase(c, l);
  const pe = E.back(P(l, 0.5, 0.8)), ret = E.io(P(l, 2.5, 2.9));
  const stag = [0, 1, 2, 3].map((i) => E.back(P(l, 1.0 + i * 0.125, 1.3 + i * 0.125)) * (1 - ret));
  const xr = l > 1.5 && l < 2.5 ? 0.22 : 1;
  const sw = [0xe6dfd0, 0x9fb7d6, 0xf3c2ab, 0xe6dfd0][Math.min(3, Math.max(0, Math.floor((l - 3.0) / 0.25)))];
  Object.assign(c.g, { az: 0.35 + l * 0.18, el: 0.25, r: 5.6, explode: pe * (1 - ret), screwStag: stag, op: xr, boneColor: l > 3 ? sw : null });
  const rows = [['Calcaneus', '#e6dfd0', l > 1.5 && l < 2.5 ? '22%' : '100%'], ['Lateral plate', '#5a5a5a', 'on'], ['Screws ×4', '#ff682c', 'on']];
  c.ui = head(l, 2, 'Layers.', 'Explode, X-ray, recolour — every part, its own layer.') +
    `<div style="position:absolute;left:84px;top:520px;width:520px">${rows.map(([n, col, v], i) =>
      `<div style="display:flex;align-items:center;gap:18px;padding:20px 0;border-top:1px solid rgba(255,255,255,.12);color:#fff;font:500 28px/1 Int;${tr(inn(l, 0.3 + i * 0.12))}"><span style="width:26px;height:26px;border-radius:7px;background:${col}"></span><span style="flex:1">${n}</span><span style="color:${i === 0 && v !== '100%' ? '#ff682c' : 'rgba(255,255,255,.5)'}">${v}</span></div>`).join('')}</div>` + toolbar(1);
  c.fx = l > 1.5 && l < 1.6 ? `<div class="L" style="background:#fff;opacity:${1 - P(l, 1.5, 1.6)}"></div>` : '';
});
/* F finishes 22–26 */
const FNAMES = ['Matte', 'Plastic', 'Ceramic', 'Glossy', 'Metal', 'Gold', 'Chrome', 'Glass'];
sec(22, 26, (l, c) => {
  viewerBase(c, l);
  const i = Math.min(7, Math.floor(l / BEAT)), name = FNAMES[i], k = pulse(l - i * BEAT, 0, 14);
  Object.assign(c.g, { fin: name, implants: false, az: 0.9 + l * 0.35, el: 0.4, r: 4.3 - 0.2 * k, offx: -200 });
  c.text = `<div class="big outline" style="position:absolute;top:340px;left:${700 - l * 60}px;font-size:420px;-webkit-text-stroke-color:${name === 'Gold' ? 'rgba(231,182,90,.35)' : 'rgba(255,255,255,.18)'}">${name.toUpperCase()}</div>`;
  c.ui = `<div class="eb">VIEWER · 03 — FINISHES</div>
    <div class="ttl" style="${tr({ s: 1 + 0.08 * k, x: -6 * k })};transform-origin:0 50%">${name}.</div>
    <div style="position:absolute;left:84px;top:330px;display:flex;flex-wrap:wrap;gap:12px;width:640px">${FNAMES.map((f, j) =>
      `<span class="chipd" style="${j === i ? 'background:#ff682c;border-color:#ff682c' : j < i ? 'opacity:.8' : 'opacity:.35'}">${f}</span>`).join('')}</div>` + toolbar(2);
});
/* G section 26–30 */
sec(26, 30, (l, c) => {
  viewerBase(c, l);
  let axis = 'x', clip = 9, guide = 0;
  if (l < 1) { axis = 'x'; clip = lerp(1.15, 0.0, E.io(P(l, 0.1, 0.9))); guide = P(l, 0, 0.1); }
  else if (l < 2) { axis = 'y'; clip = lerp(0.9, 0.05, E.out5(P(l, 1.0, 1.5))); guide = 1; }
  else if (l < 3) { axis = 'z'; clip = lerp(0.6, -0.05, E.out5(P(l, 2.0, 2.5))); guide = 1; }
  else { axis = 'x'; clip = lerp(1.15, 0.15, E.out5(P(l, 3.0, 3.3))); guide = 1 - P(l, 3.7, 4); }
  const az = l < 1 ? 1.5 : l < 2 ? 0.9 : l < 3 ? 0.5 : 1.5 + (l - 3) * 1.6;
  Object.assign(c.g, { clipAxis: axis, clip, cap: true, guide, az, el: axis === 'y' ? 0.75 : 0.32, r: 5.2 - 0.3 * pulse(l % 1, 0, 8) });
  c.text = `<div class="big outline" style="position:absolute;top:330px;left:520px;font-size:440px;clip-path:inset(0 0 ${50 + 50 * Math.sin(l * 3)}% 0)">CUT</div>`;
  c.ui = head(l, 4, 'Section.', 'Slice through any axis and see inside.') +
    `<div style="position:absolute;left:84px;top:470px;display:flex;gap:14px">${['X', 'Y', 'Z'].map((a) => `<span class="chipd" style="width:84px;justify-content:center;${a.toLowerCase() === axis ? 'background:#ff682c;border-color:#ff682c' : ''}">${a}</span>`).join('')}</div>` + toolbar(3);
  c.fx = [1, 2, 3].map((b) => (l > b && l < b + 0.08 ? `<div class="L" style="background:#ff682c;opacity:${0.5 * (1 - P(l, b, b + 0.08))}"></div>` : '')).join('');
});
/* H measure & labels 30–34 */
sec(30, 34, (l, c) => {
  viewerBase(c, l);
  Object.assign(c.g, { az: 0.55 + l * 0.12, el: 0.42, r: 5.0 });
  apply3D(c.g);
  const dot = (p, k) => `<div style="position:absolute;left:${p.x - 14}px;top:${p.y - 14}px;width:28px;height:28px;border-radius:50%;background:#ff682c;border:5px solid #fff;box-shadow:0 0 0 ${10 * k}px rgba(255,104,44,.3);${tr({ s: Math.max(0.001, k) })}"></div>`;
  let h = '';
  const meas = (pts, t0, mm) => {
    const a = proj(pts[0]), b = proj(pts[1]);
    const k1 = E.back(P(l, t0, t0 + 0.25)), k2 = E.back(P(l, t0 + 0.5, t0 + 0.75)), kl = E.io(P(l, t0 + 0.5, t0 + 0.9));
    const d = pts[0].distanceTo(pts[1]) * mm;
    h += `<svg width="1920" height="1080" style="position:absolute;left:0;top:0"><line x1="${a.x}" y1="${a.y}" x2="${lerp(a.x, b.x, kl)}" y2="${lerp(a.y, b.y, kl)}" stroke="#fff" stroke-width="4" stroke-dasharray="12 9" opacity="${kl > 0 ? 1 : 0}"/></svg>`;
    if (k1 > 0) h += dot(a, k1); if (k2 > 0) h += dot(b, k2);
    if (kl > 0) h += `<div style="position:absolute;left:${(a.x + b.x) / 2 - 80}px;top:${Math.min(a.y, b.y) - 90}px;padding:14px 20px;border-radius:12px;background:#ff682c;color:#fff;font:600 30px/1 Int;${tr({ s: 0.8 + 0.2 * E.back(kl) })}">${(d * kl).toFixed(1)} mm</div>`;
  };
  meas(G.m1, 0, 41); if (l > 1.5) meas(G.m2, 1.5, 41);
  G.pins.forEach(([name, p], i) => {
    const t0 = 2.0 + i * 0.5, k = E.back(P(l, t0, t0 + 0.3)); if (k <= 0) return;
    const q = proj(p);
    h += `<div class="pin" style="left:${q.x - 14}px;top:${q.y - 20}px;${tr({ s: Math.max(0.001, k) })}"><i></i><span>${i + 1} · ${name}</span></div>`;
  });
  c.ui = head(l, 5, 'Measure<br>&amp; label.', '') + h + toolbar(l < 2 ? 4 : 5);
});
/* I lighting 34–38 */
const LSEQ = [['Studio', 'd'], ['Soft', 'd'], ['Dramatic', 'd'], ['Bright', 'd'], ['Flat', 'l'], ['Dim', 'd'], ['Studio', 'w'], ['Studio', 'd']];
sec(34, 38, (l, c) => {
  const i = Math.min(7, Math.floor(l / BEAT)), [name, bgk] = LSEQ[i], k = pulse(l - i * BEAT, 0, 12);
  viewerBase(c, l);
  if (bgk === 'l') c.bg = '#efefef'; if (bgk === 'w') c.bg = '#ffffff';
  const dark = bgk === 'd', fg = dark ? '#fff' : '#202020';
  Object.assign(c.g, { light: name, az: 0.6 + l * 0.3, el: 0.35, r: 5.0 - 0.15 * k });
  const label = i >= 6 ? (bgk === 'w' ? 'White bg' : 'Dark bg') : i === 4 ? 'Flat · Light bg' : name;
  c.ui = `<div class="eb">VIEWER · 06 — LIGHTING &amp; BACKGROUND</div><div class="ttl" style="color:${fg};${tr({ s: 1 + 0.06 * k })};transform-origin:0 50%">${label}.</div>
    <div style="position:absolute;left:84px;top:330px;display:flex;flex-wrap:wrap;gap:12px;width:700px">${Object.keys(LIGHT).map((n) =>
      `<span class="chipd" style="${n === name && i < 6 ? 'background:#ff682c;border-color:#ff682c;color:#fff' : dark ? '' : 'color:#202020;background:#fff;border-color:#ddd'}">${n}</span>`).join('')}</div>` + toolbar(6, dark);
  c.fx = `<div class="L" style="background:#fff;opacity:${0.25 * k}"></div>`;
});
/* J scenes 38–41 */
const VIEWS = [[0.5, 0.3, 5.0, 'Overview'], [1.57, 0.15, 4.2, 'Lateral'], [1.0, -0.25, 4.4, 'Inferior'], [0.2, 1.25, 4.6, 'Superior'], [0.0, 0.1, 3.4, 'Plate fixation'], [2.6, 0.5, 4.8, 'Posterior facet']];
sec(38, 41, (l, c) => {
  viewerBase(c, l);
  const i = Math.min(5, Math.floor(l / BEAT)), [az, el, r, nm] = VIEWS[i], k = E.out5(P(l - i * BEAT, 0, 0.18));
  const [paz, pel, pr] = VIEWS[Math.max(0, i - 1)];
  Object.assign(c.g, { az: lerp(paz, az, k), el: lerp(pel, el, k), r: lerp(pr, r, k) });
  c.ui = head(l, 7, 'Scenes<br>&amp; tours.', '') +
    `<div style="position:absolute;left:84px;top:470px"><span class="chipd" style="background:#fff;color:#202020;font-size:30px;padding:18px 28px"><span class="dotx"></span>Scene ${i + 1}/12 · ${nm}</span>
    <div style="display:flex;gap:10px;margin-top:26px">${VIEWS.map((_, j) => `<span style="width:${j === i ? 60 : 22}px;height:10px;border-radius:5px;background:${j === i ? '#ff682c' : j < i ? '#fff' : 'rgba(255,255,255,.25)'}"></span>`).join('')}</div>
    <div style="margin-top:26px;font:400 26px/1.4 Int;color:rgba(255,255,255,.65)">Each scene: its own link, QR &amp; AR file.</div></div>` + toolbar(7);
  c.fx = `<div class="L" style="background:#000;opacity:${0.5 * pulse(l - i * BEAT, 0, 30)}"></div>`;
});
/* K slice 41–45 */
sec(41, 45, (l, c) => {
  c.bg = C.d;
  const cp = lerp(-0.85, 0.85, E.io(P(l, 0.3, 3.6)));
  Object.assign(c.g, { on: true, offx: 140, offy: -150, az: 1.0 + l * 0.15, el: 0.35, r: 7.2, clipAxis: 'x', clip: 9, guide: 1, implants: false });
  c.g.guidePos = cp;
  c.slice = cp;
  c.ui = head(l, 8, 'Slice view.', '2D cross-sections, with a snapping measure.') + toolbar(8);
});
/* L figure export 45–47.5 */
sec(45, 47.5, (l, c) => {
  c.bg = C.iv; c.figure = true;
  const k = E.out5(P(l, 0.15, 0.6));
  let h = '';
  [[-14, -260, 40], [9, 230, 30], [-3, 0, 0]].forEach(([r, x, y], j) => {
    const kk = E.out5(P(l, 0.15 + j * 0.12, 0.6 + j * 0.12)), fan = E.io(P(l, 1.0, 1.4));
    h += `<div style="position:absolute;left:560px;top:150px;width:800px;padding:30px 30px 36px;background:#fff;border-radius:6px;box-shadow:0 40px 90px -40px rgba(0,0,0,.4);${tr({ o: kk, y: (1 - kk) * 700 + y * fan, x: x * fan, r: r * fan + (1 - kk) * 20 })}">
      <div style="height:520px;background:#fafafa;display:flex;align-items:center;justify-content:center;overflow:hidden"><img src="${PD.figsrc || ''}" style="width:860px;margin:-60px"></div>
      <div style="margin-top:22px;font:600 22px/1.3 Int;color:#202020">Figure ${3 - j}. <span style="font-weight:400;color:#4d4d4d">Calcaneus with lateral plate fixation — exported from AcademicAR.</span></div></div>`;
  });
  c.ui = h + `<div class="eb" style="color:#ff682c">VIEWER · 09</div><div class="ttl" style="color:#202020;font-size:120px;${tr(inn(l, 0.2))}">Figure<br>export.</div>
    <div style="position:absolute;right:90px;bottom:80px;${tr(inn(l, 0.8))}"><span class="chipd" style="background:#202020">PNG · any angle · publication-ready</span></div>`;
  c.fx = `<div class="L" style="background:#fff;opacity:${1 - P(l, 0, 0.25)}"></div>`;
});
/* M AR 47.5–52 */
sec(47.5, 52, (l, c) => {
  c.bg = C.w; c.phone = true;
  const k = E.out5(P(l, 0.1, 0.8));
  PD.phStyle = `perspective(1800px) rotateY(${(1 - k) * 70 + Math.sin(l * 1.4) * 6}deg) rotateZ(${(1 - k) * -10}deg) translateY(${(1 - k) * 200}px) scale(${0.9 + 0.1 * k})`;
  PD.phScreen = 1 - E.io(P(l, 3.2, 3.6));
  PD.phLine = 220 + 220 * (0.5 - 0.5 * Math.cos(l * 5));
  const W = (txt, x, y, t0, col = C.g, sz = 200) => { const kk = E.out5(P(l, t0, t0 + 0.2)); return `<div class="big" style="position:absolute;left:${x}px;top:${y}px;font-size:${sz}px;color:${col};${tr({ s: lerp(1.6, 1, kk), o: P(l, t0, t0 + 0.05) })}">${txt}</div>`; };
  let qr = '';
  if (QR) {
    const n = QR.n, cs = 220 / n; let r = '';
    for (let i = 0; i < n * n; i++) if (QR.d[i]) r += `<rect x="${(i % n) * cs}" y="${Math.floor(i / n) * cs}" width="${cs + 0.4}" height="${cs + 0.4}" fill="#202020"/>`;
    const kq = E.back(P(l, 2.0, 2.3));
    qr = `<svg width="220" height="220" style="position:absolute;left:1520px;top:640px;${tr({ s: Math.max(0.001, kq) })}">${r}</svg>`;
    const kb = P(l, 2.4, 2.6) * (1 - P(l, 3.3, 3.5));
    if (kb > 0) qr += `<svg width="1920" height="1080" style="position:absolute;left:0;top:0"><polygon points="1190,420 1190,560 1520,860 1520,640" fill="rgba(255,104,44,.14)" stroke="rgba(255,104,44,.6)" stroke-width="2" opacity="${kb}"/><rect x="1512" y="632" width="236" height="236" rx="10" fill="none" stroke="#ff682c" stroke-width="5" opacity="${kb}"/></svg>`;
  }
  c.ui = W('NO APP.', 90, 220, 1.0, C.g, 165) + W('JUST', 90, 420, 1.5, C.g, 165) + W('SCAN.', 90, 620, 2.0, C.o, 165) + qr +
    `<div style="position:absolute;left:1430px;top:250px;font:500 32px/1.4 Int;color:#4d4d4d;width:440px;${tr(inn(l, 3.6))}">Place it on any desk —<br><b style="color:#202020">WebXR &amp; iOS Quick Look.</b></div>`;
  c.fx = `<div class="L" style="background:#ff682c;opacity:${1 - P(l, 0, 0.18)}"></div>`;
});
/* N montage 52–56 */
const MONT = ['LAYERS', 'FINISHES', 'SECTION', 'SLICE', 'MEASURE', 'LABELS', 'SCENES', 'TOURS', 'LIGHTING', 'FIGURES', 'COMPARE', 'MEDIA', 'METRICS', 'AR', 'QR', 'VERSIONS'];
sec(52, 56, (l, c) => {
  const i = Math.min(15, Math.floor(l / 0.25)), w = MONT[i], k = pulse(l - i * 0.25, 0, 18);
  const bgs = [C.g, C.o, C.w, C.d], bg = bgs[i % 4], fg = bg === C.w ? C.g : C.w;
  c.bg = bg;
  c.text = bigWord(w, { size: fit(w, 420, 1800), color: i % 2 ? 'transparent' : fg, style: `${i % 2 ? `-webkit-text-stroke:4px ${fg};` : ''}opacity:${bg === C.o || bg === C.w ? 1 : 0.9};${tr({ s: 1 + 0.12 * k, x: (i % 2 ? 1 : -1) * 30 * k })}` });
  const zoom = E.io(P(l, 3.5, 4.0));
  Object.assign(c.g, { on: true, fin: FNAMES[i % 8], implants: i % 3 !== 0, rotY: l * 3.2, az: 0.4, el: 0.3 + 0.3 * Math.sin(l * 2), r: lerp(5.6, 0.9, zoom), spinX: 0.2 * Math.sin(l * 5) });
  c.ui = `<div style="position:absolute;left:80px;bottom:70px;font:600 24px/1 Int;letter-spacing:.2em;color:${fg};opacity:.7">${String(i + 1).padStart(2, '0')} / 16</div>
    <div style="position:absolute;right:80px;top:80px;font:600 24px/1 Int;letter-spacing:.2em;color:${bg === C.o ? '#fff' : '#ff682c'}">ACADEMICAR VIEWER</div>`;
  c.fx = `<div class="L" style="background:#fff;opacity:${zoom}"></div>`;
});
/* O end 56–60 */
sec(56, 60, (l, c) => {
  c.bg = C.w;
  const kt = E.back(P(l, 0, 0.35)), mv = E.io(P(l, 0.5, 0.9)), kw = E.out5(P(l, 0.6, 1.1));
  const tw = 200, wordW = 940, total = tw + 40 + wordW, sx = (1920 - total) / 2;
  const tx = lerp(960 - tw / 2, sx, mv), ty = 380;
  const blink = l > 2.0 ? 0.5 + 0.5 * Math.cos((l - 2) * Math.PI * 2) : 1;
  c.ui = ring(960, 480, P(l, 0, 0.8), C.o, 700) +
    `<div class="tile" style="position:absolute;left:${tx}px;top:${ty}px;width:${tw}px;height:${tw}px;border-radius:44px;font-size:128px;${tr({ s: Math.max(0.001, lerp(2.2, 1, Math.min(1, kt)) * (kt > 0 ? 1 : 0)) })}">A</div>
    <div style="position:absolute;left:${sx + tw + 40}px;top:${ty + 34}px;width:${(wordW + 20) * kw}px;overflow:hidden;height:150px"><div class="big" style="font-size:132px;line-height:1.05;${tr({ x: (1 - kw) * -120 })}">Academic<span style="color:#ff682c">AR</span></div></div>
    <div style="position:absolute;left:0;right:0;top:680px;display:flex;justify-content:center;${tr(inn(l, 1.1, 0.5))}"><div style="display:flex;align-items:center;gap:18px;padding:22px 40px;border-radius:999px;border:2px solid #202020;font:500 44px/1 Int;color:#202020"><span class="dotx" style="width:16px;height:16px;opacity:${blink}"></span>academicar.com</div></div>
    <div style="position:absolute;left:0;right:0;top:830px;text-align:center;font:400 30px/1 Int;color:#6b6b6b;${tr(inn(l, 1.5, 0.5))}">Make every 3D project explorable.</div>`;
  c.fx = `<div class="L" style="background:#fff;opacity:${1 - P(l, 0, 0.12)}"></div>`;
});

/* ================================================================ slice drawing */
function drawSlice(cp) {
  const cv = $('slc'), g = cv.getContext('2d'), W = 760;
  g.clearRect(0, 0, W, W);
  g.fillStyle = 'rgba(255,255,255,0.04)'; g.beginPath(); g.roundRect(0, 0, W, W, 24); g.fill();
  g.strokeStyle = 'rgba(255,255,255,0.07)'; g.lineWidth = 1;
  for (let i = 40; i < W; i += 40) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, W); g.moveTo(0, i); g.lineTo(W, i); g.stroke(); }
  const { pos, idx, n } = G.tri, sc = 300, cx = W / 2, cy = W / 2;
  const vx = (k, a) => pos[k * 3 + a];
  let ymin = 1e9, ymax = -1e9, pmin, pmax;
  g.strokeStyle = '#ff682c'; g.lineWidth = 2.6; g.beginPath();
  for (let t = 0; t < n; t++) {
    const a = idx ? idx[t * 3] : t * 3, b = idx ? idx[t * 3 + 1] : t * 3 + 1, c = idx ? idx[t * 3 + 2] : t * 3 + 2;
    const da = vx(a, 0) - cp, db = vx(b, 0) - cp, dc = vx(c, 0) - cp;
    if ((da > 0 && db > 0 && dc > 0) || (da < 0 && db < 0 && dc < 0)) continue;
    const pts = [];
    for (const [p, q, dp, dq] of [[a, b, da, db], [b, c, db, dc], [c, a, dc, da]]) {
      if ((dp > 0) !== (dq > 0)) { const s = dp / (dp - dq); pts.push([vx(p, 2) + (vx(q, 2) - vx(p, 2)) * s, vx(p, 1) + (vx(q, 1) - vx(p, 1)) * s]); }
    }
    if (pts.length === 2) {
      const X1 = cx + pts[0][0] * sc, Y1 = cy - pts[0][1] * sc, X2 = cx + pts[1][0] * sc, Y2 = cy - pts[1][1] * sc;
      g.moveTo(X1, Y1); g.lineTo(X2, Y2);
      for (const [X, Y] of [[X1, Y1], [X2, Y2]]) { if (Y < ymin) { ymin = Y; pmin = [X, Y]; } if (Y > ymax) { ymax = Y; pmax = [X, Y]; } }
    }
  }
  g.stroke();
  if (pmin && pmax) {
    g.setLineDash([10, 8]); g.strokeStyle = '#fff'; g.lineWidth = 3; g.beginPath(); g.moveTo(...pmin); g.lineTo(...pmax); g.stroke(); g.setLineDash([]);
    for (const p of [pmin, pmax]) { g.fillStyle = '#ff682c'; g.strokeStyle = '#fff'; g.lineWidth = 4; g.beginPath(); g.arc(p[0], p[1], 10, 0, 7); g.fill(); g.stroke(); }
    const mm = ((ymax - ymin) / sc) * 41;
    g.fillStyle = '#fff'; g.font = '600 28px Int, sans-serif'; g.fillText(`${mm.toFixed(1)} mm`, Math.max(pmin[0], pmax[0]) + 24, (ymin + ymax) / 2);
  }
  g.fillStyle = 'rgba(255,255,255,.6)'; g.font = '500 22px Int, sans-serif'; g.fillText(`SLICE · X = ${(cp * 41).toFixed(1)} mm`, 28, 44);
}

/* ================================================================ render */
const IMPACTS = [0, 0.5, 1.0, 8.0, 9.0, 14, 18, 22, 26, 30, 34, 38, 41, 45, 47.5, 52, 56];
function shake(t) {
  let a = 0; for (const i of IMPACTS) a += pulse(t, i, 9) * 16;
  return a < 0.3 ? '' : `translate(${Math.sin(t * 91) * a}px,${Math.cos(t * 77) * a}px)`;
}
function wipe(t) {
  let h = '';
  [[18, 1], [22, -1], [26, 1], [30, -1], [34, 1], [41, -1]].forEach(([b, dir]) => {
    const k = P(t, b - 0.18, b + 0.18); if (k <= 0 || k >= 1) return;
    const x = lerp(-2600, 2600, E.io(k)) * dir;
    h += `<div style="position:absolute;left:-400px;top:-300px;width:2700px;height:1700px;background:#ff682c;transform:translateX(${x}px) skewX(-18deg)"></div>`;
  });
  return h;
}
async function update(t) {
  const c = ctx();
  for (const s of SEC) if (t >= s.a && t < s.b) { s.fn(t - s.a, c, t); break; }
  if (t >= DUR) SEC[SEC.length - 1].fn(t - 56, c, t);
  $('stage').style.background = c.bg;
  $('bgtext').innerHTML = c.text; $('ui').innerHTML = c.ui; $('fx').innerHTML = c.fx + wipe(t);
  $('stage').style.transform = shake(t);
  PD.slice.style.display = c.slice != null ? 'block' : 'none';
  PD.phone.style.display = c.phone ? 'block' : 'none';
  if (c.phone) { $('ph').style.transform = PD.phStyle; $('phs').style.opacity = PD.phScreen; $('phl').setAttribute('y', PD.phLine); }
  if (G.renderer) {
    apply3D(c.g);
    if (c.g.guidePos != null) { G.guide.position.set(c.g.guidePos, 0, 0); G.guide.rotation.set(0, Math.PI / 2, 0); G.guide.scale.set(1.1, 1, 1); }
    if (c.g.on) G.renderer.render(G.scene, G.camera);
    if (c.slice != null) drawSlice(c.slice);
  }
}
async function makeFigure() {
  const S = { ...default3D(), on: true, fin: 'Ceramic', az: 0.75, el: 0.3, r: 4.6 };
  apply3D(S); G.renderer.render(G.scene, G.camera);
  PD.figsrc = G.renderer.domElement.toDataURL('image/png');
  const im = new Image(); im.src = PD.figsrc; await im.decode();
}
window.renderAt = async (t) => {
  await update(t);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  return true;
};
window.ready = (async () => {
  setupPersistent();
  QR = await (await fetch('assets/qr.json')).json();
  await Promise.all(['600 28px Int', '500 22px Int', '600 100px Mont', '500 30px Int', '400 30px Int'].map((f) => document.fonts.load(f)));
  await document.fonts.ready;
  await Promise.all([...document.images].map((i) => i.decode().catch(() => {})));
  await init3D();
  // warm up every finish so shader compiles happen before capture
  for (const f of Object.keys(FIN)) { apply3D({ ...default3D(), on: true, fin: f, cap: true }); G.renderer.render(G.scene, G.camera); }
  await makeFigure();
  return true;
})();
window.DUR = DUR;
const qs = new URLSearchParams(location.search);
window.ready.then(() => { if (qs.has('t')) window.renderAt(parseFloat(qs.get('t'))); });
