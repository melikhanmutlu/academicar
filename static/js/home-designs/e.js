// Design E · Baskı: print → scan → live model. Preview only.
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  const lerp = (a, b, t) => a + (b - a) * t;

  const hero = document.querySelector('[data-e-hero]');
  const stage = hero && hero.querySelector('[data-e-stage]');
  if (stage) {
    const phone = stage.querySelector('[data-e-phone]');
    const qr = stage.querySelector('[data-e-qr]');
    const figure = stage.querySelector('[data-e-figure]');
    const print = stage.querySelector('[data-e-print]');
    const rise = stage.querySelector('[data-e-rise]');
    const steps = [...hero.querySelectorAll('[data-e-step]')];
    const pinQuery = window.matchMedia('(min-width: 1024px)');
    let geo = null;
    let progress = reduce ? 1 : 0;
    let mx = 0, my = 0;

    // Positions in % of the stage, measured from the laid-out sheet.
    const measure = () => {
      const s = stage.getBoundingClientRect();
      if (!s.width) return;
      const q = qr.getBoundingClientRect();
      const f = figure.getBoundingClientRect();
      const pw = phone.offsetWidth / s.width * 100;
      const ph = phone.offsetHeight / s.height * 100;
      const qx = (q.left + q.width / 2 - s.left) / s.width * 100;
      const qy = (q.top + q.height / 2 - s.top) / s.height * 100;
      geo = {
        start: { x: 70, y: 44, r: 9 },
        scan: { x: qx - pw / 2, y: qy - ph * 0.5, r: -3 },
        rest: { x: 72, y: 30, r: 6 },
        pw, ph,
      };
      rise.style.left = `${(f.left - s.left) / s.width * 100}%`;
      rise.style.top = `${(f.top - s.top) / s.height * 100}%`;
      rise.style.width = `${f.width / s.width * 100}%`;
      rise.style.height = `${f.height / s.height * 100}%`;
    };

    const render = () => {
      if (!geo) measure();
      if (!geo) return;
      const p = progress;
      const a = ease(clamp(p / 0.36));          // phone travels to the QR
      const sc = clamp((p - 0.38) / 0.2);        // scan line passes
      const r = ease(clamp((p - 0.6) / 0.34));   // model rises
      let pos;
      if (r > 0) {
        pos = { x: lerp(geo.scan.x, geo.rest.x, r), y: lerp(geo.scan.y, geo.rest.y, r), r: lerp(geo.scan.r, geo.rest.r, r) };
      } else {
        pos = { x: lerp(geo.start.x, geo.scan.x, a), y: lerp(geo.start.y, geo.scan.y, a), r: lerp(geo.start.r, geo.scan.r, a) };
      }
      const lift = 1 + Math.sin(a * Math.PI) * 0.04; // phone lifts slightly while moving
      phone.style.left = `${pos.x}%`;
      phone.style.top = `${pos.y}%`;
      phone.style.transform = `translate(${mx * 10}px, ${my * 10}px) rotate(${pos.r}deg) scale(${lift})`;
      stage.style.setProperty('--mx', mx.toFixed(3));
      stage.style.setProperty('--my', my.toFixed(3));
      stage.style.setProperty('--e-scan', (sc > 0 && sc < 1 ? sc : (sc >= 1 ? 1 : 0)).toFixed(3));
      phone.style.setProperty('--e-ar', sc >= 1 ? '1' : '0');
      rise.style.setProperty('--e-rise', r.toFixed(3));
      print.style.opacity = (1 - r * 0.88).toFixed(3);
      rise.classList.toggle('is-live', r > 0.9);
      const active = p < 0.2 ? 1 : (sc < 1 ? 2 : 3);
      steps.forEach((li, i) => li.classList.toggle('is-on', i < active));
    };

    let pinned = false;
    const setMode = () => {
      pinned = !reduce && pinQuery.matches;
      hero.classList.toggle('is-pinned', pinned);
      geo = null;
      requestAnimationFrame(() => { measure(); onScroll(); });
    };

    let ticking = false;
    const onScroll = () => {
      if (pinned) {
        const rect = hero.getBoundingClientRect();
        const travel = hero.offsetHeight - (window.innerHeight - 64);
        progress = clamp((64 - rect.top) / (travel * 0.92));
      }
      render();
      ticking = false;
    };
    window.addEventListener('scroll', () => {
      if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
    }, { passive: true });
    window.addEventListener('resize', () => { geo = null; requestAnimationFrame(onScroll); });
    pinQuery.addEventListener('change', setMode);

    // Smaller screens: play the sequence once when the desk comes into view.
    if (!reduce && 'IntersectionObserver' in window) {
      const io = new IntersectionObserver((entries) => {
        if (pinned || !entries[0].isIntersecting) return;
        io.disconnect();
        const t0 = performance.now();
        const dur = 3600;
        const step = (now) => {
          if (pinned) return;
          progress = clamp((now - t0) / dur);
          render();
          if (progress < 1) requestAnimationFrame(step);
        };
        setTimeout(() => requestAnimationFrame(step), 400);
      }, { threshold: 0.6 });
      io.observe(stage);
    }

    // Gentle pointer parallax on the desk.
    if (!reduce && window.matchMedia('(pointer: fine)').matches) {
      const desk = hero.querySelector('[data-e-desk]');
      desk.addEventListener('pointermove', (e) => {
        const b = desk.getBoundingClientRect();
        mx = (e.clientX - b.left) / b.width * 2 - 1;
        my = (e.clientY - b.top) / b.height * 2 - 1;
        render();
      });
      desk.addEventListener('pointerleave', () => { mx = 0; my = 0; render(); });
    }

    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { geo = null; render(); });
    setMode();
  }

  // Feature clips: play only while visible.
  const clips = [...document.querySelectorAll('.e-clip')];
  if (clips.length && 'IntersectionObserver' in window && !reduce) {
    const vio = new IntersectionObserver((entries) => {
      entries.forEach(({ target, isIntersecting }) => {
        if (isIntersecting) {
          if (target.preload === 'none') target.preload = 'auto';
          target.play().catch(() => {});
        } else {
          target.pause();
        }
      });
    }, { threshold: 0.55 });
    clips.forEach((v) => vio.observe(v));
  }

  // Audience tabs.
  const tabs = [...document.querySelectorAll('.e-aud-tabs [role="tab"]')];
  const select = (tab, focus) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', (e) => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length], true); }
    });
  });

  // Showreel: load the video only on play.
  const reel = document.querySelector('[data-e-showreel]');
  if (reel) {
    const video = reel.querySelector('video');
    const poster = reel.querySelector('.e-screen-poster');
    reel.querySelector('.e-screen-play').addEventListener('click', (event) => {
      video.poster = poster.currentSrc || poster.src;
      video.hidden = false;
      video.controls = true;
      poster.hidden = true;
      event.currentTarget.remove();
      video.play().catch(() => {});
      video.focus();
    });
  }
})();
