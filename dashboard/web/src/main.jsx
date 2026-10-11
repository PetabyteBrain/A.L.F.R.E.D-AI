import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/orbitron/500.css";
import "@fontsource/orbitron/700.css";
import "@fontsource/orbitron/900.css";
import "@fontsource/rajdhani/500.css";
import "@fontsource/rajdhani/600.css";
import "@fontsource/rajdhani/700.css";
import "@fontsource/share-tech-mono/400.css";
import "@fontsource/bebas-neue/400.css";
import "@fontsource/cormorant-garamond/500.css";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/cormorant-garamond/600-italic.css";
import "@fontsource/eb-garamond/400.css";
import "@fontsource/eb-garamond/400-italic.css";
import "@fontsource/eb-garamond/500.css";
import { normalizeTheme } from "./theme.js";
import App from "./App.jsx";
import "./styles.css";

// Apply the saved theme before first paint so there's no colour flash.
try {
  document.documentElement.dataset.theme = normalizeTheme(localStorage.getItem("alfred-theme"));
} catch {
  document.documentElement.dataset.theme = "stark";
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
