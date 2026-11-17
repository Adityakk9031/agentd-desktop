"use strict";
// The screens for agentd. Runs in the desktop app (Electron) and in a browser.
// All text from the agent is inserted with textContent, never as HTML.

const TOKEN = (window.agentd && window.agentd.token) || "";
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
  }
  return node;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error((data && data.detail) || `Request failed (${response.status})`);
  return data;
}

const pill = (text, kind) => el("span", { class: `pill ${kind || ""}` }, text);
const percent = (n) => `${Math.round((Number(n) || 0) * 100)}%`;
function note(id, kind, text) { $(id).className = "note " + (kind || "muted"); $(id).textContent = text || ""; }
const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* full or blocked */ } },
};

// ------------------------------------------------------------------ sessions
// A session is one conversation. mode: "single" (Chat) or "team" (Agents).

let sessions = store.get("agentd.sessions.v2", []);
let current = null;      // the open session
let mode = "single";     // mode of the composer when no session is open yet
let providers = [];
let currentModel = null;

const MODES = {
  single: {
    title: "What can I do for you?",
    sub: "I can work with your files, run commands and use your connected services. I ask before changing anything.",
    placeholder: "Give a task…",
    ideas: ["What is taking up space in my Downloads folder?", "Create a notes.txt on my Desktop with today's date", "What's the weather in Bangalore?"],
  },
  team: {
    title: "Put a team of agents on it",
    sub: "A supervisor splits your task, agents work on the parts at the same time, and a lead writes the answer.",
    placeholder: "Describe a bigger task for the team…",
    ideas: [
      "Compare the top 3 Python web frameworks on GitHub and recommend one",
      "Research today's AI news and the weather in Delhi, then write a morning brief",
      "Find trending repositories and summarise what each one does",
    ],
  },
};

function saveSessions() {
  sessions = sessions.slice(0, 40);
  store.set("agentd.sessions.v2", sessions);
  renderRecents();
}

function renderRecents() {
  const list = $("recents");
  if (!sessions.length) return list.replaceChildren(el("div", { class: "none" }, "Your chats will appear here."));
  list.replaceChildren(...sessions.map((s) => el("button", {
    class: "recent" + (current && current.id === s.id ? " active" : ""), type: "button", onclick: () => openSession(s.id),
  },
    el("span", { class: "t" }, s.title || "New chat"),
    s.mode === "team" ? el("span", { class: "tag" }, "team") : null,
    el("span", { class: "x", title: "Delete", onclick: (event) => { event.stopPropagation(); deleteSession(s.id); } }, "×"))));
}

function deleteSession(id) {
  sessions = sessions.filter((s) => s.id !== id);
  if (current && current.id === id) startNew(mode);
  saveSessions();
}

function applyMode() {
  const info = MODES[mode];
  $("hero-title").textContent = info.title;
  $("hero-sub").textContent = info.sub;
  $("task").placeholder = info.placeholder;
  $("mode-chip").hidden = mode !== "team";
  document.querySelectorAll(".nav-item[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode && $("view-chat").classList.contains("active")));
  renderEmptyExtras();
}

function renderEmptyExtras() {
  const box = $("suggestions");
  const ready = Boolean(currentModel);
  if (!ready && $("chat-provider").dataset.ready === "1") {
    // First run: nothing connected yet
    return box.replaceChildren(el("div", { class: "onboard" },
      el("p", {}, "Connect an AI to get started. You bring the key; it stays on this computer."),
      el("div", { class: "choices" }, Object.entries(CHAT_PROVIDERS).map(([id, label]) =>
        el("button", { type: "button", onclick: () => { $("chat-provider").value = id; chooseProvider(id); } }, label)))));
  }
  box.replaceChildren(...MODES[mode].ideas.map((idea) =>
    el("button", { class: "suggestion", type: "button", onclick: () => { $("task").value = idea; $("task").focus(); } }, idea)));
}

function startNew(newMode) {
  mode = newMode || mode;
  current = null;
  messages.replaceChildren();
  $("view-chat").classList.add("empty");
  show("chat");
  applyMode();
  renderRecents();
  $("task").focus();
}

function openSession(id) {
  const session = sessions.find((s) => s.id === id);
  if (!session) return;
  current = session;
  mode = session.mode;
  messages.replaceChildren();
  $("view-chat").classList.remove("empty");
  for (const entry of session.entries) renderEntry(entry);
  show("chat");
  applyMode();
  renderRecents();
}

function record(entry) {
  if (!current) {
    current = { id: crypto.randomUUID(), mode, title: "", entries: [], updated: Date.now() };
    sessions.unshift(current);
  }
  if (entry.kind === "user" && !current.title) current.title = entry.text.slice(0, 60);
  current.entries.push(entry);
  current.entries = current.entries.slice(-60);
  current.updated = Date.now();
  sessions = [current, ...sessions.filter((s) => s.id !== current.id)];
  saveSessions();
}

function renderEntry(entry) {
  if (entry.kind === "user") addMessage("user", el("div", { class: "body" }, entry.text));
  else if (entry.kind === "result") renderResult(entry.result);
  else if (entry.kind === "error") addMessage("agent error", el("div", { class: "body" }, entry.text));
}

// ---------------------------------------------------------------- navigation

const loaders = { approvals: loadApprovals, tools: loadTools, automations: loadAutomations, logins: loadLogins };

function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view && (!b.dataset.mode || b.dataset.mode === mode)));
  if (loaders[view]) loaders[view]();
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.mode) {
    // Chat / Agents: keep the open conversation if it is of that kind, else start fresh
    if (current && current.mode === button.dataset.mode) { mode = current.mode; show("chat"); applyMode(); }
    else startNew(button.dataset.mode);
  } else show(button.dataset.view);
}));
$("new-chat").addEventListener("click", () => startNew(mode));

