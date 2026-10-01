import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("jarvis", {
  config: (): Promise<{ coreUrl: string; httpUrl: string; token: string }> => ipcRenderer.invoke("jarvis:config"),
  onListen: (cb: () => void) => {
    const h = () => cb();
    ipcRenderer.on("jarvis:listen", h);
    return () => ipcRenderer.removeListener("jarvis:listen", h);
  },
  open: (url: string) => ipcRenderer.send("jarvis:open", url),
  /** Absolute path of a dropped file / folder (lets a dropped project folder be mapped in place). */
  pathForFile: (f: File): string => webUtils.getPathForFile(f),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("jarvis:pickFolder"),
});
