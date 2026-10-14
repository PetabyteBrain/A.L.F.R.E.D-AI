import { useStoredState } from "../hooks.js";
import { Panel } from "../components/hud.jsx";
import { can } from "../components/common.jsx";
import AiModelCard from "../components/AiModelCard.jsx";
import QuietDefaultCard from "../components/QuietDefaultCard.jsx";
import RolesManager from "../components/RolesManager.jsx";
import ScreeningCard from "../components/ScreeningCard.jsx";
import MailSettingsCard from "../components/MailSettingsCard.jsx";
import ViewAsCard from "../components/ViewAsCard.jsx";

/* Settings that apply to everyone (settings.manage) and the role editor (roles.manage), one section at a time like
   the profile; personal settings stay on each person's profile. */

const SECTIONS = [
  { id: "ai", perm: "settings.manage", label: "AI model", hint: () => "Model and billing key",
    intro: (a) => `Which Claude model ${a} thinks with, for conversations and for calls, and which API key pays.` },
  { id: "quiet", perm: "settings.manage", label: "Quiet hours", hint: () => "The household default",
    intro: (a) => `When ${a} doesn't disturb anyone on someone else's behalf, unless they set their own hours.` },
  { id: "mail", perm: "settings.manage", label: "Email", hint: () => "Mailbox and inquiries",
    intro: (a) => `${a}'s own mailbox: receiving, inquiries from outside and how attachments are filed.` },
  { id: "screening", perm: "settings.manage", label: "Unknown callers", hint: () => "Call screening",
    intro: (a) => `Whether ${a} answers numbers he doesn't know, takes a message and passes it on.` },
  { id: "roles", perm: "roles.manage", label: "Roles & groups", hint: () => "Permissions, roles, groups",
    intro: () => "What each role may do, who holds which roles, and groups of people that bring roles with them. Teams for the boards are on the Team tab." },
  { id: "preview", label: "View as", hint: () => "Preview someone's dashboard",
    intro: () => "See the dashboard as a role or a person would, to check what they can and can't do." },
];

export default function Settings({ me, assistant, refreshMe }) {
  const sections = SECTIONS.filter((x) => !x.perm || can(me, x.perm));
  const [section, setSection] = useStoredState("alfred-settings-section", sections[0]?.id || "preview");
  const current = sections.find((x) => x.id === section) || sections[0];
  const card = (children) => <div className="setting-card">{children}</div>;
  const body = {
    ai: card(<AiModelCard assistant={assistant} />),
    quiet: card(<QuietDefaultCard assistant={assistant} />),
    mail: card(<MailSettingsCard assistant={assistant} />),
    screening: card(<ScreeningCard assistant={assistant} />),
    roles: <RolesManager />,
    preview: card(<ViewAsCard me={me} onChanged={refreshMe} />),
  }[current.id];
  return (
    <Panel code="11" title="Settings">
      <p className="muted small">Settings for the whole household. Each person's own settings are on their profile.</p>
      <div className={`settings-shell${current.id === "roles" ? " wide-body" : ""}`}>
        <nav className="settings-nav" aria-label="Settings sections">
          {sections.map((x) => (
            <button key={x.id} className={x.id === current.id ? "active" : ""} aria-current={x.id === current.id ? "page" : undefined}
                    onClick={() => setSection(x.id)}>
              <b>{x.label}</b><span>{x.hint(assistant)}</span>
            </button>
          ))}
        </nav>
        <section className="settings-body" aria-labelledby="settings-title">
          <h2 id="settings-title" className="settings-title">{current.label}</h2>
          <p className="muted small settings-intro">{current.intro(assistant)}</p>
          <div className="setting-cards">{body}</div>
        </section>
      </div>
    </Panel>
  );
}