// -------------------------------------------------------------------- status

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    $("version").textContent = `v${status.version}`;
    const working = new Set(status.models.filter((m) => m.status === "ok").map((m) => m.role));
    const ready = working.has("planner") && working.has("worker");
    $("status-dot").className = `dot ${ready ? "ok" : "bad"}`;
    const planner = status.models.find((m) => m.role === "planner" && m.status === "ok");
    $("status-text").textContent = ready ? planner.model : "No model connected";
    const count = $("approval-count");
    count.hidden = !status.pending_approvals;
    count.textContent = status.pending_approvals;
    return status;
  } catch {
    $("status-dot").className = "dot bad";
    $("status-text").textContent = "Agent not reachable";
    return null;
  }
}

// ---------------------------------------------------------------------- chat

const messages = $("messages");
const taskInput = $("task");

function addMessage(kind, ...content) {
  $("view-chat").classList.remove("empty");
  const node = el("div", { class: `msg ${kind}` }, ...content);
  messages.append(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function stepList(plan, results) {
  const byId = new Map((results || []).map((r) => [r.id, r]));
  return (plan.steps || []).map((step) => {
    const result = byId.get(step.id);
    const state = !result ? pill("not run") : result.success ? pill("done", "ok")
      : String(result.error || "").startsWith("Blocked by permission") ? pill("held", "warn") : pill("failed", "fail");
    return el("div", { class: "step" },
      el("div", {}, state, " ", el("strong", {}, step.title || `Step ${step.id}`), " ", el("code", {}, step.tool || "")),
      result && !result.success ? el("div", { class: "err" }, result.error || "Failed") : null);
  });
}

// --- the agent team board: live while running, and kept with the answer

function teamBoard() {
  const head = el("span", {}, "Supervisor is planning the work…");
  const spinner = el("span", { class: "spinner" });
  const rows = el("div", {});
  const node = el("div", { class: "board" }, el("div", { class: "board-head" }, spinner, head), rows);
  const byId = new Map();

  function addAgent(agent) {
    const tools = el("div", { class: "tools" });
    const extra = el("div", {});
    const row = el("div", { class: "agent" },
      el("span", { class: "state" }),
      el("div", { class: "who" }, agent.name, " ", el("span", {}, "· " + agent.role)),
      el("div", { class: "job" }, agent.task), tools, extra);
    byId.set(agent.id, { row, tools, extra });
    rows.append(row);
  }
  function setTools(id, names) {
    const entry = byId.get(id);
    if (entry) entry.tools.replaceChildren(...[...new Set(names || [])].map((n) => pill(n)));
  }
  function finish(text) { spinner.remove(); head.textContent = text; }

  return {
    node,
    apply(events) {
      for (const event of events) {
        if (event.type === "planned" && !byId.size) {
          event.agents.forEach(addAgent);
          head.textContent = event.agents.length === 1 ? "One agent is on it" : `${event.agents.length} agents are working at the same time`;
        } else if (event.type === "agent_started" && byId.has(event.id)) {
          byId.get(event.id).row.className = "agent working";
        } else if (event.type === "agent_done" && byId.has(event.id)) {
          byId.get(event.id).row.className = "agent " + (event.success ? "done" : "failed");
          setTools(event.id, event.tools_used);
        } else if (event.type === "combining") {
          head.textContent = "Lead is writing the final answer…";
        }
      }
    },
    showFinal(agents) {
      if (!byId.size) agents.forEach(addAgent);
      for (const agent of agents) {
        const entry = byId.get(agent.id);
        if (!entry) continue;
        entry.row.className = "agent " + (agent.success ? "done" : "failed");
        setTools(agent.id, agent.tools_used);
        entry.extra.replaceChildren(agent.success
          ? el("details", {}, el("summary", {}, "Report"), el("div", { class: "report step" }, agent.output))
          : el("div", { class: "err" }, agent.error || "Failed"));
      }
      const failed = agents.filter((a) => !a.success).length;
      finish(`${agents.length} agent${agents.length > 1 ? "s" : ""} finished` + (failed ? `, ${failed} failed` : ""));
    },
  };
}

function activityFeed(runId) {
  // Shows each thing the agent does as it happens, and its questions.
  const list = el("div", { class: "activity" });
  const asks = el("div", {});
  const node = el("div", {}, list, asks);
  const rows = new Map();

  function addAction(id, summary, state) {
    const row = el("div", { class: "act " + state }, el("span", { class: "mark" }), el("span", { class: "text" }, summary));
    rows.set(id, row);
    list.append(row);
  }
  function question(event) {
    const card = el("div", { class: "ask" }, el("div", { class: "q" }, (event.kind === "handoff" ? "" : "The agent wants to: ") + event.title),
      event.detail ? el("pre", {}, event.detail) : null);
    const buttons = el("div", { class: "actions" });
    const answer = async (decision, label) => {
      buttons.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await api(`/api/runs/${runId}/actions/${event.id}`, { method: "POST", body: JSON.stringify({ decision }) });
        buttons.replaceChildren(pill(label, decision === "deny" ? "fail" : "ok"));
      } catch (error) { buttons.replaceChildren(el("span", { class: "muted" }, error.message)); }
      card.classList.add("done");
    };
    buttons.append(
      el("button", { class: "btn small", type: "button", onclick: () => answer("once", event.kind === "handoff" ? "Continued" : "Allowed") },
        event.kind === "handoff" ? "I've done it, continue" : event.kind === "purchase" ? "Yes, do it" : "Allow once"),
      event.once_only ? null : el("button", { class: "btn small secondary", type: "button", onclick: () => answer("always", "Allowed for this task") }, "Allow for this task"),
      el("button", { class: "btn small secondary danger", type: "button", onclick: () => answer("deny", "Denied") }, "Deny"));
    card.append(buttons);
    card.dataset.ask = event.id;
    asks.append(card);
  }

  return {
    node,
    apply(events) {
      for (const event of events) {
        if (event.type === "action") addAction(event.id, event.summary, "running");
        else if (event.type === "action_done" && rows.has(event.id)) rows.get(event.id).className = "act " + (event.ok ? "ok" : "fail");
        else if (event.type === "approval") question(event);
        else if (event.type === "approval_resolved") {
          const card = asks.querySelector(`[data-ask="${event.id}"]`);
          if (card && !card.classList.contains("done")) { card.classList.add("done"); card.querySelector(".actions").replaceChildren(pill(event.decision === "deny" ? "Denied" : "Allowed", event.decision === "deny" ? "fail" : "ok")); }
        }
      }
    },
  };
}

function actionList(actions) {
  return actions.map((a) => el("div", { class: "act " + (a.ok === false ? "fail" : "ok") },
    el("span", { class: "mark" }), el("span", { class: "text" }, a.summary)));
}

function renderResult(result) {
  const verification = result.verification || {};
  const summary = (verification.final_output || {}).summary;
  const text = result.final_answer || summary || "Done.";
  const source = verification.source === "llm" ? "AI-checked" : "Checked";
  const issues = verification.issues || [];
  const parts = [];

  if (result.agents) {
    const board = teamBoard();
    board.showFinal(result.agents);
    parts.push(board.node);
  }
  parts.push(el("div", { class: "body" }, text));
  parts.push(el("div", { class: "meta" },
    pill(`${source} ${percent(verification.confidence)}`, result.needs_review ? "warn" : "ok"),
    issues.length ? pill(`${issues.length} issue${issues.length > 1 ? "s" : ""}`, "fail") : null,
    result.needs_review ? "Waiting for your approval" : null));
  if (result.actions && result.actions.length) {
    const failed = result.actions.filter((a) => a.ok === false).length;
    parts.push(el("details", {}, el("summary", {},
      `${result.actions.length} action${result.actions.length > 1 ? "s" : ""}` + (failed ? `, ${failed} did not work` : "")),
      el("div", { class: "activity" }, actionList(result.actions))));
  } else if (!result.agents && !result.actions) {
    const steps = stepList(result.plan || {}, result.executor_results);
    if (steps.length) parts.push(el("details", { open: result.needs_review }, el("summary", {}, `${steps.length} step${steps.length > 1 ? "s" : ""}`), steps));
  }
  const node = addMessage("agent", ...parts);
  if (result.approval_id) node.append(approvalActions(result.approval_id, node));
}

function approvalActions(id, container) {
  const row = el("div", { class: "actions" });
  const decide = async (decision) => {
    row.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await api(`/api/approvals/${id}/resolve`, { method: "POST", body: JSON.stringify({ decision, resolved_by: "desktop" }) });
      row.replaceChildren(pill(decision === "approved" ? "Approved" : "Rejected", decision === "approved" ? "ok" : "fail"));
    } catch (error) {
      row.replaceChildren(el("span", { class: "muted" }, error.message));
    }
    refreshStatus();
    if (container && container.dataset.reload) loadApprovals();
  };
  row.append(
    el("button", { class: "btn small", onclick: () => decide("approved") }, "Approve"),
    el("button", { class: "btn small secondary", onclick: () => decide("rejected") }, "Reject"));
  return row;
}

