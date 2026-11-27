/**
 * dashboard/static/js/dashboard.js
 * CareerPilot AI / Autohire.ai — Client Interactive Controller
 * Features:
 * - Real-time statistics & IST clock synchronization
 * - Dynamic tab navigation matching SaaS layout in reference_ui.jpg
 * - Chart.js 14-day activity visualizer with offline Canvas fallback
 * - High-speed paginated Job Feed with LLM match score pills
 * - Historical Applications management & status updater
 * - Mandatory Human Approval Queue (one-click Approve / Reject, Bulk Decisions)
 * - 1,831 PDF HR Contacts Drip Campaign progress meter
 * - 3-Tier Verified Skills Manager for Manoj Kumar Mandal
 * - Server-Sent Events (SSE) live telemetry terminal
 */
// ── Central CSRF Protection ──
const originalFetch = window.fetch.bind(window);

window.fetch = function (input, init = {}) {
  const method = (
    init.method ||
    (input instanceof Request ? input.method : "GET")
  ).toUpperCase();

  const safeMethods = ["GET", "HEAD", "OPTIONS", "TRACE"];

  if (safeMethods.includes(method)) {
    return originalFetch(input, init);
  }

  const csrfToken = document
    .querySelector('meta[name="csrf-token"]')
    ?.getAttribute("content");

  if (!csrfToken) {
    console.error("CSRF token missing. Request blocked.");
    return Promise.reject(new Error("CSRF token missing."));
  }

  const headers = new Headers(
    init.headers ||
    (input instanceof Request ? input.headers : undefined)
  );

  headers.set("X-CSRFToken", csrfToken);

  return originalFetch(input, {
    ...init,
    headers: headers
  });
};
// ── Application State ─────────────────────────────────────────────────────────
const state = {
  currentTab: "tab-dashboard",
  feedPage: 1,
  feedPortal: "all",
  feedSort: "score",
  feedSearch: "",
  feedTotalPages: 1,
  appsPage: 1,
  appsStatus: "all",
  appsPortal: "all",
  appsSearch: "",
  appsTotalPages: 1,
  chartInstance: null,
  sseSource: null,
  agentRunning: false
};

// ── Initialization on DOM Ready ──────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initClock();
  setupNavigation();
  setupKeyboardShortcuts();

  // Initial Data Fetching
  loadStats();
  loadDailyChart();
  loadPortalStats();
  loadJobFeed(1);
  loadApplications(1);
  loadApprovalQueue();
  loadDripStats();
  loadProfile();
  loadSettings();

  // Start real-time SSE stream
  initSSEStream();

  // Periodic polling for stats & queue (every 25 seconds)
  setInterval(loadStats, 25000);
  setInterval(loadApprovalQueue, 30000);
});

// ── Live IST Clock ───────────────────────────────────────────────────────────
function initClock() {
  function updateTime() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "Asia/Kolkata"
    });
    const el = document.getElementById("clockText");
    if (el) el.textContent = `${timeStr} IST`;
  }
  updateTime();
  setInterval(updateTime, 1000);
}

// ── Navigation & Tabs ────────────────────────────────────────────────────────
function setupNavigation() {
  document.querySelectorAll(".nav-link[data-tab]").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      const tabId = link.getAttribute("data-tab");
      switchTab(tabId);
    });
  });

  // Handle URL hash on load
  const hash = window.location.hash.replace("#", "");
  const hashToTab = {
    dashboard: "tab-dashboard",
    jobfeed: "tab-jobfeed",
    history: "tab-history",
    approvals: "tab-approvals",
    outreach: "tab-outreach",
    profile: "tab-profile",
    logs: "tab-logs",
    settings: "tab-settings"
  };
  if (hash && hashToTab[hash]) {
    switchTab(hashToTab[hash]);
  }
}

function switchTab(tabId) {
  state.currentTab = tabId;

  // Update nav link active state
  document.querySelectorAll(".nav-link").forEach(link => {
    if (link.getAttribute("data-tab") === tabId) {
      link.classList.add("active");
    } else {
      link.classList.remove("active");
    }
  });

  // Update tab pane active state
  document.querySelectorAll(".tab-pane").forEach(pane => {
    if (pane.id === tabId) {
      pane.classList.add("active");
    } else {
      pane.classList.remove("active");
    }
  });

  // Lazy refresh based on tab
  if (tabId === "tab-jobfeed") loadJobFeed(state.feedPage);
  if (tabId === "tab-history") loadApplications(state.appsPage);
  if (tabId === "tab-approvals") loadApprovalQueue();
  if (tabId === "tab-outreach") loadDripStats();
  if (tabId === "tab-profile") loadProfile();
  if (tabId === "tab-settings") loadSettings();
}

function setupKeyboardShortcuts() {
  window.addEventListener("keydown", e => {
    if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey) {
      const activeTag = document.activeElement ? document.activeElement.tagName : "";
      if (activeTag !== "INPUT" && activeTag !== "TEXTAREA" && activeTag !== "SELECT") {
        e.preventDefault();
        const searchInput = document.getElementById("globalSearchInput");
        if (searchInput) searchInput.focus();
      }
    }
  });

  const globalSearch = document.getElementById("globalSearchInput");
  if (globalSearch) {
    globalSearch.addEventListener("keyup", e => {
      if (e.key === "Enter") {
        const val = globalSearch.value.trim();
        state.feedSearch = val;
        switchTab("tab-jobfeed");
        loadJobFeed(1);
      }
    });
  }
}

