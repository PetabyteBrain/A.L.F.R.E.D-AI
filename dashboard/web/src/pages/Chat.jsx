import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { ErrorNote } from "../components/common.jsx";
import Markdown from "../components/Markdown.jsx";
import { Emblem, Panel } from "../components/hud.jsx";

const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");

function Message({ m, assistant }) {
  const mine = m.speaker === "user";
  return (
    <div className={`msg ${mine ? "mine" : "theirs"}${m.failed ? " failed" : ""}`}>
      {!mine && <div className="avatar"><Emblem size="sm" /></div>}
      <div className="bubble">
        <div className="who">{mine ? "You" : assistant}<span>{time(m.ts)}</span></div>
        {mine ? <div className="md"><p>{m.text}</p></div> : <Markdown text={m.text} />}
        {m.failed && <div className="fail-note">Not delivered · {m.failed}</div>}
      </div>
    </div>
  );
}

export default function Chat({ me, assistant }) {
  const [messages, setMessages] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const scroller = useRef(null);
  const input = useRef(null);

  useEffect(() => {
    api("/chat/history").then(setMessages).catch((e) => { setMessages([]); setError(e.message); });
  }, []);

  // Keep the newest message in view.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  // Auto-grow the composer up to a limit.
  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [draft]);

  async function send(text) {
    const message = text.trim();
    if (!message || sending) return;
    setError("");
    setDraft("");
    const mine = { speaker: "user", text: message, ts: new Date().toISOString() };
    setMessages((prev) => [...(prev || []), mine]);
    setSending(true);
    try {
      const r = await api("/chat", { method: "POST", body: { message } });
      setMessages((prev) => [...prev, { speaker: "assistant", text: r.reply, ts: r.ts }]);
    } catch (e) {
      setMessages((prev) => prev.map((m) => (m === mine ? { ...m, failed: e.message } : m)));
      setDraft(message); // give the text back so it can be resent
    } finally {
      setSending(false);
      input.current?.focus();
    }
  }

  async function newConversation() {
    setError("");
    try {
      await api("/chat/reset", { method: "POST" });
      setMessages([]);
      input.current?.focus();
    } catch (e) {
      setError(e.message);
    }
  }

  const suggestions = [
    "What can you do for me?",
    "What's on my calendar tomorrow?",
    "What's on the task list?",
    ...(me.can_request_calls ? ["Call Max tomorrow at 9 and ask if the invoice is ready"] : []),
  ];

  return (
    <Panel code="10" title={`Chat with ${assistant}`} className="chat" actions={
      <button className="btn ghost small" onClick={newConversation} disabled={sending || !messages?.length}>New conversation</button>
    }>
      <div className="chat-scroll" ref={scroller} aria-live="polite">
        {messages && messages.length === 0 && (
          <div className="chat-empty">
            <Emblem size="md" />
            <h3>How may I be of service?</h3>
            <p className="muted">Ask anything, or try one of these:</p>
            <div className="chips">
              {suggestions.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {(messages || []).map((m, i) => <Message key={i} m={m} assistant={assistant} />)}
        {sending && (
          <div className="msg theirs thinking">
            <div className="avatar"><Emblem size="sm" /></div>
            <div className="bubble"><div className="who">{assistant}</div><div className="dots"><i /><i /><i /></div></div>
          </div>
        )}
      </div>
      <ErrorNote error={error} />
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <textarea ref={input} value={draft} rows={1} maxLength={4000} placeholder={`Message ${assistant}…`}
                  aria-label={`Message ${assistant}`} autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft); } }} />
        <button className="btn primary" type="submit" disabled={sending || !draft.trim()} aria-label="Send">
          {sending ? "…" : "Send"}
        </button>
      </form>
      <p className="muted tiny composer-hint">Enter to send · Shift+Enter for a new line · same assistant as on the phone and Telegram</p>
    </Panel>
  );
}
