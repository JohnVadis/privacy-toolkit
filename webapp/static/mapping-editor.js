/*
 * mapping-editor.js — place a form's values on the form itself.
 *
 * Everything the user does becomes an entry in `ops`, submitted as one batch so a
 * save is atomic and the server decides the order it is applied in. Nothing is
 * written until Save, and leaving with unsaved work asks first.
 *
 * Local, vendored, no dependencies.
 */
(function () {
  'use strict';

  var CFG = window.MAPPING_EDITOR;
  if (!CFG) { return; }

  var canvas = document.getElementById('canvas');
  var img = document.getElementById('page-img');
  var panelEmpty = document.getElementById('panel-empty');
  var panelEntry = document.getElementById('panel-entry');
  var dirtyNote = document.getElementById('dirty-note');
  var hint = document.getElementById('canvas-hint');

  var W = parseFloat(canvas.dataset.w);
  var H = parseFloat(canvas.dataset.h);
  var PAGE = parseInt(canvas.dataset.page, 10);

  /* index -> pin record, for the properties panel */
  var pins = {};
  CFG.pins.forEach(function (p) { pins[p.index] = p; });

  var ops = [];            /* what will be sent on Save */
  var selected = null;     /* the selected marker element */
  var placing = null;      /* 'text' | 'mark' while waiting for a click */
  var nextNewId = -1;      /* new markers get negative ids until they're saved */

  /* ---------------------------------------------------------------- state */

  function markDirty() {
    var n = ops.length;
    dirtyNote.textContent = n ? (n + ' change' + (n === 1 ? '' : 's') + ' not saved yet')
                              : 'No changes yet';
    dirtyNote.className = n ? 'note-pending' : 'muted';
    document.getElementById('ops').value = JSON.stringify(ops);
  }

  /* One op per marker per kind: editing the same thing twice shouldn't queue twice. */
  function pushOp(op) {
    ops = ops.filter(function (o) {
      return !(o.op === op.op && String(o.entry) === String(op.entry));
    });
    ops.push(op);
    markDirty();
    schedulePreviewRefresh();
  }

  function isNew(index) { return Number(index) < 0; }

  /* --------------------------------------------------------------- moving */

  /* x is the left edge and y is the BASELINE — the point reportlab draws from.
     The box grows up and to the right of it, matching the glyphs. */
  function setMarkerPosition(marker, xPt, yPt) {
    xPt = Math.min(Math.max(xPt, 0), W);
    yPt = Math.min(Math.max(yPt, 0), H);
    marker.dataset.x = xPt.toFixed(1);
    marker.dataset.y = yPt.toFixed(1);
    marker.style.left = (xPt / W * 100) + '%';
    marker.style.top = ((H - yPt) / H * 100) + '%';
    marker.classList.add('is-moved');
    if (marker === selected) {
      document.getElementById('f-x').value = marker.dataset.x;
      document.getElementById('f-y').value = marker.dataset.y;
    }
    recordMove(marker);
  }

  function recordMove(marker) {
    var index = Number(marker.dataset.index);
    if (isNew(index)) {
      /* Not saved yet — fold the position into its pending add. */
      var add = ops.filter(function (o) { return o.op === 'add' && o.entry === index; })[0];
      if (add) {
        add.entry_data.x = Number(marker.dataset.x);
        add.entry_data.y = Number(marker.dataset.y);
        markDirty();
        schedulePreviewRefresh();
      }
      return;
    }
    pushOp({ op: 'move', entry: index, x: Number(marker.dataset.x), y: Number(marker.dataset.y) });
  }

  var dragging = null;
  var dragStarted = false;     /* only true once the pointer has really moved */
  var grab = { x: 0, y: 0, px: 0, py: 0 };

  /* A drag has to travel this far before it counts. Without it, selecting a marker
     nudged it by a pixel or two — so merely looking at something changed it. */
  var DRAG_THRESHOLD_PX = 3;

  canvas.addEventListener('pointerdown', function (e) {
    var marker = e.target.closest('.marker');
    if (!marker) { return; }
    var rect = img.getBoundingClientRect();
    dragging = marker;
    dragStarted = false;
    /* Keep hold of the marker where it was grabbed, rather than snapping its
       centre onto the cursor the moment the pointer twitches. */
    grab.x = parseFloat(marker.dataset.x) - ((e.clientX - rect.left) / rect.width) * W;
    grab.y = parseFloat(marker.dataset.y) - (H - ((e.clientY - rect.top) / rect.height) * H);
    /* grab.* is the offset from the draw point, so the box keeps its position
       under the cursor instead of jumping its corner there. */
    grab.px = e.clientX;
    grab.py = e.clientY;
    marker.setPointerCapture(e.pointerId);
    select(marker);
    e.preventDefault();
  });

  canvas.addEventListener('pointermove', function (e) {
    if (!dragging) { return; }
    if (!dragStarted) {
      if (Math.abs(e.clientX - grab.px) < DRAG_THRESHOLD_PX &&
          Math.abs(e.clientY - grab.py) < DRAG_THRESHOLD_PX) { return; }
      dragStarted = true;
      dragging.classList.add('is-dragging');
    }
    var rect = img.getBoundingClientRect();
    setMarkerPosition(dragging,
      ((e.clientX - rect.left) / rect.width) * W + grab.x,
      H - ((e.clientY - rect.top) / rect.height) * H + grab.y);
  });

  function endDrag() {
    if (!dragging) { return; }
    dragging.classList.remove('is-dragging');
    dragging.focus({ preventScroll: true });
    dragging = null;
    dragStarted = false;
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  /* ------------------------------------------------------------ selecting */

  function select(marker) {
    if (selected) { selected.classList.remove('is-selected'); }
    selected = marker;
    if (!marker) {
      panelEmpty.hidden = false;
      panelEntry.hidden = true;
      return;
    }
    marker.classList.add('is-selected');
    panelEmpty.hidden = true;
    panelEntry.hidden = false;
    fillPanel(marker);
  }

  function fillPanel(marker) {
    var pin = pins[marker.dataset.index] || { data: {}, source: 'variable' };
    var data = pin.data || {};
    document.getElementById('panel-title').textContent =
      pin.kind === 'mark' ? 'Tick box' : 'Value';
    document.getElementById('f-source').value = pin.source || 'variable';
    document.getElementById('f-from').value = data.from || 'full_name';
    document.getElementById('f-literal').value = data.literal || '';
    document.getElementById('f-when').value =
      pin.when_field ? (pin.when_field + '|' + pin.when_equals) : '';
    document.getElementById('f-x').value = marker.dataset.x;
    document.getElementById('f-y').value = marker.dataset.y;
    document.getElementById('f-size').value = data.size || '';
    document.getElementById('f-page').value = data.page || PAGE;

    var anchored = !!data.anchor;
    document.getElementById('anchor-note').hidden = !anchored;
    document.getElementById('anchor-text').textContent = data.anchor || '';
    /* An anchored marker's x/y are derived, so they aren't typed directly. */
    document.getElementById('f-x').disabled = anchored;
    document.getElementById('f-y').disabled = anchored;
    showRows();
  }

  function showRows() {
    var source = document.getElementById('f-source').value;
    document.getElementById('row-variable').hidden = source !== 'variable';
    document.getElementById('row-literal').hidden = source !== 'literal';
    document.getElementById('row-when').hidden = source !== 'mark';
    document.getElementById('f-size').closest('.field').hidden = source === 'mark';
  }

  canvas.addEventListener('click', function (e) {
    var marker = e.target.closest('.marker');
    if (marker) { return; }            /* handled by pointerdown */
    if (placing) { placeNew(e); return; }
    select(null);
  });

  /* ------------------------------------------------------------- the keys */

  document.addEventListener('keydown', function (e) {
    if (!selected) { return; }
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') { return; }

    var step = e.shiftKey ? 10 : 1;
    var dx = 0, dy = 0;
    if (e.key === 'ArrowLeft') { dx = -step; }
    else if (e.key === 'ArrowRight') { dx = step; }
    else if (e.key === 'ArrowUp') { dy = step; }        /* PDF y goes up */
    else if (e.key === 'ArrowDown') { dy = -step; }
    else if (e.key === 'Delete' || e.key === 'Backspace') { removeSelected(); e.preventDefault(); return; }
    else if (e.key === 'Escape') { select(null); return; }
    else { return; }

    e.preventDefault();
    setMarkerPosition(selected,
      parseFloat(selected.dataset.x) + dx,
      parseFloat(selected.dataset.y) + dy);
  });

  /* ------------------------------------------------------ panel -> marker */

  function currentEntryData() {
    var source = document.getElementById('f-source').value;
    var pin = pins[selected.dataset.index] || { data: {} };
    var data = {
      page: Number(document.getElementById('f-page').value) || PAGE
    };
    if (pin.data && pin.data.anchor) {
      data.anchor = pin.data.anchor;
      if (pin.data.occurrence) { data.occurrence = pin.data.occurrence; }
      data.dx = pin.data.dx;
      data.dy = pin.data.dy;
    } else {
      data.x = Number(selected.dataset.x);
      data.y = Number(selected.dataset.y);
    }
    if (source === 'variable') {
      data.from = document.getElementById('f-from').value;
    } else if (source === 'literal') {
      data.literal = document.getElementById('f-literal').value;
    } else {
      data.mark = 'X';
      var when = document.getElementById('f-when').value;
      if (when) {
        var bits = when.split('|');
        data.when = { field: bits[0], equals: bits[1] };
      } else {
        data.check = true;
      }
    }
    var size = Number(document.getElementById('f-size').value);
    if (source !== 'mark' && size) { data.size = size; }
    return data;
  }

  function onPanelChange() {
    if (!selected) { return; }
    showRows();
    var index = Number(selected.dataset.index);
    var data = currentEntryData();

    if (isNew(index)) {
      var add = ops.filter(function (o) { return o.op === 'add' && o.entry === index; })[0];
      if (add) { add.entry_data = data; markDirty(); schedulePreviewRefresh(); }
    } else {
      pushOp({ op: 'update', entry: index, entry_data: data });
    }

    /* Keep the marker's own label and shape in step with the panel. */
    var isMark = document.getElementById('f-source').value === 'mark';
    selected.classList.toggle('is-mark', isMark);
    selected.classList.toggle('is-text', !isMark);
    selected.querySelector('.tag-name').textContent = labelFor(data);
    selected.classList.add('is-moved');
  }

  function labelFor(data) {
    if (data.from) {
      var opt = document.querySelector('#f-from option[value="' + data.from + '"]');
      return opt ? opt.textContent : data.from;
    }
    if (data.literal) { return '“' + data.literal.slice(0, 20) + '”'; }
    if (data.when) { return String(data.when.equals).replace(/_/g, ' '); }
    return 'always ticked';
  }

  ['f-source', 'f-from', 'f-literal', 'f-when', 'f-size', 'f-page']
    .forEach(function (id) {
      document.getElementById(id).addEventListener('change', onPanelChange);
    });
  ['f-x', 'f-y'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', function () {
      if (!selected) { return; }
      setMarkerPosition(selected,
        parseFloat(document.getElementById('f-x').value),
        parseFloat(document.getElementById('f-y').value));
    });
  });

  /* ---------------------------------------------------------- add / remove */

  document.getElementById('add-text-btn').addEventListener('click', function () { arm('text'); });
  document.getElementById('add-mark-btn').addEventListener('click', function () { arm('mark'); });

  function arm(kind) {
    placing = kind;
    canvas.classList.add('is-placing');
    hint.textContent = kind === 'mark'
      ? 'Click the box on the form you want ticked.'
      : 'Click where the value should start.';
  }

  function placeNew(e) {
    var rect = img.getBoundingClientRect();
    var xPt = ((e.clientX - rect.left) / rect.width) * W;
    var yPt = H - ((e.clientY - rect.top) / rect.height) * H;
    var index = nextNewId--;
    var data = placing === 'mark'
      ? { page: PAGE, x: round(xPt), y: round(yPt), mark: 'X', check: true }
      : { page: PAGE, x: round(xPt), y: round(yPt), from: 'full_name' };

    var marker = document.createElement('button');
    marker.type = 'button';
    marker.className = 'marker is-moved is-new ' + (placing === 'mark' ? 'is-mark' : 'is-text');
    marker.dataset.index = index;
    marker.dataset.x = data.x;
    marker.dataset.y = data.y;
    marker.style.left = (data.x / W * 100) + '%';
    marker.style.top = ((H - data.y) / H * 100) + '%';
    marker.style.width = ((placing === 'mark' ? 7 : 80) / W * 100) + '%';
    marker.style.height = (10 / H * 100) + '%';
    marker.innerHTML = '<span class="tag-name"></span>';
    marker.setAttribute('aria-label', 'New marker — select, then use arrow keys to move');
    canvas.appendChild(marker);

    pins[index] = {
      index: index, page: PAGE, x: data.x, y: data.y, data: data,
      kind: placing === 'mark' ? 'mark' : 'text',
      source: placing === 'mark' ? 'mark' : 'variable',
      when_field: '', when_equals: '', anchored: false
    };
    marker.querySelector('.tag-name').textContent = labelFor(data);

    ops.push({ op: 'add', entry: index, entry_data: data });
    markDirty();
    disarm();
    select(marker);
    marker.focus({ preventScroll: true });
  }

  function disarm() {
    placing = null;
    canvas.classList.remove('is-placing');
    setHint();
  }

  function removeSelected() {
    if (!selected) { return; }
    var index = Number(selected.dataset.index);
    if (isNew(index)) {
      ops = ops.filter(function (o) { return !(o.entry === index); });
    } else {
      ops = ops.filter(function (o) { return String(o.entry) !== String(index); });
      ops.push({ op: 'remove', entry: index });
    }
    selected.remove();
    select(null);
    markDirty();
    schedulePreviewRefresh();
  }
  document.getElementById('delete-btn').addEventListener('click', removeSelected);

  /* -------------------------------------------------------------- preview */

  var previewBtn = document.getElementById('preview-btn');
  var blankBtn = document.getElementById('blank-btn');
  var inPreview = false;
  var refreshTimer = null;
  var lastBlobUrl = null;

  function pageUrl(base, n) { return base.replace(/\/\d+$/, '/' + n); }

  /* The preview is rendered from the file PLUS everything still unsaved, so what
     you see is what Save would write — including the marker you just dragged. */
  function renderPreview(quiet) {
    var body = new URLSearchParams();
    body.set('text', document.getElementById('raw-text').value);
    body.set('client', document.getElementById('preview-client').value);
    body.set('ops', JSON.stringify(ops));

    if (!quiet) {
      previewBtn.disabled = true;
      previewBtn.textContent = 'Rendering…';
    }
    canvas.classList.add('is-refreshing');

    return fetch(pageUrl(CFG.previewUrl, PAGE), { method: 'POST', body: body })
      .then(function (r) {
        if (!r.ok) { return r.text().then(function (t) { throw new Error(t); }); }
        return r.blob();
      })
      .then(function (blob) {
        if (lastBlobUrl) { URL.revokeObjectURL(lastBlobUrl); }
        lastBlobUrl = URL.createObjectURL(blob);
        img.src = lastBlobUrl;
        inPreview = true;
        canvas.classList.add('is-preview');
        blankBtn.hidden = false;
        setHint();
      })
      .catch(function () {
        hint.textContent = 'Could not render a preview — check the mapping file below.';
      })
      .finally(function () {
        canvas.classList.remove('is-refreshing');
        previewBtn.disabled = false;
        previewBtn.textContent = 'Preview';
      });
  }

  previewBtn.addEventListener('click', function () { renderPreview(false); });

  /* After a change while previewing, re-render once things settle rather than on
     every pixel of a drag. */
  function schedulePreviewRefresh() {
    if (!inPreview) { return; }
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(function () { renderPreview(true); }, 500);
  }

  blankBtn.addEventListener('click', function () {
    window.clearTimeout(refreshTimer);
    img.src = pageUrl(CFG.blankUrl, PAGE);
    inPreview = false;
    canvas.classList.remove('is-preview', 'is-refreshing');
    blankBtn.hidden = true;
    disarm();
    setHint();
  });

  function setHint() {
    hint.textContent = inPreview
      ? 'Showing the filled form for the selected client, including your unsaved '
        + 'changes. Drag or nudge a marker and it re-renders.'
      : 'Click a marker to select it. Drag it, or nudge with the arrow keys '
        + '(hold Shift for bigger steps). Markers on other pages are left alone.';
  }

  /* ----------------------------------------------------------- submitting */

  var form = document.getElementById('map-form');
  form.addEventListener('submit', function () {
    /* The textarea is the base the ops are applied to. */
    document.getElementById('text').value = document.getElementById('raw-text').value;
    document.getElementById('ops').value = JSON.stringify(ops);
    document.getElementById('page-field').value = PAGE;
    window.onbeforeunload = null;
  });

  window.addEventListener('beforeunload', function (e) {
    if (!ops.length) { return; }
    e.preventDefault();
    e.returnValue = '';
  });

  /* Changing page reloads, so warn before losing pending edits. */
  document.querySelectorAll('[data-nav]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      if (ops.length && !window.confirm('You have unsaved changes on this page. Leave anyway?')) {
        e.preventDefault();
      }
    });
  });

  function round(n) { return Math.round(n * 10) / 10; }

  markDirty();
  setHint();

  /* Show the filled form straight away. A blank page can't tell you whether a
     marker is on the right line — only a rendered value can, and the example
     client gives every marker something to draw without opening a real file. */
  if (img.complete) { renderPreview(true); }
  else { img.addEventListener('load', function once() {
    img.removeEventListener('load', once);
    renderPreview(true);
  }); }
})();
