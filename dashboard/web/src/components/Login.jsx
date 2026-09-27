import { useState } from "react";
import { api } from "../api.js";
import { Reactor } from "./common.jsx";

export default function Login({ onLogin }) {
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
      setError(err.status === 401 ? "Invalid credentials" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="login">
      <form className="login-card" onSubmit={submit}>
        <Reactor />
        <h1>ALFRED</h1>
        <p className="muted">Restricted console</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)}
                 autoComplete="username" maxLength={100} required autoFocus />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="current-password" maxLength={200} required />
        </label>
        <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="error" role="alert">{error}</p>
      </form>
    </section>
  );
}
