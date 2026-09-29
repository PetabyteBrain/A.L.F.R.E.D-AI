import { useState } from "react";
import { api } from "../api.js";
import { Emblem } from "./hud.jsx";
import { THEMES, useThemeName } from "../theme.js";

export default function Login({ onLogin }) {
  const skin = THEMES[useThemeName()];
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
      <Emblem size="xl" />
      <form className="hud login-card" onSubmit={submit}>
        <h1 className="wordmark">{skin.wordmark}</h1>
        <p className="subtitle">{skin.subtitle} · Authorization required</p>
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
    </section>
  );
}
