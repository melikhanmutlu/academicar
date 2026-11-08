// Presenter mode (templates/presenter.html): a material's PDF pages as slides,
// with live <model-viewer> slides placed between them. A service worker
// (/present-sw.js) keeps everything the page loads, so after
// "Make available offline" the talk runs without internet.
const data = JSON.parse(document.getElementById('presenter-data').textContent);
const shell = document.getElementById('presenter');
const stage = document.getElementById('stage');
const countEl = document.getElementById('count');
const blackEl = document.getElementById('black');
const modelsById = Object.fromEntries((data.models || []).map((m) => [m.id, m]));

let pdf = null;
let pageCount = 0;
let slides = (data.slides || []).filter((s) => modelsById[s.model_id]);
let sequence = [];
let index = 0;
let slideEl = null;
const viewers = new Map(); // sequence key -> <model-viewer>
const thumbs = new Map();  // page number -> data URL
let renderToken = 0;

// ---- sequence ------------------------------------------------------------

function buildSequence() {
  const seq = [];
  for (let after = 0; after <= pageCount; after += 1) {
    if (after > 0) seq.push({ type: 'page', page: after, key: `p${after}` });
    slides.forEach((s, i) => {
      if (s.after === after) seq.push({ type: 'model', model: modelsById[s.model_id], slide: i, key: `m${i}-${s.model_id}` });
    });
  }
  sequence = seq;
  index = Math.min(index, Math.max(0, sequence.length - 1));
}

// ---- layout --------------------------------------------------------------

let aspect = 16 / 9;

function fitSlide() {
  if (!slideEl) return;
  const fs = document.fullscreenElement === shell;
  const pad = fs ? 0 : parseFloat(getComputedStyle(stage).paddingLeft) * 2;
  const w = stage.clientWidth - pad;
  const h = stage.clientHeight - pad;
  let sw = w;
  let sh = w / aspect;
  if (sh > h) { sh = h; sw = h * aspect; }
  slideEl.style.width = `${Math.floor(sw)}px`;
  slideEl.style.height = `${Math.floor(sh)}px`;
}

function ensureSlideEl() {
  if (slideEl) return slideEl;
  stage.querySelector('#loading-msg')?.remove();
  slideEl = document.createElement('div');
  slideEl.className = 'p-slide';
  stage.insertBefore(slideEl, blackEl);
  fitSlide();
  return slideEl;
}

// ---- rendering -----------------------------------------------------------

async function renderPage(pageNumber, target, cssWidth) {
  const page = await pdf.getPage(pageNumber);
  const base = page.getViewport({ scale: 1 });
  const scale = (cssWidth / base.width) * Math.min(window.devicePixelRatio || 1, 2.5);
  const viewport = page.getViewport({ scale });
  target.width = Math.round(viewport.width);
  target.height = Math.round(viewport.height);
  await page.render({ canvasContext: target.getContext('2d'), viewport }).promise;
}

function hideLayers(viewer, model) {
  const hidden = new Set(model.hidden || []);
  const faded = model.faded || {};
  for (const material of viewer.model?.materials || []) {
    const alpha = hidden.has(material.name) ? 0 : faded[material.name];
    if (alpha === undefined) continue;
    const pbr = material.pbrMetallicRoughness;
    const [r, g, b] = pbr.baseColorFactor;
    material.setAlphaMode('BLEND');
    pbr.setBaseColorFactor([r, g, b, alpha]);
  }
}