taskInput.addEventListener("input", () => {
  taskInput.style.height = "auto";
  taskInput.style.height = `${taskInput.scrollHeight}px`;
});
taskInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("composer").requestSubmit(); }
});

let running = null; // AbortController of the task in progress

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (running) { running.abort(); return; } // the button is "Stop" while a task runs
  const task = taskInput.value.trim();
  if (!task) return;
  taskInput.value = ""; taskInput.style.height = "auto";
  running = new AbortController();
  $("send").classList.add("stop"); $("send").title = "Stop";
  addMessage("user", el("div", { class: "body" }, task));
  record({ kind: "user", text: task });

  const team = mode === "team";
  const runId = crypto.randomUUID();
  running.runId = runId;
  const started = Date.now();
  const label = el("span", {}, "Working… 0s");
  const board = team ? teamBoard() : null;
  const feed = activityFeed(runId);
  const waiting = addMessage("agent", board ? board.node : null, feed.node, el("div", { class: "working" }, el("span", { class: "spinner" }), label));
  let seen = 0;
  const timer = setInterval(async () => {
    label.textContent = `Working… ${Math.round((Date.now() - started) / 1000)}s`;
    try {
      const { events } = await api(`/api/runs/${runId}`);
      const fresh = events.slice(seen);
      seen = events.length;
      if (!fresh.length) return;
      if (board) board.apply(fresh);
      feed.apply(fresh);
      messages.scrollTop = messages.scrollHeight;
    } catch { /* progress is best-effort */ }
  }, 700);

  // Earlier turns of this conversation, so follow-up messages make sense
  const history = (current ? current.entries.slice(0, -1) : []).slice(-12).map((entry) => (
    entry.kind === "user" ? { role: "user", content: entry.text }
      : entry.kind === "result" ? { role: "assistant", content: entry.result.final_answer || "" } : null)).filter(Boolean);

  const session = current;
  try {
    const result = await api("/api/submit", {
      method: "POST", signal: running.signal,
      body: JSON.stringify({ task, mode: team ? "team" : "agent", run_id: runId, access: $("access").value,
                             effort: $("effort").value, history }),
    });
    record({ kind: "result", result });
    if (current === session) { waiting.remove(); renderResult(result); }
  } catch (error) {
    const text = error.name === "AbortError" ? "Stopped." : error.message;
    record({ kind: "error", text });
    if (current === session) { waiting.remove(); addMessage("agent error", el("div", { class: "body" }, text)); }
  } finally {
    clearInterval(timer);
    running = null;
    $("send").classList.remove("stop"); $("send").title = "Send";
    taskInput.focus();
    refreshStatus();
  }
});

