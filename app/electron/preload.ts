import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("jarvis", {
  config: (): Promise<{ coreUrl: string; httpUrl: string; token: string }> => ipcRenderer.invoke("jarvis:config"),
  onListen: (cb: () => void) => {
    const h = () => cb();
    ipcRenderer.on("jarvis:listen", h);
    return () => ipcRenderer.removeListener("jarvis:listen", h);
  },
  open: (url: string) => ipcRenderer.send("jarvis:open", url),
  onSleep: (cb: () => void) => {
    const h = () => cb();
    ipcRenderer.on("jarvis:sleep", h);
    return () => ipcRenderer.removeListener("jarvis:sleep", h);
  },
  onShowcase: (cb: () => void) => {
    const h = () => cb();
    ipcRenderer.on("jarvis:showcase", h);
    return () => ipcRenderer.removeListener("jarvis:showcase", h);
  },
  /** Absolute path of a dropped file / folder (lets a dropped project folder be mapped in place). */
  pathForFile: (f: File): string => webUtils.getPathForFile(f),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("jarvis:pickFolder"),
  /** Opens the Google sign-in window; resolves with the YouTube Music session cookie string (or null if closed). */
  ytmLogin: (): Promise<string | null> => ipcRenderer.invoke("jarvis:ytmLogin"),
  ytmLogout: (): Promise<boolean> => ipcRenderer.invoke("jarvis:ytmLogout"),
});
