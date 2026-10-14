import { useState } from "react";
import { fmtCHF, fmtDate } from "../../format.js";

/* The board: one column per state, cards you drag between columns (and, in the team lanes, between teams).
   `lanes` = an array of {id, name, color} rows stacked one above the other; without it, a classic board. */

export function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

export function Card({ c, onOpen, draggable, onDragStart, onDragEnd, dragging, compact = false }) {
  return (
    <article className={`bd-card${c.done ? " done" : ""}${dragging ? " dragging" : ""}${c.priority === 1 ? " urgent" : ""}`}
             style={{ "--type": c.type_color || "var(--accent)" }} draggable={draggable}
             onDragStart={onDragStart} onDragEnd={onDragEnd}>
      <button type="button" className="bd-card-main" onClick={() => onOpen(c)}>
        <span className="bd-card-top">
          <span className="bd-type">{c.type_name}</span>
          <span className="bd-key">{c.key}</span>
          {c.priority === 1 && <span className="bd-urgent" title="Urgent">!!</span>}
        </span>
        <span className="bd-title">{c.title}</span>
        {!compact && c.tags?.length > 0 && <span className="bd-tags">{c.tags.slice(0, 3).map((t) => <span key={t} className="chip crm-tag">{t}</span>)}</span>}
        <span className="bd-card-foot">
          {c.due_date && <span className={`bd-due${c.overdue ? " overdue" : c.due_today ? " today" : ""}`} title="Due">
            {c.overdue ? "⚠ " : ""}{fmtDate(c.due_date)}</span>}
          {c.value != null && c.kind === "deal" && <span className="bd-value">{fmtCHF(c.value, { compact: true })}</span>}
          {c.children > 0 && <span className="bd-meta" title="Sub-items done">☑ {c.children_done}/{c.children}</span>}
          {c.comments > 0 && <span className="bd-meta" title="Comments">💬 {c.comments}</span>}
          {c.files > 0 && <span className="bd-meta" title="Files">📎 {c.files}</span>}
          {c.company_name && !compact && <span className="bd-meta bd-client" title="Client">{c.company_name}</span>}
          <span className="bd-people">
            {c.team_name && <span className="bd-team" style={{ "--team": c.team_color || "var(--muted)" }} title={`Team ${c.team_name}`} />}
            {c.assignee_name ? <span className="bd-avatar" title={c.assignee_name}>{initials(c.assignee_name)}</span>
              : <span className="bd-avatar nobody" title="Nobody yet">·</span>}
          </span>
        </span>
      </button>
    </article>
  );
}