// ── Stats Loader ─────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) return;
    const data = await res.json();
    if (data.status !== "success") return;

    // Animate / update metric cards
    setElementText("cardTotalApplied", (data.all_time.total_applied || 0).toLocaleString());
    setElementText("cardTodayApplied", `Today: ${data.today.applied || 0} applications`);

    setElementText("cardTotalEmailed", (data.all_time.total_emailed || 0).toLocaleString());
    setElementText("cardTodayEmailed", `Today: ${data.today.emailed || 0} sent`);

    const dripSent = data.drip.sent ?? 0;
    const dripTotal = data.drip.total_contacts ?? 0;
    setElementText("cardDripProgress", `${dripSent.toLocaleString()} / ${dripTotal.toLocaleString()}`);
    const dripPercent = dripTotal > 0 ? Math.round((dripSent / dripTotal) * 100) : 0;
    setElementText("cardDripPercentage", `${dripPercent}% Completed (50/day)`);
    setElementText("sidebarDripBadge", dripTotal.toLocaleString());

    const pendingCnt = data.approval_queue.pending ?? 0;
    setElementText("cardPendingApprovals", pendingCnt.toLocaleString());
    setElementText("sidebarApprovalBadge", pendingCnt);
    setElementText("bannerPendingCount", pendingCnt);

    setElementText("cardUniqueCompanies", (data.all_time.unique_companies ?? 0).toLocaleString());

    // Update banner counters strictly from live DB
    const totalSeen = data.all_time.total_jobs_seen ?? 0;
    setElementText("bannerScrapedCount", totalSeen > 0 ? `${totalSeen.toLocaleString()}+` : "0");
    setElementText("bannerDripCount", dripTotal.toLocaleString());

    // Update agent status indicator
    updateAgentStatusPill(data.system.agent_running);
  } catch (err) {
    console.warn("Failed to load dashboard statistics:", err);
  }
}

function updateAgentStatusPill(running) {
  state.agentRunning = running;
  const pill = document.getElementById("agentStatusPill");
  const text = document.getElementById("agentStatusText");
  const btnRun = document.getElementById("btnRunAgent");

  if (pill && text) {
    if (running) {
      pill.classList.add("running");
      text.textContent = "Running...";
      if (btnRun) btnRun.disabled = true;
    } else {
      pill.classList.remove("running");
      text.textContent = "Idle";
      if (btnRun) btnRun.disabled = false;
    }
  }
}

// ── Chart.js Activity Visualizer ─────────────────────────────────────────────
async function loadDailyChart() {
  try {
    const res = await fetch("/api/chart-data?days=14");
    if (!res.ok) return;
    const data = await res.json();
    const trends = data.trends || [];

    const labels = trends.map(t => {
      const parts = t.date.split("-");
      return parts.length === 3 ? `${parts[1]}/${parts[2]}` : t.date;
    });
    const appliedData = trends.map(t => t.total_applied || 0);
    const emailedData = trends.map(t => t.total_emailed || 0);
    const scrapedData = trends.map(t => t.total_scraped || 0);

    const canvas = document.getElementById("activityChart");
    if (!canvas) return;

    if (window.Chart) {
      if (state.chartInstance) {
        state.chartInstance.destroy();
      }

      const ctx = canvas.getContext("2d");
      state.chartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Applications",
              data: appliedData,
              borderColor: "#1A56DB",
              backgroundColor: "rgba(26, 86, 219, 0.08)",
              fill: true,
              tension: 0.35,
              borderWidth: 2.5,
              pointRadius: 3,
              pointHoverRadius: 6,
            },
            {
              label: "Cold Emails",
              data: emailedData,
              borderColor: "#8B5CF6",
              backgroundColor: "rgba(139, 92, 246, 0.05)",
              fill: true,
              tension: 0.35,
              borderWidth: 2,
              pointRadius: 3,
            },
            {
              label: "Scraped Jobs",
              data: scrapedData,
              borderColor: "#94A3B8",
              backgroundColor: "transparent",
              borderDash: [4, 4],
              borderWidth: 1.5,
              pointRadius: 0,
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "#0F172A",
              padding: 10,
              titleFont: { size: 12, weight: "bold" },
              bodyFont: { size: 12 }
            }
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: { font: { size: 11 }, color: "#94A3B8" }
            },
            y: {
              beginAtZero: true,
              grid: { color: "#F1F5F9" },
              ticks: { font: { size: 11 }, color: "#94A3B8" }
            }
          }
        }
      });
    } else {
      // Offline fallback simple canvas chart
      renderCanvasFallback(canvas, labels, appliedData, emailedData);
    }
  } catch (err) {
    console.warn("Chart data load failure:", err);
  }
}

