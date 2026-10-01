import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "./common.jsx";

const KIND = { proposed: "busy", booking: "busy", booked: "ok", no_match: "bad", cancelled: "", expired: "" };
const LABEL = { proposed: "waiting for answers", no_match: "no common time" };

/* Meeting proposals I organised or was invited to: who answered what, and the outcome. */
export default function Meetings({ me, assistant }) {
  const { data: rows, error, reload } = usePoll(() => api("/meetings?include_done=true"), 20000);
  const [err, setErr] = useState("");

  async function withdraw(id) {
    setErr("");
    try { await api(`/meetings/${Number(id)}`, { method: "DELETE" }); reload(); } catch (e) { setErr(e.message); }
  }

  return (
    <>
      <h3 className="sub">Meetings</h3>
      <ErrorNote error={error || err} />
      {rows && rows.length === 0 && <Empty>No meeting requests. Ask {assistant} "find an hour with Max next week".</Empty>}
      <div className="list">
        {(rows || []).slice(0, 12).map((m) => (
          <div className="row" key={m.meeting_id}>
            <div>
              <div>📅 {m.title} <Tag kind={KIND[m.status] ?? ""}>{LABEL[m.status] || m.status}</Tag></div>
              <div className="meta">
                {m.organizer_id === me.id ? "you" : m.organizer} · {m.duration_minutes} min
                {m.booked ? <> · <span className="mono">{m.booked}</span></> : ` · options: ${m.slots.map((s) => s.replace(/^\d+\. /, "")).join(", ")}`}
              </div>
              <div className="meta">
                {m.answers.map((a) => `${a.name}: ${a.status === "answered" ? (a.works.length ? `#${a.works.join(", #")}` : "none") : "…"}`).join(" · ")}
              </div>
            </div>
            {["proposed", "no_match"].includes(m.status) && m.organizer_id === me.id && (
              <button className="btn ghost small" onClick={() => withdraw(m.meeting_id)}>Withdraw</button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
