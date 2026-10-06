/* GazeAssist — Analytics tab charts (Chart.js 4).
 *
 * Data comes from the page as JSON (json_script id="analytics-data"), built in
 * PatientAnalyticsView: one entry per calendar day of the chosen period.
 *
 * Style rules (kept the same on every chart):
 *  - one series per chart, so no legend; the card title names the series
 *  - bars <= 24px thick with a 4px rounded end, square at the baseline
 *  - lines 2px, markers 8px with a 2px white ring
 *  - hairline gridlines on the value axis only; muted axis text
 *  - colours clear 3:1 against the white card; text never uses the series colour
 *  - the whole day column is the hover target, not just the painted mark
 *  - every value is also in a "Show as table" view
 */
(function () {
  "use strict";

  var source = document.getElementById("analytics-data");
  if (!source || !window.Chart) return;
  var data = JSON.parse(source.textContent);

  var COLORS = {
    requests: "#3B82F6",       // brand blue, 3.7:1 on white
    requestsHover: "#2563EB",
    emergency: "#DC2626",      // red, 4.8:1
    pain: "#D97706",           // amber-600, 3.2:1 (brand amber #F59E0B is only 2.2:1)
    ink: "#0F172A",
    muted: "#64748B",
    grid: "#E2E8F0",
    axis: "#CBD5E1",
    surface: "#FFFFFF"
  };

  function withAlpha(hex, alpha) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  Chart.defaults.font.family = '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLORS.muted;
  if (reduceMotion) Chart.defaults.animation = false;

  /* ---------------- shared pieces ---------------- */

  /** Tooltip: dark card, date as the title, the value bold, a short line key in the series colour. */
  function tooltip(extra) {
    var base = {
      backgroundColor: COLORS.ink,
      titleColor: "#CBD5E1",
      titleFont: { weight: "500", size: 12 },
      bodyColor: "#FFFFFF",
      bodyFont: { weight: "700", size: 13 },
      padding: 10,
      cornerRadius: 8,
      caretSize: 0,
      displayColors: true,
      usePointStyle: true,
      boxWidth: 12,
      boxHeight: 2,
      callbacks: {
        title: function (items) { return data.longLabels[items[0].dataIndex]; },
        labelPointStyle: function () { return { pointStyle: "line", rotation: 0 }; },
        labelColor: function (item) {
          var c = item.dataset.borderColor || item.dataset.backgroundColor;
          return { borderColor: c, backgroundColor: c, borderWidth: 2 };
        }
      }
    };
    extra = extra || {};
    Object.keys(extra).forEach(function (key) {
      if (key === "callbacks") {
        Object.keys(extra.callbacks).forEach(function (cb) { base.callbacks[cb] = extra.callbacks[cb]; });
      } else {
        base[key] = extra[key];
      }
    });
    return base;
  }

  /** Vertical hairline at the hovered day (line charts). */
  var crosshair = {
    id: "gaCrosshair",
    afterDatasetsDraw: function (chart, args, opts) {
      if (!opts || !opts.enabled) return;
      var active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      var x = active[0].element.x;
      var area = chart.chartArea;
      var ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = "#94A3B8";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      ctx.restore();
    }
  };

  /** Value at the tip of each horizontal bar, in text ink (never the bar colour). */
  var barValues = {
    id: "gaBarValues",
    afterDatasetsDraw: function (chart, args, opts) {
      if (!opts || !opts.enabled) return;
      var ctx = chart.ctx;
      var meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.fillStyle = COLORS.ink;
      ctx.font = '600 12px "Inter", system-ui, sans-serif';
      ctx.textBaseline = "middle";
      meta.data.forEach(function (bar, i) {
        ctx.fillText(String(chart.data.datasets[0].data[i]), bar.x + 8, bar.y);
      });
      ctx.restore();
    }
  };

  function timeScales(yExtra) {
    var y = {
      beginAtZero: true,
      border: { display: false },
      grid: { color: COLORS.grid, drawTicks: false, lineWidth: 1 },
      ticks: { precision: 0, padding: 8 }
    };
    Object.keys(yExtra || {}).forEach(function (k) { y[k] = yExtra[k]; });
    return {
      x: {
        grid: { display: false },
        border: { color: COLORS.axis },
        ticks: { maxRotation: 0, autoSkip: true, autoSkipPadding: 16, padding: 6 }
      },
      y: y
    };
  }

  function baseOptions(extra) {
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false, axis: "x" },
      plugins: { legend: { display: false } },
      layout: { padding: { top: 8, right: 8 } }
    };
    Object.keys(extra).forEach(function (k) {
      if (k === "plugins") {
        Object.keys(extra.plugins).forEach(function (p) { options.plugins[p] = extra.plugins[p]; });
      } else {
        options[k] = extra[k];
      }
    });
    return options;
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  /* ---------------- 1. Requests per day (columns) ---------------- */
  var requestsCanvas = document.getElementById("chart-requests");
  if (requestsCanvas) {
    new Chart(requestsCanvas, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [{
          label: "Requests",
          data: data.requests,
          backgroundColor: COLORS.requests,
          hoverBackgroundColor: COLORS.requestsHover,
          borderRadius: 4,
          borderSkipped: "start",
          maxBarThickness: 24,
          barPercentage: 0.75,
          categoryPercentage: 0.9
        }]
      },
      options: baseOptions({
        scales: timeScales(),
        plugins: {
          tooltip: tooltip({
            callbacks: {
              label: function (item) {
                var i = item.dataIndex;
                var text = plural(item.raw, "request");
                return data.emergencies[i] ? text + " · " + plural(data.emergencies[i], "SOS") : text;
              }
            }
          })
        }
      })
    });
  }

  /* ---------------- 2. Emergencies over time (line + light area) ---------------- */
  var emergencyCanvas = document.getElementById("chart-emergencies");
  if (emergencyCanvas) {
    var maxSos = Math.max.apply(null, data.emergencies);
    new Chart(emergencyCanvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          label: "SOS alerts",
          data: data.emergencies,
          borderColor: COLORS.emergency,
          backgroundColor: withAlpha(COLORS.emergency, 0.1),
          borderWidth: 2,
          borderJoinStyle: "round",
          borderCapStyle: "round",
          fill: "origin",
          tension: 0,
          // Only days with an SOS get a marker, so the eye goes to them.
          pointRadius: function (ctx) { return ctx.raw > 0 ? 4 : 0; },
          pointHoverRadius: 5,
          pointBackgroundColor: COLORS.emergency,
          pointBorderColor: COLORS.surface,
          pointBorderWidth: 2,
          pointHitRadius: 12
        }]
      },
      options: baseOptions({
        scales: timeScales({ suggestedMax: Math.max(3, maxSos + 1), ticks: { precision: 0, stepSize: 1, padding: 8 } }),
        plugins: {
          gaCrosshair: { enabled: true },
          tooltip: tooltip({
            callbacks: { label: function (item) { return plural(item.raw, "SOS alert"); } }
          })
        }
      }),
      plugins: [crosshair]
    });
  }

  /* ---------------- 3. Pain level trend (line, 0–10) ---------------- */
  var painCanvas = document.getElementById("chart-pain");
  if (painCanvas) {
    new Chart(painCanvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          label: "Average pain",
          data: data.painAvg,
          borderColor: COLORS.pain,
          backgroundColor: COLORS.pain,
          borderWidth: 2,
          borderJoinStyle: "round",
          borderCapStyle: "round",
          spanGaps: true,
          tension: 0,
          pointRadius: 4,
          pointHoverRadius: 5,
          pointBackgroundColor: COLORS.pain,
          pointBorderColor: COLORS.surface,
          pointBorderWidth: 2,
          pointHitRadius: 12
        }]
      },
      options: baseOptions({
        scales: timeScales({ min: 0, max: 10, ticks: { stepSize: 2, padding: 8 } }),
        plugins: {
          gaCrosshair: { enabled: true },
          tooltip: tooltip({
            filter: function (item) { return item.raw !== null; },
            callbacks: {
              label: function (item) {
                var n = data.painCount[item.dataIndex];
                return "Pain " + item.raw + " / 10 · " + plural(n, "report");
              }
            }
          })
        }
      }),
      plugins: [crosshair]
    });
  }

  /* ---------------- 4. Most-used phrases (horizontal bars) ---------------- */
  var phrasesCanvas = document.getElementById("chart-phrases");
  if (phrasesCanvas && data.phrases.length) {
    // Height follows the number of bars so each row keeps the same thickness.
    phrasesCanvas.parentElement.style.height = (data.phrases.length * 38 + 16) + "px";
    var maxCount = Math.max.apply(null, data.phraseCounts);
    // Shorten labels to fit the card (the full phrase is in the tooltip and table).
    var maxChars = phrasesCanvas.parentElement.clientWidth < 480 ? 16 : 30;
    new Chart(phrasesCanvas, {
      type: "bar",
      data: {
        labels: data.phrases.map(function (p) { return p.length > maxChars ? p.slice(0, maxChars - 1) + "…" : p; }),
        datasets: [{
          label: "Times selected",
          data: data.phraseCounts,
          backgroundColor: COLORS.requests,
          hoverBackgroundColor: COLORS.requestsHover,
          borderRadius: 4,
          borderSkipped: "start",
          maxBarThickness: 20,
          barPercentage: 0.8,
          categoryPercentage: 0.9
        }]
      },
      options: baseOptions({
        indexAxis: "y",
        interaction: { mode: "index", intersect: false, axis: "y" },
        layout: { padding: { right: 32 } },
        scales: {
          x: { display: false, beginAtZero: true, suggestedMax: maxCount * 1.08 },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: COLORS.ink, font: { size: 13 }, padding: 8 }
          }
        },
        plugins: {
          gaBarValues: { enabled: true },
          tooltip: tooltip({
            callbacks: {
              title: function (items) { return data.phrases[items[0].dataIndex]; },
              label: function (item) { return "Selected " + (item.raw === 1 ? "once" : item.raw + " times"); }
            }
          })
        }
      }),
      plugins: [barValues]
    });
  }
})();
