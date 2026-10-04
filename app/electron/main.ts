import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, nativeImage, session, shell, systemPreferences, Tray } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";

const DEV_URL = process.env.JARVIS_DEV_URL; // e.g. http://localhost:5199 (npm run dev)
const CORE_PORT = Number(process.env.JARVIS_PORT || 8765);
// Packaged J.A.R.V.I.S..app: the repo path is baked into package.json (jarvisRoot) by `npm run dist`,
// because __dirname then points inside the .app bundle. Dev runs resolve it relative to app/.
function bakedRoot(): string {
  try {
    return JSON.parse(fs.readFileSync(path.join(app.getAppPath(), "package.json"), "utf8")).jarvisRoot || "";
  } catch {
    return "";
  }
}
const PROJECT_ROOT =
  process.env.JARVIS_ROOT || (app.isPackaged && bakedRoot()) || path.resolve(__dirname, "..", "..");
const CORE_DIR = path.join(PROJECT_ROOT, "core");
const CORE_PY = path.join(CORE_DIR, ".venv", "bin", "python");
const LOG_DIR = path.join(app.getPath("home"), "Library", "Logs", "Jarvis");
const TOKEN = process.env.JARVIS_TOKEN || randomBytes(24).toString("hex");

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let core: ChildProcess | null = null;
let coreRestarts = 0;
let quitting = false;

fs.mkdirSync(LOG_DIR, { recursive: true });
const log = (...a: unknown[]) =>
  fs.appendFileSync(path.join(LOG_DIR, "app.log"), `${new Date().toISOString()} ${a.map(String).join(" ")}\n`);

// ------------------------------------------------------------------ core process
function probe(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: CORE_PORT, path: "/health", timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

let externalCore = false;

async function startCore(): Promise<void> {
  if (await probe()) {
    // A core is already running (dev mode / started by hand). Use it; it may not know our token.
    externalCore = true;
    log("using already-running core on", CORE_PORT);
    return;
  }
  if (!fs.existsSync(CORE_PY)) {
    log("core venv missing at", CORE_PY);
    return;
  }
  const out = fs.openSync(path.join(LOG_DIR, "core.log"), "a");
  core = spawn(CORE_PY, ["-m", "jarvis_core.main"], {
    cwd: CORE_DIR,
    env: { ...process.env, JARVIS_TOKEN: TOKEN, JARVIS_PORT: String(CORE_PORT), PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", out, out],
  });
  log("core spawned pid", core.pid);
  core.on("exit", (code, sig) => {
    log("core exited", code, sig);
    core = null;
    if (!quitting && coreRestarts < 5) {
      coreRestarts += 1;
      setTimeout(startCore, 1500 * coreRestarts);
    }
  });
  for (let i = 0; i < 60; i++) {
    if (await probe()) return;
    await new Promise((r) => setTimeout(r, 250));
  }
}

// ------------------------------------------------------------------ window
function createWindow(): void {
  win = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 980,
    minHeight: 640,
    title: "J.A.R.V.I.S.",
    icon: path.join(ASSETS, "icon.png"),
    backgroundColor: "#01040a",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 16, y: 14 },
    vibrancy: undefined,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  win.once("ready-to-show", () => win?.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(DEV_URL || "file://")) {
      e.preventDefault();
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
    }
  });
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      win?.hide();
    }
  });
  if (DEV_URL) win.loadURL(DEV_URL);
  else win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

/** The preset showcase from anywhere: ⌥⇧D (in the window ⌘⇧D works too). */
function showcase(): void {
  summon(false);
  win?.webContents.send("jarvis:showcase");
}

function summon(listen: boolean): void {
  if (!win) return;
  if (!win.isVisible()) win.show();
  win.focus();
  if (listen) win.webContents.send("jarvis:listen");
}

// ------------------------------------------------------------------ tray (menu bar)
const ASSETS = path.join(__dirname, "..", "assets");

function trayIcon(): Electron.NativeImage {
  // full-colour JARVIS logo; tray.png (22px) + tray@2x.png (44px) are picked up for Retina automatically
  const img = nativeImage.createFromPath(path.join(ASSETS, "tray.png"));
  return img.isEmpty() ? nativeImage.createEmpty() : img;
}

