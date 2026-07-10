// Game-detail time-series charts (UI_PLAN.md §3.3, UI-M3).
// A static module (CSP: script-src 'self' — no inline scripts). Each chart is a
// <div class="chart" data-game data-metric data-label> that this script fills by
// fetching /api/v1/games/{id}/series and rendering a uPlot line. Range buttons
// (<a data-range>) inside a [data-chart-group] re-fetch the sibling chart.
(function () {
  "use strict";
  function draw(el, range) {
    var game = el.getAttribute("data-game");
    var metric = el.getAttribute("data-metric");
    var label = el.getAttribute("data-label") || metric;
    var url = "/api/v1/games/" + encodeURIComponent(game) +
      "/series?metric=" + encodeURIComponent(metric) +
      "&range=" + encodeURIComponent(range);
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        el.innerHTML = "";
        if (!d.ts || d.ts.length === 0) {
          el.textContent = "No data for this range.";
          return;
        }
        var opts = {
          width: el.clientWidth || 640,
          height: 240,
          series: [
            {},
            { label: label, stroke: "#2563eb", width: 2 }
          ],
          scales: { x: { time: true } }
        };
        new uPlot(opts, [d.ts, d.values], el);
      })
      .catch(function () { el.textContent = "Failed to load chart."; });
  }

  function init() {
    var groups = document.querySelectorAll("[data-chart-group]");
    groups.forEach(function (group) {
      var chart = group.querySelector(".chart");
      if (!chart) return;
      var initial = chart.getAttribute("data-range") || "7d";
      draw(chart, initial);
      group.querySelectorAll("[data-range]").forEach(function (btn) {
        btn.addEventListener("click", function (ev) {
          ev.preventDefault();
          var range = btn.getAttribute("data-range");
          group.querySelectorAll("[data-range]").forEach(function (b) {
            b.classList.toggle("active", b === btn);
          });
          draw(chart, range);
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
