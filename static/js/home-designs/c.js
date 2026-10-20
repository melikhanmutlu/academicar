// Design C · Sahne: stage word + rail, stage parallax, four-step flow, pinned feature reel,
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

  /* ---------- Feature reel: vertical scroll drives the track ---------- */
  const reel = document.querySelector('[data-reel]');
  if (reel) {
    const track = reel.querySelector('[data-reel-track]');
    const panels = [...track.children];
    const dots = [...reel.querySelectorAll('[data-reel-dot]')];
    const wide = window.matchMedia('(min-width: 861px) and (min-height: 560px)');
    let ticking = false;

    const setCurrent = (i) => {
      panels.forEach((p, n) => p.classList.toggle('is-current', n === i));
      dots.forEach((d, n) => {
        d.classList.toggle('is-current', n === i);
        d.classList.toggle('is-done', n < i);
      });
    };

    const update = () => {
      ticking = false;
      if (reel.classList.contains('is-swipe')) return;
      const rect = reel.getBoundingClientRect();
      const travel = reel.offsetHeight - (window.innerHeight - 64);
      const progress = Math.min(1, Math.max(0, (64 - rect.top) / travel));
      const max = track.scrollWidth - window.innerWidth;
      track.style.transform = `translate3d(${(-progress * Math.max(0, max)).toFixed(1)}px, 0, 0)`;
      setCurrent(Math.min(panels.length - 1, Math.round(progress * (panels.length - 1))));
    };
    const request = () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } };

    const mode = () => {
      const swipe = reduceMotion || !wide.matches;
      reel.classList.toggle('is-swipe', swipe);
      if (swipe) { track.style.transform = ''; panels.forEach((p) => p.classList.add('is-current')); }
      else update();
    };
    mode();
    wide.addEventListener('change', mode);
    window.addEventListener('scroll', request, { passive: true });
    window.addEventListener('resize', request);

    // Clicking a chapter name jumps to that panel.
    dots.forEach((dot, i) => {
      dot.style.cursor = 'pointer';
      dot.addEventListener('click', () => {
        const travel = reel.offsetHeight - (window.innerHeight - 64);
        const top = reel.getBoundingClientRect().top + window.scrollY - 64;
        window.scrollTo({ top: top + travel * (i / (panels.length - 1)), behavior: reduceMotion ? 'auto' : 'smooth' });
      });
    });
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
