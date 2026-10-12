"""CRM stage 2 (roadmap #58): suggested leads and contacts, for a person to review. Nothing is added by itself.

- From market analyses: every business on an analysis' shortlist becomes a suggestion (name, category, address,
  public website and business contact details, the fit score and why). Repeat runs don't suggest it again.
- From mail: senders of inquiries who aren't in the CRM yet become suggestions (their name and address; a company
  guessed from the address's domain unless it's a free-mail provider), one per address.

Who sees which: market suggestions need market.analysis (or the company files); mail suggestions need the Mail
tab (inquiries are company mail). Acting on them needs crm.edit. "Add" creates a company with status lead (and the
contact for mail) after the usual duplicate check; "Link" attaches it to an existing company instead; "Dismiss"
hides it for good.
"""
from __future__ import annotations

import json
import re

from . import crm, db
from .identity.registry import Person

CrmError = crm.CrmError


def _can_see(person: Person, source: str) -> bool:
    if source == "market":
        return person.can("market.analysis") or person.can("files.company")
    return person.can("mail.tab")


# --- collecting -----------------------------------------------------------------------------------

def add_market(businesses: list[dict], area: str, day: str) -> int:
    """Called by market_analysis.analyse with the shortlist. Returns how many new suggestions there are."""
    n = 0
    for b in businesses:
        if not b.get("osm_id") or not b.get("name"):
            continue
        address = "\n".join(x for x in (b.get("street"), " ".join(y for y in (b.get("postcode"), b.get("town")) if y)) if x)
        data = {"name": b["name"], "industry": b.get("category") or "", "website": b.get("website") or "",
                "phone": b.get("phone") or "", "email": b.get("email") or "", "address": address,
                "score": b.get("score"), "area": area}
        reason = f"Market analysis {area.split(',')[0]} ({day}), fit {b.get('score', '?')}: " + "; ".join(b.get("reasons") or [])
        n += db.execute_count("INSERT OR IGNORE INTO crm_suggestions(source, source_ref, data, reason, created_at) "
                              "VALUES ('market', ?, ?, ?, ?)",
                              (b["osm_id"], json.dumps(data, ensure_ascii=False), reason[:400], db.now_iso()))
    return n


def _known_addresses() -> set[str]:
    out = set()
    for t in ("crm_companies", "crm_contacts"):
        out |= {r["e"] for r in db.query(f"SELECT lower(email) AS e FROM {t} WHERE email != ''")}
    out |= {r["v"] for r in db.query("SELECT lower(value) AS v FROM user_identifiers WHERE type = 'email'")}
    return out


def _guess_company(addr: str) -> dict:
    domain = addr.split("@")[-1].lower()
    if domain in crm._FREEMAIL:
        return {}
    label = domain.split(".")[-2] if domain.count(".") >= 1 else domain
    name = " ".join(w.capitalize() for w in re.split(r"[-_]", label) if w)
    return {"company": name, "website": f"https://{domain}"}


def refresh_mail(limit: int = 200) -> int:
    """New suggestions from inquiries by senders not in the CRM (one per address)."""
    known = _known_addresses()
    rows = db.query("SELECT id, from_addr, from_name, subject, summary, created_at FROM emails WHERE direction = 'in' "
                    "AND kind = 'inquiry' AND from_addr != '' ORDER BY id DESC LIMIT ?", (limit,))
    n = 0
    for r in rows:
        addr = r["from_addr"].strip().lower()
        if addr in known:
            continue
        name = " ".join(str(r["from_name"] or "").replace('"', "").split())[:120]
        first, _, last = name.partition(" ")
        data = {"first_name": first, "last_name": last, "email": addr, "email_id": r["id"], **_guess_company(addr)}
        reason = f"Inquiry “{(r['subject'] or '(no subject)')[:80]}”" + (f": {r['summary'][:160]}" if r["summary"] else "")
        n += db.execute_count("INSERT OR IGNORE INTO crm_suggestions(source, source_ref, data, reason, created_at) "
                              "VALUES ('mail', ?, ?, ?, ?)", (addr, json.dumps(data, ensure_ascii=False), reason[:400],
                                                              r["created_at"]))
    return n


# --- reviewing ------------------------------------------------------------------------------------

def _view(row: dict) -> dict:
    r = dict(row)
    r["data"] = json.loads(r["data"] or "{}")
    d = r["data"]
    r["title"] = d.get("name") or " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x) or d.get("email", "")
    r["subtitle"] = (" · ".join(x for x in (d.get("industry"), d.get("address", "").replace("\n", ", "), d.get("website"))
                                if x) if r["source"] == "market"
                     else " · ".join(x for x in (d.get("email"), d.get("company")) if x))
    return r


