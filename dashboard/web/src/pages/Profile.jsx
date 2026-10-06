import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "../components/common.jsx";
import Connections from "../components/Connections.jsx";
import BriefingCard from "../components/BriefingCard.jsx";
import MemoryCard from "../components/MemoryCard.jsx";
import QuietHoursCard from "../components/QuietHoursCard.jsx";
import GoogleCalendarCard from "../components/GoogleCalendarCard.jsx";
import TwoFactorCard from "../components/TwoFactorCard.jsx";
import VoiceModeCard from "../components/VoiceModeCard.jsx";
import ContinuityCard from "../components/ContinuityCard.jsx";
import MyDataCard from "../components/MyDataCard.jsx";
import { Panel } from "../components/hud.jsx";

/* Self-service for everyone with dashboard access: name, connections, password. */
export default function Profile({ assistant, refreshMe }) {
  const { data: me, error, reload } = usePoll(() => api("/profile"));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });

  useEffect(() => { if (me) setName(me.display_name); }, [me?.display_name]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn, okText) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      await fn();
      setMsg({ ok: okText, err: "" });
      await reload();
      return true;
    } catch (e) {
      setMsg({ ok: "", err: e.message });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(e) {
    e.preventDefault();
    if (pw.next !== pw.confirm) { setMsg({ ok: "", err: "The new passwords don't match." }); return; }
    const ok = await run(() => api("/profile/password", { method: "POST",
      body: { current_password: pw.current, new_password: pw.next } }), "Password changed. Other devices were signed out.");
    if (ok) setPw({ current: "", next: "", confirm: "" });
  }

  if (!me) return <Panel code="08" title="My profile"><ErrorNote error={error} /></Panel>;

  return (
    <Panel code="08" title="My profile">
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      <div className="profile-grid">
        <section>
          <h3 className="sub flush">Identity</h3>
          <form className="field-row" onSubmit={(e) => { e.preventDefault(); run(() => api("/profile", { method: "PATCH", body: { display_name: name } }).then(() => refreshMe?.()), "Name saved."); }}>
            <label className="field"><span>Name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
            </label>
            <button className="btn small" type="submit" disabled={busy || name.trim() === me.display_name}>Save</button>
          </form>
          <div className="kv"><span>Role</span><Tag>{me.role}</Tag></div>
          <div className="kv"><span>Login</span><b>{me.dashboard_username}</b></div>

          <h3 className="sub">Connections</h3>
          <p className="muted small">How {assistant} recognises you on the phone and on Telegram.</p>
          <Connections
            connections={me.connections}
            busy={busy}
            base="/profile/connections"
            onChanged={reload}
            onAdd={(type, value) => run(() => api("/profile/connections", { method: "POST", body: { type, value } }), "Connection added.")}
            onUpdate={(id, value) => run(() => api(`/profile/connections/${Number(id)}`, { method: "PATCH", body: { value } }), "Connection updated.")}
            onRemove={(c) => run(() => api(`/profile/connections/${Number(c.id)}`, { method: "DELETE" }), "Connection removed.")}
          />
        </section>

        <section>
          <h3 className="sub flush">Change password</h3>
          <form className="stack" onSubmit={changePassword}>
            <label className="field"><span>Current password</span>
              <input type="password" autoComplete="current-password" value={pw.current}
                     onChange={(e) => setPw({ ...pw, current: e.target.value })} required maxLength={200} />
            </label>
            <label className="field"><span>New password (min. 10 characters)</span>
              <input type="password" autoComplete="new-password" value={pw.next} minLength={10}
                     onChange={(e) => setPw({ ...pw, next: e.target.value })} required maxLength={200} />
            </label>
            <label className="field"><span>Repeat new password</span>
              <input type="password" autoComplete="new-password" value={pw.confirm} minLength={10}
                     onChange={(e) => setPw({ ...pw, confirm: e.target.value })} required maxLength={200} />
            </label>
            <button className="btn primary" type="submit" disabled={busy}>Update password</button>
          </form>
          <TwoFactorCard assistant={assistant} />
          <GoogleCalendarCard assistant={assistant} />
          <QuietHoursCard assistant={assistant} />
          <VoiceModeCard assistant={assistant} />
          <ContinuityCard assistant={assistant} />
          <BriefingCard assistant={assistant} />
          <MemoryCard assistant={assistant} />
          <MyDataCard assistant={assistant} />
        </section>
      </div>
    </Panel>
  );
}
