// Shared entrance for the homepage design previews: inside each section, the
// heading, then the text, then the picture rise in once as the section enters.
// Content is only hidden after this script runs, so nothing stays invisible
// without it, and reduced-motion visitors get no travel at all.
(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const sections = [...document.querySelectorAll('main section')].slice(1); // the hero has its own entrance
  const pick = 'h2, h3, p, dl, ol > li, ul > li, figure, picture, img:not([aria-hidden]), video, model-viewer, table';
  const armed = new Set();

  const reveal = (section) => {
    if (!armed.delete(section)) return;
    section.classList.add('hd-in');
    setTimeout(() => {
      section.querySelectorAll('.hd-ri').forEach((el) => el.classList.remove('hd-ri'));
      section.classList.remove('hd-armed', 'hd-in');
    }, 1500);
  };
  // Anything whose top has come into the lower 88% of the screen (or been scrolled past) shows.
  const check = () => {
    const line = window.innerHeight * 0.88;
    armed.forEach((section) => { if (section.getBoundingClientRect().top < line) reveal(section); });
  };

  sections.forEach((section) => {
    if (section.matches('[data-hd-noreveal]')) return;
    const parts = [...section.querySelectorAll(pick)].filter((el) =>
      !el.closest('[data-hd-noreveal]') && !el.parentElement.closest('.hd-ri'));
    if (!parts.length) return;
    parts.slice(0, 14).forEach((el, i) => {
      el.classList.add('hd-ri');
      el.style.setProperty('--hd-i', Math.min(i, 8));
    });
    section.classList.add('hd-armed');
    armed.add(section);
  });

  let ticking = false;
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { check(); ticking = false; });
  }, { passive: true });
  window.addEventListener('resize', check);
  check();
})();
