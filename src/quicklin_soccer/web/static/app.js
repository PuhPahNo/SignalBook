const sports = [
  ["all", "All", "zap"],
  ["soccer", "Soccer", "circle"],
  ["hockey", "Hockey", "activity"],
  ["basketball", "Basketball", "target"],
  ["baseball", "Baseball", "diamond"],
  ["tennis", "Tennis", "circle"],
];

const state = { activeView: "signals", sport: "all", strategies: {}, jobs: [], calibrations: [] };
const endpoints = {
  signals: ["/api/signals", "signalsTable"],
  calibrations: ["/api/calibrations", "calibrationsTable"],
  snapshots: ["/api/snapshots", "snapshotsTable"],
  skips: ["/api/skips", "skipsTable"],
  settlements: ["/api/settlements", "settlementsTable"],
  performance: ["/api/performance", "performanceTable"],
  health: ["/api/provider-health", "healthTable"],
};

function boot() {
  decorateIcons();
  renderSportTabs();
  wireNavigation();
  wireActions();
  loadStrategyMetadata().then(() => {
    renderStrategyOptions();
    renderStrategyPanel();
    refresh();
  });
  setInterval(refresh, 5000);
}

function wireNavigation() {
  document.querySelectorAll(".nav button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll("section.view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.activeView = button.dataset.view;
      document.getElementById(state.activeView).classList.add("active");
    });
  });
}

function wireActions() {
  document.getElementById("botToggleBtn").addEventListener("click", toggleBot);
  document.getElementById("reportBtn").addEventListener("click", () => runJob("/api/report", { sport: state.sport }));
  document.getElementById("calibrateBtn").addEventListener("click", () => runSportJobs("/api/calibrate", (sport) => ({ sport })));
  document.getElementById("advancedBtn").addEventListener("click", () => {
    document.getElementById("advancedPanel").classList.toggle("open");
  });
  document.getElementById("strategy").addEventListener("change", renderStrategyPanel);
}

function renderSportTabs() {
  const target = document.getElementById("sportsTabs");
  target.innerHTML = sports.map(([key, label, iconName]) => (
    `<button class="${key === state.sport ? "active" : ""}" data-sport="${key}">${icon(iconName)}${escapeHtml(label)}</button>`
  )).join("");
  target.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      state.sport = button.dataset.sport;
      renderSportTabs();
      renderStrategyOptions();
      renderStrategyPanel();
      await refresh();
    });
  });
}

function renderStrategyOptions() {
  const select = document.getElementById("strategy");
  if (state.sport === "all") {
    select.innerHTML = '<option value="">Auto per sport</option>';
    return;
  }
  const sportMeta = state.strategies[state.sport] || {};
  const options = [["", `Auto: ${sportMeta.default || "strategy"}`]].concat(
    (sportMeta.strategies || []).map((item) => [item.id, item.label])
  );
  select.innerHTML = options
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");
}

function renderStrategyPanel() {
  const meta = selectedStrategy();
  if (!meta) return;
  const calibration = latestCalibrationForSport();
  const statusClass = statusBadgeClass(meta.status);
  const calibrationStatus = state.sport === "all" ? "cross-sport" : calibration ? calibration.status : "not run";
  const calibrationMetrics = calibrationSummary(calibration);
  const compact = `
    <div class="watch-strip">
      <span class="badge blue">${escapeHtml(labelForSport(state.sport))}</span>
      <strong>${escapeHtml(meta.label)}</strong>
      <span class="subtle">${escapeHtml(calibrationMetrics)}</span>
      <span class="badge ${statusBadgeClass(calibrationStatus)}">${escapeHtml(calibrationStatus)}</span>
    </div>
  `;
  const panel = `
    <div class="strategy-card">
      <div class="eyebrow">Selected model</div>
      <h2>${escapeHtml(meta.id)}</h2>
      <div class="subtle">${escapeHtml(meta.research)}</div>
      <div class="status-row">
        <span class="badge ${statusClass}">${escapeHtml(meta.status)}</span>
        <span class="badge blue">${escapeHtml(state.sport)}</span>
        <span class="badge ${statusBadgeClass(calibrationStatus)}">${escapeHtml(calibrationStatus)}</span>
      </div>
      <div class="calibration-note">
        <div class="eyebrow">Latest calibration</div>
        <div>${escapeHtml(calibrationMetrics)}</div>
      </div>
    </div>
    <div class="criteria-card">
      <div class="criteria-list">
        <div><div class="eyebrow">Inputs</div><ul>${meta.inputs.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>
        <div><div class="eyebrow">Signal gates</div><ul>${meta.gates.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>
        <div><div class="eyebrow">Calibration sources</div><ul>${(meta.sources || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>
      </div>
    </div>
  `;
  document.getElementById("strategyPanel").innerHTML = compact;
  document.getElementById("modelDetails").innerHTML = panel;
}

