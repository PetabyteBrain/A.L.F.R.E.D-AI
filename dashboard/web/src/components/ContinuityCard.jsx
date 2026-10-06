import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Cross-channel continuity: Alfred sees your last few turns from your other channels. */
export default function ContinuityCard({ assistant }) {
  const { data: c, error, reload } = usePoll(() => api("/continuity"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!c) return <div className="continuity"><h3 className="sub">Continue across channels</h3><ErrorNote error={error} /></div>;

  async function flip(on) {
    setBusy(true); setErr("");
    try { await api("/continuity", { method: "PUT", body: { on } }); await reload(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="continuity">
      <h3 className="sub">Continue across channels {c.on ? <Tag kind="ok">on</Tag> : <Tag>off</Tag>}</h3>
      <p className="muted small">
        When on, {assistant} sees your last few messages from your other channels (up to {c.hours} hours back), so a call
        can pick up where a Telegram chat left off and the dashboard chat knows what you just said on the phone. Only
        your own conversations; nothing is shared with anyone else.
      </p>
      <ErrorNote error={err} />
      <label className="toggle">
        <input type="checkbox" checked={c.on} disabled={busy} onChange={(e) => flip(e.target.checked)} />
        <span>{c.on ? "On" : "Off"}</span>
      </label>
    </div>
  );
}
