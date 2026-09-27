import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Gauge, Panel } from "./hud.jsx";

export default function Stats() {
  const { data: o } = usePoll(() => api("/overview"), 10000);
  if (!o) return <div className="stats" />;
  return (
    <div className="stats">
      <Panel className="gauges" code="00" title="Telemetry">
        <div className="gauge-row">
          <Gauge value={o.active} label="Active" max={Math.max(3, o.active)} />
          <Gauge value={o.calls_today} label="Calls today" />
          <Gauge value={o.chats_today} label="Chats today" />
          {o.blocked_today !== null && <Gauge value={o.blocked_today} label="Blocked" tone="warn" />}
        </div>
      </Panel>
      <Panel className="channels" code="01" title="Uplinks">
        <ul className="uplinks">
          {Object.entries(o.channels).map(([k, on]) => (
            <li key={k} className={on ? "on" : "off"}><span className="led" />{k}<em>{on ? "online" : "offline"}</em></li>
          ))}
          <li className="on model"><span className="led" />core<em>{o.model}</em></li>
        </ul>
      </Panel>
    </div>
  );
}
