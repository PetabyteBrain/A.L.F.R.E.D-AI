import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";

export default function People() {
  const { data: people, error } = usePoll(() => api("/people"), 30000);
  const { data: attempts, error: err2 } = usePoll(() => api("/access-attempts?limit=80"), 15000);
  return (
    <section className="panel">
      <h2>Registry</h2>
      <p className="muted">
        Managed with <code>docker compose exec alfred python -m orchestrator.identity.admin_cli</code>
      </p>
      <ErrorNote error={error || err2} />
      <div className="list">
        {(people || []).map((p) => (
          <div className="row" key={p.id}>
            <div>
              <div>#{p.id} {p.display_name} <Tag>{p.role}</Tag> {!p.active && <Tag kind="bad">disabled</Tag>}</div>
              <div className="meta">{p.identifiers.map((i) => `${i.type}: ${i.value}`).join(" · ") || "no identifiers"}</div>
            </div>
          </div>
        ))}
      </div>
      <h2>Access attempts</h2>
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
    </section>
  );
}