// ------------------------------------------------- chat box: provider + model
// provider -> (API key, asked once) -> that provider's models -> configured.

const CHAT_PROVIDERS = { openai: "OpenAI", google_genai: "Google", anthropic: "Claude", xai: "Grok" };
const modelLists = {}; // provider id -> models its key can use

function savedKeyFor(providerId) {
  const found = providers.find((p) => p.id === providerId);
  return Boolean(found && found.has_key);
}

function showChatModels(provider, models) {
  const select = $("chat-model");
  const inUse = currentModel && currentModel.startsWith(provider + ":") ? currentModel.slice(provider.length + 1) : "";
  const options = models.map((m) => el("option", { value: m.id }, m.label || m.id));
  if (inUse && !models.some((m) => m.id === inUse)) options.unshift(el("option", { value: inUse }, inUse));
  select.replaceChildren(el("option", { value: "", hidden: true }, "Choose a model"), ...options);
  select.value = inUse;
  select.hidden = false;
}

function fillChatModels() {
  // Called after the catalog loads: show the provider and model in use now.
  const provider = currentModel ? currentModel.split(":")[0] : "";
  $("chat-provider").dataset.ready = "1";
  if (!CHAT_PROVIDERS[provider]) { $("chat-provider").value = ""; $("chat-model").hidden = true; }
  else {
    $("chat-provider").value = provider;
    const known = providers.find((p) => p.id === provider);
    showChatModels(provider, modelLists[provider] || (known && known.source === "provider" ? known.models : []));
  }
  renderEmptyExtras();
}