function renderCanvasFallback(canvas, labels, appliedData, emailedData) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = "#F8FAFC";
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = "#1A56DB";
  ctx.font = "12px sans-serif";
  ctx.fillText("Activity Trend (Offline Renderer)", 20, 30);

  const maxVal = Math.max(...appliedData, ...emailedData, 10);
  const stepX = (w - 60) / Math.max(labels.length - 1, 1);

  ctx.strokeStyle = "#1A56DB";
  ctx.lineWidth = 2;
  ctx.beginPath();
  appliedData.forEach((val, i) => {
    const x = 30 + i * stepX;
    const y = h - 30 - (val / maxVal) * (h - 70);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// ── Portal Breakdown ─────────────────────────────────────────────────────────
async function loadPortalStats() {
  const container = document.getElementById("portalBreakdownList");
  if (!container) return;

  try {
    const res = await fetch("/api/portals");
    if (!res.ok) return;
    const data = await res.json();
    const portals = data.portals || [];

    if (portals.length === 0) {
      container.innerHTML = `<div class="text-muted text-center py-3">No portal data available.</div>`;
      return;
    }

    const totalCount = portals.reduce((acc, p) => acc + (p.count || 0), 0) || 1;
    const portalColors = {
      remoteok: "#1A56DB",
      weworkremotely: "#10B981",
      internshala: "#8B5CF6",
      wellfound: "#F59E0B",
      linkedin: "#0A66C2",
      naukri: "#0095FF",
      ats: "#06B6D4",
      direct: "#64748B"
    };

    container.innerHTML = portals.map(p => {
      const name = p.portal.charAt(0).toUpperCase() + p.portal.slice(1);
      const color = portalColors[p.portal.toLowerCase()] || "#1A56DB";
      const percent = Math.min(100, Math.round((p.count / totalCount) * 100));

      return `
        <div class="portal-item">
          <div class="portal-meta">
            <div class="portal-name-group">
              <span class="portal-dot" style="background: ${color};"></span>
              <span>${name}</span>
            </div>
            <div class="portal-counts">
              <strong>${p.count.toLocaleString()}</strong> listings · 
              <span class="text-green">${p.applied || 0} applied</span>
            </div>
          </div>
          <div class="portal-progress-track">
            <div class="portal-progress-bar" style="width: ${percent}%; background: ${color};"></div>
          </div>
        </div>
      `;
    }).join("");
  } catch (err) {
    console.warn("Portal stats load error:", err);
  }
}

// ── Job Feed Loader (Matching reference_ui.jpg) ──────────────────────────────
async function loadJobFeed(page = 1) {
  state.feedPage = page;
  const tbody = document.getElementById("jobFeedTableBody");
  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">Refreshing top vetted opportunities...</td></tr>`;

  try {
    const portal = document.getElementById("feedPortalFilter")?.value || state.feedPortal;
    const url = `/api/jobs?page=${page}&per_page=10&portal=${encodeURIComponent(portal)}&sort_by=${state.feedSort}&search=${encodeURIComponent(state.feedSearch)}`;
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();

    const jobs = data.jobs || [];
    state.feedTotalPages = data.total_pages || 1;

    setElementText("jobFeedCountBadge", `${data.total.toLocaleString()} listings`);
    setElementText("feedPaginationInfo", `Page ${page} of ${state.feedTotalPages}`);

    const btnPrev = document.getElementById("btnFeedPrev");
    const btnNext = document.getElementById("btnFeedNext");
    if (btnPrev) btnPrev.disabled = page <= 1;
    if (btnNext) btnNext.disabled = page >= state.feedTotalPages;

    if (jobs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">No opportunities match the selected criteria.</td></tr>`;
      return;
    }

    tbody.innerHTML = jobs.map((job, idx) => {
      // Score pill styling (e.g. 98%, 94% Match)
      const rawScore = job.relevance_score || 0;
      const scorePct = rawScore <= 10 ? Math.round(rawScore * 10) : Math.round(rawScore);
      let matchClass = "match-high";
      if (scorePct < 85) matchClass = "match-mid";
      if (scorePct < 75) matchClass = "match-low";

      // Relative or formatted time
      const timeStr = formatRelativeTime(job.created_at || job.applied_date);

      // Company dot color
      const dotColors = ["#1A56DB", "#10B981", "#8B5CF6", "#F59E0B", "#EF4444", "#06B6D4"];
      const dotColor = dotColors[idx % dotColors.length];

      return `
        <tr>
          <td><input type="checkbox" class="feed-check" value="${job.job_hash}" /></td>
          <td>
            <div class="job-title-cell">
              <a href="javascript:void(0)" onclick="openJobDetail('${job.job_hash}')">${escapeHtml(job.job_title)}</a>
            </div>
          </td>
          <td>
            <div class="company-dot-group">
              <span class="company-color-dot" style="background: ${dotColor};"></span>
              <span>${escapeHtml(job.company || "Confidential")}</span>
            </div>
          </td>
          <td>
            <span class="work-type-pill">${escapeHtml(job.location || "Remote")}</span>
          </td>
          <td>
            <span class="text-muted">${escapeHtml(job.salary || job.portal.toUpperCase())}</span>
          </td>
          <td>
            <span class="match-badge ${matchClass}">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
              ${scorePct}% Match
            </span>
          </td>
          <td class="text-muted">${timeStr}</td>
          <td style="text-align: right;">
            <button class="btn btn-outline btn-sm" onclick="openJobDetail('${job.job_hash}')" title="View Details">
              ···
            </button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.warn("Failed to load job feed:", err);
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-danger">Error loading opportunities.</td></tr>`;
  }
}

function feedPrevPage() {
  if (state.feedPage > 1) loadJobFeed(state.feedPage - 1);
}

function feedNextPage() {
  if (state.feedPage < state.feedTotalPages) loadJobFeed(state.feedPage + 1);
}

function toggleJobFeedSort() {
  state.feedSort = state.feedSort === "score" ? "recent" : "score";
  showToast(`Sorted by: ${state.feedSort === "score" ? "Highest Match" : "Most Recent"}`, "info");
  loadJobFeed(1);
}

// ── Applications History Loader ──────────────────────────────────────────────
async function loadApplications(page = 1) {
  state.appsPage = page;
  const tbody = document.getElementById("applicationsTableBody");
  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">Loading applications...</td></tr>`;

  try {
    const url = `/api/applications?page=${page}&per_page=15&status=${encodeURIComponent(state.appsStatus)}&portal=${encodeURIComponent(state.appsPortal)}&search=${encodeURIComponent(state.appsSearch)}`;
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();

    const apps = data.applications || [];
    state.appsTotalPages = data.total_pages || 1;

    setElementText("appsPaginationInfo", `Page ${page} of ${state.appsTotalPages}`);

    const btnPrev = document.getElementById("btnAppsPrev");
    const btnNext = document.getElementById("btnAppsNext");
    if (btnPrev) btnPrev.disabled = page <= 1;
    if (btnNext) btnNext.disabled = page >= state.appsTotalPages;

    if (apps.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">No applications found.</td></tr>`;
      return;
    }

    tbody.innerHTML = apps.map(app => {
      const statusClass = `status-${app.status.replace("_", "")}`;
      const appliedDate = app.applied_date ? app.applied_date.split("T")[0] : (app.created_at ? app.created_at.split(" ")[0] : "—");

      return `
        <tr>
          <td><strong>${escapeHtml(app.job_title)}</strong></td>
          <td>${escapeHtml(app.company)}</td>
          <td>${escapeHtml(app.location || "Remote")}</td>
          <td><span class="badge badge-pill badge-primary">${escapeHtml(app.portal)}</span></td>
          <td><strong>${(app.relevance_score || 0).toFixed(1)}</strong></td>
          <td><span class="status-badge ${statusClass}">${escapeHtml(app.status)}</span></td>
          <td class="text-muted">${appliedDate}</td>
          <td style="text-align: right;">
            <select class="form-select form-select-sm" onchange="changeAppStatus('${app.job_hash}', this.value)" style="display:inline-block; width:auto;">
              <option value="" disabled selected>Update...</option>
              <option value="applied">Mark Applied</option>
              <option value="interview">Mark Interview</option>
              <option value="rejected">Mark Rejected</option>
            </select>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.warn("Failed to load applications history:", err);
  }
}

function appsPrevPage() {
  if (state.appsPage > 1) loadApplications(state.appsPage - 1);
}

function appsNextPage() {
  if (state.appsPage < state.appsTotalPages) loadApplications(state.appsPage + 1);
}

function filterApplicationsByStatus(status, btn) {
  state.appsStatus = status;
  document.querySelectorAll("#statusFilterPills .pill-tab").forEach(p => p.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadApplications(1);
}

let searchTimer = null;
function debounceSearchHistory() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const input = document.getElementById("historySearchInput");
    state.appsSearch = input ? input.value.trim() : "";
    loadApplications(1);
  }, 300);
}

async function changeAppStatus(jobHash, newStatus) {
  if (!newStatus) return;
  try {
    const res = await fetch(`/api/applications/${jobHash}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus, notes: `Manual update via Dashboard by candidate` })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Status updated to '${newStatus}'!`, "success");
      loadApplications(state.appsPage);
      loadStats();
    } else {
      showToast(data.message || "Failed to update status", "error");
    }
  } catch (err) {
    showToast("Network error updating status", "error");
  }
}

// ── Approval Queue Loader (Mandatory Human Safety Control) ───────────────────


async function loadApprovalQueue() {
  const container = document.getElementById("approvalQueueContainer");
  if (!container) return;

  // Preserve selections during automatic queue refresh.
  const previouslySelectedIds = new Set(
    Array.from(
      document.querySelectorAll(".job-approval-checkbox:checked")
    ).map(checkbox => checkbox.value)
  );

  try {
    const res = await fetch("/api/approval-queue?status=pending");
    if (!res.ok) return;

    const data = await res.json();
    const items = Array.isArray(data.items) ? data.items : [];

    setElementText("sidebarApprovalBadge", items.length);
    setElementText("cardPendingApprovals", items.length);
    setElementText("bannerPendingCount", items.length);

    const selectAllCheckbox = document.getElementById(
      "selectAllJobApprovals"
    );

    if (items.length === 0) {
      container.innerHTML = `
        <div class="text-center py-5 text-muted">
          <div style="font-weight:600; color:#0F172A;">
            All Clear! No Pending Actions.
          </div>
          <div style="font-size:12.5px;">
            There are currently no pending approval items.
          </div>
        </div>
      `;

      if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
        selectAllCheckbox.disabled = true;
      }

      return;
    }

    const jobApplications = items.filter(
      item => item.item_type === "job_application"
    );

    container.innerHTML = items.map(item => {
      const isJobApplication =
        item.item_type === "job_application";

      const typeLabel = isJobApplication
        ? "Application Submission"
        : "Recruiter Cold Email";

      const scoreBadge = item.score
        ? `<span class="badge badge-pill badge-primary">
             Score: ${Number(item.score).toFixed(1)}/10
           </span>`
        : "";

      return `
        <div class="approval-card" id="approval-item-${item.id}">

          ${isJobApplication ? `
            <div class="approval-select">
              <input
                type="checkbox"
                class="job-approval-checkbox"
                value="${item.id}"
                aria-label="Select job application ${escapeHtml(item.title)}"
              >
            </div>
          ` : ""}

          <div class="approval-info">
            <div class="approval-header-row">
              <span class="approval-type-badge">
                ${typeLabel}
              </span>

              <span class="approval-title">
                ${escapeHtml(item.title)}
              </span>

              ${scoreBadge}
            </div>

            <div class="approval-meta">
              <strong>${escapeHtml(item.company)}</strong>
              · Queued: ${formatRelativeTime(item.created_at)}
            </div>

            <div class="approval-preview-box">
              ${escapeHtml(item.details || "No preview summary available.")}
            </div>
          </div>

          <div class="approval-actions">
            <button
              class="btn btn-outline btn-sm"
              onclick="openApprovalModal(${item.id})">
              Preview Draft
            </button>

            <button
              class="btn btn-danger btn-sm"
              onclick="decideApproval(${item.id}, 'rejected')">
              Reject
            </button>

            <button
              class="btn btn-success btn-sm"
              onclick="decideApproval(${item.id}, 'approved')">
              Approve
            </button>
          </div>

        </div>
      `;
    }).join("");

    // Restore selections only for applications still pending.
    const currentCheckboxes = Array.from(
      document.querySelectorAll(".job-approval-checkbox")
    );

    currentCheckboxes.forEach(checkbox => {
      checkbox.checked = previouslySelectedIds.has(checkbox.value);
    });

    // Update Select All state after restoring selections.
    if (selectAllCheckbox) {
      const selectedCount = currentCheckboxes.filter(
        checkbox => checkbox.checked
      ).length;

      selectAllCheckbox.disabled = jobApplications.length === 0;

      selectAllCheckbox.checked =
        currentCheckboxes.length > 0 &&
        selectedCount === currentCheckboxes.length;

      selectAllCheckbox.indeterminate =
        selectedCount > 0 &&
        selectedCount < currentCheckboxes.length;
    }

  } catch (err) {
    console.warn("Failed to load approval queue:", err);
  }
}


// Select All job applications only
document.addEventListener("change", function (event) {
  if (event.target.id === "selectAllJobApprovals") {
    const checkboxes = document.querySelectorAll(
      ".job-approval-checkbox"
    );

    checkboxes.forEach(checkbox => {
      checkbox.checked = event.target.checked;
    });
  }

  if (event.target.classList.contains("job-approval-checkbox")) {
    const checkboxes = Array.from(
      document.querySelectorAll(".job-approval-checkbox")
    );

    const selectAllCheckbox = document.getElementById(
      "selectAllJobApprovals"
    );

    if (selectAllCheckbox) {
      const selectedCount = checkboxes.filter(
        checkbox => checkbox.checked
      ).length;

      selectAllCheckbox.checked =
        checkboxes.length > 0 &&
        selectedCount === checkboxes.length;

      selectAllCheckbox.indeterminate =
        selectedCount > 0 &&
        selectedCount < checkboxes.length;
    }
  }
});


async function decideApproval(itemId, decision) {
  try {
    const res = await fetch(`/api/approval-queue/${itemId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: decision, notes: `Decided via SaaS UI by candidate` })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Item ${decision === "approved" ? "Approved ✅" : "Rejected ❌"} successfully!`, "success");
      loadApprovalQueue();
      loadStats();
    } else {
      showToast(data.message || "Decision error", "error");
    }
  } catch (err) {
    showToast("Network error submitting decision", "error");
  }
}


async function bulkDecideApproval(decision) {
  const selectedCheckboxes = document.querySelectorAll(
    ".job-approval-checkbox:checked"
  );

  const itemIds = Array.from(selectedCheckboxes)
    .map(checkbox => Number(checkbox.value));

  if (itemIds.length === 0) {
    showToast("Please select at least one job application.", "error");
    return;
  }

  if (!["approved", "rejected"].includes(decision)) {
    showToast("Invalid bulk decision.", "error");
    return;
  }

  const actionLabel = decision === "approved" ? "approve" : "reject";

  if (!confirm(
    `Are you sure you want to ${actionLabel} ${itemIds.length} selected job application(s)?`
  )) {
    return;
  }

  try {
    const res = await fetch("/api/approval-queue/bulk-decide", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        decision: decision,
        item_ids: itemIds,
        notes: `Bulk ${decision} selected job applications via dashboard`
      })
    });

    const data = await res.json();

    if (res.ok) {
      showToast(data.message, "success");

      const selectAllCheckbox = document.getElementById(
        "selectAllJobApprovals"
      );

      if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
      }

      loadApprovalQueue();
      loadStats();
    } else {
      showToast(data.message || "Bulk decision error", "error");
    }

  } catch (err) {
    console.error("Bulk approval error:", err);
    showToast("Network error executing bulk decision.", "error");
  }
}


// ── 1,831 PDF HR Contacts Drip Progress ──────────────────────────────────────
async function loadDripStats() {
  try {
    const res = await fetch("/api/drip-stats");
    if (!res.ok) return;
    const data = await res.json();

    const sent = data.sent || 0;
    const total = data.total_contacts || 0;
    const remaining = data.remaining || 0;
    const sentToday = data.sent_today || 0;
    const quota = data.daily_quota || 50;
    const percent = data.percentage_complete || 0;

    setElementText("dripSentCount", `Sent: ${sent.toLocaleString()}`);
    setElementText("dripRemainingCount", `Remaining: ${remaining.toLocaleString()}`);
    setElementText("dripDailyQuota", `Today: ${sentToday} / ${quota}`);
    setElementText("dripPercentLabel", `${percent}% Completed (${sent} / ${total})`);
    setElementText("sidebarDripBadge", total.toLocaleString());
    setElementText("dripTargetLabel", `Target: ${total.toLocaleString()} verified recruiters`);

    const fill = document.getElementById("dripProgressFill");
    if (fill) fill.style.width = `${percent}%`;
  } catch (err) {
    console.warn("Failed to load drip stats:", err);
  }
}

async function sendTestEmail() {
  showToast("Initiating test email dispatch (Dry-Run)...", "info");
  try {
    const res = await fetch("/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: true })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Agent dry-run test initiated! Check logs.", "success");
      switchTab("tab-logs");
    } else {
      showToast(data.message || "Test dispatch failed", "error");
    }
  } catch (err) {
    showToast("Network error initiating email test", "error");
  }
}

// ── Candidate Profile & 3 Verified Skills Tiers ──────────────────────────────
let currentProfileData = null;

async function loadProfile() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) return;
    const data = await res.json();
    const profile = data.profile || {};
    currentProfileData = profile;

    setElementText("profName", profile.name || "Manoj Kumar Mandal");
    setElementText("profEmail", profile.email || "manojkumarmandal1111@gmail.com");
    setElementText("profPhone", profile.phone || "+91-8404970494");
    setElementText("profLocation", profile.location || "India");

    const degreeStr = `${profile.education_degree || "B.Tech (Hon's) Computer Science and Engineering"} — ${profile.education_college || "Satya Sai Engineering College Balasore, Odisha"} (${profile.education_year || "2026"}, CGPA: ${profile.education_cgpa || "8.01/10"})`;
    setElementText("profDegree", degreeStr);

    // Resume metadata
    const meta = profile.resume_metadata || {};
    if (meta.exists) {
      setElementText("resumeMetaFileName", meta.file_name || "resume.pdf");
      setElementText("resumeMetaSize", `${meta.file_size_kb || 305} KB (${meta.page_count || 1} Page)`);
      setElementText("profResumeSize", `${meta.file_size_kb || 305} KB`);
      setElementText("resumeMetaStatus", "Synced with Candidate Profile");
      const badge = document.getElementById("resumeStatusBadge");
      if (badge) {
        badge.className = "badge badge-success";
        badge.innerText = "Active & Verified";
      }
    }

    // Render 3 Skill Tiers
    const tiers = profile.skills_by_tier || {};
    renderSkillTier("tierProficientContainer", tiers.Proficient || []);
    renderSkillTier("tierIntermediateContainer", tiers.Intermediate || []);
    renderSkillTier("tierLearningContainer", tiers.Learning || []);

    // Render Pending Review Skills
    const pendingSkills = profile.pending_review_skills || [];
    const pendingCard = document.getElementById("pendingSkillsCard");
    const pendingContainer = document.getElementById("pendingSkillsContainer");
    if (pendingCard && pendingContainer) {
      if (pendingSkills.length > 0) {
        pendingCard.style.display = "block";
        pendingContainer.innerHTML = pendingSkills.map(s => `
          <span class="skill-pill" style="border-style:dashed; background:rgba(255,193,7,0.1); border-color:#ffc107;">
            <span>${escapeHtml(s.skill_name)}</span>
            <span style="font-size:10px; margin-left:6px; color:#ffc107;">[Review]</span>
            <button class="btn-remove-skill" onclick="quickVerifySkill('${escapeHtml(s.skill_name)}', 'Proficient')" title="Confirm as Proficient (1.0)">+P</button>
            <button class="btn-remove-skill" onclick="quickVerifySkill('${escapeHtml(s.skill_name)}', 'Intermediate')" title="Confirm as Intermediate (0.7)">+I</button>
            <button class="btn-remove-skill" onclick="deleteSkill(${s.id})" title="Remove skill">&times;</button>
          </span>
        `).join("");
      } else {
        pendingCard.style.display = "none";
      }
    }

    // Render Projects
    const projects = profile.projects || [];
    const projectsContainer = document.getElementById("projectsContainer");
    if (projectsContainer) {
      if (projects.length === 0) {
        projectsContainer.innerHTML = `<span class="text-muted" style="font-size:12px;">No projects found.</span>`;
      } else {
        projectsContainer.innerHTML = projects.map(p => `
          <div class="card mb-3" style="background:var(--bg-secondary); border:1px solid var(--border); padding:16px; border-radius:8px;">
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h5 style="margin:0; font-size:15px; font-weight:600; color:var(--text-primary);">${escapeHtml(p.name)}</h5>
              ${p.github_url ? `<a href="${escapeHtml(p.github_url)}" target="_blank" class="btn btn-outline btn-sm" style="font-size:11px; padding:2px 8px;">View Code</a>` : ''}
            </div>
            <p style="font-size:13px; color:var(--text-secondary); line-height:1.5; margin-bottom:10px;">${escapeHtml(p.description)}</p>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              ${(p.tech_stack || []).map(t => `<span class="badge badge-secondary" style="font-size:11px;">${escapeHtml(t)}</span>`).join('')}
            </div>
          </div>
        `).join("");
      }
    }

    // Render Certifications
    const certs = profile.certifications || [];
    const certContainer = document.getElementById("certificationsContainer");
    if (certContainer) {
      certContainer.innerHTML = certs.length === 0 ? `<span class="text-muted" style="font-size:12px;">None listed.</span>` : `
        <ul style="padding-left:18px; margin:0; font-size:13px; color:var(--text-secondary);">
          ${certs.map(c => `<li style="margin-bottom:8px;">${escapeHtml(c)}</li>`).join('')}
        </ul>
      `;
    }

    // Render Honors
    const honors = profile.honors_awards || [];
    const honorsContainer = document.getElementById("honorsContainer");
    if (honorsContainer) {
      honorsContainer.innerHTML = honors.length === 0 ? `<span class="text-muted" style="font-size:12px;">None listed.</span>` : `
        <ul style="padding-left:18px; margin:0; font-size:13px; color:var(--text-secondary);">
          ${honors.map(h => `<li style="margin-bottom:8px;">${escapeHtml(h)}</li>`).join('')}
        </ul>
      `;
    }

    // Render Preferences Summary
    const prefs = profile.preferences || {};
    const prefSummary = document.getElementById("preferencesSummary");
    if (prefSummary) {
      const roles = prefs.target_roles || [];
      const locs = prefs.preferred_locations || [];
      prefSummary.innerHTML = `
        <div style="display:flex; flex-wrap:wrap; gap:16px;">
          <div style="flex:2; min-width:240px;">
            <small class="text-muted d-block mb-1">Target Roles</small>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              ${roles.map(r => `<span class="badge badge-primary" style="font-size:11px;">${escapeHtml(r)}</span>`).join('')}
            </div>
          </div>
          <div style="flex:1; min-width:140px;">
            <small class="text-muted d-block mb-1">Min Match Score</small>
            <strong style="color:var(--primary); font-size:15px;">${prefs.matching_threshold || 7.0}/10</strong>
          </div>
          <div style="flex:1; min-width:140px;">
            <small class="text-muted d-block mb-1">Target Locations</small>
            <div style="font-size:13px; color:var(--text-primary);">${locs.join(', ') || 'Remote, India'}</div>
          </div>
        </div>
      `;
    }
  } catch (err) {
    console.warn("Failed to load candidate profile:", err);
  }
}

function renderSkillTier(containerId, skills) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (skills.length === 0) {
    container.innerHTML = `<span class="text-muted" style="font-size:12px;">No skills declared in this tier.</span>`;
    return;
  }

  container.innerHTML = skills.map(s => `
    <span class="skill-pill">
      <span>${escapeHtml(s.skill_name)}</span>
      <button class="btn-remove-skill" onclick="deleteSkill(${s.id})" title="Remove skill">&times;</button>
    </span>
  `).join("");
}

function openAddSkillModal() {
  openModal("addSkillModal");
}

async function submitAddSkill(event) {
  event.preventDefault();
  const name = document.getElementById("newSkillName")?.value.trim();
  const prof = document.getElementById("newSkillProficiency")?.value;
  const cat = document.getElementById("newSkillCategory")?.value.trim() || "Technical";

  if (!name) return;

  try {
    const res = await fetch("/api/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: name, proficiency: prof, category: cat })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Skill '${name}' added successfully!`, "success");
      closeModal("addSkillModal");
      document.getElementById("addSkillForm")?.reset();
      loadProfile();
    } else {
      showToast(data.message || "Error adding skill", "error");
    }
  } catch (err) {
    showToast("Network error declaring skill", "error");
  }
}

async function deleteSkill(skillId) {
  if (!confirm("Are you sure you want to remove this skill declaration?")) return;
  try {
    const res = await fetch(`/api/skills/${skillId}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast("Skill declaration removed.", "success");
      loadProfile();
    } else {
      showToast(data.message || "Failed to remove skill", "error");
    }
  } catch (err) {
    showToast("Network error deleting skill", "error");
  }
}

function openEditProfileModal() {
  if (currentProfileData) {
    document.getElementById("editCandidateName").value = currentProfileData.name || "";
    document.getElementById("editCandidateEmail").value = currentProfileData.email || "";
    document.getElementById("editCandidatePhone").value = currentProfileData.phone || "";
    document.getElementById("editCandidateLocation").value = currentProfileData.location || "";
    document.getElementById("editEducationDegree").value = currentProfileData.education_degree || "";
    document.getElementById("editEducationCollege").value = currentProfileData.education_college || "";
    document.getElementById("editEducationYear").value = currentProfileData.education_year || "";
    document.getElementById("editEducationCgpa").value = currentProfileData.education_cgpa || "";
  }
  openModal("editProfileModal");
}

async function submitEditProfile(event) {
  event.preventDefault();
  const name = document.getElementById("editCandidateName").value.trim();
  const email = document.getElementById("editCandidateEmail").value.trim();
  const phone = document.getElementById("editCandidatePhone").value.trim();
  const loc = document.getElementById("editCandidateLocation").value.trim();
  const degree = document.getElementById("editEducationDegree").value.trim();
  const college = document.getElementById("editEducationCollege").value.trim();
  const year = document.getElementById("editEducationYear").value.trim();
  const cgpa = document.getElementById("editEducationCgpa").value.trim();

  try {
    const resPersonal = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, phone, location: loc })
    });
    const resEdu = await fetch("/api/profile/education", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ degree, college, year, cgpa })
    });

    if (resPersonal.ok && resEdu.ok) {
      showToast("Candidate profile and education updated successfully!", "success");
      closeModal("editProfileModal");
      loadProfile();
    } else {
      showToast("Failed to update candidate profile.", "error");
    }
  } catch (err) {
    showToast("Network error updating profile.", "error");
  }
}

async function reprocessResumePrompt() {
  try {
    showToast("Analyzing resume PDF structure...", "info");
    const res = await fetch("/api/profile/reprocess-resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: false })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.message || "Error analyzing resume.", "error");
      return;
    }
    const result = data.result || {};
    const modalBody = document.getElementById("reprocessModalBody");
    const diffs = result.diff_summary || [];
    const newSkills = result.new_skills_pending_review || [];

    modalBody.innerHTML = `
      <div class="mb-3">
        <p><strong>Status:</strong> ${escapeHtml(result.message)}</p>
      </div>
      <div class="mb-3">
        <h6 style="font-weight:600;">Detected Field Differences (${diffs.length}):</h6>
        ${diffs.length === 0 ? '<p class="text-muted" style="font-size:12px;">All candidate fields already match verified resume text.</p>' : `
          <ul style="font-size:12px; padding-left:18px;">
            ${diffs.map(d => `<li><strong>${escapeHtml(d.field)}:</strong> "${escapeHtml(d.current)}" &rarr; "${escapeHtml(d.extracted)}"</li>`).join('')}
          </ul>
        `}
      </div>
      <div class="mb-3">
        <h6 style="font-weight:600;">New Skills Detected from Resume (${newSkills.length}):</h6>
        <p style="font-size:12px; color:var(--text-secondary);">${newSkills.join(', ') || 'No new skills'}</p>
      </div>
      <div class="alert alert-warning" style="font-size:12px; padding:8px 12px; border-radius:6px; background:rgba(255,193,7,0.1); border:1px solid #ffc107; color:#856404;">
        <strong>Safety Gate:</strong> Confirming will synchronize verified profile and stage new skills for review without deleting existing records.
      </div>
    `;
    openModal("reprocessResumeModal");
  } catch (err) {
    showToast("Failed to reprocess resume.", "error");
  }
}

async function confirmResumeSync() {
  try {
    const res = await fetch("/api/profile/reprocess-resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Resume synchronized with candidate profile successfully!", "success");
      closeModal("reprocessResumeModal");
      loadProfile();
    } else {
      showToast(data.message || "Failed to synchronize resume.", "error");
    }
  } catch (err) {
    showToast("Network error syncing resume.", "error");
  }
}

async function quickVerifySkill(skillName, tier) {
  try {
    const res = await fetch("/api/skills/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: skillName, proficiency: tier })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Skill '${skillName}' verified as ${tier}!`, "success");
      loadProfile();
    } else {
      showToast(data.message || "Failed to verify skill", "error");
    }
  } catch (err) {
    showToast("Network error verifying skill", "error");
  }
}

