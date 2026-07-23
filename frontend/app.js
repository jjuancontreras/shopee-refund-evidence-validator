const POLL_INTERVAL_MS = 1500;
// Adjust if the Streamlit reviewer runs on a different host/port.
const STREAMLIT_BASE_URL = "http://localhost:8501";

const STAGES = [
  "Fetching evidence",
  "Preprocessing",
  "Quality check",
  "Running inspectors",
  "Aggregating & deciding",
  "Report ready",
];

const connectionEl = document.getElementById("connection-status");
const currentContentEl = document.getElementById("current-content");
const queueListEl = document.getElementById("queue-list");
const historyBodyEl = document.getElementById("history-body");

function badgeClass(value) {
  const map = {
    Valid: "badge-valid",
    Invalid: "badge-invalid",
    "Needs Human Review": "badge-review",
    completed: "badge-completed",
    failed: "badge-failed",
  };
  return map[value] || "badge-unknown";
}

function reportLink(orderId) {
  return `${STREAMLIT_BASE_URL}/?order_id=${encodeURIComponent(orderId)}`;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function relativeTime(isoString) {
  if (!isoString) return "—";
  const then = new Date(isoString).getTime();
  const now = Date.now();
  const diffSec = Math.max(0, Math.round((now - then) / 1000));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  return `${diffHr}h ago`;
}

function elapsedSince(isoString) {
  const then = new Date(isoString).getTime();
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  const m = Math.floor(diffSec / 60);
  const s = diffSec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function stageBaseIndex(activeStage) {
  // Stages can carry live progress detail beyond the fixed label (e.g.
  // "Running inspectors (2/4 done)"), so match by prefix rather than exact
  // equality.
  for (let i = 0; i < STAGES.length; i++) {
    if (activeStage === STAGES[i] || activeStage.startsWith(STAGES[i])) return i;
  }
  return -1;
}

function renderStageStepper(activeStage) {
  const activeIndex = stageBaseIndex(activeStage);
  return STAGES.map((stage, i) => {
    let cls = "stage-step";
    if (activeIndex === -1) {
      cls += i === 0 ? " active" : "";
    } else if (i < activeIndex) {
      cls += " done";
    } else if (i === activeIndex) {
      cls += " active";
    }
    // Show the live detail (e.g. the "(2/4 done)" suffix) on the active
    // step itself instead of the fixed label, so progress within a stage
    // is actually visible rather than a static-looking pill.
    const label = i === activeIndex ? activeStage : stage;
    return `<span class="${cls}">${escapeHtml(label)}</span>`;
  }).join("");
}

function renderCurrent(current) {
  if (!current) {
    currentContentEl.className = "idle";
    currentContentEl.innerHTML = "Idle — no order processing.";
    return;
  }
  currentContentEl.className = "";
  currentContentEl.innerHTML = `
    <div class="current-order">
      <span class="current-order-id">${escapeHtml(current.order_id)}</span>
      <span class="current-meta">
        ${current.return_reason ? escapeHtml(current.return_reason) + " · " : ""}running ${elapsedSince(current.started_at)}
      </span>
    </div>
    <div class="stage-stepper">${renderStageStepper(current.stage)}</div>
  `;
}

function renderQueue(queue) {
  if (!queue || queue.length === 0) {
    queueListEl.innerHTML = '<li class="empty">Nothing waiting.</li>';
    return;
  }
  queueListEl.innerHTML = queue
    .map(
      (item) => `
      <li>
        <span class="position">${item.position}</span>
        <span>${escapeHtml(item.order_id)}</span>
      </li>`
    )
    .join("");
}

function renderHistory(history) {
  if (!history || history.length === 0) {
    historyBodyEl.innerHTML = '<tr><td colspan="5" class="empty">No runs yet.</td></tr>';
    return;
  }
  historyBodyEl.innerHTML = history
    .map((row) => {
      const badgeValue = row.status === "completed" ? row.classification || "completed" : "failed";
      const confidence = row.confidence != null ? `${Math.round(row.confidence * 100)}%` : "—";
      const errorLine = row.error_message
        ? `<span class="error-message">${escapeHtml(row.error_message)}</span>`
        : "";
      return `
        <tr>
          <td>${escapeHtml(row.order_id)}</td>
          <td><span class="badge ${badgeClass(badgeValue)}">${escapeHtml(badgeValue)}</span>${errorLine}</td>
          <td>${confidence}</td>
          <td>${relativeTime(row.finished_at)}</td>
          <td>${
            row.status === "completed"
              ? `<a class="report-link" href="${reportLink(row.order_id)}" target="_blank" rel="noopener">View full report →</a>`
              : ""
          }</td>
        </tr>`;
    })
    .join("");
}

let lastData = { current: null, queue: [], history: [] };
let pollTimer = null;

function renderAll(data) {
  lastData = data;
  renderCurrent(data.current);
  renderQueue(data.queue);
  renderHistory(data.history);
}

async function poll() {
  // A poll can be triggered both by the timer and by the tab regaining
  // focus (see below) -- cancel any pending timer-driven call so they
  // don't stack up into overlapping fetches.
  if (pollTimer) clearTimeout(pollTimer);

  try {
    const res = await fetch("/api/queue-status", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    connectionEl.textContent = "Live";
    renderAll(data);
  } catch (err) {
    connectionEl.textContent = "Disconnected — retrying…";
    console.error("poll failed", err);
  } finally {
    pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
  }
}

// Browsers throttle timers heavily in background tabs (to save battery),
// so a tab left open while you work elsewhere can sit on stale data far
// longer than POLL_INTERVAL_MS -- force an immediate re-fetch the moment
// this tab becomes active again instead of waiting for a manual refresh.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") poll();
});
window.addEventListener("focus", poll);

// Ticks the elapsed-time / relative-time displays every second using
// already-fetched data (no network call) so "running Xs" and "Xs ago"
// visibly count up between polls instead of only jumping every 1.5s.
setInterval(() => {
  if (lastData.current) renderCurrent(lastData.current);
  renderHistory(lastData.history);
}, 1000);

document.addEventListener("DOMContentLoaded", poll);
