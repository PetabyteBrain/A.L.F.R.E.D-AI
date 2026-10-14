import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { navigate, usePageActive, useStoredState } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";
import { CATEGORIES, CatDot, KIND_ICON } from "./categories.jsx";

const REPEAT = { daily: "every day", weekdays: "weekdays", weekly: "every week", monthly: "every month" };
const VIEWS = { month: "Month", week: "Week", day: "Day" };
const HOUR_PX = 44;
const pad = (n) => String(n).padStart(2, "0");
const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fromKey = (k) => { const [y, m, d] = k.split("-").map(Number); return new Date(y, m - 1, d); };
const localInput = (iso) => { const d = new Date(iso); return `${dayKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const hhmm = (iso) => { const d = new Date(iso); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const monday = (d) => addDays(new Date(d.getFullYear(), d.getMonth(), d.getDate()), -((d.getDay() + 6) % 7));
const mins = (m) => (m === 1440 ? "1 day" : m >= 60 ? `${m / 60} h` : `${m} min`);

/* The days a view shows for the focused date. */
function viewDays(view, focus) {
  if (view === "day") return [new Date(focus.getFullYear(), focus.getMonth(), focus.getDate())];
  if (view === "week") return Array.from({ length: 7 }, (_, i) => addDays(monday(focus), i));
  const start = monday(new Date(focus.getFullYear(), focus.getMonth(), 1));
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));        // 6 weeks, starting on Monday
}

function viewTitle(view, focus, days) {
  if (view === "month") return focus.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  if (view === "day") return focus.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const a = days[0], b = days[6];
  const left = a.toLocaleDateString(undefined, a.getMonth() === b.getMonth() ? { day: "numeric" } : { day: "numeric", month: "short" });
  return `${left} – ${b.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
}

/* Item start/end as Dates (all-day end is exclusive; timed items without an end count as 30 min). */
function span(it) {
  if (it.all_day) return [fromKey(it.start.slice(0, 10)), it.end ? fromKey(it.end.slice(0, 10)) : addDays(fromKey(it.start.slice(0, 10)), 1)];
  const s = new Date(it.start);
  const e = it.end ? new Date(it.end) : new Date(s.getTime() + 30 * 60000);
  return [s, e > s ? e : new Date(s.getTime() + 30 * 60000)];
}

/* Side-by-side lanes for overlapping timed items in one day column. */
function lanes(list, day) {
  const d0 = day.getTime(), d1 = addDays(day, 1).getTime();
  const rows = list.map((it) => {
    const [s, e] = span(it);
    const a = Math.max(s.getTime(), d0), b = Math.min(Math.max(e.getTime(), s.getTime() + 25 * 60000), d1);
    return { it, a, b };
  }).sort((x, y) => x.a - y.a || y.b - x.b);
  const out = [];
  let group = [], groupEnd = 0;
  const flush = () => { const n = Math.max(...group.map((g) => g.lane)) + 1; group.forEach((g) => out.push({ ...g, n })); group = []; };
  for (const r of rows) {
    if (group.length && r.a >= groupEnd) flush();
    const used = new Set(group.filter((g) => g.b > r.a).map((g) => g.lane));
    let lane = 0;
    while (used.has(lane)) lane += 1;
    group.push({ ...r, lane });
    groupEnd = Math.max(groupEnd, r.b);
  }
  if (group.length) flush();
  return out.map((r) => ({ ...r, top: ((r.a - d0) / 3600000) * HOUR_PX, height: Math.max(((r.b - r.a) / 3600000) * HOUR_PX, 18) }));
}

function ItemLabel({ it, time = true }) {
  return (
    <>
      {time && !it.all_day && <span className="mono">{hhmm(it.start)}</span>} {KIND_ICON[it.kind]}
      {it.recurrence ? "↻" : ""}{it.link_state === "linked" ? "🔗" : ""} {it.title}
    </>
  );
}

/* Edit one agenda item: title, time, category; reminder links; "remind me before" for events. */
function ItemEditor({ item, onClose, onChanged }) {
  const [form, setForm] = useState({ title: item.title, start: item.all_day ? "" : localInput(item.start), category: item.category });
  const [before, setBefore] = useState(15);
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
    <div className="hud agenda-editor" role="dialog" aria-label="Edit item">
      <div className="note-head">
        <h3 className="sub flush">{KIND_ICON[item.kind]} {item.kind === "event" ? "Calendar event" : item.kind === "work" ? "Board item due" : item.kind}</h3>
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
      ) : item.kind === "work" ? (
        <div className="stack">
          <p className="small">{item.title} · due {item.start}{item.board ? ` · ${item.board}` : ""}</p>
          <button className="btn primary small" type="button" onClick={() => navigate("boards", { item: item.id, board: item.board_id })}>Open on the board</button>
        </div>
      ) : <p className="muted small">{item.title} · {hhmm(item.start)} (shown here; manage it in the call queue)</p>}
      {item.kind === "event" && (
        <div className="field-row">
          <label className="field"><span>Remind me before</span>
            <select value={before} onChange={(e) => setBefore(Number(e.target.value))}>
              {[0, 5, 10, 15, 30, 60, 120, 1440].map((m) => <option key={m} value={m}>{mins(m)}</option>)}
            </select>
          </label>
          <button className="btn small" disabled={busy}
                  onClick={() => run(() => api("/agenda/remind", { method: "POST", body: { event_id: item.id, minutes_before: before } }))}>Add reminder</button>
        </div>
      )}
    </div>
  );
}

