// Sortering av tabeller og søk i virksomhetslisten. Ingen avhengigheter.
(function () {
  "use strict";
  var collator = new Intl.Collator("nb", { sensitivity: "base", numeric: true });

  function cellValue(row, i, type) {
    var cell = row.cells[i];
    var raw = cell.hasAttribute("data-sort") ? cell.getAttribute("data-sort") : cell.textContent.trim();
    if (type === "num") {
      var n = parseFloat(raw);
      return isNaN(n) ? null : n;
    }
    return raw;
  }

  document.querySelectorAll("table.sortable").forEach(function (table) {
    var headers = table.tHead.rows[0].cells;
    Array.prototype.forEach.call(headers, function (th, i) {
      var button = th.querySelector("button");
      if (!button) return;
      button.addEventListener("click", function () {
        var type = th.getAttribute("data-type") || "text";
        var asc = th.getAttribute("aria-sort") !== "ascending";
        if (th.getAttribute("aria-sort") === null && type === "num") asc = false; // tall: størst først
        Array.prototype.forEach.call(headers, function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellValue(a, i, type), y = cellValue(b, i, type);
          if (x === null && y === null) return 0;
          if (x === null) return 1; // tomme verdier alltid sist
          if (y === null) return -1;
          var d = type === "num" ? x - y : collator.compare(x, y);
          return asc ? d : -d;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  // Søkefelt med data-filter="<tabell-id>" og nedtrekk med data-flag-filter="<tabell-id>".
  // Søket matcher data-name (ellers hele radteksten), nedtrekket et ord i data-flags.
  function applyFilters(tableId) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var input = document.querySelector('input[data-filter="' + tableId + '"]');
    var select = document.querySelector('select[data-flag-filter="' + tableId + '"]');
    var counter = document.querySelector('[data-count-for="' + tableId + '"]');
    var q = input ? input.value.trim().toLowerCase() : "";
    var flag = select ? select.value : "";
    var shown = 0;
    Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
      var text = (row.getAttribute("data-name") || row.textContent).toLowerCase();
      var flags = " " + (row.getAttribute("data-flags") || "") + " ";
      row.hidden = (q !== "" && text.indexOf(q) === -1) || (flag !== "" && flags.indexOf(" " + flag + " ") === -1);
      if (!row.hidden) shown++;
    });
    if (counter) counter.textContent = q || flag ? shown + " treff" : "";
  }
  document.querySelectorAll("input[data-filter]").forEach(function (el) {
    el.addEventListener("input", function () { applyFilters(el.getAttribute("data-filter")); });
  });
  document.querySelectorAll("select[data-flag-filter]").forEach(function (el) {
    el.addEventListener("change", function () { applyFilters(el.getAttribute("data-flag-filter")); });
  });
})();
