import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { usePoll, useStoredState } from "./hooks.js";
import Login from "./components/Login.jsx";
import Stats from "./components/Stats.jsx";
import { ArcReactor, Clock } from "./components/hud.jsx";
import Live from "./pages/Live.jsx";
import Recordings from "./pages/Recordings.jsx";
import Tasks from "./pages/Tasks.jsx";
import Activity from "./pages/Activity.jsx";
import Health from "./pages/Health.jsx";
import People from "./pages/People.jsx";

const TABS = [
  { id: "live", label: "Live", Page: Live },
  { id: "history", label: "Archive", Page: Recordings },
  { id: "tasks", label: "Tasks", Page: Tasks },
  { id: "activity", label: "Log", Page: Activity },
  { id: "health", label: "Systems", Page: Health },
  { id: "people", label: "Personnel", Page: People, admin: true },
];

const THEMES = { alfred: "J.A.R.V.I.S.", friday: "F.R.I.D.A.Y." };
const SUBTITLES = {
  alfred: "Just A Rather Very Intelligent System",
  friday: "Female Replacement Intelligent Digital Assistant Youth",
};

function useTheme() {
  const [theme, setTheme] = useStoredState("alfred-theme", "alfred");
  useEffect(() => { document.documentElement.dataset.theme = THEMES[theme] ? theme : "alfred"; }, [theme]);
  return [THEMES[theme] ? theme : "alfred", setTheme];
}

function SystemStatus() {
  const { data } = usePoll(() => api("/health"), 30000);
  if (!data) return <div className="sys-status">Scanning…</div>;
  const down = data.filter((h) => !h.ok).length;
  return (
    <div className={`sys-status ${down ? "warn" : "ok"}`}>
      <span className="led" />
      {down ? `${down} system${down > 1 ? "s" : ""} degraded` : "All systems nominal"}
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState(undefined); // undefined = checking, null = signed out
  const [tab, setTab] = useStoredState("alfred-tab", "live");
  const [theme, setTheme] = useTheme();

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

  if (me === undefined) {
    return <div className="boot"><ArcReactor size="xl" /><p className="subtitle blink">Initializing</p></div>;
  }
  if (me === null) return <Login onLogin={setMe} />;

  const isAdmin = me.role === "admin";
  const tabs = TABS.filter((t) => !t.admin || isAdmin);
  const active = tabs.find((t) => t.id === tab) || tabs[0];
  const Page = active.Page;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <ArcReactor size="sm" />
          <div>
            <div className="wordmark">{THEMES[theme]}</div>
            <div className="subtitle">{SUBTITLES[theme]}</div>
          </div>
        </div>
        <SystemStatus />
        <div className="top-right">
          <div className="theme-switch" role="group" aria-label="Interface">
            {Object.keys(THEMES).map((t) => (
              <button key={t} className={t === theme ? "active" : ""} onClick={() => setTheme(t)} aria-pressed={t === theme}>
                {t}
              </button>
            ))}
          </div>
          <Clock />
          <div className="operator">
            <span className="op-label">Operator</span>
            <span className="op-name">{me.display_name}</span>
            <button className="btn ghost small" onClick={logout}>Sign out</button>
          </div>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {tabs.map((t, i) => (
          <button key={t.id} role="tab" aria-selected={t.id === active.id}
                  className={t.id === active.id ? "active" : ""} onClick={() => setTab(t.id)}>
            <span className="tab-idx">{String(i + 1).padStart(2, "0")}</span>{t.label}
          </button>
        ))}
      </nav>

      <main>
        <Stats />
        <Page me={me} isAdmin={isAdmin} />
      </main>
    </div>
  );
}
