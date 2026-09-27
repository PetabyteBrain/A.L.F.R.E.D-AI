import { useEffect, useRef } from "react";
import { api } from "../api.js";
import { firstName, fmtDur } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";

function Tail({ session }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [session.tail, session.partial]);
  return (
    <div className="tail" ref={ref}>
      {session.tail.length === 0 && <div className="p">waiting…</div>}
      {session.tail.map((t, i) => (
        <div key={i} className={t.speaker === "user" ? "u" : "a"}>
          <b>{t.speaker === "user" ? firstName(session.display_name) : "Alfred"}:</b> {t.text}
        </div>
      ))}
      {session.partial && <div className="p">… {session.partial}</div>}
    </div>
  );
}

export default function Live() {
  const { data, error } = usePoll(() => api("/sessions/active"), 2000);
  return (
    <section className="panel">
      <h2>Active sessions <span className="pulse" aria-hidden="true" /></h2>
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No calls or chats in progress.</Empty>}
      <div className="cards">
        {(data || []).map((s) => (
          <article key={s.id} className={`card ${s.channel}`}>
            <header>
              <span className="who">{s.display_name}</span>
              <span>
                <Tag>{s.channel === "call" ? "📞 call" : "✈ telegram"} · {s.direction}</Tag>{" "}
                {s.speaking ? <Tag kind="busy">speaking</Tag> : s.busy ? <Tag kind="busy">thinking</Tag> : null}
              </span>
            </header>
            <div className="meta small">running {fmtDur(s.duration_s)} · idle {fmtDur(s.idle_s)}</div>
            <Tail session={s} />
          </article>
        ))}
      </div>
    </section>
  );
}
