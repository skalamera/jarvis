import { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, shell, systemPreferences, Tray } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";

const DEV_URL = process.env.JARVIS_DEV_URL; // e.g. http://localhost:5199 (npm run dev)
const CORE_PORT = Number(process.env.JARVIS_PORT || 8765);
const PROJECT_ROOT = process.env.JARVIS_ROOT || path.resolve(__dirname, "..", "..");
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
  ipcMain.on("jarvis:open", (_e, url: string) => {
    if (/^https?:\/\//.test(url) || /^tel:\+?[\d()\-. ]{3,20}$/.test(url)) shell.openExternal(url);
  });
  if (process.platform === "darwin" && app.dock) {
    const dockImg = nativeImage.createFromPath(path.join(ASSETS, "icon.png"));
    if (!dockImg.isEmpty()) app.dock.setIcon(dockImg); // dev runs show the JARVIS icon in the Dock too
  }
  await startCore();
  createWindow();
  buildTray();
  globalShortcut.register("Alt+Space", () => (win?.isFocused() ? win.hide() : summon(false)));
  globalShortcut.register("Alt+Shift+Space", () => summon(true));
  app.on("activate", () => summon(false));
});

app.on("before-quit", () => {
  quitting = true;
  globalShortcut.unregisterAll();
  if (core && !core.killed) core.kill("SIGTERM");
});