function latestCalibrationForSport() {
  if (state.sport === "all") return null;
  return (state.calibrations || []).find((item) => item.sport === state.sport);
}

function labelForSport(sport) {
  const item = sports.find(([key]) => key === sport);
  return item ? item[1] : sport;
}

function calibrationSummary(calibration) {
  if (state.sport === "all") return "Showing every open candidate across all sports.";
  if (!calibration) return "No calibration has run for this sport yet.";
  const metrics = calibration.metrics_json || {};
  const key = Object.keys(metrics).find((item) => item.startsWith("estimated_game_total_"))
    || Object.keys(metrics).find((item) => item.endsWith("_per_team_game"));
  const numeric = key ? Number(metrics[key]) : NaN;
  const metric = key ? `${key.replaceAll("_", " ")}: ${Number.isFinite(numeric) ? numeric.toFixed(3) : metrics[key]}` : "No numeric baseline stored.";
  return `${calibration.source} ${calibration.season || ""} | ${calibration.sample_size} rows | ${metric}`;
}

function selectedStrategy() {
  if (state.sport === "all") {
    return {
      id: "all_sports_watchlist",
      label: "All Sports Watchlist",
      status: "watchlist",
      research: "Shows every open cross-sport candidate. Each card is generated by that sport's default strategy and latest stored calibration baseline.",
      inputs: ["live AIScore rows", "sport-specific score state", "available totals line", "offered odds"],
      gates: ["open signal", "supported totals market", "positive model edge", "paper-trading settlement path"],
      sources: ["AIScore live pages", "stored sport calibrations"],
    };
  }
  const sportMeta = state.strategies[state.sport] || {};
  const strategyId = document.getElementById("strategy").value || sportMeta.default;
  return (sportMeta.strategies || []).find((item) => item.id === strategyId);
}

function jobPayload(sport = state.sport) {
  return {
    sport,
    strategy: state.sport === "all" ? "" : document.getElementById("strategy").value,
    limit: numberValue("limit", 10),
    min_ev: numberValue("minEv", 0.03),
  };
}

function botPayload() {
  return {
    sports: "all",
    limit: numberValue("limit", 10),
    min_ev: numberValue("minEv", 0.03),
    interval_seconds: numberValue("intervalSeconds", 60),
    settle_interval_seconds: 300,
  };
}

function numberValue(id, fallback) {
  const parsed = Number(document.getElementById(id).value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

async function runJob(url, body) {
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  document.querySelector('[data-view="jobs"]').click();
  await refresh();
}

async function toggleBot() {
  if (runningBot()) {
    await fetch("/api/bot/cancel", { method: "POST" });
  } else {
    await fetch("/api/bot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(botPayload()),
    });
  }
  await refresh();
}

async function runSportJobs(url, payloadForSport = (sport) => jobPayload(sport)) {
  await Promise.all(targetSports().map((sport) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadForSport(sport)),
  })));
  document.querySelector('[data-view="jobs"]').click();
  await refresh();
}

function targetSports() {
  if (state.sport !== "all") return [state.sport];
  return sports.map(([key]) => key).filter((key) => key !== "all");
}

async function cancelJob(jobId) {
  await fetch("/api/jobs/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId }),
  });
  await refresh();
}

async function refresh() {
  await Promise.all([loadSummary(), loadTables(), loadJobs(), loadCalibrations()]);
  renderStrategyPanel();
}

