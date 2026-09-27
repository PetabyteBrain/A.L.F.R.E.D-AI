import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Tag } from "./common.jsx";

export default function Stats() {
  const { data: o } = usePoll(() => api("/overview"), 10000);
  if (!o) return <section className="stats" />;
  const items = [
    ["Active now", o.active],
    ["Calls today", o.calls_today],
    ["Chats today", o.chats_today],
  ];
  if (o.blocked_today !== null) items.push(["Blocked today", o.blocked_today]);
  return (
    <section className="stats">
      {items.map(([k, v]) => (
        <div className="stat" key={k}><div className="v">{v}</div><div className="k">{k}</div></div>
      ))}
      <div className="stat">
        <div className="k spaced">Channels · {o.model}</div>
        <div className="tags">
          {Object.entries(o.channels).map(([k, on]) => <Tag key={k} kind={on ? "ok" : "bad"}>{k}</Tag>)}
        </div>
      </div>
    </section>
  );
}
