"use strict";
/*
 * agentd desktop shell (Electron, Windows + macOS).
 *
 * The app starts the agentd runtime as a background process on a random
 * localhost port, protected by a random token, waits until it is ready, and
 * then shows the runtime's own screens (served at /app). When the window
 * closes, the runtime is stopped.
 *
 * Runtime lookup order:
 *   1. A bundled runtime in <resources>/runtime/agentd(.exe)   (packaged app)
 *   2. AGENTD_PYTHON, else `python` / `python3`, running `-m agentd`
 */

const { app, BrowserWindow, session, shell, dialog } = require("electron");
const { spawn, execFile } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const E2E = process.argv.includes("--e2e");      // drive the real screens end to end, print a report, quit
const SMOKE = process.argv.includes("--smoke") || E2E; // start, check, quit (used by tests)
if (SMOKE) {
  // Tests get their own profile, so they never touch the user's chats and can run while the app is open
  app.setPath("userData", path.join(app.getPath("temp"), "agentd-test-profile"));
}
const START_TIMEOUT_MS = 180_000;
const isWindows = process.platform === "win32";

let runtime = null;      // child process
let runtimeLog = "";     // last output, shown if startup fails
let mainWindow = null;
let quitting = false;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function runtimeCommand(port) {
  const args = ["serve", "--host", "127.0.0.1", "--port", String(port)];
  const bundled = path.join(process.resourcesPath || "", "runtime", isWindows ? "agentd.exe" : "agentd");
  if (app.isPackaged && fs.existsSync(bundled)) {
    return { command: bundled, args, cwd: app.getPath("userData") };
  }
  const python = process.env.AGENTD_PYTHON || (isWindows ? "python" : "python3");
  // In development run from the repository so .env and data/ are found there;
  // an installed app keeps its settings in the user's app-data folder.
  const cwd = app.isPackaged ? app.getPath("userData") : path.resolve(__dirname, "..");
  return { command: python, args: ["-m", "agentd", ...args], cwd };
}

function startRuntime(port, token) {
  const { command, args, cwd } = runtimeCommand(port);
  fs.mkdirSync(cwd, { recursive: true });
  runtime = spawn(command, args, {
    cwd,
    env: { ...process.env, AGENTD_API_TOKEN: token, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8",
           ...(app.isPackaged ? { AGENTD_PACKAGED: "1" } : {}) },
    windowsHide: true,
  });
  const remember = (chunk) => { runtimeLog = (runtimeLog + chunk.toString()).slice(-4000); };
  runtime.stdout.on("data", remember);
  runtime.stderr.on("data", remember);
  return new Promise((resolve, reject) => {
    runtime.once("error", (error) => reject(new Error(
      `Could not start the agent (${command}): ${error.message}\n` +
      "Install Python 3.11+ and run `pip install -e .` in the project, or set AGENTD_PYTHON.")));
    runtime.once("exit", (code) => {
      runtime = null;
      if (code === 75 && !quitting) {  // the runtime installed an update and asks to be restarted
        quitting = true;
        app.relaunch();
        app.exit(0);
        return;
      }
      if (!quitting) reject(new Error(`The agent stopped unexpectedly (exit code ${code}).`));
    });
    waitForHealth(port).then(resolve, reject);
  });
}

function waitForHealth(port) {
  const deadline = Date.now() + START_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      if (!runtime) return; // exit handler already rejected
      const request = http.get({ host: "127.0.0.1", port, path: "/health", timeout: 2000 }, (response) => {
        response.resume();
        if (response.statusCode === 200) resolve();
        else retry();
      });
      request.on("error", retry);
      request.on("timeout", () => request.destroy());
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error("The agent took too long to start."));
      else setTimeout(attempt, 500);
    };
    attempt();
  });
}

function stopRuntime() {
  if (!runtime) return;
  const pid = runtime.pid;
  if (isWindows) execFile("taskkill", ["/pid", String(pid), "/T", "/F"], () => {});
  else runtime.kill("SIGTERM");
  runtime = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 720,
    minHeight: 480,
    show: !SMOKE,
    backgroundColor: "#1b1b1a",
    title: "agentd",
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  return mainWindow;
}

function lockDown(window, origin) {
  // The window only ever shows the local agent. Links to the web open in the
  // user's normal browser; nothing else may navigate or open windows.
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(origin + "/")) {
      event.preventDefault();
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
    }
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
}