function buildTray(): void {
  tray = new Tray(trayIcon());
  tray.setToolTip("J.A.R.V.I.S.");
  const menu = () =>
    Menu.buildFromTemplate([
      { label: "Show J.A.R.V.I.S.", accelerator: "Alt+Space", click: () => summon(false) },
      { label: "Talk to J.A.R.V.I.S.", accelerator: "Alt+Shift+Space", click: () => summon(true) },
      { label: "Brief me on my day", accelerator: "Alt+Shift+D", click: () => showcase() },
      { label: "Sleep / power on", accelerator: "Alt+Shift+S", click: () => { summon(false); win?.webContents.send("jarvis:sleep"); } },
      { type: "separator" },
      {
        label: "Launch at Login",
        type: "checkbox",
        checked: app.getLoginItemSettings().openAtLogin,
        click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
      },
      { label: "Open Logs", click: () => shell.openPath(LOG_DIR) },
      { type: "separator" },
      { label: "Quit J.A.R.V.I.S.", accelerator: "Cmd+Q", click: () => { quitting = true; app.quit(); } },
    ]);
  tray.setContextMenu(menu());
  tray.on("click", () => summon(false));
}

// ------------------------------------------------------------------ lifecycle
const single = app.requestSingleInstanceLock();
if (!single) app.quit();
app.on("second-instance", () => summon(false));

