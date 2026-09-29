import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Hud } from "./hud/Hud";
import { core } from "./ws/core";

core.init();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Hud />
  </StrictMode>,
);
