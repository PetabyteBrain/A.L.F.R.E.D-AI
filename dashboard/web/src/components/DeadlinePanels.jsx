import { useEffect, useState } from "react";
import { api } from "../api.js";
import { navigate, usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Deadlines from the CRM and the boards in the calendar: what one is, where it comes from, and syncing it (or its
   whole board / the pipeline / invoices) to your Google Calendar. */

const KIND_LABEL = { work: "Board item due", deal_close: "Deal expected to close", deal_step: "Deal next step",
                     invoice: "Invoice due", quote: "Quote valid until" };
const fmtDay = (d) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });

export function DeadlinePanel({ item, calendarLinked, onClose, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  async function run(fn, ok) {
    setBusy(true); setErr(""); setMsg("");
    try { const r = await fn(); setMsg(typeof ok === "function" ? ok(r) : ok); onChanged?.(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const sum = (r) => [r.created && `${r.created} added`, r.updated && `${r.updated} updated`, r.removed && `${r.removed} removed`]
    .filter(Boolean).join(", ") || "Everything was already in your calendar";
  return (
    <div className="hud agenda-editor dl-panel" role="dialog" aria-label="Deadline">
      <div className="note-head">
        <h3 className="sub flush"><span className={`dl-badge src-${item.source.split(":")[0]}`}>{item.badge}</span> {KIND_LABEL[item.deadline] || "Deadline"}</h3>
        <button className="btn small" onClick={onClose}>Close</button>
      </div>
      <p className="dl-title">{item.title}</p>
      <div className="kv"><span>When</span><b className={item.overdue ? "warn-text" : ""}>{fmtDay(item.start)}{item.overdue ? " (overdue)" : ""}</b></div>
      <div className="kv"><span>From</span><b>{item.source_label}</b></div>
      {item.who && <div className="kv"><span>Who</span><b>{item.who}</b></div>}
      <p className="muted tiny">This comes from {item.source.startsWith("board") ? "a board" : "the CRM"}: change the date there and the calendar follows.</p>
      <div className="form-actions">
        <button className="btn primary small" onClick={() => navigate(item.open.tab, item.open)}>Open it</button>
      </div>
      <div className="dl-sync">
        <h4 className="sub flush">Google Calendar</h4>
        {!calendarLinked ? <p className="muted small">Link your Google Calendar on your Profile to put deadlines into it.</p> : (
          <>
            <p className="small">
              {item.synced ? (item.sync_stale ? <Tag kind="busy">in your calendar, out of date</Tag> : <Tag kind="ok">✓ in your calendar</Tag>)
                : <Tag>not in your calendar</Tag>}
              {item.source_followed && <> <Tag kind="ok">⟳ {item.source_label} kept in sync</Tag></>}
            </p>
            <div className="form-actions">
              {(!item.synced || item.sync_stale) && (
                <button className="btn small" disabled={busy} onClick={() => run(() => api("/agenda/deadlines/sync", { method: "POST", body: { keys: [item.key] } }),
                  item.synced ? "Updated in your calendar." : "Added to your calendar.")}>
                  {item.synced ? "Update the event" : "Sync this event"}</button>
              )}
              {item.synced && (
                <button className="btn ghost small" disabled={busy} onClick={() => run(() => api(`/agenda/deadlines/sync?key=${encodeURIComponent(item.key)}`, { method: "DELETE" }),
                  "Removed from your calendar.")}>Remove from my calendar</button>
              )}
            </div>
            <div className="form-actions">
              {item.source_followed ? (
                <button className="btn ghost small" disabled={busy} onClick={() => run(() => api("/agenda/deadlines/follow", { method: "POST", body: { source: item.source, follow: false } }),
                  `${item.source_label} is no longer kept in sync (events already in your calendar stay).`)}>Stop syncing {item.source_label}</button>
              ) : (
                <button className="btn small" disabled={busy} onClick={() => run(() => api("/agenda/deadlines/follow", { method: "POST", body: { source: item.source, follow: true } }), sum)}>
                  Sync all events from {item.source_label}</button>
              )}
            </div>
            {!item.source_followed && <p className="muted tiny">Syncing all of {item.source_label} adds your deadlines there and keeps them up to date: moved ones move, finished ones disappear.</p>}
          </>
        )}
        {msg && <div className="notice" role="status">{msg}</div>}
        <ErrorNote error={err} />
      </div>
    </div>
  );
}

export function SyncSources({ calendarLinked, onClose, onChanged }) {
  const { data, error, reload } = usePoll(() => api("/agenda/deadline-sources"), 0);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  async function toggle(s) {
    setBusy(s.source); setErr(""); setMsg("");
    try {
      const r = await api("/agenda/deadlines/follow", { method: "POST", body: { source: s.source, follow: !s.followed } });
      setMsg(s.followed ? `${s.label}: no longer kept in sync.` : `${s.label}: ${r.created || 0} added, ${r.updated || 0} updated.`);
      reload(); onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  return (
    <div className="hud agenda-editor dl-panel" role="dialog" aria-label="Sync deadlines">
      <div className="note-head"><h3 className="sub flush">Deadlines in Google Calendar</h3><button className="btn small" onClick={onClose}>Close</button></div>
      <p className="muted small">Pick what goes into your Google Calendar as all-day events. A synced source stays up to date by itself
        (hourly): new deadlines appear, moved ones move, finished ones disappear. Only your own deadlines are added.</p>
      {!calendarLinked && <p className="warn-text small">Link your Google Calendar on your Profile first.</p>}
      <ErrorNote error={error || err} />
      {data && data.length === 0 && <p className="muted small">You have no deadlines in the CRM or on the boards right now.</p>}
      <div className="dl-sources">
        {(data || []).map((s) => (
          <div key={s.source} className="dl-source">
            <span className={`dl-badge src-${s.source.split(":")[0]}`}>{s.badge || "…"}</span>
            <span className="dl-source-name"><b>{s.label}</b><span className="meta">{s.count} upcoming · {s.synced} in your calendar</span></span>
            <button className={`btn small${s.followed ? " primary" : ""}`} disabled={!calendarLinked || busy === s.source} onClick={() => toggle(s)}
                    aria-pressed={s.followed}>{s.followed ? "⟳ Synced" : "Sync all"}</button>
          </div>
        ))}
      </div>
      {msg && <div className="notice" role="status">{msg}</div>}
    </div>
  );
}