app.whenReady().then(async () => {
  app.setName("J.A.R.V.I.S.");
  if (process.platform === "darwin") {
    try {
      await systemPreferences.askForMediaAccess("microphone");
    } catch (e) {
      log("mic permission error", e);
    }
  }
  ipcMain.handle("jarvis:config", () => ({
    coreUrl: `ws://127.0.0.1:${CORE_PORT}/ws`,
    httpUrl: `http://127.0.0.1:${CORE_PORT}`,
    token: externalCore ? process.env.JARVIS_TOKEN || "" : TOKEN,
  }));
  ipcMain.handle("jarvis:pickFolder", async () => {
    const r = await dialog.showOpenDialog({ title: "Open a project for JARVIS", properties: ["openDirectory"],
      defaultPath: path.join(app.getPath("home"), "Documents", "Projects") });
    return r.canceled || !r.filePaths[0] ? null : r.filePaths[0];
  });
  // YouTube Music sign-in. Google refuses sign-in inside embedded app windows ("This browser or app may not be
  // secure"), so this opens REAL Google Chrome with a dedicated, persistent JARVIS profile. Nothing attaches to the
  // page while he signs in (only the HTTP target list is polled); once a tab is on music.youtube.com with a Google
  // session cookie, JARVIS reads the music.youtube.com cookies and hands them to Core (stored 0600 in its state dir).
  // The profile persists, so later sign-ins are one click and usually instant.
  ipcMain.handle("jarvis:ytmLogin", () => new Promise<string | null>((resolve) => {
    const chrome = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      path.join(app.getPath("home"), "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")].find((p) => fs.existsSync(p));
    if (!chrome) { dialog.showErrorBox("Google Chrome needed", "Signing in to YouTube Music needs Google Chrome installed."); resolve(null); return; }
    const profile = path.join(app.getPath("home"), ".hermes", "jarvis", "state", "ytmusic-chrome");
    fs.mkdirSync(profile, { recursive: true });
    const port = 9335;
    const proc = spawn(chrome, [`--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, "--remote-allow-origins=http://127.0.0.1",
      "--no-first-run", "--no-default-browser-check", "--new-window", "https://music.youtube.com/"], { stdio: "ignore", detached: false });
    let done = false;
    const started = Date.now();
    const getJson = (p: string): Promise<any> => new Promise((res, rej) => {
      http.get({ host: "127.0.0.1", port, path: p, timeout: 2000 }, (r) => {
        let b = ""; r.on("data", (c) => (b += c)); r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
      }).on("error", rej).on("timeout", function (this: any) { this.destroy(); rej(new Error("timeout")); });
    });
    const cookiesVia = (wsUrl: string): Promise<any[]> => new Promise((res, rej) => {
      const ws = new WebSocket(wsUrl);
      const t = setTimeout(() => { try { ws.close(); } catch {} rej(new Error("cdp timeout")); }, 5000);
      ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Storage.getCookies" }));
      ws.onmessage = (ev) => { const m = JSON.parse(String(ev.data)); if (m.id === 1) { clearTimeout(t); ws.close(); res(m.result?.cookies || []); } };
      ws.onerror = () => { clearTimeout(t); rej(new Error("cdp error")); };
    });
    const finish = (v: string | null) => { if (done) return; done = true; clearInterval(timer); try { proc.kill(); } catch {} resolve(v); };
    proc.on("exit", () => setTimeout(() => finish(null), 300));
    const timer = setInterval(async () => {
      if (Date.now() - started > 10 * 60_000) return finish(null);
      try {
        const tabs: any[] = await getJson("/json/list");
        if (!tabs.some((t) => t.type === "page" && String(t.url).startsWith("https://music.youtube.com"))) return;
        const ver = await getJson("/json/version");
        const all = await cookiesVia(ver.webSocketDebuggerUrl);
        const yt = all.filter((c) => /(^|\.)youtube\.com$/.test(String(c.domain).replace(/^\./, "")) || String(c.domain).endsWith(".youtube.com"));
        if (!yt.some((c) => c.name === "SAPISID" || c.name === "__Secure-3PAPISID")) return;
        if (!yt.some((c) => c.name === "LOGIN_INFO")) return; // fully signed in to YouTube, not just Google
        finish(yt.map((c) => `${c.name}=${c.value}`).join("; "));
      } catch { /* Chrome still starting */ }
    }, 1500);
  }));
  ipcMain.handle("jarvis:ytmLogout", async () => {
    // Forget JARVIS's Chrome profile too, so the next sign-in starts clean.
    fs.rmSync(path.join(app.getPath("home"), ".hermes", "jarvis", "state", "ytmusic-chrome"), { recursive: true, force: true });
    return true;
  });
  ipcMain.on("jarvis:open", (_e, url: string) => {
    if (/^https?:\/\//.test(url) || /^tel:\+?[\d()\-. ]{3,20}$/.test(url) || /^slack:\/\/channel\?[\w=&%.-]+$/.test(url)) shell.openExternal(url);
  });
  if (process.platform === "darwin" && app.dock) {
    const dockImg = nativeImage.createFromPath(path.join(ASSETS, "icon.png"));
    if (!dockImg.isEmpty()) app.dock.setIcon(dockImg); // dev runs show the JARVIS icon in the Dock too
  }
  // Music visualizer: the HUD asks getDisplayMedia() for its OWN audio (the YouTube Music iframe lives in this
  // window). Answer with this window's frame as both sources and keep local playback audible (enableLocalEcho).
  // Tab capture of our own webContents needs no macOS screen-recording permission and never sees other apps.
  session.defaultSession.setDisplayMediaRequestHandler((request, cb) => {
    const frame = request.frame;
    const own = frame && BrowserWindow.getAllWindows().some((w) => w.webContents.mainFrame === frame);
    if (!own || !frame) return cb({});
    cb({ video: frame, audio: frame, enableLocalEcho: true });
  });
  // The HUD loads from file://, so YouTube's embedded player gets no Referer and refuses to play (error 153).
  // Give YouTube embed requests a stable app Referer (only those hosts; nothing else is touched).
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ["https://www.youtube-nocookie.com/*", "https://www.youtube.com/*"] },
    (details, cb) => {
      details.requestHeaders["Referer"] = "https://jarvis.local/";
      cb({ requestHeaders: details.requestHeaders });
    },
  );
  await startCore();
  createWindow();
  buildTray();
  globalShortcut.register("Alt+Space", () => (win?.isFocused() ? win.hide() : summon(false)));
  globalShortcut.register("Alt+Shift+Space", () => summon(true));
  globalShortcut.register("Alt+Shift+D", () => showcase());
  globalShortcut.register("Alt+Shift+S", () => { summon(false); win?.webContents.send("jarvis:sleep"); });
  app.on("activate", () => summon(false));
});

app.on("before-quit", () => {
  quitting = true;
  globalShortcut.unregisterAll();
  if (core && !core.killed) core.kill("SIGTERM");
});
