import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { firstName, fmtDur } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { ConfirmDialog, Panel, Waveform } from "../components/hud.jsx";

function Tail({ session, assistant }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [session.tail, session.partial]);
  return (
    <div className="tail" ref={ref}>
      {session.tail.length === 0 && <div className="p">Awaiting input…</div>}
      {session.tail.map((t, i) => (
        <div key={i} className={t.speaker === "user" ? "u" : "a"}>
          <b>{t.speaker === "user" ? firstName(session.display_name) : assistant}</b>{t.text}
        </div>
      ))}
      {session.partial && <div className="p">› {session.partial}</div>}
    </div>
  );
}

function SessionCard({ s, isAdmin, onEnd, assistant }) {
  const isCall = s.channel === "call";
  const state = s.speaking ? "speaking" : s.busy ? "processing" : isCall ? "listening" : "idle";
  return (
    <article className={`hud live-card ${s.channel} state-${state}`}>
      <header className="live-head">
        <div>
          <div className="live-name">{s.display_name}</div>
          <div className="live-meta">
            <Tag>{isCall ? "voice" : "telegram"} · {s.direction}</Tag>
            <span className="mono">T+{fmtDur(s.duration_s)}</span>
            <span className="mono dim">idle {fmtDur(s.idle_s)}</span>
          </div>
        </div>
        <div className={`state-chip ${state}`}>{state}</div>
      </header>
      {isCall && <Waveform active={s.speaking || Boolean(s.partial)} />}
      <Tail session={s} assistant={assistant} />
      {isAdmin && (
        <footer className="live-foot">
          <button className="btn danger small" onClick={() => onEnd(s)}>
            {isCall ? "Terminate call" : "Close session"}
          </button>
        </footer>
      )}
    </article>
  );
}

export default function Live({ isAdmin, assistant }) {
  const { data, error, reload } = usePoll(() => api("/sessions/active"), 2000);
  const [target, setTarget] = useState(null);
  const [busy, setBusy] = useState(false);
  const [endError, setEndError] = useState("");
  const [notice, setNotice] = useState("");

  async function confirmEnd() {
    setBusy(true);
    setEndError("");
    try {
      const r = await api(`/sessions/${encodeURIComponent(target.id)}/end`, { method: "POST" });
      setNotice(target.channel === "call"
        ? `Call with ${target.display_name} terminated (carrier: ${r.twilio}).`
        : `Session with ${target.display_name} closed.`);
      setTarget(null);
      reload();
    } catch (e) {
      setEndError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel code="02" title="Active sessions" actions={<span className="pulse" aria-hidden="true" />}>
      <ErrorNote error={error} />
      {notice && <div className="notice" role="status" onClick={() => setNotice("")}>{notice}</div>}
      {data && data.length === 0 && <Empty>No active channels. Standing by.</Empty>}
      <div className="cards">
        {(data || []).map((s) => (
          <SessionCard key={s.id} s={s} isAdmin={isAdmin} assistant={assistant} onEnd={(x) => { setEndError(""); setTarget(x); }} />
        ))}
      </div>
      {target && (
        <ConfirmDialog
          title={target.channel === "call" ? "Terminate call" : "Close session"}
          body={target.channel === "call"
            ? <>Hang up the call with <b>{target.display_name}</b> now? The line is dropped via the carrier; the recording and transcript so far are kept.</>
            : <>Close the Telegram session with <b>{target.display_name}</b>? Their conversation history is kept.</>}
          confirmLabel={target.channel === "call" ? "Terminate" : "Close"}
          busy={busy}
          error={endError}
          onConfirm={confirmEnd}
          onCancel={() => setTarget(null)}
        />
      )}
    </Panel>
  );
}
