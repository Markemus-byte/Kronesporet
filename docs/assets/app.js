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

  // Søkefelt med data-filter="<tabell-id>": skjul rader som ikke inneholder teksten.
  // Rader med data-name søkes i det feltet, ellers i hele radteksten.
  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    var table = document.getElementById(input.getAttribute("data-filter"));
    var counter = document.querySelector('[data-count-for="' + input.getAttribute("data-filter") + '"]');
    if (!table) return;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var shown = 0;
      Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
        var text = (row.getAttribute("data-name") || row.textContent).toLowerCase();
        row.hidden = q !== "" && text.indexOf(q) === -1;
        if (!row.hidden) shown++;
      });
      if (counter) counter.textContent = q ? shown + " treff" : "";
    });
  });
})();
