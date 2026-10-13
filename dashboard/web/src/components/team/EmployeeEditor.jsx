import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Add an employee or change their details. Nothing changes right away: the form sends a request that someone with
   approval rights decides (Team → Approvals, or Telegram). New employees can start with the onboarding checklist;
   setting a last day can start offboarding. */

const EMPTY = { name: "", job_title: "", department: "", manager_id: "", email: "", phone: "", employment: "full_time",
                workload: 100, start_date: "", end_date: "", holiday_days: "", user_id: "", timetrack_user: "", notes: "" };

export default function EmployeeEditor({ employee, status, onSaved, onClose }) {
  const isNew = !employee?.id;
  const year = String(new Date().getFullYear());
  const [form, setForm] = useState(() => {
    const f = { ...EMPTY };
    if (!isNew) for (const k of Object.keys(f)) f[k] = employee[k] ?? "";
    return f;
  });
  const [carry, setCarry] = useState(() => (employee?.holiday_adjust?.[year] ?? ""));
  const [onboarding, setOnboarding] = useState(isNew);
  const [offboarding, setOffboarding] = useState(!!employee?.offboard);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    const fields = { ...form };
    for (const k of ["manager_id", "user_id"]) fields[k] = fields[k] ? Number(fields[k]) : null;
    for (const k of ["start_date", "end_date"]) fields[k] = fields[k] || null;
    fields.workload = Number(fields.workload) || 100;
    if (fields.holiday_days === "" || fields.holiday_days === null) delete fields.holiday_days;
    else fields.holiday_days = Number(fields.holiday_days);
    const adjust = { ...(employee?.holiday_adjust || {}) };
    if (carry === "" || Number(carry) === 0) delete adjust[year]; else adjust[year] = Number(carry);
    fields.holiday_adjust = adjust;
    if (!isNew) {
      for (const k of Object.keys(fields)) {
        const before = k === "holiday_adjust" ? JSON.stringify(employee.holiday_adjust || {}) : String(employee[k] ?? "");
        const after = k === "holiday_adjust" ? JSON.stringify(fields[k]) : String(fields[k] ?? "");
        if (before === after) delete fields[k];
      }
    } else if (!Object.keys(adjust).length) delete fields.holiday_adjust;
    try {
      onSaved(await api(isNew ? "/team/employees" : `/team/employees/${employee.id}`,
        { method: isNew ? "POST" : "PATCH", body: { fields, onboarding: isNew && onboarding, offboarding: !isNew && offboarding, reason } }));
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  const managers = (status?.managers || []).filter((m) => m.id !== employee?.id);
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor crm-editor" role="dialog" aria-modal="true" aria-label={isNew ? "New employee" : `Edit ${employee.name}`}
            onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head">
          <h2><span className="hud-code">EM</span>{isNew ? "New employee" : `Edit ${employee.name}`}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <p className="muted small">Changes to employee data are sent for approval and apply once someone with approval rights agrees
          (you can approve your own).</p>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field wide"><span>Name</span>
            <input value={form.name} maxLength={120} required autoFocus onChange={(e) => set("name", e.target.value)} placeholder="e.g. Erika Mustermann" />
          </label>
          <label className="field"><span>Job title</span>
            <input value={form.job_title} maxLength={80} onChange={(e) => set("job_title", e.target.value)} placeholder="e.g. Designer" />
          </label>
          <label className="field"><span>Department</span>
            <input value={form.department} maxLength={80} onChange={(e) => set("department", e.target.value)} placeholder="e.g. Studio" />
          </label>
          <label className="field"><span>Manager</span>
            <select value={form.manager_id || ""} onChange={(e) => set("manager_id", e.target.value)}>
              <option value="">—</option>
              {managers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Alfred account</span>
            <select value={form.user_id || ""} onChange={(e) => set("user_id", e.target.value)}>
              <option value="">None</option>
              {(status?.people || []).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </select>
          </label>
          <label className="field"><span>Email (work)</span>
            <input type="email" value={form.email} maxLength={254} onChange={(e) => set("email", e.target.value)} />
          </label>
          <label className="field"><span>Phone (work)</span>
            <input value={form.phone} maxLength={40} onChange={(e) => set("phone", e.target.value)} />
          </label>
          <label className="field"><span>Employment</span>
            <select value={form.employment} onChange={(e) => set("employment", e.target.value)}>
              {Object.entries(status?.employment || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <label className="field"><span>Workload (%)</span>
            <input type="number" min={1} max={100} value={form.workload} onChange={(e) => set("workload", e.target.value)} />
          </label>
          <label className="field"><span>First day</span>
            <input type="date" value={form.start_date || ""} onChange={(e) => set("start_date", e.target.value)} />
          </label>
          <label className="field"><span>Last day</span>
            <input type="date" value={form.end_date || ""} onChange={(e) => set("end_date", e.target.value)} />
          </label>
          <label className="field"><span>Holiday days a year</span>
            <input type="number" min={0} max={60} step={0.5} value={form.holiday_days} onChange={(e) => set("holiday_days", e.target.value)}
                   placeholder={isNew ? "company default" : ""} />
          </label>
          <label className="field"><span>Carried over / correction {year} (days)</span>
            <input type="number" min={-60} max={60} step={0.5} value={carry} onChange={(e) => setCarry(e.target.value)} placeholder="0" />
          </label>
          {status?.timetrack && (
            <label className="field wide"><span>Name in the time-tracking app</span>
              <input value={form.timetrack_user} maxLength={60} onChange={(e) => set("timetrack_user", e.target.value)}
                     placeholder={`Empty = “${form.name || "their name"}”`} />
            </label>
          )}
          <label className="field wide"><span>Notes</span>
            <textarea rows={3} value={form.notes} maxLength={3000} onChange={(e) => set("notes", e.target.value)} />
          </label>
          {isNew ? (
            <label className="toggle wide"><input type="checkbox" checked={onboarding} onChange={(e) => setOnboarding(e.target.checked)} />
              <span>Start the onboarding checklist (due dates count from the first day)</span></label>
          ) : (
            <label className="toggle wide"><input type="checkbox" checked={offboarding} onChange={(e) => setOffboarding(e.target.checked)} />
              <span>Start offboarding (needs a last day; due dates count from it)</span></label>
          )}
          <label className="field wide"><span>Reason (optional, shown to the approver)</span>
            <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy}><Icon name="approve" className="btn-ico" />Send for approval</button>
        </div>
      </form>
    </div>
  );
}
