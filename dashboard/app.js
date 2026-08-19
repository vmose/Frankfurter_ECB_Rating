/**
 * app.js — Fixing Board dashboard
 *
 * Reads two static JSON files (written by ingestion/export_marts.py from
 * the dbt marts as the last step of deploy.yml):
 *
 *   data/latest.json   — one row per currency pair, most recent rate + delta
 *   data/history.json  — { "BASE/QUOTE": [{date, rate}, ...], ... }
 *   data/quality.json  — { freshness, volume, reconciliation, schema_drift, checked_at }
 *
 * No BigQuery calls happen from the browser — the dashboard is a static
 * site, kept deliberately dumb. All the real work happens upstream in
 * the pipeline.
 */

const DATA_PATHS = {
  latest: "data/latest.json",
  history: "data/history.json",
  quality: "data/quality.json",
};

const state = {
  latest: [],
  history: {},
  selectedPair: null,
};

async function loadJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} responded ${res.status}`);
  return res.json();
}

function fmtRate(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(n < 10 ? 4 : 2);
}

function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "flat";
  const sign = n > 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(2)}%`;
}

function deltaDirection(n) {
  if (n === null || n === undefined || Number.isNaN(n) || Math.abs(n) < 0.0001) return "flat";
  return n > 0 ? "up" : "down";
}

function deltaArrow(dir) {
  return { up: "▲", down: "▼", flat: "—" }[dir];
}

/* ---------------- Empty / error state ---------------- */

function renderEmptyState(board, message) {
  board.innerHTML = "";
  const note = document.createElement("div");
  note.style.gridColumn = "1 / -1";
  note.style.padding = "2.5rem 1rem";
  note.style.color = "var(--paper-dim)";
  note.style.fontFamily = "var(--font-mono)";
  note.style.fontSize = "0.85rem";
  note.textContent = message;
  board.appendChild(note);
}

/* ---------------- Board rendering ---------------- */

function renderBoard(rows) {
  const board = document.getElementById("board");
  board.innerHTML = "";

  if (!rows.length) {
    renderEmptyState(
      board,
      "No rates loaded yet. Run the ingestion → dbt → deploy pipeline, or see README.md to run it locally."
    );
    return;
  }

  const sorted = [...rows].sort((a, b) => a.pair.localeCompare(b.pair));

  for (const row of sorted) {
    const cell = document.createElement("button");
    cell.className = "flap-cell";
    cell.type = "button";
    cell.setAttribute("aria-label", `${row.pair}, rate ${fmtRate(row.rate)}, ${fmtPct(row.pct_change)}`);

    const dir = deltaDirection(row.pct_change);

    cell.innerHTML = `
      <span class="flap-cell__pair">${row.pair}</span>
      <span class="flap-cell__rate">${fmtRate(row.rate)}</span>
      <span class="flap-cell__delta" data-dir="${dir}">
        <span class="flap-cell__delta-arrow">${deltaArrow(dir)}</span>
        <span>${fmtPct(row.pct_change)}</span>
      </span>
    `;

    cell.addEventListener("click", () => selectPair(row.pair));
    board.appendChild(cell);
  }
}

function updateMeta(rows, qualityStatus) {
  const asOfEl = document.getElementById("as-of-date");
  const countEl = document.getElementById("pair-count");
  const qualityEl = document.getElementById("quality-status");

  if (rows.length) {
    const mostRecent = rows.reduce((max, r) => (r.as_of_date > max ? r.as_of_date : max), rows[0].as_of_date);
    asOfEl.textContent = `as of ${mostRecent}`;
    countEl.textContent = `${rows.length} pair${rows.length === 1 ? "" : "s"}`;
  } else {
    asOfEl.textContent = "as of —";
    countEl.textContent = "0 pairs";
  }

  if (qualityStatus) {
    qualityEl.dataset.state = qualityStatus.state;
    qualityEl.textContent = qualityStatus.label;
  } else {
    qualityEl.dataset.state = "unknown";
    qualityEl.textContent = "quality: unavailable";
  }
}

function deriveQualityStatus(quality) {
  if (!quality) return null;
  const checks = ["freshness", "volume", "reconciliation", "schema_drift"];
  const values = checks.map((k) => quality[k]).filter((v) => v !== undefined);
  if (!values.length) return null;

  if (values.includes("fail")) return { state: "fail", label: "quality: failing" };
  if (values.includes("warn")) return { state: "warn", label: "quality: warning" };
  return { state: "pass", label: "quality: passing" };
}

