import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { useStoredState } from "./hooks.js";
import Login from "./components/Login.jsx";
import Stats from "./components/Stats.jsx";
import { Reactor } from "./components/common.jsx";
import Live from "./pages/Live.jsx";
import Recordings from "./pages/Recordings.jsx";
import Tasks from "./pages/Tasks.jsx";
import Activity from "./pages/Activity.jsx";
import Health from "./pages/Health.jsx";
import People from "./pages/People.jsx";

const TABS = [
  { id: "live", label: "Live", Page: Live },
  { id: "history", label: "Recordings", Page: Recordings },
  { id: "tasks", label: "Tasks", Page: Tasks },
  { id: "activity", label: "Activity", Page: Activity },
  { id: "health", label: "Health", Page: Health },
  { id: "people", label: "People", Page: People, admin: true },
];

export default function App() {
  const [me, setMe] = useState(undefined); // undefined = checking, null = signed out
  const [tab, setTab] = useStoredState("alfred-tab", "live");

  const signOut = useCallback(() => setMe(null), []);
  useEffect(() => {
    setUnauthorizedHandler(signOut);
    api("/me").then(setMe).catch(() => setMe(null));
  }, [signOut]);

  async function logout() {
    try {
      await api("/logout", { method: "POST" });
    } finally {
      setMe(null);
    }
  }

  if (me === undefined) return <div className="boot"><Reactor /></div>;
  if (me === null) return <Login onLogin={setMe} />;

  const isAdmin = me.role === "admin";
  const tabs = TABS.filter((t) => !t.admin || isAdmin);
  const active = tabs.find((t) => t.id === tab) || tabs[0];
  const Page = active.Page;

  return (
    <div>
      <header className="topbar">
        <div className="brand"><Reactor small /><span>{(me.assistant_name || "Alfred").toUpperCase()}</span></div>
        <nav role="tablist">
          {tabs.map((t) => (
            <button key={t.id} role="tab" aria-selected={t.id === active.id}
                    className={t.id === active.id ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="me">
          <span>{me.display_name} · {me.role}</span>
          <button className="ghost" onClick={logout}>Sign out</button>
        </div>
      </header>
      <main>
        <Stats />
        <Page me={me} isAdmin={isAdmin} />
      </main>
    </div>
  );
}
