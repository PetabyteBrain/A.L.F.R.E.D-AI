import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/orbitron/500.css";
import "@fontsource/orbitron/700.css";
import "@fontsource/orbitron/900.css";
import "@fontsource/rajdhani/500.css";
import "@fontsource/rajdhani/600.css";
import "@fontsource/rajdhani/700.css";
import "@fontsource/share-tech-mono/400.css";
import App from "./App.jsx";
import "./styles.css";

// Apply the saved theme before first paint so there's no colour flash.
try {
  document.documentElement.dataset.theme = localStorage.getItem("alfred-theme") || "alfred";
} catch {
  document.documentElement.dataset.theme = "alfred";
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
