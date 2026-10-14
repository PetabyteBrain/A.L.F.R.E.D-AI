import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtAgo, fmtPhone } from "../format.js";
import { useNavTarget, usePageActive, usePoll, useStoredState } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import { Icon } from "../components/icons.jsx";
import Approvals from "../components/crm/Approvals.jsx";
import Billing from "../components/crm/Billing.jsx";
import BillingDoc from "../components/crm/BillingDoc.jsx";
import BillingSettings from "../components/crm/BillingSettings.jsx";
import DealEditor from "../components/crm/DealEditor.jsx";
import DealView from "../components/crm/DealView.jsx";
import EmailDialog from "../components/crm/EmailDialog.jsx";
import FollowUps from "../components/crm/FollowUps.jsx";
import ProactiveSettings from "../components/crm/ProactiveSettings.jsx";
import Reports from "../components/crm/Reports.jsx";
import Leads from "../components/crm/Leads.jsx";
import Pipeline from "../components/crm/Pipeline.jsx";
import StagesDialog from "../components/crm/StagesDialog.jsx";
import RecordEditor, { STATUS_LABEL } from "../components/crm/RecordEditor.jsx";
import RecordView, { Avatar, STATUS_TONE } from "../components/crm/RecordView.jsx";

/* CRM (roadmap #58): the sales pipeline, follow-ups, companies and the people at them (each with a timeline),
   quotes and invoices, suggested leads, reports, and Approvals for anything that needs a second look. Same layout
   as Mail: views on the left, a list or the board, and a record opened in place. */

const PAGE = 50;
const VIEWS = [
  { id: "company", label: "Companies", icon: "building", count: "company" },
  { id: "contact", label: "People", icon: "user", count: "contact" },
];

function Toast({ toast, onUndo, onClose }) {
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(onClose, 8000);
    return () => clearTimeout(t);
  }, [toast, onClose]);
  if (!toast) return null;
  return (
    <div className="crm-toast" role="status">
      <span>{toast.text}</span>
      {toast.changeId && <button className="btn small" onClick={onUndo}><Icon name="undo" className="btn-ico" />Undo</button>}
      <button className="btn ghost small" onClick={onClose} aria-label="Dismiss">✕</button>
    </div>
  );
}

