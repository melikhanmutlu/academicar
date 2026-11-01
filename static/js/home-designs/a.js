// Homepage design A · Kesit — a pinned, scroll-driven dissection that really cuts the model,
// a tool picker and the click-to-play showreel.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const desktop = window.matchMedia('(min-width: 1024px)');
  const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

  /* ---------------- Dissection: pinned while the three steps play; the plane really cuts ----------------
     The section is tall and its inner frame sticks, so scrolling moves through
     "turn", "cut" and "AR" before the page continues. The cut is a three.js clipping plane on the
     model's own materials (reached through model-viewer's private scene symbol, as the viewer's
     Section tool does) and the orange outline is that plane projected to the screen. */
  const dissect = document.querySelector('[data-a-dissect]');
  if (dissect) {
    const model = dissect.querySelector('[data-a-model]');
    const steps = [...dissect.querySelectorAll('[data-callout]')];
    const list = dissect.querySelector('.a-callouts');
    const cutSvg = dissect.querySelector('[data-a-cut]');
    const qr = dissect.querySelector('.a-specimen-qr');
    const stage = dissect.querySelector('.a-specimen');
    let lastQr = -1;
    const cutPoly = cutSvg && cutSvg.querySelector('polygon');
    const pin = dissect.querySelector('.a-dissect-pin');
    const pinned = () => getComputedStyle(pin).position === 'sticky';
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
    let progress = 0;
    let lastOn = -1;
    let lastOrbit = '';

    // ---- three.js access (null when model-viewer's internals differ: the page then just turns the model)
    const plane = { normal: null, constant: 0 };
    let ctx = null;
    let cutOn = false;
    const sceneOf = () => {
      const key = Object.getOwnPropertySymbols(model).find((s) => s.description === 'scene');
      const scene = key && model[key];
      return scene && typeof scene.traverse === 'function' ? scene : null;
    };
    const rendererOf = () => {
      let proto = model;
      let key = null;
      while (proto && !key) {
        key = Object.getOwnPropertySymbols(proto).find((s) => s.description === 'renderer') || null;
        proto = Object.getPrototypeOf(proto);
      }
      return key ? model[key] : null;
    };
    const prepare = () => {
      try {
        const scene = sceneOf();
        const three = rendererOf()?.threeRenderer;
        const root = scene && ((scene.models && scene.models[0]) || scene.target);
        if (!scene || !three || !root || !('localClippingEnabled' in three)) return null;
        const meshes = [];
        let Vec3 = null;
        let Mat4 = null;
        root.traverse((o) => {
          if (!o.isMesh || !o.material || !o.geometry) return;
          meshes.push(o);
          Vec3 = Vec3 || o.position.constructor;
          Mat4 = Mat4 || o.matrixWorld.constructor;
        });
        if (!meshes.length) return null;
        root.updateMatrixWorld(true);
        const toRoot = new Mat4().copy(root.matrixWorld).invert();
        const c = new Vec3();
        const min = [Infinity, Infinity, Infinity];
        const max = [-Infinity, -Infinity, -Infinity];
        meshes.forEach((mesh) => {
          if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
          const b = mesh.geometry.boundingBox;
          const m = new Mat4().multiplyMatrices(toRoot, mesh.matrixWorld);
          for (let k = 0; k < 8; k += 1) {
            c.set(k & 1 ? b.max.x : b.min.x, k & 2 ? b.max.y : b.min.y, k & 4 ? b.max.z : b.min.z).applyMatrix4(m);
            [c.x, c.y, c.z].forEach((v, i) => { min[i] = Math.min(min[i], v); max[i] = Math.max(max[i], v); });
          }
        });
        if (!min.every(Number.isFinite)) return null;
        // Cut along the axis named by data-cut-axis (x, y or z); otherwise the shortest one.
        const size = max.map((v, i) => v - min[i]);
        const named = { x: 0, y: 1, z: 2 }[dissect.dataset.cutAxis];
        const axis = named !== undefined ? named : size.indexOf(Math.min(...size));
        plane.normal = new Vec3();
        return {
          scene, three, root, meshes, Vec3, min, max, size, axis,
          centre: min.map((v, i) => (v + max[i]) / 2),
          p: new Vec3(), n: new Vec3(), corner: new Vec3(),
          sides: new Map(),
        };
      } catch (e) { return null; }
    };
    const eachMaterial = (fn) => ctx.meshes.forEach((mesh) => {
      (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((m) => m && fn(m));
    });
    const setCut = (on) => {
      if (!ctx || on === cutOn) return;
      cutOn = on;
      if (on) ctx.three.localClippingEnabled = true;
      eachMaterial((m) => {
        if (on) {
          if (!ctx.sides.has(m)) ctx.sides.set(m, m.side);
          m.clippingPlanes = [plane];
          m.side = 2; // DoubleSide: the inside faces show through the cut
        } else {
          m.clippingPlanes = null;
          if (ctx.sides.has(m)) m.side = ctx.sides.get(m);
        }
        m.needsUpdate = true;
      });
    };
    // offset: 0 = through the centre, 1 = just outside the top (nothing removed).
    const placePlane = (offset) => {
      const { root, centre, size, axis, p, n } = ctx;
      root.updateMatrixWorld();
      p.set(centre[0], centre[1], centre[2]);
      p.setComponent(axis, centre[axis] + offset * (size[axis] / 2 + 0.02 * size[axis]));
      p.applyMatrix4(root.matrixWorld);
      n.set(axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0).transformDirection(root.matrixWorld).negate();
      plane.normal.copy(n);
      plane.constant = -n.dot(p);
    };
    // The plane's outline, a little larger than the model, projected onto the stage.
    const drawOutline = (offset, alpha) => {
      if (!cutPoly) return;
      const camera = ctx.scene.camera || (ctx.scene.getCamera && ctx.scene.getCamera());
      if (!camera || alpha <= 0.01) { cutSvg.style.opacity = '0'; return; }
      const { root, centre, size, axis, min, max, corner } = ctx;
      const [u, v] = [0, 1, 2].filter((i) => i !== axis);
      const w = model.clientWidth;
      const h = model.clientHeight;
      cutSvg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      const pad = 0.08;
      const pts = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([a, b]) => {
        corner.set(0, 0, 0);
        corner.setComponent(axis, centre[axis] + offset * (size[axis] / 2 + 0.02 * size[axis]));
        corner.setComponent(u, (a ? max[u] : min[u]) + (a ? 1 : -1) * size[u] * pad);
        corner.setComponent(v, (b ? max[v] : min[v]) + (b ? 1 : -1) * size[v] * pad);
        corner.applyMatrix4(root.matrixWorld).project(camera);
        return `${((corner.x + 1) / 2 * w).toFixed(1)},${((1 - corner.y) / 2 * h).toFixed(1)}`;
      });
      cutPoly.setAttribute('points', pts.join(' '));
      cutSvg.style.opacity = alpha.toFixed(3);
    };

    // ---- scroll → progress, current step, camera, cut
    const readProgress = () => {
      if (pinned()) {
        const r = dissect.getBoundingClientRect();
        return clamp(-r.top / Math.max(1, r.height - window.innerHeight));
      }
      const r = list.getBoundingClientRect();
      const mid = window.innerHeight * 0.55;
      return clamp((mid - r.top) / Math.max(1, r.height));
    };
    const cutAmount = (p) => {
      // Step 2 (cut): the plane comes down to the centre; step 3 (AR): it lifts away again.
      if (p < 0.36) return 0;
      if (p < 0.6) return ease(clamp((p - 0.36) / 0.2));
      if (p < 0.72) return 1;
      return 1 - ease(clamp((p - 0.72) / 0.14));
    };
    const frame = () => {
      const p = progress;
      dissect.style.setProperty('--a-progress', p.toFixed(4));
      const on = Math.min(steps.length - 1, Math.floor(p * steps.length));
      if (on !== lastOn) {
        steps.forEach((s, i) => s.classList.toggle('is-current', i === on));
        lastOn = on;
      }
      if (reduceMotion || !model) return;

      // Step 3: the QR leaves its corner for the centre of the stage and grows;
      // scrolling back up returns it.
      const q = ease(clamp((p - 0.74) / 0.12));
      if (qr && stage && Math.abs(q - lastQr) > 0.0005) {
        lastQr = q;
        const sw = stage.clientWidth;
        const sh = stage.clientHeight;
        const size = qr.offsetWidth;
        const inset = qr.offsetTop;
        const dx = -(sw / 2 - inset - size / 2);
        const dy = sh / 2 - inset - size / 2;
        const grow = Math.min(2.4, (Math.min(sw, sh) * 0.55) / size);
        qr.style.transform = `translate(${(dx * q).toFixed(1)}px, ${(dy * q).toFixed(1)}px) scale(${(1 + (grow - 1) * q).toFixed(3)})`;
        stage.style.setProperty('--a-qr', q.toFixed(3));
      }

      // Camera: turns only during step 1, tilting down to face the cut; from then on
      // it holds still so steps 2 and 3 are only the section plane moving.
      const k = cutAmount(p);
      const t = ease(clamp(p / 0.3));
      const theta = 20 + 200 * t;
      const phi = 72 - 24 * t;
      const radius = (desktop.matches ? 7 : 11);
      const orbit = `${theta.toFixed(1)}deg ${phi.toFixed(1)}deg ${radius.toFixed(2)}m`;
      if (orbit !== lastOrbit) { model.cameraOrbit = orbit; lastOrbit = orbit; }

      if (!ctx && model.loaded) ctx = prepare();
      if (!ctx) return;
      const offset = 1 - k; // 1 = above the model, 0 = through its centre
      setCut(k > 0.001);
      if (cutOn) placePlane(offset);
      drawOutline(offset, Math.min(1, k * 3));
      try { ctx.scene.queueRender(); } catch (e) { /* next frame redraws */ }
    };

    // Run every frame while the section is on screen, so the outline follows the
    // camera's own easing; scrolling only moves the target.
    let running = false;
    let inView = false;
    const loop = () => {
      progress = readProgress();
      frame();
      if (inView) requestAnimationFrame(loop); else running = false;
    };
    const start = () => { if (!running) { running = true; requestAnimationFrame(loop); } };
    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        inView = entries[0].isIntersecting;
        if (inView) start();
      }).observe(dissect);
    } else {
      inView = true; start();
    }
    if (model) model.addEventListener('load', () => { ctx = null; cutOn = false; start(); });

    // Clicking a step jumps to its part of the pinned scroll.
    steps.forEach((s, i) => {
      s.addEventListener('click', () => {
        if (!pinned()) return;
        const top = window.scrollY + dissect.getBoundingClientRect().top;
        const span = dissect.offsetHeight - window.innerHeight;
        window.scrollTo({ top: top + span * ((i + 0.5) / steps.length), behavior: reduceMotion ? 'auto' : 'smooth' });
      });
    });
  }

  /* ---------------- Tools: pick a tool, play its recording while in view ---------------- */
  const tools = document.querySelector('[data-a-tools]');
  if (tools) {
    const buttons = [...tools.querySelectorAll('.a-step')];
    const videos = [...tools.querySelectorAll('.a-screen-video')];
    let active = 0;
    let visible = false;
    const show = (i) => {
      active = i;
      buttons.forEach((b, j) => { b.classList.toggle('is-active', j === i); b.setAttribute('aria-pressed', j === i ? 'true' : 'false'); });
      videos.forEach((v, j) => {
        v.classList.toggle('is-active', j === i);
        if (j === i && visible && !reduceMotion) v.play().catch(() => {}); else v.pause();
      });
    };
    buttons.forEach((b, i) => b.addEventListener('click', () => show(i)));
    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; show(active); }, { threshold: 0.35 })
        .observe(tools.querySelector('.a-screen'));
    }
  }

  /* ---------------- Showreel: load the MP4 only when asked ---------------- */
  const reel = document.querySelector('[data-a-reel]');
  if (reel) {
    const video = reel.querySelector('video');
    const poster = reel.querySelector('.a-reel-poster');
    reel.querySelector('.a-reel-play').addEventListener('click', (event) => {
      video.poster = poster.currentSrc || poster.src;
      video.hidden = false;
      video.controls = true;
      reel.classList.add('is-playing');
      event.currentTarget.remove();
      video.play().catch(() => {});
      video.focus();
    });
  }
})();
