import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { usePoll, useStoredState } from "./hooks.js";
import { THEMES, ThemeContext, useTheme } from "./theme.js";
import Login from "./components/Login.jsx";
import TwoFactorCard from "./components/TwoFactorCard.jsx";
import Stats from "./components/Stats.jsx";
import Backdrop from "./components/Backdrop.jsx";
import { Icon } from "./components/icons.jsx";
import { Clock, DecodeText, Emblem } from "./components/hud.jsx";
import Live from "./pages/Live.jsx";
import Recordings from "./pages/Recordings.jsx";
import Tasks from "./pages/Tasks.jsx";
import Activity from "./pages/Activity.jsx";
import Health from "./pages/Health.jsx";
import People from "./pages/People.jsx";
import Profile from "./pages/Profile.jsx";
import Usage from "./pages/Usage.jsx";
import Chat from "./pages/Chat.jsx";
import Notes from "./pages/Notes.jsx";

const TABS = [
  { id: "live", label: "Live", title: "Live operations", kicker: "Channels in progress", Page: Live },
  { id: "chat", label: "Chat", title: "Chat", kicker: "Talk to your butler", Page: Chat },
  { id: "history", label: "Archive", title: "Archive", kicker: "Recordings & transcripts", Page: Recordings },
  { id: "tasks", label: "Tasks", title: "Tasks & calls", kicker: "Reminders, calls & tasks", Page: Tasks },
  { id: "notes", label: "Files", title: "Notes & files", kicker: "On the NAS", Page: Notes },
  { id: "activity", label: "Log", title: "Event log", kicker: "Everything that happened", Page: Activity },
  { id: "health", label: "Systems", title: "Systems", kicker: "Diagnostics", Page: Health },
  { id: "people", label: "Personnel", title: "Personnel", kicker: "Registry & access", Page: People, admin: true },
  { id: "usage", label: "Usage", title: "API usage", kicker: "Tokens, minutes & cost", Page: Usage, admin: true },
  { id: "profile", label: "Profile", title: "My profile", kicker: "Identity & connections", Page: Profile },
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

function ThemeSwitch({ theme, setTheme }) {
  return (
    <div className="theme-switch" role="group" aria-label="Interface">
      {Object.entries(THEMES).map(([t, cfg]) => (
        <button key={t} className={t === theme ? "active" : ""} onClick={() => setTheme(t)} aria-pressed={t === theme}>
          {cfg.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState(undefined); // undefined = checking, null = signed out
  const [tab, setTab] = useStoredState("alfred-tab", "live");
  // Returning from Google's consent screen (/app?google=...): open the Profile tab where the result shows.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("google")) setTab("profile");
  }, [setTab]);
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
        <Backdrop />
        <div className="boot"><Emblem size="xl" /><p className="subtitle blink">Initializing</p></div>
      </ThemeContext.Provider>
    );
  }
  if (me === null) {
    return (
      <ThemeContext.Provider value={theme}>
        <Backdrop />
        <Login onLogin={setMe} themeSwitch={<ThemeSwitch theme={theme} setTheme={setTheme} />} />
      </ThemeContext.Provider>
    );
  }

  if (me.mfa_setup_required) {
    return (
      <ThemeContext.Provider value={theme}>
        <Backdrop />
        <section className="mfa-gate">
          <header className="login-top">
            <span className="kicker"><span className="blip" />Security check</span>
            <button className="btn small" onClick={logout}>Sign out</button>
          </header>
          <div className="hud mfa-gate-card">
            <h1>One more step, {me.display_name.split(" ")[0]}</h1>
            <p className="muted">Your account needs two-factor sign-in before the dashboard opens.
              Set up an authenticator app, or switch on codes by Telegram.</p>
            <TwoFactorCard assistant={me.assistant_name || "Alfred"} onEnabled={() => api("/me").then(setMe).catch(() => {})} />
          </div>
        </section>
      </ThemeContext.Provider>
    );
  }

  const isAdmin = me.role === "admin";
  // Previewing a role without dashboard access: only the chat (to test what Alfred lets them do).
  const tabs = TABS.filter((t) => (!t.admin || isAdmin) && (me.role_has_dashboard !== false || t.id === "chat"));
  const refreshMe = () => api("/me").then(setMe).catch(() => {});
  const stopViewAs = () => api("/view-as", { method: "DELETE" }).then(refreshMe).catch(() => {});
  const active = tabs.find((t) => t.id === tab) || tabs[0];
  const Page = active.Page;
  const skin = THEMES[theme];
  const assistant = me.assistant_name || "Alfred";
  const wordmark = theme === "alfred" ? assistant.toUpperCase().split("").join(".") + "." : assistant.toUpperCase();
  const index = String(tabs.indexOf(active) + 1).padStart(2, "0");

  return (
    <ThemeContext.Provider value={theme}>
      <Backdrop />
      <div className="app">
        <aside className="rail">
          <a className="rail-brand" href="/" title="Home">
            <Emblem size="sm" />
            <div>
              <div className="wordmark">{wordmark}</div>
              <div className="subtitle">{skin.subtitle}</div>
            </div>
          </a>

          <nav className="rail-nav" role="tablist" aria-label="Sections">
            {tabs.map((t, i) => (
              <button key={t.id} role="tab" aria-selected={t.id === active.id}
                      className={t.id === active.id ? "active" : ""} onClick={() => setTab(t.id)}>
                <Icon name={t.id} />
                <span className="nav-label">{t.label}</span>
                <span className="nav-idx">{String(i + 1).padStart(2, "0")}</span>
              </button>
            ))}
          </nav>

          <div className="rail-foot">
            <ThemeSwitch theme={theme} setTheme={setTheme} />
            <div className="operator">
              <div>
                <span className="op-label">Operator · {me.role}</span>
                <span className="op-name">{me.display_name}</span>
              </div>
              <button className="icon-btn" onClick={logout} title="Sign out" aria-label="Sign out"><Icon name="logout" /></button>
            </div>
            <nav className="legal-links" aria-label="Legal">
              <a href="/privacy-policy">Privacy</a>
              <a href="/terms-of-service">Terms</a>
            </nav>
          </div>
        </aside>

        <div className="content">
          <header className="topline">
            <div className="page-title">
              <p className="kicker"><span className="blip" />{index} · {active.kicker}</p>
              <DecodeText as="h1" text={active.title} key={active.id} />
            </div>
            <div className="topline-right">
              <SystemStatus />
              <Clock />
            </div>
            {me.viewing_as && (
              <div className="view-as-banner" role="status">
                <span>👁 Viewing as <b>{me.viewing_as}</b>: their permissions, applied to your own account
                  {me.role_has_dashboard === false ? ". This role has no dashboard; test Alfred in the chat." : "."}</span>
                <button className="btn primary small" onClick={stopViewAs}>Back to my view</button>
              </div>
            )}
          </header>

          <main key={active.id} className="page-enter">
            {active.id === "live" && <Stats />}
            <Page me={me} isAdmin={isAdmin} assistant={assistant} refreshMe={refreshMe} />
          </main>
        </div>
      </div>
    </ThemeContext.Provider>
  );
}