async function loadSummary() {
  const data = await getJson(withSport("/api/summary"));
  const summary = data.summary || {};
  const items = [
    ["Matches", summary.matches || 0],
    ["Snapshots", summary.snapshots || 0],
    ["Signals", summary.signals || 0],
    ["Open", summary.open_signals || 0],
    ["Settled", summary.settled_signals || 0],
    ["Skips", summary.skips || 0],
  ];
  document.getElementById("summary").innerHTML = items.map(([label, value]) => (
    `<div class="metric"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>`
  )).join("");
  document.getElementById("interpretation").textContent = data.interpretation || "No scanner state yet.";
}

async function loadTables() {
  for (const [key, [url, target]] of Object.entries(endpoints)) {
    const data = await getJson(key === "calibrations" || key === "signals" ? url : withSport(url));
    document.getElementById(target).innerHTML = table(data.rows || [], key);
  }
}

async function loadJobs() {
  const data = await getJson("/api/jobs");
  const jobs = data.jobs || [];
  state.jobs = jobs;
  updateBotButton();
  const target = document.getElementById("jobsList");
  if (!jobs.length) {
    target.innerHTML = '<div class="job subtle">The bot has not started yet.</div>';
    return;
  }
  target.innerHTML = jobs.map((job) => `
    <div class="job">
      <div><span class="badge ${job.status === "ok" ? "green" : job.status === "failed" ? "red" : "blue"}">${escapeHtml(job.status)}</span>
      <strong>${escapeHtml(job.kind)}</strong> #${escapeHtml(job.id)} <span class="subtle">${escapeHtml(new Date(job.started_at * 1000).toLocaleString())}</span>
      ${job.status === "running" ? `<button class="danger mini" data-cancel-job="${escapeHtml(job.id)}">${icon("square")}Stop</button>` : ""}</div>
      ${job.stdout ? `<pre>${escapeHtml(job.stdout)}</pre>` : ""}
      ${job.stderr ? `<pre>${escapeHtml(job.stderr)}</pre>` : ""}
    </div>
  `).join("");
  target.querySelectorAll("[data-cancel-job]").forEach((button) => {
    button.addEventListener("click", () => cancelJob(Number(button.dataset.cancelJob)));
  });
}

function runningBot() {
  return state.jobs.find((job) => job.kind === "bot" && job.status === "running");
}

function updateBotButton() {
  const button = document.getElementById("botToggleBtn");
  if (!button) return;
  const running = runningBot();
  button.className = running ? "danger" : "primary";
  button.innerHTML = running ? `${icon("square")}Stop Bot` : `${icon("repeat")}Run Bot`;
}

async function loadCalibrations() {
  const data = await getJson("/api/calibrations");
  state.calibrations = data.rows || [];
}

async function loadStrategyMetadata() {
  const data = await getJson("/api/strategies");
  state.strategies = data.sports || {};
}

function table(rows, kind) {
  if (kind === "signals") return betSlip(rows);
  if (!rows.length) return '<div style="padding:14px" class="subtle">No rows yet.</div>';
  const columns = columnsFor(kind, rows[0]);
  return `<table><thead><tr>${columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("")}</tr></thead><tbody>`
    + rows.map((row) => `<tr>${columns.map((col) => `<td class="${col.wrap ? "wrap" : ""}">${formatCell(row[col.key], col)}</td>`).join("")}</tr>`).join("")
    + "</tbody></table>";
}

