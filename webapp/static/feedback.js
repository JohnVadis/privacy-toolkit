/*
 * feedback.js — mark up a screenshot before commenting on it.
 *
 * Two tools: Highlight (translucent yellow) and Hide (solid black). Hide is drawn onto
 * the image data itself, so a redacted area is genuinely gone from the file that gets
 * attached — not merely covered by something a viewer could peel away.
 */
(function () {
  'use strict';

  var url = window.FEEDBACK_IMAGE;
  var canvas = document.getElementById('shot');
  if (!url || !canvas) { return; }

  var ctx = canvas.getContext('2d');
  var base = new Image();
  var marks = [];                 /* {tool, x, y, w, h} in image pixels */
  var tool = 'highlight';
  var drawing = null;

  base.onload = function () {
    canvas.width = base.naturalWidth;
    canvas.height = base.naturalHeight;
    redraw();
  };
  base.src = url;

  function redraw() {
    ctx.drawImage(base, 0, 0);
    marks.forEach(function (m) {
      if (m.tool === 'hide') {
        ctx.fillStyle = '#000';
        ctx.fillRect(m.x, m.y, m.w, m.h);
      } else {
        ctx.fillStyle = 'rgba(255, 214, 0, 0.32)';
        ctx.fillRect(m.x, m.y, m.w, m.h);
        ctx.strokeStyle = 'rgba(200, 150, 0, 0.95)';
        ctx.lineWidth = Math.max(2, canvas.width / 600);
        ctx.strokeRect(m.x, m.y, m.w, m.h);
      }
    });
    var n = marks.length;
    document.getElementById('marks-note').textContent =
      n ? (n + ' mark' + (n === 1 ? '' : 's')) : 'Drag on the picture to mark it';
  }

  /* Canvas pixels from a pointer position, whatever size it is displayed at. */
  function at(e) {
    var r = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) / r.width * canvas.width,
      y: (e.clientY - r.top) / r.height * canvas.height
    };
  }

  canvas.addEventListener('pointerdown', function (e) {
    var p = at(e);
    drawing = { tool: tool, x: p.x, y: p.y, w: 0, h: 0 };
    canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  canvas.addEventListener('pointermove', function (e) {
    if (!drawing) { return; }
    var p = at(e);
    drawing.w = p.x - drawing.x;
    drawing.h = p.y - drawing.y;
    redraw();
    var m = normalise(drawing);
    ctx.fillStyle = drawing.tool === 'hide' ? 'rgba(0,0,0,0.75)' : 'rgba(255,214,0,0.32)';
    ctx.fillRect(m.x, m.y, m.w, m.h);
  });

  function stop() {
    if (!drawing) { return; }
    var m = normalise(drawing);
    drawing = null;
    if (m.w > 4 && m.h > 4) { marks.push(m); }
    redraw();
  }
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);

  function normalise(m) {
    return {
      tool: m.tool,
      x: m.w < 0 ? m.x + m.w : m.x,
      y: m.h < 0 ? m.y + m.h : m.y,
      w: Math.abs(m.w),
      h: Math.abs(m.h)
    };
  }

  document.querySelectorAll('[data-tool]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      tool = btn.dataset.tool;
      document.querySelectorAll('[data-tool]').forEach(function (b) {
        b.classList.toggle('btn-primary', b === btn);
      });
      canvas.classList.toggle('is-hiding', tool === 'hide');
    });
  });

  document.getElementById('undo-btn').addEventListener('click', function () {
    marks.pop();
    redraw();
  });
  document.getElementById('clear-btn').addEventListener('click', function () {
    marks = [];
    redraw();
  });

  /* Send the flattened picture, so redactions are part of the pixels. */
  document.getElementById('feedback-form').addEventListener('submit', function () {
    redraw();
    document.getElementById('image-data').value = canvas.toDataURL('image/png');
  });
})();
