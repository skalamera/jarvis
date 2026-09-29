import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Hud } from "./hud/Hud";
import { core } from "./ws/core";
import { useStore } from "./state/store";

core.init();
// Test hook for the CDP e2e scripts (renderer-only; not reachable from web content or the network).
Object.assign(window, { __core: core, useStore });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Hud />
  </StrictMode>,
);
