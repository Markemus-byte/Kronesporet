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

  function compare(x, y, type, asc) {
    if (x === null && y === null) return 0;
    if (x === null) return 1; // tomme verdier alltid sist
    if (y === null) return -1;
    var d = type === "num" ? x - y : collator.compare(x, y);
    return asc ? d : -d;
  }

  // Ny retning for en kolonne, og aria-sort på overskriftene.
  function nextSort(headers, th) {
    var type = th.getAttribute("data-type") || "text";
    var asc = th.getAttribute("aria-sort") !== "ascending";
    if (th.getAttribute("aria-sort") === null && type === "num") asc = false; // tall: størst først
    Array.prototype.forEach.call(headers, function (h) { h.removeAttribute("aria-sort"); });
    th.setAttribute("aria-sort", asc ? "ascending" : "descending");
    return { type: type, asc: asc };
  }

  var lazy = {}; // tabell-id -> styring for tabeller med data-source

  function domSort(table, i, type, asc) {
    var body = table.tBodies[0];
    // Les verdiene én gang per rad, ikke ved hver sammenligning.
    var keyed = Array.prototype.map.call(body.rows, function (r) { return [cellValue(r, i, type), r]; });
    keyed.sort(function (a, b) { return compare(a[0], b[0], type, asc); });
    var frag = document.createDocumentFragment();
    keyed.forEach(function (k) { frag.appendChild(k[1]); });
    body.appendChild(frag);
  }

  document.querySelectorAll("table.sortable").forEach(function (table) {
    var headers = table.tHead.rows[0].cells;
    Array.prototype.forEach.call(headers, function (th, i) {
      var button = th.querySelector("button");
      if (!button) return;
      button.addEventListener("click", function () {
        var s = nextSort(headers, th);
        if (lazy[table.id]) return lazy[table.id].sort(i, s.type, s.asc);
        domSort(table, i, s.type, s.asc);
      });
    });
  });

  // Store tabeller (leverandørlisten) har bare de første radene i HTML-en. Resten hentes som JSON
  // første gang noen søker, sorterer eller trykker «Vis alle», og tegnes i porsjoner.
  var NB = "\u00a0"; // hardt mellomrom, som i beløpene fra byggeskriptet
  function kr(x) {
    var ax = Math.abs(x);
    if (ax >= 1e9) return (x / 1e9).toFixed(1).replace(".", ",") + NB + "mrd." + NB + "kr";
    if (ax >= 1e6) return (x / 1e6).toFixed(1).replace(".", ",") + NB + "mill." + NB + "kr";
    return group(x) + NB + "kr";
  }
  function group(n) { return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, NB); }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  document.querySelectorAll("table[data-source]").forEach(function (table) {
    var id = table.id, body = table.tBodies[0];
    var counter = document.querySelector('[data-count-for="' + id + '"]');
    var shownNote = document.querySelector('[data-shown-for="' + id + '"]');
    var moreButton = document.querySelector('[data-show-all="' + id + '"]');
    var input = document.querySelector('input[data-filter="' + id + '"]');
    var rows = null, loading = null, limit = body.rows.length;
    var sortCol = null, sortType = "num", sortAsc = false;

    function load() {
      if (!loading) {
        loading = fetch(table.getAttribute("data-source"))
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (data) { rows = data.rows; });
      }
      return loading;
    }
    // Kolonne i tabellen -> verdi i JSON-raden: [navn, orgnr, kontrakter, kjøpere, år ...]
    function value(r, col) { return col === 0 ? r[0] : r[col + 1]; }

    function render() {
      var q = input ? input.value.trim().toLowerCase() : "";
      var list = q ? rows.filter(function (r) { return (r[0] + " " + r[1]).toLowerCase().indexOf(q) !== -1; }) : rows.slice();
      if (sortCol !== null) {
        list.sort(function (a, b) { return compare(value(a, sortCol), value(b, sortCol), sortType, sortAsc); });
      }
      var html = list.slice(0, limit).map(function (r) {
        var cells = '<td><a href="leverandor/' + esc(r[1]) + '.html">' + esc(r[0]) + '</a> <span class="orgnr">' +
          esc(r[1]) + "</span></td>";
        for (var c = 2; c < r.length; c++) {
          cells += '<td class="num" data-sort="' + (r[c] === null ? "" : r[c]) + '">' +
            (r[c] === null ? "–" : c < 4 ? r[c] : kr(r[c])) + "</td>";
        }
        return '<tr data-name="' + esc(r[0].toLowerCase()) + " " + esc(r[1]) + '">' + cells + "</tr>";
      }).join("");
      body.innerHTML = html;
      if (counter) counter.textContent = q ? list.length + " treff" : "";
      var rest = list.length - Math.min(limit, list.length);
      if (shownNote) shownNote.textContent = rest > 0 ? "Viser " + group(Math.min(limit, list.length)) + " av " + group(list.length) + "." : "";
      if (moreButton) {
        moreButton.hidden = rest <= 0;
        moreButton.textContent = "Vis alle " + group(list.length);
      }
    }
    function withData(fn, fallback) {
      load().then(function () { fn(); render(); }, function () {
        // Uten JSON (f.eks. åpnet som fil): søk og sorter bare radene som står i HTML-en.
        delete lazy[id];
        if (shownNote) shownNote.textContent = "Kunne ikke hente hele listen. Søk og sortering gjelder radene over.";
        if (moreButton) moreButton.hidden = true;
        if (fallback) fallback();
      });
    }

    lazy[id] = {
      sort: function (col, type, asc) {
        withData(function () { sortCol = col; sortType = type; sortAsc = asc; },
                 function () { domSort(table, col, type, asc); });
      },
      filter: function () { withData(function () {}, function () { applyFilters(id); }); }
    };
    if (moreButton) moreButton.addEventListener("click", function () { withData(function () { limit = Infinity; }); });
  });

  // Søkefelt med data-filter="<tabell-id>" og nedtrekk med data-flag-filter="<tabell-id>".
  // Søket matcher data-name (ellers hele radteksten), nedtrekket et ord i data-flags.
  function applyFilters(tableId) {
    if (lazy[tableId]) return lazy[tableId].filter();
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
