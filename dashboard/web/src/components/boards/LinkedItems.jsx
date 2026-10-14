import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { fmtDate } from "../../format.js";
import { navigate, usePoll } from "../../hooks.js";
import { can, ErrorNote } from "../common.jsx";

/* Board items linked to a CRM client or deal, or assigned to a person (Team page), with a quick way to add one.
   Opening an item switches to the Boards tab with it open. */

export default function LinkedItems({ me, filter, preset, title = "Work items", empty = "No board items yet." }) {
  const { data, error, reload } = usePoll(() => (can(me, "boards.use") ? api(`/boards/linked?${filter}`) : Promise.resolve(null)), 0, [filter]);
  const [adding, setAdding] = useState(false);
  const [boards, setBoards] = useState([]);
  const [f, setF] = useState({ board: "", title: "" });
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!adding) return;
    api("/boards").then((d) => {
      const list = d.boards.filter((b) => b.kind === "board" && (b.level === "contribute" || b.level === "manage"));
      setBoards(list);
      setF((x) => ({ ...x, board: x.board || String(list[0]?.id || "") }));
    }).catch((e) => setErr(e.message));
  }, [adding]);
  if (!can(me, "boards.use")) return null;
  async function add(e) {
    e.preventDefault();
    setErr("");
    try {
      const it = await api(`/boards/${f.board}/items`, { method: "POST", body: { fields: { title: f.title.trim(), ...preset } } });
      setF({ ...f, title: "" }); setAdding(false); reload();
      navigate("boards", { item: it.id, board: it.board_id });
    } catch (e2) { setErr(e2.message); }
  }
  const items = data || [];
  return (
    <div className="crm-people bd-linked-items">
      <h3 className="sub">{title} {items.length > 0 && <span className="mail-count plain">{items.filter((c) => !c.done).length}</span>}</h3>
      <ErrorNote error={error || err} />
      {data && items.length === 0 && !adding && <p className="muted small">{empty}</p>}
      {items.map((c) => (
        <button key={c.id} type="button" className={`bd-child${c.done ? " done" : ""}`} onClick={() => navigate("boards", { item: c.id, board: c.board_id })}>
          <span className="bd-type-dot" style={{ "--type": c.type_color }} /><span className="bd-key">{c.key}</span>
          <span className="bd-child-title">{c.title}</span>
          <span className={`meta${c.overdue ? " warn-text" : ""}`}>{c.state_name}{c.due_date ? ` · ${fmtDate(c.due_date)}` : ""}</span>
        </button>
      ))}
      {preset && (adding ? (
        <form className="team-step-add" onSubmit={add}>
          <select value={f.board} onChange={(e) => setF({ ...f, board: e.target.value })} aria-label="Board">
            {boards.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <input autoFocus value={f.title} maxLength={200} placeholder="What needs doing?" onChange={(e) => setF({ ...f, title: e.target.value })} />
          <button className="btn small" disabled={!f.title.trim() || !f.board}>Add</button>
          <button type="button" className="btn ghost small" onClick={() => setAdding(false)}>Cancel</button>
          {boards.length === 0 && <span className="muted small">No board you can add to.</span>}
        </form>
      ) : <button type="button" className="btn ghost small" onClick={() => setAdding(true)}>+ Work item</button>)}
    </div>
  );
}
