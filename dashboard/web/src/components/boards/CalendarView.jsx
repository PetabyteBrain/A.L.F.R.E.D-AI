import { useState } from "react";

/* Items by due date on a month grid. Drag an item to another day to move its due date; items without a date wait
   on the right. */

const local = (d) => d.toLocaleDateString("sv-SE");

export default function CalendarView({ items, canEdit, onOpen, onDue }) {
  const [month, setMonth] = useState(() => local(new Date()).slice(0, 7));
  const [drag, setDrag] = useState(null);
  const [over, setOver] = useState("");
  const [y, m] = month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));            // Monday before the 1st
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  const today = local(new Date());
  const byDay = {};
  for (const c of items) if (c.due_date) (byDay[c.due_date] ||= []).push(c);
  const undated = items.filter((c) => !c.due_date && !c.done);
  const shift = (by) => { const d = new Date(y, m - 1 + by, 1); setMonth(local(d).slice(0, 7)); };
  const editable = (c) => canEdit && c.kind !== "deal";

  return (
    <div className="bd-cal-wrap">
      <div className="bd-cal">
        <div className="team-month-nav">
          <button className="btn ghost small" onClick={() => shift(-1)} aria-label="Previous month">‹</button>
          <span className="team-month">{first.toLocaleDateString([], { month: "long", year: "numeric" })}</span>
          <button className="btn ghost small" onClick={() => shift(1)} aria-label="Next month">›</button>
          {month !== today.slice(0, 7) && <button className="btn ghost small" onClick={() => setMonth(today.slice(0, 7))}>Today</button>}
        </div>
        <div className="bd-cal-grid">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="bd-cal-dow">{d}</div>)}
          {days.map((d) => {
            const key = local(d);
            const list = byDay[key] || [];
            return (
              <div key={key} className={`bd-cal-day${d.getMonth() !== m - 1 ? " other" : ""}${key === today ? " today" : ""}${over === key ? " over" : ""}${d.getDay() % 6 === 0 ? " we" : ""}`}
                   onDragOver={(e) => { if (drag) { e.preventDefault(); setOver(key); } }} onDragLeave={() => setOver("")}
                   onDrop={(e) => { e.preventDefault(); if (drag && drag.due_date !== key) onDue(drag, key); setDrag(null); setOver(""); }}>
                <span className="bd-cal-num">{d.getDate()}</span>
                {list.slice(0, 4).map((c) => (
                  <button key={c.key} type="button" className={`bd-cal-chip${c.done ? " done" : ""}${c.overdue ? " overdue" : ""}`}
                          style={{ "--type": c.type_color }} draggable={editable(c)} onClick={() => onOpen(c)}
                          onDragStart={(e) => { setDrag(c); e.dataTransfer.setData("text/plain", c.key); }} onDragEnd={() => setDrag(null)}
                          title={`${c.key} ${c.title}${c.assignee_name ? ` · ${c.assignee_name}` : ""}`}>{c.title}</button>
                ))}
                {list.length > 4 && <span className="meta">+{list.length - 4} more</span>}
              </div>
            );
          })}
        </div>
      </div>
      <aside className="bd-cal-side">
        <h3 className="sub">No due date <span className="mail-count plain">{undated.length}</span></h3>
        {canEdit && undated.length > 0 && <p className="muted tiny">Drag one onto a day to give it a date.</p>}
        {undated.slice(0, 40).map((c) => (
          <button key={c.key} type="button" className="bd-cal-chip wide" style={{ "--type": c.type_color }} draggable={editable(c)}
                  onClick={() => onOpen(c)} onDragStart={(e) => { setDrag(c); e.dataTransfer.setData("text/plain", c.key); }}
                  onDragEnd={() => setDrag(null)}>{c.key} {c.title}</button>
        ))}
      </aside>
    </div>
  );
}
