import "@fontsource/orbitron/700.css";
import "@fontsource/orbitron/800.css";
import "@fontsource/orbitron/900.css";
import "@fontsource/rajdhani/500.css";
import "@fontsource/rajdhani/600.css";
import "@fontsource/rajdhani/700.css";
import "@fontsource/share-tech-mono/400.css";
import "./home.css";

document.documentElement.classList.add("js");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
if (headline && !reduceMotion) {
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

/* ---------- particle network background ---------- */
const canvas = document.getElementById("field");
if (canvas && canvas.getContext) {
  const ctx = canvas.getContext("2d");
  let w = 0, h = 0, dpr = 1, nodes = [], mouse = { x: -9999, y: -9999 };
  const LINK = 140;

  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.min(90, Math.round((w * h) / 16000));
    nodes = Array.from({ length: count }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
      r: Math.random() * 1.4 + 0.6,
    }));
  };

  const draw = () => {
    ctx.clearRect(0, 0, w, h);
    for (const n of nodes) {
      if (!reduceMotion) {
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
      }
    }
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < LINK) {
          ctx.strokeStyle = `rgba(88,214,255,${(1 - d / LINK) * 0.18})`;
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
      const md = Math.hypot(a.x - mouse.x, a.y - mouse.y);
      if (md < LINK * 1.3) {
        ctx.strokeStyle = `rgba(88,214,255,${(1 - md / (LINK * 1.3)) * 0.45})`;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke();
      }
      ctx.fillStyle = "rgba(160,235,255,0.75)";
      ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2); ctx.fill();
    }
  };

  let running = true;
  const loop = () => { if (running) draw(); if (!reduceMotion) requestAnimationFrame(loop); };
  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", (e) => { mouse = { x: e.clientX, y: e.clientY }; }, { passive: true });
  document.addEventListener("visibilitychange", () => { running = !document.hidden; });
  loop();
}
