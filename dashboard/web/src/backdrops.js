/* Animated backgrounds per skin, shared by the dashboard (components/Backdrop.jsx) and the public
   pages (home/home.js). Each factory takes a 2D context and a live {w, h} size and returns
   draw(move): move = false draws one still frame (used for reduced motion). */

// Half-width katakana, digits and a few symbols, as in the digital rain.
export const RAIN_GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789:=*+<>¦";

/* Stark HUD: drifting particle network that reacts to the pointer. */
export function particles(ctx, size, pointer, { density = 20000, max = 70, link = 130, alpha = 0.14 } = {}) {
  const count = Math.min(max, Math.round((size.w * size.h) / density));
  const nodes = Array.from({ length: count }, () => ({
    x: Math.random() * size.w, y: Math.random() * size.h,
    vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: Math.random() * 1.3 + 0.5,
  }));
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
        if (d < link) {
          ctx.strokeStyle = `rgba(88,214,255,${(1 - d / link) * alpha})`;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
      const md = Math.hypot(a.x - pointer.x, a.y - pointer.y);
      if (md < link * 1.3) {
        ctx.strokeStyle = `rgba(88,214,255,${(1 - md / (link * 1.3)) * 0.35})`;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke();
      }
      ctx.fillStyle = "rgba(160,235,255,0.7)";
      ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2); ctx.fill();
    }
  };
}

/* Batcave: rain slanting through the dark, with slow fog banks. */
export function rain(ctx, size) {
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
export function digitalRain(ctx, size) {
  const FS = 16;
  const cols = Math.ceil(size.w / FS);
  const drops = Array.from({ length: cols }, () => ({ y: Math.random() * -60, speed: 0.35 + Math.random() * 0.65 }));
  const glyph = () => RAIN_GLYPHS[Math.floor(Math.random() * RAIN_GLYPHS.length)];
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

/* Alfred: candlelight. Two warm pools of light that gently flicker, and dust motes drifting up
   through them. */
export function candlelight(ctx, size) {
  const motes = Array.from({ length: Math.min(70, Math.round(size.w / 18)) }, () => ({
    x: Math.random() * size.w, y: Math.random() * size.h, r: 0.6 + Math.random() * 1.6,
    vy: 0.08 + Math.random() * 0.22, sway: Math.random() * Math.PI * 2, a: 0.15 + Math.random() * 0.4,
  }));
  const candles = [{ x: 0.12, y: 0.92 }, { x: 0.9, y: 0.12 }];
  let t = 0;
  return (move) => {
    if (move) t += 1;
    ctx.clearRect(0, 0, size.w, size.h);
    for (const [i, c] of candles.entries()) {
      const flicker = 1 + Math.sin(t * 0.05 + i * 2) * 0.04 + Math.sin(t * 0.13 + i) * 0.03;
      const r = Math.max(size.w, size.h) * 0.55 * flicker;
      const x = c.x * size.w, y = c.y * size.h;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, "rgba(232,176,96,0.13)");
      g.addColorStop(0.45, "rgba(196,140,70,0.05)");
      g.addColorStop(1, "rgba(196,140,70,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, size.w, size.h);
    }
    for (const m of motes) {
      if (move) {
        m.y -= m.vy; m.sway += 0.01; m.x += Math.sin(m.sway) * 0.15;
        if (m.y < -4) { m.y = size.h + 4; m.x = Math.random() * size.w; }
      }
      const twinkle = 0.65 + Math.sin(m.sway * 3) * 0.35;
      ctx.fillStyle = `rgba(240,205,140,${m.a * twinkle})`;
      ctx.beginPath(); ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2); ctx.fill();
    }
  };
}

export function backdropFor(theme, ctx, size, pointer) {
  if (theme === "batcave") return rain(ctx, size);
  if (theme === "matrix") return digitalRain(ctx, size);
  if (theme === "alfred") return candlelight(ctx, size);
  return particles(ctx, size, pointer);
}
