// Newtonite Operations Under Pressure - Frontend Controller
let token = localStorage.getItem("token") || "";
let currentUser = null;
let currentTeams = [];
let activeItem = null;
let currentCursor = null;
let searchDebounceTimeout = null;

const state = {
  team_id: null,
  status: "",
  priority: "",
  assignee: "",
  needs_attention: null,
  q: "",
  cursor: null,
  limit: 15,
  optimistic: true
};

// API Helper
async function api(path, options = {}) {
  const headers = options.headers || {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (!headers["Content-Type"] && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    logout();
    throw new Error("Session expired. Please sign in again.");
  }

  const data = await res.json().catch(() => ({}));

  if (res.status === 409) {
    showConflictBanner(data.detail || "Conflict: This item was modified concurrently.");
    throw new Error(data.detail || "Conflict (HTTP 409)");
  }

  if (!res.ok) {
    const errorMsg = data.detail || `Request failed with status ${res.status}`;
    showToast("⚠️", errorMsg);
    throw new Error(errorMsg);
  }

  return data;
}

// Global UI Notifications
function showToast(icon, message) {
  const toast = document.getElementById("toast");
  document.getElementById("toastIcon").textContent = icon;
  document.getElementById("toastMessage").textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4000);
}

function showConflictBanner(message) {
  const banner = document.getElementById("conflictBanner");
  document.getElementById("conflictMessage").textContent = message;
  banner.classList.remove("hidden");

  document.getElementById("conflictReloadBtn").onclick = () => {
    banner.classList.add("hidden");
    if (activeItem) {
      loadItemDetail(activeItem.id);
    }
    loadItems();
    loadStats();
  };
}

// Authentication
async function quickLogin(email) {
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: email, password: "password123" })
    });
    token = data.access_token;
    localStorage.setItem("token", token);
    currentUser = data.user;
    document.getElementById("loginScreen").classList.add("hidden");
    showToast("✅", `Logged in as ${currentUser.full_name}`);
    await initApp();
  } catch (err) {
    console.error("Login failed:", err);
  }
}

function logout() {
  token = "";
  currentUser = null;
  localStorage.removeItem("token");
  document.getElementById("loginScreen").classList.remove("hidden");
}

document.getElementById("logoutBtn").onclick = logout;

document.getElementById("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  const email = document.getElementById("loginEmail").value;
  const password = document.getElementById("loginPassword").value;
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password })
    });
    token = data.access_token;
    localStorage.setItem("token", token);
    currentUser = data.user;
    document.getElementById("loginScreen").classList.add("hidden");
    showToast("✅", `Logged in as ${currentUser.full_name}`);
    await initApp();
  } catch (err) {
    // Handled in api()
  }
};

// Initial App Setup
async function initApp() {
  try {
    // 1. Fetch current user profile
    currentUser = await api("/api/auth/me");
    renderUserBadge();

    // 2. Fetch accessible teams
    currentTeams = await api("/api/teams");
    populateTeamFilters();

    // 3. Set default team
    if (currentTeams.length > 0 && !state.team_id) {
      state.team_id = currentTeams[0].id;
      document.getElementById("teamFilter").value = state.team_id;
    }

    // 4. Load dashboard stats & initial items
    await loadStats();
    await loadItems();
  } catch (err) {
    console.error("App init error:", err);
  }
}

function renderUserBadge() {
  const badge = document.getElementById("currentUserBadge");
  const rolesList = currentUser.roles.map(r => `${r.team_name}: ${r.role.toUpperCase()}`).join(" | ");
  badge.innerHTML = `
    <div class="text-right">
      <div class="font-bold text-white">${currentUser.full_name}</div>
      <div class="text-[10px] text-slate-400 font-mono" title="${rolesList}">
        ${currentUser.roles[0] ? `${currentUser.roles[0].team_name} (${currentUser.roles[0].role})` : "User"}
      </div>
    </div>
  `;
}

