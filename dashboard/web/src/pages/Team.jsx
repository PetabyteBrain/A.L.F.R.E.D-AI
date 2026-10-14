import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { usePageActive, usePoll, useStoredState } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import { Icon } from "../components/icons.jsx";
import Approvals from "../components/crm/Approvals.jsx";
import { Avatar } from "../components/crm/RecordView.jsx";
import AbsenceDialog from "../components/team/AbsenceDialog.jsx";
import Checklists from "../components/team/Checklists.jsx";
import EmployeeEditor from "../components/team/EmployeeEditor.jsx";
import EmployeeView, { awayText, STATUS } from "../components/team/EmployeeView.jsx";
import TeamCalendar from "../components/team/TeamCalendar.jsx";
import TeamSettings from "../components/team/TeamSettings.jsx";
import Teams from "../components/team/Teams.jsx";
import PeopleAccounts, { AccessLog } from "./People.jsx";
import { can as canDo } from "../components/common.jsx";

/* Team (CRM stage 4, roadmap #58): the employee directory linked to Alfred's people, absences and holidays on a
   team calendar, onboarding and offboarding checklists, and the approvals for employee changes. Everyone with
   their own employee record sees "Me": their holiday balance and absences, and can ask for time off. */

export default function Team({ me: viewer, assistant }) {
  const active = usePageActive();
  const { data: st, reload: reloadStatus } = usePoll(() => api("/team/status"), 60000);
  const [saved, setSaved] = useStoredState("alfred-team-view", "directory");
  const [view, setViewState] = useState(saved);
  const [open, setOpen] = useState(null);                       // employee id
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [former, setFormer] = useState(false);
  const [editor, setEditor] = useState(null);                   // {employee?}
  const [absence, setAbsence] = useState(null);                 // {employee?}
  const [settings, setSettings] = useState(false);
  const [toast, setToast] = useState("");
  const [rev, setRev] = useState(0);
  const [account, setAccount] = useState(null);         // open this person's account (from their employee record)
  const searchRef = useRef(null);
  const can = st?.can || {};
  const me = st?.me;

  // Without the directory, "Me" is the start page.
  useEffect(() => {
    if (st && !can.view && (view === "directory" || view === "checklists")) setViewState(me ? "me" : "teams");
  }, [st, can.view, view, me]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(""), 6000); return () => clearTimeout(t); }, [toast]);

  const listUrl = can.view && view === "directory" ? `/team/employees?former=${former}${query ? `&q=${encodeURIComponent(query)}` : ""}` : null;
  const { data: people, error, reload } = usePoll(() => (listUrl ? api(listUrl) : Promise.resolve(null)), 60000, [listUrl, rev]);
  const setView = (v) => { setViewState(v); setSaved(v); setOpen(null); };
  const changed = () => { reload(); reloadStatus(); setRev((n) => n + 1); };
  const shown = view === "me" && me ? me.id : open;

  useEffect(() => {
    if (!active) return undefined;
    const onKey = (e) => {
      if (editor || absence || settings || e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/" && view === "directory") { e.preventDefault(); searchRef.current?.focus(); }
      else if (e.key === "Escape" && open) setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <Panel code="14" title="Team" actions={(
      <>
        {(can.self || can.manage) && <button className="btn small" onClick={() => setAbsence({})}><Icon name="calendar" className="btn-ico" />Request time off</button>}
        {can.manage && <button className="btn primary small" onClick={() => setEditor({})}><Icon name="plus" className="btn-ico" />New employee</button>}
      </>
    )}>
      <p className="muted small mail-intro">
        Who works here, who's away, and what's left to do when someone joins or leaves. Changes to employee data and time off
        wait for approval. Ask {assistant} too: “book me a holiday from 2 to 6 November”, “who's off next week?”.
      </p>
      <div className={`mail-layout crm-layout${shown && view !== "me" ? " has-open" : ""}`}>
        <nav className="mail-side" aria-label="Team views">
          {me && (
            <button type="button" className={`mail-side-box${view === "me" ? " active" : ""}`} onClick={() => setView("me")}>
              <Icon name="profile" className="lab-ico" />Me
              <span className="mail-count plain" title="Holiday days left">{me.balance.remaining}</span>
            </button>
          )}
          {can.view && (
            <button type="button" className={`mail-side-box${view === "directory" ? " active" : ""}`} onClick={() => setView("directory")}>
              <Icon name="people" className="lab-ico" />Directory
              <span className="mail-count plain">{st?.count ?? ""}</span>
            </button>
          )}
          <button type="button" className={`mail-side-box${view === "calendar" ? " active" : ""}`} onClick={() => setView("calendar")}>
            <Icon name="calendar" className="lab-ico" />Absences
            {st?.absences_pending > 0 && <b className="mail-count" title="Waiting for approval">{st.absences_pending}</b>}
          </button>
          <button type="button" className={`mail-side-box${view === "checklists" ? " active" : ""}`} onClick={() => setView("checklists")}>
            <Icon name="tasks" className="lab-ico" />Checklists
            {st?.my_steps_due > 0 && <b className="mail-count" title="Your steps due">{st.my_steps_due}</b>}
          </button>
          <button type="button" className={`mail-side-box${view === "teams" ? " active" : ""}`} onClick={() => setView("teams")}>
            <Icon name="people" className="lab-ico" />Teams</button>
          {canDo(viewer, "people.manage") && (
            <>
              <button type="button" className={`mail-side-box${view === "accounts" ? " active" : ""}`} onClick={() => setView("accounts")}>
                <Icon name="profile" className="lab-ico" />Accounts</button>
              <button type="button" className={`mail-side-box${view === "access" ? " active" : ""}`} onClick={() => setView("access")}>
                <Icon name="approve" className="lab-ico" />Access log</button>
            </>
          )}
          <button type="button" className={`mail-side-box${view === "approvals" ? " active" : ""}`} onClick={() => setView("approvals")}>
            <Icon name="approve" className="lab-ico" />Approvals
            {st?.approvals_pending > 0 && <b className="mail-count">{st.approvals_pending}</b>}
          </button>
          {can.view && st?.away_today?.length > 0 && (
            <div className="mail-side-sec team-away">
              <div className="mail-side-head"><span>Away today</span></div>
              {st.away_today.map((a) => (
                <div key={a.name} className="team-away-row"><span className="dot s-away" />
                  <span>{a.name}</span><span className="meta">{a.absence}{a.half_day ? " (½)" : ""}</span></div>
              ))}
            </div>
          )}
          {can.manage && (
            <button type="button" className="btn ghost small team-settings-btn" onClick={() => setSettings(true)}>
              <Icon name="settings" className="btn-ico" />Holidays &amp; checklists</button>
          )}
        </nav>

        <section className="mail-main">
          {toast && <div className="notice" role="status" onClick={() => setToast("")}>{toast}</div>}
          {shown && !["teams", "accounts", "access"].includes(view) ? (
            <EmployeeView key={`${shown}:${rev}`} id={shown} status={st} viewer={viewer} isMePage={view === "me"} onBack={view === "me" ? null : () => setOpen(null)}
                          onOpen={(id) => { setViewState("directory"); setOpen(id); }}
                          onEdit={(e) => setEditor({ employee: e })} onAbsence={(e) => setAbsence({ employee: e })}
                          onAccount={canDo(viewer, "people.manage") ? (uid) => { setAccount(uid); setView("accounts"); } : null}
                          onToast={setToast} onChanged={changed} />
          ) : view === "me" ? (
            <Empty>You're not in the employee directory yet. Ask whoever manages employees to add you.</Empty>
          ) : view === "calendar" ? (
            <TeamCalendar status={st} rev={rev} onOpen={can.view ? (id) => { setViewState("directory"); setOpen(id); } : null}
                          onToast={setToast} onChanged={changed} />
          ) : view === "teams" ? (
            <Teams onToast={setToast} />
          ) : view === "accounts" && canDo(viewer, "people.manage") ? (
            <PeopleAccounts me={viewer} assistant={assistant} openId={account} onOpened={() => setAccount(null)} />
          ) : view === "access" && canDo(viewer, "people.manage") ? (
            <AccessLog />
          ) : view === "checklists" ? (
            <Checklists status={st} onOpen={can.view ? (id) => { setViewState("directory"); setOpen(id); } : null} onChanged={changed} />
          ) : view === "approvals" ? (
            <Approvals area="employees" onChanged={changed}
                       intro="New employees, changes to their details, time off and cancelling approved time off wait here until someone with approval rights decides." />
          ) : (
            <>
              <div className="mail-toolbar">
                <div className="mail-filters">
                  <b className="mail-title">{former ? "Everyone, also former" : "Directory"}</b>
                  <label className="mail-unread-toggle"><input type="checkbox" checked={former} onChange={(e) => setFormer(e.target.checked)} /> Former employees</label>
                  <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
                    <input ref={searchRef} value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} aria-label="Search the team"
                           placeholder="Name, title, department…" />
                    {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
                  </form>
                </div>
              </div>
              <ErrorNote error={error} />
              {people && people.length === 0 && (
                query ? <Empty>Nobody matches.</Empty> : (
                  <div className="crm-empty">
                    <Icon name="people" className="crm-empty-ico" />
                    <p><b>No employees yet.</b></p>
                    <p className="muted small">Add the team here, linked to their Alfred accounts, or tell {assistant}: “add Erika Mustermann as a
                      designer starting 1 November, with onboarding”.</p>
                    {can.manage && <button className="btn primary small" onClick={() => setEditor({})}><Icon name="plus" className="btn-ico" />New employee</button>}
                  </div>
                )
              )}
              <div className="mail-list">
                {(people || []).map((e) => {
                  const [label, tone] = STATUS[e.status] || [e.status, ""];
                  return (
                    <button key={e.id} type="button" className="crm-row" onClick={() => setOpen(e.id)}>
                      <Avatar entity="contact" name={e.name} />
                      <span className="crm-row-main">
                        <span className="crm-row-top"><b className="crm-row-name">{e.name}</b>
                          {e.status !== "active" && <Tag kind={tone}>{label}</Tag>}
                          {e.away && <Tag kind="busy">{awayText(e.away)}</Tag>}</span>
                        <span className="meta crm-row-sub">{[e.job_title, e.department, e.employment !== "full_time" ? e.employment_label : null,
                          e.workload !== 100 ? `${e.workload} %` : null, e.manager_name ? `reports to ${e.manager_name}` : null].filter(Boolean).join(" · ")}</span>
                      </span>
                      <span className="meta crm-row-when">{e.user_name ? "" : "no Alfred account"}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>
      </div>
      {editor && <EmployeeEditor employee={editor.employee} status={st} onClose={() => setEditor(null)}
                                 onSaved={(a) => { setEditor(null); changed(); setToast(a.already_pending ? "That change is already waiting for approval." : `Sent for approval: ${a.summary}`); }} />}
      {absence && <AbsenceDialog employee={absence.employee} status={st} onClose={() => setAbsence(null)}
                                 onSaved={(out) => { setAbsence(null); changed(); setToast(`Waiting for approval: ${out.approval.summary}`); }} />}
      {settings && <TeamSettings status={st} onClose={() => setSettings(false)} onChanged={changed} />}
    </Panel>
  );
}
