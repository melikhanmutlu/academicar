// Smooth open/close for FAQ <details> lists. Mark the list with
// data-smooth-details; everything after each <summary> slides instead of
// jumping. Skipped under prefers-reduced-motion (native toggle stays).
(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll('[data-smooth-details] details').forEach((item) => {
    const summary = item.querySelector('summary');
    if (!summary) return;
    const body = document.createElement('div');
    body.style.overflow = 'hidden';
    while (summary.nextSibling) body.appendChild(summary.nextSibling);
    item.appendChild(body);
    let anim = null;
    summary.addEventListener('click', (event) => {
      event.preventDefault();
      const closing = item.open && !item.classList.contains('is-closing');
      // A closed <details> may still report its content height, so start from 0.
      const from = item.open ? body.offsetHeight : 0;
      if (anim) anim.cancel();
      if (closing) {
        item.classList.add('is-closing');
      } else {
        item.classList.remove('is-closing');
        item.open = true;
      }
      const to = closing ? 0 : body.scrollHeight;
      anim = body.animate(
        [{ height: from + 'px', opacity: closing ? 1 : 0 }, { height: to + 'px', opacity: closing ? 0 : 1 }],
        { duration: 280, easing: 'cubic-bezier(0.2, 0, 0, 1)' }
      );
      anim.onfinish = () => {
        anim = null;
        if (closing) { item.open = false; item.classList.remove('is-closing'); }
      };
    });
  });
})();
