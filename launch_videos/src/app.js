import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export const DUR = 74;
const $ = (id) => document.getElementById(id);
const cl = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const P = (t, a, b) => cl((t - a) / (b - a));
const lerp = (a, b, k) => a + (b - a) * k;
const E = {
  out: (x) => 1 - Math.pow(1 - x, 3),
  out5: (x) => 1 - Math.pow(1 - x, 5),
  io: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  back: (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
};
function S(el, { o = 1, x = 0, y = 0, s = 1, r = 0 } = {}) {
  el.style.opacity = o;
  el.style.transform = `translate(${x}px,${y}px) scale(${s}) rotate(${r}deg)`;
}
const inn = (t, a, d = 0.7, dy = 34) => { const k = E.out(P(t, a, a + d)); return { o: k, y: (1 - k) * dy }; };
function words(el, t, a, st = 0.08, d = 0.7) {
  el.querySelectorAll('.w').forEach((w, i) => {
    const k = E.out(P(t, a + i * st, a + i * st + d));
    w.style.opacity = k;
    w.style.transform = `translateY(${(1 - k) * 46}px)`;
  });
}
function rng(seed) { return () => ((seed = (seed * 16807) % 2147483647) / 2147483647); }
const NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
const ICON = {
  orbit: 'M12 3a9 9 0 1 0 9 9M21 3v6h-6',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
  section: 'M4 20L20 4M4 4h16v16H4z',
  measure: 'M3 17L17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2',
  ar: 'M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2',
  share: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
  scenes: 'M4 5h16v11H4zM8 20h8M12 16v4',
  cube: 'M21 16V8l-9-5-9 5v8l9 5zM3 8l9 5 9-5M12 13v8',
  slice: 'M3 12h18M7 7c3 2 7 2 10 0M7 17c3-2 7-2 10 0',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  image: 'M3 5h18v14H3zM3 15l5-5 5 5 3-3 5 5M15 9h.01',
  compare: 'M3 4h7v16H3zM14 4h7v16h-7z',
  film: 'M3 4h18v16H3zM7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 3',
};
const icon = (d, size = 26, color = 'currentColor') =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;

/* ------------------------------------------------------------------ 3D */
let boneGeo, mm = 41; // 2 normalised units ≈ 82 mm
const V = {};
function shadowTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const g = c.getContext('2d'); const gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  gr.addColorStop(0, 'rgba(0,0,0,0.30)'); gr.addColorStop(0.6, 'rgba(0,0,0,0.10)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}
function makeViewer(container, w, h) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1); renderer.setSize(w, h);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.localClippingEnabled = true;
  container.insertBefore(renderer.domElement, container.firstChild);
  const scene = new THREE.Scene();
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
  const key = new THREE.DirectionalLight(0xffffff, 1.3); key.position.set(3, 5, 4); scene.add(key);
  const camera = new THREE.PerspectiveCamera(28, w / h, 0.1, 100);
  const head = new THREE.DirectionalLight(0xffffff, 0.9); head.position.set(-1, 1.5, 0); camera.add(head); scene.add(camera);
  const root = new THREE.Group(); scene.add(root);
  return { renderer, scene, camera, root, w, h };
}
async function init3D() {
  const gltf = await new GLTFLoader().loadAsync('assets/calcaneus.glb');
  let src; gltf.scene.traverse((o) => { if (o.isMesh && !src) src = o; });
  src.updateMatrixWorld(true);
  const g = src.geometry.clone(); g.applyMatrix4(src.matrixWorld);
  g.deleteAttribute('color'); g.computeBoundingBox();
  const bb = g.boundingBox, c = bb.getCenter(new THREE.Vector3()), sz = bb.getSize(new THREE.Vector3());
  g.translate(-c.x, -c.y, -c.z);
  const ax = [0, 1, 2].sort((a, b) => [sz.x, sz.y, sz.z][b] - [sz.x, sz.y, sz.z][a]); // long, mid, short
  const rows = ax.map((i) => [0, 1, 2].map((j) => (j === i ? 1 : 0)));
  const m = new THREE.Matrix4().set(...rows[0], 0, ...rows[1], 0, ...rows[2], 0, 0, 0, 0, 1);
  if (m.determinant() < 0) m.elements[2] *= -1, m.elements[6] *= -1, m.elements[10] *= -1;
  g.applyMatrix4(m);
  const s = 2 / Math.max(sz.x, sz.y, sz.z); g.scale(s, s, s);
  if (!g.attributes.normal) g.computeVertexNormals();
  g.computeBoundingBox(); boneGeo = g;
  const B = g.boundingBox;
  const shadow = () => {
    const p = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 2.2), new THREE.MeshBasicMaterial({ map: shadowTex(), transparent: true, depthWrite: false }));
    p.rotation.x = -Math.PI / 2; p.position.y = B.min.y - 0.04; return p;
  };
  const boneMat = () => new THREE.MeshStandardMaterial({ color: 0xe6dfd0, roughness: 0.55, metalness: 0 });

  // Viewer A — conversion result
  const A = makeViewer($('va'), 720, 580); V.A = A;
  A.bone = new THREE.Mesh(g, boneMat()); A.root.add(A.bone, shadow());
  A.camera.position.set(0, 1.5, 5.4); A.camera.lookAt(0, 0.05, 0);

  // Viewer B — explore (bone + plate + screws, section, measure)
  const Bv = makeViewer($('vb'), 1200, 820); V.B = Bv;
  Bv.plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 5);
  const clip = [Bv.plane];
  Bv.boneMat = new THREE.MeshStandardMaterial({ color: 0xe6dfd0, roughness: 0.55, clippingPlanes: clip, transparent: true });
  Bv.capMat = new THREE.MeshStandardMaterial({ color: 0xff682c, roughness: 0.85, side: THREE.BackSide, clippingPlanes: clip, emissive: 0x7a2a0a, emissiveIntensity: 0.6 });
  Bv.bone = new THREE.Mesh(g, Bv.boneMat); Bv.bone.renderOrder = 2;
  Bv.cap = new THREE.Mesh(g, Bv.capMat); Bv.cap.renderOrder = 1;
  Bv.root.add(Bv.bone, Bv.cap, shadow());
  Bv.root.updateMatrixWorld(true);
  const rc = new THREE.Raycaster();
  const hitsAt = (o, d) => { rc.set(o, d); return rc.intersectObject(Bv.bone, false); };
  const screwMat = new THREE.MeshStandardMaterial({ color: 0xff682c, metalness: 0.35, roughness: 0.4, clippingPlanes: clip });
  const plateMat = new THREE.MeshStandardMaterial({ color: 0x5a5a5a, metalness: 0.8, roughness: 0.35, clippingPlanes: clip });
  const screws = []; let plateZ = -9, plateY = 0;
  for (const x of [-0.52, -0.2, 0.12, 0.44]) {
    for (const y of [0, 0.12, -0.12, 0.24, -0.24]) {
      const h = hitsAt(new THREE.Vector3(x, y, 5), new THREE.Vector3(0, 0, -1));
      if (h.length >= 2) { screws.push({ x, y, z1: h[0].point.z, z2: h[h.length - 1].point.z }); break; }
    }
  }
  screws.forEach((sc) => { plateZ = Math.max(plateZ, sc.z1); plateY += sc.y / screws.length; });
  plateZ += 0.03;
  for (const sc of screws) {
    const len = plateZ - sc.z2 - 0.08;
    const cyl = new THREE.Mesh(new THREE.CylinderGeometry(0.034, 0.03, len, 20), screwMat);
    cyl.rotation.x = Math.PI / 2; cyl.position.set(sc.x, plateY, plateZ - len / 2);
    const head = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.045, 24), screwMat);
    head.rotation.x = Math.PI / 2; head.position.set(sc.x, plateY, plateZ + 0.035);
    Bv.root.add(cyl, head);
  }
  if (screws.length) {
    const x0 = screws[0].x - 0.14, x1 = screws[screws.length - 1].x + 0.14;
    const plate = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, 0.22, 0.035), plateMat);
    plate.position.set((x0 + x1) / 2, plateY, plateZ); Bv.root.add(plate);
  }
  // section guide
  const pg = new THREE.PlaneGeometry(B.max.z - B.min.z + 0.9, B.max.y - B.min.y + 0.7);
  Bv.guide = new THREE.Group();
  Bv.guideMat = new THREE.MeshBasicMaterial({ color: 0xff682c, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false });
  Bv.edgeMat = new THREE.LineBasicMaterial({ color: 0xff682c, transparent: true });
  Bv.guide.add(new THREE.Mesh(pg, Bv.guideMat), new THREE.LineSegments(new THREE.EdgesGeometry(pg), Bv.edgeMat));
  Bv.guide.rotation.y = Math.PI / 2; Bv.root.add(Bv.guide);
  // measurement points on the top surface
  Bv.mpts = [-0.8, 0.82].map((x) => {
    for (const z of [0, 0.1, -0.1, 0.2, -0.2]) {
      const h = hitsAt(new THREE.Vector3(x, 5, z), new THREE.Vector3(0, -1, 0));
      if (h.length) return h[0].point.clone();
    }
    return new THREE.Vector3(x, B.max.y, 0);
  });
  Bv.camera.setViewOffset(1200, 820, -120, -16, 1200, 820);
}

