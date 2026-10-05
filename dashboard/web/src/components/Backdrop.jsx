import { useEffect, useRef, useState } from "react";
import { useThemeName } from "../theme.js";
import { RAIN_GLYPHS as GLYPHS } from "./hud.jsx";

const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Stark HUD: drifting particle network that reacts to the pointer. */
function particles(ctx, size, pointer) {
  const count = Math.min(70, Math.round((size.w * size.h) / 20000));
  const nodes = Array.from({ length: count }, () => ({
    x: Math.random() * size.w, y: Math.random() * size.h,
    vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: Math.random() * 1.3 + 0.5,
  }));
  const LINK = 130;
  return (move) => {
    ctx.clearRect(0, 0, size.w, size.h);
    for (const n of nodes) {
      if (move) {
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0 || n.x > size.w) n.vx *= -1;
        if (n.y < 0 || n.y > size.h) n.vy *= -1;
      }
    }
    ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < LINK) {
          ctx.strokeStyle = `rgba(88,214,255,${(1 - d / LINK) * 0.14})`;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
      const md = Math.hypot(a.x - pointer.x, a.y - pointer.y);
      if (md < 170) {
        ctx.strokeStyle = `rgba(88,214,255,${(1 - md / 170) * 0.35})`;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke();
      }
      ctx.fillStyle = "rgba(160,235,255,0.7)";
      ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2); ctx.fill();
    }
  };
}

/* Batcave: rain slanting through the dark, with slow fog banks. */
function rain(ctx, size) {
  const drops = Array.from({ length: Math.min(180, Math.round(size.w / 8)) }, () => ({
    x: Math.random() * size.w, y: Math.random() * size.h, len: 10 + Math.random() * 18,
    speed: 7 + Math.random() * 7, a: 0.08 + Math.random() * 0.18,
  }));
  const fog = Array.from({ length: 5 }, (_, i) => ({
    x: Math.random() * size.w, y: size.h * (0.35 + i * 0.13), r: 260 + Math.random() * 260, vx: 0.12 + Math.random() * 0.2,
  }));
  return (move) => {
    ctx.clearRect(0, 0, size.w, size.h);
    for (const f of fog) {
      if (move) { f.x += f.vx; if (f.x - f.r > size.w) f.x = -f.r; }
      const g = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, f.r);
      g.addColorStop(0, "rgba(120,125,140,0.07)");
      g.addColorStop(1, "rgba(120,125,140,0)");
      ctx.fillStyle = g;
      ctx.fillRect(f.x - f.r, f.y - f.r, f.r * 2, f.r * 2);
    }
    ctx.lineWidth = 1;
    for (const d of drops) {
      if (move) {
        d.y += d.speed; d.x += d.speed * 0.18;
        if (d.y > size.h) { d.y = -d.len; d.x = Math.random() * size.w; }
      }
      ctx.strokeStyle = `rgba(190,195,210,${d.a})`;
      ctx.beginPath(); ctx.moveTo(d.x, d.y); ctx.lineTo(d.x + d.len * 0.18, d.y + d.len); ctx.stroke();
    }
  };
}

/* Matrix: columns of glyphs falling, a bright head and a fading green trail. */
function digitalRain(ctx, size) {
  const FS = 16;
  const cols = Math.ceil(size.w / FS);
  const drops = Array.from({ length: cols }, () => ({ y: Math.random() * -60, speed: 0.35 + Math.random() * 0.65 }));
  const glyph = () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, size.w, size.h);
  ctx.font = `${FS}px "Share Tech Mono", monospace`;
  ctx.textBaseline = "top";
  return (move) => {
    if (!move) {                                    // reduced motion: a still scatter of code
      for (let i = 0; i < cols; i++) {
        for (let r = 0; r < 6; r++) {
          ctx.fillStyle = `rgba(0,255,65,${0.05 + Math.random() * 0.12})`;
          ctx.fillText(glyph(), i * FS, Math.random() * size.h);
        }
      }
      return;
    }
    ctx.fillStyle = "rgba(0,0,0,0.09)";            // fade the trails
    ctx.fillRect(0, 0, size.w, size.h);
    for (let i = 0; i < cols; i++) {
      const d = drops[i];
      const y = Math.floor(d.y) * FS;
      if (d.y >= 0) {
        ctx.fillStyle = "rgba(0,255,65,0.55)";
        ctx.fillText(glyph(), i * FS, y - FS);
        ctx.fillStyle = "rgba(210,255,220,0.85)";     // the bright head
        ctx.fillText(glyph(), i * FS, y);
      }
      d.y += d.speed;
      if (y > size.h && Math.random() > 0.975) { d.y = Math.random() * -20; d.speed = 0.35 + Math.random() * 0.65; }
    }
  };
}

export default function Backdrop() {
  const theme = useThemeName();
  const canvasRef = useRef(null);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    const size = { w: 0, h: 0 };
    const pointer = { x: -9999, y: -9999 };
    const still = reduceMotion();
    let draw = () => {};
    const setup = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      size.w = window.innerWidth; size.h = window.innerHeight;
      canvas.width = size.w * dpr; canvas.height = size.h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw = theme === "batcave" ? rain(ctx, size) : theme === "matrix" ? digitalRain(ctx, size) : particles(ctx, size, pointer);
      draw(false);
    };
    setup();
    let raf = 0, last = 0;
    const loop = (t) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || t - last < 33) return; // ~30 fps is plenty for ambience
      last = t;
      draw(true);
    };
    if (!still) raf = requestAnimationFrame(loop);
    const onMove = (e) => { pointer.x = e.clientX; pointer.y = e.clientY; };
    window.addEventListener("resize", setup);
    window.addEventListener("pointermove", onMove, { passive: true });

    // Occasional lightning in the Batcave.
    let timer = 0;
    if (theme === "batcave" && !still) {
      const strike = () => {
        setFlash(true);
        setTimeout(() => setFlash(false), 900);
        timer = setTimeout(strike, 14000 + Math.random() * 20000);
      };
      timer = setTimeout(strike, 6000 + Math.random() * 8000);
    }
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      window.removeEventListener("resize", setup);
      window.removeEventListener("pointermove", onMove);
    };
  }, [theme]);

  return (
    <div className="backdrop" aria-hidden="true">
      <canvas ref={canvasRef} />
      <div className="floor" />
      <div className={`flash${flash ? " on" : ""}`} />
    </div>
  );
}
