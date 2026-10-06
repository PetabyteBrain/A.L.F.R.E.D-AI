import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, apiStream } from "../api.js";
import { ErrorNote } from "../components/common.jsx";
import Markdown from "../components/Markdown.jsx";
import { Panel } from "../components/hud.jsx";
import { Portrait } from "../components/portraits.jsx";

const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");

// What to show while a tool runs mid-reply.
function toolLabel(name) {
  if (name === "web_search") return "Searching the web";
  if (name.startsWith("calendar_")) return "Consulting the calendar";
  if (/call/.test(name)) return "Arranging the call";
  if (name.includes("task")) return "Checking the task list";
  if (name.startsWith("n8n_")) return "Consulting the automation server";
  if (name === "send_telegram_message") return "Sending the message";
  if (name === "list_people") return "Checking the registry";
  return "Looking into it";
}

function Message({ m, assistant }) {
  const mine = m.speaker === "user";
  const waiting = m.streaming && !m.text;
  return (
    <div className={`msg ${mine ? "mine" : "theirs"}${m.failed ? " failed" : ""}${m.streaming ? " streaming" : ""}`}>
      {!mine && (
        <div className="avatar portrait">
          <Portrait decorative state={m.streaming ? (m.tool ? "reading" : m.text ? "explaining" : "thinking") : "neutral"} />
        </div>
      )}
      <div className="bubble">
        <div className="who">{mine ? "You" : assistant}<span>{time(m.ts)}</span></div>
        {mine ? <div className="md"><p>{m.text}</p></div> : !waiting && <Markdown text={m.text} />}
        {m.streaming && m.tool && <div className="tool-note"><span className="led" />{toolLabel(m.tool)}…</div>}
        {waiting && !m.tool && <div className="dots"><i /><i /><i /></div>}
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
  const pinned = useRef(true); // follow new text only while the reader is at the bottom

  useEffect(() => {
    api("/chat/history").then(setMessages).catch((e) => { setMessages([]); setError(e.message); });
  }, []);

  // Keep the newest message in view, unless the reader scrolled up to look at something.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  const onScroll = () => {
    const el = scroller.current;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  };

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
    const id = Symbol("reply");
    const patch = (fn) => setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));
    pinned.current = true;
    setMessages((prev) => [...(prev || []), mine, { id, speaker: "assistant", text: "", streaming: true }]);
    setSending(true);
    let finished = false;
    try {
      await apiStream("/chat/stream", { message }, (ev) => {
        if (ev.type === "delta") patch((m) => ({ text: m.text + ev.text, tool: null }));
        // Text before a tool call was only a preamble ("Let me check…"); the final answer replaces it.
        else if (ev.type === "tool") patch(() => ({ text: "", tool: ev.name }));
        else if (ev.type === "done") { finished = true; patch(() => ({ text: ev.reply, ts: ev.ts, streaming: false, tool: null })); }
        else if (ev.type === "error") throw new Error(ev.detail || "Reply failed");
      });
      if (!finished) throw new Error("Connection lost");
    } catch (e) {
      setMessages((prev) => prev
        .filter((m) => m.id !== id)
        .map((m) => (m === mine ? { ...m, failed: e.message } : m)));
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
      <div className="chat-scroll" ref={scroller} onScroll={onScroll} aria-live="polite" aria-busy={sending}>
        {messages && messages.length === 0 && (
          <div className="chat-empty">
            <Portrait state="greeting" className="chat-hero-img" />
            <h3>How may I be of service?</h3>
            <p className="muted">Ask anything, or try one of these:</p>
            <div className="chips">
              {suggestions.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {(messages || []).map((m, i) => <Message key={i} m={m} assistant={assistant} />)}
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
