import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ErrorNote, Tag } from "./common.jsx";
import { CATEGORIES, CatDot, KIND_ICON } from "./categories.jsx";

const REPEAT = { daily: "every day", weekdays: "weekdays", weekly: "every week", monthly: "every month" };
const pad = (n) => String(n).padStart(2, "0");
const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localInput = (iso) => { const d = new Date(iso); return `${dayKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const hhmm = (iso) => { const d = new Date(iso); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function monthGrid(anchor) {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));                // weeks start on Monday
  return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
}

/* Edit one agenda item: title, time, category; reminder links; "remind me before" for events. */
function ItemEditor({ item, onClose, onChanged }) {
  const [form, setForm] = useState({ title: item.title, start: item.all_day ? "" : localInput(item.start), category: item.category });
  const [mins, setMins] = useState(15);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    setForm({ title: item.title, start: item.all_day ? "" : localInput(item.start), category: item.category });
    setErr("");
  }, [item.key]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn) {
    setBusy(true); setErr("");
    try { await fn(); onChanged(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const save = () => run(() => {
    const body = { kind: item.kind, id: String(item.id) };
    if (form.title.trim() && form.title !== item.title) body.title = form.title.trim();
    if (form.start && form.start !== localInput(item.start)) body.start = form.start;
    if (form.category !== item.category) body.category = form.category;
    return api("/agenda", { method: "PATCH", body });
  });
  const editable = item.editable;
  return (
    <div className="hud agenda-editor">
      <div className="note-head">
        <h3 className="sub flush">{KIND_ICON[item.kind]} {item.kind === "event" ? "Calendar event" : item.kind}</h3>
        <button className="btn small" onClick={onClose}>Close</button>
      </div>
      <ErrorNote error={err} />
      {item.recurrence && <p className="small"><Tag>↻ repeats {REPEAT[item.recurrence]}</Tag> Changing it changes the whole series.</p>}
      {item.link_state === "linked" && <p className="small"><Tag kind="ok">🔗 in the calendar</Tag> {item.link_note || "Follows its calendar event."}</p>}
      {item.link_state === "event_deleted" && <p className="small"><Tag kind="bad">not needed</Tag> {item.link_note}</p>}
      {item.link_state === "duplicate" && <p className="small"><Tag kind="busy">probably a duplicate</Tag> {item.link_note}</p>}
      {editable ? (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label className="field"><span>Title</span>
            <input value={form.title} maxLength={300} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </label>
          {!item.all_day && (
            <label className="field"><span>When</span>
              <input type="datetime-local" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </label>
          )}
          <label className="field"><span>Category</span>
            <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          {item.link_state === "linked" && <p className="muted tiny">Moving this reminder by hand stops it following the event.</p>}
          <div className="form-actions">
            <button className="btn primary small" type="submit" disabled={busy}>Save</button>
            {item.kind === "reminder" && ["event_deleted", "duplicate"].includes(item.link_state) && (
              <button className="btn danger small" type="button" disabled={busy}
                      onClick={() => run(() => api(`/reminders/${Number(item.id)}/dismiss`, { method: "POST" }))}>Not needed</button>
            )}
            {item.kind === "reminder" && item.link_state === "duplicate" && item.duplicate_of && (
              <button className="btn small" type="button" disabled={busy}
                      onClick={() => run(() => api(`/reminders/${Number(item.id)}/link`, { method: "POST", body: { event_id: item.duplicate_of, minutes_before: 15 } }))}>
                Tie to that event
              </button>
            )}
            {item.kind === "reminder" && item.link_state === "linked" && (
              <button className="btn small" type="button" disabled={busy}
                      onClick={() => run(() => api(`/reminders/${Number(item.id)}/unlink`, { method: "POST" }))}>Untie from event</button>
            )}
          </div>
        </form>
      ) : <p className="muted small">{item.title} · {hhmm(item.start)} (shown here; manage it in the call queue)</p>}
      {item.kind === "event" && (
        <div className="field-row">
          <label className="field"><span>Remind me before</span>
            <select value={mins} onChange={(e) => setMins(Number(e.target.value))}>
              {[0, 5, 10, 15, 30, 60, 120, 1440].map((m) => <option key={m} value={m}>{m === 1440 ? "1 day" : m >= 60 ? `${m / 60} h` : `${m} min`}</option>)}
            </select>
          </label>
          <button className="btn small" disabled={busy}
                  onClick={() => run(() => api("/agenda/remind", { method: "POST", body: { event_id: item.id, minutes_before: mins } }))}>Add reminder</button>
        </div>
      )}
    </div>
  );
}

/* Month calendar of everything: events, reminders (incl. repeats), tasks with due dates, calls. */
export default function AgendaCalendar() {
  const [anchor, setAnchor] = useState(() => { const d = new Date(); d.setDate(1); return d; });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [hidden, setHidden] = useState(() => new Set());
  const [sel, setSel] = useState(null);
  const [day, setDay] = useState(null);
  const grid = useMemo(() => monthGrid(anchor), [anchor]);

  async function load() {
    setErr("");
    try {
      const start = new Date(grid[0]); start.setHours(0, 0, 0, 0);
      const end = new Date(grid[41]); end.setHours(23, 59, 59, 0);
      setData(await api(`/agenda?${new URLSearchParams({ start: start.toISOString(), end: end.toISOString() })}`));
    } catch (e) { setErr(e.message); }
  }
  useEffect(() => { load(); }, [grid]); // eslint-disable-line react-hooks/exhaustive-deps

  const byDay = useMemo(() => {
    const m = {};
    for (const it of data?.items || []) {
      if (hidden.has(it.category)) continue;
      const k = it.all_day ? it.start.slice(0, 10) : dayKey(new Date(it.start));
      (m[k] ||= []).push(it);
    }
    return m;
  }, [data, hidden]);
  const toggle = (c) => setHidden((h) => { const n = new Set(h); n.has(c) ? n.delete(c) : n.add(c); return n; });
  const shift = (n) => setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + n, 1));
  const today = dayKey(new Date());
  const changed = async () => { await load(); setSel(null); };

  return (
    <section className="agenda">
      <div className="agenda-bar">
        <div className="seg">
          <button onClick={() => shift(-1)} aria-label="Previous month">‹</button>
          <button onClick={() => setAnchor(() => { const d = new Date(); d.setDate(1); return d; })}>Today</button>
          <button onClick={() => shift(1)} aria-label="Next month">›</button>
        </div>
        <h3 className="sub flush">{anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h3>
        <div className="cat-filters" role="group" aria-label="Categories">
          {CATEGORIES.map((c) => (
            <button key={c} className={`chip ${hidden.has(c) ? "off" : ""}`} onClick={() => toggle(c)} aria-pressed={!hidden.has(c)}>
              <CatDot category={c} />{c}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote error={err || data?.calendar?.error} />
      {data && !data.calendar.linked && <p className="muted small">Link your Google Calendar on your Profile to see its events here.</p>}
      <div className="month">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="dow">{d}</div>)}
        {grid.map((d) => {
          const k = dayKey(d);
          const list = byDay[k] || [];
          return (
            <div key={k} className={`cell${d.getMonth() !== anchor.getMonth() ? " out" : ""}${k === today ? " today" : ""}`}>
              <button className="cell-day" onClick={() => setDay(k)}>{d.getDate()}</button>
              {list.slice(0, 4).map((it) => (
                <button key={it.key} className={`ev cat-${it.category}${it.link_state === "event_deleted" ? " gone" : ""}`}
                        onClick={() => setSel(it)} title={it.title}>
                  {!it.all_day && <span className="mono">{hhmm(it.start)}</span>} {KIND_ICON[it.kind]}
                  {it.recurrence ? "↻" : ""}{it.link_state === "linked" ? "🔗" : ""} {it.title}
                </button>
              ))}
              {list.length > 4 && <button className="more" onClick={() => setDay(k)}>+{list.length - 4} more</button>}
            </div>
          );
        })}
      </div>
      {day && (
        <div className="hud agenda-day">
          <div className="note-head">
            <h3 className="sub flush">{new Date(day + "T12:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</h3>
            <button className="btn small" onClick={() => setDay(null)}>Close</button>
          </div>
          {(byDay[day] || []).length === 0 && <p className="muted small">Nothing on this day.</p>}
          {(byDay[day] || []).map((it) => (
            <button key={it.key} className="row click" onClick={() => setSel(it)}>
              <div>
                <div><CatDot category={it.category} /> {KIND_ICON[it.kind]} {it.title}</div>
                <div className="meta">{it.all_day ? "all day" : hhmm(it.start)} · {it.category}{it.recurrence ? ` · repeats ${REPEAT[it.recurrence]}` : ""}
                  {it.link_state === "linked" ? " · in the calendar" : ""}{it.link_state === "event_deleted" ? " · not needed" : ""}</div>
              </div>
            </button>
          ))}
        </div>
      )}
      {sel && <ItemEditor item={sel} onClose={() => setSel(null)} onChanged={changed} />}
    </section>
  );
}