async function showMessage(window, message, detail = "") {
  await window.loadFile(path.join(__dirname, "loading.html"));
  await window.webContents.executeJavaScript(
    `document.getElementById("message").textContent = ${JSON.stringify(message)};` +
    `document.getElementById("detail").textContent = ${JSON.stringify(detail)};`);
}

async function boot() {
  const window = createWindow();
  await window.loadFile(path.join(__dirname, "loading.html"));

  const port = await freePort();
  const token = crypto.randomBytes(32).toString("hex");
  const origin = `http://127.0.0.1:${port}`;

  // Attach the token to requests for the local agent only. The page itself
  // never sees it, and it is never sent to any other address.
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, callback) => {
    details.requestHeaders.Authorization = `Bearer ${token}`;
    callback({ requestHeaders: details.requestHeaders });
  });
  lockDown(window, origin);

  try {
    await startRuntime(port, token);
  } catch (error) {
    if (SMOKE) { console.error(`SMOKE FAIL: ${error.message}\n${runtimeLog}`); app.exit(1); return; }
    if (mainWindow) await showMessage(mainWindow, error.message, runtimeLog.trim().split("\n").slice(-12).join("\n"));
    return;
  }
  if (!mainWindow) return;
  await mainWindow.loadURL(`${origin}/app/`);

  if (E2E) await e2e(mainWindow);
  else if (SMOKE) await smoke(mainWindow, origin, token);
}

async function e2e(window) {
  window.showInactive();
  await window.webContents.executeJavaScript("localStorage.clear()");
  await window.webContents.reload();
  await new Promise((r) => setTimeout(r, 1500));
  const e2eDir = path.join(app.getPath("temp"), "agentd-e2e-files");
  fs.rmSync(e2eDir, { recursive: true, force: true });
  fs.mkdirSync(e2eDir, { recursive: true });
  await window.webContents.executeJavaScript(`window.E2E_DIR = ${JSON.stringify(e2eDir)}`);
  let report;
  try {
    report = JSON.parse(await window.webContents.executeJavaScript(require(process.env.AGENTD_E2E_SCRIPT || "./e2e.js")));
  } catch (error) {
    console.log(`E2E FAIL: ${error.message}`); quitting = true; stopRuntime(); app.exit(1); return;
  }
  fs.writeFileSync(path.join(app.getPath("temp"), "agentd-e2e.png"), (await window.webContents.capturePage()).toPNG());
  const note = path.join(e2eDir, "e2e-note.txt");
  report.fileOnDisk = fs.existsSync(note) ? fs.readFileSync(note, "utf-8").trim() : "(file was not created)";
  console.log("E2E REPORT " + JSON.stringify(report, null, 1));
  const failed = Object.entries(report).filter(([, value]) => value && value.ok === false).map(([name]) => name);
  console.log(failed.length ? `E2E FAIL: ${failed.join(", ")}` : "E2E OK");
  quitting = true;
  stopRuntime();
  app.exit(failed.length ? 1 : 0);
}