def list_for(person: Person, *, source: str = "", status: str = "open", limit: int = 200) -> list[dict]:
    if not person.can("crm.edit"):
        raise CrmError("You don't have permission to add leads to the CRM.")
    if _can_see(person, "mail"):
        refresh_mail()
    sources = [s for s in ("market", "mail") if (not source or s == source) and _can_see(person, s)]
    if not sources:
        return []
    rows = db.query(f"SELECT * FROM crm_suggestions WHERE source IN ({','.join('?' * len(sources))})"
                    + (" AND status = ?" if status != "all" else "") + " ORDER BY id DESC LIMIT ?",
                    (*sources, *([status] if status != "all" else []), limit))
    return [_view(r) for r in rows]


def counts(person: Person) -> dict:
    if not person.can("crm.edit"):
        return {}
    if _can_see(person, "mail"):
        refresh_mail()
    out = {}
    for s in ("market", "mail"):
        if _can_see(person, s):
            out[s] = db.query_one("SELECT COUNT(*) AS n FROM crm_suggestions WHERE source = ? AND status = 'open'", (s,))["n"]
    return out


def _get(person: Person, sid: int) -> dict:
    row = db.query_one("SELECT * FROM crm_suggestions WHERE id = ?", (sid,))
    if not row or not _can_see(person, row["source"]):
        raise CrmError("No such suggestion.")
    if row["status"] != "open":
        raise CrmError(f"That suggestion was already {row['status']}.")
    return _view(row)


def _decide(sid: int, person: Person, status: str, company_id: int | None) -> None:
    db.execute("UPDATE crm_suggestions SET status = ?, company_id = ?, decided_by = ?, decided_at = ? WHERE id = ?",
               (status, company_id, person.id, db.now_iso(), sid))


def add(person: Person, sid: int, *, force: bool = False) -> dict:
    """Create the lead (company, status lead) and, for mail, the contact. Duplicates raise crm.DuplicateError."""
    s = _get(person, sid)
    d = s["data"]
    company = None
    if s["source"] == "market":
        fields = {k: d[k] for k in ("name", "industry", "website", "phone", "email", "address") if d.get(k)}
        company = crm.save(person, "company", {**fields, "status": "lead",
                                               "source": f"market analysis {d.get('area', '').split(',')[0]}".strip()},
                           force=force)["record"]
    else:
        person_fields = {"first_name": d.get("first_name", ""), "last_name": d.get("last_name", ""), "email": d.get("email", "")}
        if not force:                              # check the person first, so no half-made company is left behind
            dup = crm.duplicates(person, "contact", person_fields)
            if dup:
                raise crm.DuplicateError(dup)
        if d.get("company"):
            company = crm.save(person, "company", {"name": d["company"], "website": d.get("website", ""),
                                                   "status": "lead", "source": "email inquiry"}, force=force)["record"]
        crm.save(person, "contact", {"first_name": d.get("first_name", ""), "last_name": d.get("last_name", ""),
                                     "email": d.get("email", ""), "source": "email inquiry",
                                     **({"company_id": company["id"]} if company else {})}, force=force)
    _decide(sid, person, "added", company["id"] if company else None)
    return {"added": s["title"], "company": company}


def link(person: Person, sid: int, company_id: int) -> dict:
    """Attach the suggestion to an existing company: fill its empty details (market) or add the person (mail)."""
    s = _get(person, sid)
    co = crm.get(person, "company", company_id)
    d = s["data"]
    if s["source"] == "market":
        fill = {k: d[k] for k in ("industry", "website", "phone", "email", "address") if d.get(k) and not co.get(k)}
        if fill:
            crm.save(person, "company", fill, company_id)
    else:
        crm.save(person, "contact", {"first_name": d.get("first_name", ""), "last_name": d.get("last_name", ""),
                                     "email": d.get("email", ""), "source": "email inquiry", "company_id": company_id},
                 force=True)
    _decide(sid, person, "linked", company_id)
    return {"linked": s["title"], "company": {"id": co["id"], "name": co["name"]}}


def dismiss(person: Person, ids: list[int]) -> int:
    if not person.can("crm.edit"):
        raise CrmError("You don't have permission to do that in the CRM.")
    n = 0
    for sid in ids:
        try:
            _get(person, int(sid))
        except CrmError:
            continue
        _decide(int(sid), person, "dismissed", None)
        n += 1
    return n


def add_many(person: Person, ids: list[int]) -> dict:
    """Bulk "Add": everything without a likely duplicate is added; the rest come back for a decision."""
    added, duplicates, errors = [], [], []
    for sid in ids:
        try:
            added.append(add(person, int(sid))["added"])
        except crm.DuplicateError as e:
            duplicates.append({"id": int(sid), "matches": e.matches})
        except CrmError as e:
            errors.append({"id": int(sid), "error": str(e)})
    return {"added": added, "duplicates": duplicates, "errors": errors}
