/* app.js — the only hand-written JS. Local, no network, ~30 lines.
   HTMX does the fetching; this just supplies the next row index and removes rows. */

/* Next free index for a repeatable group, so removing row 1 of [0,1,2] and then
   adding gives 3 — never a collision with an existing row. */
function nextIndex(group) {
  var max = -1;
  document.querySelectorAll('[data-idx-group="' + CSS.escape(group) + '"]').forEach(function (el) {
    var n = parseInt(el.dataset.idx, 10);
    if (!isNaN(n) && n > max) { max = n; }
  });
  return max + 1;
}

/* Remove the nearest enclosing repeatable block. */
function removeRow(btn, selector) {
  var block = btn.closest(selector);
  if (!block) { return; }
  var group = block.parentElement;
  block.remove();
  if (group) { group.dispatchEvent(new CustomEvent('rows:changed', { bubbles: true })); }
}

/* Keep the "residence" radios pointing at a row that still exists: if the checked
   one was just removed, check the first remaining address for that person. */
document.addEventListener('rows:changed', function (e) {
  var host = e.target.closest('[data-person-block]') || document;
  host.querySelectorAll('[data-residence-group]').forEach(function (fieldset) {
    var radios = fieldset.querySelectorAll('input[type="radio"]');
    var anyChecked = Array.prototype.some.call(radios, function (r) { return r.checked; });
    if (!anyChecked && radios.length) { radios[0].checked = true; }
  });
});
