import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { ConfirmDialog, Panel } from "../components/hud.jsx";

export default function People() {
  const { data: people, error } = usePoll(() => api("/people"), 30000);
  const { data: attempts, error: err2 } = usePoll(() => api("/access-attempts?limit=80"), 15000);
  const [callee, setCallee] = useState(null);
  const [busy, setBusy] = useState(false);
  const [callError, setCallError] = useState("");
  const [notice, setNotice] = useState("");

  async function placeCall() {
    setBusy(true);
    setCallError("");
    try {
      await api("/calls", { method: "POST", body: { user_id: callee.id } });
      setNotice(`Dialing ${callee.display_name}… watch the Live tab.`);
      setCallee(null);
    } catch (e) {
      setCallError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel code="07" title="Personnel registry">
      <p className="muted">
        Managed with <code>docker compose exec alfred python -m orchestrator.identity.admin_cli</code>
      </p>
      <ErrorNote error={error || err2} />
      {notice && <div className="notice" role="status" onClick={() => setNotice("")}>{notice}</div>}
      <div className="list">
        {(people || []).map((p) => (
          <div className="row" key={p.id}>
            <div>
              <div>#{p.id} {p.display_name} <Tag>{p.role}</Tag> {!p.active && <Tag kind="bad">disabled</Tag>}</div>
              <div className="meta">{p.identifiers.map((i) => `${i.type}: ${i.value}`).join(" · ") || "no identifiers"}</div>
            </div>
            {p.active && p.identifiers.some((i) => i.type === "phone") && (
              <button className="btn small" onClick={() => { setCallError(""); setCallee(p); }}>Call</button>
            )}
          </div>
        ))}
      </div>
      <h3 className="sub">Access attempts</h3>
      {attempts && attempts.length === 0 && <Empty>No attempts recorded.</Empty>}
      <div className="feed">
        {(attempts || []).map((a, i) => (
          <div className="e" key={i}>
            <span className="ts">{fmtTime(a.ts)}</span>
            <span className={`kind ${a.resolved ? "" : "access"}`}>{a.resolved ? "allowed" : "blocked"}</span>
            <span>{a.channel} · {a.raw_identifier}{a.detail ? ` · ${a.detail}` : ""}</span>
          </div>
        ))}
      </div>
      {callee && (
        <ConfirmDialog
          title="Place call"
          body={<>Have Alfred phone <b>{callee.display_name}</b> now from the Alfred number?</>}
          confirmLabel="Dial"
          busy={busy}
          error={callError}
          onConfirm={placeCall}
          onCancel={() => setCallee(null)}
        />
      )}
    </Panel>
  );
}
