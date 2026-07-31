"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  bootstrap: null,
  workers: [],
  selectedFarm: null,
  selectedWorker: null,
  selectedDecision: "approve_merge",
  pollTimer: null,
  planPollTimer: null,
  windowChromeBound: false,
  settings: null,
  providerSecretDrafts: {},
  providerCatalogs: {},
  settingsDirty: false,
  settingsSection: "agents",
  appearanceTheme: "system",
  activeThreadId: null,
  activeTurnId: null,
  shellBound: false,
  activeView: "workspace",
};

const APP_VIEWS = new Set(["workspace", "history", "profiles", "settings"]);
const API_TIMEOUT_MS = 15000;
const BUILT_IN_PROVIDER_LABELS = Object.freeze({
  openai: "OpenAI",
  ollama: "Ollama",
  lmstudio: "LM Studio",
});

window.setAgentFarmMaximized = (maximized) => {
  const isMaximized = Boolean(maximized);
  document.body.classList.toggle("window-maximized", isMaximized);
  const button = document.querySelector("#maximize-window");
  if (button) {
    button.title = isMaximized ? "Restore" : "Maximize";
    button.setAttribute("aria-label", button.title);
  }
};

function setTheme(theme, persist = false) {
  const selected = ["system", "light", "dark"].includes(theme) ? theme : "system";
  const isLight = selected === "light" || (selected === "system" && window.matchMedia?.("(prefers-color-scheme: light)").matches);
  state.appearanceTheme = selected;
  document.body.classList.toggle("light", isLight);
  document.documentElement.style.colorScheme = isLight ? "light" : "dark";
  const button = $("#theme-button");
  if (button) {
    button.title = isLight ? "Use dark appearance" : "Use light appearance";
    button.setAttribute("aria-label", button.title);
  }
  if (persist) {
    try { localStorage.setItem("agent-farm-theme", selected); } catch { /* Storage may be disabled. */ }
  }
  const appearance = $("#settings-appearance");
  if (appearance) appearance.value = selected;
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("agent-farm-theme"); } catch { /* Use system preference. */ }
  setTheme(["system", "light", "dark"].includes(saved) ? saved : "system");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function splitLines(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || API_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      signal: options.signal || controller.signal,
      headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
    });
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) throw new Error(payload?.error?.message || `Request failed (${response.status})`);
    return payload;
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("The local Agent Farm service did not respond in time.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 4200);
}

function setAppBanner(message = "", isError = false) {
  const banner = $("#app-status-banner");
  if (!banner) return;
  banner.classList.toggle("hidden", !message);
  banner.classList.toggle("error", Boolean(isError));
  $("#app-status-copy", banner).textContent = message;
}

async function desktopWindowAction(action) {
  const bridge = window.pywebview?.api;
  if (!bridge?.window_action) return;
  try {
    const result = await bridge.window_action(action);
    if (result && typeof result.maximized === "boolean") {
      window.setAgentFarmMaximized(result.maximized);
    }
  } catch (error) {
    if (action !== "close") toast(`Window action failed: ${error.message || error}`, true);
  }
}

function initWindowChrome() {
  if (state.windowChromeBound || !document.body.classList.contains("desktop-runtime")) return;
  const bridge = window.pywebview?.api;
  if (!bridge) return;
  state.windowChromeBound = true;

  if (bridge.window_state) {
    bridge.window_state()
      .then((result) => window.setAgentFarmMaximized(Boolean(result?.maximized)))
      .catch(() => { /* Native state will be synchronized by later window events. */ });
  }

  $("#minimize-window").addEventListener("click", () => desktopWindowAction("minimize"));
  $("#maximize-window").addEventListener("click", () => desktopWindowAction("toggle_maximize"));
  $("#close-window").addEventListener("click", () => desktopWindowAction("close"));
  $("#window-titlebar").addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) desktopWindowAction("toggle_maximize");
  });

  let activeResize = null;
  let latestPoint = null;
  let resizeBusy = false;

  async function flushResize() {
    if (resizeBusy || !latestPoint || !activeResize) return;
    resizeBusy = true;
    try {
      while (latestPoint && activeResize) {
        const point = latestPoint;
        latestPoint = null;
        await bridge.resize_window(point.x, point.y);
      }
    } catch (error) {
      activeResize = null;
      toast(`Window resize failed: ${error.message || error}`, true);
    } finally {
      resizeBusy = false;
    }
  }

  function onResizeMove(event) {
    if (!activeResize) return;
    latestPoint = { x: event.screenX, y: event.screenY };
    requestAnimationFrame(flushResize);
  }

  async function onResizeEnd() {
    if (!activeResize) return;
    activeResize = null;
    latestPoint = null;
    window.removeEventListener("pointermove", onResizeMove);
    window.removeEventListener("pointerup", onResizeEnd);
    try { await bridge.end_resize(); } catch { /* Window may be closing. */ }
  }

  $$("[data-resize-edge]").forEach((handle) => handle.addEventListener("pointerdown", async (event) => {
    if (event.button !== 0 || document.body.classList.contains("window-maximized")) return;
    event.preventDefault();
    try {
      const result = await bridge.begin_resize(handle.dataset.resizeEdge, event.screenX, event.screenY);
      if (!result?.resizing) return;
      activeResize = handle.dataset.resizeEdge;
      window.addEventListener("pointermove", onResizeMove);
      window.addEventListener("pointerup", onResizeEnd, { once: true });
    } catch (error) {
      toast(`Could not resize the window: ${error.message || error}`, true);
    }
  }));
}

function shortCommit(value) {
  return value ? String(value).slice(0, 8) : "—";
}

function readableStatus(value) {
  const labels = {
    QUEUED: "Queued", RUNNING: "Running", COMPLETED: "Completed", FAILED: "Failed",
    SUPERVISOR_REVIEW_PENDING: "Awaiting review", SUPERVISOR_APPROVED: "Approved",
    REVISION_REQUESTED: "Revision requested", REJECTED: "Rejected", HOLD_FOR_USER: "Waiting for user",
    FAILED_TO_RUN: "Failed to start",
    idle: "Idle", planning: "Planning", awaiting_confirmation: "Plan ready", running: "Running",
    awaiting_review: "Awaiting review", awaiting_user: "Waiting for user", completed: "Completed", failed: "Failed",
  };
  return labels[value] || value || "Unknown";
}

function locationRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const view = APP_VIEWS.has(parts[0]) ? parts[0] : "workspace";
  const section = SETTINGS_SECTION_COPY[parts[1]] ? parts[1] : "agents";
  return { view, section };
}

function routeHash(name, section = state.settingsSection) {
  return name === "settings" ? `#/settings/${section}` : `#/${name}`;
}

