import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import CallQueue from "../components/CallQueue.jsx";
import Reminders from "../components/Reminders.jsx";
import TaskList from "../components/TaskList.jsx";
import Meetings from "../components/Meetings.jsx";

export default function Tasks({ isAdmin, me, assistant }) {
  const { data: t } = usePoll(() => (isAdmin ? api("/tasks") : Promise.resolve(null)), 60000);

  return (
    <Panel code="04" title="Tasks & calls">
      <TaskList me={me} assistant={assistant} />
      <Meetings me={me} assistant={assistant} />
      <Reminders me={me} assistant={assistant} />
      <CallQueue me={me} assistant={assistant} />

      {isAdmin && t && (
        <>
          <h3 className="sub">n8n · active workflows</h3>
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
    </Panel>
  );
}