function openPreferencesModal() {
  if (currentProfileData && currentProfileData.preferences) {
    const p = currentProfileData.preferences;
    document.getElementById("prefTargetRoles").value = (p.target_roles || []).join(", ");
    document.getElementById("prefPreferredLocations").value = (p.preferred_locations || []).join(", ");
    document.getElementById("prefMinScore").value = p.matching_threshold || 7.0;
    document.getElementById("prefMinSalary").value = p.min_salary_lpa || 3.6;
  }
  openModal("preferencesModal");
}

async function submitPreferences(event) {
  event.preventDefault();
  const roles = document.getElementById("prefTargetRoles").value.split(",").map(r => r.trim()).filter(Boolean);
  const locs = document.getElementById("prefPreferredLocations").value.split(",").map(l => l.trim()).filter(Boolean);
  const minScore = parseFloat(document.getElementById("prefMinScore").value) || 7.0;
  const minSalary = parseFloat(document.getElementById("prefMinSalary").value) || 3.6;

  try {
    const res = await fetch("/api/profile/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_roles: roles,
        preferred_locations: locs,
        matching_threshold: minScore,
        min_salary_lpa: minSalary
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Career preferences updated successfully!", "success");
      closeModal("preferencesModal");
      loadProfile();
    } else {
      showToast(data.message || "Failed to update preferences", "error");
    }
  } catch (err) {
    showToast("Network error updating preferences", "error");
  }
}

