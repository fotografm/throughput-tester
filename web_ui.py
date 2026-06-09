#!/usr/bin/env python3
"""Rolling 24-hour web dashboard for ct152 → ct166 throughput tests."""

import sqlite3
import time

from flask import Flask, jsonify

from common import DB_PATH, load_config

app = Flask(__name__, static_folder=None)

TRANSPORTS = ["ygg", "rns", "i2p"]
WINDOW_HOURS = 24


def _node_labels():
    cfg = load_config()
    from_label = cfg["nodes"]["ct152"]["label"]
    to_label = cfg["nodes"]["ct166"]["label"]
    return from_label, to_label


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Throughput Tester</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
* { box-sizing: border-box; }
body {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 18px;
  background: #ffffff;
  color: #111111;
  margin: 0;
  padding: 20px 28px;
}
h1 { font-size: 32px; font-weight: 900; margin: 0 0 4px; }
.subtitle {
  font-size: 16px;
  color: #555;
  margin-bottom: 28px;
}
.legend {
  display: flex;
  gap: 28px;
  margin-bottom: 16px;
  font-size: 17px;
  font-weight: 700;
}
.dot {
  display: inline-block;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  margin-right: 7px;
  vertical-align: middle;
}
.chart-wrap { margin-bottom: 44px; }
h2 { font-size: 22px; font-weight: 700; margin: 0 0 8px; }
canvas { display: block; width: 100% !important; max-height: 300px; }
.no-data {
  font-size: 17px;
  color: #888;
  margin-top: 4px;
  height: 60px;
  display: flex;
  align-items: center;
}
</style>
</head>
<body>

<h1>Throughput Tester</h1>
<div class="subtitle">ct152 &rarr; ct166 &nbsp;&bull;&nbsp; rolling 24 hours &nbsp;&bull;&nbsp; auto-refreshes every 5 min</div>

<div class="legend">
  <span><span class="dot" style="background:#0055FF"></span>Yggdrasil</span>
  <span><span class="dot" style="background:#FF6600"></span>Reticulum</span>
  <span><span class="dot" style="background:#00AA00"></span>I2P</span>
</div>

<div class="chart-wrap">
  <h2>Upload Speed (Mbps)</h2>
  <canvas id="uploadChart" height="300"></canvas>
</div>
<div class="chart-wrap">
  <h2>Download Speed (Mbps)</h2>
  <canvas id="downloadChart" height="300"></canvas>
</div>
<div class="chart-wrap">
  <h2>Latency (ms)</h2>
  <canvas id="latencyChart" height="300"></canvas>
</div>

<script>
const COLORS = { ygg: '#0055FF', rns: '#FF6600', i2p: '#00AA00' };
const LABELS = { ygg: 'Yggdrasil', rns: 'Reticulum', i2p: 'I2P' };

const AXIS_FONT = { size: 15, weight: '600' };
const TICK_FONT = { size: 14 };

function makeChart(canvasId, yLabel) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: { datasets: [] },
    options: {
      animation: false,
      parsing: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: 'time',
          time: { unit: 'hour', displayFormats: { hour: 'HH:mm' }, tooltipFormat: 'yyyy-MM-dd HH:mm' },
          ticks: { font: TICK_FONT, maxTicksLimit: 12, color: '#222' },
          grid: { color: '#e8e8e8' }
        },
        y: {
          beginAtZero: true,
          title: { display: true, text: yLabel, font: AXIS_FONT, color: '#333' },
          ticks: { font: TICK_FONT, color: '#222' },
          grid: { color: '#e8e8e8' }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          bodyFont: { size: 15 },
          titleFont: { size: 15 },
          callbacks: {
            label: ctx => ' ' + LABELS[ctx.dataset._transport] + ': ' + ctx.parsed.y.toFixed(2)
          }
        }
      }
    }
  });
}

const charts = {
  upload:   makeChart('uploadChart',   'Mbps'),
  download: makeChart('downloadChart', 'Mbps'),
  latency:  makeChart('latencyChart',  'ms'),
};

const FIELDS = { upload: 'upload_mbps', download: 'download_mbps', latency: 'latency_avg_ms' };

fetch('/api/data').then(r => r.json()).then(data => {
  for (const [name, chart] of Object.entries(charts)) {
    const field = FIELDS[name];
    chart.data.datasets = Object.entries(data)
      .filter(([, rows]) => rows.length > 0)
      .map(([transport, rows]) => {
        const ds = {
          label: LABELS[transport] || transport,
          _transport: transport,
          data: rows.map(r => ({ x: r.t, y: r[field] })),
          borderColor: COLORS[transport] || '#888888',
          backgroundColor: 'transparent',
          borderWidth: 2.5,
          pointRadius: rows.length < 50 ? 5 : 3,
          pointHoverRadius: 7,
          tension: 0.15,
        };
        return ds;
      });
    chart.update();
  }
});
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return DASHBOARD_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/data")
def api_data():
    cutoff = time.time() - WINDOW_HOURS * 3600
    if not DB_PATH.exists():
        return jsonify({t: [] for t in TRANSPORTS})

    try:
        from_label, to_label = _node_labels()
    except Exception:
        from_label = to_label = None

    conn = sqlite3.connect(DB_PATH)
    out = {}
    for t in TRANSPORTS:
        try:
            if from_label and to_label:
                rows = conn.execute(
                    "SELECT timestamp, latency_avg_ms, upload_mbps, download_mbps "
                    "FROM results "
                    "WHERE transport=? AND timestamp>=? AND from_node=? AND to_node=? "
                    "ORDER BY timestamp ASC",
                    (t, cutoff, from_label, to_label),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT timestamp, latency_avg_ms, upload_mbps, download_mbps "
                    "FROM results "
                    "WHERE transport=? AND timestamp>=? "
                    "ORDER BY timestamp ASC",
                    (t, cutoff),
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        out[t] = [
            {"t": int(r[0] * 1000), "latency_avg_ms": r[1],
             "upload_mbps": r[2], "download_mbps": r[3]}
            for r in rows
        ]
    conn.close()
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
