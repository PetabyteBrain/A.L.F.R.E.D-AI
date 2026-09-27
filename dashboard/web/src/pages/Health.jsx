import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

export default function Health({ isAdmin }) {
  const { data, error, reload } = usePoll(() => api("/health"), 15000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function recheck() {
    setBusy(true);
    setErr("");
    try {
      await api("/health/refresh", { method: "POST" });
      await reload();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel code="06" title="Systems diagnostics" actions={isAdmin && (
      <button className="btn ghost small" onClick={recheck} disabled={busy}>{busy ? "Scanning…" : "Run diagnostics"}</button>
    )}>
      <ErrorNote error={error || err} />
      {data && data.length === 0 && <Empty>No checks yet — they run every 5 minutes.</Empty>}
      <div className="health-grid">
        {(data || []).map((h) => (
          <div className={`sys-card ${h.ok ? "ok" : "bad"}`} key={h.service}>
            <div className="sys-name"><span className="led" />{h.service}<em>{h.ok ? "online" : "fault"}</em></div>
            <div className="muted small">{h.detail}</div>
            <div className="muted tiny">checked {fmtTime(h.checked_at)}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