function populateTeamFilters() {
  const teamSelect = document.getElementById("teamFilter");
  const createTeamSelect = document.getElementById("createTeamId");

  teamSelect.innerHTML = currentTeams.map(t =>
    `<option value="${t.id}">${t.name} ${t.user_role ? `(${t.user_role})` : ""}</option>`
  ).join("");

  createTeamSelect.innerHTML = currentTeams
    .filter(t => t.is_member && t.user_role !== "viewer")
    .map(t => `<option value="${t.id}">${t.name}</option>`)
    .join("");

  if (createTeamSelect.options.length === 0) {
    createTeamSelect.innerHTML = `<option value="">No member teams (Viewer role)</option>`;
  }
}

// Dashboard KPIs
async function loadStats() {
  try {
    const stats = await api("/api/dashboard/stats");
    document.getElementById("statAssigned").textContent = stats.assigned_to_me;
    document.getElementById("statUrgent").textContent = stats.unassigned_urgent;
    document.getElementById("statApproval").textContent = stats.awaiting_approval;
    document.getElementById("statTotal").textContent = stats.total_team_items;
  } catch (err) {
    console.error("Stats load failed:", err);
  }
}

// Work Items List & Cursor Pagination
async function loadItems(cursor = null) {
  state.cursor = cursor;
  const params = new URLSearchParams();
  if (state.team_id) params.append("team_id", state.team_id);
  if (state.status) params.append("status", state.status);
  if (state.priority) params.append("priority", state.priority);
  if (state.assignee) params.append("assignee", state.assignee);
  if (state.needs_attention) params.append("needs_attention", state.needs_attention);
  if (state.q) params.append("q", state.q);
  if (state.cursor) params.append("cursor", state.cursor);
  params.append("limit", state.limit);

  try {
    const data = await api(`/api/items?${params.toString()}`);
    renderTable(data.items);

    currentCursor = data.next_cursor;
    const loadMoreBtn = document.getElementById("loadMoreBtn");
    loadMoreBtn.disabled = !data.has_more;

    document.getElementById("paginationInfo").textContent =
      `Showing ${data.items.length} items (Total: ${data.total_count})`;
  } catch (err) {
    console.error("Failed to load items:", err);
  }
}

function renderTable(items) {
  const tbody = document.getElementById("itemsTableBody");
  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-slate-500">No work items found matching current filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(item => {
    const isUnassigned = !item.assignee_id;
    return `
      <tr class="hover:bg-slate-900/60 cursor-pointer transition border-b border-slate-800/40" onclick="openDetailDrawer(${item.id})">
        <td class="py-3 px-4 font-mono font-medium text-slate-400">#${item.id}</td>
        <td class="py-3 px-4 font-medium text-white max-w-xs truncate">
          ${item.title}
          <div class="text-[10px] text-slate-500 uppercase font-mono">${item.item_type}</div>
        </td>
        <td class="py-3 px-4">
          <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase badge-${item.priority}">
            ${item.priority}
          </span>
        </td>
        <td class="py-3 px-4">
          <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase badge-${item.status}">
            ${item.status.replace("_", " ")}
          </span>
        </td>
        <td class="py-3 px-4 text-slate-400 text-xs">${item.team_name || "Team"}</td>
        <td class="py-3 px-4">
          ${isUnassigned
            ? `<span class="text-amber-400 font-semibold text-[11px]">Unassigned</span>`
            : `<span class="text-slate-300 text-xs">${item.assignee_name || `User #${item.assignee_id}`}</span>`
          }
        </td>
        <td class="py-3 px-4 font-mono text-[11px] text-indigo-400">v${item.version}</td>
        <td class="py-3 px-4 text-right" onclick="event.stopPropagation()">
          ${isUnassigned ? `
            <button onclick="claimItem(${item.id}, ${item.version})" class="px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-[10px] transition shadow">
              Claim
            </button>
          ` : `
            <button onclick="openDetailDrawer(${item.id})" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[10px]">
              View
            </button>
          `}
        </td>
      </tr>
    `;
  }).join("");
}

