import { useMemo, useState } from "react";
import { fmtAgo, fmtCHF, fmtDate } from "../../format.js";
import { initials } from "./BoardView.jsx";

/* List / backlog: every item in a sortable table; "Tree" shows sub-items under their parent. The state can be
   changed in place. */

const COLS = [["key", "ID"], ["title", "Title"], ["type_name", "Type"], ["state", "State"], ["assignee_name", "Assigned"],
              ["team_name", "Team"], ["priority", "Priority"], ["due_date", "Due"], ["updated_at", "Changed"]];

export default function ListView({ board, items, canEdit, onOpen, onState }) {
  const [sort, setSort] = useState({ by: "state", dir: 1 });
  const [tree, setTree] = useState(true);
  const states = (board.states || []).filter((s) => !s.archived);
  const pos = Object.fromEntries(states.map((s, i) => [s.id, i]));
  const rows = useMemo(() => {
    const val = (c) => (sort.by === "state" ? pos[c.state_id] ?? 99 : sort.by === "key" ? c.number
      : c[sort.by] ?? (sort.by === "due_date" ? "9999" : ""));
    const sorted = [...items].sort((a, b) => {
      const x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : a.id - b.id) * sort.dir;
    });
    if (!tree) return sorted.map((c) => ({ c, depth: 0 }));
    const byParent = {};
    const ids = new Set(items.map((c) => c.id));
    for (const c of sorted) (byParent[c.parent_id && ids.has(c.parent_id) ? c.parent_id : 0] ||= []).push(c);
    const out = [];
    const walk = (pid, depth) => (byParent[pid] || []).forEach((c) => { out.push({ c, depth }); if (depth < 6) walk(c.id, depth + 1); });
    walk(0, 0);
    return out;
  }, [items, sort, tree]); // eslint-disable-line react-hooks/exhaustive-deps
  const head = (k, l) => (
    <th key={k}><button type="button" className="linkish bd-th" onClick={() => setSort({ by: k, dir: sort.by === k ? -sort.dir : 1 })}>
      {l}{sort.by === k ? (sort.dir > 0 ? " ▲" : " ▼") : ""}</button></th>
  );
  return (
    <div className="bd-list-wrap">
      <label className="mail-unread-toggle bd-tree-toggle"><input type="checkbox" checked={tree} onChange={(e) => setTree(e.target.checked)} /> Tree (sub-items under their parent)</label>
      <div className="bd-table-scroll">
        <table className="bd-table">
          <thead><tr>{COLS.map(([k, l]) => head(k, l))}</tr></thead>
          <tbody>
            {rows.map(({ c, depth }) => (
              <tr key={`${c.kind || "item"}:${c.id}`} className={c.done ? "done" : ""}>
                <td className="bd-td-key"><span className="bd-type-dot" style={{ "--type": c.type_color }} />{c.key}</td>
                <td className="bd-td-title" style={{ paddingLeft: `${8 + depth * 18}px` }}>
                  {depth > 0 && <span className="muted">↳ </span>}
                  <button type="button" className="linkish" onClick={() => onOpen(c)}>{c.title}</button>
                  {c.value != null && c.kind === "deal" && <span className="meta"> · {fmtCHF(c.value, { compact: true })}</span>}
                </td>
                <td>{c.type_name}</td>
                <td>
                  {canEdit ? (
                    <select className="crm-sort" value={c.state_id} aria-label="State" onChange={(e) => onState(c, Number(e.target.value))}>
                      {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  ) : c.state_name}
                </td>
                <td>{c.assignee_name ? <span className="bd-who"><span className="bd-avatar sm">{initials(c.assignee_name)}</span>{c.assignee_name}</span> : <span className="muted">—</span>}</td>
                <td>{c.team_name ? <span className="bd-who"><span className="bd-team" style={{ "--team": c.team_color || "var(--muted)" }} />{c.team_name}</span> : ""}</td>
                <td>{c.priority === 1 ? <b className="warn-text">Urgent</b> : c.priority === 3 ? "Low" : "Normal"}</td>
                <td className={c.overdue ? "warn-text" : ""}>{c.due_date ? fmtDate(c.due_date) : ""}</td>
                <td className="meta">{c.updated_at ? fmtAgo(c.updated_at) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="muted small">No items match.</p>}
    </div>
  );
}