function askForKey(provider, reason) {
  // Resolves with the provider's models once a key is accepted, or null if cancelled.
  return new Promise((resolve) => {
    const dialog = $("key-dialog");
    $("key-title").textContent = "Enter your " + CHAT_PROVIDERS[provider] + " API key";
    $("key-input").value = "";
    note("key-error", reason ? "fail" : "", reason || "");
    const finish = (value) => { $("key-form").onsubmit = null; $("key-cancel").onclick = null; dialog.oncancel = null; dialog.close(); resolve(value); };
    $("key-cancel").onclick = () => finish(null);
    dialog.oncancel = (event) => { event.preventDefault(); finish(null); };
    $("key-form").onsubmit = async (event) => {
      event.preventDefault();
      const key = $("key-input").value.trim();
      if (!key) return note("key-error", "fail", "Paste your API key first.");
      $("key-ok").disabled = true;
      note("key-error", "muted", "Checking the key with " + CHAT_PROVIDERS[provider] + "…");
      try {
        const saved = await api("/api/settings/key", { method: "POST", body: JSON.stringify({ provider, api_key: key }) });
        finish(saved.models);
      } catch (error) {
        note("key-error", "fail", error.message);
      } finally {
        $("key-ok").disabled = false;
      }
    };
    dialog.showModal();
    $("key-input").focus();
  });
}

async function providerModels(provider) {
  // The models this provider's key can use; asks for the key if there is no working one.
  if (modelLists[provider]) return modelLists[provider];
  let models = null;
  let reason = "";
  if (savedKeyFor(provider)) {
    try {
      models = (await api("/api/settings/models/list", { method: "POST", body: JSON.stringify({ provider }) })).models;
    } catch (error) { reason = error.message; } // saved key no longer works: ask for a new one
  }
  if (!models) {
    models = await askForKey(provider, reason);
    if (models) await loadProviders(true);
  }
  if (models) modelLists[provider] = models;
  return models;
}

async function chooseProvider(provider) {
  $("chat-model").hidden = true;
  const models = await providerModels(provider);
  if (!models) return fillChatModels(); // cancelled: go back to what was in use
  $("chat-provider").value = provider;
  showChatModels(provider, models);
  if (!$("chat-model").value) $("chat-model").focus();
}

$("chat-provider").addEventListener("change", () => chooseProvider($("chat-provider").value));

$("chat-model").addEventListener("change", async () => {
  const provider = $("chat-provider").value;
  const model = $("chat-model").value;
  if (!provider || !model) return;
  try {
    const saved = await api("/api/settings/model", { method: "POST", body: JSON.stringify({ provider, model, test: false }) });
    if (!saved.ok) throw new Error(saved.message);
    currentModel = provider + ":" + model;
    renderEmptyExtras();
  } catch (error) {
    addMessage("agent error", el("div", { class: "body" }, "Could not switch model: " + error.message));
    fillChatModels();
  }
  refreshStatus();
});

