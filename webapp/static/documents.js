/*
 * documents.js — pick which rows of a record export belong to this client.
 *
 * A name search at the Clerk returns every SMITH in the county, so the import shows
 * every parsed row unticked and this narrows them down: type a name to hide the rest,
 * then tick what is left. Filtering only hides rows — it never unticks one, so a row
 * ticked under one filter stays ticked when the filter changes, and the count always
 * reports the whole set rather than what happens to be on screen.
 *
 * Nothing here talks to the server. The form posts the ticked positions.
 */
(function () {
  'use strict';

  var picker = document.getElementById('picker');
  var table = document.getElementById('pick-table');
  if (!picker || !table) { return; }

  var filter = document.getElementById('pick-filter');
  var counter = document.getElementById('pick-count');
  var rows = Array.prototype.slice.call(table.tBodies[0].rows);
  var total = rows.length;

  /* Match against the row's own text, lowercased once up front — with a few hundred
     rows this runs on every keystroke. */
  rows.forEach(function (row) {
    row.dataset.haystack = (row.textContent || '').toLowerCase().replace(/\s+/g, ' ');
  });

  function shown() {
    return rows.filter(function (row) { return row.style.display !== 'none'; });
  }

  function box(row) {
    return row.querySelector('input[name="pick"]');
  }

  function count() {
    var ticked = rows.filter(function (row) {
      var input = box(row);
      return input && input.checked;
    }).length;
    var visible = shown().length;
    var text = ticked + ' of ' + total + ' ticked';
    if (visible !== total) { text += ' · ' + visible + ' shown'; }
    counter.textContent = text;
  }

  function apply() {
    var needle = (filter.value || '').toLowerCase().trim();
    rows.forEach(function (row) {
      row.style.display = (!needle || row.dataset.haystack.indexOf(needle) !== -1) ? '' : 'none';
    });
    count();
  }

  if (filter) {
    filter.addEventListener('input', apply);
    /* Enter would submit the form with whatever is ticked so far. */
    filter.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); apply(); }
    });
  }

  picker.addEventListener('click', function (e) {
    var button = e.target.closest ? e.target.closest('[data-pick]') : null;
    if (!button) { return; }
    var on = button.getAttribute('data-pick') === 'all';
    shown().forEach(function (row) {
      var input = box(row);
      if (input) { input.checked = on; }
    });
    count();
  });

  table.addEventListener('change', count);
  count();
}());
