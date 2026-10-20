/* Homepage design D · Hat: pipeline instrument, live camera readout, tools monitor. */
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const inputsData = window.D_INPUTS || [];

  /* ---------------- Pipeline ---------------- */
  const panel = document.querySelector('[data-pipeline]');
  if (panel && inputsData.length) {
    const body = panel.querySelector('.d-panel-body');
    const svg = panel.querySelector('[data-wires]');
    const stepsEl = panel.querySelector('[data-steps]');
    const inputEls = [...panel.querySelectorAll('.d-input')];
    const inputList = panel.querySelector('.d-inputs-list');
    const outputs = [...panel.querySelectorAll('[data-output]')];
    const toggle = panel.querySelector('[data-cycle-toggle]');
    const status = panel.querySelector('[data-status]');
    const readout = {
      note: panel.querySelector('[data-r="note"]'),
      layers: panel.querySelector('[data-r="layers"]'),
      medical: panel.querySelector('[data-r="medical"]'),
      count: panel.querySelector('[data-r="count"]'),
    };

    let current = 0;
    let cycling = !reduceMotion;
    let inView = true;
    let cycleTimer = null;
    let mainPath = null;
    let branchPaths = [];
    let nodeLengths = [];
    let mainLength = 0;
    let packets = [];
    let lastSpawn = 0;
    let rafId = null;
    const MAIN_MS = 2600;
    const BRANCH_MS = 650;
    const SPAWN_MS = 1100;

    const isVertical = () => window.matchMedia('(max-width: 900px)').matches;

    const setStatus = () => {
      const label = inputsData[current].label;
      status.textContent = cycling ? `Showing each input in turn: ${label}` : `Showing ${label}`;
      toggle.textContent = cycling ? 'Pause' : 'Cycle inputs';
      toggle.setAttribute('aria-pressed', cycling ? 'true' : 'false');
      if (reduceMotion) toggle.hidden = true;
    };

    const renderSteps = (data) => {
      stepsEl.innerHTML = '';
      data.steps.forEach(([title, text]) => {
        const li = document.createElement('li');
        li.className = 'd-step' + (reduceMotion ? ' is-done' : '');
        const node = document.createElement('span');
        node.className = 'd-node';
        node.setAttribute('aria-hidden', 'true');
        const strong = document.createElement('strong');
        strong.textContent = title;
        const span = document.createElement('span');
        span.textContent = text;
        li.append(node, strong, span);
        stepsEl.append(li);
      });
    };

    const centerOf = (el, base) => {
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2 - base.left, y: r.top + r.height / 2 - base.top };
    };

    const curve = (a, b, vertical) => {
      if (vertical) {
        const dy = Math.max(18, Math.abs(b.y - a.y) * 0.45);
        return `C ${a.x} ${a.y + dy} ${b.x} ${b.y - dy} ${b.x} ${b.y}`;
      }
      const dx = Math.max(18, Math.abs(b.x - a.x) * 0.45);
      return `C ${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`;
    };

    const drawWires = () => {
      const base = body.getBoundingClientRect();
      const vertical = isVertical();
      svg.setAttribute('viewBox', `0 0 ${base.width} ${base.height}`);
      svg.innerHTML = '';

      const activeInput = inputEls[current];
      const port = activeInput.querySelector('.d-port');
      let start = centerOf(port, base);
      if (vertical) {
        // The chip row scrolls sideways; keep the wire inside the visible row.
        const listBox = inputList.getBoundingClientRect();
        start.x = Math.min(Math.max(start.x, listBox.left - base.left + 12), listBox.right - base.left - 12);
      }
      const nodes = [...stepsEl.querySelectorAll('.d-node')].map((n) => centerOf(n, base));
      if (!nodes.length) return;

      // The hub sits past the last step, so branch wires never cross step text.
      const last = nodes[nodes.length - 1];
      const stageBox = stepsEl.parentElement.getBoundingClientRect();
      const hub = vertical
        ? { x: last.x, y: outputs[0].parentElement.getBoundingClientRect().top - base.top - 16 }
        : { x: stageBox.right - base.left + 20, y: last.y };
      let d = `M ${start.x} ${start.y} `;
      if (vertical) {
        // Drop onto the step rail above the "Processing" label, then run down it.
        const labelTop = stepsEl.parentElement.querySelector('.d-col-label').getBoundingClientRect().top - base.top;
        const railTop = { x: nodes[0].x, y: labelTop - 6 };
        d += curve(start, railTop, true) + ` L ${nodes[0].x} ${nodes[0].y}`;
      } else {
        // Join the rail in the gap before the first step, clear of its label.
        const leadIn = { x: nodes[0].x - 26, y: nodes[0].y };
        d += curve(start, leadIn, false) + ` L ${nodes[0].x} ${nodes[0].y}`;
      }
      for (let i = 1; i < nodes.length; i += 1) d += ` L ${nodes[i].x} ${nodes[i].y}`;
      d += ` L ${hub.x} ${hub.y}`;
      mainPath = document.createElementNS(SVG_NS, 'path');
      mainPath.setAttribute('d', d);
      mainPath.setAttribute('class', 'd-wire d-wire-main');
      svg.append(mainPath);
      mainLength = mainPath.getTotalLength();

      // Length along the path of each node (closest sampled point).
      const samples = [];
      for (let s = 0; s <= mainLength; s += 2) samples.push([s, mainPath.getPointAtLength(s)]);
      nodeLengths = nodes.map((n) => {
        let best = 0; let bestDist = Infinity;
        samples.forEach(([s, p]) => {
          const dist = (p.x - n.x) ** 2 + (p.y - n.y) ** 2;
          if (dist < bestDist) { bestDist = dist; best = s; }
        });
        return best;
      });

      branchPaths = outputs.map((out) => {
        const p = centerOf(out.querySelector('.d-port-in'), base);
        const path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', `M ${hub.x} ${hub.y} ` + curve(hub, p, vertical));
        path.setAttribute('class', 'd-wire d-wire-branch');
        svg.append(path);
        return { path, len: path.getTotalLength(), out };
      });
    };

    const ensureCircle = (packet, key) => {
      if (!packet[key]) {
        const halo = document.createElementNS(SVG_NS, 'circle');
        halo.setAttribute('r', '8');
        halo.setAttribute('class', 'd-packet-halo');
        const dot = document.createElementNS(SVG_NS, 'circle');
        dot.setAttribute('r', '3.5');
        dot.setAttribute('class', 'd-packet');
        svg.append(halo, dot);
        packet[key] = [halo, dot];
      }
      return packet[key];
    };

    const place = (pair, pt) => {
      pair.forEach((c) => { c.setAttribute('cx', pt.x.toFixed(1)); c.setAttribute('cy', pt.y.toFixed(1)); });
    };

    const removePacket = (packet) => {
      Object.keys(packet).forEach((k) => {
        if (Array.isArray(packet[k])) packet[k].forEach((c) => c.remove());
      });
    };

    const tick = (now) => {
      rafId = null;
      if (!inView || document.hidden || !mainPath) return;
      if (now - lastSpawn > SPAWN_MS) { packets.push({ t0: now }); lastSpawn = now; }

      const steps = [...stepsEl.children];
      const lit = new Array(steps.length).fill(false);
      const hit = new Array(outputs.length).fill(false);

      packets = packets.filter((packet) => {
        const age = now - packet.t0;
        if (age < MAIN_MS) {
          const s = (age / MAIN_MS) * mainLength;
          place(ensureCircle(packet, 'main'), mainPath.getPointAtLength(s));
          nodeLengths.forEach((nl, i) => {
            if (s >= nl - 2 && s <= nl + 70) lit[i] = true;
            if (s >= nl) steps[i] && steps[i].classList.add('is-done');
          });
          return true;
        }
        if (packet.main) { packet.main.forEach((c) => c.remove()); packet.main = null; }
        if (age < MAIN_MS + BRANCH_MS) {
          const t = (age - MAIN_MS) / BRANCH_MS;
          branchPaths.forEach((b, i) => {
            place(ensureCircle(packet, `b${i}`), b.path.getPointAtLength(t * b.len));
            if (t > 0.75) hit[i] = true;
          });
          return true;
        }
        removePacket(packet);
        return false;
      });

      steps.forEach((el, i) => el.classList.toggle('is-lit', lit[i]));
      outputs.forEach((el, i) => el.classList.toggle('is-hit', hit[i]));
      rafId = requestAnimationFrame(tick);
    };

    const startLoop = () => {
      if (reduceMotion || rafId) return;
      rafId = requestAnimationFrame(tick);
    };

    const resetPackets = () => {
      packets.forEach(removePacket);
      packets = [];
      lastSpawn = 0;
    };

    const scheduleCycle = () => {
      clearTimeout(cycleTimer);
      if (!cycling || reduceMotion) return;
      cycleTimer = setTimeout(() => {
        if (inView && !document.hidden) select((current + 1) % inputsData.length, false);
        else scheduleCycle();
      }, 6500);
    };

    const select = (index, fromUser) => {
      current = index;
      const data = inputsData[index];
      inputEls.forEach((el, i) => {
        el.classList.toggle('is-active', i === index);
        el.querySelector('input').checked = i === index;
      });
      // Keep the active chip visible in the sideways row without moving the page.
      const chip = inputEls[index];
      if (inputList.scrollWidth > inputList.clientWidth) {
        const target = chip.offsetLeft - (inputList.clientWidth - chip.offsetWidth) / 2;
        inputList.scrollTo({ left: target, behavior: reduceMotion ? 'auto' : 'smooth' });
      }
      readout.note.textContent = data.note;
      readout.layers.textContent = data.layers;
      readout.medical.textContent = data.medical;
      readout.count.textContent = String(data.steps.length);
      if (fromUser) cycling = false;
      setStatus();

      const swap = () => {
        renderSteps(data);
        resetPackets();
        outputs.forEach((o) => o.classList.remove('is-hit'));
        drawWires();
        stepsEl.classList.remove('is-swapping');
        scheduleCycle();
      };
      if (reduceMotion) { swap(); return; }
      stepsEl.classList.add('is-swapping');
      setTimeout(swap, 220);
    };

    inputEls.forEach((el, i) => {
      el.querySelector('input').addEventListener('change', () => select(i, true));
    });
    toggle.addEventListener('click', () => {
      cycling = !cycling;
      setStatus();
      scheduleCycle();
    });

    let resizeRaf = null;
    const onResize = () => {
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(() => { resizeRaf = null; resetPackets(); drawWires(); });
    };
    window.addEventListener('resize', onResize);
    inputList.addEventListener('scroll', onResize, { passive: true });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(onResize);

    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        inView = entries[0].isIntersecting;
        if (inView) startLoop();
      }, { threshold: 0.1 }).observe(panel);
    }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) startLoop(); });

    inputEls[0].classList.add('is-active');
    if (reduceMotion) stepsEl.querySelectorAll('.d-step').forEach((s) => s.classList.add('is-done'));
    setStatus();
    drawWires();
    startLoop();
    scheduleCycle();
  }

  /* ---------------- Live camera readout ---------------- */
  const mv = document.querySelector('[data-live-model]');
  if (mv) {
    const theta = document.querySelector('[data-cam="theta"]');
    const phi = document.querySelector('[data-cam="phi"]');
    const stat = document.querySelector('[data-cam="status"]');
    const deg = (rad) => {
      let v = (rad * 180) / Math.PI;
      v = ((v % 360) + 360) % 360;
      return `${v.toFixed(1)}°`;
    };
    let pending = false;
    mv.addEventListener('camera-change', () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        const orbit = mv.getCameraOrbit && mv.getCameraOrbit();
        if (!orbit) return;
        if (theta) theta.textContent = deg(orbit.theta);
        if (phi) phi.textContent = deg(orbit.phi);
      });
    });
    mv.addEventListener('load', () => { if (stat) stat.textContent = 'Live'; });
    mv.addEventListener('error', () => { if (stat) stat.textContent = 'Could not load'; });
    if (reduceMotion) mv.removeAttribute('auto-rotate');
  }

  /* ---------------- Tools monitor ---------------- */
  const tools = document.querySelector('[data-tools]');
  if (tools) {
    const video = tools.querySelector('[data-monitor]');
    const reel = tools.querySelector('[data-reel]');
    const reelBtn = tools.querySelector('[data-reel-play]');
    const label = tools.querySelector('[data-monitor-label]');
    const rows = [...tools.querySelectorAll('.d-spec-row[data-clip]')];
    const clipBase = window.D_CLIP_BASE;
    const posterBase = window.D_POSTER_BASE;
    let monitorInView = false;

    const playClip = () => {
      if (reduceMotion || !monitorInView || !reel.hidden) return;
      const p = video.play();
      if (p && p.catch) p.catch(() => {});
    };

    const showClip = (row) => {
      rows.forEach((r) => {
        const on = r === row;
        r.classList.toggle('is-active', on);
        r.querySelector('.d-spec-btn').setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (!reel.hidden) { reel.pause(); reel.hidden = true; video.hidden = false; }
      const clip = row.dataset.clip;
      label.textContent = row.dataset.label;
      video.poster = `${posterBase}${clip}.webp`;
      video.src = `${clipBase}${clip}.mp4`;
      video.load();
      playClip();
    };

    rows.forEach((row) => row.querySelector('.d-spec-btn').addEventListener('click', () => showClip(row)));

    reelBtn.addEventListener('click', () => {
      video.pause();
      video.hidden = true;
      reel.hidden = false;
      reel.controls = true;
      label.textContent = 'Showreel, every viewer tool';
      const p = reel.play();
      if (p && p.catch) p.catch(() => {});
      reel.focus();
    });

    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        monitorInView = entries[0].isIntersecting;
        if (monitorInView) playClip(); else video.pause();
      }, { threshold: 0.35 }).observe(video.parentElement);
    }
  }

  /* ---------------- Grid parallax ---------------- */
  const grid = document.querySelector('.d-grid-bg');
  if (grid && !reduceMotion) {
    let ticking = false;
    const update = () => {
      grid.style.setProperty('--d-grid-y', `${(window.scrollY * 0.25).toFixed(1)}px`);
      ticking = false;
    };
    window.addEventListener('scroll', () => {
      if (!ticking) { requestAnimationFrame(update); ticking = true; }
    }, { passive: true });
  }
})();
