import { Panel } from "../components/hud.jsx";
import AiModelCard from "../components/AiModelCard.jsx";
import QuietDefaultCard from "../components/QuietDefaultCard.jsx";
import RolePermissionsCard from "../components/RolePermissionsCard.jsx";
import ScreeningCard from "../components/ScreeningCard.jsx";
import ViewAsCard from "../components/ViewAsCard.jsx";

/* Admin: settings that apply to everyone (personal settings stay on each person's profile). */
export default function Settings({ me, assistant, refreshMe }) {
  return (
    <Panel code="11" title="Settings">
      <p className="muted small">Settings for the whole household. Each person's own settings are on their profile.</p>
      <div className="settings-grid">
        <div>
          <AiModelCard assistant={assistant} />
          <RolePermissionsCard assistant={assistant} />
          <section className="settings-block"><ViewAsCard me={me} onChanged={refreshMe} /></section>
        </div>
        <div>
          <QuietDefaultCard assistant={assistant} />
          <ScreeningCard assistant={assistant} />
        </div>
      </div>
    </Panel>
  );
}