// ── Settings & Connectivity ──────────────────────────────────────────────────
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const data = await res.json();
    const settings = data.settings || {};
    const connect = data.connectivity || {};

    const chkDryRun = document.getElementById("settingDryRun");
    if (chkDryRun) chkDryRun.checked = settings.dry_run;

    const chkApproval = document.getElementById("settingHumanApproval");
    if (chkApproval) chkApproval.checked = settings.require_human_approval;

    const numScore = document.getElementById("settingMinScore");
    if (numScore) numScore.value = settings.match_score_threshold;

    // Render Connectivity Cards
    const connectGrid = document.getElementById("connectivityGrid");
    if (connectGrid) {
      const services = [
        { name: "Google Gemini 1.5 Flash", ok: connect.gemini_configured, note: "Offline Heuristics Fallback Ready" },
        { name: "Gmail SMTP / IMAP SSL", ok: connect.gmail_configured, note: "Local Dry-Run Simulation Active" },
        { name: "SQLite Relational WAL Mode", ok: connect.sqlite_wal, note: "Multi-Reader Active" },
        { name: "Tavily Enrichment API", ok: connect.tavily_configured, note: "Optional Web Search" },
        { name: "Apollo Email Finder", ok: connect.apollo_configured, note: "Waterfall Enrichment" },
        { name: "Mem0 Long-Term Memory", ok: connect.mem0_configured, note: "SQLite Cache Fallback Active" }
      ];

      connectGrid.innerHTML = services.map(s => `
        <div class="connect-card">
          <div>
            <div class="connect-name">${s.name}</div>
            <div class="text-muted" style="font-size:11px;">${s.note}</div>
          </div>
          <span class="connect-badge ${s.ok ? 'badge-green' : 'badge-amber'}">
            ${s.ok ? 'Connected' : 'Graceful Fallback'}
          </span>
        </div>
      `).join("");
    }
  } catch (err) {
    console.warn("Failed to load settings:", err);
  }
}