function modelViewerFor(item) {
  let viewer = viewers.get(item.key);
  if (viewer) return viewer;
  const m = item.model;
  viewer = document.createElement('model-viewer');
  viewer.setAttribute('src', m.src);
  if (m.poster) viewer.setAttribute('poster', m.poster);
  viewer.setAttribute('alt', `3D model: ${m.name}`);
  viewer.setAttribute('camera-controls', '');
  viewer.setAttribute('touch-action', 'none');
  viewer.setAttribute('interaction-prompt', 'none');
  viewer.setAttribute('tone-mapping', 'neutral');
  viewer.setAttribute('camera-orbit', '0deg 90deg auto');
  viewer.setAttribute('field-of-view', '28deg');
  viewer.setAttribute('exposure', String(m.exposure ?? 1));
  viewer.setAttribute('shadow-intensity', String(m.shadow_intensity ?? 1));
  viewer.setAttribute('shadow-softness', String(m.shadow_softness ?? 1));
  if (m.environment) viewer.setAttribute('environment-image', m.environment);
  viewer.setAttribute('loading', 'eager');
  viewer.addEventListener('load', () => hideLayers(viewer, m));
  viewers.set(item.key, viewer);
  return viewer;
}

async function show(i) {
  if (!sequence.length) return;
  index = Math.max(0, Math.min(sequence.length - 1, i));
  const item = sequence[index];
  const token = ++renderToken;
  const el = ensureSlideEl();
  countEl.textContent = `${index + 1} / ${sequence.length}`;
  updateList();

  if (item.type === 'page') {
    const canvas = document.createElement('canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `Slide ${item.page}`);
    await renderPage(item.page, canvas, el.clientWidth || 1280);
    if (token !== renderToken) return;
    el.replaceChildren(canvas);
  } else {
    const viewer = modelViewerFor(item);
    const label = document.createElement('div');
    label.className = 'p-model-label';
    label.innerHTML = '<b>3D</b>';
    label.append(document.createTextNode(item.model.name));
    const hint = document.createElement('div');
    hint.className = 'p-model-hint';
    hint.textContent = 'Drag to rotate · scroll to zoom';
    el.replaceChildren(viewer, label, hint);
  }
  preloadAround(index);
}

function resetViewers() {
  viewers.forEach((viewer) => viewer.remove());
  viewers.clear();
}

function preloadAround(i) {
  // Start loading the next 3D model before it is shown, so it appears at once.
  for (const j of [i + 1, i + 2]) {
    const item = sequence[j];
    if (item?.type === 'model' && !viewers.has(item.key)) {
      const viewer = modelViewerFor(item);
      viewer.style.cssText = 'position:fixed;left:-10000px;top:0;width:320px;height:180px';
      document.body.appendChild(viewer);
      viewer.addEventListener('load', () => { viewer.style.cssText = ''; }, { once: true });
    }
  }
}

// ---- navigation ----------------------------------------------------------

const next = () => show(index + 1);
const prev = () => show(index - 1);

document.getElementById('next-btn').addEventListener('click', next);
document.getElementById('prev-btn').addEventListener('click', prev);
document.getElementById('present-btn').addEventListener('click', () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else shell.requestFullscreen?.().catch(() => {});
});

window.addEventListener('keydown', (event) => {
  if (event.target.closest?.('select, input, textarea')) return;
  const key = event.key;
  let handled = true;
  if (['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter', 'n', 'N'].includes(key)) next();
  else if (['ArrowLeft', 'ArrowUp', 'PageUp', 'Backspace', 'p', 'P'].includes(key)) prev();
  else if (key === 'Home') show(0);
  else if (key === 'End') show(sequence.length - 1);
  else if (key === 'f' || key === 'F') document.getElementById('present-btn').click();
  else if (key === 'b' || key === 'B' || key === '.') blackEl.classList.toggle('is-on');
  else handled = false;
  if (handled) {
    // Capture phase: keep model-viewer from also turning the model on arrow keys.
    event.preventDefault();
    event.stopPropagation();
  }
}, true);

