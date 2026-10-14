import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { usePageActive, usePoll, useStoredState, useNavTarget } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import { Icon } from "../components/icons.jsx";
import BoardSettings from "../components/boards/BoardSettings.jsx";
import BoardView, { Card } from "../components/boards/BoardView.jsx";
import CalendarView from "../components/boards/CalendarView.jsx";
import ItemDrawer from "../components/boards/ItemDrawer.jsx";
import ListView from "../components/boards/ListView.jsx";
import NewBoard from "../components/boards/NewBoard.jsx";
import Requests, { RequestDialog } from "../components/boards/Requests.jsx";

/* Boards (roadmap #59): work on boards with their own item types and columns, in several views (board, team lanes,
   list, calendar), assigned to people and teams, plus "My work" across every board and the sales pipeline, and the
   task requests to decide. Items also live in Alfred's agenda, the briefing and on Telegram. */

const VIEWS = [["board", "Board"], ["lanes", "Team lanes"], ["list", "List"], ["calendar", "Calendar"]];
const GROUPS = [["overdue", "Overdue"], ["today", "Today"], ["week", "Next 7 days"], ["later", "Later"], ["none", "No due date"]];

function bucket(c, today, week) {
  if (!c.due_date) return "none";
  if (c.due_date < today) return "overdue";
  if (c.due_date === today) return "today";
  return c.due_date <= week ? "week" : "later";
}

