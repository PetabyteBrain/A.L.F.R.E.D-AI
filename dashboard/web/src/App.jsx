import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { usePoll, useStoredState } from "./hooks.js";
import { THEMES, ThemeContext, useTheme } from "./theme.js";
import Login from "./components/Login.jsx";
import Stats from "./components/Stats.jsx";
import { Clock, Emblem } from "./components/hud.jsx";
import Live from "./pages/Live.jsx";
import Recordings from "./pages/Recordings.jsx";
import Tasks from "./pages/Tasks.jsx";
import Activity from "./pages/Activity.jsx";
import Health from "./pages/Health.jsx";
import People from "./pages/People.jsx";
import Profile from "./pages/Profile.jsx";
import Usage from "./pages/Usage.jsx";

const TABS = [
  { id: "live", label: "Live", Page: Live },
  { id: "history", label: "Archive", Page: Recordings },
  { id: "tasks", label: "Tasks", Page: Tasks },
  { id: "activity", label: "Log", Page: Activity },
  { id: "health", label: "Systems", Page: Health },
  { id: "people", label: "Personnel", Page: People, admin: true },
  { id: "usage", label: "Usage", Page: Usage, admin: true },
  { id: "profile", label: "Profile", Page: Profile },
];


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
    return (
      <ThemeContext.Provider value={theme}>
        <div className="boot"><Emblem size="xl" /><p className="subtitle blink">Initializing</p></div>
      </ThemeContext.Provider>
    );
  }
  if (me === null) {
    return <ThemeContext.Provider value={theme}><Login onLogin={setMe} /></ThemeContext.Provider>;
  }

  const isAdmin = me.role === "admin";
  const tabs = TABS.filter((t) => !t.admin || isAdmin);
  const active = tabs.find((t) => t.id === tab) || tabs[0];
  const Page = active.Page;
  const skin = THEMES[theme];
  const assistant = me.assistant_name || "Alfred";
  const wordmark = theme === "alfred" ? assistant.toUpperCase().split("").join(".") + "." : assistant.toUpperCase();

  return (
    <ThemeContext.Provider value={theme}>
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Emblem size="sm" />
          <div>
            <div className="wordmark">{wordmark}</div>
            <div className="subtitle">{skin.subtitle}</div>
          </div>
        </div>
        <SystemStatus />
        <div className="top-right">
          <div className="theme-switch" role="group" aria-label="Interface">
            {Object.entries(THEMES).map(([t, cfg]) => (
              <button key={t} className={t === theme ? "active" : ""} onClick={() => setTheme(t)} aria-pressed={t === theme}>
                {cfg.label}
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
        <Page me={me} isAdmin={isAdmin} assistant={assistant} refreshMe={() => api("/me").then(setMe).catch(() => {})} />
        <nav className="legal-links" aria-label="Legal">
          <a href="/privacy-policy">Privacy Policy</a>
          <a href="/terms-of-service">Terms of Service</a>
        </nav>
      </main>
    </div>
    </ThemeContext.Provider>
  );
}
