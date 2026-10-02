import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

const DURATIONS = [
  { label: "for 1 hour", minutes: 60 },
  { label: "for 3 hours", minutes: 180 },
  { label: "for 8 hours", minutes: 480 },
  { label: "until I switch it off", minutes: null },
];

/* Voice mode: Telegram replies and messages also arrive as voice notes. */
export default function VoiceModeCard({ assistant }) {
  const { data: v, error, reload } = usePoll(() => api("/voice-mode"), 60000);
  const [minutes, setMinutes] = useState("60");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!v) return <div className="voice-mode"><h3 className="sub">Voice replies</h3><ErrorNote error={error} /></div>;

  async function save(on) {
    setBusy(true);
    setErr("");
    try {
      await api("/voice-mode", { method: "PUT", body: { on, minutes: on && minutes ? Number(minutes) : null } });
      await reload();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="voice-mode">
      <h3 className="sub">
        Voice replies {v.on ? <Tag kind="ok">{v.until_local ? `on until ${v.until_local}` : "on"}</Tag> : <Tag>off</Tag>}
      </h3>
      <p className="muted small">
        While on, {assistant}'s Telegram replies, reminders, briefing and reports also arrive as voice notes,
        for when you can't read. You can also just ask: "answer by voice" or "voice replies for an hour".
      </p>
      <ErrorNote error={err || error} />
      {!v.telegram_linked ? (
        <p className="muted small">Add and verify a Telegram connection first.</p>
      ) : v.on ? (
        <button className="btn small" disabled={busy} onClick={() => save(false)}>Back to text</button>
      ) : (
        <div className="field-row">
          <label className="field"><span>Switch on</span>
            <select value={minutes} onChange={(e) => setMinutes(e.target.value)}>
              {DURATIONS.map((d) => <option key={d.label} value={d.minutes ?? ""}>{d.label}</option>)}
            </select>
          </label>
          <button className="btn primary small" disabled={busy} onClick={() => save(true)}>Switch on</button>
        </div>
      )}
    </div>
  );
}