let navTimer = null;
shell.addEventListener('pointermove', () => {
  shell.classList.add('show-nav');
  clearTimeout(navTimer);
  navTimer = setTimeout(() => shell.classList.remove('show-nav'), 2000);
});
document.addEventListener('fullscreenchange', () => {
  document.getElementById('present-btn').textContent = document.fullscreenElement ? 'Exit full screen' : 'Start presenting';
  requestAnimationFrame(() => { fitSlide(); show(index); });
});
let resizeTimer = null;
window.addEventListener('resize', () => {
  fitSlide();
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (sequence[index]?.type === 'page') show(index); }, 150);
});

// ---- 3D slide editing ----------------------------------------------------

const editBtn = document.getElementById('edit-btn');
const editPanel = document.getElementById('edit-panel');
const list = document.getElementById('slide-list');
const savedMsg = document.getElementById('saved-msg');

editBtn?.addEventListener('click', () => {
  const open = editPanel.hidden;
  editPanel.hidden = !open;
  editBtn.setAttribute('aria-pressed', String(open));
  requestAnimationFrame(() => { fitSlide(); show(index); });
});

async function thumbFor(pageNumber) {
  if (thumbs.has(pageNumber)) return thumbs.get(pageNumber);
  const canvas = document.createElement('canvas');
  await renderPage(pageNumber, canvas, 64);
  const url = canvas.toDataURL('image/png');
  thumbs.set(pageNumber, url);
  return url;
}

function updateList() {
  if (!list || editPanel.hidden) return;
  list.replaceChildren(...sequence.map((item, i) => {
    const li = document.createElement('li');
    li.className = `${item.type === 'model' ? 'is-model' : ''} ${i === index ? 'is-current' : ''}`;
    const thumb = document.createElement('span');
    thumb.className = 'p-thumb';
    const name = document.createElement('span');
    name.className = 'p-name';
    if (item.type === 'page') {
      name.textContent = `Slide ${item.page}`;
      thumbFor(item.page).then((url) => {
        const img = new Image();
        img.alt = '';
        img.src = url;
        thumb.replaceChildren(img);
      });
    } else {
      thumb.textContent = '3D';
      name.textContent = item.model.name;
    }
    li.append(thumb, name);
    if (item.type === 'model') {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'p-remove';
      remove.setAttribute('aria-label', `Remove the 3D slide ${item.model.name}`);
      remove.textContent = '✕';
      remove.addEventListener('click', (event) => {
        event.stopPropagation();
        slides.splice(item.slide, 1);
        resetViewers();
        buildSequence();
        save();
        show(index);
      });
      li.append(remove);
    }
    li.addEventListener('click', () => show(i));
    if (i === index) requestAnimationFrame(() => li.scrollIntoView({ block: 'nearest' }));
    return li;
  }));
}

document.getElementById('add-btn')?.addEventListener('click', () => {
  const modelId = document.getElementById('add-model').value;
  const current = sequence[index];
  // After the current page; a 3D slide goes after the page it follows.
  const after = current ? (current.type === 'page' ? current.page : slides[current.slide].after) : 0;
  const insertAt = current?.type === 'model' ? current.slide + 1 : slides.filter((s) => s.after <= after).length;
  slides.splice(insertAt, 0, { after, model_id: modelId });
  resetViewers();
  buildSequence();
  save();
  show(sequence.findIndex((s) => s.type === 'model' && s.slide === insertAt));
});

async function save() {
  savedMsg.textContent = 'Saving…';
  try {
    const response = await fetch(data.save_url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': data.csrf },
      body: JSON.stringify({ page_count: pageCount, slides }),
    });
    if (!response.ok) throw new Error(String(response.status));
    savedMsg.textContent = 'Saved. Make the talk available offline again to keep the change.';
  } catch {
    savedMsg.textContent = navigator.onLine ? 'Could not save. Try again.' : 'Offline: this change is kept until you reload.';
  }
}

// ---- offline -------------------------------------------------------------

const offlineBtn = document.getElementById('offline-btn');
const offlineStatus = document.getElementById('offline-status');
const OFFLINE_KEY = `academicar-present:${location.pathname}`;

