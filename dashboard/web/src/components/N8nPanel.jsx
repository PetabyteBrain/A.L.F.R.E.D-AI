import { fmtTime } from "../format.js";
import { Empty, Tag } from "./common.jsx";

const RUN_KIND = { success: "ok", error: "bad", running: "busy", waiting: "busy", crashed: "bad" };

/* Admin: n8n workflows (active first) with their last run, and a button to open n8n. */
export default function N8nPanel({ t }) {
  if (!t) return null;
  const wfs = [...(t.n8n || [])].sort((a, b) => (b.active - a.active) || (a.name || "").localeCompare(b.name || ""));
  return (
    <section className="n8n-panel">
      <h3 className="sub">
        Automations · n8n
        {t.n8n_url && <a className="btn primary small" href={t.n8n_url} target="_blank" rel="noreferrer noopener">Open n8n ↗</a>}
      </h3>
      {t.n8n_error && <Empty>n8n: {t.n8n_error}</Empty>}
      {!t.n8n_error && !t.n8n && <Empty>n8n isn't configured.</Empty>}
      {t.n8n && (
        <p className="muted small">
          {wfs.filter((w) => w.active).length} active of {wfs.length} workflows
          {t.n8n_failed_24h ? <> · <Tag kind="bad">{t.n8n_failed_24h} failed run{t.n8n_failed_24h > 1 ? "s" : ""} in 24 h</Tag></> : " · no failed runs in 24 h"}
        </p>
      )}
      <div className="list">
        {wfs.map((w) => (
          <div className={`row${w.active ? "" : " inactive"}`} key={w.id}>
            <div>
              <div>{w.name} {w.active ? <Tag kind="ok">active</Tag> : <Tag>inactive</Tag>}</div>
              <div className="meta">
                {(w.triggers || []).join(" · ") || "no trigger"} · updated {fmtTime(w.updatedAt)}
                {w.last_run ? <> · last run {fmtTime(w.last_run.startedAt)} <Tag kind={RUN_KIND[w.last_run.status] || ""}>{w.last_run.status}</Tag></> : " · never run"}
              </div>
            </div>
            {t.n8n_url && <a className="btn small" href={`${t.n8n_url}/workflow/${encodeURIComponent(w.id)}`} target="_blank" rel="noreferrer noopener">Edit ↗</a>}
          </div>
        ))}
      </div>
    </section>
  );
}
