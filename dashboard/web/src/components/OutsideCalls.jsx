import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

const KIND = { pending: "", dialing: "busy", finishing: "busy", done: "ok", no_answer: "bad", failed: "bad", cancelled: "" };

/* Admins/cofounders: task calls to numbers outside the registry (asked for in chat). */
export default function OutsideCalls({ assistant }) {
  const { data: rows, error, reload } = usePoll(() => api("/external-calls"), 15000);
  const [err, setErr] = useState("");
  async function cancel(id) {
    setErr("");
    try { await api(`/external-calls/${Number(id)}`, { method: "DELETE" }); reload(); } catch (e) { setErr(e.message); }
  }
  return (
    <>
      <h3 className="sub">Outside calls</h3>
      <p className="muted small">
        Ask {assistant} in chat, with a concrete task: "call the garage at +41 31 000 01 00 and book a tyre change
        for next week". {assistant} reads it back and only dials after your yes, Mon–Sat 08:00–19:00.
      </p>
      <ErrorNote error={error || err} />
      <div className="list">
        {(rows || []).map((c) => (
          <div className="row call-row" key={c.id}>
            <div>
              <div>☎ {c.contact} <span className="mono small">{c.number}</span> <Tag kind={KIND[c.status] ?? ""}>{c.status.replace("_", " ")}</Tag>{" "}
                <span className="mono small">{c.run_at_local}</span></div>
              <div className="meta">for {c.requester_name} · {c.task}{c.attempts > 1 ? ` · ${c.attempts} attempts` : ""}</div>
              {c.result && <div className="call-result">{c.result}</div>}
            </div>
            {c.status === "pending" && <button className="btn danger small" onClick={() => cancel(c.id)}>Cancel</button>}
          </div>
        ))}
      </div>
    </>
  );
}
