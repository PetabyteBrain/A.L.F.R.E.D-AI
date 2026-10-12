import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { can, Empty, ErrorNote, Tag } from "./common.jsx";
import CallDialog from "./CallDialog.jsx";

const STATUS_KIND = { pending: "", dialing: "busy", done: "ok", voicemail: "busy", no_answer: "bad", failed: "bad", cancelled: "" };

/* Upcoming call requests; finished ones in an "Archived" tab with a search. */
export default function CallQueue({ me, assistant }) {
  const [tab, setTab] = useState("upcoming");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => setQ(search.trim()), 300); return () => clearTimeout(t); }, [search]);
  const path = tab === "upcoming" ? "/calls/scheduled?include_done=false"
    : `/calls/scheduled?${new URLSearchParams({ archived: "true", q })}`;
  const { data: rows, error, reload } = usePoll(() => api(path), tab === "upcoming" ? 10000 : 0, [path]);
  const { data: targets } = usePoll(() => (me.can_request_calls ? api("/calls/targets") : Promise.resolve([])));
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");

  async function cancel(id) {
    setErr("");
    try {
      await api(`/calls/scheduled/${Number(id)}`, { method: "DELETE" });
      reload();
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <>
      <h3 className="sub">
        Calls
        {me.can_request_calls && <button className="btn primary small" onClick={() => setOpen(true)}>Schedule a call</button>}
      </h3>
      <div className="call-tabs">
        <div className="seg" role="tablist" aria-label="Calls">
          <button role="tab" className={tab === "upcoming" ? "active" : ""} aria-selected={tab === "upcoming"} onClick={() => setTab("upcoming")}>Upcoming</button>
          <button role="tab" className={tab === "archived" ? "active" : ""} aria-selected={tab === "archived"} onClick={() => setTab("archived")}>Archived</button>
        </div>
        {tab === "archived" && (
          <input type="search" value={search} placeholder="Search name, purpose or result…" aria-label="Search archived calls"
                 maxLength={100} onChange={(e) => setSearch(e.target.value)} />
        )}
      </div>
      <ErrorNote error={error || err} />
      {notice && <div className="notice" role="status" onClick={() => setNotice("")}>{notice}</div>}
      {rows && rows.length === 0 && (tab === "upcoming"
        ? <Empty>No calls planned. Ask {assistant} "call Max tomorrow at 9 and ask about the invoice".</Empty>
        : <Empty>{q ? "No archived call matches." : "No finished calls yet."}</Empty>)}
      <div className="list">
        {(rows || []).map((c) => (
          <div className="row call-row" key={c.id}>
            <div>
              <div>
                ☎ {c.target_name} <Tag kind={STATUS_KIND[c.status] ?? ""}>{c.status.replace("_", " ")}</Tag>{" "}
                <span className="mono small">{c.run_at_local}</span>
              </div>
              <div className="meta">
                {c.requester_name ? `for ${c.requester_name}` : "system"}{c.purpose ? ` · ${c.purpose}` : " · plain call"}
                {c.attempts > 1 ? ` · ${c.attempts} attempts` : ""}
              </div>
              {c.result && <div className="call-result">{c.result}</div>}
            </div>
            {c.status === "pending" && (can(me, "everyone.view") || c.requested_by === me.id) && (
              <button className="btn danger small" onClick={() => cancel(c.id)}>Cancel</button>
            )}
          </div>
        ))}
      </div>
      {open && targets && (
        <CallDialog
          assistant={assistant}
          targets={targets}
          onCancel={() => setOpen(false)}
          onDone={(r) => {
            setOpen(false);
            setNotice(r.held_for_quiet_hours ? `${r.target_name} is in quiet hours: ${assistant} calls at ${r.run_at_local}.`
              : r.status === "pending" ? `Scheduled: ${r.target_name}, ${r.run_at_local}.` : `Dialing ${r.target_name}…`);
            reload();
          }}
        />
      )}
    </>
  );
}