async function loadProviders(refresh) {
  try {
    const catalog = await api("/api/settings/catalog" + (refresh ? "?refresh=true" : ""));
    providers = catalog.providers;
    currentModel = catalog.current;
    fillChatModels();
  } catch { /* shown by the status line */ }
}

// ----------------------------------------------------------------- approvals

async function loadApprovals() {
  const list = $("approvals");
  try {
    const { approvals } = await api("/api/approvals?status=pending");
    if (!approvals.length) return list.replaceChildren(el("p", { class: "empty-note" }, "Nothing is waiting for you."));
    list.replaceChildren(...approvals.map((a) => {
      const summary = ((a.verification || {}).final_output || {}).summary;
      const card = el("div", { class: "card", "data-reload": "1" },
        el("div", { class: "title" }, a.task, pill(`confidence ${percent(a.confidence)}`, "warn"), pill(a.channel)),
        el("p", {}, a.final_answer || summary || ""),
        el("details", {}, el("summary", {}, "Steps"), stepList(a.plan || {}, a.executor_results)));
      card.append(approvalActions(a.id, card));
      return card;
    }));
  } catch (error) { list.replaceChildren(el("p", { class: "empty-note" }, error.message)); }
}

// --------------------------------------------------------------------- tools

const SERVICES = { github: "GitHub", weather: "Weather", news: "News", discord: "Discord", slack: "Slack" };
const PERMISSION_TEXT = { allow: "Runs on its own", ask: "Asks you first", deny: "Blocked" };
let allTools = [];

function toolLabel(name, service) {
  const rest = name.startsWith(service + "_") ? name.slice(service.length + 1) : name;
  const words = rest.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function renderTools() {
  const list = $("tools");
  const query = $("tool-search").value.trim().toLowerCase();
  const shown = allTools.filter((t) => !query || (t.name + " " + t.description).toLowerCase().includes(query));
  const count = (kind) => allTools.filter((t) => t.permission === kind).length;
  $("tools-summary").textContent = allTools.length
    ? `${allTools.length} tools connected. ${count("allow")} run on their own, ${count("ask")} ask you first, ${count("deny")} blocked.`
    : "What the agent can use, and whether it must ask first.";
  if (!allTools.length) return list.replaceChildren(el("p", { class: "empty-note" }, "No tools are connected."));
  if (!shown.length) return list.replaceChildren(el("p", { class: "empty-note" }, "No tool matches your search."));

  const groups = new Map();
  for (const tool of shown) {
    const service = tool.name.split("_")[0];
    if (!groups.has(service)) groups.set(service, []);
    groups.get(service).push(tool);
  }
  list.replaceChildren(...[...groups.entries()].map(([service, tools]) => {
    const name = SERVICES[service] || service.charAt(0).toUpperCase() + service.slice(1);
    const kinds = [...new Set(tools.map((t) => t.permission))];
    return el("div", { class: "card tool-group" },
      el("div", { class: "tool-group-head" },
        el("span", { class: "avatar" }, name.charAt(0)),
        el("div", {}, el("div", { class: "name" }, name), el("div", { class: "muted" }, tools.length + (tools.length === 1 ? " tool" : " tools"))),
        el("div", { class: "legend" }, kinds.map((k) => pill(PERMISSION_TEXT[k] || k, k)))),
      tools.map((t) => el("div", { class: "tool-row" },
        el("div", { class: "label" }, toolLabel(t.name, service), el("code", {}, t.name)),
        el("div", { class: "desc" }, t.description),
        pill(PERMISSION_TEXT[t.permission] || t.permission, t.permission))));
  }));
}

async function loadTools() {
  const list = $("tools");
  if (!allTools.length) list.replaceChildren(el("p", { class: "empty-note" }, "Loading tools…"));
  try {
    allTools = (await api("/api/tools")).tools;
    renderTools();
  } catch (error) { list.replaceChildren(el("p", { class: "empty-note" }, error.message)); }
}
$("tool-search").addEventListener("input", renderTools);

// --------------------------------------------------------------- automations

function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? `today at ${time}` : `${date.toLocaleDateString([], { day: "numeric", month: "short" })} at ${time}`;
}

