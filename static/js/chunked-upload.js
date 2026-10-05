/*
 * Resumable chunked uploads + live processing progress.
 *
 * Progressive enhancement: a form marked data-chunked-upload='{"paper_slug": "..."}'
 * (or {"model_id": "..."} / {"new_project": true}) sends its model file in small
 * PUT requests (see uploads.py) and then submits itself with a hidden upload_id
 * instead of the file. Without JS, fetch or File.slice the plain multipart form
 * keeps working. Every number on the progress panel is what the server reports
 * as received; nothing is estimated from what was merely sent.
 *
 * AcademicARProgress.update(panel, payload) fills a processing progress panel
 * ([data-progress-panel]) from the /models/<id>/status JSON.
 */
(function () {
  'use strict';

  var MB = 1024 * 1024;
  var BACKOFF_MS = [1000, 2000, 4000, 8000];
  var SPEED_WINDOW = 6;
  var NOTE = 'Keep this page open until the upload finishes. After that you can leave — processing continues on our servers.';

  function formatMB(bytes) {
    if (bytes < MB / 10) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / MB).toFixed(1) + ' MB';
  }
  function formatSize(received, size) {
    if (size < MB / 10) return (received / 1024).toFixed(1) + ' / ' + (size / 1024).toFixed(1) + ' KB';
    return (received / MB).toFixed(1) + ' / ' + (size / MB).toFixed(1) + ' MB';
  }
  function formatEta(seconds) {
    if (!isFinite(seconds) || seconds < 0) return '';
    if (seconds < 5) return 'almost done';
    if (seconds < 60) return Math.round(seconds / 5) * 5 + ' s left';
    if (seconds < 3600) return Math.ceil(seconds / 60) + ' min left';
    var h = Math.floor(seconds / 3600);
    var m = Math.round((seconds % 3600) / 60);
    return h + ' h ' + m + ' min left';
  }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  /* ---------- processing progress panel ---------- */

  function ordinal(n) {
    var rest = n % 100;
    if (rest >= 11 && rest <= 13) return n + 'th';
    return n + (['th', 'st', 'nd', 'rd'][n % 10] || 'th');
  }

  function updateProgressPanel(panel, payload) {
    if (!panel || !payload) return;
    var progress = typeof payload.progress === 'number' ? Math.max(0, Math.min(100, payload.progress)) : 0;
    var stage = payload.stage || (payload.status === 'queued' ? 'Waiting for the converter' : 'Processing');
    var stageNode = panel.querySelector('[data-progress-stage]');
    var percentNode = panel.querySelector('[data-progress-percent]');
    var fill = panel.querySelector('[data-progress-fill]');
    var bar = panel.querySelector('[role="progressbar"]');
    var queue = panel.querySelector('[data-progress-queue]');
    if (stageNode) stageNode.textContent = stage;
    if (percentNode) percentNode.textContent = Math.round(progress) + '%';
    if (fill) fill.style.width = progress + '%';
    if (bar) bar.setAttribute('aria-valuenow', String(Math.round(progress)));
    var chip = panel.closest('[data-model-row]') && panel.closest('[data-model-row]').querySelector('.detail-chip.status');
    if (chip && payload.status && payload.status !== 'ready' && payload.status !== 'failed') {
      chip.textContent = payload.status.charAt(0).toUpperCase() + payload.status.slice(1);
    }
    if (queue) {
      var position = payload.queue_position;
      queue.textContent = position ? (position === 1 ? 'Next in line' : ordinal(position) + ' in line') : '';
      queue.hidden = !position;
    }
  }

  window.AcademicARProgress = { update: updateProgressPanel };

  /* ---------- chunked upload ---------- */

  function supported() {
    return !!(window.fetch && window.File && File.prototype && File.prototype.slice && window.FormData && window.AbortController);
  }

  function attach(form) {
    var target;
    try { target = JSON.parse(form.getAttribute('data-chunked-upload')); } catch (e) { target = null; }
    if (!target || !supported()) return;
    var input = form.querySelector('[data-dropzone-input], input[type="file"][name="file"], input[type="file"][name="model_file"]');
    if (!input) return;
    var csrfField = form.querySelector('input[name="csrf_token"]');
    var host = form.querySelector('[data-upload-progress-host]');

    var state = {
      session: null,      // {id, chunkSize, size, fingerprint}
      file: null,
      received: 0,
      samples: [],
      running: false,
      cancelled: false,
      armed: false,
      finished: false,
      controller: null,
      submitter: null,
      csrf: csrfField ? csrfField.value : ''
    };

    /* panel */
    var panel = el('div', 'cu-panel');
    panel.hidden = true;
    panel.setAttribute('role', 'status');
    panel.setAttribute('aria-live', 'polite');
    panel.setAttribute('data-upload-progress', '');
    var head = el('div', 'cu-head');
    var stepNode = el('span', 'cu-step', 'Uploading');
    var percentNode = el('span', 'cu-percent', '0%');
    head.appendChild(stepNode);
    head.appendChild(percentNode);
    var bar = el('div', 'cu-bar');
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', 'Upload progress');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', '0');
    var fill = el('span', 'cu-fill');
    bar.appendChild(fill);
    var meta = el('div', 'cu-meta');
    var bytesNode = el('span', 'cu-bytes');
    var speedNode = el('span', 'cu-speed');
    var etaNode = el('span', 'cu-eta');
    meta.appendChild(bytesNode);
    meta.appendChild(speedNode);
    meta.appendChild(etaNode);
    var note = el('p', 'cu-note', NOTE);
    var errorNode = el('p', 'cu-error');
    errorNode.hidden = true;
    errorNode.setAttribute('role', 'alert');
    var actions = el('div', 'cu-actions');
    var cancelButton = el('button', 'cu-cancel', 'Cancel upload');
    cancelButton.type = 'button';
    var retryButton = el('button', 'cu-retry', 'Try again');
    retryButton.type = 'button';
    retryButton.hidden = true;
    actions.appendChild(retryButton);
    actions.appendChild(cancelButton);
    [head, bar, meta, note, errorNode, actions].forEach(function (node) { panel.appendChild(node); });
    if (host) host.appendChild(panel); else form.appendChild(panel);

    function submitButtons() { return form.querySelectorAll('button[type="submit"], input[type="submit"]'); }
    function setBusy(busy) {
      submitButtons().forEach(function (button) {
        button.disabled = busy;
        button.classList.toggle('opacity-70', busy);
        button.classList.toggle('cursor-not-allowed', busy);
      });
      input.disabled = busy;
    }
    function beforeUnload(event) {
      if (!state.armed) return undefined;
      event.preventDefault();
      event.returnValue = '';
      return '';
    }
    window.addEventListener('beforeunload', beforeUnload);
    function arm(on) { state.armed = on; }

    function render() {
      var size = state.session ? state.session.size : (state.file ? state.file.size : 0);
      var percent = size ? Math.floor((state.received / size) * 100) : 0;
      percentNode.textContent = percent + '%';
      fill.style.width = percent + '%';
      bar.setAttribute('aria-valuenow', String(percent));
      bytesNode.textContent = formatSize(state.received, size);
      var speed = currentSpeed();
      speedNode.textContent = speed ? (speed / MB).toFixed(1) + ' MB/s' : '';
      etaNode.textContent = speed ? formatEta((size - state.received) / speed) : '';
    }
    function currentSpeed() {
      var s = state.samples;
      if (s.length < 2) return 0;
      var first = s[0];
      var last = s[s.length - 1];
      var seconds = (last.t - first.t) / 1000;
      return seconds > 0 ? (last.bytes - first.bytes) / seconds : 0;
    }
    function sample() {
      state.samples.push({ t: Date.now(), bytes: state.received });
      if (state.samples.length > SPEED_WINDOW) state.samples.shift();
    }
    function step(text) { stepNode.textContent = text; }
    function showError(message, canRetry) {
      speedNode.textContent = '';
      etaNode.textContent = '';
      errorNode.textContent = message;
      errorNode.hidden = false;
      retryButton.hidden = !canRetry;
    }
    function clearError() {
      errorNode.hidden = true;
      retryButton.hidden = true;
    }

    /* requests */
    function headers(extra) {
      var h = { 'X-CSRFToken': state.csrf, 'Accept': 'application/json' };
      for (var key in extra) h[key] = extra[key];
      return h;
    }
    function takeToken(body) {
      if (body && body.csrf_token) {
        state.csrf = body.csrf_token;
        if (csrfField) csrfField.value = body.csrf_token;
      }
    }
    // Resolves {status, body}; rejects only for network failures and aborts.
    function request(url, options) {
      options.credentials = 'same-origin';
      options.signal = state.controller.signal;
      return fetch(url, options).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          takeToken(body);
          return { status: response.status, body: body };
        });
      });
    }
    // A failure retrying the same request cannot fix. ``retryable``: the person can try again later.
    function fatal(message, retryable) {
      var error = new Error(message);
      error.fatal = true;
      error.retryable = !!retryable;
      return error;
    }
    function explain(result) {
      var body = result.body || {};
      if (body.code === 'csrf_expired') return 'This page has been open too long. Reload it and select the file again.';
      if (result.status === 401) return 'You were signed out. Log in again, then select the file again.';
      return body.error || 'The upload failed. Please try again.';
    }

    function startSession() {
      step('Preparing the upload');
      return request('/uploads', {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ filename: state.file.name, size: state.file.size, target: target })
      }).then(function (result) {
        if (result.status !== 201) throw fatal(explain(result), result.status === 507 || result.status === 429);
        state.session = {
          id: result.body.upload_id,
          chunkSize: result.body.chunk_size,
          size: state.file.size,
          fingerprint: fingerprint(state.file)
        };
        state.received = 0;
        state.samples = [];
      });
    }
    function fingerprint(file) { return [file.name, file.size, file.lastModified].join('|'); }

    // Asks the server how much it holds. Returns that number or throws.
    function resume() {
      return request('/uploads/' + state.session.id, { method: 'GET', headers: headers({}) }).then(function (result) {
        if (result.status === 404) {
          state.session = null;
          throw fatal('This upload expired on the server. Select the file again to restart it.', true);
        }
        if (result.status !== 200) throw new Error('resume ' + result.status);
        state.received = result.body.received;
        return state.received;
      });
    }

    function sendChunk(offset) {
      var end = Math.min(offset + state.session.chunkSize, state.session.size);
      var attempt = 0;
      function once() {
        return request('/uploads/' + state.session.id + '?offset=' + offset, {
          method: 'PUT',
          headers: headers({ 'Content-Type': 'application/octet-stream' }),
          body: state.file.slice(offset, end)
        }).then(function (result) {
          if (result.status === 200) {
            state.received = result.body.received;
            return;
          }
          if (result.status === 409 && typeof result.body.received === 'number') {
            state.received = result.body.received; // the server is somewhere else: carry on from there
            return;
          }
          if (result.status >= 500 || result.status === 429) throw new Error('retry ' + result.status);
          throw fatal(explain(result), result.status === 507);
        });
      }
      function attemptLoop() {
        return once().catch(function (error) {
          if (error.fatal || state.cancelled || error.name === 'AbortError') throw error;
          if (attempt >= BACKOFF_MS.length) throw error;
          var delay = BACKOFF_MS[attempt];
          attempt += 1;
          step('Connection problem — retrying (' + attempt + ' of ' + BACKOFF_MS.length + ')');
          return sleep(delay).then(function () {
            if (state.cancelled) throw error;
            // The reply may simply have been lost: ask where the server is before resending.
            return resume().then(function (received) {
              if (received > offset) return;
            }, function (resumeError) {
              if (resumeError.fatal) throw resumeError;
            });
          }).then(function () {
            if (state.received > offset && state.received >= end) return;
            step('Uploading');
            return attemptLoop();
          });
        });
      }
      return attemptLoop();
    }

    function loop() {
      if (state.cancelled) return Promise.reject(new DOMException('cancelled', 'AbortError'));
      if (state.received >= state.session.size) return Promise.resolve();
      return sendChunk(state.received).then(function () {
        step('Uploading'); // also clears a "retrying" message once a chunk has gone through
        sample();
        render();
        return loop();
      });
    }

    function run() {
      if (state.running) return;
      var chosen = input.files && input.files[0];
      if (chosen && state.session && state.session.fingerprint !== fingerprint(chosen)) {
        discardSession(); // another file was picked after a failure: its old upload is of no use
      }
      if (chosen) state.file = chosen;
      state.running = true;
      state.cancelled = false;
      state.controller = new AbortController();
      clearError();
      panel.hidden = false;
      if (panel.scrollIntoView) panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      setBusy(true);
      arm(true);
      step('Uploading');
      var begin = state.session && state.session.fingerprint === fingerprint(state.file)
        ? resume().catch(function (error) {
            if (error.fatal) { return startSession(); }
            throw error;
          })
        : startSession();
      begin.then(function () {
        step('Uploading');
        state.samples = [];
        sample();
        render();
        return loop();
      }).then(function () {
        step('Checking the file');
        return resume();
      }).then(function (received) {
        if (received !== state.session.size) throw fatal('The server did not receive the whole file. Please try again.', true);
        render();
        step('Starting processing');
        finish();
      }).catch(function (error) {
        state.running = false;
        if (state.cancelled || (error && error.name === 'AbortError')) return;
        arm(false);
        setBusy(false);
        step('Upload paused');
        if (error && error.fatal) showError(error.message, error.retryable);
        else showError('The connection was lost. Your progress is kept \u2014 check your network, then try again.', true);
      });
    }

    function finish() {
      var hidden = form.querySelector('input[name="upload_id"]');
      if (!hidden) {
        hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'upload_id';
        form.appendChild(hidden);
      }
      hidden.value = state.session.id;
      cancelButton.hidden = true;
      state.finished = true;
      form.dataset.chunkedDone = '1';
      arm(false); // leaving is fine from here on: the server has the whole file
      input.disabled = true; // a disabled file input is not sent
      submitButtons().forEach(function (button) { button.disabled = false; });
      var submitter = state.submitter;
      try {
        if (form.requestSubmit) {
          if (submitter && submitter.form === form) form.requestSubmit(submitter); else form.requestSubmit();
        } else {
          HTMLFormElement.prototype.submit.call(form);
        }
      } catch (e) {
        HTMLFormElement.prototype.submit.call(form);
      }
    }

    function discardSession() {
      var id = state.session && state.session.id;
      state.session = null;
      state.received = 0;
      state.samples = [];
      if (id) {
        fetch('/uploads/' + id, { method: 'DELETE', headers: { 'X-CSRFToken': state.csrf }, credentials: 'same-origin' }).catch(function () {});
      }
    }

    function cancel() {
      state.cancelled = true;
      arm(false);
      if (state.controller) state.controller.abort();
      state.running = false;
      discardSession();
      panel.hidden = true;
      clearError();
      setBusy(false);
      input.focus();
    }

    cancelButton.addEventListener('click', cancel);
    retryButton.addEventListener('click', function () { run(); });

    // Capture on the document so this runs before the page's own submit handlers.
    document.addEventListener('submit', function (event) {
      if (event.target !== form) return;
      if (form.dataset.chunkedDone === '1') return; // the final, real submit: let the page handle it
      var file = input.files && input.files[0];
      if (!file) return; // nothing to chunk: the page's own handler explains what is missing
      if (state.running) { event.preventDefault(); event.stopImmediatePropagation(); return; }
      // Validation (consent boxes, unit, ...) happens before a single byte is uploaded.
      if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (form.reportValidity) form.reportValidity();
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      state.submitter = event.submitter || null;
      state.file = file;
      run();
    }, true);

    // Back/forward cache: a page restored after the final submit starts clean.
    window.addEventListener('pageshow', function (event) {
      if (!event.persisted || form.dataset.chunkedDone !== '1') return;
      delete form.dataset.chunkedDone;
      var hidden = form.querySelector('input[name="upload_id"]');
      if (hidden) hidden.remove();
      state.session = null;
      state.finished = false;
      state.running = false;
      state.received = 0;
      panel.hidden = true;
      cancelButton.hidden = false;
      clearError();
      setBusy(false);
    });
  }

  function init() {
    document.querySelectorAll('form[data-chunked-upload]').forEach(attach);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