async function smoke(window, origin, token) {
  // Proves the whole chain: runtime up, token enforced, screens rendered.
  const status = (headers) => new Promise((resolve) => {
    http.get(`${origin}/api/status`, { headers }, (r) => { r.resume(); resolve(r.statusCode); }).on("error", () => resolve(0));
  });
  const withoutToken = await status({});
  const withToken = await status({ Authorization: `Bearer ${token}` });
  await new Promise((r) => setTimeout(r, 2500));
  const statusText = await window.webContents.executeJavaScript('document.getElementById("status-text").textContent');
  // Models screen: load the provider's model list through the real form
  await window.loadURL(`${origin}/app/#automations`);
  await new Promise((r) => setTimeout(r, 2500));
  // Pictures of the main screens
  window.showInactive();
  for (const name of ["chat", "agents", "automations", "tools"]) {
    await window.loadURL(`${origin}/app/#${name}`);
    await window.webContents.executeJavaScript(`(async () => {
      const p = document.getElementById("chat-provider");
      for (let i = 0; i < 150 && p.dataset.ready !== "1"; i++) await new Promise((r) => setTimeout(r, 300));
      for (let i = 0; i < 80 && location.hash === "#tools" && !document.querySelector(".tool-group"); i++) await new Promise((r) => setTimeout(r, 300));
      await new Promise((r) => setTimeout(r, 900));
    })()`);
    fs.writeFileSync(path.join(app.getPath("temp"), `agentd-ui-${name}.png`), (await window.webContents.capturePage()).toPNG());
  }

  // Picture of the chat box with the API-key dialog open
  window.showInactive();
  await window.loadURL(`${origin}/app/#chat`);
  await window.webContents.executeJavaScript(`(async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const provider = document.getElementById("chat-provider");
    for (let i = 0; i < 150 && provider.dataset.ready !== "1"; i++) await wait(300);
    provider.value = "xai"; provider.dispatchEvent(new Event("change"));
    await wait(1200);
  })()`);
  fs.writeFileSync(path.join(app.getPath("temp"), "agentd-smoke-dialog.png"), (await window.webContents.capturePage()).toPNG());
  await window.webContents.executeJavaScript('document.getElementById("key-cancel").click()');
  await window.loadURL(`${origin}/app/#automations`);
  await new Promise((r) => setTimeout(r, 1500));
  const loaded = await window.webContents.executeJavaScript(`(async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const started = Date.now();
    while (document.getElementById("chat-provider").dataset.ready !== "1" && Date.now() - started < 45000) await wait(300);
    // Chat box: four providers; one without a key must ask for it in a dialog
    const chatProvider = document.getElementById("chat-provider");
    const chatLabels = [...chatProvider.options].filter((o) => o.value).map((o) => o.textContent);
    chatProvider.value = "xai";
    chatProvider.dispatchEvent(new Event("change"));
    await wait(800);
    const dialog = document.getElementById("key-dialog");
    const asked = dialog.open ? document.getElementById("key-title").textContent : "(no dialog)";
    document.getElementById("key-cancel").click();
    // A provider whose key works must list only its own models
    chatProvider.value = "google_genai";
    chatProvider.dispatchEvent(new Event("change"));
    const chatModel = document.getElementById("chat-model");
    while (chatModel.hidden && Date.now() - started < 70000) await wait(300);
    const chat = { chatLabels, asked, googleModels: chatModel.options.length - 1, first: chatModel.options[1] ? chatModel.options[1].textContent : "" };
    return JSON.stringify({ chat, modelsNav: Boolean(document.querySelector('[data-view="models"]')) });
  })()`);
  const picker = JSON.parse(loaded);
  const chatOk = picker.chat.chatLabels.join() === "OpenAI,Google,Claude,Grok" && /Grok API key/.test(picker.chat.asked) && picker.chat.googleModels > 0;
  if (!chatOk || picker.modelsNav) {
    console.log(`SMOKE FAIL models: ${loaded}`); quitting = true; stopRuntime(); app.exit(1); return;
  }
  console.log(`SMOKE models: ${loaded}`);
  // Automation form: choosing an AI must offer that provider's models
  const autoModels = await window.webContents.executeJavaScript(`(async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    document.getElementById("automation-new").click();
    document.getElementById("auto-name").value = "Morning brief";
    document.getElementById("auto-task").value = "Find today's top AI news and summarise it in five lines";
    const provider = document.getElementById("auto-provider");
    provider.value = "google_genai"; provider.dispatchEvent(new Event("change"));
    for (let i = 0; i < 100 && document.getElementById("auto-model-field").hidden; i++) await wait(300);
    const select = document.getElementById("auto-model");
    if (select.options.length > 1) select.selectedIndex = 1;
    return select.options.length - 1;
  })()`);
  if (autoModels < 1) { console.log("SMOKE FAIL automation models: " + autoModels); quitting = true; stopRuntime(); app.exit(1); return; }
  console.log("SMOKE automation models: " + autoModels);
  window.showInactive(); // a hidden window does not repaint, so the picture would be stale
  await new Promise((r) => setTimeout(r, 1200));
  const image = await window.webContents.capturePage();
  const out = path.join(app.getPath("temp"), "agentd-smoke.png");
  fs.writeFileSync(out, image.toPNG());
  const ok = withoutToken === 401 && withToken === 200 && statusText && !/not reachable/i.test(statusText);
  console.log(`SMOKE ${ok ? "OK" : "FAIL"}: no-token=${withoutToken} with-token=${withToken} status="${statusText}" screenshot=${out}`);
  quitting = true;
  stopRuntime();
  app.exit(ok ? 0 : 1);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }
  });
  app.whenReady().then(boot).catch((error) => {
    dialog.showErrorBox("agentd could not start", String(error && error.message || error));
    app.exit(1);
  });
  app.on("activate", () => { if (!mainWindow && !quitting) boot(); });
  app.on("window-all-closed", () => { quitting = true; stopRuntime(); app.quit(); });
  app.on("before-quit", () => { quitting = true; stopRuntime(); });
}