async function updateSettings() {
  const dryRun = document.getElementById("settingDryRun")?.checked;
  const humanApproval = document.getElementById("settingHumanApproval")?.checked;
  const minScore = parseFloat(document.getElementById("settingMinScore")?.value || "7.0");

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run: dryRun,
        require_human_approval: humanApproval,
        match_score_threshold: minScore
      })
    });
    if (res.ok) {
      showToast("Settings updated successfully!", "success");
      const modeLabel = document.getElementById("sidebarSafetyMode");
      if (modeLabel) modeLabel.textContent = dryRun ? "Safe Dry-Run Mode" : "Live Submission Mode";
    }
  } catch (err) {
    showToast("Failed to save settings", "error");
  }
}

// ── Agent Execution & Live SSE Log Telemetry ─────────────────────────────────
async function triggerAgentRun() {
  const chkDryRun = document.getElementById("agentDryRunCheck");
  const dryRun = chkDryRun ? chkDryRun.checked : true;

  try {
    const res = await fetch("/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: dryRun })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Agent triggered in background! Streaming logs...", "info");
      updateAgentStatusPill(true);
      switchTab("tab-logs");
    } else {
      showToast(data.message || "Agent trigger failed", "error");
    }
  } catch (err) {
    showToast("Network error triggering agent", "error");
  }
}

