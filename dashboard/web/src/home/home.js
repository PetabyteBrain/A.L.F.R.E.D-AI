import "@fontsource/orbitron/700.css";
import "@fontsource/orbitron/800.css";
import "@fontsource/orbitron/900.css";
import "@fontsource/rajdhani/500.css";
import "@fontsource/rajdhani/600.css";
import "@fontsource/rajdhani/700.css";
import "@fontsource/share-tech-mono/400.css";
import "@fontsource/bebas-neue/400.css";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/cormorant-garamond/600-italic.css";
import "@fontsource/eb-garamond/400.css";
import "@fontsource/eb-garamond/400-italic.css";
import { THEME_KEY, THEMES, normalizeTheme } from "../themes.js";
import { backdropFor } from "../backdrops.js";
import "./home.css";

document.documentElement.classList.add("js");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- skins: same choice as the dashboard (public/theme-boot.js applied it before paint) ---------- */
let theme = normalizeTheme(document.documentElement.dataset.theme);
const listeners = [];
function applyTheme(t) {
  theme = normalizeTheme(t);
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage blocked: this page only */ }
  const mark = document.getElementById("mark");
  if (mark) mark.textContent = THEMES[theme].wordmark;
  document.querySelectorAll("[data-theme-switch] button").forEach((b) => {
    b.classList.toggle("active", b.dataset.theme === theme);
    b.setAttribute("aria-pressed", String(b.dataset.theme === theme));
  });
  listeners.forEach((fn) => fn(theme));
}
for (const switcher of document.querySelectorAll("[data-theme-switch]")) {
  for (const [id, cfg] of Object.entries(THEMES)) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.theme = id;
    b.textContent = cfg.label;
    b.addEventListener("click", () => applyTheme(id));
    switcher.appendChild(b);
  }
}
applyTheme(theme);

/* ---------- signed in? swap "Sign in" for "Open Alfred" ---------- */
fetch("/api/session", { credentials: "same-origin", cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((s) => {
    if (!s || !s.authenticated) return;
    document.querySelectorAll('[data-auth="guest"]').forEach((el) => { el.hidden = true; });
    document.querySelectorAll('[data-auth="user"]').forEach((el) => { el.hidden = false; });
    const welcome = document.getElementById("welcome");
    if (welcome && s.first_name) welcome.textContent = ` · welcome back, ${s.first_name}`;
  })
  .catch(() => { /* offline: keep the sign-in buttons */ });

/* ---------- clock ---------- */
const clock = document.getElementById("clock");
const tick = () => { if (clock) clock.textContent = new Date().toLocaleTimeString([], { hour12: false }); };
tick();
setInterval(tick, 1000);

/* ---------- headline decode effect ---------- */
const headline = document.getElementById("headline");
if (headline && !reduceMotion && theme !== "alfred") {   // the butler doesn't scramble
  const text = headline.dataset.text || headline.textContent;
  const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&*<>/\\";
  headline.setAttribute("aria-label", text);
  const start = performance.now();
  const duration = 1100;
  const step = (now) => {
    const p = Math.min(1, (now - start) / duration);
    const settled = Math.floor(p * text.length);
    let out = "";
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      out += i < settled || ch === " " ? ch : glyphs[(Math.random() * glyphs.length) | 0];
    }
    headline.textContent = out;
    if (p < 1) requestAnimationFrame(step);
    else headline.textContent = text;
  };
  requestAnimationFrame(step);
}

/* ---------- reveal on scroll ---------- */
const reveals = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window && !reduceMotion) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }
  }, { threshold: 0.15 });
  reveals.forEach((el) => io.observe(el));
} else {
  reveals.forEach((el) => el.classList.add("in"));
}

/* ---------- reactor parallax ---------- */
const visual = document.getElementById("visual");
if (visual && !reduceMotion && window.matchMedia("(pointer: fine)").matches) {
  window.addEventListener("pointermove", (e) => {
    const x = (e.clientX / window.innerWidth - 0.5) * 2;
    const y = (e.clientY / window.innerHeight - 0.5) * 2;
    visual.style.transform = `translate3d(${x * -14}px, ${y * -14}px, 0) rotateX(${y * 4}deg) rotateY(${x * -4}deg)`;
  }, { passive: true });
}

/* ---------- animated background for the current skin (src/backdrops.js) ---------- */
const canvas = document.getElementById("field");
if (canvas && canvas.getContext) {
  const ctx = canvas.getContext("2d");
  const size = { w: 0, h: 0 };
  const pointer = { x: -9999, y: -9999 };
  let draw = () => {};
  const setup = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    size.w = window.innerWidth; size.h = window.innerHeight;
    canvas.width = size.w * dpr; canvas.height = size.h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw = backdropFor(theme, ctx, size, pointer);
    draw(false);
  };
  setup();
  listeners.push(setup);
  window.addEventListener("resize", setup);
  window.addEventListener("pointermove", (e) => { pointer.x = e.clientX; pointer.y = e.clientY; }, { passive: true });
  let last = 0;
  const loop = (t) => {
    requestAnimationFrame(loop);
    if (document.hidden || t - last < 33) return;        // ~30 fps is plenty for ambience
    last = t;
    draw(true);
  };
  if (!reduceMotion) requestAnimationFrame(loop);
}
