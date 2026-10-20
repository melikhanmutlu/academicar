// Design C · Sahne: stage word + rail, stage parallax, four-step flow, swipeable feature reel,
// in-view clip playback, monitor tilt, click-to-play showreel.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Stage: kinetic word in sync with the tool rail ---------- */
  const stage = document.querySelector('[data-stage]');
  if (stage) {
    const word = stage.querySelector('[data-stage-word]');
    const label = stage.querySelector('[data-stage-label]');
    const note = stage.querySelector('[data-stage-note]');
    const buttons = [...stage.querySelectorAll('.c-rail-btn')];
    let index = 0;
    let timer = null;

    const show = (next, animate) => {
      index = (next + buttons.length) % buttons.length;
      const btn = buttons[index];
      buttons.forEach((b) => {
        const on = b === btn;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      const swap = () => {
        word.textContent = btn.dataset.label;
        label.textContent = btn.dataset.label;
        note.textContent = btn.dataset.note;
      };
      if (!animate || reduceMotion) { swap(); return; }
      word.classList.remove('is-in');
      word.classList.add('is-out');
      window.setTimeout(() => {
        swap();
        word.classList.remove('is-out');
        void word.offsetWidth;
        word.classList.add('is-in');
      }, 380);
    };

    const start = () => {
      if (reduceMotion || timer) return;
      timer = window.setInterval(() => show(index + 1, true), 2800);
    };
    const stop = () => { window.clearInterval(timer); timer = null; };

    buttons.forEach((btn, i) => {
      btn.addEventListener('click', () => { stop(); show(i, true); });
    });
    // Only cycle while the stage is on screen.
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(([entry]) => (entry.isIntersecting ? start() : stop()), { threshold: 0.2 }).observe(stage);
    } else {
      start();
    }

    // Subtle pointer parallax on the stage layers.
    if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
      let raf = 0;
      stage.addEventListener('pointermove', (event) => {
        const rect = stage.getBoundingClientRect();
        const mx = ((event.clientX - rect.left) / rect.width - 0.5) * 2;
        const my = ((event.clientY - rect.top) / rect.height - 0.5) * 2;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          stage.style.setProperty('--mx', mx.toFixed(3));
          stage.style.setProperty('--my', my.toFixed(3));
        });
      });
      stage.addEventListener('pointerleave', () => {
        stage.style.setProperty('--mx', 0);
        stage.style.setProperty('--my', 0);
      });
    }
  }

  /* ---------- Feature reel: arrow buttons page the swipe row ---------- */
  const reel = document.querySelector('[data-reel]');
  if (reel) {
    const win = reel.querySelector('[data-reel-window]');
    const prev = reel.querySelector('[data-reel-prev]');
    const next = reel.querySelector('[data-reel-next]');
    const panel = win.querySelector('.c-reel-panel');
    const step = () => (panel ? panel.getBoundingClientRect().width + 24 : win.clientWidth * 0.8);
    const sync = () => {
      prev.disabled = win.scrollLeft < 4;
      next.disabled = win.scrollLeft > win.scrollWidth - win.clientWidth - 4;
    };
    prev.addEventListener('click', () => win.scrollBy({ left: -step(), behavior: reduceMotion ? 'auto' : 'smooth' }));
    next.addEventListener('click', () => win.scrollBy({ left: step(), behavior: reduceMotion ? 'auto' : 'smooth' }));
    win.addEventListener('scroll', sync, { passive: true });
    window.addEventListener('resize', sync);
    sync();
  }

  /* ---------- Clips play only while visible ---------- */
  const clips = [...document.querySelectorAll('video[data-autoplay]')];
  if (clips.length && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach(({ target, isIntersecting }) => {
        if (isIntersecting && !reduceMotion) {
          if (target.preload === 'none') target.preload = 'auto';
          target.play().catch(() => {});
        } else {
          target.pause();
        }
      });
    }, { threshold: 0.55 });
    clips.forEach((v) => io.observe(v));
  }

  /* ---------- Flow: the four steps light up in order once ---------- */
  const flow = document.querySelector('[data-flow]');
  if (flow && !reduceMotion && 'IntersectionObserver' in window) {
    flow.classList.add('is-armed');
    const fio = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { flow.classList.add('is-in'); fio.disconnect(); }
    }, { threshold: 0.3 });
    fio.observe(flow);
  }

  /* ---------- Monitor flattens as it scrolls into view ---------- */
  const frame = document.querySelector('[data-monitor-frame]');
  if (frame && !reduceMotion) {
    let ticking = false;
    const tilt = () => {
      ticking = false;
      const r = frame.getBoundingClientRect();
      const t = Math.min(1, Math.max(0, (r.top - window.innerHeight * 0.25) / (window.innerHeight * 0.6)));
      frame.style.setProperty('--tilt', t.toFixed(3));
    };
    tilt();
    window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(tilt); } }, { passive: true });
  }

  /* ---------- Showreel: swap the poster for the video only on play ---------- */
  const launch = document.querySelector('[data-launch-video]');
  if (launch) {
    const video = launch.querySelector('video');
    const poster = launch.querySelector('.c-showreel-poster');
    launch.querySelector('.c-showreel-play').addEventListener('click', (event) => {
      video.poster = poster.currentSrc || poster.src;
      video.hidden = false;
      video.controls = true;
      launch.classList.add('is-playing');
      event.currentTarget.remove();
      video.play().catch(() => {});
      video.focus();
    });
  }
})();