export default function CRM({ me, assistant }) {
  const active = usePageActive();
  const { data: st, reload: reloadStatus } = usePoll(() => api("/crm/status"), 60000);
  const [savedView, setSavedView] = useStoredState("alfred-crm-view", "pipeline");
  const [view, setViewState] = useState(savedView);                      // pipeline | company | contact | leads | approvals
  const [statusFilter, setStatusFilter] = useState("");
  const [tag, setTag] = useState("");
  const [mine, setMine] = useState(false);
  const [archived, setArchived] = useState(false);
  const [sort, setSort] = useStoredState("alfred-crm-sort", "name");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState(null);                                // {entity, id}
  const [editor, setEditor] = useState(null);                            // {entity, record?, presetCompany?}
  const [stagesOpen, setStagesOpen] = useState(false);
  const [billingSettings, setBillingSettings] = useState(false);
  const [proactiveSettings, setProactiveSettings] = useState(false);
  const [emailFor, setEmailFor] = useState(null);                        // {entity, record, after}
  const [toast, setToast] = useState(null);
  const [viewRev, setViewRev] = useState(0);                             // reloads the open record after edits/undo
  const [showFilters, setShowFilters] = useState(false);                 // phones: filters folded away
  const [err, setErr] = useState("");
  const searchRef = useRef(null);
  const listing = view === "company" || view === "contact";
  const canEdit = !!st?.can?.edit;
  const canBilling = !!st?.can?.billing;
  const { data: bill, reload: reloadBill } = usePoll(() => (canBilling ? api("/billing/status") : Promise.resolve(null)), 60000, [canBilling]);
  async function newDoc(kind, preset = {}) {
    setErr("");
    try {
      const d = await api("/billing/docs", { method: "POST", body: { fields: { kind, ...preset } } });
      setViewState("billing"); setOpen({ entity: "billing", id: d.id }); reloadBill();
    } catch (e) { setErr(e.message); }
  }

  useNavTarget("crm", (t) => {                            // "open this deal / client" from a board item
    if (t.deal) setOpen({ entity: "deal", id: Number(t.deal) });
    else if (t.company) setOpen({ entity: "company", id: Number(t.company) });
  });
  const setView = (v) => { setViewState(v); if (!["approvals", "leads", "followups"].includes(v)) setSavedView(v); setOpen(null); setOffset(0); };
  const openEmail = (entity, record, after) => setEmailFor({ entity, record, after });
  const url = useMemo(() => {
    if (!listing) return null;
    const p = new URLSearchParams({ offset: String(offset), limit: String(PAGE), sort });
    if (query) p.set("q", query);
    if (statusFilter && view === "company") p.set("status", statusFilter);
    if (tag) p.set("tag", tag);
    if (mine) p.set("mine", "true");
    if (archived) p.set("archived", "true");
    return `/crm/${view === "company" ? "companies" : "contacts"}?${p}`;
  }, [listing, view, offset, sort, query, statusFilter, tag, mine, archived]);
  const { data, error, reload } = usePoll(() => (url ? api(url) : Promise.resolve(null)), 30000, [url]);
  const items = data?.items || [];
  const changed = useCallback(() => { reload(); reloadStatus(); }, [reload, reloadStatus]);
  const billChanged = () => { reloadBill(); reloadStatus(); };
  const showToast = useCallback((text, changeId = null) => setToast({ text, changeId, at: Date.now() }), []);

  async function undo() {
    const id = toast?.changeId;
    setToast(null);
    try { const out = await api("/crm-actions/undo", { method: "POST", body: { change_id: id } }); showToast(`Undone: ${out.what}`); changed(); setViewRev((n) => n + 1); }
    catch (e) { setErr(e.message); }
  }

  // Keyboard: / search, n new, Esc back (not while typing).
  useEffect(() => {
    if (!active) return undefined;
    const onKey = (e) => {
      if (editor || e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); }
      else if (e.key === "n" && canEdit && (listing || view === "pipeline")) setEditor({ entity: listing ? view : "deal" });
      else if (e.key === "Escape" && open) setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const counts = st?.counts || {};
  const statuses = st?.statuses || [];
  const filtered = !!(statusFilter || tag || mine || archived || query);
  const noun = view === "company" ? "companies" : "people";
  const title = archived ? `Archived ${noun}` : noun[0].toUpperCase() + noun.slice(1);

  return (
    <Panel code="13" title="CRM" actions={canEdit && (
      <>
        <button className="btn small" onClick={() => setEditor({ entity: "contact" })}><Icon name="user" className="btn-ico" />New person</button>
        <button className="btn small" onClick={() => setEditor({ entity: "company" })}><Icon name="building" className="btn-ico" />New company</button>
        <button className="btn primary small" onClick={() => setEditor({ entity: "deal" })}><Icon name="crm" className="btn-ico" />New deal</button>
      </>
    )}>
      <p className="muted small mail-intro">
        Clients, leads and partners with everyone you deal with there, and a timeline of every note, call, meeting and
        email. Ask {assistant} too: “add Erika Mustermann from Acme”, “what's open with Acme?”, “log that I called them”.
      </p>
      <div className={`mail-layout crm-layout${open ? " has-open" : ""}`}>
        <nav className="mail-side" aria-label="CRM views">
          <button type="button" className={`mail-side-box${view === "pipeline" ? " active" : ""}`} onClick={() => setView("pipeline")}>
            <Icon name="crm" className="lab-ico" />Pipeline
            <span className="mail-count plain">{st?.deals_open ?? ""}</span>
          </button>
          <button type="button" className={`mail-side-box${view === "followups" ? " active" : ""}`} onClick={() => setView("followups")}>
            <Icon name="check" className="lab-ico" />Follow-ups
            {st?.follow_ups > 0 && <b className="mail-count">{st.follow_ups}</b>}
          </button>
          {VIEWS.map((v) => (
            <button key={v.id} type="button" className={`mail-side-box${view === v.id ? " active" : ""}`} onClick={() => setView(v.id)}>
              <Icon name={v.icon} className="lab-ico" />{v.label}
              <span className="mail-count plain">{counts[v.count] ?? ""}</span>
            </button>
          ))}
          {canBilling && (
            <button type="button" className={`mail-side-box${view === "billing" ? " active" : ""}`} onClick={() => setView("billing")}>
              <Icon name="doc" className="lab-ico" />Quotes &amp; invoices
              {bill?.summary?.overdue_count > 0 ? <b className="mail-count warn" title="Overdue invoices">{bill.summary.overdue_count}</b>
                : bill?.summary?.unpaid_count > 0 ? <span className="mail-count plain">{bill.summary.unpaid_count}</span> : null}
            </button>
          )}
          {canEdit && Object.keys(st?.leads || {}).length > 0 && (
            <button type="button" className={`mail-side-box${view === "leads" ? " active" : ""}`} onClick={() => setView("leads")}>
              <Icon name="inbox" className="lab-ico" />Leads
              {Object.values(st.leads).reduce((a, b) => a + b, 0) > 0 && <b className="mail-count">{Object.values(st.leads).reduce((a, b) => a + b, 0)}</b>}
            </button>
          )}
          {st?.can?.reports && (
            <button type="button" className={`mail-side-box${view === "reports" ? " active" : ""}`} onClick={() => setView("reports")}>
              <Icon name="usage" className="lab-ico" />Reports
            </button>
          )}
          <button type="button" className={`mail-side-box${view === "approvals" ? " active" : ""}`} onClick={() => setView("approvals")}>
            <Icon name="approve" className="lab-ico" />Approvals
            {st?.approvals_pending > 0 ? <b className="mail-count">{st.approvals_pending}</b>
              : st?.my_requests_pending > 0 ? <span className="mail-count plain">{st.my_requests_pending}</span> : null}
          </button>

          {listing && (
            <button type="button" className="mail-side-toggle btn ghost small" onClick={() => setShowFilters(!showFilters)}
                    aria-expanded={showFilters}>
              Filters{[statusFilter, tag, mine, archived].filter(Boolean).length ? ` (${[statusFilter, tag, mine, archived].filter(Boolean).length})` : ""} {showFilters ? "▴" : "▾"}
            </button>
          )}
          <div className={`mail-side-labels${showFilters ? " open" : ""}`}>
          {listing && view === "company" && (
            <div className="mail-side-sec">
              <div className="mail-side-head"><span>Status</span></div>
              <button type="button" className={`mail-side-pick${!statusFilter ? " on" : ""}`} onClick={() => { setStatusFilter(""); setOffset(0); }}>
                <span className="dot s-all" />All</button>
              {statuses.map((s) => (
                <button key={s} type="button" className={`mail-side-pick${statusFilter === s ? " on" : ""}`}
                        onClick={() => { setStatusFilter(statusFilter === s ? "" : s); setOffset(0); }}>
                  <span className={`dot s-${s}`} /><span>{STATUS_LABEL[s] || s}</span>
                  <span className="mail-count muted">{counts.statuses?.[s] || ""}</span>
                </button>
              ))}
            </div>
          )}
          {listing && (st?.tags || []).length > 0 && (
            <div className="mail-side-sec">
              <div className="mail-side-head"><span>Tags</span></div>
              <div className="pick-chips crm-side-tags">
                {st.tags.map((t) => (
                  <button key={t} type="button" className={`chip${tag === t ? "" : " off"}`} aria-pressed={tag === t}
                          onClick={() => { setTag(tag === t ? "" : t); setOffset(0); }}>{t}</button>
                ))}
              </div>
            </div>
          )}
          {listing && (
            <div className="mail-side-sec crm-side-toggles">
              <label className="mail-unread-toggle"><input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setOffset(0); }} /> Only mine</label>
              <label className="mail-unread-toggle"><input type="checkbox" checked={archived} onChange={(e) => { setArchived(e.target.checked); setOffset(0); }} /> Archive</label>
            </div>
          )}
          </div>
        </nav>

        <section className="mail-main">
          {open?.entity === "billing" ? (
            <BillingDoc key={`bill:${open.id}`} id={open.id} billing={bill} onBack={() => setOpen(null)}
                        onOpenRecord={(entity, id) => setOpen({ entity, id })} onOpenDoc={(id) => setOpen({ entity: "billing", id })}
                        onToast={showToast} onChanged={billChanged} />
          ) : view === "billing" && !open ? (
            <Billing billing={bill} onOpen={(id) => setOpen({ entity: "billing", id })} onNew={(kind) => newDoc(kind)}
                     onSettings={() => setBillingSettings(true)} />
          ) : open?.entity === "deal" ? (
            <DealView key={`deal:${open.id}:${viewRev}`} id={open.id} status={st} onBack={() => setOpen(null)}
                      canBilling={canBilling} onNewDoc={(kind, preset) => newDoc(kind, preset)}
                      onOpenDoc={(id) => setOpen({ entity: "billing", id })}
                      onOpen={(entity, id) => setOpen({ entity, id })} onEdit={(deal) => setEditor({ entity: "deal", record: deal })}
                      onEmail={canEdit ? openEmail : null} me={me} onToast={showToast} onChanged={changed} />
          ) : view === "approvals" && !open ? <Approvals onChanged={changed} />
            : view === "followups" && !open ? (
              <FollowUps status={st} onOpen={(entity, id) => setOpen({ entity, id })} onOpenDoc={(id) => setOpen({ entity: "billing", id })}
                         onEmail={openEmail} onToast={showToast} onChanged={reloadStatus} onSettings={() => setProactiveSettings(true)} />
            ) : view === "reports" && !open ? (
              <Reports onOpen={(entity, id) => setOpen({ entity, id })} canSettings={!!st?.can?.settings}
                       onSettings={() => setProactiveSettings(true)} />
            )
            : view === "leads" && !open ? <Leads status={st} onOpen={(entity, id) => setOpen({ entity, id })} onToast={showToast} onChanged={changed} />
            : view === "pipeline" && !open ? (
              <>
                <div className="mail-toolbar">
                  <div className="mail-filters">
                    <b className="mail-title">Pipeline</b>
                    <label className="mail-unread-toggle"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
                    <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
                      <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} aria-label="Search deals"
                             placeholder="Search deals or companies…" />
                      {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
                    </form>
                  </div>
                </div>
                <Pipeline status={st} mine={mine} q={query} rev={viewRev} onOpen={(id) => setOpen({ entity: "deal", id })}
                          onToast={showToast} onChanged={changed} onStages={() => setStagesOpen(true)} />
              </>
            ) : open ? (
            <RecordView key={`${open.entity}:${open.id}:${viewRev}`} entity={open.entity} id={open.id} status={st}
                        onBack={() => setOpen(null)} onOpen={(entity, id) => setOpen({ entity, id })}
                        onEdit={(record) => setEditor({ entity: open.entity, record })}
                        onAddContact={(company) => setEditor({ entity: "contact", presetCompany: { id: company.id, name: company.name } })}
                        onAddDeal={(company) => setEditor({ entity: "deal", presetCompany: { id: company.id, name: company.name } })}
                        canBilling={canBilling} onNewDoc={(kind, preset) => newDoc(kind, preset)}
                        onOpenDoc={(id) => setOpen({ entity: "billing", id })} onEmail={canEdit ? openEmail : null}
                        me={me} onToast={showToast} onChanged={changed} />
          ) : (
            <>
              <div className="mail-toolbar">
                <div className="mail-filters">
                  <b className="mail-title">{title}</b>
                  {filtered && <button className="btn ghost small" onClick={() => { setStatusFilter(""); setTag(""); setMine(false); setArchived(false); setQ(""); setQuery(""); setOffset(0); }}>Clear filters</button>}
                  <select className="crm-sort" value={sort} onChange={(e) => { setSort(e.target.value); setOffset(0); }} aria-label="Sort">
                    <option value="name">A–Z</option>
                    <option value="recent">Recently changed</option>
                  </select>
                  <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); setOffset(0); }}>
                    <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} aria-label="Search the CRM"
                           placeholder={view === "company" ? "Search name, email, phone, tag…" : "Search name, company, email…"} />
                    {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
                  </form>
                </div>
                {data && data.total > PAGE && (
                  <div className="mail-pager">
                    <span className="muted tiny">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
                    <button className="btn small ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} aria-label="Previous page">‹</button>
                    <button className="btn small ghost" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)} aria-label="Next page">›</button>
                  </div>
                )}
              </div>
              <ErrorNote error={err || error} />
              {data && items.length === 0 && (
                filtered ? <Empty>Nothing matches. Try another search or clear the filters.</Empty> : (
                  <div className="crm-empty">
                    <Icon name={view === "company" ? "building" : "user"} className="crm-empty-ico" />
                    <p><b>{view === "company" ? "No companies yet." : "No people yet."}</b></p>
                    <p className="muted small">Add the first one, or tell {assistant}: “add Acme AG as a new lead, they're at acme.example.com”.</p>
                    {canEdit && <button className="btn primary small" onClick={() => setEditor({ entity: view })}>
                      <Icon name="plus" className="btn-ico" />{view === "company" ? "New company" : "New person"}</button>}
                  </div>
                )
              )}
              <div className="mail-list">
                {items.map((r) => (
                  <button key={r.id} type="button" className="crm-row" onClick={() => setOpen({ entity: view, id: r.id })}>
                    <Avatar entity={view} name={r.name} />
                    <span className="crm-row-main">
                      <span className="crm-row-top">
                        <b className="crm-row-name">{r.name}</b>
                        {view === "company" && <Tag kind={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status] || r.status}</Tag>}
                        {r.tags.slice(0, 3).map((t) => <span key={t} className="chip crm-tag">{t}</span>)}
                      </span>
                      <span className="meta crm-row-sub">
                        {view === "company"
                          ? [r.industry, r.contacts ? `${r.contacts} ${r.contacts === 1 ? "person" : "people"}` : null, r.owner_name].filter(Boolean).join(" · ")
                          : [r.title && r.company_name ? `${r.title}, ${r.company_name}` : r.title || r.company_name, r.email || fmtPhone(r.phone)].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span className="meta crm-row-when">{r.last_activity ? fmtAgo(r.last_activity) : ""}</span>
                  </button>
                ))}
              </div>
              {items.length > 0 && <p className="muted tiny mail-keys">Keys: / search · n new · Esc back</p>}
            </>
          )}
        </section>
      </div>

      {editor?.entity === "deal" && (
        <DealEditor deal={editor.record} presetCompany={editor.presetCompany} status={st} onClose={() => setEditor(null)}
                    onSaved={(out) => {
                      const isNew = !editor.record;
                      setEditor(null); changed(); setViewRev((n) => n + 1);
                      showToast(isNew ? `Added the deal ${out.record.title}` : `Saved ${out.record.title}`, out.change_id);
                      if (isNew && !editor.presetCompany) setOpen({ entity: "deal", id: out.record.id });
                    }} />
      )}
      {billingSettings && <BillingSettings onClose={() => setBillingSettings(false)} onChanged={billChanged} />}
      {proactiveSettings && <ProactiveSettings onClose={() => setProactiveSettings(false)} onChanged={reloadStatus} />}
      {emailFor && (
        <EmailDialog entity={emailFor.entity} record={emailFor.record} assistant={assistant} onClose={() => setEmailFor(null)}
                     onSent={(a) => { setEmailFor(null); reloadStatus(); showToast(a.already_pending ? "That email is already waiting for approval" : `Email to ${a.payload?.to || "the client"} sent for approval`); }}
                     onNextStep={async (step) => {
                       try {
                         await api(`/crm/deals/${emailFor.record.id}`, { method: "PATCH", body: { fields: { next_step: step } } });
                         showToast(`Next step saved: ${step}`); emailFor.after?.(); changed();
                       } catch (e) { setErr(e.message); }
                     }} />
      )}
      {stagesOpen && <StagesDialog onClose={() => setStagesOpen(false)} onChanged={() => { reloadStatus(); setViewRev((n) => n + 1); }} />}
      {editor && editor.entity !== "deal" && (
        <RecordEditor entity={editor.entity} record={editor.record} presetCompany={editor.presetCompany} status={st}
                      onClose={() => setEditor(null)}
                      onOpen={(entity, id) => { setEditor(null); setViewState(entity); setOpen({ entity, id }); }}
                      onSaved={(out) => {
                        const isNew = !editor.record;
                        setEditor(null); changed(); setViewRev((n) => n + 1);
                        showToast(isNew ? `Added ${out.record.name}` : `Saved ${out.record.name}`, out.change_id);
                        if (isNew && !(open && editor.presetCompany)) setOpen({ entity: editor.entity, id: out.record.id });
                        if (isNew && editor.entity !== view && !editor.presetCompany) setViewState(editor.entity);
                      }} />
      )}
      <Toast toast={toast} onUndo={undo} onClose={() => setToast(null)} />
    </Panel>
  );
}