/* ------------------------------------------------------------------ DOM setup */
const M = {}; // measured positions
function setup() {
  // S2 grid
  const grid = $('s2grid'); M.grid = [];
  for (let x = 120; x < 1920; x += 240) M.grid.push(svgEl('line', { x1: x, y1: 0, x2: x, y2: 1080, stroke: '#f0f0f0', 'stroke-width': 1.5, 'stroke-dasharray': 1080, 'stroke-dashoffset': 1080 }, grid));
  for (let y = 60; y < 1080; y += 240) M.grid.push(svgEl('line', { x1: 0, y1: y, x2: 1920, y2: y, stroke: '#f0f0f0', 'stroke-width': 1.5, 'stroke-dasharray': 1920, 'stroke-dashoffset': 1920 }, grid));
  $('s2checks').innerHTML = ['No coding required', 'Auto format conversion', 'QR generated automatically', 'Mobile AR ready']
    .map((l) => `<span class="chip"><svg width="20" height="20" viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" fill="none" stroke="#ff682c" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>${l}</span>`).join('');

  // S3 chips
  const chips = [['STL', 'calcaneus_fixation.stl', 1110, 380], ['OBJ', 'heart_model.obj', 1450, 330], ['STEP', 'gearbox.step', 1010, 500],
    ['DICOM', 'ct_series.zip', 1440, 470], ['NIfTI', 'segmentation.nii.gz', 1180, 560], ['FBX', 'tree.fbx', 1560, 560]];
  M.chips = chips.map(([tg, name, x, y], i) => {
    const d = document.createElement('div'); d.className = 'chip abs';
    d.style.left = x + 'px'; d.style.top = y + 'px'; d.style.zIndex = 10 - i;
    d.innerHTML = `<span class="tag ${i === 0 ? 'o' : ''}">${tg}</span>${name}`;
    $('s3chips').appendChild(d); return d;
  });

  // S4 pipeline
  const pipe = $('pipe');
  const ins = ['STL', 'OBJ', 'FBX', 'STEP', 'DICOM', 'NIfTI'], outs = ['GLB · Draco', 'WebP textures', 'USDZ · iOS AR', 'Link + QR'];
  M.pin = []; M.pout = []; M.inG = []; M.outG = []; M.dots = [];
  const nx = 520, ny = 340;
  ins.forEach((l, i) => {
    const y = 40 + i * 120;
    const p = svgEl('path', { d: `M210 ${y} C330 ${y} 360 ${ny} 440 ${ny}`, fill: 'none', stroke: '#d6d6d6', 'stroke-width': 2.5 }, pipe);
    M.pin.push(p);
    const gr = svgEl('g', {}, pipe);
    svgEl('rect', { x: 40, y: y - 30, width: 170, height: 60, rx: 30, fill: '#fff', stroke: '#e0e0e0', 'stroke-width': 1.5 }, gr);
    const tx = svgEl('text', { x: 125, y: y + 8, 'text-anchor': 'middle', 'font-family': 'Int', 'font-weight': 600, 'font-size': 22, fill: '#202020' }, gr); tx.textContent = l;
    M.inG.push(gr);
  });
  outs.forEach((l, i) => {
    const y = 110 + i * 150;
    const p = svgEl('path', { d: `M600 ${ny} C680 ${ny} 690 ${y} 760 ${y}`, fill: 'none', stroke: '#d6d6d6', 'stroke-width': 2.5 }, pipe);
    M.pout.push(p);
    const gr = svgEl('g', {}, pipe);
    svgEl('rect', { x: 760, y: y - 32, width: 230, height: 64, rx: 14, fill: i === 0 ? '#202020' : '#fff', stroke: i === 0 ? '#202020' : '#e0e0e0', 'stroke-width': 1.5 }, gr);
    const tx = svgEl('text', { x: 875, y: y + 8, 'text-anchor': 'middle', 'font-family': 'Int', 'font-weight': 600, 'font-size': 21, fill: i === 0 ? '#fff' : '#202020' }, gr); tx.textContent = l;
    M.outG.push(gr);
  });
  [...M.pin, ...M.pout].forEach((p) => { const L = p.getTotalLength(); p.style.strokeDasharray = L; p.style.strokeDashoffset = L; p._L = L; });
  M.node = svgEl('g', {}, pipe);
  svgEl('circle', { cx: nx, cy: ny, r: 82, fill: '#fff', stroke: '#e0e0e0', 'stroke-width': 1.5 }, M.node);
  M.nodeArc = svgEl('circle', { cx: nx, cy: ny, r: 96, fill: 'none', stroke: '#ff682c', 'stroke-width': 4, 'stroke-linecap': 'round', 'stroke-dasharray': '120 480' }, M.node);
  const ng = svgEl('g', { transform: `translate(${nx - 22} ${ny - 40}) scale(1.85)` }, M.node);
  svgEl('path', { d: ICON.cube, fill: 'none', stroke: '#202020', 'stroke-width': 1.6, 'stroke-linejoin': 'round' }, ng);
  const nt = svgEl('text', { x: nx, y: ny + 44, 'text-anchor': 'middle', 'font-family': 'Int', 'font-weight': 600, 'font-size': 18, fill: '#6b6b6b', 'letter-spacing': 2 }, M.node); nt.textContent = 'WORKER';
  for (let i = 0; i < 10; i++) M.dots.push(svgEl('circle', { r: 6, fill: '#ff682c', opacity: 0 }, pipe));

  // S5 tools + features
  M.tools = ['orbit', 'layers', 'section', 'measure', 'ar', 'share'].map((k) => {
    const d = document.createElement('span'); d.className = 'tool'; d.innerHTML = icon(ICON[k], 24); $('tools').appendChild(d); return d;
  });
  const feats = [['orbit', '360° orbit & zoom', 'Inspect from any angle — no login, no plugin.'], ['layers', 'Layers', 'Toggle parts, set opacity and finishes.'],
    ['section', 'Section plane', 'Cut through any axis to see inside.'], ['measure', 'Measure', 'Snapping point-to-point distances in mm.']];
  M.feats = feats.map(([k, b, s]) => {
    const d = document.createElement('div'); d.className = 'feat';
    d.innerHTML = `<span class="ind"></span><span class="ix">${icon(ICON[k], 24)}</span><div><b>${b}</b><span>${s}</span></div>`;
    $('feats').appendChild(d); return d;
  });
  const meas = $('meas');
  M.mline = svgEl('line', { stroke: '#202020', 'stroke-width': 3, 'stroke-dasharray': '10 8' }, meas);
  M.mp = [0, 1].map(() => { const g = svgEl('g', {}, meas); svgEl('circle', { r: 16, fill: 'rgba(255,104,44,.22)' }, g); svgEl('circle', { r: 8, fill: '#ff682c', stroke: '#fff', 'stroke-width': 3 }, g); return g; });

  // S6 QR
  fetchQR();

  // S7 feature cards
  const fc = [['scenes', 'Saved scenes & tours', 'Up to 12 views per model, each with its own link and QR.'], ['ar', 'Scene AR', 'Every saved scene becomes its own AR file.'],
    ['slice', 'Slice view', '2D cross-section contours with a snapping measure.'], ['chart', 'Layer metrics', 'Volume and surface area for every structure.'],
    ['image', 'Figure export', 'Publication-ready images from any angle.'], ['compare', 'Comparisons', 'Two models side by side, one shareable link.'],
    ['film', 'Media studio', 'Auto-generated views and showcase videos.'], ['history', 'Versioning', 'Replace a model — the QR code never changes.']];
  M.fcards = fc.map(([k, b, s], i) => {
    const d = document.createElement('div'); d.className = 'fcard';
    d.style.left = 135 + (i % 4) * 420 + 'px'; d.style.top = 390 + Math.floor(i / 4) * 280 + 'px';
    d.innerHTML = `<span class="top"></span><div class="ic">${icon(ICON[k], 28)}</div><b>${b}</b><span>${s}</span>`;
    $('fgrid').appendChild(d); return d;
  });

  // S8 use cases
  const uc = [['use-scientific-publication.png', 'Scientific publications', 'Interactive 3D in articles and supplementary material.'],
    ['use-theses-dissertations.png', 'Theses & dissertations', 'Anatomical, engineering and scientific models alongside your thesis.'],
    ['use-medical-education.png', 'Medical education', 'Anatomy, surgical planning and simulation models for students.'],
    ['use-research-projects.png', 'Research projects', 'Stable links and QR codes for outputs and datasets.']];
  M.ucards = uc.map(([img, b, s], i) => {
    const d = document.createElement('div'); d.className = 'ucard'; d.style.left = 106 + i * 436 + 'px';
    d.innerHTML = `<div class="im"><img src="assets/${img}"></div><b>${b}</b><span>${s}</span>`;
    $('ugrid').appendChild(d); return d;
  });

  // S9
  M.s9chips = ['CT/MR DICOM series', 'NIfTI · NRRD · DICOM-SEG', 'Invite links', 'Quota-funded licences'].map((l, i) => {
    const d = document.createElement('span'); d.className = 'chip'; d.style.fontSize = '21px'; d.style.padding = '12px 20px';
    d.innerHTML = `<span class="dot" style="background:${i < 2 ? '#ff682c' : '#202020'}"></span>${l}`; $('s9chips').appendChild(d); return d;
  });
  const avc = [['#202020', 'EK'], ['#ff682c', 'MY'], ['#816729', 'SA'], ['#4d4d4d', 'DT'], ['#6b6b6b', 'AB'], ['#c9bfae', 'LN']];
  M.avs = avc.map(([c, l]) => { const d = document.createElement('span'); d.className = 'av'; d.style.background = c; d.textContent = l; $('avs').appendChild(d); return d; });
  const more = document.createElement('span'); more.style.cssText = 'margin-left:18px;font:500 22px/1 Int;color:#6b6b6b'; more.textContent = '+38 researchers';
  $('avs').appendChild(more); M.avs.push(more);

  // S10 orbit
  const ob = $('orbit');
  M.ell = svgEl('ellipse', { cx: 960, cy: 560, rx: 860, ry: 330, fill: 'none', stroke: '#e6e6e6', 'stroke-width': 2, transform: 'rotate(-8 960 560)' }, ob);
  M.ell2 = svgEl('ellipse', { cx: 960, cy: 560, rx: 700, ry: 250, fill: 'none', stroke: '#ff682c', 'stroke-width': 2, 'stroke-dasharray': '6 12', opacity: 0.5, transform: 'rotate(10 960 560)' }, ob);
  M.odots = ['#ff682c', '#202020', '#816729', '#c9c9c9', '#ff682c'].map((c) => svgEl('circle', { r: 11, fill: c }, ob));

  // measure positions inside S2 / S3
  $('s2').style.display = 'block'; M.wordW = $('s2word').offsetWidth; $('s2').style.display = 'none';
  $('s3').style.display = 'block';
  const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; };
  M.box = r($('cbox')); M.btn = r($('ubtn')); M.drop = r($('drop'));
  $('s3').style.display = 'none';
}
async function fetchQR() {
  const q = await (await fetch('assets/qr.json')).json();
  const svg = $('qrbig'), n = q.n, cell = 360 / (n + 4);
  svgEl('rect', { x: 0, y: 0, width: 360, height: 360, fill: '#fff', rx: 10 }, svg);
  M.qr = []; const R = rng(7);
  for (let i = 0; i < n * n; i++) if (q.d[i]) {
    const x = i % n, y = Math.floor(i / n);
    M.qr.push({ el: svgEl('rect', { x: (x + 2) * cell, y: (y + 2) * cell, width: cell + 0.4, height: cell + 0.4, fill: '#202020', opacity: 0 }, svg), k: R() });
  }
}