function resetPagination() {
  loadItems(null);
}

function loadNextPage() {
  if (currentCursor) {
    loadItems(currentCursor);
  }
}

// Claim Item with Concurrency & Optimistic Handling
async function claimItem(itemId, itemVersion) {
  const optimistic = document.getElementById("optimisticToggle").checked;

  if (optimistic) {
    // Optimistically update the UI immediately
    const row = document.querySelector(`tr[onclick*="${itemId}"]`);
    if (row) {
      const assigneeCell = row.children[5];
      assigneeCell.innerHTML = `<span class="text-emerald-400 font-semibold text-xs">${currentUser.full_name} (optimistic)</span>`;
    }
  }

  try {
    const updated = await api(`/api/items/${itemId}/claim`, {
      method: "POST",
      body: JSON.stringify({ version: itemVersion })
    });
    showToast("🎉", `Successfully claimed work item #${itemId}`);
    loadItems(state.cursor);
    loadStats();
    if (activeItem && activeItem.id === itemId) {
      loadItemDetail(itemId);
    }
  } catch (err) {
    // If optimistic was applied, rollback and refresh
    console.warn("Claim failed, rolling back UI:", err);
    loadItems(state.cursor);
  }
}

// Work Item Detail Drawer
async function openDetailDrawer(itemId) {
  document.getElementById("detailDrawer").classList.remove("hidden");
  await loadItemDetail(itemId);
}

function closeDetailDrawer() {
  document.getElementById("detailDrawer").classList.add("hidden");
  activeItem = null;
}

async function loadItemDetail(itemId) {
  try {
    const item = await api(`/api/items/${itemId}`);
    activeItem = item;

    document.getElementById("detailId").textContent = `#${item.id}`;
    document.getElementById("detailType").textContent = item.item_type;
    document.getElementById("detailVersion").textContent = `v${item.version}`;
    document.getElementById("detailTitle").textContent = item.title;
    document.getElementById("detailDescription").textContent = item.description || "No description provided.";
    document.getElementById("detailStatus").textContent = item.status.replace("_", " ").toUpperCase();
    document.getElementById("detailPriority").textContent = item.priority.toUpperCase();
    document.getElementById("detailAssignee").textContent = item.assignee_name || "Unassigned";
    document.getElementById("detailTeam").textContent = item.team_name || `#${item.team_id}`;

    renderWorkflowActions(item);
    renderActivityTimeline(item.activity_events);
    renderComments(item.comments);
  } catch (err) {
    console.error("Failed to load item detail:", err);
  }
}

function renderWorkflowActions(item) {
  const container = document.getElementById("workflowActions");
  const membership = currentUser.roles.find(r => r.team_id === item.team_id);
  const userRole = membership ? membership.role.toLowerCase() : "viewer";

  if (userRole === "viewer") {
    container.innerHTML = `<span class="text-xs text-slate-500 italic">Viewers have read-only access.</span>`;
    return;
  }

  let buttons = [];

  // 1. Claim button if unassigned
  if (!item.assignee_id) {
    buttons.push(`
      <button onclick="claimItem(${item.id}, ${item.version})" class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs">
        ⚡ Claim This Item
      </button>
    `);
  }

  // 2. Transitions based on current status
  const s = item.status.toLowerCase();

  if (s === "open") {
    buttons.push(`
      <button onclick="transitionItem('in_progress', ${item.version})" class="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs">
        Start Progress &rarr;
      </button>
    `);
  } else if (s === "in_progress") {
    buttons.push(`
      <button onclick="transitionItem('awaiting_approval', ${item.version})" class="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold text-xs">
        Request Approval &rarr;
      </button>
      <button onclick="transitionItem('open', ${item.version})" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs">
        Move Back to Open
      </button>
    `);
  } else if (s === "awaiting_approval") {
    const isLeadOrAdmin = userRole === "lead" || userRole === "admin";
    buttons.push(`
      <button onclick="transitionItem('resolved', ${item.version})" ${!isLeadOrAdmin ? "disabled title='Only Leads or Admins can approve'" : ""} class="px-3 py-1.5 rounded-lg ${isLeadOrAdmin ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 text-slate-500 cursor-not-allowed'} font-semibold text-xs">
        ✓ Approve & Resolve ${!isLeadOrAdmin ? " [Lead Only]" : ""}
      </button>
      <button onclick="transitionItem('in_progress', ${item.version})" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs">
        Reject to In Progress
      </button>
    `);
  } else if (s === "resolved") {
    const isLeadOrAdmin = userRole === "lead" || userRole === "admin";
    buttons.push(`
      <button onclick="transitionItem('closed', ${item.version})" ${!isLeadOrAdmin ? "disabled title='Only Leads or Admins can close'" : ""} class="px-3 py-1.5 rounded-lg ${isLeadOrAdmin ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-slate-800 text-slate-500 cursor-not-allowed'} font-semibold text-xs">
        🔒 Close Work Item ${!isLeadOrAdmin ? " [Lead Only]" : ""}
      </button>
      <button onclick="transitionItem('in_progress', ${item.version})" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs">
        Reopen to In Progress
      </button>
    `);
  } else if (s === "closed") {
    buttons.push(`
      <button onclick="transitionItem('open', ${item.version})" class="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs">
        Reopen Item
      </button>
    `);
  }

  container.innerHTML = buttons.join("");
}

