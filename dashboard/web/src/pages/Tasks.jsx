import { api } from "../api.js";
import { usePoll, useStoredState } from "../hooks.js";
import { Panel } from "../components/hud.jsx";
import AgendaCalendar from "../components/AgendaCalendar.jsx";
import CallQueue from "../components/CallQueue.jsx";
import N8nPanel from "../components/N8nPanel.jsx";
import OutsideCalls from "../components/OutsideCalls.jsx";
import Reminders from "../components/Reminders.jsx";
import TaskList from "../components/TaskList.jsx";
import Meetings from "../components/Meetings.jsx";

export default function Tasks({ me, assistant }) {
  const [view, setView] = useStoredState("alfred-tasks-view", "calendar");
  // n8n block: admins, and cofounders while the admin allows it (Settings tab)
  const { data: t } = usePoll(() => api("/tasks"), 60000);

  return (
    <Panel code="04" title="Tasks & calls" actions={
      <div className="seg" role="tablist" aria-label="View">
        <button role="tab" className={view === "calendar" ? "active" : ""} aria-selected={view === "calendar"} onClick={() => setView("calendar")}>Calendar</button>
        <button role="tab" className={view === "list" ? "active" : ""} aria-selected={view === "list"} onClick={() => setView("list")}>List</button>
      </div>
    }>
      {view === "calendar" ? (
        <AgendaCalendar />
      ) : (
        <>
          <TaskList me={me} assistant={assistant} />
          <Meetings me={me} assistant={assistant} />
          <Reminders me={me} assistant={assistant} />
          <CallQueue me={me} assistant={assistant} />
          {(me.role === "admin" || me.role === "cofounder") && <OutsideCalls assistant={assistant} />}
          {t && "n8n_can_edit" in t && <N8nPanel t={t} assistant={assistant} />}
        </>
      )}
    </Panel>
  );
}
