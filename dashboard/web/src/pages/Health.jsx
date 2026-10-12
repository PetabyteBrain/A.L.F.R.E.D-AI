import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { can, Empty, ErrorNote } from "../components/common.jsx";
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
  backup: "Backups · Jetson",
  backup_nas: "Backups · NAS",
  restore_drill: "Restore test (monthly)",
  nas: "NAS (files, notes, lists)",
  searxng: "Self-hosted search (SearXNG)",
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

function HostUpdates({ h }) {
  const [open, setOpen] = useState(false);
  if (h.error) return <li><b>{h.host}</b> <span className="muted">couldn't check ({h.error})</span></li>;
  const pk = h.packages;
  const newer = (h.images || []).filter((i) => i.state === "newer upstream");
  const quiet = !pk?.count && !newer.length && !h.reboot_required;
  return (
    <li>
      <b>{h.host}</b>{" "}
      {quiet ? <span className="muted">up to date</span> : (
        <>
          {pk?.count > 0 && <span>{pk.count} packages{pk.security ? ` (${pk.security} security)` : ""}</span>}
          {newer.length > 0 && <span>{pk?.count ? " · " : ""}newer images: {newer.map((i) => i.container).join(", ")}</span>}
          {h.reboot_required && <span className="warn-text"> · reboot needed</span>}
          {pk?.count > 0 && (
            <button className="btn ghost small upd-more" onClick={() => setOpen(!open)}>{open ? "hide" : "packages"}</button>
          )}
        </>
      )}
      <div className="muted tiny">{[h.os, h.kernel && `kernel ${h.kernel}`, h.docker && `Docker ${h.docker}`,
        pk?.lists_age_d != null && `package lists ${pk.lists_age_d} d old`].filter(Boolean).join(" · ")}</div>
      {open && <div className="muted tiny upd-names">{pk.names.join(", ")}{pk.count > pk.names.length ? " …" : ""}</div>}
    </li>
  );
}

function Updates() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [shown, setShown] = useState(false);

  async function load() {
    setErr("");
    try { setData(await api("/updates")); } catch (e) { setErr(e.message); }
  }

  async function check() {
    setBusy(true);
    setErr("");
    try { setData(await api("/updates/check", { method: "POST" })); setShown(true); } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <section className="sys-group">
      <h3 className="sub">
        Updates
        <span className="group-actions">
          {!shown && <button className="btn ghost small" onClick={() => { setShown(true); load(); }}>Show report</button>}
          <button className="btn ghost small" onClick={check} disabled={busy}>{busy ? "Checking hosts…" : "Check now"}</button>
        </span>
      </h3>
      <p className="muted small">
        Checked on the 1st of every month and sent to you on Telegram. Report only: Alfred never installs anything.
      </p>
      <ErrorNote error={err} />
      {shown && data && !data.at && <Empty>No check yet.</Empty>}
      {shown && data?.at && (
        <>
          <p className="small">{data.summary} <span className="muted tiny">· {fmtTime(data.at)}</span></p>
          <ul className="upd-list">{data.hosts.map((h) => <HostUpdates h={h} key={h.host} />)}</ul>
        </>
      )}
    </section>
  );
}

export default function Health({ me }) {
  const isAdmin = can(me, "homelab.manage");
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
      {isAdmin && lab.length > 0 && <Updates />}
    </Panel>
  );
}
