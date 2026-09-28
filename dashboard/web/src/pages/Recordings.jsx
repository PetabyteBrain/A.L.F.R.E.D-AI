import { useEffect, useRef, useState } from "react";
import { api, audioUrl } from "../api.js";
import { fmtClock, fmtDur, fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

function Tree({ filter, setFilter }) {
  const { data: tree, error } = usePoll(() => api("/recordings/tree"));
  return (
    <aside>
      <h3 className="sub">Index · person / date</h3>
      <ErrorNote error={error} />
      {tree && tree.length === 0 && <Empty>Nothing recorded yet.</Empty>}
      <div className="tree">
        {(tree || []).map((p) => (
          <div className="person" key={p.user_id}>
            <div>{p.display_name}</div>
            {p.days.map((d) => {
              const sel = filter.user_id === p.user_id && filter.date === d.day;
              return (
                <button key={d.day} className={`day${sel ? " sel" : ""}`}
                        onClick={() => setFilter(sel ? {} : { user_id: p.user_id, date: d.day })}>
                  <span>{d.day}</span>
                  <span>{d.sessions}{d.calls ? ` · ${d.calls}📞` : ""}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </aside>
  );
}

function Detail({ id, assistant }) {
  const { data: s, error } = usePoll(() => api(`/sessions/${encodeURIComponent(id)}`), 0, [id]);
  const ref = useRef(null);
  useEffect(() => {
    if (s) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [s]);
  if (error) return <ErrorNote error={error} />;
  if (!s || s.id !== id) return null;
  return (
    <div className="hud detail" ref={ref}>
      <h3 className="sub flush">{s.display_name} · {s.channel} · {fmtTime(s.started_at)}</h3>
      {s.recordings.map((r) => (
        <div key={r.id}>
          <audio controls preload="none" src={audioUrl(r.id)} />
          <div className="muted small">Stereo: left = {s.display_name}, right = {assistant} · {fmtDur(r.duration)}</div>
        </div>
      ))}
      {s.turns.length === 0 && <div className="muted">No transcript.</div>}
      {s.turns.map((t) => (
        <div key={t.turn_index} className={`turn ${t.speaker}`}>
          <div className="sp">{t.speaker === "user" ? s.display_name : assistant}<br />{fmtClock(t.ts)}</div>
          <div className="tx">{t.text}</div>
        </div>
      ))}
      {s.tool_calls.length > 0 && <h3 className="sub">Tool calls</h3>}
      {s.tool_calls.map((t, i) => (
        <div key={i} className="tool">
          {t.is_error ? "✖" : "✔"} {t.tool_name} {t.payload}{"\n"}→ {(t.result || "").slice(0, 1500)}
        </div>
      ))}
    </div>
  );
}

export default function Recordings({ assistant }) {
  const [filter, setFilter] = useState({});
  const [selected, setSelected] = useState(null);
  const q = new URLSearchParams();
  if (filter.user_id) q.set("user_id", String(filter.user_id));
  if (filter.date) q.set("date", filter.date);
  const query = q.toString();
  const { data: rows, error } = usePoll(() => api(`/sessions?${query}`), 0, [query]);

  return (
    <Panel code="03" title="Archive">
      <div className="split">
        <Tree filter={filter} setFilter={(f) => { setFilter(f); setSelected(null); }} />
        <div>
          <h3 className="sub">{filter.date ? `Sessions · ${filter.date}` : "Recent sessions"}</h3>
          <ErrorNote error={error} />
          {rows && rows.length === 0 && <Empty>No sessions.</Empty>}
          <div className="list">
            {(rows || []).map((s) => (
              <button key={s.id} className={`row click${selected === s.id ? " sel" : ""}`}
                      onClick={() => setSelected(s.id)}>
                <div>
                  <div>
                    {s.display_name} <Tag>{s.channel}</Tag>{" "}
                    {s.recording_id ? <Tag kind="ok">🎙 {fmtDur(s.duration)}</Tag> : null}
                  </div>
                  <div className="meta">{fmtTime(s.started_at)} · {s.turns} turns · {s.status}</div>
                </div>
                <span className="muted">›</span>
              </button>
            ))}
          </div>
        </div>
      </div>
      {selected && <Detail id={selected} assistant={assistant} />}
    </Panel>
  );
}
