// Render an SVG to transparent PNGs at several sizes with Chromium (Electron offscreen).
// usage: electron render_svg.cjs <in.svg> <outdir> <size> [<size> ...]
const { app, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

const [svgPath, outDir, ...sizes] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const svg = fs.readFileSync(svgPath);
  const dataUrl = "data:image/svg+xml;base64," + svg.toString("base64");
  for (const s of sizes.map(Number)) {
    const win = new BrowserWindow({
      show: false, width: s, height: s, useContentSize: true, transparent: true, frame: false,
      backgroundColor: "#00000000", webPreferences: { offscreen: true },
    });
    win.webContents.setZoomFactor(1);
    const html = `<!doctype html><html><head><style>html,body{margin:0;background:transparent;overflow:hidden}
      img{display:block;width:${s}px;height:${s}px}</style></head><body><img id="i" src="${dataUrl}"></body></html>`;
    await win.loadURL("data:text/html;base64," + Buffer.from(html).toString("base64"));
    await win.webContents.executeJavaScript("new Promise(r => { const i = document.getElementById('i'); i.complete ? r() : i.onload = r; })");
    await new Promise((r) => setTimeout(r, 400));
    const img = await win.webContents.capturePage({ x: 0, y: 0, width: s, height: s });
    const out = img.getSize().width === s ? img : img.resize({ width: s, height: s, quality: "best" });
    fs.writeFileSync(path.join(outDir, `logo_${s}.png`), out.toPNG());
    console.log("wrote", s, JSON.stringify(out.getSize()));
    win.destroy();
  }
  app.quit();
});
