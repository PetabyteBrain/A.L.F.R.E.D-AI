import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Two-factor sign-in: authenticator app (preferred), Telegram codes, backup codes. */
export default function TwoFactorCard({ assistant, onEnabled }) {
  const { data: st, error, reload } = usePoll(() => api("/profile/2fa"), 60000);
  const [setup, setSetup] = useState(null);       // {secret, uri, qr} while adding the app
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [ask, setAsk] = useState(null);           // action waiting for the password
  const [codes, setCodes] = useState(null);       // freshly generated backup codes (shown once)
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  async function run(fn, ok) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      const res = await fn();
      if (res?.backup_codes) setCodes(res.backup_codes);
      setMsg({ ok, err: "" });
      await reload();
      // From the set-up gate: open the dashboard only once the backup codes were seen.
      if (res?.enabled && !res.backup_codes) onEnabled?.();
      return true;
    } catch (e) {
      setMsg({ ok: "", err: e.message });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!st) return <div className="twofa"><h3 className="sub">Two-factor sign-in</h3><ErrorNote error={error} /></div>;

  const withPassword = (action) => { setAsk(action); setPassword(""); setMsg({ ok: "", err: "" }); };
  async function submitPassword(e) {
    e.preventDefault();
    const actions = {
      "app-off": () => api("/profile/2fa/totp/disable", { method: "POST", body: { password } }),
      "tg-off": () => api("/profile/2fa/telegram", { method: "POST", body: { enabled: false, password } }),
      codes: () => api("/profile/2fa/backup-codes", { method: "POST", body: { password } }),
    };
    const labels = { "app-off": "Authenticator app removed.", "tg-off": "Telegram codes switched off.", codes: "New backup codes created; the old ones no longer work." };
    if (await run(actions[ask], labels[ask])) setAsk(null);
    setPassword("");
  }

  return (
    <div className="twofa">
      <h3 className="sub">
        Two-factor sign-in {st.enabled ? <Tag kind="ok">on</Tag> : st.required ? <Tag kind="bad">required</Tag> : <Tag>off</Tag>}
      </h3>
      <p className="muted small">
        After your password, the dashboard asks for a code from an authenticator app (Google Authenticator,
        Microsoft Authenticator, 1Password, Aegis…) or one {assistant} sends you on Telegram.
        {st.required && !st.enabled && " Your role needs this before the dashboard opens."}
      </p>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />

      {codes && (
        <div className="backup-codes">
          <p className="small"><b>Backup codes.</b> Each works once if you lose your phone. Save them somewhere safe now; they won't be shown again.</p>
          <ul>{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
          <div className="form-actions">
            <button className="btn small" type="button" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy</button>
            <button className="btn primary small" type="button" onClick={() => { setCodes(null); if (st.enabled) onEnabled?.(); }}>I've saved them</button>
          </div>
        </div>
      )}

      <div className="kv">
        <span>Authenticator app</span>
        {st.totp ? (
          <span><Tag kind="ok">active</Tag> <button className="btn danger small" disabled={busy} onClick={() => withPassword("app-off")}>Remove</button></span>
        ) : !setup ? (
          <button className="btn primary small" disabled={busy}
                  onClick={() => run(() => api("/profile/2fa/totp/start", { method: "POST" }).then((s) => { setSetup(s); setCode(""); }), "")}>
            Set up
          </button>
        ) : null}
      </div>

      {setup && !st.totp && (
        <form className="totp-setup" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api("/profile/2fa/totp/confirm", { method: "POST", body: { code } }), "Authenticator app active.")) setSetup(null);
        }}>
          <p className="small">1. Scan this with the app, or enter the key by hand.</p>
          {setup.qr && <img className="qr" src={setup.qr} alt="QR code for the authenticator app" width="200" height="200" />}
          <p className="small">Key: <code className="secret">{setup.secret.match(/.{1,4}/g).join(" ")}</code></p>
          <label className="field"><span>2. Enter the 6-digit code it shows</span>
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
                   pattern="[0-9 ]{6,7}" maxLength={7} required autoFocus />
          </label>
          <div className="form-actions">
            <button className="btn primary small" type="submit" disabled={busy}>Confirm</button>
            <button className="btn small" type="button" disabled={busy} onClick={() => setSetup(null)}>Cancel</button>
          </div>
        </form>
      )}

      <div className="kv">
        <span>Code on Telegram</span>
        {st.telegram ? (
          <span><Tag kind="ok">active</Tag> <button className="btn danger small" disabled={busy} onClick={() => withPassword("tg-off")}>Switch off</button></span>
        ) : st.telegram_available ? (
          <button className="btn small" disabled={busy}
                  onClick={() => run(() => api("/profile/2fa/telegram", { method: "POST", body: { enabled: true } }), "Telegram codes switched on.")}>
            Switch on
          </button>
        ) : <span className="muted small">add and verify a Telegram connection first</span>}
      </div>

      {st.enabled && (
        <div className="kv">
          <span>Backup codes</span>
          <span>{st.backup_codes_left} left <button className="btn small" disabled={busy} onClick={() => withPassword("codes")}>New codes</button></span>
        </div>
      )}

      {ask && (
        <form className="field-row" onSubmit={submitPassword}>
          <label className="field"><span>Confirm with your password</span>
            <input type="password" autoComplete="current-password" value={password} maxLength={200} required autoFocus
                   onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button className="btn primary small" type="submit" disabled={busy}>Confirm</button>
          <button className="btn small" type="button" disabled={busy} onClick={() => setAsk(null)}>Cancel</button>
        </form>
      )}
    </div>
  );
}
