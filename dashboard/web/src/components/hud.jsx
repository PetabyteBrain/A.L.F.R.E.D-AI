import { useEffect, useRef, useState } from "react";
import { useThemeName } from "../theme.js";

// Generic bat silhouette (symmetric, 200x100 box, centred on x=100).
const BAT = "M100 40 L94 24 L90 40 Q60 30 12 46 Q34 54 33 72 Q46 62 58 71 Q70 60 82 75 Q92 70 100 88 "
  + "Q108 70 118 75 Q130 60 142 71 Q154 62 167 72 Q166 54 188 46 Q140 30 110 40 L106 24 Z";

/* Bat-signal: a searchlight disc with the bat cut out, sweeping rings, cloud haze. */
export function BatSignal({ size = "md", label }) {
  const ticks = Array.from({ length: 48 }, (_, i) => {
    const a = (i * 7.5) * (Math.PI / 180);
    const r0 = i % 4 === 0 ? 82 : 86;
    return `M ${100 + r0 * Math.cos(a)} ${100 + r0 * Math.sin(a)} L ${100 + 90 * Math.cos(a)} ${100 + 90 * Math.sin(a)}`;
  });
  return (
    <div className={`reactor emblem-bat reactor-${size}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <svg viewBox="0 0 200 200">
        <circle className="r-halo" cx="100" cy="100" r="78" />
        <g className="spin spin-slow"><circle className="ring ring-dash-a" cx="100" cy="100" r="96" /></g>
        <g className="spin spin-rev"><path className="ring ring-ticks" d={ticks.join(" ")} /></g>
        <circle className="bat-disc" cx="100" cy="100" r="62" />
        <circle className="bat-rim" cx="100" cy="100" r="62" />
        <path className="bat" d={BAT} transform="translate(100 102) scale(0.56) translate(-100 -56)" />
      </svg>
    </div>
  );
}

// Half-width katakana, digits and a few symbols, as in the digital rain.
export const RAIN_GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789:=*+<>¦";
const RING_TEXT = Array.from({ length: 34 }, (_, i) => RAIN_GLYPHS[(i * 7) % RAIN_GLYPHS.length]).join(" ");

/* Matrix: a ring of falling-code glyphs around a terminal cursor. */
export function CodeRing({ size = "md", label }) {
  const ticks = Array.from({ length: 36 }, (_, i) => {
    const a = (i * 10) * (Math.PI / 180);
    const r0 = i % 3 === 0 ? 50 : 53;
    return `M ${100 + r0 * Math.cos(a)} ${100 + r0 * Math.sin(a)} L ${100 + 56 * Math.cos(a)} ${100 + 56 * Math.sin(a)}`;
  });
  return (
    <div className={`reactor emblem-code reactor-${size}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <svg viewBox="0 0 200 200">
        <defs><path id="code-ring" d="M 100 100 m -76 0 a 76 76 0 1 1 152 0 a 76 76 0 1 1 -152 0" /></defs>
        <circle className="r-halo" cx="100" cy="100" r="62" />
        <g className="spin spin-slow"><circle className="ring ring-dash-a" cx="100" cy="100" r="96" /></g>
        <g className="spin spin-rev"><text className="code-text"><textPath href="#code-ring">{RING_TEXT}</textPath></text></g>
        <g className="spin spin-med"><path className="ring ring-ticks" d={ticks.join(" ")} /></g>
        <rect className="code-core" x="64" y="64" width="72" height="72" />
        <text className="code-a" x="100" y="114" textAnchor="middle">A</text>
        <rect className="code-cursor" x="88" y="120" width="24" height="5" />
      </svg>
    </div>
  );
}

/* The theme's emblem: arc reactor on the Stark HUD, bat-signal in the Batcave, code ring in the Matrix. */
export function Emblem(props) {
  const theme = useThemeName();
  if (theme === "batcave") return <BatSignal {...props} />;
  if (theme === "matrix") return <CodeRing {...props} />;
  return <ArcReactor {...props} />;
}

/* Arc reactor: layered rotating rings around a glowing core (pure SVG + CSS animation). */
export function ArcReactor({ size = "md", label }) {
  const segments = Array.from({ length: 10 }, (_, i) => {
    const a0 = (i * 36 + 4) * (Math.PI / 180);
    const a1 = (i * 36 + 32) * (Math.PI / 180);
    const p = (r, a) => `${100 + r * Math.cos(a)} ${100 + r * Math.sin(a)}`;
    return `M ${p(40, a0)} L ${p(56, a0)} A 56 56 0 0 1 ${p(56, a1)} L ${p(40, a1)} A 40 40 0 0 0 ${p(40, a0)} Z`;
  });
  const ticks = Array.from({ length: 60 }, (_, i) => {
    const a = (i * 6) * (Math.PI / 180);
    const r0 = i % 5 === 0 ? 80 : 84;
    return `M ${100 + r0 * Math.cos(a)} ${100 + r0 * Math.sin(a)} L ${100 + 88 * Math.cos(a)} ${100 + 88 * Math.sin(a)}`;
  });
  return (
    <div className={`reactor reactor-${size}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <svg viewBox="0 0 200 200">
        <circle className="r-halo" cx="100" cy="100" r="70" />
        <g className="spin spin-slow"><circle className="ring ring-dash-a" cx="100" cy="100" r="96" /></g>
        <g className="spin spin-rev"><path className="ring ring-ticks" d={ticks.join(" ")} /></g>
        <g className="spin spin-med"><circle className="ring ring-dash-b" cx="100" cy="100" r="70" /></g>
        <g className="spin spin-rev-slow">{segments.map((d, i) => <path key={i} className="seg" d={d} />)}</g>
        <circle className="ring ring-inner" cx="100" cy="100" r="34" />
        <circle className="core" cx="100" cy="100" r="24" />
        <circle className="core-hot" cx="100" cy="100" r="11" />
      </svg>
    </div>
  );
}

/* Circular gauge for a count. The ring fills relative to `max` (auto-scales). */
export function Gauge({ value, label, max, tone = "" }) {
  const v = Number(value) || 0;
  const shown = useCountUp(v);
  const scale = max || Math.max(5, Math.ceil(v / 5) * 5);
  const C = 2 * Math.PI * 40;
  const frac = Math.min(1, v / scale);
  return (
    <div className={`gauge ${tone}`}>
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle className="g-track" cx="50" cy="50" r="40" />
        <circle className="g-ticks" cx="50" cy="50" r="46" />
        <circle className="g-fill" cx="50" cy="50" r="40" strokeDasharray={`${C * frac} ${C}`} transform="rotate(-90 50 50)" />
      </svg>
      <div className="g-value">{shown}</div>
      <div className="g-label">{label}</div>
    </div>
  );
}

/* Voice activity bars: animate while `active`. */
export function Waveform({ active }) {
  return (
    <div className={`waveform${active ? " on" : ""}`} aria-hidden="true">
      {Array.from({ length: 16 }, (_, i) => <span key={i} className={`b b${i % 8}`} />)}
    </div>
  );
}

export function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="clock">
      <div className="clock-time">{now.toLocaleTimeString([], { hour12: false })}</div>
      <div className="clock-date">{now.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).toUpperCase()}</div>
    </div>
  );
}

/* Titled HUD frame with corner brackets. */
export function Panel({ title, code, actions, className = "", children }) {
  return (
    <section className={`hud ${className}`}>
      {(title || actions) && (
        <header className="hud-head">
          <h2>{code && <span className="hud-code">{code}</span>}{title}</h2>
          {actions && <div className="hud-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function ConfirmDialog({ title, body, confirmLabel, busy, error, onConfirm, onCancel }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onCancel()}>
      <div className="hud modal alert" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title"
           onClick={(e) => e.stopPropagation()}>
        <h2 id="dlg-title"><span className="hud-code">!!</span>{title}</h2>
        <div className="modal-body">{body}</div>
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onCancel} disabled={busy} autoFocus>Abort</button>
          <button className="btn danger" onClick={onConfirm} disabled={busy}>{busy ? "Executing…" : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&*<>/";
const stillMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Text that "decodes" from random glyphs whenever it changes (page titles, wordmarks). */
export function DecodeText({ text, as: Tag = "span", className = "", duration = 700 }) {
  const [shown, setShown] = useState(text);
  useEffect(() => {
    if (stillMotion()) { setShown(text); return undefined; }
    let raf = 0;
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const settled = Math.floor(p * text.length);
      let out = "";
      for (let i = 0; i < text.length; i++) {
        out += i < settled || text[i] === " " || text[i] === "." ? text[i] : GLYPHS[(Math.random() * GLYPHS.length) | 0];
      }
      setShown(out);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [text, duration]);
  return <Tag className={className} aria-label={text}><span aria-hidden="true">{shown}</span></Tag>;
}

/* Animate a number from its previous value to the new one. */
export function useCountUp(value, duration = 900) {
  const [v, setV] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const target = Number(value) || 0;
    if (stillMotion()) { setV(target); from.current = target; return undefined; }
    let raf = 0;
    const start = performance.now(), a = from.current;
    const step = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setV(Math.round(a + (target - a) * eased));
      if (p < 1) raf = requestAnimationFrame(step);
      else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return v;
}
