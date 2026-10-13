import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { Balance } from "./EmployeeView.jsx";

/* Ask for time off (or, for people managing employees, record someone's absence). It waits for approval; weekends
   and public holidays don't count, a single day can be a half day. */

const KIND_ICON = { holiday: "🌴", sick: "🤒", unpaid: "⏸", training: "🎓", parental: "👶", other: "•" };

function weekdays(a, b) {
  if (!a || !b || b < a) return 0;
  let n = 0;
  for (let d = new Date(`${a}T12:00:00`); d <= new Date(`${b}T12:00:00`); d.setDate(d.getDate() + 1)) if (d.getDay() % 6) n += 1;
  return n;
}

export default function AbsenceDialog({ employee, status, onSaved, onClose }) {
  const canOthers = !!status?.can?.manage;
  const me = status?.me;
  const [who, setWho] = useState(employee?.id || me?.id || "");
  const [kind, setKind] = useState("holiday");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [half, setHalf] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const isMe = me && Number(who) === me.id;
  const single = start && (!end || end === start);
  const n = single ? (half ? 0.5 : weekdays(start, start)) : weekdays(start, end);

  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      onSaved(await api("/team/absences", { method: "POST", body: {
        kind, start, end: end || start, half_day: single && half, note, employee_id: isMe ? null : Number(who) || null } }));
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor" role="dialog" aria-modal="true" aria-label="Time off" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head">
          <h2><span className="hud-code">TO</span>{isMe ? "Request time off" : "Record an absence"}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <ErrorNote error={err} />
        {canOthers && (status?.managers || []).length > 0 && (
          <label className="field"><span>For</span>
            <select value={who} onChange={(e) => setWho(e.target.value)} required>
              <option value="">Choose…</option>
              {status.managers.map((m) => <option key={m.id} value={m.id}>{m.name}{me && m.id === me.id ? " (me)" : ""}</option>)}
            </select>
          </label>
        )}
        <div className="pick-chips team-kinds" role="radiogroup" aria-label="Kind">
          {Object.entries(status?.kinds || {}).map(([k, l]) => (
            <button key={k} type="button" role="radio" aria-checked={kind === k} className={`chip${kind === k ? "" : " off"}`}
                    onClick={() => setKind(k)}>{KIND_ICON[k]} {l}</button>
          ))}
        </div>
        <div className="form-grid">
          <label className="field"><span>From</span>
            <input type="date" required value={start} onChange={(e) => { setStart(e.target.value); if (!end || end < e.target.value) setEnd(e.target.value); }} />
          </label>
          <label className="field"><span>To (including)</span>
            <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
          </label>
          {single && (
            <label className="toggle wide"><input type="checkbox" checked={half} onChange={(e) => setHalf(e.target.checked)} /><span>Half a day</span></label>
          )}
          <label className="field wide"><span>Note (optional)</span>
            <input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="e.g. family trip, doctor's certificate follows" />
          </label>
        </div>
        {start && <p className="small">{n ? `About ${n} working day${n === 1 ? "" : "s"}` : "Only weekend days"}{n ? " (public holidays don't count)." : "."}</p>}
        {isMe && kind === "holiday" && me?.balance && <Balance b={me.balance} compact />}
        <p className="muted tiny">It waits for approval; you'll hear the decision on Telegram. Approved time off shows on the team calendar,
          and Alfred won't put calls from others through to someone who is away.</p>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !start || !who}><Icon name="approve" className="btn-ico" />Send for approval</button>
        </div>
      </form>
    </div>
  );
}
