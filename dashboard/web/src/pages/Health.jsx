import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

// Alfred's own components, in the order that matters most.
const ALFRED = {
  claude: "Language model (Claude)",
  telegram: "Telegram",
  twilio: "Phone (Twilio)",
  jetson_speech: "Speech · Jetson",
  elevenlabs: "Speech fallback · ElevenLabs",
  elevenlabs_stt: "ElevenLabs recognition",
  elevenlabs_tts: "ElevenLabs voice",
  google: "Google Calendar",
  n8n: "Automations (n8n)",
  backup: "Backups",
  restore_drill: "Restore test (monthly)",
};
const order = (s) => {
  const i = Object.keys(ALFRED).indexOf(s);
  return i < 0 ? 99 : i;
};

function Card({ h }) {
  return (
    <div className={`sys-card ${h.ok ? "ok" : "bad"}`}>
      <div className="sys-name">
        <span className="led" />{h.group === "alfred" ? (ALFRED[h.service] || h.service) : h.label}
        <em>{h.ok ? "online" : "fault"}</em>
      </div>
      {h.role && <div className="sys-role">{h.role}</div>}
      <div className="muted small">{h.group === "homelab" ? h.detail.replace(`${h.label}: `, "") : h.detail}</div>
      <div className="muted tiny">checked {fmtTime(h.checked_at)}</div>
    </div>
  );
}

function Group({ title, note, rows }) {
  const down = rows.filter((r) => !r.ok).length;
  return (
    <section className="sys-group">
      <h3 className="sub">
        {title}
        <span className={`group-state ${down ? "bad" : "ok"}`}>{down ? `${down} of ${rows.length} need attention` : `all ${rows.length} OK`}</span>
      </h3>
      {note && <p className="muted small">{note}</p>}
      <div className="health-grid">{rows.map((h) => <Card h={h} key={h.service} />)}</div>
    </section>
  );
}

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

  const alfred = (data || []).filter((h) => h.group !== "homelab").sort((a, b) => order(a.service) - order(b.service));
  const lab = (data || []).filter((h) => h.group === "homelab");

  return (
    <Panel code="06" title="Systems diagnostics" actions={isAdmin && (
      <button className="btn ghost small" onClick={recheck} disabled={busy}>{busy ? "Scanning…" : "Run diagnostics"}</button>
    )}>
      <ErrorNote error={error || err} />
      {data && data.length === 0 && <Empty>No checks yet — they run every 5 minutes.</Empty>}
      {alfred.length > 0 && <Group title="Alfred" rows={alfred} />}
      {isAdmin && lab.length > 0 && (
        <Group title="Homelab" rows={lab}
               note="Your machines, checked every 5 minutes. Problems are sent to you on Telegram (silently during quiet hours)." />
      )}
    </Panel>
  );
}
