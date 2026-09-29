import { useState } from "react";
import { api } from "../api.js";
import { DecodeText, Emblem } from "./hud.jsx";
import { THEMES, useThemeName } from "../theme.js";

export default function Login({ onLogin, themeSwitch }) {
  const theme = useThemeName();
  const skin = THEMES[theme];
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api("/login", { method: "POST", body: { username, password } });
      setPassword("");
      onLogin(me);
    } catch (err) {
      setError(err.status === 401 ? "Access denied" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="login">
      <header className="login-top">
        <a className="back" href="/">← Home</a>
        {themeSwitch}
      </header>

      <div className="login-grid">
        <div className="login-visual" aria-hidden="true">
          <Emblem size="xl" />
          <div className="readout r1"><span>ACCESS</span><b>RESTRICTED</b></div>
          <div className="readout r2"><span>CHANNEL</span><b>ENCRYPTED</b></div>
          <div className="readout r3"><span>IDENTITY</span><b>REQUIRED</b></div>
        </div>

        <div className="login-side">
          <p className="kicker"><span className="blip" />Secure console · {skin.label}</p>
          <DecodeText as="h1" className="login-mark" text={skin.wordmark} duration={900} />
          <p className="subtitle">{skin.subtitle}</p>
          <ul className="bootlog" aria-hidden="true">
            <li><span>secure channel</span><b>ready</b></li>
            <li><span>identity service</span><b>ready</b></li>
            <li><span>awaiting operator</span><b className="blink">_</b></li>
          </ul>

          <form className="hud login-card" onSubmit={submit}>
            <label>
              <span>Identity</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)}
                     autoComplete="username" maxLength={100} required autoFocus spellCheck={false} />
            </label>
            <label>
              <span>Passphrase</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                     autoComplete="current-password" maxLength={200} required />
            </label>
            <button className="btn primary" type="submit" disabled={busy}>{busy ? "Verifying…" : "Authenticate"}</button>
            <p className="error" role="alert">{error}</p>
          </form>

          <nav className="legal-links" aria-label="Legal">
            <a href="/privacy-policy">Privacy Policy</a>
            <a href="/terms-of-service">Terms of Service</a>
          </nav>
        </div>
      </div>
    </section>
  );
}