const MODES = {
  reminder: { label: "Remind me", hint: "Alfred acts on it: he reminds you by Telegram or a call.", category: "reminder" },
  task: { label: "Task", hint: "Alfred acts on it: a task due then; he can follow up and ask if it's done.", category: "task" },
  calendar: { label: "Calendar only", hint: "Just a date in your Google Calendar. Alfred won't remind you or act on it.", category: "other" },
};

/* New item from the calendar: what Alfred should do with it, then the details. */
function NewItemEditor({ draft, calendarLinked, onClose, onCreated }) {
  const [f, setF] = useState(() => ({
    mode: "reminder", title: "", date: draft.date, time: draft.time || "09:00", allDay: !!draft.allDay,
    duration: 60, category: "", channel: "telegram", recurrence: "", alsoCalendar: false, before: 15, followUp: "none",
  }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const titleRef = useRef(null);
  useEffect(() => {
    setF((x) => ({ ...x, date: draft.date, time: draft.time || x.time, allDay: !!draft.allDay, mode: draft.allDay ? "calendar" : x.mode }));
    if (draft.allDay && !calendarLinked) setF((x) => ({ ...x, mode: "reminder", allDay: false }));
    titleRef.current?.focus();
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const allDay = f.mode === "calendar" && f.allDay;
  const toCalendar = f.mode === "calendar" || (f.mode === "reminder" && f.alsoCalendar);
  const category = f.category || MODES[f.mode].category;

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api("/agenda", { method: "POST", body: {
        mode: f.mode, title: f.title.trim(), start: allDay ? f.date : `${f.date}T${f.time}`, category,
        duration_min: Number(f.duration), channel: f.channel, recurrence: f.mode === "reminder" ? f.recurrence : "",
        also_calendar: f.mode === "reminder" && f.alsoCalendar, minutes_before: Number(f.before), follow_up: f.followUp,
      } });
      onCreated(r);
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  }
  return (
    <div className="hud agenda-editor" role="dialog" aria-label="New item">
      <div className="note-head">
        <h3 className="sub flush">New · {fromKey(f.date).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</h3>
        <button className="btn small" onClick={onClose}>Close</button>
      </div>
      <form className="stack" onSubmit={submit}>
        <label className="field"><span>Title</span>
          <input ref={titleRef} value={f.title} maxLength={300} required placeholder="What is it?" onChange={set("title")} />
        </label>
        <fieldset className="mode-pick">
          <legend>What should Alfred do with it?</legend>
          {Object.entries(MODES).map(([k, m]) => {
            const off = k === "calendar" && !calendarLinked;
            return (
              <label key={k} className={`mode-opt${f.mode === k ? " on" : ""}${off ? " off" : ""}`}>
                <input type="radio" name="mode" value={k} checked={f.mode === k} disabled={off}
                       onChange={() => setF({ ...f, mode: k, category: "" })} />
                <span><b>{m.label}</b><span className="muted tiny">{off ? "Link your Google Calendar on your Profile first." : m.hint}</span></span>
              </label>
            );
          })}
        </fieldset>
        <div className="field-row">
          <label className="field"><span>Date</span><input type="date" value={f.date} required onChange={set("date")} /></label>
          {!allDay && (
            <label className="field"><span>{f.mode === "reminder" && f.alsoCalendar ? "Event time" : f.mode === "task" ? "Due" : "Time"}</span>
              <input type="time" value={f.time} required onChange={set("time")} />
            </label>
          )}
          {toCalendar && !allDay && (
            <label className="field"><span>Duration</span>
              <select value={f.duration} onChange={set("duration")}>
                {[15, 30, 45, 60, 90, 120, 180, 240, 480].map((m) => <option key={m} value={m}>{m >= 60 ? `${m / 60} h` : `${m} min`}</option>)}
              </select>
            </label>
          )}
        </div>
        {f.mode === "calendar" && (
          <label className="toggle"><input type="checkbox" checked={f.allDay} onChange={set("allDay")} /><span>All day</span></label>
        )}
        {f.mode === "reminder" && (
          <>
            <div className="field-row">
              <label className="field"><span>How</span>
                <select value={f.channel} onChange={set("channel")}><option value="telegram">Telegram</option><option value="call">Phone call</option></select>
              </label>
              <label className="field"><span>Repeat</span>
                <select value={f.recurrence} onChange={(e) => setF({ ...f, recurrence: e.target.value, alsoCalendar: e.target.value ? false : f.alsoCalendar })}>
                  <option value="">Doesn't repeat</option>
                  {Object.entries(REPEAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
            </div>
            <label className={`toggle${!calendarLinked || f.recurrence ? " off" : ""}`}>
              <input type="checkbox" checked={f.alsoCalendar} disabled={!calendarLinked || !!f.recurrence} onChange={set("alsoCalendar")} />
              <span>Also put it in my calendar (the reminder follows the event)</span>
            </label>
            {f.recurrence && <p className="muted tiny">Repeating reminders stay off the calendar (they can't follow one event).</p>}
            {f.alsoCalendar && (
              <label className="field"><span>Remind me</span>
                <select value={f.before} onChange={set("before")}>
                  {[0, 5, 10, 15, 30, 60, 120, 1440].map((m) => <option key={m} value={m}>{m === 0 ? "at the start" : `${mins(m)} before`}</option>)}
                </select>
              </label>
            )}
          </>
        )}
        {f.mode === "task" && (
          <label className="field"><span>Follow-up</span>
            <select value={f.followUp} onChange={set("followUp")}>
              <option value="none">No follow-up (just a due date)</option>
              <option value="telegram">Alfred asks by Telegram when it's due</option>
              <option value="call">Alfred calls when it's due</option>
            </select>
          </label>
        )}
        <label className="field"><span>Category</span>
          <select value={category} onChange={set("category")}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <ErrorNote error={err} />
        <div className="form-actions">
          <button className="btn primary small" type="submit" disabled={busy || !f.title.trim()}>
            {f.mode === "calendar" ? "Add to calendar" : f.mode === "task" ? "Create task" : "Set reminder"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* Week / day: hours down the side, items placed by time, click an empty slot to add something. */
function TimeGrid({ days, byDay, timed, today, onPick, onNew, onDay }) {
  const scroller = useRef(null);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = 7 * HOUR_PX; }, [days.length]);
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 60000); return () => clearInterval(id); }, []);
  const slot = (day, e) => {
    const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
    const half = Math.max(0, Math.min(47, Math.floor(y / (HOUR_PX / 2))));
    onNew({ date: dayKey(day), time: `${pad(Math.floor(half / 2))}:${half % 2 ? "30" : "00"}` });
  };
  return (
    <div className={`timegrid${days.length === 1 ? " single" : ""}`}>
      <div className="tg-head">
        <div className="tg-gutter" />
        {days.map((d) => (
          <button key={dayKey(d)} className={`tg-day${dayKey(d) === today ? " today" : ""}`} onClick={() => onDay(d)}
                  title="Show this day">
            <span>{d.toLocaleDateString(undefined, { weekday: "short" })}</span> <b>{d.getDate()}</b>
          </button>
        ))}
      </div>
      <div className="tg-allday">
        <div className="tg-gutter muted tiny">all day</div>
        {days.map((d) => (
          <div key={dayKey(d)} className="tg-allday-cell" onClick={(e) => { if (e.target === e.currentTarget) onNew({ date: dayKey(d), allDay: true }); }}>
            {(byDay[dayKey(d)] || []).filter((it) => it.all_day).map((it) => (
              <button key={it.key} className={`ev cat-${it.category}`} onClick={() => onPick(it)} title={it.title}><ItemLabel it={it} /></button>
            ))}
          </div>
        ))}
      </div>
      <div className="tg-body" ref={scroller}>
        <div className="tg-hours">
          {Array.from({ length: 24 }, (_, h) => <div key={h} className="tg-hour mono tiny">{pad(h)}:00</div>)}
        </div>
        {days.map((d) => (
          <div key={dayKey(d)} className={`tg-col${dayKey(d) === today ? " today" : ""}`}
               onClick={(e) => { if (e.target === e.currentTarget) slot(d, e); }} title="Click an empty slot to add something">
            {lanes(timed(d), d).map(({ it, top, height, lane, n }) => (
              <button key={it.key} className={`tg-ev cat-${it.category}${height < 34 ? " short" : ""}${it.link_state === "event_deleted" ? " gone" : ""}`}
                      style={{ top, height, left: `calc(${(lane / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)` }}
                      onClick={() => onPick(it)} title={`${hhmm(it.start)} ${it.title}`}>
                <ItemLabel it={it} />
              </button>
            ))}
            {dayKey(d) === today && <div className="tg-now" style={{ top: ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_PX }} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/* The agenda: events, reminders (incl. repeats), tasks with due dates and calls, as month, week or day. */
export default function AgendaCalendar() {
  const active = usePageActive();
  const [view, setView] = useStoredState("alfred-cal-view", "month");
  const [focus, setFocus] = useState(() => new Date());
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [hidden, setHidden] = useState(() => new Set());
  const [sel, setSel] = useState(null);
  const [draft, setDraft] = useState(null);
  const [day, setDay] = useState(null);
  const days = useMemo(() => viewDays(view, focus), [view, focus]);
  const rangeKey = `${dayKey(days[0])}:${days.length}`;

  async function load() {
    setErr("");
    try {
      const start = new Date(days[0]); start.setHours(0, 0, 0, 0);
      const end = addDays(start, days.length); end.setSeconds(-1);
      setData(await api(`/agenda?${new URLSearchParams({ start: start.toISOString(), end: end.toISOString() })}`));
    } catch (e) { setErr(e.message); }
  }
  useEffect(() => { if (active) load(); }, [rangeKey, active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Items per day; all-day items spanning several days show on each of them.
  const byDay = useMemo(() => {
    const m = {};
    for (const it of data?.items || []) {
      if (hidden.has(it.category)) continue;
      if (it.all_day) {
        const [s, e] = span(it);
        for (let d = s; d < e && d <= days[days.length - 1]; d = addDays(d, 1)) (m[dayKey(d)] ||= []).push(it);
      } else {
        (m[dayKey(new Date(it.start))] ||= []).push(it);
      }
    }
    return m;
  }, [data, hidden, days]);
  // Timed items touching a day (week/day views also show the part of an event that runs past midnight).
  const timed = (d) => (data?.items || []).filter((it) => {
    if (it.all_day || hidden.has(it.category)) return false;
    const [s, e] = span(it);
    return s < addDays(d, 1) && e > d;
  });
  const toggle = (c) => setHidden((h) => { const n = new Set(h); n.has(c) ? n.delete(c) : n.add(c); return n; });
  const shift = (n) => setFocus((f) => (view === "month" ? new Date(f.getFullYear(), f.getMonth() + n, 1) : addDays(f, view === "week" ? 7 * n : n)));
  const today = dayKey(new Date());
  const changed = async () => { await load(); setSel(null); };
  const pick = (it) => { setDraft(null); setSel(it); };
  const newItem = (d) => { setSel(null); setDraft({ ...d }); };
  const openDay = (d) => { setFocus(d); setView("day"); setDay(null); };
  const linked = !!data?.calendar?.linked;

  return (
    <section className="agenda">
      <div className="agenda-bar">
        <div className="agenda-nav">
          <div className="seg">
            <button onClick={() => shift(-1)} aria-label={`Previous ${view}`}>‹</button>
            <button onClick={() => setFocus(new Date())}>Today</button>
            <button onClick={() => shift(1)} aria-label={`Next ${view}`}>›</button>
          </div>
          <label className="jump" title="Go to a date (keeps this view)">
            <span className="sr-only">Go to date</span>
            <input type="date" value={dayKey(focus)} onChange={(e) => { if (e.target.value) setFocus(fromKey(e.target.value)); }} />
          </label>
          <div className="seg" role="tablist" aria-label="Calendar view">
            {Object.entries(VIEWS).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={view === k} className={view === k ? "active" : ""} onClick={() => setView(k)}>{label}</button>
            ))}
          </div>
          <button className="btn primary small" onClick={() => newItem({ date: dayKey(focus) })}>+ New</button>
        </div>
        <h3 className="sub flush">{viewTitle(view, focus, days)}</h3>
        <div className="cat-filters" role="group" aria-label="Categories">
          {CATEGORIES.map((c) => (
            <button key={c} className={`chip ${hidden.has(c) ? "off" : ""}`} onClick={() => toggle(c)} aria-pressed={!hidden.has(c)}>
              <CatDot category={c} />{c}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote error={err || data?.calendar?.error} />
      {data && !linked && <p className="muted small">Link your Google Calendar on your Profile to see its events here.</p>}
      <p className="muted tiny">Click an empty {view === "month" ? "part of a day" : "slot"} to add a reminder, task or calendar entry.</p>

      {view === "month" ? (
        <div className="month">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="dow">{d}</div>)}
          {days.map((d) => {
            const k = dayKey(d);
            const list = byDay[k] || [];
            return (
              <div key={k} className={`cell${d.getMonth() !== focus.getMonth() ? " out" : ""}${k === today ? " today" : ""}`}
                   onClick={(e) => { if (e.target === e.currentTarget) newItem({ date: k }); }}>
                <button className="cell-day" onClick={() => setDay(k)} title="Everything on this day">{d.getDate()}</button>
                {list.slice(0, 4).map((it) => (
                  <button key={it.key} className={`ev cat-${it.category}${it.link_state === "event_deleted" ? " gone" : ""}`}
                          onClick={() => pick(it)} title={it.title}>
                    <ItemLabel it={it} />
                  </button>
                ))}
                {list.length > 4 && <button className="more" onClick={() => setDay(k)}>+{list.length - 4} more</button>}
              </div>
            );
          })}
        </div>
      ) : (
        <TimeGrid days={days} byDay={byDay} timed={timed} today={today} onPick={pick} onNew={newItem} onDay={openDay} />
      )}

      {day && (
        <div className="hud agenda-day">
          <div className="note-head">
            <h3 className="sub flush">{fromKey(day).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</h3>
            <button className="btn small" onClick={() => setDay(null)}>Close</button>
          </div>
          <div className="form-actions">
            <button className="btn primary small" onClick={() => newItem({ date: day })}>+ New on this day</button>
            <button className="btn small" onClick={() => openDay(fromKey(day))}>Open day view</button>
          </div>
          {(byDay[day] || []).length === 0 && <p className="muted small">Nothing on this day.</p>}
          {(byDay[day] || []).map((it) => (
            <button key={it.key} className="row click" onClick={() => pick(it)}>
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
      {draft && <NewItemEditor draft={draft} calendarLinked={linked} onClose={() => setDraft(null)}
                               onCreated={async () => { setDraft(null); setDay(null); await load(); }} />}
    </section>
  );
}
