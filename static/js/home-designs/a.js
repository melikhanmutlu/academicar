// Homepage design A · Kesit — live hero model, a short scroll-driven dissection,
// a tool picker, drifting format type and the click-to-play showreel.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const desktop = window.matchMedia('(min-width: 1024px)');
  const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

  /* ---------------- Hero: gentle turn of the live model, then the route draws in ---------------- */
  const heroModel = document.querySelector('[data-a-hero-model]');
  if (heroModel && !reduceMotion) heroModel.setAttribute('auto-rotate', '');
  const hero = document.querySelector('.a-hero');
  if (hero && !reduceMotion) {
    hero.classList.add('is-armed');
    requestAnimationFrame(() => requestAnimationFrame(() => hero.classList.add('is-in')));
  }

  /* ---------------- Dissection: sticky model, three steps drive camera + section plane ---------------- */
  const dissect = document.querySelector('[data-a-dissect]');
  if (dissect) {
    const model = dissect.querySelector('[data-a-model]');
    const steps = [...dissect.querySelectorAll('[data-callout]')];
    const list = dissect.querySelector('.a-callouts');
    let lastOrbit = '';
    let lastOn = -1;

    const update = () => {
      const r = list.getBoundingClientRect();
      const mid = window.innerHeight * 0.55;
      const p = clamp((mid - r.top) / Math.max(1, r.height));
      dissect.style.setProperty('--a-progress', p.toFixed(4));

      let on = 0;
      steps.forEach((s, i) => { if (s.getBoundingClientRect().top < mid) on = i; });
      if (on !== lastOn) {
        steps.forEach((s, i) => s.classList.toggle('is-current', i === on));
        lastOn = on;
      }
      if (reduceMotion) return;

      // The plane sweeps across while the "Section" step is current.
      const third = 1 / steps.length;
      const planeT = clamp((p - third) / third);
      dissect.style.setProperty('--a-plane-on', on === 1 ? 1 : 0);
      dissect.style.setProperty('--a-plane-x', (-70 + planeT * 140).toFixed(2));
      dissect.style.setProperty('--a-zoom', clamp((p - 0.66) / 0.3).toFixed(3));

      const theta = 20 + p * 300;
      const phi = 70 - 18 * Math.sin(p * Math.PI);
      const radius = (9.5 - 1.2 * Math.sin(p * Math.PI)) * (desktop.matches ? 1 : 1.25);
      const orbit = `${theta.toFixed(1)}deg ${phi.toFixed(1)}deg ${radius.toFixed(2)}m`;
      if (model && orbit !== lastOrbit) { model.cameraOrbit = orbit; lastOrbit = orbit; }
    };

    let ticking = false;
    window.addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { update(); ticking = false; });
    }, { passive: true });
    window.addEventListener('resize', update);
    update();
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
