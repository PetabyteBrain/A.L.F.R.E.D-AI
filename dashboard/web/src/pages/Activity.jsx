import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

const PERIODS = [[7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "1 year"], [0, "everything"]];

/* Admin: download the event log or the blocked-access log as CSV (for audits / records). */
function ExportBar() {
  const [days, setDays] = useState(90);
  const [kind, setKind] = useState("");
  const url = (source) => `/api/audit/export?source=${source}&days=${Number(days)}${source === "activity" && kind ? `&kind=${kind}` : ""}`;
  return (
    <div className="export-bar">
      <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {PERIODS.map(([d, l]) => <option key={d} value={d}>{l}</option>)}
      </select>
      <select aria-label="Events" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="">all events</option>
        <option value="access">sign-ins &amp; security</option>
        <option value="call">calls</option>
        <option value="message">messages</option>
        <option value="system">system</option>
        <option value="error">errors</option>
      </select>
      <a className="btn small" href={url("activity")} download>Event log CSV</a>
      <a className="btn small" href={url("access")} download>Blocked access CSV</a>
    </div>
  );
}

export default function Activity({ isAdmin }) {
  const { data, error } = usePoll(() => api("/activity?limit=150"), 5000);
  return (
    <Panel code="05" title="Event log" actions={isAdmin && <ExportBar />}>
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No activity yet.</Empty>}
      <div className="feed">
        {(data || []).map((a) => (
          <div className="e" key={a.id}>
            <span className="ts">{fmtTime(a.ts)}</span>
            <span className={`kind ${a.kind === "access" ? "access" : ""}`}>{a.kind}</span>
            <span>{a.user && <><b>{a.user}</b> · </>}{a.summary}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