function deleteButton(onConfirm) {
  // Two clicks instead of a pop-up: the first arms it, the second deletes
  const button = el("button", { class: "btn small secondary danger", type: "button" }, "Delete");
  let armed = null;
  button.addEventListener("click", () => {
    if (armed) { clearTimeout(armed); button.disabled = true; onConfirm(); return; }
    button.textContent = "Click again to delete";
    armed = setTimeout(() => { armed = null; button.textContent = "Delete"; }, 4000);
  });
  return button;
}

let automationTimer = null;

async function loadAutomations() {
  const list = $("automations");
  clearTimeout(automationTimer);
  try {
    const { automations } = await api("/api/automations");
    if (!automations.length) {
      return list.replaceChildren(el("p", { class: "empty-note" }, "No automations yet. Create one to have the agent work on a schedule."));
    }
    const statusPill = { ok: ["done", "ok"], review: ["needs approval", "warn"], failed: ["failed", "fail"], skipped: ["missed", "warn"] };
    list.replaceChildren(...automations.map((a) => {
      const act = async (path, options) => {
        try { await api(path, options); await loadAutomations(); }
        catch (error) { await loadAutomations(); list.prepend(el("p", { class: "note fail" }, error.message)); }
      };
      const last = a.last_status ? statusPill[a.last_status] : null;
      return el("div", { class: "card" },
        el("div", { class: "title" }, a.name, pill(a.mode === "team" ? "team of agents" : "one agent"),
          pill(a.model ? a.model.split(":").slice(1).join(":") : "chat's model"),
          a.running ? pill("running now", "warn") : null,
          el("label", { class: "switch" },
            el("input", { type: "checkbox", checked: a.enabled, onchange: (e) => act(`/api/automations/${a.id}`, { method: "PATCH", body: JSON.stringify({ enabled: e.target.checked }) }) }),
            a.enabled ? "On" : "Paused")),
        el("p", {}, a.task),
        el("p", { class: "muted" }, a.schedule + (a.enabled && a.next_run_at ? ` · next run ${when(a.next_run_at)}` : "")),
        a.last_run_at ? el("div", { class: "result" },
          el("div", {}, pill(last[0], last[1]), " last run " + when(a.last_run_at)), a.last_result || "") : null,
        el("div", { class: "actions" },
          el("button", { class: "btn small secondary", disabled: a.running, onclick: () => act(`/api/automations/${a.id}/run`, { method: "POST" }) }, "Run now"),
          deleteButton(() => act(`/api/automations/${a.id}`, { method: "DELETE" }))));
    }));
    // While something is running, keep the page fresh
    if (automations.some((a) => a.running) && $("view-automations").classList.contains("active")) {
      automationTimer = setTimeout(loadAutomations, 3000);
    }
  } catch (error) { list.replaceChildren(el("p", { class: "empty-note" }, error.message)); }
}

$("automation-new").addEventListener("click", () => { $("automation-form").hidden = false; $("auto-name").focus(); });
$("automation-cancel").addEventListener("click", () => { $("automation-form").hidden = true; });
$("auto-kind").addEventListener("change", () => {
  const daily = $("auto-kind").value === "daily";
  $("auto-time-field").hidden = !daily;
  $("auto-days-field").hidden = !daily;
  $("auto-every-field").hidden = daily;
});
$("auto-provider").addEventListener("change", async () => {
  const provider = $("auto-provider").value;
  $("auto-model-field").hidden = true;
  if (!provider) return;
  const models = await providerModels(provider);
  if (!models) { $("auto-provider").value = ""; return; } // key dialog cancelled
  $("auto-model").replaceChildren(el("option", { value: "" }, "Choose a model…"), ...models.map((m) => el("option", { value: m.id }, m.label || m.id)));
  $("auto-model-field").hidden = false;
});

$("automation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const provider = $("auto-provider").value;
    if (provider && !$("auto-model").value) throw new Error("Choose a model for " + CHAT_PROVIDERS[provider] + ", or pick \"Same as the chat\".");
    await api("/api/automations", { method: "POST", body: JSON.stringify({
      name: $("auto-name").value, task: $("auto-task").value, mode: $("auto-mode").value, kind: $("auto-kind").value,
      every_minutes: Number($("auto-every").value), daily_time: $("auto-time").value || "09:00",
      weekdays: $("auto-days").value ? $("auto-days").value.split(",").map(Number) : [],
      model: provider ? provider + ":" + $("auto-model").value : "",
    }) });
    $("automation-form").reset();
    $("auto-time-field").hidden = false; $("auto-every-field").hidden = true; $("auto-model-field").hidden = true;
    $("automation-form").hidden = true;
    note("automation-error");
    loadAutomations();
  } catch (error) { note("automation-error", "fail", error.message); }
});