function MyWork({ onOpen }) {
  const [teams, setTeams] = useStoredState("alfred-boards-myteams", "false");
  const { data, error } = usePoll(() => api(`/boards/mine?teams=${teams === "true"}`), 30000, [teams]);
  const today = new Date().toLocaleDateString("sv-SE");
  const w = new Date(); w.setDate(w.getDate() + 7);
  const week = w.toLocaleDateString("sv-SE");
  const items = data || [];
  return (
    <div className="bd-mine">
      <div className="mail-toolbar">
        <div className="mail-filters"><b className="mail-title">My work</b>
          <label className="mail-unread-toggle"><input type="checkbox" checked={teams === "true"} onChange={(e) => setTeams(String(e.target.checked))} /> Also my teams' unassigned items</label>
        </div>
      </div>
      <p className="muted small">Everything assigned to you on every board, and the deals you own. Due dates are also in your agenda and in the morning briefing.</p>
      <ErrorNote error={error} />
      {data && items.length === 0 && (
        <div className="crm-empty"><Icon name="check" className="crm-empty-ico" /><p><b>Nothing on your plate.</b></p>
          <p className="muted small">Items assigned to you on any board show up here.</p></div>
      )}
      {GROUPS.map(([k, l]) => {
        const list = items.filter((c) => bucket(c, today, week) === k);
        if (!list.length) return null;
        return (
          <section key={k} className={`bd-mine-group g-${k}`}>
            <h3 className="sub">{l} <span className="mail-count plain">{list.length}</span></h3>
            <div className="bd-mine-cards">
              {list.map((c) => (
                <div key={c.key} className="bd-mine-row">
                  <span className="meta bd-mine-board">{c.board_name} · {c.state_name}</span>
                  <Card c={c} onOpen={() => onOpen(c)} />
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export default function Boards({ me, assistant }) {
  const active = usePageActive();
  const { data: list, error: listErr, reload: reloadList } = usePoll(() => api("/boards"), 60000);
  const [sel, setSel] = useStoredState("alfred-boards-sel", "mine");
  const [view, setView] = useStoredState("alfred-boards-view", "board");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [fType, setFType] = useState("");
  const [fWho, setFWho] = useState("");
  const [fTeam, setFTeam] = useState("");
  const [showDone, setShowDone] = useStoredState("alfred-boards-done", "true");
  const [drawer, setDrawer] = useState(null);           // {id, boardId}
  const [drawerBoard, setDrawerBoard] = useState(null);
  const [dialog, setDialog] = useState(null);           // "new" | "settings" | "request"
  const [quick, setQuick] = useState("");
  const [toast, setToast] = useState("");
  const [err, setErr] = useState("");
  const [rev, setRev] = useState(0);
  const searchRef = useRef(null);
  const quickRef = useRef(null);
  const boards = list?.boards || [];
  const isBoard = sel !== "mine" && sel !== "requests";
  const current = boards.find((b) => String(b.id) === String(sel));

  useEffect(() => {                                  // a board that's gone (or not shared any more): back to My work
    if (list && isBoard && !current) setSel("mine");
  }, [list, isBoard, current, setSel]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(""), 5000); return () => clearTimeout(t); }, [toast]);

  const { data: board, error: boardErr, reload: reloadBoard } = usePoll(() => (isBoard && current ? api(`/boards/${sel}`) : Promise.resolve(null)), 0, [sel, isBoard && !!current, rev]);
  const itemsUrl = useMemo(() => {
    if (!isBoard || !current) return null;
    const p = new URLSearchParams({ done: showDone });
    if (query) p.set("q", query);
    if (fType) p.set("type_id", fType);
    if (fWho) p.set("assignee_id", fWho === "me" ? String(me?.id || 0) : fWho);
    if (fTeam) p.set("team_id", fTeam);
    return `/boards/${sel}/items?${p}`;
  }, [isBoard, current, sel, showDone, query, fType, fWho, fTeam, me?.id]);
  const { data: items, error: itemsErr, reload: reloadItems } = usePoll(() => (itemsUrl ? api(itemsUrl) : Promise.resolve(null)), 30000, [itemsUrl, rev]);
  const changed = useCallback(() => { reloadItems(); reloadList(); }, [reloadItems, reloadList]);

  async function openItem(c) {
    const bid = c.board_id ?? sel;
    setDrawer({ id: c.id, boardId: bid, card: c.kind === "deal" ? c : null });
    if (String(bid) === String(sel) && board) { setDrawerBoard(null); return; }
    try { setDrawerBoard(await api(`/boards/${bid}`)); } catch (e) { setErr(e.message); }
  }
  useNavTarget("boards", (t) => {
    if (t.board) setSel(String(t.board));
    if (t.item) openItem({ id: t.item, board_id: t.board ?? null });
  });
  async function move(c, target) {
    setErr("");
    const body = { state_id: target.state_id, index: target.index };
    if (target.team_id !== undefined) body.team_id = target.team_id;
    try { await api(`/boards/${sel}/items/${c.id}/move`, { method: "POST", body }); changed(); }
    catch (e) { setErr(e.message); reloadItems(); }
  }
  async function quickAdd(fields) {
    setErr("");
    try {
      const it = await api(`/boards/${sel}/items`, { method: "POST", body: { fields } });
      changed(); setToast(`Added ${it.key}`);
      return true;
    } catch (e) { setErr(e.message); return false; }
  }
  async function quickSubmit(e) {
    e.preventDefault();
    if (!quick.trim()) return;
    if (await quickAdd({ title: quick.trim(), ...(fTeam ? { team_id: Number(fTeam) } : {}), ...(fWho === "me" ? { assignee_id: me?.id } : {}) })) setQuick("");
  }

  useEffect(() => {
    if (!active) return undefined;
    const onKey = (e) => {
      if (dialog || e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/" && isBoard) { e.preventDefault(); searchRef.current?.focus(); }
      else if (e.key === "n" && isBoard) { e.preventDefault(); quickRef.current?.focus(); }
      else if (/^[1-4]$/.test(e.key) && isBoard) setView(VIEWS[Number(e.key) - 1][0]);
      else if (e.key === "Escape" && drawer) setDrawer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const level = board?.level;
  const canEdit = level === "contribute" || level === "manage";
  const lanes = useMemo(() => {
    if (!board) return null;
    const used = new Set((items || []).map((c) => c.team_id || 0));
    // Lanes: the teams with items here, plus one's own teams (somewhere to drop work for them).
    const ls = (board.teams || []).filter((t) => (fTeam ? String(t.id) === fTeam : used.has(t.id) || t.members?.includes(me?.id)))
      .map((t) => ({ id: t.id, name: t.name, color: t.color, manager_name: t.manager_name }));
    if (!fTeam) ls.push({ id: 0, name: "No team", color: "" });
    return ls;
  }, [board, items, fTeam, me?.id]);
  const drawerMeta = drawer ? (drawerBoard && String(drawerBoard.id) === String(drawer.boardId) ? drawerBoard : board) : null;
  const filtered = !!(query || fType || fWho || fTeam);

  return (
    <Panel code="15" title="Boards" actions={(
      <>
        {isBoard && level === "request" && <button className="btn small" onClick={() => setDialog("request")}><Icon name="send" className="btn-ico" />Ask the team</button>}
        {list?.can?.create && <button className="btn primary small" onClick={() => setDialog("new")}><Icon name="plus" className="btn-ico" />New board</button>}
      </>
    )}>
      <p className="muted small mail-intro">Your team's work on boards: assign items to people or whole teams, set deadlines (they land in
        the agenda and the briefing) and attach company files. Tell {assistant} too: “add a task for Erika on the website board: logo draft by Friday”,
        “what's on my plate?”, “move WEB-12 to review”.</p>
      <div className={`mail-layout bd-layout${drawer ? " has-drawer" : ""}`}>
        <nav className="mail-side" aria-label="Boards">
          <button type="button" className={`mail-side-box${sel === "mine" ? " active" : ""}`} onClick={() => setSel("mine")}>
            <Icon name="profile" className="lab-ico" />My work<span className="mail-count plain">{list?.mine ?? ""}</span></button>
          <button type="button" className={`mail-side-box${sel === "requests" ? " active" : ""}`} onClick={() => setSel("requests")}>
            <Icon name="inbox" className="lab-ico" />Requests{list?.requests_to_decide > 0 && <b className="mail-count">{list.requests_to_decide}</b>}</button>
          <div className="mail-side-sec">
            <div className="mail-side-head"><span>Boards</span></div>
            {boards.map((b) => (
              <button key={b.id} type="button" className={`mail-side-pick bd-side-board${String(sel) === String(b.id) ? " on" : ""}`} onClick={() => { setSel(String(b.id)); setDrawer(null); }}>
                <span className={`bd-side-key${b.kind === "pipeline" ? " deal" : ""}`}>{b.key}</span><span className="bd-side-name">{b.name}</span>
                <span className="mail-count muted">{b.open || ""}</span>
              </button>
            ))}
            {list && boards.length === 0 && <p className="muted tiny bd-side-empty">No boards yet.{list.can?.create ? " Create the first one." : ""}</p>}
          </div>
        </nav>

        <section className="mail-main bd-main">
          {toast && <div className="notice" role="status" onClick={() => setToast("")}>{toast}</div>}
          <ErrorNote error={listErr || err} />
          {sel === "mine" ? <MyWork onOpen={openItem} />
            : sel === "requests" ? <Requests onOpenItem={(id, bid) => openItem({ id, board_id: bid })} onChanged={reloadList} />
              : !current ? <Empty>Pick a board.</Empty> : (
                <>
                  <div className="mail-toolbar bd-toolbar">
                    <div className="mail-filters">
                      <b className="mail-title">{current.name}</b>
                      <div className="seg bd-views" role="tablist" aria-label="View">
                        {VIEWS.map(([k, l], i) => <button key={k} role="tab" aria-selected={view === k} className={view === k ? "active" : ""} title={`${l} (${i + 1})`} onClick={() => setView(k)}>{l}</button>)}
                      </div>
                      <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
                        <input ref={searchRef} value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} placeholder="Search items… ( / )" aria-label="Search items" />
                        {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
                      </form>
                    </div>
                    {level === "manage" && board?.kind === "board" && <button className="btn ghost small" onClick={() => setDialog("settings")}><Icon name="settings" className="btn-ico" />Board settings</button>}
                  </div>
                  <div className="bd-filters">
                    {board?.types?.filter((t) => !t.archived).length > 1 && (
                      <select className="crm-sort" value={fType} onChange={(e) => setFType(e.target.value)} aria-label="Type">
                        <option value="">All types</option>{board.types.filter((t) => !t.archived).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                      </select>
                    )}
                    <select className="crm-sort" value={fWho} onChange={(e) => setFWho(e.target.value)} aria-label="Assigned to">
                      <option value="">Anyone</option><option value="me">Me</option><option value="-1">Nobody yet</option>
                      {(board?.people || []).filter((p) => p.id !== me?.id).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
                    </select>
                    <select className="crm-sort" value={fTeam} onChange={(e) => setFTeam(e.target.value)} aria-label="Team">
                      <option value="">All teams</option>{(board?.teams || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                    <label className="mail-unread-toggle"><input type="checkbox" checked={showDone === "true"} onChange={(e) => setShowDone(String(e.target.checked))} /> Done (30 days)</label>
                    {filtered && <button className="btn ghost small" onClick={() => { setQ(""); setQuery(""); setFType(""); setFWho(""); setFTeam(""); }}>Clear filters</button>}
                    {canEdit && board?.kind === "board" && (
                      <form className="bd-quick-top" onSubmit={quickSubmit}>
                        <input ref={quickRef} value={quick} maxLength={200} onChange={(e) => setQuick(e.target.value)} aria-label="Quick add"
                               placeholder={`+ New item${fWho === "me" ? " for me" : ""}${fTeam ? " for this team" : ""}… ( n )`} />
                      </form>
                    )}
                  </div>
                  {level === "request" && <p className="crm-callout"><span className="crm-callout-label">You can ask</span>
                    <span className="small">You can't add items to this board yourself, but you can ask the team: <button type="button" className="linkish" onClick={() => setDialog("request")}>send a request</button>.</span></p>}
                  <ErrorNote error={boardErr || itemsErr} />
                  {board && items && (
                    items.length === 0 && !filtered && view !== "calendar" ? (
                      <div className="crm-empty"><Icon name="tasks" className="crm-empty-ico" /><p><b>No items yet.</b></p>
                        <p className="muted small">{canEdit ? "Type one above and press Enter, or ask " + assistant + "." : "The team hasn't added anything yet."}</p></div>
                    ) : view === "list" ? <ListView board={board} items={items} canEdit={canEdit} onOpen={openItem} onState={(c, s) => move(c, { state_id: s })} />
                      : view === "calendar" ? <CalendarView items={items} canEdit={canEdit} onOpen={openItem}
                                                            onDue={async (c, d) => { try { await api(`/boards/items/${c.id}`, { method: "PATCH", body: { fields: { due_date: d } } }); changed(); } catch (e) { setErr(e.message); } }} />
                        : <BoardView board={board} items={items} lanes={view === "lanes" ? lanes : null} canEdit={canEdit} onOpen={openItem} onMove={move} onQuickAdd={quickAdd} />
                  )}
                  {items && <p className="muted tiny mail-keys">Keys: / search · n new item · 1–4 views · Esc close. {canEdit ? "Drag cards between columns" + (view === "lanes" ? " and teams." : ".") : ""}</p>}
                </>
              )}
        </section>
        {drawer && drawerMeta && (
          <ItemDrawer key={`${drawer.boardId}:${drawer.id}`} id={drawer.id} me={me} card={drawer.card}
                      board={{ ...drawerMeta, items: String(drawerMeta.id) === String(sel) ? items : drawerMeta.items }}
                      onClose={() => setDrawer(null)} onChanged={changed} onToast={setToast}
                      onOpenItem={(id) => setDrawer({ id, boardId: drawer.boardId })} />
        )}
      </div>
      {dialog === "new" && <NewBoard templates={list?.templates} onClose={() => setDialog(null)}
                                     onCreated={(b) => { setDialog(null); reloadList(); setSel(String(b.id)); setToast(`Created ${b.name}`); }} />}
      {dialog === "settings" && board && <BoardSettings board={board} onClose={() => setDialog(null)} onChanged={() => { reloadBoard(); changed(); }} />}
      {dialog === "request" && board && <RequestDialog board={board} onClose={() => setDialog(null)}
                                                       onDone={() => { setDialog(null); setToast("Request sent to the team"); reloadList(); }} />}
    </Panel>
  );
}