/* ---------------- Detail panel + D3 chart ---------------- */

function selectPair(pair) {
  state.selectedPair = pair;
  const detail = document.getElementById("detail");
  const title = document.getElementById("detail-title");
  detail.hidden = false;
  title.textContent = pair;

  const series = state.history[pair] || [];
  drawChart(series);
  renderDetailStats(pair, series);

  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderDetailStats(pair, series) {
  const statsEl = document.getElementById("detail-stats");
  if (!series.length) {
    statsEl.innerHTML = `<div>No history loaded for ${pair} yet.</div>`;
    return;
  }

  const rates = series.map((d) => d.rate);
  const min = Math.min(...rates);
  const max = Math.max(...rates);
  const first = series[0];
  const last = series[series.length - 1];
  const totalChange = (last.rate - first.rate) / first.rate;

  statsEl.innerHTML = `
    <div><strong>${fmtRate(last.rate)}</strong>latest</div>
    <div><strong>${fmtRate(min)}</strong>period low</div>
    <div><strong>${fmtRate(max)}</strong>period high</div>
    <div><strong>${fmtPct(totalChange)}</strong>since ${first.date}</div>
    <div><strong>${series.length}</strong>observations</div>
  `;
}

function drawChart(series) {
  const svg = d3.select("#detail-chart");
  svg.selectAll("*").remove();

  if (!series.length) {
    svg
      .append("text")
      .attr("x", 360)
      .attr("y", 160)
      .attr("text-anchor", "middle")
      .attr("fill", "var(--paper-dim)")
      .attr("font-family", "var(--font-mono)")
      .attr("font-size", "13px")
      .text("No history available for this pair yet.");
    return;
  }

  const margin = { top: 16, right: 24, bottom: 28, left: 56 };
  const width = 720 - margin.left - margin.right;
  const height = 320 - margin.top - margin.bottom;

  const parsed = series.map((d) => ({ date: new Date(d.date), rate: d.rate }));

  const x = d3
    .scaleTime()
    .domain(d3.extent(parsed, (d) => d.date))
    .range([0, width]);

  const y = d3
    .scaleLinear()
    .domain([d3.min(parsed, (d) => d.rate) * 0.995, d3.max(parsed, (d) => d.rate) * 1.005])
    .range([height, 0]);

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const xAxis = d3.axisBottom(x).ticks(6).tickSizeOuter(0);
  const yAxis = d3.axisLeft(y).ticks(5).tickSizeOuter(0);

  g.append("g")
    .attr("class", "chart-axis")
    .attr("transform", `translate(0,${height})`)
    .call(xAxis);

  g.append("g").attr("class", "chart-axis").call(yAxis);

  const area = d3
    .area()
    .x((d) => x(d.date))
    .y0(height)
    .y1((d) => y(d.rate))
    .curve(d3.curveMonotoneX);

  const line = d3
    .line()
    .x((d) => x(d.date))
    .y((d) => y(d.rate))
    .curve(d3.curveMonotoneX);

  g.append("path").datum(parsed).attr("class", "chart-area").attr("d", area);
  g.append("path").datum(parsed).attr("class", "chart-line").attr("d", line);

  const last = parsed[parsed.length - 1];
  g.append("circle")
    .attr("class", "chart-dot")
    .attr("cx", x(last.date))
    .attr("cy", y(last.rate))
    .attr("r", 4);
}

function closeDetail() {
  document.getElementById("detail").hidden = true;
  state.selectedPair = null;
}

/* ---------------- Init ---------------- */

async function init() {
  document.getElementById("detail-close").addEventListener("click", closeDetail);

  let latest = [];
  let history = {};
  let quality = null;

  try {
    latest = await loadJSON(DATA_PATHS.latest);
  } catch (err) {
    console.warn("Could not load latest.json:", err);
  }

  try {
    history = await loadJSON(DATA_PATHS.history);
  } catch (err) {
    console.warn("Could not load history.json:", err);
  }

  try {
    quality = await loadJSON(DATA_PATHS.quality);
  } catch (err) {
    console.warn("Could not load quality.json:", err);
  }

  state.latest = latest;
  state.history = history;

  renderBoard(latest);
  updateMeta(latest, deriveQualityStatus(quality));
}

document.addEventListener("DOMContentLoaded", init);
