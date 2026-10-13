import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { usePoll } from "../../hooks.js";
import { ErrorNote, Tag } from "../common.jsx";
import { absenceWhen, ABSENCE_TONE } from "./EmployeeView.jsx";

/* The team calendar: one month, people as rows, their absences as coloured days (striped while waiting for
   approval), weekends and public holidays shaded. Below: the month's absences as a list. */

const localToday = () => new Date().toLocaleDateString("sv-SE");
const shift = (month, by) => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + by, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};

export default function TeamCalendar({ status, rev, onOpen }) {
  const [month, setMonth] = useState(localToday().slice(0, 7));
  const { data: cal, error } = usePoll(() => api(`/team/calendar?month=${month}`), 60000, [month, rev]);
  const wrap = useRef(null);
  useEffect(() => {                                     // a month wider than the screen opens at today
    const el = wrap.current?.querySelector(".team-day-head.today");
    if (el && wrap.current.scrollWidth > wrap.current.clientWidth) wrap.current.scrollLeft = Math.max(0, el.offsetLeft - 200);
  }, [cal?.month]);
  const title = new Date(`${month}-15T12:00:00`).toLocaleDateString([], { month: "long", year: "numeric" });
  const byPerson = {};
  for (const a of cal?.absences || []) (byPerson[a.employee_id] ||= []).push(a);
  const dayOf = (pid, d) => (byPerson[pid] || []).find((a) => a.start_date <= d && a.end_date >= d);
  const kinds = status?.kinds || {};
  return (
    <div className="team-calendar">
      <div className="mail-toolbar">
        <div className="mail-filters">
          <b className="mail-title">Absences</b>
          <div className="team-month-nav">
            <button className="btn ghost small" onClick={() => setMonth(shift(month, -1))} aria-label="Previous month">‹</button>
            <span className="team-month">{title}</span>
            <button className="btn ghost small" onClick={() => setMonth(shift(month, 1))} aria-label="Next month">›</button>
            {month !== localToday().slice(0, 7) && <button className="btn ghost small" onClick={() => setMonth(localToday().slice(0, 7))}>Today</button>}
          </div>
        </div>
      </div>
      <ErrorNote error={error} />
      {cal && (
        <>
          <div className="team-grid-wrap" ref={wrap}>
            <div className="team-grid" style={{ gridTemplateColumns: `minmax(110px, 160px) repeat(${cal.days.length}, minmax(22px, 1fr))` }}
                 role="grid" aria-label={`Absences in ${title}`}>
              <div className="team-grid-corner" />
              {cal.days.map((d) => (
                <div key={d.date} className={`team-day-head${d.weekend ? " we" : ""}${d.holiday ? " ph" : ""}${d.date === cal.today ? " today" : ""}`}
                     title={d.holiday ? "Public holiday" : undefined}>
                  <span>{new Date(`${d.date}T12:00:00`).toLocaleDateString([], { weekday: "narrow" })}</span><b>{Number(d.date.slice(8))}</b>
                </div>
              ))}
              {cal.people.length === 0 && <div className="team-grid-empty muted small">Nobody in the directory yet.</div>}
              {cal.people.map((p) => (
                <div key={p.id} className="team-grid-row" role="row">
                  {onOpen ? <button type="button" className="team-person linkish" onClick={() => onOpen(p.id)}>{p.name}</button>
                    : <span className="team-person">{p.name}</span>}
                  {cal.days.map((d) => {
                    const a = dayOf(p.id, d.date);
                    const cls = `team-cell${d.weekend ? " we" : ""}${d.holiday ? " ph" : ""}${d.date === cal.today ? " today" : ""}${a && !d.weekend && !d.holiday ? ` k-${a.kind} s-${a.status}${a.half_day ? " half" : ""}` : ""}`;
                    return <div key={d.date} className={cls} title={a ? `${p.name}: ${a.kind_label} (${a.status})` : undefined} />;
                  })}
                </div>
              ))}
            </div>
          </div>
          <div className="team-legend muted tiny">
            {Object.entries(kinds).map(([k, l]) => <span key={k}><i className={`team-cell k-${k} s-approved`} />{l}</span>)}
            <span><i className="team-cell k-holiday s-pending" />waiting for approval</span>
            <span><i className="team-cell ph" />public holiday</span>
          </div>
          <section className="team-card">
            <h3 className="sub">This month</h3>
            {cal.absences.length === 0 ? <p className="muted small">Everyone's in.</p> : (
              <ul className="team-absences">
                {cal.absences.map((a) => (
                  <li key={a.id} className={`k-${a.kind}`}>
                    <b className="team-abs-who">{a.employee_name}</b>
                    <span className="team-abs-kind">{a.kind_label}</span>
                    <span className="team-abs-when">{absenceWhen(a)}<span className="meta"> · {a.days} d</span></span>
                    <Tag kind={ABSENCE_TONE[a.status]}>{a.status === "pending" ? "waiting for approval" : a.status}</Tag>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