// -------------------------------------------------------------------- logins

async function loadLogins() {
  const list = $("logins");
  try {
    const { logins } = await api("/api/vault");
    if (!logins.length) return list.replaceChildren(el("p", { class: "empty-note" }, "No saved logins yet."));
    list.replaceChildren(...logins.map((login) => el("div", { class: "card tool-row" },
      el("div", { class: "label" }, login.site, el("code", {}, login.username)),
      el("div", { class: "desc" }, "Password saved, hidden from the agent"),
      deleteButton(async () => { await api(`/api/vault/${login.id}`, { method: "DELETE" }); loadLogins(); }))));
  } catch (error) { list.replaceChildren(el("p", { class: "empty-note" }, error.message)); }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/vault", { method: "POST", body: JSON.stringify({
      site: $("login-site").value, username: $("login-user").value, password: $("login-pass").value }) });
    $("login-form").reset();
    note("login-error");
    loadLogins();
  } catch (error) { note("login-error", "fail", error.message); }
});

// ------------------------------------------------------------------- updates

let updateInfo = null;

function showUpdate(info) {
  updateInfo = info;
  $("update-title").textContent = `Version ${info.latest} is available`;
  $("update-sub").textContent = `You have ${info.current}. ` + (info.install === "source"
    ? "This copy runs from source code, so update it with git pull."
    : info.install === "packaged" ? "Update downloads the new installer." : "Update installs it and restarts the app.");
  $("update-notes").textContent = info.notes || "";
  $("update-now").textContent = info.install === "source" ? "OK" : info.install === "packaged" ? "Open download page" : "Update now";
  note("update-status");
  $("update-dialog").showModal();
}

async function checkForUpdate() {
  try {
    const info = await api("/api/update");
    if (!info.newer) return;
    $("update-badge").hidden = false;
    $("update-badge").textContent = `Update available: ${info.latest}`;
    // Ask once per version; the badge stays for later
    if (store.get("agentd.update.dismissed", "") !== info.latest) showUpdate(info);
  } catch { /* offline or GitHub unreachable: try again next time */ }
}

$("update-badge").addEventListener("click", () => updateInfo && showUpdate(updateInfo));
$("update-later").addEventListener("click", () => {
  if (updateInfo) store.set("agentd.update.dismissed", updateInfo.latest);
  $("update-dialog").close();
});
$("update-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!updateInfo || updateInfo.install === "source") return $("update-dialog").close();
  if (updateInfo.install === "packaged") { window.open(updateInfo.url, "_blank"); return $("update-dialog").close(); }
  $("update-now").disabled = true; $("update-later").disabled = true;
  note("update-status", "muted", "Downloading and installing… this can take a minute.");
  try {
    const result = await api("/api/update/install", { method: "POST" });
    note("update-status", result.ok ? "ok" : "fail", result.message);
    if (result.open) window.open(result.open, "_blank");
    if (!result.ok) { $("update-now").disabled = false; $("update-later").disabled = false; }
  } catch (error) {
    // The app restarting cuts the connection; that is expected after a successful install
    note("update-status", "muted", "Restarting…");
  }
});

// ---------------------------------------------------------------------- boot

function route() {
  const target = location.hash.slice(1);
  if (target === "agents") startNew("team");
  else if (target === "chat") startNew("single");
  else if (target && $("view-" + target)) show(target);
}
window.addEventListener("hashchange", route);

$("access").value = store.get("agentd.access", "ask") === "auto" ? "auto" : "ask";
$("access").addEventListener("change", async () => {
  store.set("agentd.access", $("access").value);
  if (running && running.runId) {  // applies to the task already running, from its next action
    try { await api(`/api/runs/${running.runId}/access`, { method: "POST", body: JSON.stringify({ access: $("access").value }) }); }
    catch { /* the task may have just finished */ }
  }
});
$("effort").value = ["low", "medium", "high"].includes(store.get("agentd.effort", "")) ? store.get("agentd.effort", "") : "";
$("effort").addEventListener("change", () => store.set("agentd.effort", $("effort").value));

renderRecents();
applyMode();
loadProviders();
refreshStatus();
setInterval(refreshStatus, 15000);
setTimeout(checkForUpdate, 4000);
setInterval(checkForUpdate, 6 * 3600 * 1000);
route();
