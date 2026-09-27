import { useEffect, useState } from "react";

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
      <div className="g-value">{v}</div>
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