function initSSEStream() {
  if (state.sseSource) {
    state.sseSource.close();
  }

  const terminalBody = document.getElementById("terminalLogs");
  const terminalStatus = document.getElementById("terminalStatus");

  try {
    state.sseSource = new EventSource("/api/agent/stream-logs");

    state.sseSource.onopen = () => {
      if (terminalStatus) terminalStatus.textContent = "Live Stream Active";
    };

    state.sseSource.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.heartbeat) {
          updateAgentStatusPill(payload.running);
          return;
        }

        if (payload.line && terminalBody) {
          const div = document.createElement("div");
          div.className = "log-line";
          div.textContent = payload.line;

          // Colorize keywords
          if (payload.line.includes("[ERROR]") || payload.line.includes("❌")) {
            div.style.color = "#EF4444";
          } else if (payload.line.includes("[RUN COMPLETE]") || payload.line.includes("✅")) {
            div.style.color = "#10B981";
          } else if (payload.line.includes("🚀") || payload.line.includes("[AGENT TRIGGERED]")) {
            div.style.color = "#38BDF8";
          }

          terminalBody.appendChild(div);
          terminalBody.scrollTop = terminalBody.scrollHeight;
        }
      } catch (parseErr) {
        // Raw line fallback
      }
    };

    state.sseSource.onerror = () => {
      if (terminalStatus) terminalStatus.textContent = "Reconnecting...";
    };
  } catch (err) {
    console.warn("SSE stream unavailable:", err);
  }
}