async function transitionItem(targetStatus, version) {
  if (!activeItem) return;

  const optimistic = document.getElementById("optimisticToggle").checked;
  const oldStatus = activeItem.status;

  if (optimistic) {
    document.getElementById("detailStatus").textContent = targetStatus.replace("_", " ").toUpperCase();
  }

  try {
    const updated = await api(`/api/items/${activeItem.id}/transition`, {
      method: "POST",
      body: JSON.stringify({
        to_status: targetStatus,
        version: version,
        comment: `Status transitioned via dashboard`
      })
    });
    showToast("✅", `Item transitioned to ${targetStatus}`);
    await loadItemDetail(activeItem.id);
    loadItems(state.cursor);
    loadStats();
  } catch (err) {
    console.warn("Transition failed:", err);
    if (optimistic) {
      document.getElementById("detailStatus").textContent = oldStatus.replace("_", " ").toUpperCase();
    }
  }
}

// Stale Edit Concurrency Simulation
async function simulateStaleEdit() {
  if (!activeItem) return;
  // Deliberately send an out-of-date version (version - 1)
  const staleVersion = Math.max(0, activeItem.version - 1);
  try {
    await api(`/api/items/${activeItem.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        title: `${activeItem.title} (STALE EDIT)`,
        version: staleVersion
      })
    });
  } catch (err) {
    // 409 Conflict handled by api() helper which displays the top banner!
  }
}

function renderActivityTimeline(events) {
  const container = document.getElementById("detailTimeline");
  if (!events || events.length === 0) {
    container.innerHTML = `<p class="text-slate-500 text-xs italic">No activity recorded yet.</p>`;
    return;
  }

  container.innerHTML = events.map(e => {
    const dt = new Date(e.created_at).toLocaleString();
    return `
      <div class="relative pb-2">
        <div class="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-indigo-500 border-2 border-slate-900"></div>
        <div class="flex items-center justify-between text-[11px]">
          <span class="font-bold text-slate-200">${e.actor_name || "System"}</span>
          <span class="text-slate-500 font-mono text-[10px]">${dt}</span>
        </div>
        <div class="text-[11px] text-indigo-300 font-mono uppercase">${e.event_type}</div>
        <div class="text-xs text-slate-400 mt-0.5">${e.details || ""}</div>
      </div>
    `;
  }).join("");
}

function renderComments(comments) {
  const container = document.getElementById("detailComments");
  if (!comments || comments.length === 0) {
    container.innerHTML = `<p class="text-slate-500 text-xs italic">No comments yet. Start the conversation.</p>`;
    return;
  }

  container.innerHTML = comments.map(c => {
    const dt = new Date(c.created_at).toLocaleString();
    return `
      <div class="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
        <div class="flex items-center justify-between text-[11px]">
          <span class="font-semibold text-slate-300">${c.author_name || "Unknown"}</span>
          <span class="text-[10px] text-slate-500 font-mono">${dt}</span>
        </div>
        <p class="text-xs text-slate-200 mt-1">${c.content}</p>
      </div>
    `;
  }).join("");
}

document.getElementById("commentForm").onsubmit = async (e) => {
  e.preventDefault();
  if (!activeItem) return;
  const input = document.getElementById("commentInput");
  const content = input.value.trim();
  if (!content) return;

  try {
    await api(`/api/items/${activeItem.id}/comments`, {
      method: "POST",
      body: JSON.stringify({ content })
    });
    input.value = "";
    showToast("💬", "Comment posted");
    await loadItemDetail(activeItem.id);
  } catch (err) {
    // Handled in api()
  }
};

// Create Item Modal & Idempotency Key
function openCreateModal() {
  document.getElementById("createModal").classList.remove("hidden");
}

function closeCreateModal() {
  document.getElementById("createModal").classList.add("hidden");
  document.getElementById("createItemForm").reset();
}

document.getElementById("openCreateModalBtn").onclick = openCreateModal;

document.getElementById("createItemForm").onsubmit = async (e) => {
  e.preventDefault();
  const title = document.getElementById("createTitle").value.trim();
  const team_id = parseInt(document.getElementById("createTeamId").value);
  const item_type = document.getElementById("createItemType").value;
  const priority = document.getElementById("createPriority").value;
  const assigneeVal = document.getElementById("createAssigneeId").value;
  const assignee_id = assigneeVal ? parseInt(assigneeVal) : null;
  const description = document.getElementById("createDescription").value.trim();

  // Generate unique Idempotency Key
  const idempotencyKey = `idemp-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

  try {
    const created = await api("/api/items", {
      method: "POST",
      headers: {
        "Idempotency-Key": idempotencyKey
      },
      body: JSON.stringify({
        title,
        team_id,
        item_type,
        priority,
        assignee_id,
        description
      })
    });
    showToast("✨", `Created work item #${created.id}`);
    closeCreateModal();
    loadItems();
    loadStats();
  } catch (err) {
    // Handled in api()
  }
};

// Filter Change Handlers
document.getElementById("teamFilter").onchange = (e) => {
  state.team_id = parseInt(e.target.value);
  loadItems(null);
};

document.getElementById("statusFilter").onchange = (e) => {
  state.status = e.target.value;
  loadItems(null);
};

document.getElementById("priorityFilter").onchange = (e) => {
  state.priority = e.target.value;
  loadItems(null);
};

document.getElementById("assigneeFilter").onchange = (e) => {
  state.assignee = e.target.value;
  loadItems(null);
};

document.getElementById("searchInput").oninput = (e) => {
  clearTimeout(searchDebounceTimeout);
  searchDebounceTimeout = setTimeout(() => {
    state.q = e.target.value.trim();
    loadItems(null);
  }, 300);
};

function setNeedsAttentionFilter(tabName) {
  state.needs_attention = tabName;

  // Update tab UI styling
  const buttons = document.querySelectorAll("#attentionTabs button");
  buttons.forEach(btn => {
    const tab = btn.getAttribute("data-tab");
    if ((!tabName && tab === "all") || tab === tabName) {
      btn.className = "px-3 py-1.5 rounded-md font-medium bg-indigo-600 text-white shadow";
    } else {
      btn.className = "px-3 py-1.5 rounded-md font-medium text-slate-400 hover:text-white";
    }
  });

  loadItems(null);
}

// Start app on page load
window.addEventListener("DOMContentLoaded", () => {
  if (token) {
    document.getElementById("loginScreen").classList.add("hidden");
    initApp();
  } else {
    document.getElementById("loginScreen").classList.remove("hidden");
  }
});