function setOffline(state, text) {
  offlineStatus.className = `p-offline ${state ? `is-${state}` : ''}`;
  offlineStatus.querySelector('span').textContent = text;
}

function offlineUrls() {
  const urls = [location.href, data.pdfjs, data.pdfjs_worker, data.model_viewer, data.presenter_js, ...data.draco];
  if (data.pdf_url) urls.push(data.pdf_url);
  for (const m of data.models) {
    if (!slides.some((s) => s.model_id === m.id)) continue;
    urls.push(m.src);
    if (m.poster) urls.push(m.poster);
    if (m.environment && m.environment.startsWith('/')) urls.push(m.environment);
  }
  return [...new Set(urls)];
}

async function makeOffline() {
  if (!('serviceWorker' in navigator) || !window.caches) {
    setOffline('', 'This browser cannot save the talk offline');
    return;
  }
  offlineBtn.disabled = true;
  setOffline('busy', 'Saving for offline…');
  try {
    await navigator.serviceWorker.register(data.sw_url, { scope: location.pathname });
    await navigator.serviceWorker.ready;
    const urls = offlineUrls();
    const cache = await caches.open('academicar-present-v1');
    let bytes = 0;
    for (const [n, url] of urls.entries()) {
      setOffline('busy', `Saving for offline… ${n + 1}/${urls.length}`);
      const cross = new URL(url, location.href).origin !== location.origin;
      const response = await fetch(url, { cache: 'reload', mode: cross ? 'cors' : 'same-origin', credentials: cross ? 'omit' : 'same-origin' });
      if (!response.ok) throw new Error(`${response.status} ${url}`);
      const blob = await response.clone().blob();
      bytes += blob.size;
      await cache.put(url, response);
    }
    await navigator.storage?.persist?.();
    const mb = (bytes / 1048576).toFixed(bytes > 10485760 ? 0 : 1);
    try { localStorage.setItem(OFFLINE_KEY, JSON.stringify({ at: Date.now(), mb, slides })); } catch { /* private mode */ }
    setOffline('ready', `Ready offline · ${mb} MB`);
  } catch (error) {
    console.warn('Offline save failed', error);
    setOffline('', navigator.onLine ? 'Offline save failed. Try again.' : 'Connect to the internet to save offline');
  } finally {
    offlineBtn.disabled = false;
  }
}

offlineBtn.addEventListener('click', makeOffline);

async function checkOffline() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(OFFLINE_KEY) || 'null'); } catch { /* ignore */ }
  if (!saved || !window.caches) return;
  const cache = await caches.open('academicar-present-v1');
  const missing = [];
  for (const url of offlineUrls()) if (!(await cache.match(url, { ignoreVary: true }))) missing.push(url);
  if (missing.length) setOffline('', 'Changed since last offline save');
  else setOffline('ready', `Ready offline · ${saved.mb} MB`);
}

// ---- start ---------------------------------------------------------------

async function start() {
  if (!data.pdf_url) return;
  try {
    const pdfjs = await import(data.pdfjs);
    pdfjs.GlobalWorkerOptions.workerSrc = data.pdfjs_worker;
    const response = await fetch(data.pdf_url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(String(response.status));
    const bytes = new Uint8Array(await response.arrayBuffer());
    pdf = await pdfjs.getDocument({ data: bytes, isEvalSupported: false }).promise;
    pageCount = pdf.numPages;
    const first = (await pdf.getPage(1)).getViewport({ scale: 1 });
    aspect = first.width / first.height;
    slides = slides.filter((s) => s.after <= pageCount);
    buildSequence();
    await show(0);
  } catch (error) {
    console.error(error);
    const msg = stage.querySelector('#loading-msg');
    if (msg) msg.innerHTML = '<strong>The slides could not be loaded.</strong>' +
      (navigator.onLine ? 'Reload the page to try again.' : 'You are offline and this talk was not saved for offline use.');
  }
  checkOffline();
}

start();