function clearLogConsole() {
  const terminalBody = document.getElementById("terminalLogs");
  if (terminalBody) {
    terminalBody.innerHTML = `<div class="log-line text-cyan">[SYSTEM] Console buffer cleared by user.</div>`;
  }
}

// ── Modals & Previews ────────────────────────────────────────────────────────
async function openJobDetail(jobHash) {
  try {
    const res = await fetch(`/api/jobs?search=${jobHash}`);
    const data = await res.json();
    const job = (data.jobs || []).find(j => j.job_hash === jobHash) || {};

    document.getElementById("modalJobTitle").textContent = job.job_title || "Job Details";
    const body = document.getElementById("modalJobBody");

    const skillsHtml = (job.matched_skills || []).map(s => `<span class="badge badge-pill badge-primary mr-1">${escapeHtml(s)}</span>`).join(" ");

    body.innerHTML = `
      <div class="mb-3">
        <h4 style="font-size:16px; font-weight:700;">${escapeHtml(job.company || "Confidential")}</h4>
        <div class="text-muted" style="font-size:13px;">${escapeHtml(job.location || "Remote")} · Portal: ${escapeHtml(job.portal)}</div>
      </div>
      <div class="mb-3">
        <strong>Match Score:</strong> <span class="badge badge-pill badge-green">${(job.relevance_score || 0).toFixed(1)} / 10</span>
      </div>
      ${skillsHtml ? `<div class="mb-3"><strong>Matched Skills:</strong><div class="mt-1">${skillsHtml}</div></div>` : ""}
      ${job.reasoning ? `<div class="mb-3"><strong>AI Evaluation Reasoning:</strong><p class="text-muted mt-1" style="font-size:12.5px;">${escapeHtml(job.reasoning)}</p></div>` : ""}
      <div class="mb-3">
        <strong>Candidate Application Pitch:</strong>
        <p class="text-muted mt-1" style="font-size:12px; background:#F8FAFC; padding:10px; border-radius:6px;">
          Applied as Manoj Kumar Mandal (B.Tech CSE, AI/ML & Agentic AI Engineer). Targeting high-impact machine learning and autonomous agent pipelines.
        </p>
      </div>
    `;

    const linkBtn = document.getElementById("modalJobUrlLink");
    if (linkBtn) linkBtn.href = job.job_url || "#";

    openModal("jobDetailModal");
  } catch (err) {
    console.warn("Error opening job detail:", err);
  }
}

async function openApprovalModal(itemId) {
  try {
    const res = await fetch(`/api/approval-queue?status=pending`);
    const data = await res.json();
    const item = (data.items || []).find(i => i.id === itemId);
    if (!item) return;

    document.getElementById("approvalModalTitle").textContent = `Review: ${item.title}`;
    const body = document.getElementById("approvalModalBody");

    body.innerHTML = `
      <div class="mb-3">
        <strong>Target:</strong> ${escapeHtml(item.company)} (${item.item_type})
      </div>
      <div class="mb-3">
        <strong>Candidate Name:</strong> Manoj Kumar Mandal
      </div>
      <div class="mb-3">
        <strong>Draft Summary & Pitch:</strong>
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; padding:12px; border-radius:8px; font-size:12.5px; white-space:pre-wrap;">
          ${escapeHtml(item.details)}
        </div>
      </div>
    `;

    const footer = document.getElementById("approvalModalFooter");
    footer.innerHTML = `
      <button class="btn btn-outline" onclick="closeModal('approvalReviewModal')">Cancel</button>
      <button class="btn btn-danger" onclick="decideApproval(${item.id}, 'rejected'); closeModal('approvalReviewModal');">Reject Draft</button>
      <button class="btn btn-success" onclick="decideApproval(${item.id}, 'approved'); closeModal('approvalReviewModal');">Approve for Dispatch</button>
    `;

    openModal("approvalReviewModal");
  } catch (err) {
    console.warn("Error opening approval modal:", err);
  }
}

function openModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) el.classList.add("active");
}

function closeModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) el.classList.remove("active");
}

// ── Utilities ───────────────────────────────────────────────────────────────
function setElementText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return "recently";
  try {
    const d = new Date(dateStr.replace(" ", "T"));
    const now = new Date();
    const diffSec = Math.floor((now - d) / 1000);
    if (diffSec < 60) return "just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    if (diffSec < 604800) return `${Math.floor(diffSec / 86400)}d ago`;
    return dateStr.split("T")[0].split(" ")[0];
  } catch (e) {
    return dateStr;
  }
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}