function setView(name, { updateLocation = true, replace = false } = {}) {
  const target = APP_VIEWS.has(name) ? name : "workspace";
  state.activeView = target;
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `${target}-view`));
  $$(".nav-item").forEach((item) => {
    const active = item.dataset.view === target;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  const titles = { workspace: "New task", history: "Run history", profiles: "Model routes", settings: "Settings" };
  $("#view-title").textContent = titles[target] || "New task";
  document.body.classList.remove("menu-open");
  if (updateLocation) {
    const nextHash = routeHash(target);
    if (location.hash !== nextHash) {
      if (replace) history.replaceState(null, "", nextHash);
      else history.pushState(null, "", nextHash);
    }
  }
  if (target === "history") renderHistory();
  if (target === "profiles") renderProfiles();
  if (target === "settings") loadSettings().catch(() => { /* Inline recovery UI is rendered by loadSettings. */ });
}

function applyLocationRoute() {
  const route = locationRoute();
  if (route.view === "settings") setSettingsSection(route.section, { updateLocation: false });
  setView(route.view, { updateLocation: false });
}

function resetThread() {
  clearTimeout(state.pollTimer);
  clearTimeout(state.planPollTimer);
  state.selectedFarm = null;
  state.selectedWorker = null;
  state.workers = [];
  state.activeThreadId = null;
  state.activeTurnId = null;
  $("#mission-prompt").value = "";
  $("#task-id").value = "";
  $("#base-ref").value = "HEAD";
  $("#max-parallel").value = Math.min(3, state.bootstrap?.limits?.max_parallel_workers || 3);
  $("#deliverable-path").value = "";
  $("#deliverable-instructions").value = "";
  $("#welcome-state").classList.remove("hidden");
  $("#user-turn").classList.add("hidden");
  $("#mission-status").classList.add("hidden");
  $("#composer-card").classList.add("hidden");
  $("#live-section").classList.add("hidden");
  $("#inspector").classList.remove("open");
  $("#view-title").textContent = "New task";
  addWorker({
    id: "implementation",
    role: "Implementation agent",
    allowed_paths: state.bootstrap?.defaults?.allowed_paths || [],
    test_commands: state.bootstrap?.defaults?.test_commands || [],
  });
  $("#mission-prompt").focus();
}

function profiles() {
  return state.bootstrap?.profiles || [];
}

function profileDisplayName(profileId) {
  const profile = profiles().find((item) => item.name === profileId);
  return profile?.display_name || profile?.name || profileId;
}

function defaultProfile() {
  return profiles().find((profile) => profile.is_default)?.name || profiles()[0]?.name || "default";
}

function addWorker(seed = {}) {
  const number = state.workers.length + 1;
  const worker = {
    key: crypto.randomUUID(),
    id: seed.id || `worker-${number}`,
    role: seed.role || "Implementation agent",
    profile: seed.profile || defaultProfile(),
    goal: seed.goal || "",
    allowed_paths: seed.allowed_paths || [],
    test_commands: seed.test_commands || [],
    acceptance: seed.acceptance || [],
    forbidden_paths: seed.forbidden_paths || [],
    context: seed.context || "",
  };
  state.workers.push(worker);
  renderWorkers();
}

function syncWorkersFromForm() {
  $$(".worker-card", $("#worker-list")).forEach((card) => {
    const worker = state.workers.find((item) => item.key === card.dataset.key);
    if (!worker) return;
    ["id", "role", "profile", "goal", "context"].forEach((field) => {
      worker[field] = $(`[data-field="${field}"]`, card).value.trim();
    });
    ["allowed_paths", "test_commands", "acceptance"].forEach((field) => {
      worker[field] = splitLines($(`[data-field="${field}"]`, card).value);
    });
  });
}

function renderWorkers() {
  const list = $("#worker-list");
  list.innerHTML = "";
  state.workers.forEach((worker, index) => {
    const fragment = $("#worker-template").content.cloneNode(true);
    const card = $(".worker-card", fragment);
    card.dataset.key = worker.key;
    $(".worker-index", card).textContent = String(index + 1).padStart(2, "0");
    ["id", "role", "goal", "context"].forEach((field) => {
      $(`[data-field="${field}"]`, card).value = worker[field];
    });
    ["allowed_paths", "test_commands", "acceptance"].forEach((field) => {
      $(`[data-field="${field}"]`, card).value = worker[field].join("\n");
    });
    const select = $("[data-field=" + '"profile"' + "]", card);
    const availableProfiles = profiles().length ? profiles() : [{ name: "default", model: "Model required" }];
    select.innerHTML = availableProfiles.map((profile) =>
      `<option value="${escapeHtml(profile.name)}">${escapeHtml(profile.display_name || profile.name)} · ${escapeHtml(profile.model)}</option>`
    ).join("");
    select.value = worker.profile;
    $(".remove-worker", card).addEventListener("click", () => {
      syncWorkersFromForm();
      if (state.workers.length === 1) return toast("Keep at least one Worker.", true);
      state.workers = state.workers.filter((item) => item.key !== worker.key);
      renderWorkers();
    });
    $(".details-toggle", card).addEventListener("click", (event) => {
      card.classList.toggle("expanded");
      event.currentTarget.firstChild.textContent = card.classList.contains("expanded") ? "Hide execution boundaries " : "Show execution boundaries ";
    });
    list.append(fragment);
  });
}

function loadExample() {
  state.workers = [];
  $("#task-id").value = "ship-settings-console";
  $("#base-ref").value = "HEAD";
  $("#max-parallel").value = Math.min(3, state.bootstrap?.limits?.max_parallel_workers || 3);
  $("#deliverable-path").value = "";
  $("#deliverable-instructions").value = "";
  addWorker({ id: "frontend", role: "Frontend engineer", goal: "Build a responsive settings interface and connect it to the existing API.", allowed_paths: ["agent_farm/web/**"], test_commands: ["python -m unittest discover -s tests"], acceptance: ["Works at desktop and compact window sizes", "Adds no external runtime dependency"], context: "Preserve the current visual language and keyboard accessibility." });
  addWorker({ id: "backend", role: "Backend engineer", goal: "Implement the settings endpoint and input validation.", allowed_paths: ["agent_farm/**", "tests/**"], test_commands: ["python -m unittest discover -s tests"], acceptance: ["The endpoint never returns secrets", "Invalid input produces a clear 4xx response"] });
  addWorker({ id: "reviewer", role: "Test and review agent", goal: "Review the implementation and add critical regression coverage.", allowed_paths: ["tests/**"], test_commands: ["python -m unittest discover -s tests"], acceptance: ["Covers security boundaries and failure paths"] });
  $("#welcome-state").classList.add("hidden");
  $("#composer-card").classList.remove("hidden");
}

async function ensureActiveThread(title) {
  if (state.activeThreadId) return { thread_id: state.activeThreadId };
  const thread = await api("/api/threads", {
    method: "POST",
    body: JSON.stringify({ title: title || "New task" }),
  });
  state.activeThreadId = thread.thread_id;
  return thread;
}

async function openThread(threadId) {
  try {
    const thread = await api(`/api/threads/${encodeURIComponent(threadId)}`);
    setView("workspace");
    resetThread();
    state.activeThreadId = thread.thread_id;
    const turn = thread.turns?.at(-1);
    state.activeTurnId = turn?.turn_id || null;
    $("#view-title").textContent = thread.title || "Task thread";
    if (!turn) return;
    const userItem = turn.items?.find((item) => item.type === "user_message");
    const planItem = [...(turn.items || [])].reverse().find((item) => item.type === "supervisor_plan" && item.payload?.plan);
    const farmItem = [...(turn.items || [])].reverse().find((item) => item.type === "farm_run" && item.payload?.farm_id);
    if (userItem?.payload?.text) {
      $("#mission-prompt").value = userItem.payload.text;
      $("#user-turn-copy").textContent = userItem.payload.text;
      $("#welcome-state").classList.add("hidden");
      $("#user-turn").classList.remove("hidden");
    }
    if (planItem) applyPlan(planItem.payload.plan);
    if (farmItem) await openFarm(farmItem.payload.farm_id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function draftPlan() {
  const request = $("#mission-prompt").value.trim();
  if (!request) return toast("Describe what you want to accomplish first.", true);
  const button = $("#draft-plan-button");
  try {
    const maxWorkers = Math.min(Number($("#max-parallel").value) || 3, 12);
    const thread = await ensureActiveThread(request);
    const payload = {
      request,
      task_id: $("#task-id").value.trim() || null,
      base_ref: $("#base-ref").value.trim() || "HEAD",
      worker_count: maxWorkers,
      thread_id: thread.thread_id,
    };
    button.disabled = true;
    $("span", button).textContent = "Supervisor is planning…";
    $("#welcome-state").classList.add("hidden");
    $("#user-turn-copy").textContent = request;
    $("#user-turn").classList.remove("hidden");
    $("#mission-status").classList.remove("hidden");
    $("#user-turn").scrollIntoView({ behavior: "smooth", block: "start" });
    const job = await api("/api/plans", { method: "POST", body: JSON.stringify(payload) });
    state.activeTurnId = job.turn_id;
    pollPlanJob(job.job_id);
  } catch (error) {
    button.disabled = false;
    $("span", button).textContent = "Ask the Supervisor to plan";
    $("#mission-status").classList.add("hidden");
    toast(error.message, true);
  }
}

async function pollPlanJob(jobId) {
  clearTimeout(state.planPollTimer);
  try {
    const job = await api(`/api/plan-jobs/${encodeURIComponent(jobId)}`);
    if (job.thread_id) state.activeThreadId = job.thread_id;
    if (job.turn_id) state.activeTurnId = job.turn_id;
    if (job.status === "QUEUED") {
      $("#mission-status-title").textContent = "Supervisor is queued";
      $("#mission-status-copy").textContent = "Planning requests run serially to prevent accidental high-cost model usage.";
    } else if (job.status === "RUNNING") {
      $("#mission-status-title").textContent = "Supervisor is reading the repository";
      $("#mission-status-copy").textContent = "Understanding boundaries, selecting model profiles, and drafting acceptance criteria in read-only mode…";
    } else if (job.status === "COMPLETED") {
      applyPlan(job.plan);
      $("#mission-status").classList.add("hidden");
      const button = $("#draft-plan-button");
      button.disabled = false;
      $("span", button).textContent = "Plan again";
      toast("The Worker Plan is ready. Review it before execution." );
      $("#composer-card").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    } else if (job.status === "FAILED") {
      throw new Error(job.error?.message || "Supervisor planning failed.");
    }
    state.planPollTimer = setTimeout(() => pollPlanJob(jobId), 1500);
  } catch (error) {
    const button = $("#draft-plan-button");
    button.disabled = false;
    $("span", button).textContent = "Ask the Supervisor to plan";
    $("#mission-status").classList.add("hidden");
    toast(error.message, true);
  }
}

function applyPlan(plan) {
  if (!plan || !Array.isArray(plan.workers)) throw new Error("The Supervisor Plan does not contain workers.");
  $("#task-id").value = plan.task_id;
  $("#base-ref").value = plan.base_ref || "HEAD";
  if (plan.max_parallel) $("#max-parallel").value = plan.max_parallel;
  $("#deliverable-path").value = plan.deliverable?.path || "";
  $("#deliverable-instructions").value = plan.deliverable?.instructions || "";
  state.workers = plan.workers.map((worker) => ({
    key: crypto.randomUUID(),
    id: worker.id,
    role: worker.role,
    profile: worker.profile,
    goal: worker.goal,
    allowed_paths: worker.allowed_paths || [],
    forbidden_paths: worker.forbidden_paths || [],
    test_commands: worker.test_commands || [],
    acceptance: worker.acceptance || [],
    context: worker.context || "",
  }));
  renderWorkers();
  $("#composer-card").classList.remove("hidden");
  $("#view-title").textContent = plan.task_id || "Execution plan";
  $$(".worker-card").forEach((card) => card.classList.add("expanded"));
}

function buildPlan() {
  syncWorkersFromForm();
  const taskId = $("#task-id").value.trim();
  if (!taskId) throw new Error("Enter a task name.");
  if (!state.workers.length) throw new Error("At least one Worker is required.");
  const workers = state.workers.map(({ key, ...worker }) => worker);
  workers.forEach((worker, index) => {
    if (!worker.id || !worker.role || !worker.profile || !worker.goal) throw new Error(`Worker ${index + 1} requires an ID, role, model profile, and goal.`);
    if (!worker.allowed_paths.length && !(state.bootstrap?.defaults?.allowed_paths || []).length) throw new Error(`Worker ${worker.id} requires at least one allowed path.`);
  });
  const plan = { schema_version: 1, task_id: taskId, base_ref: $("#base-ref").value.trim() || "HEAD", workers };
  const deliverablePath = $("#deliverable-path").value.trim();
  const deliverableInstructions = $("#deliverable-instructions").value.trim();
  if (deliverablePath || deliverableInstructions) {
    if (!deliverablePath || !deliverableInstructions) throw new Error("Final deliverable path and synthesis instructions are both required.");
    plan.deliverable = { path: deliverablePath, instructions: deliverableInstructions };
  }
  const maxParallel = Number($("#max-parallel").value);
  if (Number.isInteger(maxParallel) && maxParallel > 0) plan.max_parallel = maxParallel;
  return plan;
}

async function launchFarm() {
  const button = $("#launch-button");
  try {
    const plan = buildPlan();
    const thread = await ensureActiveThread($("#mission-prompt").value.trim() || plan.task_id);
    button.disabled = true;
    $("span", button).textContent = "Submitting…";
    const submission = { plan, thread_id: thread.thread_id, turn_id: state.activeTurnId };
    const job = await api("/api/farms", { method: "POST", body: JSON.stringify(submission) });
    state.activeTurnId = job.turn_id;
    showLiveJob(job, plan.workers);
    toast("The Farm is queued for background execution.");
    pollJob(job.job_id);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    $("span", button).textContent = "Start execution";
  }
}

function showLiveJob(job, workers = state.workers) {
  $("#live-section").classList.remove("hidden");
  $("#live-title").textContent = job.task_id || "Task in progress";
  const badge = $("#live-status");
  badge.textContent = readableStatus(job.status);
  badge.dataset.status = job.status;
  $("#live-progress").style.width = job.status === "QUEUED" ? "12%" : job.status === "RUNNING" ? "56%" : "100%";
  $("#live-workers").innerHTML = workers.map((worker) => `
    <div class="live-worker"><div><strong>${escapeHtml(worker.role)}</strong><p>${escapeHtml(profileDisplayName(worker.profile))} · ${escapeHtml(worker.id)}</p></div><span class="status-badge" data-status="${escapeHtml(job.status)}">${readableStatus(job.status)}</span></div>
  `).join("");
  $("#live-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (job.thread_id) state.activeThreadId = job.thread_id;
    if (job.turn_id) state.activeTurnId = job.turn_id;
    showLiveJob(job);
    if (job.status === "COMPLETED") {
      await refreshBootstrap();
      toast("Farm execution completed. Open the result to inspect Worker evidence and the final deliverable.");
      if (job.farm_id) await openFarm(job.farm_id);
      return;
    }
    if (job.status === "FAILED") {
      toast(job.error?.message || "Farm execution failed.", true);
      return;
    }
    state.pollTimer = setTimeout(() => pollJob(jobId), 1800);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderRecent() {
  const farms = state.bootstrap?.farms || [];
  const threads = state.bootstrap?.threads || [];
  $("#farm-count").textContent = farms.length;
  const recent = $("#recent-farms");
  recent.innerHTML = threads.slice(0, 8).map((thread) => `
    <button class="recent-item" data-thread="${escapeHtml(thread.thread_id)}" data-status="${escapeHtml(thread.status)}"><span></span><span>${escapeHtml(thread.title)}</span></button>
  `).join("") || farms.slice(0, 6).map((farm) => `
    <button class="recent-item" data-farm="${escapeHtml(farm.farm_id)}" data-status="${escapeHtml(farm.status)}"><span></span><span>${escapeHtml(farm.plan_task_id || farm.farm_id)}</span></button>
  `).join("") || '<div class="empty-list">No task threads yet</div>';
  $$('[data-thread]', recent).forEach((button) => button.addEventListener("click", () => openThread(button.dataset.thread)));
  $$('[data-farm]', recent).forEach((button) => button.addEventListener("click", () => openFarm(button.dataset.farm)));
}

function renderHistory() {
  const farms = state.bootstrap?.farms || [];
  $("#history-list").innerHTML = farms.map((farm) => `
    <button class="history-row" data-farm="${escapeHtml(farm.farm_id)}">
      <span class="history-task"><strong>${escapeHtml(farm.plan_task_id || farm.farm_id)}</strong><small>${escapeHtml(farm.farm_id)}</small></span>
      <span><i class="status-badge" data-status="${escapeHtml(farm.status)}">${escapeHtml(readableStatus(farm.status))}</i></span>
      <span class="history-cell">${escapeHtml(farm.passed_workers)} / ${escapeHtml(farm.worker_count)}</span>
      <span class="history-cell">${escapeHtml(shortCommit(farm.base_commit))}</span>
      <svg class="history-arrow" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>
    </button>
  `).join("") || '<div class="empty-list">No Farm runs yet.</div>';
  $$('[data-farm]', $("#history-list")).forEach((button) => button.addEventListener("click", () => openFarm(button.dataset.farm)));
}

function renderProfiles() {
  $("#profile-grid").innerHTML = profiles().map((profile) => `
    <article class="profile-card">
      <div class="profile-label">${profile.is_default ? "DEFAULT ROUTE" : "WORKER PROFILE"}</div>
      <h3>${escapeHtml(profile.display_name || profile.name)}</h3>
      <p>${escapeHtml(profile.model)}</p>
      <div class="profile-facts"><div><span>PROVIDER</span><strong>${escapeHtml(profile.provider_name || profile.provider)}</strong></div><div><span>TIMEOUT</span><strong>${escapeHtml(profile.timeout_seconds)} sec</strong></div></div>
    </article>
  `).join("") || '<div class="empty-list">No model profiles are available.</div>';
}

const SETTINGS_SECTION_COPY = {
  agents: ["Agents and models", "Route expensive planning and review separately from economical execution."],
  providers: ["Providers", "Connect Worker routes to hosted, proxy, or local model endpoints."],
  safety: ["Safety", "Control command permissions, network access, and review boundaries."],
  storage: ["Worktrees and limits", "Choose where isolated work and evidence live, then bound each run."],
  general: ["General", "Manage the desktop appearance and autonomous Agent runtime."],
};

function setSettingsSection(section, { updateLocation = true } = {}) {
  if (!SETTINGS_SECTION_COPY[section]) return;
  if (state.settingsSection === "providers" && section !== "providers" && state.settings) {
    try { syncProviderDraftToRoutes(); }
    catch (error) { toast(error.message, true); }
  }
  state.settingsSection = section;
  $$("[data-settings-section]").forEach((button) => {
    const active = button.dataset.settingsSection === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$("[data-settings-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.settingsPanel === section));
  const [title, copy] = SETTINGS_SECTION_COPY[section];
  $("#settings-section-title").textContent = title;
  $("#settings-section-copy").textContent = copy;
  if (updateLocation && state.activeView === "settings") {
    const nextHash = routeHash("settings", section);
    if (location.hash !== nextHash) history.pushState(null, "", nextHash);
  }
}

function setSettingsFeedback(kind = "", title = "", message = "") {
  const feedback = $("#settings-feedback");
  if (!feedback) return;
  feedback.className = `settings-feedback${kind ? ` ${kind}` : " hidden"}`;
  $("#settings-feedback-title", feedback).textContent = title;
  $("#settings-feedback-copy", feedback).textContent = message;
  $("#settings-retry-button", feedback).classList.toggle("hidden", kind !== "error");
}

function setSettingsSaveState(message, status = "") {
  const target = $("#settings-save-state");
  target.textContent = message;
  target.className = status;
}

function markSettingsDirty() {
  if (!state.settings) return;
  state.settingsDirty = true;
  setSettingsSaveState("Unsaved changes", "dirty");
}

function nullableInput(selector) {
  const value = $(selector).value.trim();
  return value || null;
}

function positiveInteger(selector, label) {
  const value = Number.parseInt($(selector).value, 10);
  if (!Number.isInteger(value) || value < 1) throw new Error(`${label} must be a positive integer.`);
  return value;
}

function selectOptions(values, selected, labels = {}) {
  return values.map((value) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(labels[value] || value)}</option>`).join("");
}

function providerChoices(config, selected = "", { includeDefault = false } = {}) {
  const configured = config.model_providers || {};
  const customIds = Object.keys(configured)
    .filter((providerId) => !Object.hasOwn(BUILT_IN_PROVIDER_LABELS, providerId))
    .sort((left, right) => left.localeCompare(right));
  const values = [...Object.keys(BUILT_IN_PROVIDER_LABELS), ...customIds];
  if (selected && !values.includes(selected)) values.push(selected);
  const labels = {};
  Object.entries(BUILT_IN_PROVIDER_LABELS).forEach(([providerId, label]) => { labels[providerId] = label; });
  Object.entries(configured).forEach(([providerId, provider]) => {
    const displayName = String(provider?.name || "").trim();
    labels[providerId] = displayName && displayName !== providerId ? `${displayName} (${providerId})` : providerId;
  });
  if (selected && !Object.hasOwn(BUILT_IN_PROVIDER_LABELS, selected) && !Object.hasOwn(configured, selected)) {
    labels[selected] = `${selected} (not configured)`;
  }
  if (includeDefault) {
    values.unshift("");
    labels[""] = "OpenAI (default)";
  }
  return { values, labels };
}

function providerSelectOptions(config, selected = "", options = {}) {
  const choices = providerChoices(config, selected, options);
  return selectOptions(choices.values, selected, choices.labels);
}

function providerTemplateForRoute(config, providerId) {
  providerId ||= "openai";
  const templates = state.settings?.provider_templates || [];
  const provider = config.model_providers?.[providerId] || {};
  const normalizedBaseUrl = String(provider.base_url || "").replace(/\/$/, "").toLowerCase();
  const templateId = String(provider.template_id || "");
  return templates.find((template) => {
    const templateBaseUrl = String(template.base_url || "").replace(/\/$/, "").toLowerCase();
    return template.id === templateId
      || template.id === providerId
      || (normalizedBaseUrl && templateBaseUrl === normalizedBaseUrl);
  }) || null;
}

function providerUsesOfficialCatalog(config, providerId) {
  providerId ||= "openai";
  const template = providerTemplateForRoute(config, providerId);
  return Boolean(template && !template.custom && template.model_catalog?.mode === "live");
}

function providerCatalogModels(config, providerId) {
  providerId ||= "openai";
  const catalog = state.providerCatalogs[providerId]?.catalog;
  if (Array.isArray(catalog?.models)) return catalog.models;
  const template = providerTemplateForRoute(config, providerId);
  return Array.isArray(template?.models) ? template.models.filter((model) => model?.id) : [];
}

function modelCatalogNote(config, providerId) {
  providerId ||= "openai";
  if (!providerUsesOfficialCatalog(config, providerId)) return "Custom compatible route · enter the exact Model ID.";
  const entry = state.providerCatalogs[providerId];
  if (!entry || entry.status === "loading") return "Loading every compatible model available to this provider account…";
  if (entry.status === "error") return entry.error || "Models could not be loaded.";
  const count = entry.catalog?.model_count ?? entry.catalog?.models?.length ?? 0;
  if (entry.catalog?.source === "fallback") return `${count} verified fallback models · live catalog unavailable.`;
  return `${count} available models · loaded from the provider.`;
}

function modelPickerContents(config, providerId, selectedModel, fieldAttribute) {
  providerId ||= "openai";
  const official = providerUsesOfficialCatalog(config, providerId);
  if (!official) {
    return `<input ${fieldAttribute} data-model-provider="${escapeHtml(providerId)}" value="${escapeHtml(selectedModel || "")}" placeholder="Model ID" spellcheck="false"><span class="model-catalog-note">${escapeHtml(modelCatalogNote(config, providerId))}</span>`;
  }
  const entry = state.providerCatalogs[providerId];
  const models = providerCatalogModels(config, providerId);
  const selectedAvailable = models.some((model) => model.id === selectedModel);
  let options = "";
  if (selectedModel && !selectedAvailable) {
    options += `<option value="${escapeHtml(selectedModel)}" selected>${escapeHtml(selectedModel)} (currently selected)</option>`;
  }
  options += models.map((model) => `<option value="${escapeHtml(model.id)}"${model.id === selectedModel ? " selected" : ""}>${escapeHtml(model.name && model.name !== model.id ? `${model.name} — ${model.id}` : model.id)}</option>`).join("");
  if (!options) options = `<option value="">${entry?.status === "error" ? "Models unavailable — refresh" : "Loading models…"}</option>`;
  const noteClass = entry?.status === "error" ? " error" : "";
  return `<select ${fieldAttribute} data-model-provider="${escapeHtml(providerId)}">${options}</select><button class="model-refresh-button" type="button" data-refresh-models="${escapeHtml(providerId)}" title="Refresh model list" aria-label="Refresh model list"${entry?.status === "loading" ? " disabled" : ""}><svg viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M18.2 9A7 7 0 0 0 6.1 6.1L4 8m16 8-2.1 1.9A7 7 0 0 1 5.8 15"/></svg></button><span class="model-catalog-note${noteClass}">${escapeHtml(modelCatalogNote(config, providerId))}</span>`;
}

function modelReasoningCapability(config, providerId, modelId) {
  providerId ||= "openai";
  const model = providerCatalogModels(config, providerId).find((item) => item.id === modelId);
  if (model?.reasoning) return model.reasoning;
  const template = providerTemplateForRoute(config, providerId);
  return template?.reasoning || { efforts: [], thinking: [] };
}

function normalizeReasoningValues(config, providerId, capability, mode, effort) {
  providerId ||= "openai";
  const efforts = Array.isArray(capability?.efforts) ? capability.efforts : [];
  const thinking = Array.isArray(capability?.thinking) ? capability.thinking : [];
  let selectedEffort = efforts.includes(effort) ? effort : "";
  if (providerTemplateForRoute(config, providerId)?.id === "deepseek" && effort === "xhigh") selectedEffort = "max";
  return {
    mode: thinking.includes(mode) ? mode : "",
    effort: selectedEffort,
    efforts,
    thinking,
  };
}

function workerReasoningMarkup(config, providerId, modelId, mode, effort) {
  const values = normalizeReasoningValues(config, providerId, modelReasoningCapability(config, providerId, modelId), mode, effort);
  const thinking = values.thinking.length
    ? `<label class="route-field"><span>Thinking</span><select data-profile-field="reasoning_mode"><option value="">Automatic</option>${selectOptions(values.thinking, values.mode, { enabled: "Enabled", disabled: "Disabled" })}</select></label>`
    : "";
  const effortControl = values.efforts.length
    ? `<label class="route-field"><span>Reasoning effort</span><select data-profile-field="reasoning_effort"><option value="">Automatic</option>${selectOptions(values.efforts, values.effort, { none: "None", default: "Default", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "XHigh", max: "Max" })}</select></label>`
    : "";
  return thinking || effortControl
    ? thinking + effortControl
    : '<label class="route-field"><span>Reasoning</span><select disabled><option>Provider / model default</option></select></label>';
}

function renderSupervisorReasoning(config, providerId, modelId, mode = "", effort = "") {
  const container = $("#settings-supervisor-reasoning");
  if (!container) return;
  const values = normalizeReasoningValues(config, providerId, modelReasoningCapability(config, providerId, modelId), mode, effort);
  const rows = [];
  if (values.thinking.length) rows.push(`<div class="settings-row"><div><strong>Thinking</strong><small>Uses this provider's native thinking control.</small></div><select id="settings-supervisor-reasoning-mode"><option value="">Automatic</option>${selectOptions(values.thinking, values.mode, { enabled: "Enabled", disabled: "Disabled" })}</select></div>`);
  if (values.efforts.length) rows.push(`<div class="settings-row"><div><strong>Reasoning effort</strong><small>Only levels supported by the selected model are shown.</small></div><select id="settings-supervisor-reasoning-effort"><option value="">Automatic</option>${selectOptions(values.efforts, values.effort, { none: "None", default: "Default", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "XHigh", max: "Max" })}</select></div>`);
  container.innerHTML = rows.join("");
}

function rerenderWorkerReasoning(card, mode = null, effort = null) {
  if (!card || !state.settings) return;
  const config = state.settings.config;
  const providerId = $("[data-profile-field='provider']", card)?.value || "";
  const modelId = $("[data-profile-field='model']", card)?.value || "";
  const container = $("[data-reasoning-container]", card);
  if (!container) return;
  const currentMode = mode ?? $("[data-profile-field='reasoning_mode']", card)?.value ?? "";
  const currentEffort = effort ?? $("[data-profile-field='reasoning_effort']", card)?.value ?? "";
  container.innerHTML = workerReasoningMarkup(config, providerId, modelId, currentMode, currentEffort);
}

function rerenderModelPickers(config, providerId) {
  const supervisorPicker = $("#settings-supervisor-model-picker");
  if (supervisorPicker?.dataset.modelPickerProvider === providerId) {
    const current = $("#settings-supervisor-model")?.value || config.supervisor_model || "";
    const currentMode = $("#settings-supervisor-reasoning-mode")?.value || config.supervisor_reasoning_mode || "";
    const currentEffort = $("#settings-supervisor-reasoning-effort")?.value || config.supervisor_reasoning_effort || "";
    supervisorPicker.innerHTML = modelPickerContents(config, providerId, current, 'id="settings-supervisor-model"');
    renderSupervisorReasoning(config, providerId, $("#settings-supervisor-model")?.value || current, currentMode, currentEffort);
  }
  $$("[data-profile-name]", $("#settings-profile-list")).forEach((card) => {
    const picker = $("[data-model-picker-provider]", card);
    if (picker?.dataset.modelPickerProvider !== providerId) return;
    const current = $("[data-profile-field='model']", card)?.value || "";
    picker.innerHTML = modelPickerContents(config, providerId, current, 'data-profile-field="model"');
    rerenderWorkerReasoning(card);
  });
}

async function loadProviderCatalog(providerId, { refresh = false } = {}) {
  providerId ||= "openai";
  const config = state.settings?.config;
  if (!config || !providerUsesOfficialCatalog(config, providerId)) return;
  if (!refresh && ["loading", "ready"].includes(state.providerCatalogs[providerId]?.status)) return;
  state.providerCatalogs[providerId] = { status: "loading" };
  rerenderModelPickers(config, providerId);
  try {
    const suffix = refresh ? "?refresh=1" : "";
    const catalog = await api(`/api/providers/${encodeURIComponent(providerId)}/models${suffix}`);
    state.providerCatalogs[providerId] = { status: "ready", catalog };
  } catch (error) {
    state.providerCatalogs[providerId] = { status: "error", error: error.message };
  }
  rerenderModelPickers(config, providerId);
}

function applyProviderModelDefault(providerSelect) {
  if (!state.settings) return;
  const config = state.settings.config;
  const providerId = providerSelect.value || "openai";
  const template = providerTemplateForRoute(config, providerId);
  const routeCard = providerSelect.closest("[data-profile-name]");
  const modelInput = providerSelect.id === "settings-supervisor-provider"
    ? $("#settings-supervisor-model")
    : $("[data-profile-field='model']", routeCard);
  const previousProvider = providerSelect.dataset.previousProvider || "";
  const previousModels = providerCatalogModels(config, previousProvider).map((model) => model.id);
  const currentModel = modelInput?.value.trim() || "";
  const deepSeekMismatch = template?.id === "deepseek"
    && currentModel
    && !currentModel.toLowerCase().startsWith("deepseek-");
  if (template?.default_model && (!currentModel || previousModels.includes(currentModel) || deepSeekMismatch)) {
    if (modelInput) modelInput.value = template.default_model;
    const modelSummary = $(".settings-card-header small", routeCard);
    if (modelSummary) modelSummary.textContent = template.default_model;
    toast(`Model changed to ${template.default_model} for ${template.name}.`);
  }
  providerSelect.dataset.previousProvider = providerId;
  if (providerSelect.id === "settings-supervisor-provider") {
    const picker = $("#settings-supervisor-model-picker");
    picker.dataset.modelPickerProvider = providerId;
    picker.innerHTML = modelPickerContents(config, providerId, modelInput?.value || template?.default_model || "", 'id="settings-supervisor-model"');
    renderSupervisorReasoning(config, providerId, $("#settings-supervisor-model")?.value || "", config.supervisor_reasoning_mode || "", config.supervisor_reasoning_effort || "");
  } else if (routeCard) {
    const picker = $("[data-model-picker-provider]", routeCard);
    picker.dataset.modelPickerProvider = providerId;
    picker.innerHTML = modelPickerContents(config, providerId, modelInput?.value || template?.default_model || "", 'data-profile-field="model"');
    rerenderWorkerReasoning(routeCard);
  }
  loadProviderCatalog(providerId);
}

function migrateProviderReferences(config, renamedProviders) {
  const renamed = (providerId) => providerId && renamedProviders[providerId] ? renamedProviders[providerId] : providerId;
  config.supervisor_provider = renamed(config.supervisor_provider);
  config.worker_provider = renamed(config.worker_provider);
  Object.values(config.worker_profiles || {}).forEach((profile) => {
    if (profile?.provider) profile.provider = renamed(profile.provider);
  });
  return config;
}

function collectProviderDraft(config) {
  captureProviderSecretDrafts();
  const originalProviders = config.model_providers || {};
  const providersById = {};
  const renamedProviders = {};
  $$("[data-provider-id]", $("#settings-provider-list")).forEach((card) => {
    const oldId = card.dataset.providerId;
    const id = $("[data-provider-field='id']", card).value.trim();
    if (!id) throw new Error("Every provider needs an ID.");
    if (providersById[id]) throw new Error(`Duplicate provider: ${id}`);
    const provider = structuredClone(originalProviders[oldId] || {});
    ["name", "base_url", "env_key", "wire_api"].forEach((field) => {
      const value = $(`[data-provider-field='${field}']`, card).value.trim();
      if (value) provider[field] = value; else delete provider[field];
    });
    if (oldId !== id) {
      renamedProviders[oldId] = id;
      if (state.providerSecretDrafts[oldId]) {
        state.providerSecretDrafts[id] = state.providerSecretDrafts[oldId];
        delete state.providerSecretDrafts[oldId];
      }
      card.dataset.providerId = id;
    }
    providersById[id] = provider;
  });
  return { providersById, renamedProviders };
}

function refreshProviderRouteSelectors(config, renamedProviders = {}) {
  const supervisorSelect = $("#settings-supervisor-provider");
  if (supervisorSelect) {
    const selected = renamedProviders[supervisorSelect.value] || supervisorSelect.value || config.supervisor_provider || config.worker_provider || "openai";
    supervisorSelect.innerHTML = providerSelectOptions(config, selected);
    supervisorSelect.dataset.previousProvider = selected;
  }
  $$("[data-profile-field='provider']", $("#settings-profile-list")).forEach((select) => {
    const selected = renamedProviders[select.value] || select.value;
    select.innerHTML = providerSelectOptions(config, selected, { includeDefault: true });
  });
}

function syncProviderDraftToRoutes() {
  if (!state.settings || !$("#settings-provider-list")) return;
  const config = structuredClone(state.settings.config);
  const { providersById, renamedProviders } = collectProviderDraft(config);
  config.model_providers = providersById;
  migrateProviderReferences(config, renamedProviders);
  state.settings.config = config;
  refreshProviderRouteSelectors(config, renamedProviders);
}

function renderSettingsProfiles(config) {
  const profileEntries = Object.entries(config.worker_profiles || {});
  $("#settings-default-profile").innerHTML = profileEntries.map(([name, profile]) => `<option value="${escapeHtml(name)}"${name === config.default_worker_profile ? " selected" : ""}>${escapeHtml(profile.display_name || name)}</option>`).join("");
  $("#settings-profile-list").innerHTML = profileEntries.map(([name, profile]) => {
    const legacyReasoning = profile.codex_config_overrides?.model_reasoning_effort || "";
    const reasoningMode = profile.reasoning_mode || "";
    const reasoningEffort = profile.reasoning_effort || legacyReasoning;
    const provider = profile.provider || "";
    const effectiveProvider = provider || "openai";
    const displayName = profile.display_name || name;
    return `
      <article class="settings-route-card" data-profile-name="${escapeHtml(name)}">
        <div class="settings-card-header"><div><span class="worker-dot"></span><strong>${escapeHtml(displayName)}</strong><small>${escapeHtml(profile.model || "Model required")}</small></div><button class="icon-button remove-settings-profile" aria-label="Remove Worker" title="Remove Worker"><svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3M8 10v8M12 10v8M16 10v8M7 7l1 14h8l1-14"/></svg></button></div>
        <div class="settings-card-grid">
          <input type="hidden" data-profile-field="name" value="${escapeHtml(name)}">
          <label class="route-field"><span>Worker name</span><input data-profile-field="display_name" value="${escapeHtml(displayName)}" maxlength="120" placeholder="Worker name"></label>
          <label class="route-field"><span>Provider</span><select data-profile-field="provider" data-previous-provider="${escapeHtml(provider)}">${providerSelectOptions(config, provider, { includeDefault: true })}</select></label>
          <label class="route-field"><span>Model</span><div class="model-picker" data-model-picker-provider="${escapeHtml(effectiveProvider)}">${modelPickerContents(config, effectiveProvider, profile.model || "", 'data-profile-field="model"')}</div></label>
          <div class="route-reasoning-fields" data-reasoning-container>${workerReasoningMarkup(config, effectiveProvider, profile.model || "", reasoningMode, reasoningEffort)}</div>
          <label class="route-field"><span>Timeout (seconds)</span><input data-profile-field="timeout" type="number" min="1" max="86400" value="${escapeHtml(profile.timeout_seconds || config.timeout_seconds)}"></label>
          <label class="route-field"><span>Legacy Codex profile</span><input data-profile-field="codex_profile" value="${escapeHtml(profile.codex_profile || "")}" placeholder="Optional"></label>
        </div>
      </article>`;
  }).join("") || '<div class="empty-list">Add at least one Worker route before planning a task.</div>';
  $$(".remove-settings-profile", $("#settings-profile-list")).forEach((button) => button.addEventListener("click", () => removeSettingsProfile(button.closest("[data-profile-name]").dataset.profileName)));
}

function renderSettingsProviders(config) {
  const statuses = state.settings.provider_status || {};
  const templates = state.settings.provider_templates || [];
  $("#settings-provider-list").innerHTML = Object.entries(config.model_providers || {}).map(([providerId, provider]) => {
    const configured = Boolean(statuses[providerId]?.credential_configured);
    const needsCredential = Boolean(statuses[providerId]?.uses_environment_credential ?? provider.env_key);
    const endpointReachable = statuses[providerId]?.endpoint_reachable;
    const ready = (!needsCredential || configured) && endpointReachable !== false;
    const statusCopy = needsCredential && !configured ? "Credential missing" : (endpointReachable === false ? "Endpoint offline" : (endpointReachable === true ? "Ready" : "Configured"));
    const matchingTemplate = templates.find((template) => template.id === providerId || template.base_url === provider.base_url);
    const documentation = matchingTemplate?.docs_url ? `<a class="provider-docs-link" href="${escapeHtml(matchingTemplate.docs_url)}" target="_blank" rel="noreferrer">Docs</a>` : "";
    const draftSecret = state.providerSecretDrafts[providerId] || "";
    const secretPlaceholder = configured ? "Configured — enter a new key to replace" : (needsCredential ? "Paste API key" : "Optional if your endpoint requires authentication");
    return `
      <article class="settings-provider-card" data-provider-id="${escapeHtml(providerId)}">
        <div class="settings-card-header"><div><span class="worker-dot"></span><strong>${escapeHtml(providerId)}</strong><span class="settings-provider-status${ready ? " configured" : ""}"><i></i>${statusCopy}</span>${documentation}</div><button class="icon-button remove-settings-provider" aria-label="Remove provider" title="Remove provider"><svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3M8 10v8M12 10v8M16 10v8M7 7l1 14h8l1-14"/></svg></button></div>
        <div class="settings-card-grid provider-grid">
          <label class="provider-field"><span>Provider ID</span><input data-provider-field="id" value="${escapeHtml(providerId)}" maxlength="64"></label>
          <label class="provider-field"><span>Display name</span><input data-provider-field="name" value="${escapeHtml(provider.name || "")}" placeholder="Provider name"></label>
          <label class="provider-field"><span>Base URL</span><input data-provider-field="base_url" value="${escapeHtml(provider.base_url || "")}" placeholder="https://api.example.com/v1"></label>
          <label class="provider-field"><span>API key environment variable</span><input data-provider-field="env_key" value="${escapeHtml(provider.env_key || "")}" placeholder="PROVIDER_API_KEY"></label>
          <label class="provider-field"><span>Wire API</span><select data-provider-field="wire_api">${selectOptions(state.settings.options?.wire_apis || ["responses", "chat"], provider.wire_api || "responses", { responses: "Responses", chat: "Chat Completions" })}</select></label>
          <label class="provider-field provider-secret-field"><span>API key <em>stored locally, never displayed again</em></span><input data-provider-secret type="password" value="${escapeHtml(draftSecret)}" placeholder="${escapeHtml(secretPlaceholder)}" autocomplete="new-password" spellcheck="false"></label>
        </div>
      </article>`;
  }).join("") || '<div class="empty-list">No configured providers. Choose a template above, or continue using the built-in OpenAI, Ollama, or LM Studio route.</div>';
  $$(".remove-settings-provider", $("#settings-provider-list")).forEach((button) => button.addEventListener("click", () => removeSettingsProvider(button.closest("[data-provider-id]").dataset.providerId)));
}

function renderProviderTemplatePicker() {
  const select = $("#settings-provider-template");
  const templates = state.settings?.provider_templates || [];
  const selected = select.value || "custom-openai-compatible";
  const groups = new Map();
  templates.forEach((template) => {
    const category = template.category || "Other";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(template);
  });
  select.innerHTML = [...groups.entries()].map(([category, entries]) => `<optgroup label="${escapeHtml(category)}">${entries.map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(template.name)}</option>`).join("")}</optgroup>`).join("");
  select.value = templates.some((template) => template.id === selected) ? selected : "custom-openai-compatible";
  updateProviderTemplatePreview();
}

function updateProviderTemplatePreview() {
  const template = (state.settings?.provider_templates || []).find((item) => item.id === $("#settings-provider-template").value);
  if (!template) return;
  $("#provider-template-name").textContent = template.name;
  $("#provider-template-description").textContent = template.description;
  const docs = $("#provider-template-docs");
  docs.classList.toggle("hidden", !template.docs_url);
  docs.href = template.docs_url || "#";
}

function captureProviderSecretDrafts() {
  $$("[data-provider-id]", $("#settings-provider-list")).forEach((card) => {
    const currentId = $("[data-provider-field='id']", card)?.value.trim() || card.dataset.providerId;
    const secret = $("[data-provider-secret]", card)?.value || "";
    if (secret) state.providerSecretDrafts[currentId] = secret;
    else delete state.providerSecretDrafts[currentId];
    if (currentId !== card.dataset.providerId) delete state.providerSecretDrafts[card.dataset.providerId];
  });
}

function collectProviderSecrets() {
  captureProviderSecretDrafts();
  const secrets = {};
  $$("[data-provider-id]", $("#settings-provider-list")).forEach((card) => {
    const id = $("[data-provider-field='id']", card).value.trim();
    const secret = $("[data-provider-secret]", card).value;
    if (id && secret) secrets[id] = secret;
  });
  return secrets;
}

function renderSettings() {
  if (!state.settings) return;
  const config = state.settings.config;
  $("#settings-file-path").textContent = state.settings.editable_path;
  $("#settings-config-scope").textContent = state.settings.editable_path;
  $("#settings-app-version").textContent = state.settings.app?.version || state.bootstrap?.app?.version || "Unknown";
  const supervisorProvider = config.supervisor_provider || config.worker_provider || "openai";
  $("#settings-supervisor-provider").innerHTML = providerSelectOptions(config, supervisorProvider);
  $("#settings-supervisor-provider").dataset.previousProvider = supervisorProvider;
  $("#settings-supervisor-model-picker").dataset.modelPickerProvider = supervisorProvider;
  $("#settings-supervisor-model-picker").innerHTML = modelPickerContents(config, supervisorProvider, config.supervisor_model || "", 'id="settings-supervisor-model"');
  renderSupervisorReasoning(config, supervisorProvider, config.supervisor_model || "", config.supervisor_reasoning_mode || "", config.supervisor_reasoning_effort || "");
  $("#settings-supervisor-profile").value = config.supervisor_codex_profile || "";
  $("#settings-supervisor-timeout").value = config.supervisor_timeout_seconds;
  $("#settings-auto-supervisor-review").checked = Boolean(config.auto_supervisor_review);
  $("#settings-max-parallel").value = config.max_parallel_workers;
  $("#settings-sandbox").innerHTML = selectOptions(state.settings.options.sandbox_modes, config.sandbox, { "read-only": "Read only", "workspace-write": "Workspace write", "danger-full-access": "Full access" });
  $("#settings-approval").innerHTML = selectOptions(state.settings.options.approval_policies, config.approval_policy, { untrusted: "Always ask", "on-failure": "On failure", "on-request": "On request", never: "Never ask" });
  $("#settings-native-max-turns").value = config.native_max_turns;
  $("#settings-native-command-timeout").value = config.native_command_timeout_seconds;
  $("#settings-network-access").checked = Boolean(config.codex_config_overrides?.["sandbox_workspace_write.network_access"]);
  $("#settings-ephemeral").checked = Boolean(config.ephemeral);
  $("#settings-allow-lockfiles").checked = Boolean(config.allow_lockfiles);
  $("#settings-worktrees-dir").value = config.worktrees_dir;
  $("#settings-runs-dir").value = config.runs_dir;
  $("#settings-farms-dir").value = config.farms_dir;
  $("#settings-worker-timeout").value = config.timeout_seconds;
  $("#settings-test-timeout").value = config.test_timeout_seconds;
  $("#settings-max-files").value = config.max_changed_files;
  $("#settings-max-diff").value = config.max_diff_lines;
  $("#settings-appearance").value = state.appearanceTheme;
  $("#settings-agent-backend").innerHTML = selectOptions(state.settings.options.agent_backends || ["native", "codex"], config.agent_backend, { native: "Native Agent Farm", codex: "Codex compatibility" });
  $("#settings-codex-binary").value = config.codex_binary;
  const runtimeAvailable = config.agent_backend === "native" ? Boolean(state.settings.runtime?.native_ready) : Boolean(state.settings.runtime?.codex_available);
  $("#settings-runtime-status").textContent = runtimeAvailable ? "Ready" : "Not found";
  $("#settings-runtime-status").dataset.status = runtimeAvailable ? "PASS" : "FAIL";
  $("#settings-runtime-copy").textContent = config.agent_backend === "native" ? (runtimeAvailable ? "The native harness and active model route are ready." : "Check the active model, credential, and local provider endpoint.") : (runtimeAvailable ? "The Codex compatibility runtime is available." : "Update the legacy executable before starting a Codex run.");
  renderSettingsProfiles(config);
  renderProviderTemplatePicker();
  renderSettingsProviders(config);
  const catalogProviders = new Set([
    supervisorProvider,
    ...Object.values(config.worker_profiles || {}).map((profile) => profile.provider || "openai"),
  ]);
  catalogProviders.forEach((providerId) => { void loadProviderCatalog(providerId); });
  setSettingsSection(state.settingsSection);
  state.settingsDirty = Boolean(state.settings.migration_required);
  if (state.settingsDirty) setSettingsSaveState("Legacy Worker route ready to save", "dirty");
  else setSettingsSaveState(`Saved locally · applies to ${state.settings.applies_to}`, "saved");
}

function collectSettingsForm() {
  if (!state.settings) throw new Error("Settings are not loaded.");
  captureProviderSecretDrafts();
  const config = structuredClone(state.settings.config);
  config.supervisor_model = nullableInput("#settings-supervisor-model");
  config.supervisor_provider = nullableInput("#settings-supervisor-provider");
  config.supervisor_reasoning_mode = $("#settings-supervisor-reasoning-mode")?.value || null;
  config.supervisor_reasoning_effort = $("#settings-supervisor-reasoning-effort")?.value || null;
  config.supervisor_codex_profile = nullableInput("#settings-supervisor-profile");
  config.supervisor_timeout_seconds = positiveInteger("#settings-supervisor-timeout", "Planning timeout");
  config.auto_supervisor_review = $("#settings-auto-supervisor-review").checked;
  config.max_parallel_workers = positiveInteger("#settings-max-parallel", "Parallel worker limit");
  config.sandbox = $("#settings-sandbox").value;
  config.approval_policy = $("#settings-approval").value;
  config.native_max_turns = positiveInteger("#settings-native-max-turns", "Maximum agent turns");
  config.native_command_timeout_seconds = positiveInteger("#settings-native-command-timeout", "Native command timeout");
  config.ephemeral = $("#settings-ephemeral").checked;
  config.allow_lockfiles = $("#settings-allow-lockfiles").checked;
  config.worktrees_dir = $("#settings-worktrees-dir").value.trim();
  config.runs_dir = $("#settings-runs-dir").value.trim();
  config.farms_dir = $("#settings-farms-dir").value.trim();
  config.timeout_seconds = positiveInteger("#settings-worker-timeout", "Worker timeout");
  config.test_timeout_seconds = positiveInteger("#settings-test-timeout", "Test timeout");
  config.max_changed_files = positiveInteger("#settings-max-files", "Maximum changed files");
  config.max_diff_lines = positiveInteger("#settings-max-diff", "Maximum diff lines");
  config.agent_backend = $("#settings-agent-backend").value;
  config.codex_binary = $("#settings-codex-binary").value.trim();
  config.codex_config_overrides ||= {};
  if ($("#settings-network-access").checked) config.codex_config_overrides["sandbox_workspace_write.network_access"] = true;
  else delete config.codex_config_overrides["sandbox_workspace_write.network_access"];

  const originalProfiles = state.settings.config.worker_profiles || {};
  const profilesByName = {};
  const renamedProfiles = {};
  $$("[data-profile-name]", $("#settings-profile-list")).forEach((card) => {
    const oldName = card.dataset.profileName;
    const name = $("[data-profile-field='name']", card).value.trim();
    if (!name) throw new Error("Every Worker route needs a name.");
    if (profilesByName[name]) throw new Error(`Duplicate Worker route: ${name}`);
    const profile = structuredClone(originalProfiles[oldName] || {});
    const displayName = $("[data-profile-field='display_name']", card).value.trim();
    const model = $("[data-profile-field='model']", card).value.trim();
    const provider = $("[data-profile-field='provider']", card).value.trim();
    const codexProfile = $("[data-profile-field='codex_profile']", card).value.trim();
    const timeout = Number.parseInt($("[data-profile-field='timeout']", card).value, 10);
    const reasoningMode = $("[data-profile-field='reasoning_mode']", card)?.value || "";
    const reasoningEffort = $("[data-profile-field='reasoning_effort']", card)?.value || "";
    if (!displayName) delete profile.display_name; else profile.display_name = displayName;
    if (!model) delete profile.model; else profile.model = model;
    if (!provider) delete profile.provider; else profile.provider = provider;
    if (!reasoningMode) delete profile.reasoning_mode; else profile.reasoning_mode = reasoningMode;
    if (!reasoningEffort) delete profile.reasoning_effort; else profile.reasoning_effort = reasoningEffort;
    if (!codexProfile) delete profile.codex_profile; else profile.codex_profile = codexProfile;
    if (!Number.isInteger(timeout) || timeout < 1) throw new Error(`Route ${name} needs a positive timeout.`);
    profile.timeout_seconds = timeout;
    profile.codex_config_overrides ||= {};
    delete profile.codex_config_overrides.model_reasoning_effort;
    if (!Object.keys(profile.codex_config_overrides).length) delete profile.codex_config_overrides;
    profilesByName[name] = profile;
    renamedProfiles[oldName] = name;
  });
  const selectedDefault = $("#settings-default-profile").value;
  config.worker_profiles = profilesByName;
  config.default_worker_profile = Object.keys(profilesByName).length
    ? (profilesByName[selectedDefault] ? selectedDefault : (renamedProfiles[selectedDefault] || Object.keys(profilesByName)[0]))
    : null;

  const { providersById, renamedProviders } = collectProviderDraft(config);
  config.model_providers = providersById;
  migrateProviderReferences(config, renamedProviders);
  return config;
}

async function loadSettings(force = false) {
  if (state.settings && !force) {
    renderSettings();
    return;
  }
  setSettingsSaveState("Loading settings…");
  setSettingsFeedback("loading", "Loading settings", "Reading the effective local configuration and runtime status.");
  try {
    state.settings = await api("/api/settings");
    renderSettings();
    setSettingsFeedback();
  } catch (error) {
    setSettingsSaveState("Settings unavailable", "dirty");
    setSettingsFeedback("error", "Settings could not be loaded", error.message);
    throw error;
  }
}

async function saveSettings() {
  const button = $("#settings-save-button");
  try {
    button.disabled = true;
    setSettingsSaveState("Saving…");
    const config = collectSettingsForm();
    const provider_secrets = collectProviderSecrets();
    state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify({ config, provider_secrets }) });
    state.providerSecretDrafts = {};
    renderSettings();
    setSettingsFeedback();
    await refreshBootstrap();
    toast("Settings saved. New runs will use the updated configuration.");
  } catch (error) {
    setSettingsSaveState("Could not save", "dirty");
    setSettingsFeedback("error", "Settings were not saved", error.message);
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function removeSettingsProfile(name) {
  try {
    const cards = $$("[data-profile-name]", $("#settings-profile-list"));
    if (cards.length <= 1) return toast("Keep at least one Worker route.", true);
    cards.find((card) => card.dataset.profileName === name)?.remove();
    const config = collectSettingsForm();
    state.settings.config = config;
    renderSettings();
    markSettingsDirty();
  } catch (error) { toast(error.message, true); }
}

function addSettingsProfile() {
  try {
    const config = collectSettingsForm();
    let index = Object.keys(config.worker_profiles).length + 1;
    let name = `worker-${index}`;
    while (config.worker_profiles[name]) name = `worker-${++index}`;
    config.worker_profiles[name] = { display_name: `Worker ${index}`, model: "", timeout_seconds: config.timeout_seconds };
    config.default_worker_profile ||= name;
    state.settings.config = config;
    renderSettings();
    markSettingsDirty();
  } catch (error) { toast(error.message, true); }
}

function removeSettingsProvider(providerId) {
  try {
    captureProviderSecretDrafts();
    $$("[data-provider-id]", $("#settings-provider-list")).find((card) => card.dataset.providerId === providerId)?.remove();
    delete state.providerSecretDrafts[providerId];
    const config = collectSettingsForm();
    state.settings.config = config;
    renderSettings();
    markSettingsDirty();
  } catch (error) { toast(error.message, true); }
}

function addSettingsProvider() {
  try {
    const config = collectSettingsForm();
    const template = (state.settings.provider_templates || []).find((item) => item.id === $("#settings-provider-template").value);
    if (!template) throw new Error("Choose a provider template first.");
    const baseId = template.custom ? "custom-provider" : template.id;
    let id = baseId;
    let index = 2;
    while (config.model_providers[id]) id = `${baseId}-${index++}`;
    config.model_providers[id] = {
      template_id: template.id,
      name: template.name,
      base_url: template.base_url,
      env_key: template.env_key,
      wire_api: template.wire_api,
      requires_openai_auth: Boolean(template.env_key),
    };
    state.settings.config = config;
    renderSettings();
    markSettingsDirty();
    toast(`${template.name} added. It is now available in Supervisor and Worker routes.`);
  } catch (error) { toast(error.message, true); }
}

async function openFarm(farmId) {
  try {
    const farm = await api(`/api/farms/${encodeURIComponent(farmId)}`);
    state.selectedFarm = farm;
    state.selectedWorker = farm.workers?.[0]?.id || null;
    $("#view-title").textContent = farm.plan_task_id || farm.farm_id;
    $("#inspector").classList.add("open");
    $("#inspector-empty").classList.add("hidden");
    $("#inspector-content").classList.remove("hidden");
    $("#inspector-title").textContent = farm.plan_task_id || farm.farm_id;
    const badge = $("#inspect-status");
    badge.textContent = readableStatus(farm.status);
    badge.dataset.status = farm.status;
    $("#inspect-base").textContent = `${farm.base_ref || "HEAD"} · ${shortCommit(farm.base_commit)}`;
    renderWorkerTabs();
    renderWorkerDetail();
    const existing = farm.decision;
    if (existing) {
      state.selectedDecision = existing.decision;
      $("#risk-level").value = existing.risk_level;
      $("#rollback-required").checked = existing.rollback_required;
      $("#decision-reason").value = existing.reason;
    }
    renderDecisionSelection();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderWorkerTabs() {
  const workers = state.selectedFarm?.workers || [];
  $("#worker-tabs").innerHTML = workers.map((worker) => `
    <button class="worker-tab${worker.id === state.selectedWorker ? " active" : ""}" data-worker="${escapeHtml(worker.id)}">${escapeHtml(worker.id)}</button>
  `).join("");
  $$('[data-worker]', $("#worker-tabs")).forEach((button) => button.addEventListener("click", () => {
    state.selectedWorker = button.dataset.worker;
    renderWorkerTabs();
    renderWorkerDetail();
  }));
}

function renderWorkerDetail() {
  const worker = state.selectedFarm?.workers?.find((item) => item.id === state.selectedWorker);
  const target = $("#worker-detail");
  if (!worker) {
    target.innerHTML = '<div class="empty-list">This run has not produced Worker results yet.</div>';
    return;
  }
  const files = worker.changed_files || [];
  const tests = worker.tests || [];
  const findings = worker.machine_review?.findings || [];
  const deliverable = state.selectedFarm?.deliverable;
  const deliverableCard = deliverable?.path ? `<div class="evidence-card"><div class="evidence-title"><strong>Final deliverable</strong><span class="status-badge" data-status="${escapeHtml(state.selectedFarm.status)}">${escapeHtml(readableStatus(state.selectedFarm.status))}</span></div><div class="evidence-list"><div class="evidence-item ok"><i></i><span>${escapeHtml(deliverable.path)}</span></div></div></div>` : "";
  target.innerHTML = `
    ${deliverableCard}
    <div class="evidence-card"><div class="evidence-title"><strong>${escapeHtml(worker.role)}</strong><span class="status-badge" data-status="${escapeHtml(worker.machine_review?.status || worker.status)}">${escapeHtml(worker.machine_review?.status || readableStatus(worker.status))}</span></div><div class="evidence-list"><div class="evidence-item"><i></i><span>${escapeHtml(profileDisplayName(worker.profile))} · ${escapeHtml(worker.model)} · ${escapeHtml(worker.provider)}</span></div></div></div>
    <div class="evidence-card"><div class="evidence-title"><strong>Changed files</strong><span class="status-badge">${files.length}</span></div><div class="evidence-list">${files.map((file) => `<div class="evidence-item"><i></i><span>${escapeHtml(file.status)} · ${escapeHtml(file.old_path ? `${file.old_path} → ${file.path}` : file.path)}</span></div>`).join("") || '<div class="evidence-item"><span>No file changes</span></div>'}</div></div>
    <div class="evidence-card"><div class="evidence-title"><strong>Test evidence</strong><span class="status-badge">${tests.length}</span></div><div class="evidence-list">${tests.map((test) => `<div class="evidence-item ${test.returncode === 0 && !test.timed_out ? "ok" : "bad"}"><i></i><span>${escapeHtml(test.command)} · ${test.returncode === 0 && !test.timed_out ? "Passed" : "Failed"}</span></div>`).join("") || '<div class="evidence-item"><span>No tests configured</span></div>'}</div></div>
    <div class="evidence-card"><div class="evidence-title"><strong>Machine review</strong><span class="status-badge" data-status="${escapeHtml(worker.machine_review?.status)}">${escapeHtml(worker.machine_review?.status || "unknown")}</span></div><div class="evidence-list">${findings.map((finding) => `<div class="evidence-item ${finding.severity === "error" ? "bad" : ""}"><i></i><span>${escapeHtml(finding.code)}${finding.path ? ` · ${escapeHtml(finding.path)}` : ""}<br>${escapeHtml(finding.message)}</span></div>`).join("") || '<div class="evidence-item ok"><i></i><span>No boundary or policy issues found</span></div>'}</div><button class="patch-button" id="patch-button">View full patch</button><pre class="patch-view hidden" id="patch-view"></pre></div>
  `;
  $("#patch-button").addEventListener("click", loadPatch);
}

async function loadPatch() {
  const button = $("#patch-button");
  const viewer = $("#patch-view");
  if (!viewer.classList.contains("hidden")) {
    viewer.classList.add("hidden");
    button.textContent = "View full patch";
    return;
  }
  try {
    button.textContent = "Loading…";
    const payload = await api(`/api/farms/${encodeURIComponent(state.selectedFarm.farm_id)}/workers/${encodeURIComponent(state.selectedWorker)}/patch`);
    viewer.textContent = payload.patch || "(empty patch)";
    if (payload.truncated) viewer.textContent += "\n\n… The patch is too large. Only the first 2 MB are shown.";
    viewer.classList.remove("hidden");
    button.textContent = "Collapse patch";
  } catch (error) {
    button.textContent = "View full patch";
    toast(error.message, true);
  }
}

function renderDecisionSelection() {
  $$("[data-decision]", $(".decision-options")).forEach((button) => button.classList.toggle("active", button.dataset.decision === state.selectedDecision));
}

async function submitDecision() {
  if (!state.selectedFarm) return;
  const reason = $("#decision-reason").value.trim();
  if (!reason) return toast("Enter a decision rationale.", true);
  const approvedWorker = state.selectedDecision === "approve_merge" ? state.selectedWorker : null;
  if (state.selectedDecision === "approve_merge" && !approvedWorker) return toast("There is no Worker result to approve.", true);
  const payload = {
    schema_version: 1,
    decision: state.selectedDecision,
    task_id: state.selectedFarm.farm_id,
    approved_worker: approvedWorker,
    risk_level: $("#risk-level").value,
    reason,
    rollback_required: $("#rollback-required").checked,
  };
  const button = $("#submit-decision");
  try {
    button.disabled = true;
    const result = await api(`/api/farms/${encodeURIComponent(state.selectedFarm.farm_id)}/decision`, { method: "POST", body: JSON.stringify(payload) });
    state.selectedFarm = result;
    const badge = $("#inspect-status");
    badge.textContent = readableStatus(result.status);
    badge.dataset.status = result.status;
    await refreshBootstrap();
    toast("The Supervisor decision was added to the Farm evidence package.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function refreshBootstrap() {
  state.bootstrap = await api("/api/bootstrap");
  $("#repo-name").textContent = state.bootstrap.repository.name;
  $("#repo-name").title = state.bootstrap.repository.path;
  $("#repo-branch").textContent = state.bootstrap.repository.branch;
  $(".branch-pill span").textContent = state.bootstrap.repository.branch;
  $("#mission-repo").textContent = state.bootstrap.repository.name;
  $("#welcome-repo").textContent = state.bootstrap.repository.name;
  $("#composer-repo").textContent = state.bootstrap.repository.name;
  const supervisor = state.bootstrap.supervisor;
  const backendLabel = supervisor.backend === "codex" ? "Codex" : "Native";
  const routeTitle = `${supervisor.model} via ${supervisor.provider}`;
  const routeButton = $("#supervisor-route-button");
  routeButton.dataset.ready = String(Boolean(supervisor.ready));
  routeButton.title = supervisor.ready ? `Supervisor route: ${routeTitle}` : `Supervisor route needs setup: ${routeTitle}`;
  $("#supervisor-status").textContent = `${backendLabel} · ${supervisor.ready ? "Ready" : "Needs setup"}`;
  $(".model-context").dataset.ready = String(Boolean(supervisor.ready));
  $("#composer-supervisor-model").textContent = supervisor.ready ? `${supervisor.model} · ${supervisor.provider}` : "Configure Supervisor";
  $("#composer-supervisor-model").title = routeTitle;
  $("#max-parallel").max = state.bootstrap.limits.max_parallel_workers;
  $("#mission-worker-count").textContent = `Up to ${state.bootstrap.limits.max_parallel_workers} economical Workers`;
  renderRecent();
  renderHistory();
  renderProfiles();
}

function bindInterface() {
  if (state.shellBound) return;
  state.shellBound = true;

  initTheme();
  if (new URLSearchParams(location.search).has("desktop")) document.body.classList.add("desktop-runtime");
  initWindowChrome();

  $$(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $$("[data-settings-section]").forEach((button) => button.addEventListener("click", () => setSettingsSection(button.dataset.settingsSection)));
  $("#new-task-button").addEventListener("click", () => { setView("workspace"); resetThread(); });
  $("#add-worker").addEventListener("click", () => { syncWorkersFromForm(); addWorker(); });
  $("#load-example").addEventListener("click", loadExample);
  $("#launch-button").addEventListener("click", launchFarm);
  $("#draft-plan-button").addEventListener("click", draftPlan);
  $$("[data-intent-prompt]").forEach((button) => button.addEventListener("click", () => {
    $("#mission-prompt").value = button.dataset.intentPrompt;
    $("#mission-prompt").focus();
    $("#mission-prompt").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));
  $$('[data-compose-mode]').forEach((button) => button.addEventListener("click", () => {
    $$('[data-compose-mode]').forEach((item) => item.classList.toggle("active", item === button));
    if (button.dataset.composeMode === "manual") {
      $("#welcome-state").classList.add("hidden");
      $("#composer-card").classList.remove("hidden");
      $("#composer-card").scrollIntoView({ behavior: "smooth" });
    }
    else $("#mission-prompt").focus();
  }));
  $("#refresh-button").addEventListener("click", () => refreshBootstrap().then(() => toast("Refreshed." )).catch((error) => toast(error.message, true)));
  $("#supervisor-route-button").addEventListener("click", () => { setView("settings"); setSettingsSection("agents"); });
  $("#history-refresh").addEventListener("click", () => refreshBootstrap().catch((error) => toast(error.message, true)));
  $("#close-inspector").addEventListener("click", () => $("#inspector").classList.remove("open"));
  $("#mobile-menu").addEventListener("click", () => document.body.classList.toggle("menu-open"));
  $("#theme-button").addEventListener("click", () => setTheme(document.body.classList.contains("light") ? "dark" : "light", true));
  $("#settings-save-button").addEventListener("click", saveSettings);
  $("#settings-reload-button").addEventListener("click", () => loadSettings(true).catch((error) => toast(error.message, true)));
  $("#settings-retry-button").addEventListener("click", () => loadSettings(true).catch(() => {}));
  $("#app-status-retry").addEventListener("click", hydrateApplication);
  $("#add-profile-button").addEventListener("click", addSettingsProfile);
  $("#add-provider-button").addEventListener("click", addSettingsProvider);
  $("#settings-provider-template").addEventListener("change", updateProviderTemplatePreview);
  $("#settings-view").addEventListener("click", (event) => {
    const refresh = event.target.closest?.("[data-refresh-models]");
    if (!refresh) return;
    void loadProviderCatalog(refresh.dataset.refreshModels, { refresh: true });
  });
  $("#settings-view").addEventListener("input", (event) => { if (event.target.id !== "settings-appearance") markSettingsDirty(); });
  $("#settings-view").addEventListener("change", (event) => {
    if (event.target.id === "settings-appearance") setTheme(event.target.value, true);
    else {
      if (event.target.matches("[data-provider-field]")) {
        try { syncProviderDraftToRoutes(); }
        catch (error) { toast(error.message, true); }
      }
      if (event.target.matches("#settings-supervisor-provider, [data-profile-field='provider']")) {
        applyProviderModelDefault(event.target);
      }
      if (event.target.matches("#settings-supervisor-model")) {
        const config = state.settings.config;
        const providerId = $("#settings-supervisor-provider").value || "openai";
        renderSupervisorReasoning(config, providerId, event.target.value, $("#settings-supervisor-reasoning-mode")?.value || "", $("#settings-supervisor-reasoning-effort")?.value || "");
      }
      if (event.target.matches("[data-profile-field='model']")) {
        const card = event.target.closest("[data-profile-name]");
        const summary = $(".settings-card-header small", card);
        if (summary) summary.textContent = event.target.value || "Model required";
        rerenderWorkerReasoning(card);
      }
      markSettingsDirty();
    }
  });
  window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", () => { if (state.appearanceTheme === "system") setTheme("system"); });
  $$("[data-decision]", $(".decision-options")).forEach((button) => button.addEventListener("click", () => { state.selectedDecision = button.dataset.decision; renderDecisionSelection(); }));
  $("#submit-decision").addEventListener("click", submitDecision);
  document.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() === "n" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      setView("workspace");
      resetThread();
    }
    if (event.key === "," && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      setView("settings");
    }
    if (event.key === "Escape") { $("#inspector").classList.remove("open"); document.body.classList.remove("menu-open"); }
  });
  window.addEventListener("hashchange", applyLocationRoute);
  window.addEventListener("popstate", applyLocationRoute);
  applyLocationRoute();
  document.body.classList.add("app-ready");
  window.__agentFarm = Object.freeze({ navigate: (view) => setView(view), version: "0.3-shell" });
}

async function hydrateApplication() {
  setAppBanner("Connecting to the local Agent Farm service…");
  try {
    const settingsWarmup = loadSettings().catch(() => null);
    await refreshBootstrap();
    await settingsWarmup;
    setAppBanner();
    if (!state.workers.length) {
      addWorker({ id: "implementation", role: "Implementation agent", allowed_paths: state.bootstrap.defaults.allowed_paths, test_commands: state.bootstrap.defaults.test_commands });
    }
    const running = state.bootstrap.jobs.find((job) => ["QUEUED", "RUNNING"].includes(job.status));
    if (running) { $("#welcome-state").classList.add("hidden"); showLiveJob(running); pollJob(running.job_id); }
    const planning = state.bootstrap.plan_jobs.find((job) => ["QUEUED", "RUNNING"].includes(job.status));
    if (planning) { $("#welcome-state").classList.add("hidden"); $("#mission-status").classList.remove("hidden"); $("#draft-plan-button").disabled = true; pollPlanJob(planning.job_id); }
  } catch (error) {
    const message = `Could not connect to the local service: ${error.message}`;
    setAppBanner(message, true);
    toast(message, true);
  }
}

async function init() {
  bindInterface();
  await hydrateApplication();
}

document.addEventListener("DOMContentLoaded", init);
window.addEventListener("pywebviewready", initWindowChrome);