export default function BoardView({ board, items, lanes, canEdit, onOpen, onMove, onQuickAdd }) {
  const states = (board.states || []).filter((s) => !s.archived);
  const [drag, setDrag] = useState(null);           // the item being dragged
  const [over, setOver] = useState(null);           // {state, lane, index}
  const [collapsed, setCollapsed] = useState({});
  const [adding, setAdding] = useState(null);       // {state, lane}
  const [title, setTitle] = useState("");

  const cardsIn = (stateId, laneId) => items.filter((c) => c.state_id === stateId
    && (laneId === undefined || (c.team_id || 0) === laneId));
  function dragOverCard(e, stateId, laneId, idx) {
    if (!drag) return;
    e.preventDefault(); e.stopPropagation();
    const r = e.currentTarget.getBoundingClientRect();
    const index = e.clientY > r.top + r.height / 2 ? idx + 1 : idx;
    if (!over || over.state !== stateId || over.lane !== laneId || over.index !== index) setOver({ state: stateId, lane: laneId, index });
  }
  function dragOverColumn(e, stateId, laneId, count) {
    if (!drag) return;
    e.preventDefault();
    if (!over || over.state !== stateId || over.lane !== laneId) setOver({ state: stateId, lane: laneId, index: count });
  }
  function drop(e, stateId, laneId) {
    e.preventDefault();
    if (!drag) return;
    const index = over?.state === stateId && over?.lane === laneId ? over.index : cardsIn(stateId, laneId).length;
    const target = { state_id: stateId, index };
    if (laneId !== undefined && (drag.team_id || 0) !== laneId) target.team_id = laneId;
    // Same place: nothing to do.
    const col = cardsIn(stateId, laneId);
    const from = col.findIndex((c) => c.id === drag.id);
    if (drag.state_id === stateId && target.team_id === undefined && (from === index || from + 1 === index)) { setDrag(null); setOver(null); return; }
    onMove(drag, target);
    setDrag(null); setOver(null);
  }
  async function add(e, stateId, laneId) {
    e.preventDefault();
    if (!title.trim()) { setAdding(null); return; }
    const ok = await onQuickAdd({ title: title.trim(), state_id: stateId, ...(laneId ? { team_id: laneId } : {}) });
    if (ok) setTitle("");
  }

  function column(s, laneId, showHead) {
    const cards = cardsIn(s.id, laneId);
    const total = laneId === undefined ? cards.length : items.filter((c) => c.state_id === s.id).length;
    const over_ = s.wip_limit && total > s.wip_limit;
    const isOver = over?.state === s.id && over?.lane === laneId;
    return (
      <div key={`${s.id}:${laneId ?? "all"}`} className={`bd-col c-${s.category}${isOver ? " over" : ""}`}
           onDragOver={(e) => dragOverColumn(e, s.id, laneId, cards.length)} onDrop={(e) => drop(e, s.id, laneId)}
           onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(null); }}>
        {showHead && (
          <header className="bd-col-head">
            <b>{s.name}</b>
            <span className={`bd-count${over_ ? " warn" : ""}`} title={s.wip_limit ? `Work in progress limit ${s.wip_limit}` : undefined}>
              {total}{s.wip_limit ? ` / ${s.wip_limit}` : ""}</span>
            {s.probability != null && <span className="meta">{s.probability} %</span>}
          </header>
        )}
        <div className="bd-cards">
          {cards.map((c, i) => (
            <div key={c.id} className={`bd-slot${isOver && over.index === i ? " drop-before" : ""}`}
                 onDragOver={(e) => dragOverCard(e, s.id, laneId, i)}>
              <Card c={c} onOpen={onOpen} draggable={canEdit} dragging={drag?.id === c.id}
                    onDragStart={(e) => { setDrag(c); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.key); }}
                    onDragEnd={() => { setDrag(null); setOver(null); }} />
            </div>
          ))}
          {isOver && over.index >= cards.length && <div className="bd-drop-end" />}
          {canEdit && board.kind !== "pipeline" && s.category !== "done" && (
            adding?.state === s.id && adding?.lane === laneId ? (
              <form className="bd-quick" onSubmit={(e) => add(e, s.id, laneId)}>
                <input autoFocus value={title} maxLength={200} placeholder="Title, then Enter" onChange={(e) => setTitle(e.target.value)}
                       onBlur={() => { if (!title.trim()) setAdding(null); }} onKeyDown={(e) => e.key === "Escape" && setAdding(null)} />
              </form>
            ) : (
              <button type="button" className="bd-add" onClick={() => { setAdding({ state: s.id, lane: laneId }); setTitle(""); }}>+ Add</button>
            )
          )}
        </div>
      </div>
    );
  }

  if (!lanes) {
    return <div className="bd-board" style={{ "--cols": states.length }}>{states.map((s) => column(s, undefined, true))}</div>;
  }
  return (
    <div className="bd-lanes" style={{ "--cols": states.length }}>
      <div className="bd-lane-head-row">
        {states.map((s) => {
          const total = items.filter((c) => c.state_id === s.id).length;
          return <div key={s.id} className={`bd-col-head sticky c-${s.category}`}><b>{s.name}</b>
            <span className={`bd-count${s.wip_limit && total > s.wip_limit ? " warn" : ""}`}>{total}{s.wip_limit ? ` / ${s.wip_limit}` : ""}</span></div>;
        })}
      </div>
      {lanes.map((l) => {
        const count = items.filter((c) => (c.team_id || 0) === l.id).length;
        const open = !collapsed[l.id];
        return (
          <section key={l.id} className={`bd-lane${open ? "" : " closed"}`} style={{ "--team": l.color || "var(--muted)" }}>
            <button type="button" className="bd-lane-title" onClick={() => setCollapsed({ ...collapsed, [l.id]: open })} aria-expanded={open}>
              <span className="bd-lane-dot" />{l.name}<span className="mail-count plain">{count}</span>
              {l.manager_name && <span className="meta">manager {l.manager_name}</span>}<span className="bd-lane-caret">{open ? "▾" : "▸"}</span>
            </button>
            {open && <div className="bd-board in-lane">{states.map((s) => column(s, l.id, false))}</div>}
          </section>
        );
      })}
    </div>
  );
}