/* ------------------------------------------------------------------ scenes */
const SC = [
  ['s1', 0, 5.6, u1], ['s2', 5.4, 10.8, u2], ['s3', 10.6, 19.2, u3], ['s4', 19.0, 27.4, u4], ['s5', 27.2, 40.6, u5],
  ['s6', 40.4, 48.6, u6], ['s7', 48.4, 55.6, u7], ['s8', 55.4, 61.2, u8], ['s9', 61.0, 67.2, u9], ['s10', 67.0, DUR, u10],
];

function u1(l) {
  words($('s1a'), l, 0.25, 0.12, 0.7);
  const k = E.io(P(l, 2.1, 2.7));
  S($('s1a'), { o: 1 - k, y: -70 * k, s: 1 - 0.08 * k });
  const ki = E.out(P(l, 2.35, 3.25)), kz = E.io(P(l, 4.5, 5.6));
  S($('s1paper'), { o: ki, y: (1 - ki) * 220, r: -2 * (1 - kz) - 3 * (1 - ki), s: 1 + kz * 4.5 });
  const kb = E.out(P(l, 3.0, 3.8));
  S($('s1b'), { o: kb * (1 - P(l, 4.4, 4.8)), x: (1 - kb) * 70 });
}
function u2(l) {
  M.grid.forEach((ln, i) => { const L = +ln.getAttribute('stroke-dasharray'); ln.setAttribute('stroke-dashoffset', L * (1 - E.io(P(l, 0.1 + i * 0.05, 1.4 + i * 0.05)))); });
  const total = 200 + 54 + M.wordW, sx = (1920 - total) / 2, ty = 330;
  const ks = E.back(P(l, 0, 0.7)), km = E.io(P(l, 0.9, 1.6));
  const tx = lerp(860, sx, km);
  const tile = $('s2tile'); tile.style.left = tx + 'px'; tile.style.top = ty + 'px';
  S(tile, { o: P(l, 0, 0.2), s: Math.max(0.001, ks) });
  [['s2ring1', 0.15], ['s2ring2', 0.45]].forEach(([id, d]) => {
    const k = E.out(P(l, d, d + 1.3)), D = 200 + 560 * k, el = $(id);
    el.style.width = el.style.height = D + 'px'; el.style.left = 960 - D / 2 + 'px'; el.style.top = ty + 100 - D / 2 + 'px';
    el.style.opacity = k > 0 && k < 1 ? 0.8 * (1 - k) : 0;
  });
  const kw = E.out(P(l, 1.1, 2.0)), wrap = $('s2wordwrap');
  wrap.style.left = sx + 254 + 'px'; wrap.style.top = ty + 12 + 'px'; wrap.style.width = (M.wordW + 10) * kw + 'px'; wrap.style.visibility = kw > 0.005 ? 'visible' : 'hidden';
  $('s2word').style.transform = `translateX(${(1 - kw) * -60}px)`;
  S($('s2tag'), inn(l, 2.0, 0.8));
  [...$('s2checks').children].forEach((c, i) => { const v = inn(l, 2.7 + i * 0.12, 0.6, 24); S(c, { ...v, s: 0.94 + 0.06 * v.o }); });
}
function u3(l) {
  S($('s3e'), inn(l, 0.1)); words($('s3h'), l, 0.25, 0.08); S($('s3s'), inn(l, 0.9));
  const kc = E.out(P(l, 0.3, 1.1)); S($('up'), { o: kc, x: (1 - kc) * 90 });
  const dropT = { x: M.drop.x - 190, y: M.drop.y - 26 };
  M.chips.forEach((c, i) => {
    const k = E.out(P(l, 0.9 + i * 0.16, 1.6 + i * 0.16));
    let x = (1 - k) * 520, y = (1 - k) * (i % 2 ? -60 : 60), o = k, s = 1, r = (1 - k) * 8;
    y += Math.sin(l * 2 + i) * 6 * k;
    if (i > 0) { const kf = E.io(P(l, 2.5 + i * 0.04, 2.95 + i * 0.04)); o *= 1 - kf; s = 1 - 0.2 * kf; }
    else {
      const km = E.io(P(l, 2.5, 3.2)), kd = E.io(P(l, 3.2, 3.5));
      x = lerp(x, dropT.x - parseFloat(c.style.left), km); y = lerp(y, dropT.y - parseFloat(c.style.top), km);
      s = 1 + 0.08 * km - 0.3 * kd; o *= 1 - kd;
    }
    S(c, { o, x, y, s, r });
  });
  const hov = l > 2.6 && l < 3.5;
  $('drop').style.borderColor = hov ? '#ff682c' : '#cfcfcf'; $('drop').style.background = hov ? '#fff6f2' : '#fcfcfc';
  S($('frow'), inn(l, 3.25, 0.5, 16));
  const pc = E.io(P(l, 3.4, 5.0)); $('pfill').style.width = pc * 100 + '%'; $('pct').textContent = Math.round(pc * 100) + '%';
  $('pct').style.color = pc >= 1 ? '#202020' : '#6b6b6b';
  S($('chk'), inn(l, 1.2, 0.6, 16));
  const ticked = l > 5.4;
  $('cbox').style.background = ticked ? '#202020' : '#fff'; $('cbox').style.borderColor = ticked ? '#202020' : '#bdbdbd';
  $('ctick').setAttribute('stroke-dashoffset', 20 * (1 - E.out(P(l, 5.4, 5.7))));
  // cursor path
  const kp = [[4.4, 1820, 1040], [5.25, M.box.x, M.box.y], [5.95, M.btn.x, M.btn.y], [7.6, M.btn.x + 120, M.btn.y + 140]];
  let cx = kp[0][1], cy = kp[0][2];
  for (let i = 0; i < kp.length - 1; i++) {
    const [ta, xa, ya] = kp[i], [tb, xb, yb] = kp[i + 1];
    if (l >= ta) { const k = E.io(P(l, ta, tb)); cx = lerp(xa, xb, k); cy = lerp(ya, yb, k); }
  }
  const press = (l > 5.3 && l < 5.45) || (l > 6.05 && l < 6.2);
  S($('cursor'), { o: P(l, 4.4, 4.7) * (1 - P(l, 7.4, 7.8)), x: cx - 7, y: cy - 4, s: press ? 0.85 : 1 });
  $('cursor').style.left = '0px'; $('cursor').style.top = '0px';
  const kb = l > 6.05 && l < 6.25 ? 0.95 : 1;
  const done = l > 6.15;
  $('ubtn').style.background = done ? '#ff682c' : '#202020';
  $('ubtn').innerHTML = done ? 'Queued for conversion ✓' : 'Upload &amp; convert →';
  S($('ubtn'), { o: 1, s: kb });
}
function u4(l) {
  S($('s4e'), inn(l, 0.1)); words($('s4h'), l, 0.2, 0.07);
  M.inG.forEach((g, i) => { const v = inn(l, 0.4 + i * 0.08, 0.6, 20); g.style.opacity = v.o; g.style.transform = `translate(${-30 * (1 - v.o)}px,0)`; });
  M.pin.forEach((p, i) => (p.style.strokeDashoffset = p._L * (1 - E.io(P(l, 1.0 + i * 0.05, 1.8 + i * 0.05)))));
  M.pout.forEach((p, i) => (p.style.strokeDashoffset = p._L * (1 - E.io(P(l, 3.0 + i * 0.1, 3.7 + i * 0.1)))));
  const kn = E.back(P(l, 0.8, 1.4)); M.node.style.opacity = P(l, 0.8, 1.0);
  M.node.style.transformOrigin = '520px 340px'; M.node.style.transform = `scale(${Math.max(0.01, kn)})`;
  M.nodeArc.setAttribute('transform', `rotate(${l * 220} 520 340)`);
  M.nodeArc.style.opacity = l < 5.0 ? 1 : 1 - P(l, 5.0, 5.4);
  M.outG.forEach((g, i) => { const v = inn(l, 3.5 + i * 0.15, 0.6, 20); g.style.opacity = v.o; g.style.transform = `translate(${30 * (1 - v.o)}px,0)`; });
  M.dots.forEach((d, i) => {
    let p, ph, vis;
    if (i < 6) { p = M.pin[i]; ph = (l * 0.55 + i * 0.17) % 1; vis = l > 1.8 && l < 4.9; }
    else { p = M.pout[i - 6]; ph = (l * 0.6 + i * 0.23) % 1; vis = l > 3.8 && l < 5.6; }
    const pt = p.getPointAtLength(ph * p._L); d.setAttribute('cx', pt.x); d.setAttribute('cy', pt.y);
    d.setAttribute('opacity', vis ? Math.sin(ph * Math.PI) : 0);
  });
  const kv = E.out(P(l, 0.5, 1.2)); S($('va'), { o: kv, x: (1 - kv) * 60 });
  const st = l < 1.6 ? 0 : l < 4.9 ? 1 : 2;
  $('vstxt').textContent = ['Queued', 'Processing…', 'Ready'][st];
  $('vdot').style.background = ['#bdbdbd', '#ff682c', '#202020'][st];
  $('vstat').style.background = st === 2 ? '#fff' : '#efefef'; $('vstat').style.border = st === 2 ? '1px solid #202020' : '1px solid transparent';
  $('vspin').style.opacity = 1 - P(l, 4.8, 5.1);
  $('vspinarc').setAttribute('transform', `rotate(${l * 300} 60 60)`);
  const km = E.out(P(l, 4.9, 5.8));
  if (V.A) {
    V.A.root.visible = km > 0; V.A.root.scale.setScalar(0.8 + 0.2 * km);
    V.A.root.rotation.y = -0.6 + l * 0.55; V.A.renderer.domElement.style.opacity = km;
  }
  const kz = E.io(P(l, 5.0, 6.2));
  $('szB').textContent = lerp(9.4, 1.6, kz).toFixed(1) + ' MB'; $('szP').textContent = '−' + Math.round(83 * kz) + '%';
  S($('s4size'), inn(l, 4.9, 0.6, 14));
}
function u5(l) {
  const kv = E.out(P(l, 0.1, 0.9)); S($('vb'), { o: kv, s: 0.96 + 0.04 * kv });
  S($('s5e'), inn(l, 0.3)); S($('s5h'), inn(l, 0.45));
  const phase = l < 3 ? 0 : l < 6.5 ? 1 : l < 10 ? 2 : 3;
  M.tools.forEach((t, i) => t.classList.toggle('on', i === phase));
  M.feats.forEach((f, i) => {
    const v = inn(l, 0.7 + i * 0.12, 0.6, 20);
    S(f, { o: v.o * (i === phase ? 1 : 0.4), y: v.y });
    f.querySelector('.ind').style.opacity = i === phase ? 1 : 0;
  });
  const kl = E.out(P(l, 3.0, 3.7)); S($('layers'), { o: kl, x: (1 - kl) * -40 });
  const op = 1 - 0.78 * E.io(P(l, 4.0, 5.0)) + 0.78 * E.io(P(l, 6.4, 7.0));
  $('bop').textContent = Math.round(op * 100) + '%'; $('bfill').style.width = op * 100 + '%'; $('bknob').style.left = `calc(${op * 100}% - 11px)`;
  const ksec = E.io(P(l, 7.3, 9.4)) * (1 - E.io(P(l, 9.8, 10.5)));
  const cpos = lerp(1.15, -0.05, ksec);
  const kg = P(l, 6.9, 7.3) * (1 - P(l, 9.9, 10.4));
  const sp = inn(l, 6.9, 0.5, 10); S($('secpill'), { o: sp.o * (1 - P(l, 9.9, 10.3)), y: sp.y });
  if (V.B) {
    const B = V.B;
    B.boneMat.opacity = op; B.boneMat.depthWrite = op > 0.99; B.boneMat.transparent = op < 0.999;
    B.plane.constant = l > 6.8 && l < 10.6 ? cpos : 5;
    B.cap.visible = l > 6.8 && l < 10.6 && op > 0.99;
    B.guide.visible = kg > 0; B.guide.position.x = cpos; B.guideMat.opacity = 0.14 * kg; B.edgeMat.opacity = kg;
    const az = 0.3 + 0.18 * l, el = 0.36, r = 5.5;
    B.camera.position.set(r * Math.cos(el) * Math.sin(az), r * Math.sin(el), r * Math.cos(el) * Math.cos(az));
    B.camera.lookAt(0, -0.05, 0); B.camera.updateMatrixWorld();
    const pr = B.mpts.map((p) => { const v = p.clone().project(B.camera); return { x: (v.x + 1) / 2 * 1200, y: (1 - v.y) / 2 * 820 }; });
    const k1 = E.back(P(l, 10.6, 10.95)), k2 = E.back(P(l, 10.85, 11.2)), kln = E.io(P(l, 11.0, 11.8));
    M.mp.forEach((g, i) => { const k = i ? k2 : k1; g.setAttribute('transform', `translate(${pr[i].x} ${pr[i].y}) scale(${Math.max(0.001, k)})`); g.style.opacity = k > 0 ? 1 : 0; });
    M.mline.setAttribute('x1', pr[0].x); M.mline.setAttribute('y1', pr[0].y);
    M.mline.setAttribute('x2', lerp(pr[0].x, pr[1].x, kln)); M.mline.setAttribute('y2', lerp(pr[0].y, pr[1].y, kln));
    M.mline.style.opacity = kln > 0 ? 1 : 0;
    const dist = B.mpts[0].distanceTo(B.mpts[1]) * mm;
    const lab = $('mlab'); lab.textContent = (dist * kln).toFixed(1) + ' mm';
    const mx = (pr[0].x + pr[1].x) / 2, my = Math.min(pr[0].y, pr[1].y) - 70;
    lab.style.left = mx - 70 + 'px'; lab.style.top = my + 'px'; lab.style.opacity = P(l, 11.0, 11.3); lab.style.zIndex = 4;
  }
}
function u6(l) {
  S($('s6e'), inn(l, 0.1)); words($('s6h'), l, 0.25, 0.08); S($('s6s'), inn(l, 1.0)); S($('s6url'), inn(l, 1.3));
  if (M.qr) M.qr.forEach((m) => m.el.setAttribute('opacity', l > 0.4 + m.k * 1.3 ? 1 : 0));
  const kq = E.io(P(l, 2.0, 2.9)), q = $('qrbig');
  const x0 = 900, y0 = 360, x1 = 804, y1 = 706, sc = lerp(1, 150 / 360, kq);
  q.style.left = '0px'; q.style.top = '0px'; q.style.transformOrigin = '0 0';
  const ki = E.back(P(l, 0.2, 0.8));
  S(q, { o: P(l, 0.2, 0.4), x: lerp(x0, x1, kq), y: lerp(y0, y1, kq), s: sc * (0.85 + 0.15 * Math.min(1, ki)) });
  const kp = E.out(P(l, 1.8, 2.6)); S($('poster'), { o: kp, y: (1 - kp) * 60 });
  const kph = E.out(P(l, 3.2, 4.1)); S($('phone'), { o: kph, x: (1 - kph) * 320, r: (1 - kph) * 6 });
  const kbm = P(l, 4.1, 4.4) * (1 - P(l, 5.1, 5.5));
  const beam = $('beam'); beam.innerHTML = '';
  if (kbm > 0) {
    const px = 1390 + 242, py = 120 + 330;
    svgEl('polygon', { points: `${px - 60},${py - 60} ${px - 60},${py + 60} 879,856 879,706`, fill: 'rgba(255,104,44,0.10)', stroke: 'rgba(255,104,44,0.55)', 'stroke-width': 2, opacity: kbm }, beam);
    svgEl('rect', { x: 800, y: 702, width: 158, height: 158, fill: 'none', stroke: '#ff682c', 'stroke-width': 4, rx: 8, opacity: kbm }, beam);
  }
  $('scanline').setAttribute('y', 220 + 220 * (0.5 - 0.5 * Math.cos(l * 4.2)));
  $('pscreen').style.opacity = 1 - E.io(P(l, 5.2, 5.8));
  const kc = inn(l, 5.7, 0.6, 16); S($('s6cap'), kc);
}
function u7(l) {
  S($('s7e'), inn(l, 0.1)); S($('s7h'), inn(l, 0.25, 0.8, 40));
  M.fcards.forEach((c, i) => {
    const k = E.out(P(l, 0.6 + i * 0.09, 1.3 + i * 0.09));
    S(c, { o: k, y: (1 - k) * 60, s: 0.94 + 0.06 * k });
    c.querySelector('.top').style.width = E.io(P(l, 2.1 + i * 0.45, 2.5 + i * 0.45)) * 100 + '%';
    const act = l > 2.1 + i * 0.45 && l < 2.55 + i * 0.45;
    c.style.borderColor = act ? '#ff682c' : '#e8e8e8';
  });
}
function u8(l) {
  S($('s8e'), inn(l, 0.1)); S($('s8h'), inn(l, 0.25, 0.8, 40));
  M.ucards.forEach((c, i) => {
    const k = E.out(P(l, 0.5 + i * 0.15, 1.3 + i * 0.15));
    S(c, { o: k, y: (1 - k) * 90 - (i % 2 ? 0 : 0), x: -26 * (l / 5.8) });
    c.querySelector('img').style.transform = `scale(${1.12 - 0.08 * (l / 5.8)})`;
  });
}
function u9(l) {
  S($('s9e'), inn(l, 0.1)); words($('s9h'), l, 0.25, 0.08); S($('s9s'), inn(l, 0.9));
  M.s9chips.forEach((c, i) => S(c, inn(l, 1.3 + i * 0.12, 0.5, 16)));
  const kc = E.out(P(l, 0.3, 1.1)); S($('inst'), { o: kc, x: (1 - kc) * 90 });
  const kq = E.io(P(l, 1.1, 2.7));
  $('qm').textContent = Math.round(142 * kq); $('qmf').style.width = 71 * kq + '%';
  $('qs').textContent = (38.4 * kq).toFixed(1); $('qsf').style.width = 76.8 * kq + '%';
  M.avs.forEach((a, i) => { const k = E.back(P(l, 2.0 + i * 0.09, 2.45 + i * 0.09)); S(a, { o: P(l, 2.0 + i * 0.09, 2.2 + i * 0.09), s: Math.max(0.001, k) }); });
  S($('showc'), inn(l, 3.0, 0.6, 20));
}
function u10(l) {
  const kl = inn(l, 0.2, 0.7, 20); S($('s10logo'), kl);
  words($('s10h'), l, 0.5, 0.1, 0.8);
  const ku = E.back(P(l, 1.7, 2.3)); S($('urlpill'), { o: P(l, 1.7, 1.9), s: Math.max(0.001, 0.8 + 0.2 * ku) });
  $('urlpill').style.borderColor = l > 2.3 ? '#202020' : '#e8e8e8';
  S($('s10s'), inn(l, 2.4, 0.7, 20));
  const ke = E.io(P(l, 0, 1.6));
  [M.ell, M.ell2].forEach((e) => { e.style.opacity = ke * (e === M.ell2 ? 0.5 : 1); });
  M.odots.forEach((d, i) => {
    const e = i % 2 ? [700, 250, 10] : [860, 330, -8], a = l * (i % 2 ? -0.32 : 0.26) + i * 1.3, rot = e[2] * Math.PI / 180;
    const x = e[0] * Math.cos(a), y = e[1] * Math.sin(a);
    d.setAttribute('cx', 960 + x * Math.cos(rot) - y * Math.sin(rot)); d.setAttribute('cy', 560 + x * Math.sin(rot) + y * Math.cos(rot));
    d.setAttribute('opacity', ke);
  });
}

