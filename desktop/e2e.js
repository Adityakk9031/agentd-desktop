"use strict";
/*
 * End-to-end check, run inside the app's window by `npm run e2e`.
 * It uses the screens exactly as a person would (typing, clicking) against
 * the real agent and whatever model is configured, and returns a report.
 * This file is the page-side script; main.js loads and runs it.
 */
module.exports = `(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (id) => document.getElementById(id);
  const waitFor = async (check, ms, step = 300) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const value = check(); if (value) return value; await wait(step); }
    return null;
  };
  const report = {};
  const attempt = async (name, run) => {
    const started = Date.now();
    try { report[name] = { ok: true, ...(await run()) }; }
    catch (error) { report[name] = { ok: false, error: String(error && error.message || error) }; }
    report[name].seconds = Math.round((Date.now() - started) / 1000);
  };
  const send = (text) => { $("task").value = text; $("composer").requestSubmit(); };
  const lastAgent = () => [...document.querySelectorAll("#messages .msg.agent")].pop();
  const finished2 = () => { const all = [...document.querySelectorAll("#messages .msg.agent")]; const m = all[all.length - 1];
    return all.length >= 2 && m && (m.querySelector(".meta") || m.classList.contains("error")) ? m : null; };
  const finished = () => { const m = lastAgent(); return m && (m.querySelector(".meta") || m.classList.contains("error")) ? m : null; };

  await waitFor(() => $("chat-provider").dataset.ready === "1", 45000);
  report.model = $("status-text").textContent;

  await attempt("chat", async () => {
    location.hash = "#chat"; await wait(400);
    send("Reply with exactly one word: pong");
    const stopShown = $("send").classList.contains("stop");
    const done = await waitFor(finished, 180000);
    if (!done) throw new Error("no answer within 3 minutes");
    if (done.classList.contains("error")) throw new Error(done.textContent.slice(0, 300));
    return { stopShown, answer: done.querySelector(".body").textContent.slice(0, 120), badge: done.querySelector(".meta").textContent,
             recents: document.querySelectorAll("#recents .recent").length, sendRestored: !$("send").classList.contains("stop") };
  });

  await attempt("approval", async () => {
    // Ask mode: the agent must pause and show a card before writing a file
    $("new-chat").click(); await wait(300);
    $("access").value = "ask"; $("access").dispatchEvent(new Event("change"));
    const file = window.E2E_DIR.split(String.fromCharCode(92)).join("/") + "/e2e-note.txt";
    send("Create a text file at " + file + " containing exactly: hello from agentd");
    const card = await waitFor(() => document.querySelector("#messages .ask:not(.done)"), 120000);
    if (!card) throw new Error("no approval card appeared: " + (lastAgent() ? lastAgent().textContent.slice(0, 200) : ""));
    const question = card.querySelector(".q").textContent, detail = (card.querySelector("pre") || {}).textContent;
    const stillWorking = Boolean(document.querySelector("#messages .working"));
    [...card.querySelectorAll("button")].find((b) => b.textContent === "Allow once").click();
    const done = await waitFor(finished, 120000);
    if (!done) throw new Error("the agent did not finish after approval");
    return { question, detail, pausedWhileAsking: stillWorking, answer: done.querySelector(".body").textContent.slice(0, 160),
             actions: (done.querySelector("details summary") || {}).textContent };
  });

  await attempt("followUp", async () => {
    send("What exact text did you just put in that file? Answer in one line.");
    const done = await waitFor(finished2, 120000);
    if (!done) throw new Error("no answer");
    return { answer: done.querySelector(".body").textContent.slice(0, 160) };
  });

  await attempt("stop", async () => {
    $("new-chat").click(); await wait(300);
    send("Write a 4000 word essay about rivers");
    await wait(2500);
    $("composer").requestSubmit(); // the button is Stop now
    const done = await waitFor(() => { const m = lastAgent(); return m && m.classList.contains("error") ? m : null; }, 20000);
    if (!done) throw new Error("the task did not stop");
    return { message: done.textContent };
  });

  await attempt("agents", async () => {
    location.hash = "#agents"; await wait(500);
    const chip = !$("mode-chip").hidden;
    send("Use one agent per city: get the current weather in Bangalore and in Delhi with the weather tool, then compare them in two lines.");
    let rowsWhileRunning = 0, sawWorking = false, headings = new Set();
    const done = await waitFor(() => {
      const board = document.querySelector("#messages .msg.agent:last-child .board");
      if (board && !finished()) {
        rowsWhileRunning = Math.max(rowsWhileRunning, board.querySelectorAll(".agent").length);
        if (board.querySelector(".agent.working")) sawWorking = true;
        headings.add(board.querySelector(".board-head").textContent);
      }
      return finished();
    }, 300000, 250);
    if (!done) throw new Error("no answer within 5 minutes");
    if (done.classList.contains("error")) throw new Error(done.textContent.slice(0, 300));
    const agents = [...done.querySelectorAll(".agent")].map((a) => ({
      who: a.querySelector(".who").textContent, state: a.className.replace("agent ", ""),
      tools: [...a.querySelectorAll(".tools .pill")].map((p) => p.textContent), hasReport: Boolean(a.querySelector("details")) }));
    return { chip, rowsWhileRunning, sawWorking, liveHeadings: [...headings], agents,
             answer: done.querySelector(":scope > .body").textContent.slice(0, 260), badge: done.querySelector(".meta").textContent };
  });

  await attempt("recents", async () => {
    const items = [...document.querySelectorAll("#recents .recent")];
    const tags = items.map((i) => (i.querySelector(".tag") ? "team" : "chat"));
    items[items.length - 1].click(); await wait(400); // the first chat
    return { count: items.length, tags, reopenedMessages: document.querySelectorAll("#messages .msg").length,
             firstText: (document.querySelector("#messages .msg.user .body") || {}).textContent };
  });

  await attempt("automation", async () => {
    location.hash = "#automations"; await wait(600);
    $("automation-new").click();
    $("auto-name").value = "E2E check";
    $("auto-task").value = "Reply with exactly one word: ready";
    $("auto-kind").value = "interval"; $("auto-kind").dispatchEvent(new Event("change"));
    const intervalFieldShown = !$("auto-every-field").hidden && $("auto-time-field").hidden;
    $("automation-form").requestSubmit();
    const card = await waitFor(() => document.querySelector("#automations .card"), 10000);
    if (!card) throw new Error("the automation did not appear: " + $("automation-error").textContent);
    const created = card.textContent.slice(0, 140);
    [...card.querySelectorAll("button")].find((b) => b.textContent === "Run now").click();
    const result = await waitFor(() => { const r = document.querySelector("#automations .card .result"); return r && !/running now/.test(document.querySelector("#automations .card .title").textContent) ? r : null; }, 180000, 1000);
    if (!result) throw new Error("the run did not finish within 3 minutes");
    const text = result.textContent.slice(0, 160);
    const del = [...document.querySelectorAll("#automations .card button")].find((b) => b.textContent === "Delete");
    del.click(); await wait(200);
    const armedText = del.textContent;
    del.click();
    const gone = await waitFor(() => !document.querySelector("#automations .card"), 8000);
    if (!gone) throw new Error("the automation was not deleted");
    return { intervalFieldShown, created, result: text, armedText, deleted: true };
  });

  await attempt("tools", async () => {
    location.hash = "#tools";
    if (!(await waitFor(() => document.querySelector(".tool-group"), 30000))) throw new Error("tools did not load");
    const groups = document.querySelectorAll(".tool-group").length, rows = document.querySelectorAll(".tool-row").length;
    $("tool-search").value = "weather"; $("tool-search").dispatchEvent(new Event("input"));
    return { summary: $("tools-summary").textContent, groups, rows, rowsAfterSearch: document.querySelectorAll(".tool-row").length };
  });

  await attempt("approvals", async () => {
    location.hash = "#approvals"; await wait(1500);
    return { shows: $("approvals").textContent.slice(0, 80) };
  });

  return JSON.stringify(report);
})()`;
