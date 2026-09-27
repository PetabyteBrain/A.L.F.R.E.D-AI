import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";

export default function Tasks({ isAdmin }) {
  const { data: t, error, reload } = usePoll(() => api("/tasks"), 30000);
  const [title, setTitle] = useState("");
  const [schedule, setSchedule] = useState("");
  const [formError, setFormError] = useState("");

  async function add(e) {
    e.preventDefault();
    setFormError("");
    try {
      await api("/tasks", { method: "POST", body: { title, schedule } });
      setTitle("");
      setSchedule("");
      reload();
    } catch (err) {
      setFormError(err.message);
    }
  }

  async function done(id) {
    try {
      await api(`/tasks/${Number(id)}`, { method: "DELETE" });
      reload();
    } catch (err) {
      setFormError(err.message);
    }
  }

  return (
    <section className="panel">
      <h2>Tasks</h2>
      {isAdmin && (
        <form className="inline-form" onSubmit={add}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Task" required maxLength={200} />
          <input value={schedule} onChange={(e) => setSchedule(e.target.value)}
                 placeholder="Schedule (e.g. every Monday 08:00)" maxLength={200} />
          <button type="submit">Add</button>
        </form>
      )}
      <ErrorNote error={error || formError} />
      {t && t.local.length === 0 && <Empty>No tasks yet.</Empty>}
      <div className="list">
        {(t?.local || []).map((x) => (
          <div className="row" key={x.id}>
            <div>
              <div>{x.title}</div>
              <div className="meta">
                {x.schedule || "no schedule"}{x.next_run_at ? ` · next ${fmtTime(x.next_run_at)}` : ""}
              </div>
            </div>
            {isAdmin && <button className="ghost small" onClick={() => done(x.id)}>Done</button>}
          </div>
        ))}
      </div>

      {isAdmin && t && (
        <>
          <h2>n8n active workflows</h2>
          {t.n8n_error && <Empty>n8n: {t.n8n_error}</Empty>}
          {!t.n8n_error && !(t.n8n || []).length && (
            <Empty>No active n8n workflows{t.n8n ? "" : " (n8n not configured)"}.</Empty>
          )}
          <div className="list">
            {(t.n8n || []).map((w) => (
              <div className="row" key={w.id}>
                <div>
                  <div>{w.name}</div>
                  <div className="meta">{(w.triggers || []).join(" · ") || "no trigger"} · updated {fmtTime(w.updatedAt)}</div>
                </div>
                <Tag kind="ok">active</Tag>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