/* ------------------------------------------------------------------ render */
function update(t) {
  for (const [id, a, b, fn] of SC) {
    const el = $(id), on = t >= a && t < b + (id === 's10' ? 1 : 0);
    el.style.display = on ? 'block' : 'none';
    if (!on) continue;
    const kin = id === 's1' ? 1 : P(t, a, a + 0.4);
    const kout = id === 's10' ? 0 : E.io(P(t, b - 0.45, b));
    el.style.opacity = kin * (1 - kout);
    el.style.transform = `translateY(${-30 * kout}px) scale(${1 + 0.018 * P(t, a, b)})`;
    fn(t - a);
  }
  const kh = P(t, 10.6, 11.1) * (1 - P(t, 66.6, 67.1));
  $('hdr').style.opacity = kh;
  $('prog').style.width = (t / DUR) * 1920 + 'px';
  $('prog').style.opacity = kh;
  $('dots').style.opacity = 0.55 * (1 - 0.6 * P(t, 27.2, 27.8) + 0.6 * P(t, 40.2, 40.8));
}
function render3D(t) {
  if (!V.A) return;
  if (t >= 19.0 && t < 27.4) V.A.renderer.render(V.A.scene, V.A.camera);
  if (t >= 27.2 && t < 40.6) V.B.renderer.render(V.B.scene, V.B.camera);
}
window.renderAt = async (t) => {
  update(t); render3D(t);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  return true;
};
window.ready = (async () => {
  setup();
  await document.fonts.ready;
  await Promise.all([...document.images].map((i) => i.decode().catch(() => {})));
  await init3D();
  await new Promise((r) => setTimeout(r, 300));
  return true;
})();
window.DUR = DUR;

// live preview: ?play or ?t=12.3
const qs = new URLSearchParams(location.search);
window.ready.then(() => {
  if (qs.has('t')) window.renderAt(parseFloat(qs.get('t')));
  else if (qs.has('play')) { const t0 = performance.now(); const loop = () => { update(((performance.now() - t0) / 1000) % DUR); render3D(((performance.now() - t0) / 1000) % DUR); requestAnimationFrame(loop); }; loop(); }
});
