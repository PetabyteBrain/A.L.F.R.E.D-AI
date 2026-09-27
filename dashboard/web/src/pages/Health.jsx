import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";

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
    <section className="panel">
      <h2>
        System health{" "}
        {isAdmin && (
          <button className="ghost small" onClick={recheck} disabled={busy}>{busy ? "Checking…" : "Re-check now"}</button>
        )}
      </h2>
      <ErrorNote error={error || err} />
      {data && data.length === 0 && <Empty>No checks yet — they run every 5 minutes.</Empty>}
      <div className="health-grid">
        {(data || []).map((h) => (
          <div className="card" key={h.service}>
            <div><span className={`dot ${h.ok ? "ok" : "bad"}`} /><b>{h.service}</b></div>
            <div className="muted small">{h.detail}</div>
            <div className="muted tiny">checked {fmtTime(h.checked_at)}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
