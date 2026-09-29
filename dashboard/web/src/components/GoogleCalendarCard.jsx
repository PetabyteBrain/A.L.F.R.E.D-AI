import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

const RETURN_MESSAGES = {
  linked: ["ok", "Google Calendar linked."],
  denied: ["err", "Google access wasn't granted."],
  error: ["err", "Linking failed. Try again, and tick the calendar permission when Google asks."],
};

/* Link / unlink the signed-in person's own Google Calendar. */
export default function GoogleCalendarCard({ assistant }) {
  const { data: st, error, reload } = usePoll(() => api("/google/status"));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  // Coming back from Google: /app?google=linked|denied|error -> show it once, then clean the URL.
  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("google");
    if (!result) return;
    const [kind, text] = RETURN_MESSAGES[result] || RETURN_MESSAGES.error;
    setMsg(kind === "ok" ? { ok: text, err: "" } : { ok: "", err: text });
    window.history.replaceState(null, "", "/app");
  }, []);

  async function connect() {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      const { url } = await api("/google/connect", { method: "POST" });
      if (!url.startsWith("https://accounts.google.com/")) throw new Error("Unexpected sign-in address.");
      window.location.assign(url);
    } catch (e) {
      setMsg({ ok: "", err: e.message });
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api("/google", { method: "DELETE" });
      setMsg({ ok: "Google Calendar unlinked and access revoked.", err: "" });
      reload();
    } catch (e) {
      setMsg({ ok: "", err: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gcal">
      <h3 className="sub">Google Calendar</h3>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      {st && !st.available && (
        <p className="muted small">Calendar linking isn't set up on the server yet (an admin needs to add the Google OAuth keys).</p>
      )}
      {st && st.available && !st.linked && (
        <>
          <p className="muted small">
            Link your calendar so {assistant} can check your schedule and add, move or remove events when you ask.
            {" "}{assistant} only sees <b>your</b> calendar, and only while it's linked.
          </p>
          <button className="btn primary" onClick={connect} disabled={busy}>{busy ? "Opening Google…" : "Link Google Calendar"}</button>
        </>
      )}
      {st && st.linked && (
        <div className="gcal-linked">
          <div className="kv"><span>Linked</span><Tag kind="ok">{st.email || "Google account"}</Tag></div>
          <p className="muted tiny">Since {fmtTime(st.linked_at)} · calendar: {st.calendar_id}</p>
          <button className="btn danger small" onClick={disconnect} disabled={busy}>Unlink</button>
        </div>
      )}
    </div>
  );
}