function betSlip(rows) {
  const openRows = rows.filter((row) => row.status === "open");
  if (!openRows.length) {
    return '<div class="empty-slip"><strong>No open bet candidates.</strong><span>The SignalBook bot keeps scanning and will drop cards here when a signal clears the model gates.</span></div>';
  }
  return `
    <div class="slip-board">
      ${openRows.map((row) => {
        const isEstimated = row.odds_source && row.odds_source !== "market";
        const estTag = isEstimated
          ? `<span class="badge estimated-badge" title="Odds were estimated by SignalBook because the bookmaker did not post a two-sided total. ${escapeHtml(row.odds_source)}, confidence ${escapeHtml(formatPercent(row.odds_confidence))}.">[ESTIMATED] ${escapeHtml(formatPercent(row.odds_confidence))}</span>`
          : "";
        return `
        <article class="bet-card ${row.side === "over" ? "over-card" : "under-card"} ${isEstimated ? "estimated-card" : ""}">
          <div class="bet-top">
            <span class="badge ${badgeClass(row.sport)}">${escapeHtml(row.sport)}</span>
            <span class="badge ${badgeClass(row.side)}">${escapeHtml(row.side)}</span>
            ${estTag}
            <span class="freshness">${escapeHtml(relativeAge(row.created_at))}</span>
          </div>
          <div class="bet-pick">${escapeHtml(row.side.toUpperCase())} ${escapeHtml(row.line)}</div>
          <div class="bet-match">${escapeHtml(row.match)}</div>
          <div class="bet-grid">
            <div><span>Odds${isEstimated ? " (est)" : ""}</span><strong>${escapeHtml(formatNumber(row.offered_odds, 2))}</strong></div>
            <div><span>Stake</span><strong>${escapeHtml(formatNumber(row.stake_units, 2))}u</strong></div>
            <div><span>Model fair</span><strong>${escapeHtml(formatNumber(row.fair_odds, 2))}</strong></div>
            <div><span>Edge</span><strong>${escapeHtml(formatPercent(row.ev))}</strong></div>
          </div>
          <div class="bet-meta">
            <span>${escapeHtml(row.strategy_version)}</span>
            <span>confidence ${escapeHtml(formatPercent(row.confidence))}</span>
            ${isEstimated ? `<span class="estimated-source">${escapeHtml(row.odds_source)}</span>` : ""}
          </div>
          <a class="bet-link" href="${escapeHtml(row.url)}" target="_blank" rel="noreferrer">${icon("external")}Open AIScore</a>
        </article>
      `;
      }).join("")}
    </div>
    <div class="slip-note">Paper-trading candidates only. Check the live book line/odds before acting; odds move fast.</div>
  `;
}

function columnsFor(kind, sample) {
  const fallback = Object.keys(sample).slice(0, 10).map((key) => ({ key, label: key }));
  const map = {
    signals: [
      { key: "sport", label: "Sport", badge: true },
      { key: "created_at", label: "Time" },
      { key: "match", label: "Match", wrap: true },
      { key: "side", label: "Side", badge: true },
      { key: "line", label: "Line" },
      { key: "offered_odds", label: "Odds" },
      { key: "fair_odds", label: "Fair" },
      { key: "ev", label: "EV", pct: true },
      { key: "confidence", label: "Conf", pct: true },
      { key: "odds_source", label: "Odds source", badge: true },
      { key: "odds_confidence", label: "Est. conf", pct: true },
      { key: "status", label: "Status", badge: true },
    ],
    snapshots: [
      { key: "sport", label: "Sport", badge: true },
      { key: "captured_at", label: "Time" },
      { key: "match", label: "Match", wrap: true },
      { key: "phase", label: "Phase" },
      { key: "clock", label: "Clock" },
      { key: "minute", label: "Min" },
      { key: "score", label: "Score" },
      { key: "shots_on_target", label: "SOT" },
      { key: "dangerous_attacks", label: "Danger" },
      { key: "corners", label: "Corners" },
    ],
    skips: [
      { key: "sport", label: "Sport", badge: true },
      { key: "created_at", label: "Time" },
      { key: "match", label: "Match", wrap: true },
      { key: "strategy_version", label: "Strategy" },
      { key: "reason", label: "Reason", badge: true },
      { key: "details_json", label: "Details", wrap: true },
    ],
    settlements: [
      { key: "sport", label: "Sport", badge: true },
      { key: "settled_at", label: "Time" },
      { key: "match", label: "Match", wrap: true },
      { key: "side", label: "Side" },
      { key: "line", label: "Line" },
      { key: "final_score", label: "Final" },
      { key: "result", label: "Result", badge: true },
      { key: "payout_units", label: "Payout" },
    ],
    performance: [
      { key: "sport", label: "Sport", badge: true },
      { key: "strategy_version", label: "Strategy", wrap: true },
      { key: "signals", label: "Signals" },
      { key: "open_signals", label: "Open" },
      { key: "settled", label: "Settled" },
      { key: "wins", label: "Wins" },
      { key: "pushes", label: "Pushes" },
      { key: "losses", label: "Losses" },
      { key: "hit_rate", label: "Hit", pct: true },
      { key: "profit_units", label: "Profit" },
      { key: "roi", label: "ROI", pct: true },
      { key: "avg_model_ev", label: "Avg EV", pct: true },
      { key: "avg_confidence", label: "Conf", pct: true },
    ],
    health: [
      { key: "sport", label: "Sport", badge: true },
      { key: "checked_at", label: "Time" },
      { key: "provider", label: "Provider" },
      { key: "ok", label: "OK", badge: true },
      { key: "message", label: "Message", wrap: true },
      { key: "latency_ms", label: "Latency" },
    ],
    calibrations: [
      { key: "sport", label: "Sport", badge: true },
      { key: "created_at", label: "Time" },
      { key: "strategy_version", label: "Strategy" },
      { key: "status", label: "Status", badge: true },
      { key: "sample_size", label: "Rows" },
      { key: "source", label: "Source", wrap: true },
      { key: "season", label: "Season" },
      { key: "metrics_json", label: "Metrics", wrap: true },
      { key: "notes", label: "Notes", wrap: true },
    ],
  };
  return map[kind] || fallback;
}

