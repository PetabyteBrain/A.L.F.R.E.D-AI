import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll, useStoredState } from "../hooks.js";
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

// Profile sections: one at a time, picked from the menu (remembered in the browser).
const SECTIONS = [
  { id: "account", label: "Account", hint: () => "Name, login, password",
    intro: () => "Your name as everyone sees it, your sign-in name and your password." },
  { id: "security", label: "Sign-in & security", hint: () => "Two-factor sign-in",
    intro: () => "A second step when you sign in: a code from an authenticator app or from Telegram." },
  { id: "connections", label: "Connections", hint: () => "Phone, Telegram, email",
    intro: (a) => `The numbers and accounts ${a} knows you by. New ones are confirmed with a code.` },
  { id: "calendar", label: "Calendar", hint: () => "Google Calendar",
    intro: (a) => `Link your own Google Calendar so ${a} can read and arrange it when you ask.` },
  { id: "alfred", label: (a) => `How ${a} serves you`, hint: () => "Quiet hours, briefing, voice, continuity",
    intro: (a) => `When ${a} may disturb you, your morning briefing, voice replies, and whether he carries a conversation from one channel to another.` },
  { id: "memory", label: "Memory", hint: (a) => `What ${a} remembers`,
    intro: (a) => `Short notes ${a} keeps about how you like things; used only in your conversations.` },
  { id: "data", label: "Your data", hint: () => "Download or delete",
    intro: () => "Download everything stored about you, or ask for it to be deleted." },
];

/* Self-service for everyone with dashboard access, as a settings page with sections. */
export default function Profile({ assistant, refreshMe }) {
  const { data: me, error, reload } = usePoll(() => api("/profile"));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  // Back from Google's consent screen (/app?google=...): open the Calendar section where the result shows.
  const [section, setSection] = useStoredState("alfred-profile-section",
    new URLSearchParams(window.location.search).has("google") ? "calendar" : "account");
  useEffect(() => { if (new URLSearchParams(window.location.search).has("google")) setSection("calendar"); }, [setSection]);

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

  const current = SECTIONS.find((x) => x.id === section) || SECTIONS[0];
  const labelOf = (x) => (typeof x.label === "function" ? x.label(assistant) : x.label);
  const card = (children) => <div className="setting-card">{children}</div>;
  const body = {
    account: (
      <>
        {card(
          <>
            <h3 className="sub flush">Identity</h3>
            <form className="field-row" onSubmit={(e) => { e.preventDefault(); run(() => api("/profile", { method: "PATCH", body: { display_name: name } }).then(() => refreshMe?.()), "Name saved."); }}>
              <label className="field"><span>Name</span>
                <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
              </label>
              <button className="btn small" type="submit" disabled={busy || name.trim() === me.display_name}>Save</button>
            </form>
            <div className="kv"><span>{(me.roles || []).length > 1 ? "Roles" : "Role"}</span>
              <span>{(me.roles || [{ name: me.role, label: me.role }]).map((r) => <Tag key={r.name}>{r.label}</Tag>)}</span></div>
            <div className="kv"><span>Login</span><b>{me.dashboard_username}</b></div>
          </>,
        )}
        {card(
          <>
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
          </>,
        )}
      </>
    ),
    security: card(<TwoFactorCard assistant={assistant} />),
    connections: card(
      <>
        <h3 className="sub flush">Phone, Telegram and email</h3>
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
      </>,
    ),
    calendar: card(<GoogleCalendarCard assistant={assistant} />),
    alfred: (
      <>
        {card(<QuietHoursCard assistant={assistant} />)}
        {card(<BriefingCard assistant={assistant} />)}
        {card(<VoiceModeCard assistant={assistant} />)}
        {card(<ContinuityCard assistant={assistant} />)}
      </>
    ),
    memory: card(<MemoryCard assistant={assistant} />),
    data: card(<MyDataCard assistant={assistant} />),
  }[current.id];

  return (
    <Panel code="08" title="My profile">
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      <div className="settings-shell">
        <nav className="settings-nav" aria-label="Profile sections">
          {SECTIONS.map((x) => (
            <button key={x.id} className={x.id === current.id ? "active" : ""} aria-current={x.id === current.id ? "page" : undefined}
                    onClick={() => { setSection(x.id); setMsg({ ok: "", err: "" }); }}>
              <b>{labelOf(x)}</b><span>{x.hint(assistant)}</span>
            </button>
          ))}
        </nav>
        <section className="settings-body" aria-labelledby="settings-title">
          <h2 id="settings-title" className="settings-title">{labelOf(current)}</h2>
          <p className="muted small settings-intro">{current.intro(assistant)}</p>
          <div className="setting-cards">{body}</div>
        </section>
      </div>
    </Panel>
  );
}
