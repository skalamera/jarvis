import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("jarvis", {
  config: (): Promise<{ coreUrl: string; httpUrl: string; token: string }> => ipcRenderer.invoke("jarvis:config"),
  onListen: (cb: () => void) => {
    const h = () => cb();
    ipcRenderer.on("jarvis:listen", h);
    return () => ipcRenderer.removeListener("jarvis:listen", h);
  },
  open: (url: string) => ipcRenderer.send("jarvis:open", url),
});
