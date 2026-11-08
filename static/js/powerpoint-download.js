// "PowerPoint" button: asks the server for a PowerPoint-ready GLB (built by the
// worker), waits while it is prepared, then downloads it. Markup:
// <button data-powerpoint-url="/models/<id>/powerpoint" data-csrf="...">
(function () {
  function toast(message, isError) {
    var el = document.getElementById('powerpoint-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'powerpoint-toast';
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;right:16px;bottom:16px;left:16px;margin-left:auto;max-width:380px;z-index:80;' +
        'background:#fff;color:#202020;border:1px solid #e8e8e8;border-radius:10px;padding:12px 14px;' +
        'box-shadow:0 10px 30px rgba(17,24,39,.12);font-size:14px;line-height:1.45';
      document.body.appendChild(el);
    }
    el.style.borderLeft = '3px solid ' + (isError ? '#b91c1c' : '#ff682c');
    el.innerHTML = message;
    clearTimeout(el._hide);
    el._hide = setTimeout(function () { el.remove(); }, 12000);
  }

  function setBusy(button, busy) {
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = busy;
    button.setAttribute('aria-busy', busy ? 'true' : 'false');
    button.textContent = busy ? 'Preparing…' : button.dataset.label;
  }

  function handle(button, state) {
    if (state.status === 'ready') {
      setBusy(button, false);
      window.location.href = state.download_url;
      toast('<strong>Downloading the PowerPoint file.</strong><br>In PowerPoint choose <em>Insert → 3D Models → This Device</em>. ' +
        'The model is saved inside the .pptx, so it works offline.');
      return;
    }
    if (state.status === 'preparing') {
      setTimeout(function () { poll(button); }, 2500);
      return;
    }
    setBusy(button, false);
    toast(state.error || 'The PowerPoint file could not be prepared. Try again.', true);
  }

  function poll(button) {
    fetch(button.dataset.powerpointUrl, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (state) { handle(button, state); })
      .catch(function () { setBusy(button, false); toast('The PowerPoint file could not be prepared. Try again.', true); });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-powerpoint-url]');
    if (!button || button.disabled) return;
    event.preventDefault();
    setBusy(button, true);
    toast('Preparing a PowerPoint copy of the model. This can take up to a minute for large scans.');
    fetch(button.dataset.powerpointUrl, {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-CSRFToken': button.dataset.csrf || '' },
    })
      .then(function (r) {
        if (r.status === 409) throw new Error('inactive');
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function (state) { handle(button, state); })
      .catch(function (err) {
        setBusy(button, false);
        toast(err.message === 'inactive'
          ? 'This model is not active, so it cannot be downloaded for PowerPoint.'
          : 'The PowerPoint file could not be prepared. Try again.', true);
      });
  });
})();
