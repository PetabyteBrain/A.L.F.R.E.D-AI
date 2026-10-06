import { useEffect, useRef, useState } from "react";
import { useThemeName } from "../theme.js";
import { RAIN_GLYPHS } from "../backdrops.js";

// Bat symbol (classic shape: pointed ears, wide wings, three scallops a side, pointed tail),
// symmetric in a 200x100 box, centred on x=100. Shared with the homepage (home.html) and favicons.
export const BAT = "M100 27 L103 27 L106 12 L109.5 29 Q117 31 124 26 Q152 19 194 44 Q177 46 169 63 Q158 51 145 66 "
  + "Q134 56 122 73 Q110 70 100 90 Q90 70 78 73 Q66 56 55 66 Q42 51 31 63 Q23 46 6 44 Q48 19 76 26 Q83 31 90.5 29 "
  + "L94 12 L97 27 Z";

/* Bat-signal. Large (sign-in, loading): a searchlight beam rising to a lit oval on the clouds, the bat
   as a soft shadow in it and cloud banks drifting across. Small: the disc with the bat, crisp. */
export function BatSignal({ size = "md", label }) {
  const big = size === "xl";
  const a11y = { role: label ? "img" : undefined, "aria-label": label, "aria-hidden": label ? undefined : true };
  if (!big) {
    return (
      <div className={`reactor emblem-bat reactor-${size}`} {...a11y}>
        <svg viewBox="0 0 200 200">
          <circle className="bat-glow" cx="100" cy="100" r="86" />
          <ellipse className="bat-disc" cx="100" cy="100" rx="78" ry="70" />
          <ellipse className="bat-rim" cx="100" cy="100" rx="78" ry="70" />
          <path className="bat" d={BAT} transform="translate(100 104) scale(0.74) translate(-100 -56)" />
        </svg>
      </div>
    );
  }
  return (
    <div className={`reactor emblem-bat emblem-bat-scene reactor-${size}`} {...a11y}>
      <svg viewBox="0 0 200 200">
        <defs>
          <radialGradient id="sig-light" cx=".5" cy=".45" r=".6">
            <stop offset="0" stopColor="#fff6c8" /><stop offset=".55" stopColor="#ffd84a" /><stop offset="1" stopColor="#e6a800" />
          </radialGradient>
          <linearGradient id="sig-beam" x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor="#ffe27a" stopOpacity=".55" /><stop offset="1" stopColor="#ffe27a" stopOpacity="0" />
          </linearGradient>
          <filter id="sig-shadow" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="1.1" /></filter>
          <filter id="sig-cloud" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7" /></filter>
          <clipPath id="sig-clip"><ellipse cx="100" cy="86" rx="70" ry="54" /></clipPath>
        </defs>
        <path className="sig-beam" d="M93 214 L107 214 L168 96 L32 96 Z" fill="url(#sig-beam)" />
        <ellipse className="sig-halo" cx="100" cy="86" rx="92" ry="74" />
        <ellipse className="sig-disc" cx="100" cy="86" rx="70" ry="54" fill="url(#sig-light)" />
        <g clipPath="url(#sig-clip)">
          <path className="sig-bat" d={BAT} filter="url(#sig-shadow)"
                transform="translate(100 90) scale(0.62 0.56) skewX(-3) translate(-100 -56)" />
          <g className="sig-clouds" filter="url(#sig-cloud)">
            <ellipse cx="40" cy="60" rx="46" ry="12" /><ellipse cx="150" cy="118" rx="54" ry="13" /><ellipse cx="96" cy="40" rx="30" ry="8" />
          </g>
        </g>
        <g className="sig-clouds outer" filter="url(#sig-cloud)">
          <ellipse cx="20" cy="30" rx="50" ry="14" /><ellipse cx="185" cy="70" rx="40" ry="12" /><ellipse cx="60" cy="150" rx="60" ry="14" />
        </g>
      </svg>
    </div>
  );
}

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

// A bow tie centred on (100,100), about 76 wide: two wings with a fold line each, and the knot.
export const BOW_TIE = "M100 100 C92 91 80 85 66 87 C61 95 61 105 66 113 C80 115 92 109 100 100 Z "
  + "M100 100 C108 91 120 85 134 87 C139 95 139 105 134 113 C120 115 108 109 100 100 Z";

/* Alfred: a discreet mark, a bow tie in a fine silver ring (think of a pressed white tie). */
export function ButlerMark({ size = "md", label }) {
  return (
    <div className={`reactor emblem-butler reactor-${size}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <svg viewBox="0 0 200 200">
        <circle className="bm-halo" cx="100" cy="100" r="82" />
        <circle className="bm-ring" cx="100" cy="100" r="84" />
        <circle className="bm-ring thin" cx="100" cy="100" r="77" />
        <g className="spin spin-slow bm-sheen-wrap"><circle className="bm-sheen" cx="100" cy="100" r="84" /></g>
        <path className="bm-ticks" d="M100 18 V24 M182 100 H176 M100 182 V176 M18 100 H24" />
        <path className="bm-tie" d={BOW_TIE} />
        <path className="bm-fold" d="M80 93 Q86 100 80 107 M120 93 Q114 100 120 107" />
        <rect className="bm-knot" x="93.5" y="92.5" width="13" height="15" rx="3.5" />
      </svg>
    </div>
  );
}

/* The theme's emblem: arc reactor (Stark), bat-signal (Batcave), code ring (Matrix), bow tie (Alfred). */
export function Emblem(props) {
  const theme = useThemeName();
  if (theme === "batcave") return <BatSignal {...props} />;
  if (theme === "matrix") return <CodeRing {...props} />;
  if (theme === "alfred") return <ButlerMark {...props} />;
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
  const calm = useThemeName() === "alfred";           // the butler doesn't scramble: a quiet fade instead
  useEffect(() => {
    if (stillMotion() || calm) { setShown(text); return undefined; }
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
  }, [text, duration, calm]);
  return (
    <Tag className={className} aria-label={text}>
      <span aria-hidden="true" key={calm ? text : undefined} className={calm ? "fade-in" : undefined}>{shown}</span>
    </Tag>
  );
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
