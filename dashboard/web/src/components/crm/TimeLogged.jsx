import { useState } from "react";
import { api } from "../../api.js";
import { fmtDate } from "../../format.js";
import { usePoll } from "../../hooks.js";

/* Hours logged for a client in the time-tracking app (CRM stage 4): this month, this year, by person, recent
   sessions. Only shown when the time-tracking link is set up. */

export default function TimeLogged({ companyId }) {
  const [open, setOpen] = useState(false);
  const { data: t } = usePoll(() => api(`/crm-actions/time/company/${companyId}`), 0, [companyId]);
  if (!t || !t.configured) return null;
  return (
    <div className="crm-people crm-time">
      <h3 className="sub">Time logged</h3>
      {t.error ? <p className="muted small">{t.error}</p> : (
        <>
          <div className="crm-time-nums">
            <div><b>{t.month} h</b><span className="meta">this month</span></div>
            <div><b>{t.last_30_days} h</b><span className="meta">last 30 days</span></div>
            <div><b>{t.year} h</b><span className="meta">this year</span></div>
          </div>
          {t.by_person.length > 0 && (
            <p className="small">{t.by_person.map((p) => `${p.name} ${p.hours} h`).join(" · ")}</p>
          )}
          {t.recent.length > 0 && (
            <>
              <button type="button" className="btn ghost small" aria-expanded={open} onClick={() => setOpen(!open)}>
                Recent sessions {open ? "▴" : "▾"}</button>
              {open && (
                <ul className="crm-history-list">
                  {t.recent.map((s, i) => (
                    <li key={i}><span>{s.who} · {s.hours} h{s.note ? ` · ${s.note}` : ""}</span><span className="meta">{fmtDate(s.day)}</span></li>
                  ))}
                </ul>
              )}
            </>
          )}
          {t.year === 0 && <p className="muted tiny">Nothing yet: sessions count when their tag is “{t.matched_by}” (set another
            time-tracking tag in Edit).</p>}
        </>
      )}
    </div>
  );
}
