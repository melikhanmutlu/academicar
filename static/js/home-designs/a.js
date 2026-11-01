// Homepage design A · Kesit — a tool picker and the click-to-play showreel.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
