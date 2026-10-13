import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { fmtDate } from "../../format.js";
import { ErrorNote } from "../common.jsx";

/* Company-wide employee settings (employees.manage): holiday days for new employees, public holidays (they don't count
   as days off), and the onboarding / offboarding checklist templates. */

function Steps({ label, hint, rows, who, onChange }) {
  const set = (i, k, v) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const move = (i, by) => {
    const next = [...rows];
    const [r] = next.splice(i, 1);
    next.splice(i + by, 0, r);
    onChange(next);
  };
  return (
    <fieldset className="team-steps-edit">
      <legend>{label}</legend>
      <p className="muted tiny">{hint}</p>
      {rows.map((r, i) => (
        <div key={i} className="team-step-row">
          <input value={r.title} maxLength={160} onChange={(e) => set(i, "title", e.target.value)} aria-label="Step" />
          <input type="number" min={-365} max={365} value={r.days} onChange={(e) => set(i, "days", Number(e.target.value))}
                 aria-label="Days from the day" title="Days before (−) or after the day" />
          <select value={r.who} onChange={(e) => set(i, "who", e.target.value)} aria-label="Responsible">
            {Object.entries(who).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <button type="button" className="icon-x" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
          <button type="button" className="icon-x" disabled={i === rows.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
          <button type="button" className="icon-x" onClick={() => onChange(rows.filter((_, j) => j !== i))} aria-label="Remove">×</button>
        </div>
      ))}
      <button type="button" className="btn ghost small" onClick={() => onChange([...rows, { title: "", days: 0, who: "starter" }])}>+ Add a step</button>
    </fieldset>
  );
}

export default function TeamSettings({ status, onClose, onChanged }) {
  const [s, setS] = useState(null);
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api("/team/settings").then(setS).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const set = (k, v) => setS((x) => ({ ...x, [k]: v }));
  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api("/team/settings", { method: "PUT", body: { settings: {
        ...s, holiday_days: Number(s.holiday_days),
        onboarding: s.onboarding.filter((r) => r.title.trim()), offboarding: s.offboarding.filter((r) => r.title.trim()) } } });
      onChanged?.(); onClose();
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  const who = status?.who || { starter: "whoever starts it", manager: "their manager", employee: "the employee" };
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor team-settings" role="dialog" aria-modal="true" aria-label="Holidays and checklists"
            onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head">
          <h2><span className="hud-code">HR</span>Holidays &amp; checklists</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <ErrorNote error={err} />
        {!s ? <p className="muted">Loading…</p> : (
          <>
            <div className="form-grid">
              <label className="field"><span>Holiday days a year (default for new employees)</span>
                <input type="number" min={0} max={60} step={0.5} value={s.holiday_days} onChange={(e) => set("holiday_days", e.target.value)} />
              </label>
              <div className="field wide"><span>Public holidays (not counted as days off)</span>
                <div className="pick-chips">
                  {s.public_holidays.map((d) => (
                    <button key={d} type="button" className="chip" title="Remove" onClick={() => set("public_holidays", s.public_holidays.filter((x) => x !== d))}>
                      {fmtDate(d)} ×</button>
                  ))}
                  {s.public_holidays.length === 0 && <span className="muted small">None yet.</span>}
                </div>
                <div className="team-step-add">
                  <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Public holiday" />
                  <button type="button" className="btn small" disabled={!day}
                          onClick={() => { set("public_holidays", [...new Set([...s.public_holidays, day])].sort()); setDay(""); }}>Add</button>
                </div>
              </div>
            </div>
            <Steps label="Onboarding" hint="Due dates count from the first day (−3 = three days before). Responsible: whoever starts it, their manager (if they have an Alfred account) or the employee."
                   rows={s.onboarding} who={who} onChange={(v) => set("onboarding", v)} />
            <Steps label="Offboarding" hint="Due dates count from the last working day."
                   rows={s.offboarding} who={who} onChange={(v) => set("offboarding", v)} />
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !s}>Save</button>
        </div>
      </form>
    </div>
  );
}
