import { useEffect, useRef, useState } from "react";
import { api, audioUrl } from "../api.js";
import { fmtClock, fmtDur, fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

function Tree({ tree, error, filter, setFilter }) {
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
                        onClick={() => setFilter(sel ? { ...filter, user_id: "", date: "" } : { ...filter, user_id: p.user_id, date: d.day, from: "", to: "" })}>
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
      <h3 className="sub flush">{s.screened_caller ? `Unknown caller ${s.screened_caller} (screened for ${s.display_name})` : s.display_name} · {s.channel} · {fmtTime(s.started_at)}</h3>
      {s.recordings.map((r) => r.deleted_at ? (
        <div key={r.id} className="muted small">
          Audio deleted {fmtTime(r.deleted_at)} (kept {s.retention_days} days after a call); the transcript stays.
        </div>
      ) : (
        <div key={r.id}>
          <audio controls preload="none" src={audioUrl(r.id)} />
          <div className="muted small">Stereo: left = {s.screened_caller ? "caller" : s.display_name}, right = {assistant} · {fmtDur(r.duration)}</div>
        </div>
      ))}
      {s.turns.length === 0 && <div className="muted">No transcript.</div>}
      {s.turns.map((t) => (
        <div key={t.turn_index} className={`turn ${t.speaker}`}>
          <div className="sp">{t.speaker === "user" ? (s.screened_caller ? "Caller" : s.display_name) : assistant}<br />{fmtClock(t.ts)}</div>
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

/* "a «word» here" -> text with <mark>ed hits (no HTML injection: plain React text nodes). */
function Snippet({ text }) {
  const parts = text.split(/[«»]/);
  return <div className="snippet">{parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : p))}</div>;
}

const EMPTY = { q: "", user_id: "", channel: "", date: "", from: "", to: "" };

function Filters({ filter, setFilter, people }) {
  const [text, setText] = useState(filter.q);
  useEffect(() => {                                  // search as you type, after a short pause
    const t = setTimeout(() => { if (text !== filter.q) setFilter({ ...filter, q: text }); }, 350);
    return () => clearTimeout(t);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setFilter({ ...filter, [k]: e.target.value, ...(k === "from" || k === "to" ? { date: "" } : {}) });
  const active = Object.values(filter).some(Boolean);
  return (
    <form className="archive-filters" role="search" onSubmit={(e) => { e.preventDefault(); setFilter({ ...filter, q: text }); }}>
      <input type="search" placeholder="Search what was said…" aria-label="Search transcripts" value={text}
             maxLength={200} onChange={(e) => setText(e.target.value)} />
      {people.length > 1 && (
        <select aria-label="Person" value={filter.user_id} onChange={set("user_id")}>
          <option value="">everyone</option>
          {people.map((p) => <option key={p.user_id} value={p.user_id}>{p.display_name}</option>)}
        </select>
      )}
      <select aria-label="Channel" value={filter.channel} onChange={set("channel")}>
        <option value="">all channels</option>
        <option value="call">phone</option>
        <option value="telegram">Telegram</option>
        <option value="web">web chat</option>
        <option value="email">email</option>
      </select>
      <label className="date-range"><span>from</span><input type="date" value={filter.from} onChange={set("from")} /></label>
      <label className="date-range"><span>to</span><input type="date" value={filter.to} onChange={set("to")} /></label>
      {active && <button className="btn small" type="button" onClick={() => { setText(""); setFilter(EMPTY); }}>Clear</button>}
    </form>
  );
}

export default function Recordings({ assistant }) {
  const [filter, setFilterRaw] = useState(EMPTY);
  const [selected, setSelected] = useState(null);
  const { data: tree, error: treeError } = usePoll(() => api("/recordings/tree"));
  const setFilter = (f) => { setFilterRaw(f); setSelected(null); };
  const q = new URLSearchParams();
  if (filter.q.trim()) q.set("q", filter.q.trim());
  if (filter.user_id) q.set("user_id", String(filter.user_id));
  if (filter.channel) q.set("channel", filter.channel);
  if (filter.date) q.set("date", filter.date);
  if (filter.from) q.set("date_from", filter.from);
  if (filter.to) q.set("date_to", filter.to);
  const query = q.toString();
  const { data: rows, error } = usePoll(() => api(`/sessions?${query}`), 0, [query]);
  const title = filter.q.trim() ? `Matches for “${filter.q.trim()}”` : filter.date ? `Sessions · ${filter.date}` : query ? "Filtered sessions" : "Recent sessions";

  return (
    <Panel code="03" title="Archive">
      <Filters filter={filter} setFilter={setFilter} people={tree || []} />
      <div className="split">
        <Tree tree={tree} error={treeError} filter={filter} setFilter={setFilter} />
        <div>
          <h3 className="sub">{title}</h3>
          <ErrorNote error={error} />
          {rows && rows.length === 0 && <Empty>{filter.q.trim() ? "Nothing found. Try other or fewer words." : "No sessions."}</Empty>}
          <div className="list">
            {(rows || []).map((s) => (
              <button key={s.id} className={`row click${selected === s.id ? " sel" : ""}`}
                      onClick={() => setSelected(s.id)}>
                <div>
                  <div>
                    {s.screened_caller ? `Caller ${s.screened_caller}` : s.display_name} <Tag>{s.channel}</Tag>{" "}
                    {s.screened_caller && <Tag kind="busy">screened</Tag>}{" "}
                    {s.recording_id ? <Tag kind="ok">🎙 {fmtDur(s.duration)}</Tag> : s.audio_deleted_at ? <Tag>audio expired</Tag> : null}
                  </div>
                  <div className="meta">{fmtTime(s.started_at)} · {s.turns} turns · {s.status}</div>
                  {s.match && <Snippet text={s.match} />}
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
