// Homepage design A · Kesit — scroll-driven dissection, tool scrollytelling,
// drifting format type and the click-to-play showreel.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const desktop = window.matchMedia('(min-width: 1024px)');
  const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
  const SVG = 'http://www.w3.org/2000/svg';

  /* ---------------- Hero dissection ---------------- */
  const dissect = document.querySelector('[data-a-dissect]');
  if (dissect && !reduceMotion) {
    dissect.classList.add('is-live');
    const stage = dissect.querySelector('.a-stage');
    const model = dissect.querySelector('[data-a-model]');
    const specimen = dissect.querySelector('.a-specimen');
    const callouts = [...dissect.querySelectorAll('[data-callout]')];
    const leaders = dissect.querySelector('[data-a-leaders]');
    const thresholds = [0.2, 0.37, 0.54, 0.71];
    let lastOrbit = '';
    const fitModel = () => { if (model) model.fieldOfView = '30deg'; };
    fitModel();
    desktop.addEventListener('change', fitModel);
    let lastOn = -1;

    const lines = callouts.map(() => {
      const line = document.createElementNS(SVG, 'line');
      leaders.appendChild(line);
      return line;
    });

    // Leader lines run from each callout's inner corner to a point near the model.
    const layoutLeaders = () => {
      if (!desktop.matches) return;
      const s = stage.getBoundingClientRect();
      leaders.setAttribute('viewBox', `0 0 ${s.width} ${s.height}`);
      const m = specimen.getBoundingClientRect();
      const cx = m.left - s.left + m.width / 2;
      const cy = m.top - s.top + m.height / 2;
      callouts.forEach((c, i) => {
        const r = c.getBoundingClientRect();
        const right = i % 2 === 1;
        const bottom = i >= 2;
        const x1 = (right ? r.left : r.right) - s.left;
        const y1 = (bottom ? r.top + 24 : r.bottom - 24) - s.top;
        const x2 = cx + (right ? 1 : -1) * m.width * 0.13;
        const y2 = cy + (bottom ? 1 : -1) * m.height * 0.12;
        const line = lines[i];
        line.setAttribute('x1', x1); line.setAttribute('y1', y1);
        line.setAttribute('x2', x2); line.setAttribute('y2', y2);
        const len = Math.hypot(x2 - x1, y2 - y1);
        line.style.strokeDasharray = len;
        line.style.strokeDashoffset = line.classList.contains('is-on') ? 0 : len;
      });
    };

    const update = () => {
      const rect = dissect.getBoundingClientRect();
      const travel = dissect.offsetHeight - stage.offsetHeight;
      const pinTop = parseFloat(getComputedStyle(stage).top) || 0;
      const p = clamp((pinTop - rect.top) / Math.max(1, travel));
      const copyOut = clamp((p - 0.03) / 0.13);
      const big = desktop.matches;

      dissect.style.setProperty('--a-progress', p.toFixed(4));
      dissect.style.setProperty('--a-copy-out', copyOut.toFixed(4));
      dissect.style.setProperty('--a-shift', big ? (17 * (1 - copyOut)).toFixed(3) : 0);
      dissect.classList.toggle('is-past-intro', copyOut > 0.98);

      // Section plane sweeps across during the third callout.
      const planeT = clamp((p - 0.5) / 0.2);
      dissect.style.setProperty('--a-plane-on', p > 0.5 && p < 0.72 ? 1 : 0);
      dissect.style.setProperty('--a-plane-x', (-70 + planeT * 140).toFixed(2));
      dissect.style.setProperty('--a-zoom', clamp((p - 0.7) / 0.2).toFixed(3));

      let on = -1;
      thresholds.forEach((t, i) => { if (p >= t) on = i; });
      if (on !== lastOn) {
        callouts.forEach((c, i) => {
          c.classList.toggle('is-on', i <= on);
          c.classList.toggle('is-current', i === on);
          const line = lines[i];
          line.classList.toggle('is-on', i <= on);
          const len = parseFloat(line.style.strokeDasharray) || 0;
          line.style.strokeDashoffset = i <= on ? 0 : len;
        });
        lastOn = on;
      }

      if (big && p > 0.15) layoutLeaders();

      // Camera: one slow turn plus a tilt toward the cut.
      const theta = 20 + p * 330;
      const phi = 70 - 18 * Math.sin(p * Math.PI);
      const radius = (9.5 - 1.2 * Math.sin(p * Math.PI)) * (big ? 1 : 1.45);
      const orbit = `${theta.toFixed(1)}deg ${phi.toFixed(1)}deg ${radius.toFixed(2)}m`;
      if (model && orbit !== lastOrbit) {
        model.cameraOrbit = orbit;
        lastOrbit = orbit;
      }
    };

    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { update(); ticking = false; });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', () => { layoutLeaders(); update(); });
    update();
    // Callout boxes settle after fonts load; lay leaders out once they do.
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => { layoutLeaders(); update(); });
    setTimeout(layoutLeaders, 600);
  }

  /* ---------------- Clips: play only while visible ---------------- */
  const clipObserver = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const video = entry.target;
      if (entry.isIntersecting && !reduceMotion) {
        if (video.dataset.gated === 'off') return;
        video.play().catch(() => {});
      } else {
        video.pause();
      }
    });
  }, { threshold: 0.35 }) : null;

  document.querySelectorAll('[data-inline-clip]').forEach((v) => clipObserver && clipObserver.observe(v));

  /* ---------------- Tools scrollytelling ---------------- */
  const steps = [...document.querySelectorAll('.a-step')];
  const screens = [...document.querySelectorAll('.a-screen-video')];
  const screen = document.querySelector('.a-screen');
  const countEl = document.querySelector('[data-screen-count]');
  const nameEl = document.querySelector('[data-screen-name]');
  const stepList = document.querySelector('.a-steps');
  let active = 0;
  let screenVisible = false;

  const setActive = (i) => {
    active = i;
    steps.forEach((s, j) => s.classList.toggle('is-active', j === i));
    screens.forEach((v, j) => {
      const on = j === i;
      v.classList.toggle('is-active', on);
      if (on && screenVisible && !reduceMotion && desktop.matches) {
        v.play().catch(() => {});
      } else {
        v.pause();
      }
    });
    if (countEl) countEl.textContent = i + 1;
    if (nameEl) nameEl.textContent = steps[i].dataset.name;
    if (stepList) stepList.style.setProperty('--a-steps-progress', ((i + 1) / steps.length).toFixed(3));
  };

  if ('IntersectionObserver' in window && steps.length) {
    const stepObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) setActive(Number(entry.target.dataset.step));
      });
    }, { rootMargin: '-45% 0px -45% 0px' });
    steps.forEach((s) => stepObserver.observe(s));

    if (screen) {
      new IntersectionObserver((entries) => {
        screenVisible = entries[0].isIntersecting;
        setActive(active);
      }, { threshold: 0.2 }).observe(screen);
    }
  }
  if (steps.length) setActive(0);

  /* ---------------- Drifting format type (partial parallax) ---------------- */
  const formats = document.querySelector('[data-a-formats]');
  if (formats && !reduceMotion) {
    let ticking = false;
    const drift = () => {
      const r = formats.getBoundingClientRect();
      if (r.bottom > 0 && r.top < window.innerHeight) {
        const offset = window.innerHeight - r.top;
        formats.style.setProperty('--a-drift', (offset * -0.18).toFixed(1));
        formats.style.setProperty('--a-drift-alt', (offset * 0.14).toFixed(1));
        formats.querySelectorAll('[data-drift]').forEach((row, i) => {
          row.style.transform = i === 0
            ? `translateX(calc(-8% + ${(offset * -0.18).toFixed(1)}px))`
            : `translateX(calc(-30% + ${(offset * 0.14).toFixed(1)}px))`;
        });
      }
      ticking = false;
    };
    window.addEventListener('scroll', () => {
      if (!ticking) { ticking = true; requestAnimationFrame(drift); }
    }, { passive: true });
    drift();
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