function formatCell(value, col) {
  if (value === null || value === undefined) return "";
  let rendered = value;
  if (typeof value === "object") rendered = JSON.stringify(value);
  if (typeof rendered === "number" && col.pct) rendered = `${(rendered * 100).toFixed(1)}%`;
  else if (typeof rendered === "number") rendered = Number.isInteger(rendered) ? rendered : rendered.toFixed(3);
  if (col.badge) {
    return `<span class="badge ${badgeClass(String(rendered))}">${escapeHtml(rendered)}</span>`;
  }
  return escapeHtml(rendered);
}

function formatNumber(value, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "";
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "";
}

function relativeAge(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function badgeClass(value) {
  const lower = value.toLowerCase();
  if (["ok", "open", "over", "win", "half_win", "1", "true", "soccer"].includes(lower)) return "green";
  if (["failed", "loss", "half_loss", "0", "false"].includes(lower)) return "red";
  if (lower.includes("calibrated")) return "green";
  if (lower.includes("skip") || lower.includes("unavailable") || lower.includes("missing") || lower.includes("required") || lower.includes("manual") || lower.includes("not run")) return "amber";
  return "blue";
}

function statusBadgeClass(value) {
  return badgeClass(String(value || ""));
}

function withSport(url) {
  if (state.sport === "all") return url;
  return `${url}?sport=${encodeURIComponent(state.sport)}`;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

function decorateIcons() {
  document.querySelectorAll("[data-icon]").forEach((node) => {
    node.insertAdjacentHTML("afterbegin", icon(node.dataset.icon));
  });
}

function icon(name) {
  const paths = {
    activity: '<polyline points="3 12 7 12 9 5 13 19 15 12 21 12"/>',
    coins: '<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/><path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
    circle: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/>',
    diamond: '<path d="M12 3l9 9-9 9-9-9 9-9z"/><path d="M12 3v18M3 12h18"/>',
    chart: '<path d="M4 19V5"/><path d="M4 19h17"/><rect x="7" y="11" width="3" height="5"/><rect x="12" y="8" width="3" height="8"/><rect x="17" y="6" width="3" height="10"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M8 13h8M8 17h8"/>',
    external: '<path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M11 5H5v14h14v-6"/>',
    filter: '<path d="M4 5h16l-6 7v5l-4 2v-7z"/>',
    gauge: '<path d="M4 14a8 8 0 1 1 16 0"/><path d="M12 14l4-5"/><path d="M8 18h8"/>',
    play: '<path d="M8 5v14l11-7z"/>',
    pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    repeat: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
    terminal: '<path d="M4 7l5 5-5 5"/><path d="M11 17h9"/>',
    square: '<rect x="7" y="7" width="10" height="10" rx="1"/>',
    zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.circle}</svg>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot();
