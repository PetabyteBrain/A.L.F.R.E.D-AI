"""CRM stage 3 (roadmap #58): quotes and invoices with the Swiss QR-bill.

- Settings (billing.settings): the company's billing address, bank account (IBAN or QR-IBAN), VAT (on/off, VAT
  number, rates), payment terms, numbering, rounding to 5 Rappen, and the email texts. Products and services with
  a unit price and VAT rate fill invoice lines.
- A document is a quote or an invoice for a company (and optionally a contact and a deal). Drafts can be changed
  freely and discarded. Issuing gives it the next number (per kind and year, no gaps), freezes it, renders the PDF
  (orchestrator/invoicing_pdf.py; invoices carry a QR-bill) and files it in the company files. Issued documents are
  never changed: they are cancelled and written anew.
- Anything that goes to a client (sending a quote or invoice, a payment reminder) and cancelling an issued
  invoice is an approval draft (orchestrator/approvals.py, area "billing", decided by holders of
  billing.approve; self-approval allowed). Issuing without sending (to post it yourself) and recording payments
  apply directly.
- Who sees a document: billing.use, plus seeing its company in the CRM (or having written it).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from . import approvals, crm, db
from .config import settings
from .identity.registry import Person

KINDS = ("quote", "invoice")
SETTING = "billing"
DEFAULTS = {
    "name": "", "street": "", "house_num": "", "pcode": "", "city": "", "country": "CH",
    "email": "", "phone": "", "website": "", "iban": "",
    "vat_enabled": False, "vat_number": "",
    "vat_rates": [{"key": "standard", "label": "Standard", "rate": 8.1},
                  {"key": "reduced", "label": "Reduced", "rate": 2.6},
                  {"key": "lodging", "label": "Accommodation", "rate": 3.8},
                  {"key": "none", "label": "No VAT", "rate": 0.0}],
    "default_vat": "standard",
    "payment_days": 30, "quote_valid_days": 30, "round_05": True,
    "invoice_format": "R-{year}-{seq:04d}", "quote_format": "Q-{year}-{seq:04d}",
    "invoice_footer": "Payable within {days} days. Thank you for your trust.",
    "quote_footer": "This quote is valid until {valid_until}. We look forward to working with you.",
    "invoice_email": "Please find attached our invoice {number} of {total}, payable by {due_date}.\n\nThe QR-bill is at the "
                     "bottom of the invoice; scan it with your banking app to pay.",
    "quote_email": "Thank you for your interest. Please find attached our quote {number} of {total}, valid until "
                   "{valid_until}.\n\nWe'd be glad to answer any questions.",
    "reminder_email": "Our invoice {number} of {total} was due on {due_date} and we haven't received the payment yet. "
                      "Could you please check it? The invoice is attached again; if you've paid in the meantime, please "
                      "ignore this message.",
}
TEXT_KEYS = ("invoice_footer", "quote_footer", "invoice_email", "quote_email", "reminder_email")
MAX_LINES = 100
MAX_AMOUNT = Decimal("99999999.99")         # the QR-bill's limit
CENT = Decimal("0.01")


class BillingError(ValueError):
    """Message is safe to show."""


def _need(person: Person, permission: str) -> None:
    if not person.can(permission):
        raise BillingError("You don't have permission to do that with quotes and invoices.")


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _money(v) -> float:
    return float(_d(v).quantize(CENT, ROUND_HALF_UP))


def fmt_chf(v, currency: str = "CHF") -> str:
    n = _d(v).quantize(CENT, ROUND_HALF_UP)
    whole, _, cents = f"{abs(n):.2f}".partition(".")
    grouped = re.sub(r"(?<=\d)(?=(\d{3})+$)", "’", whole)
    return f"{currency} {'-' if n < 0 else ''}{grouped}.{cents}"


# --- settings -------------------------------------------------------------------------------------

def get_settings() -> dict:
    stored = db.get_setting(SETTING, {}) or {}
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not out["name"]:
        out["name"] = settings.company_name or ""
    return out


def _iban(v: str) -> str:
    s = re.sub(r"\s", "", str(v or "")).upper()
    if not s:
        return ""
    from stdnum import iban as std_iban
    if not std_iban.is_valid(s) or s[:2] not in ("CH", "LI"):
        raise BillingError("That isn't a valid Swiss or Liechtenstein IBAN (the QR-bill needs one).")
    return s


def is_qr_iban(iban: str) -> bool:
    s = re.sub(r"\s", "", iban or "")
    return len(s) >= 9 and s[4:9].isdigit() and 30000 <= int(s[4:9]) <= 31999


def save_settings(person: Person, data: dict) -> dict:
    _need(person, "billing.settings")
    cur = get_settings()
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise BillingError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    for k, v in data.items():
        if k in ("name", "street", "city", "email", "phone", "website", "vat_number"):
            cur[k] = " ".join(str(v or "").split())[:70]
        elif k in ("house_num", "pcode"):
            cur[k] = " ".join(str(v or "").split())[:16]
        elif k == "country":
            v = str(v or "CH").strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", v):
                raise BillingError("Country: the two-letter code, e.g. CH.")
            cur[k] = v
        elif k == "iban":
            cur[k] = _iban(v)
        elif k in ("vat_enabled", "round_05"):
            cur[k] = bool(v)
        elif k in ("payment_days", "quote_valid_days"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise BillingError("Days must be a whole number.")
            if not 0 <= n <= 365:
                raise BillingError("Days must be between 0 and 365.")
            cur[k] = n
        elif k == "vat_rates":
            rates = []
            for r in (v or [])[:8]:
                key = re.sub(r"[^a-z0-9_]", "", str(r.get("key", "")).lower())[:20]
                try:
                    rate = float(r.get("rate", 0))
                except (TypeError, ValueError):
                    raise BillingError("VAT rates must be numbers.")
                if not key or not 0 <= rate <= 30:
                    raise BillingError("Each VAT rate needs a key and a percentage from 0 to 30.")
                rates.append({"key": key, "label": " ".join(str(r.get("label") or key).split())[:30], "rate": rate})
            if not rates:
                raise BillingError("Keep at least one VAT rate.")
            cur[k] = rates
        elif k == "default_vat":
            cur[k] = str(v or "")[:20]
        elif k in ("invoice_format", "quote_format"):
            v = str(v or "").strip()[:40]
            if "{seq" not in v:
                raise BillingError("The number format needs {seq}, e.g. R-{year}-{seq:04d}.")
            try:
                v.format(year=2026, seq=1)
            except (KeyError, ValueError, IndexError):
                raise BillingError("The number format can use {year} and {seq} (e.g. {seq:04d}) only.")
            cur[k] = v
        elif k in TEXT_KEYS:
            cur[k] = str(v or "").replace("\r\n", "\n").strip()[:2000]
    if cur["default_vat"] not in {r["key"] for r in cur["vat_rates"]}:
        cur["default_vat"] = cur["vat_rates"][0]["key"]
    db.set_setting(SETTING, cur)
    db.log_activity("billing", f"{person.display_name} changed the billing settings", user_id=person.id)
    return cur


def missing_for_invoices(s: dict | None = None) -> list[str]:
    s = s or get_settings()
    need = {"name": "company name", "street": "street", "pcode": "postcode", "city": "town", "iban": "IBAN"}
    return [label for k, label in need.items() if not s.get(k)]


def _rate(key: str, s: dict) -> dict:
    for r in s["vat_rates"]:
        if r["key"] == key:
            return r
    return next((r for r in s["vat_rates"] if r["key"] == s["default_vat"]), s["vat_rates"][0])


# --- products -------------------------------------------------------------------------------------

def products(include_inactive: bool = False) -> list[dict]:
    rows = db.query("SELECT * FROM billing_products" + ("" if include_inactive else " WHERE active = 1")
                    + " ORDER BY active DESC, lower(name)")
    for r in rows:
        r["active"] = bool(r["active"])
    return rows


def save_product(person: Person, data: dict, product_id: int | None = None) -> dict:
    _need(person, "billing.settings")
    s = get_settings()
    clean = {}
    for k, v in data.items():
        if k == "name":
            clean[k] = " ".join(str(v or "").split())[:120]
        elif k == "description":
            clean[k] = str(v or "").strip()[:500]
        elif k == "unit":
            clean[k] = " ".join(str(v or "").split())[:16]
        elif k == "unit_price":
            clean[k] = _amount(v)
        elif k == "vat_key":
            clean[k] = _rate(str(v or ""), s)["key"]
        elif k == "active":
            clean[k] = int(bool(v))
        else:
            raise BillingError(f"Unknown field {k}.")
    now = db.now_iso()
    if product_id is None:
        if not clean.get("name"):
            raise BillingError("Give the product or service a name.")
        clean.setdefault("vat_key", s["default_vat"])
        cols = {**clean, "created_at": now, "updated_at": now}
        pid = db.execute(f"INSERT INTO billing_products({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         list(cols.values()))
    else:
        if not db.query_one("SELECT 1 FROM billing_products WHERE id = ?", (product_id,)):
            raise BillingError("Unknown product.")
        if "name" in clean and not clean["name"]:
            raise BillingError("A product needs a name.")
        if clean:
            db.execute(f"UPDATE billing_products SET {', '.join(f'{k} = ?' for k in clean)}, updated_at = ? WHERE id = ?",
                       (*clean.values(), now, product_id))
        pid = product_id
    row = db.query_one("SELECT * FROM billing_products WHERE id = ?", (pid,))
    row["active"] = bool(row["active"])
    return row


def find_product(ref) -> dict | None:
    """An active product by id or name: the exact name, else the only one containing the words; None if unclear."""
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return db.query_one("SELECT * FROM billing_products WHERE id = ? AND active = 1", (int(ref),))
    want = " ".join(str(ref or "").split()).lower()
    if not want:
        return None
    rows = [p for p in products() if want in p["name"].lower()]
    exact = [p for p in rows if p["name"].lower() == want]
    if len(exact) == 1:
        return exact[0]
    return rows[0] if len(rows) == 1 else None


# --- amounts --------------------------------------------------------------------------------------

def _amount(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        x = Decimal(re.sub(r"[’'\s]|CHF|Fr\.?", "", str(v), flags=re.I).replace(",", "."))
    except Exception:  # noqa: BLE001
        raise BillingError(f"“{v}” isn't an amount.")
    if abs(x) > MAX_AMOUNT:
        raise BillingError("That amount is too large.")
    return float(x.quantize(CENT, ROUND_HALF_UP))


def compute(lines: list[dict], s: dict | None = None) -> dict:
    """Net per line, VAT per rate (on the rate's net sum), total, optional 5-Rappen rounding."""
    s = s or get_settings()
    net_total = Decimal(0)
    by_rate: dict[tuple[str, float], Decimal] = {}
    out_lines = []
    for ln in lines:
        q, price, disc = _d(ln.get("quantity", 1)), _d(ln.get("unit_price", 0)), _d(ln.get("discount", 0))
        net = (q * price * (Decimal(100) - disc) / Decimal(100)).quantize(CENT, ROUND_HALF_UP)
        rate = _rate(ln.get("vat_key") or s["default_vat"], s) if s["vat_enabled"] else {"key": "", "rate": 0.0}
        out_lines.append({**ln, "net": float(net), "vat_key": rate["key"], "vat_rate": rate["rate"]})
        net_total += net
        if s["vat_enabled"] and rate["rate"]:
            k = (rate["key"], rate["rate"])
            by_rate[k] = by_rate.get(k, Decimal(0)) + net
    breakdown = []
    vat_total = Decimal(0)
    for (key, rate), base in sorted(by_rate.items(), key=lambda x: -x[0][1]):
        vat = (base * _d(rate) / Decimal(100)).quantize(CENT, ROUND_HALF_UP)
        vat_total += vat
        breakdown.append({"key": key, "rate": rate, "base": float(base), "vat": float(vat)})
    gross = net_total + vat_total
    total = gross
    if s["round_05"]:
        total = (gross * 20).quantize(Decimal(1), ROUND_HALF_UP) / 20
    return {"lines": out_lines, "total_net": float(net_total), "total_vat": float(vat_total),
            "rounding": float((total - gross).quantize(CENT)), "total": float(total.quantize(CENT)),
            "vat_breakdown": breakdown}


# --- references -----------------------------------------------------------------------------------

_MOD10 = [0, 9, 4, 6, 8, 2, 7, 1, 3, 5]


def qr_reference(n: int) -> str:
    """27-digit QR reference (QRR, for QR-IBANs): the number padded to 26 digits plus a recursive mod-10 check."""
    body = f"{n:026d}"
    carry = 0
    for ch in body:
        carry = _MOD10[(carry + int(ch)) % 10]
    return body + str((10 - carry) % 10)


def creditor_reference(text: str) -> str:
    """ISO 11649 creditor reference ("RF.."), for ordinary IBANs."""
    body = re.sub(r"[^A-Za-z0-9]", "", text).upper()[:21] or "0"
    digits = "".join(str(int(c, 36)) for c in body + "RF00")
    return f"RF{98 - int(digits) % 97:02d}{body}"


# --- documents ------------------------------------------------------------------------------------

def _scope(person: Person) -> tuple[str, list]:
    _need(person, "billing.use")
    if person.can("crm.view_all"):
        return "", []
    where, params = crm._scope(person, "co", "company")
    return (f" AND (d.created_by = ? OR d.owner_id = ? OR (d.company_id IS NOT NULL AND EXISTS (SELECT 1 FROM "
            f"crm_companies co WHERE co.id = d.company_id{where})))", [person.id, person.id, *params])


_SELECT = ("SELECT d.*, co.name AS company_name, ct.first_name AS contact_first, ct.last_name AS contact_last, "
           "dl.title AS deal_title FROM billing_docs d LEFT JOIN crm_companies co ON co.id = d.company_id "
           "LEFT JOIN crm_contacts ct ON ct.id = d.contact_id LEFT JOIN crm_deals dl ON dl.id = d.deal_id")


def display_status(d: dict, today: date | None = None) -> str:
    today = (today or _today()).isoformat()
    if d["status"] == "issued":
        if d["kind"] == "invoice":
            if d["paid_amount"] and d["paid_amount"] < d["total"]:
                return "partly_paid" if not (d["due_date"] and d["due_date"] < today) else "overdue"
            if d["due_date"] and d["due_date"] < today:
                return "overdue"
            return "open"
        if d["due_date"] and d["due_date"] < today:
            return "expired"
        return "sent"
    return d["status"]


def _view(row: dict, names: dict | None = None) -> dict:
    names = names if names is not None else crm._names()
    d = dict(row)
    d["recipient"] = json.loads(d["recipient"] or "{}")
    d["vat_breakdown"] = json.loads(d["vat_breakdown"] or "[]")
    d["contact_name"] = " ".join(x for x in (d.pop("contact_first", None), d.pop("contact_last", None)) if x) or None
    d["owner_name"] = names.get(d.get("owner_id"))
    d["created_by_name"] = names.get(d.get("created_by"))
    d["state"] = display_status(d)
    d["open_amount"] = _money(_d(d["total"]) - _d(d["paid_amount"])) if d["kind"] == "invoice" else 0.0
    d["editable"] = d["status"] == "draft"
    d["label"] = d["number"] or f"Draft {'quote' if d['kind'] == 'quote' else 'invoice'} #{d['id']}"
    return d


def _get_row(person: Person, doc_id: int) -> dict:
    where, params = _scope(person)
    row = db.query_one(f"{_SELECT} WHERE d.id = ?{where}", (doc_id, *params))
    if not row:
        raise BillingError("No such quote or invoice.")
    return row


def get(person: Person, doc_id: int) -> dict:
    d = _view(_get_row(person, doc_id))
    d["lines"] = db.query("SELECT * FROM billing_lines WHERE doc_id = ? ORDER BY position, id", (doc_id,))
    d["payments"] = db.query("SELECT p.*, u.display_name AS user_name FROM billing_payments p LEFT JOIN users u "
                             "ON u.id = p.user_id WHERE p.doc_id = ? ORDER BY p.paid_on, p.id", (doc_id,))
    d["pending_approval"] = db.query_one(
        "SELECT id, kind, summary FROM approvals WHERE area = 'billing' AND status = 'pending' AND payload LIKE ?",
        (f'%"doc_id": {doc_id}%',))
    if d["quote_id"]:
        q = db.query_one("SELECT id, number FROM billing_docs WHERE id = ?", (d["quote_id"],))
        d["quote"] = q
    d["invoices"] = db.query("SELECT id, number, status FROM billing_docs WHERE quote_id = ?", (doc_id,)) \
        if d["kind"] == "quote" else []
    return d


def search(person: Person, *, kind: str = "invoice", state: str = "", q: str = "", company_id: int | None = None,
           deal_id: int | None = None, limit: int = 100, offset: int = 0) -> dict:
    where, params = _scope(person)
    where = " WHERE 1=1" + where
    if kind in KINDS:
        where += " AND d.kind = ?"
        params.append(kind)
    if company_id:
        where += " AND d.company_id = ?"
        params.append(company_id)
    if deal_id:
        where += " AND d.deal_id = ?"
        params.append(deal_id)
    if q:
        like = f"%{q.strip().lower()[:80]}%"
        where += " AND (lower(d.number) LIKE ? OR lower(d.title) LIKE ? OR lower(co.name) LIKE ?)"
        params += [like] * 3
    rows = db.query(f"{_SELECT}{where} ORDER BY CASE d.status WHEN 'draft' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
                    f"COALESCE(d.issue_date, d.created_at) DESC, d.id DESC", params)
    names = crm._names()
    items = [_view(r, names) for r in rows]
    if state:
        items = [d for d in items if d["state"] == state or (state == "unpaid" and d["state"] in ("open", "overdue", "partly_paid"))]
    return {"total": len(items), "items": items[offset:offset + max(1, min(limit, 500))]}


def summary(person: Person) -> dict:
    items = search(person, kind="invoice", limit=5000)["items"]
    month = _today().strftime("%Y-%m")
    unpaid = [d for d in items if d["state"] in ("open", "overdue", "partly_paid")]
    overdue = [d for d in items if d["state"] == "overdue"]
    paid_month = db.query("SELECT p.amount, p.doc_id FROM billing_payments p WHERE substr(p.paid_on, 1, 7) = ?", (month,))
    visible = {d["id"] for d in items}
    quotes = search(person, kind="quote", limit=5000)["items"]
    return {
        "receivable": _money(sum(_d(d["open_amount"]) for d in unpaid)), "unpaid_count": len(unpaid),
        "overdue": _money(sum(_d(d["open_amount"]) for d in overdue)), "overdue_count": len(overdue),
        "paid_month": _money(sum(_d(p["amount"]) for p in paid_month if p["doc_id"] in visible)),
        "drafts": len([d for d in items + quotes if d["status"] == "draft"]),
        "quotes_open": len([q for q in quotes if q["state"] == "sent"]),
        "quotes_open_value": _money(sum(_d(q["total"]) for q in quotes if q["state"] == "sent")),
        "pending": len([d for d in items + quotes if d["status"] == "pending"]),
    }


def _parse_address(text: str) -> dict:
    """“Musterstrasse 1\\n8000 Zürich” (or CH-8000) -> street, house_num, pcode, city."""
    lines = [ln.strip() for ln in str(text or "").replace(",", "\n").split("\n") if ln.strip()]
    out = {"street": "", "house_num": "", "pcode": "", "city": "", "country": "CH"}
    for i in range(len(lines) - 1, -1, -1):
        m = re.match(r"^(?:(CH|LI|DE|AT|FR|IT)[- ])?(\d{4,5})\s+(.+)$", lines[i], re.I)
        if m:
            out.update(pcode=m.group(2), city=m.group(3), country=(m.group(1) or "CH").upper())
            if i > 0:
                sm = re.match(r"^(.*?)[\s,]+(\d+[a-zA-Z]?(?:[-/]\d+)?)$", lines[i - 1])
                out.update(street=sm.group(1), house_num=sm.group(2)) if sm else out.update(street=lines[i - 1])
            break
    return out


def recipient_for(person: Person, company_id: int | None, contact_id: int | None) -> dict:
    rec = {"name": "", "attention": "", "street": "", "house_num": "", "pcode": "", "city": "", "country": "CH", "email": ""}
    if company_id:
        co = crm.get(person, "company", company_id)
        rec.update(name=co["name"], email=co.get("email") or "", **_parse_address(co.get("address", "")))
    if contact_id:
        ct = crm.get(person, "contact", contact_id)
        rec["attention"] = ct["name"]
        rec["email"] = ct.get("email") or rec["email"]
        if not company_id:
            rec["name"] = ct["name"]
            rec["attention"] = ""
    return rec


def _clean_recipient(v: dict) -> dict:
    out = {}
    for k, n in (("name", 70), ("attention", 70), ("street", 70), ("house_num", 16), ("pcode", 16), ("city", 35),
                 ("country", 2), ("email", 254)):
        out[k] = " ".join(str((v or {}).get(k) or "").split())[:n]
    out["country"] = (out["country"] or "CH").upper()
    if out["email"] and not crm._EMAIL.match(out["email"].lower()):
        raise BillingError("The recipient's email address doesn't look right.")
    out["email"] = out["email"].lower()
    return out


def _clean_lines(lines: list, s: dict) -> list[dict]:
    if len(lines) > MAX_LINES:
        raise BillingError(f"At most {MAX_LINES} lines.")
    out = []
    for i, ln in enumerate(lines):
        ln = dict(ln or {})
        prod = None
        if ln.get("product_id"):
            prod = db.query_one("SELECT * FROM billing_products WHERE id = ?", (int(ln["product_id"]),))
            if not prod:
                raise BillingError("Unknown product on a line.")
        desc = str(ln.get("description") or (prod and (prod["name"] + (f"\n{prod['description']}" if prod["description"] else ""))) or "").strip()[:1000]
        try:
            qty = float(_d(ln.get("quantity", 1) if ln.get("quantity") not in (None, "") else 1))
            disc = float(_d(ln.get("discount") or 0))
        except Exception:  # noqa: BLE001
            raise BillingError("Quantity and discount must be numbers.")
        if not -100000 <= qty <= 100000 or not 0 <= disc <= 100:
            raise BillingError("Quantity or discount out of range.")
        price = _amount(ln["unit_price"]) if ln.get("unit_price") not in (None, "") else (prod["unit_price"] if prod else 0.0)
        if not desc:
            raise BillingError(f"Line {i + 1} needs a description.")
        out.append({"position": i, "product_id": prod["id"] if prod else None, "description": desc, "quantity": qty,
                    "unit": " ".join(str(ln.get("unit") or (prod and prod["unit"]) or "").split())[:16],
                    "unit_price": price, "discount": disc,
                    "vat_key": _rate(str(ln.get("vat_key") or (prod and prod["vat_key"]) or s["default_vat"]), s)["key"]})
    return out


def _date(v, label: str) -> str | None:
    v = str(v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise BillingError(f"{label}: give a date like 2026-11-30.")


def save(person: Person, data: dict, doc_id: int | None = None) -> dict:
    """Create a draft (kind required) or change one. `lines` replaces all lines."""
    _need(person, "billing.use")
    s = get_settings()
    allowed = {"kind", "company_id", "contact_id", "deal_id", "title", "intro", "footer", "due_date", "recipient",
               "lines", "owner_id"}
    unknown = set(data) - allowed
    if unknown:
        raise BillingError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    if doc_id is not None:
        row = _get_row(person, doc_id)
        if row["status"] != "draft":
            raise BillingError("Only drafts can be changed. Cancel it and write a new one instead.")
        kind = row["kind"]
    else:
        kind = data.get("kind")
        if kind not in KINDS:
            raise BillingError("Is it a quote or an invoice?")
    clean: dict = {}
    if "company_id" in data:
        clean["company_id"] = crm.get(person, "company", int(data["company_id"]))["id"] if data["company_id"] else None
    if "contact_id" in data:
        clean["contact_id"] = crm.get(person, "contact", int(data["contact_id"]))["id"] if data["contact_id"] else None
    if "deal_id" in data:
        from . import crm_deals
        clean["deal_id"] = crm_deals.get(person, int(data["deal_id"]))["id"] if data["deal_id"] else None
    for k, n in (("title", 160), ("intro", 2000), ("footer", 2000)):
        if k in data:
            clean[k] = str(data[k] or "").replace("\r\n", "\n").strip()[:n]
    if "due_date" in data:
        clean["due_date"] = _date(data["due_date"], "Due date" if kind == "invoice" else "Valid until")
    if "owner_id" in data:
        clean["owner_id"] = crm._person_ref(data["owner_id"], "owner")
    if "recipient" in data:
        clean["recipient"] = json.dumps(_clean_recipient(data["recipient"]), ensure_ascii=False)
    elif doc_id is None or {"company_id", "contact_id"} & set(clean):
        cur = {} if doc_id is None else {"company_id": row["company_id"], "contact_id": row["contact_id"]}
        cur.update({k: clean[k] for k in ("company_id", "contact_id") if k in clean})
        clean["recipient"] = json.dumps(recipient_for(person, cur.get("company_id"), cur.get("contact_id")),
                                        ensure_ascii=False)
    lines = _clean_lines(data["lines"], s) if "lines" in data else None
    now = db.now_iso()
    with db.tx() as conn:
        if doc_id is None:
            clean.setdefault("owner_id", person.id)
            cols = {"kind": kind, **clean, "currency": "CHF", "created_by": person.id, "created_at": now, "updated_at": now}
            doc_id = conn.execute(f"INSERT INTO billing_docs({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  list(cols.values())).lastrowid
        elif clean:
            conn.execute(f"UPDATE billing_docs SET {', '.join(f'{k} = ?' for k in clean)}, updated_at = ? WHERE id = ?",
                         (*clean.values(), now, doc_id))
        if lines is not None:
            conn.execute("DELETE FROM billing_lines WHERE doc_id = ?", (doc_id,))
            conn.executemany("INSERT INTO billing_lines(doc_id, position, product_id, description, quantity, unit, "
                             "unit_price, discount, vat_key) VALUES (?,?,?,?,?,?,?,?,?)",
                             [(doc_id, ln["position"], ln["product_id"], ln["description"], ln["quantity"], ln["unit"],
                               ln["unit_price"], ln["discount"], ln["vat_key"]) for ln in lines])
    _recompute(doc_id, s)
    return get(person, doc_id)


def _recompute(doc_id: int, s: dict | None = None) -> None:
    s = s or get_settings()
    lines = db.query("SELECT * FROM billing_lines WHERE doc_id = ? ORDER BY position, id", (doc_id,))
    t = compute(lines, s)
    with db.tx() as conn:
        for ln in t["lines"]:
            conn.execute("UPDATE billing_lines SET net = ?, vat_rate = ?, vat_key = ? WHERE id = ?",
                         (ln["net"], ln["vat_rate"], ln["vat_key"] or ln.get("vat_key", ""), ln["id"]))
        conn.execute("UPDATE billing_docs SET total_net = ?, total_vat = ?, total = ?, rounding = ?, vat_breakdown = ? "
                     "WHERE id = ?", (t["total_net"], t["total_vat"], t["total"], t["rounding"],
                                      json.dumps(t["vat_breakdown"]), doc_id))


def discard(person: Person, doc_id: int) -> None:
    """Drafts (never issued, no number) can be thrown away directly."""
    row = _get_row(person, doc_id)
    if row["status"] != "draft":
        raise BillingError("Only drafts can be discarded; issued documents are cancelled instead.")
    if not (row["created_by"] == person.id or person.can("billing.approve")):
        raise BillingError("Only whoever wrote the draft (or someone who approves billing) can discard it.")
    db.execute("DELETE FROM billing_docs WHERE id = ?", (doc_id,))
    db.log_activity("billing", f"{person.display_name} discarded a draft {row['kind']} ({row['title'] or '#' + str(doc_id)})",
                    user_id=person.id)


def copy(person: Person, doc_id: int, kind: str | None = None) -> dict:
    """A new draft from an existing document: revise a quote, or turn an accepted quote into an invoice."""
    src = get(person, doc_id)
    kind = kind or src["kind"]
    data = {"kind": kind, "company_id": src["company_id"], "contact_id": src["contact_id"], "deal_id": src["deal_id"],
            "title": src["title"], "intro": src["intro"] if kind == src["kind"] else "", "recipient": src["recipient"],
            "lines": [{k: ln[k] for k in ("product_id", "description", "quantity", "unit", "unit_price", "discount", "vat_key")}
                      for ln in src["lines"]]}
    new = save(person, data)
    if src["kind"] == "quote" and kind == "invoice":
        db.execute("UPDATE billing_docs SET quote_id = ? WHERE id = ?", (doc_id, new["id"]))
        new = get(person, new["id"])
    return new


# --- issuing and sending -------------------------------------------------------------------------

def _check_ready(d: dict, s: dict) -> None:
    if not d["lines"]:
        raise BillingError("Add at least one line first.")
    if d["total"] <= 0 and d["kind"] == "invoice":
        raise BillingError("An invoice needs a total above zero.")
    if not d["recipient"].get("name"):
        raise BillingError("Who is it for? Pick a company or fill in the recipient.")
    if d["kind"] == "invoice":
        missing = missing_for_invoices(s)
        if missing:
            raise BillingError(f"Billing settings are missing: {', '.join(missing)} (Billing → Settings).")


def _next_number(conn, kind: str, s: dict) -> str:
    year = _today().year
    key = f"billing_seq_{kind}_{year}"
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    seq = (int(json.loads(row[0])) if row else 0) + 1
    while True:
        number = s[f"{kind}_format"].format(year=year, seq=seq)
        if not conn.execute("SELECT 1 FROM billing_docs WHERE number = ?", (number,)).fetchone():
            break
        seq += 1
    conn.execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value, updated_at = excluded.updated_at", (key, json.dumps(seq), db.now_iso()))
    return number


def _footer_text(d: dict, s: dict) -> str:
    template = d["footer"] or s[f"{d['kind']}_footer"]
    return _fill(template, d, s)


def _fill(text: str, d: dict, s: dict) -> str:
    values = {"number": d.get("number") or "", "total": fmt_chf(d["total"], d["currency"]),
              "due_date": _human(d.get("due_date")), "valid_until": _human(d.get("due_date")),
              "days": s["payment_days"], "company": s["name"], "name": d["recipient"].get("attention") or d["recipient"].get("name", ""),
              "title": d.get("title") or ""}
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text or "")


def _human(iso: str | None) -> str:
    if not iso:
        return ""
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def issue(person: Person, doc_id: int) -> dict:
    """Number, freeze, render and file the document. Directly ("issue without sending") or as part of sending."""
    _need(person, "billing.use")
    s = get_settings()
    d = get(person, doc_id)
    if d["status"] not in ("draft", "pending"):
        return d
    _check_ready(d, s)
    from . import invoicing_pdf
    today = _today()
    with db.tx() as conn:
        claimed = conn.execute("UPDATE billing_docs SET status = 'issued' WHERE id = ? AND status IN ('draft','pending')",
                               (doc_id,)).rowcount
        if not claimed:
            return get(person, doc_id)
        number = _next_number(conn, d["kind"], s)
        due = d["due_date"] or (today + timedelta(days=s["payment_days"] if d["kind"] == "invoice" else s["quote_valid_days"])).isoformat()
        ref = ""
        if d["kind"] == "invoice":
            ref = qr_reference(doc_id) if is_qr_iban(s["iban"]) else creditor_reference(number)
        conn.execute("UPDATE billing_docs SET number = ?, issue_date = ?, due_date = ?, reference = ?, issued_at = ?, "
                     "footer = ?, updated_at = ? WHERE id = ?",
                     (number, today.isoformat(), due, ref, db.now_iso(), d["footer"] or s[f"{d['kind']}_footer"],
                      db.now_iso(), doc_id))
    d = get(person, doc_id)
    try:
        pdf = invoicing_pdf.render(d, s)
    except BillingError:
        db.execute("UPDATE billing_docs SET status = 'draft', number = NULL, issue_date = NULL, reference = '', "
                   "issued_at = NULL WHERE id = ?", (doc_id,))
        raise
    path = _file_pdf(person, d, pdf)
    if path:
        db.execute("UPDATE billing_docs SET pdf_path = ? WHERE id = ?", (path, doc_id))
    _timeline(person, d, f"{'Invoice' if d['kind'] == 'invoice' else 'Quote'} {number} issued ({fmt_chf(d['total'])})")
    db.log_activity("billing", f"{person.display_name} issued {d['kind']} {number} ({fmt_chf(d['total'])})", user_id=person.id)
    return get(person, doc_id)


def _file_pdf(person: Person, d: dict, pdf: bytes) -> str | None:
    """Into the company files: Billing/<Invoices|Quotes>/<year>/<number> <client>.pdf (never overwrites)."""
    try:
        from . import nas, nas_files
        if not nas.enabled():
            return None
        folder = f"Billing/{'Invoices' if d['kind'] == 'invoice' else 'Quotes'}/{d['issue_date'][:4]}"
        client = re.sub(r"[^\w\- ]", "", d["recipient"].get("name", ""))[:40].strip()
        saved = nas_files.upload(person, "company", folder, f"{d['number']} {client}.pdf".strip(), pdf)
        return f"company:{saved['saved']}"
    except Exception:  # noqa: BLE001 — the document stands without the copy; the PDF can be rendered again
        import logging
        logging.getLogger(__name__).warning("Billing: filing the PDF failed", exc_info=True)
        return None


def pdf_bytes(person: Person, doc_id: int) -> tuple[bytes, str]:
    """The PDF (drafts are rendered as a preview, marked DRAFT)."""
    from . import invoicing_pdf
    d = get(person, doc_id)
    s = get_settings()
    return invoicing_pdf.render(d, s, draft=d["status"] in ("draft", "pending")), f"{d['number'] or d['label']}.pdf"


def _timeline(person: Person, d: dict, text: str) -> None:
    if d.get("company_id") or d.get("deal_id"):
        cols = {"company_id": d.get("company_id"), "contact_id": d.get("contact_id"), "deal_id": d.get("deal_id"),
                "kind": "document", "summary": text[:200], "body": "", "occurred_at": db.now_iso(),
                "user_id": person.id, "created_at": db.now_iso()}
        db.execute(f"INSERT INTO crm_activities({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))


def request_send(person: Person, doc_id: int, *, to: str = "", message: str = "") -> dict:
    """Sending to the client always waits for approval; the draft is checked now so the approver isn't surprised."""
    _need(person, "billing.use")
    s = get_settings()
    d = get(person, doc_id)
    if d["status"] not in ("draft", "issued") or (d["status"] == "issued" and d["sent_email_id"]):
        raise BillingError("This one was already sent." if d["sent_email_id"] else f"It's {d['state']}.")
    _check_ready(d, s)
    to = (to or d["recipient"].get("email") or "").strip().lower()
    if not to or not crm._EMAIL.match(to):
        raise BillingError("Which email address should it go to? The recipient has none.")
    what = "invoice" if d["kind"] == "invoice" else "quote"
    summary = (f"Send the {what} {d['number'] or '(numbered on approval)'} “{d['title'] or what}” of {fmt_chf(d['total'])} "
               f"to {d['recipient'].get('name')} <{to}>.")
    if d["status"] == "draft":
        db.execute("UPDATE billing_docs SET status = 'pending' WHERE id = ?", (doc_id,))
    return approvals.create("billing", "send", summary, {"doc_id": doc_id, "to": to, "message": message[:2000]}, person)


def request_reminder(person: Person, doc_id: int, *, message: str = "") -> dict:
    _need(person, "billing.use")
    d = get(person, doc_id)
    if d["kind"] != "invoice" or d["state"] not in ("overdue", "partly_paid", "open"):
        raise BillingError("Reminders are for issued invoices that aren't paid.")
    to = d["recipient"].get("email")
    if not to:
        raise BillingError("The recipient has no email address on this invoice.")
    n = d["reminders"] + 1
    summary = (f"Send payment reminder {n} for invoice {d['number']} ({fmt_chf(d['open_amount'])} open, due "
               f"{_human(d['due_date'])}) to {d['recipient'].get('name')} <{to}>.")
    return approvals.create("billing", "remind", summary, {"doc_id": doc_id, "to": to, "level": n, "message": message[:2000]},
                            person)


def request_cancel(person: Person, doc_id: int, reason: str = "") -> dict:
    _need(person, "billing.use")
    d = get(person, doc_id)
    if d["status"] not in ("issued", "accepted", "declined"):
        raise BillingError("Only issued documents are cancelled; drafts are simply discarded.")
    if d["paid_amount"]:
        raise BillingError("Payments are recorded on this invoice; remove them first.")
    summary = f"Cancel the {d['kind']} {d['number']} of {fmt_chf(d['total'])} for {d['recipient'].get('name')}." + (
        f" Reason: {reason[:200]}" if reason else "")
    return approvals.create("billing", "cancel", summary, {"doc_id": doc_id, "reason": reason[:200]}, person)


async def _send_mail(actor: Person, d: dict, to: str, subject: str, body: str, kind: str) -> int:
    from . import mail
    pdf, name = pdf_bytes(actor, d["id"])
    greeting = f"Dear {d['recipient'].get('attention') or d['recipient'].get('name')},"
    row = await mail.send(to=[to], subject=subject, body_md=body, template="company", scope="company",
                          kind="sent", user_id=actor.id, attachments=[(name, pdf, "application/pdf")], greeting=greeting,
                          summary=f"{kind} {d['number']} {fmt_chf(d['total'])}")
    return row["id"]


@approvals.handler("billing", "send")
async def _approved_send(payload: dict, actor: Person) -> str:
    doc_id = int(payload["doc_id"])
    s = get_settings()
    d = get(actor, doc_id)
    if d["status"] == "cancelled":
        raise ValueError("It was cancelled in the meantime.")
    if d["status"] in ("draft", "pending"):
        try:
            d = issue(actor, doc_id)
        except BillingError as e:
            db.execute("UPDATE billing_docs SET status = 'draft' WHERE id = ? AND status = 'pending'", (doc_id,))
            raise ValueError(str(e))
    what = "Invoice" if d["kind"] == "invoice" else "Quote"
    body = (payload.get("message") or "").strip() or _fill(s[f"{d['kind']}_email"], d, s)
    from .mail import MailError
    try:
        email_id = await _send_mail(actor, d, payload["to"], f"{what} {d['number']}: {d['title'] or s['name']}", body, what)
    except MailError as e:
        raise ValueError(f"{what} {d['number']} was issued but the email failed: {e}")
    db.execute("UPDATE billing_docs SET sent_email_id = ?, updated_at = ? WHERE id = ?", (email_id, db.now_iso(), doc_id))
    _timeline(actor, d, f"{what} {d['number']} sent to {payload['to']}")
    db.log_activity("billing", f"{actor.display_name} sent {what.lower()} {d['number']} to {payload['to']} (approved)",
                    user_id=actor.id)
    return f"{what} {d['number']} sent to {payload['to']}."


@approvals.handler("billing", "remind")
async def _approved_remind(payload: dict, actor: Person) -> str:
    doc_id = int(payload["doc_id"])
    s = get_settings()
    d = get(actor, doc_id)
    if d["state"] not in ("overdue", "partly_paid", "open"):
        raise ValueError(f"Invoice {d['number']} is {d['state']} now; no reminder sent.")
    level = int(payload.get("level") or d["reminders"] + 1)
    body = (payload.get("message") or "").strip() or _fill(s["reminder_email"], d, s)
    from .mail import MailError
    try:
        await _send_mail(actor, d, payload["to"], f"Payment reminder{'' if level == 1 else f' {level}'}: invoice {d['number']}",
                         body, "Reminder")
    except MailError as e:
        raise ValueError(f"The reminder couldn't be sent: {e}")
    db.execute("UPDATE billing_docs SET reminders = ?, last_reminder_at = ? WHERE id = ?", (level, db.now_iso(), doc_id))
    _timeline(actor, d, f"Payment reminder {level} for invoice {d['number']} sent")
    return f"Reminder {level} for {d['number']} sent to {payload['to']}."


@approvals.handler("billing", "cancel")
def _approved_cancel(payload: dict, actor: Person) -> str:
    doc_id = int(payload["doc_id"])
    d = get(actor, doc_id)
    if d["paid_amount"]:
        raise ValueError("Payments were recorded in the meantime.")
    db.execute("UPDATE billing_docs SET status = 'cancelled', closed_at = ?, updated_at = ? WHERE id = ?",
               (db.now_iso(), db.now_iso(), doc_id))
    _timeline(actor, d, f"{d['kind'].capitalize()} {d['number']} cancelled")
    db.log_activity("billing", f"{actor.display_name} cancelled {d['kind']} {d['number']} (approved)", user_id=actor.id)
    return f"{d['number']} cancelled."


# --- after sending ---------------------------------------------------------------------------------

def set_quote_outcome(person: Person, doc_id: int, accepted: bool) -> dict:
    _need(person, "billing.use")
    d = get(person, doc_id)
    if d["kind"] != "quote" or d["status"] not in ("issued", "accepted", "declined"):
        raise BillingError("Only issued quotes can be accepted or declined.")
    db.execute("UPDATE billing_docs SET status = ?, closed_at = ?, updated_at = ? WHERE id = ?",
               ("accepted" if accepted else "declined", db.now_iso(), db.now_iso(), doc_id))
    _timeline(person, d, f"Quote {d['number']} {'accepted' if accepted else 'declined'}")
    return get(person, doc_id)


def add_payment(person: Person, doc_id: int, amount, paid_on: str | None = None, note: str = "") -> dict:
    _need(person, "billing.use")
    d = get(person, doc_id)
    if d["kind"] != "invoice" or d["status"] not in ("issued", "paid"):
        raise BillingError("Payments are recorded on issued invoices.")
    amt = _amount(amount if amount not in (None, "") else d["open_amount"])
    if amt <= 0:
        raise BillingError("The amount must be above zero.")
    day = _date(paid_on, "Paid on") or _today().isoformat()
    db.execute("INSERT INTO billing_payments(doc_id, amount, paid_on, note, user_id, created_at) VALUES (?,?,?,?,?,?)",
               (doc_id, amt, day, str(note or "")[:200], person.id, db.now_iso()))
    _sync_paid(doc_id)
    d = get(person, doc_id)
    _timeline(person, d, f"Payment of {fmt_chf(amt)} recorded for invoice {d['number']}")
    db.log_activity("billing", f"{person.display_name} recorded {fmt_chf(amt)} on {d['number']}", user_id=person.id)
    return d


def remove_payment(person: Person, payment_id: int) -> dict:
    _need(person, "billing.use")
    p = db.query_one("SELECT * FROM billing_payments WHERE id = ?", (payment_id,))
    if not p:
        raise BillingError("No such payment.")
    _get_row(person, p["doc_id"])
    db.execute("DELETE FROM billing_payments WHERE id = ?", (payment_id,))
    _sync_paid(p["doc_id"])
    db.log_activity("billing", f"{person.display_name} removed a payment of {fmt_chf(p['amount'])}", user_id=person.id)
    return get(person, p["doc_id"])


def _sync_paid(doc_id: int) -> None:
    row = db.query_one("SELECT COALESCE(SUM(amount), 0) AS paid, MAX(paid_on) AS last FROM billing_payments "
                       "WHERE doc_id = ?", (doc_id,))
    doc = db.query_one("SELECT total FROM billing_docs WHERE id = ?", (doc_id,))
    paid = _money(row["paid"])
    full = _d(paid) >= _d(doc["total"])
    db.execute("UPDATE billing_docs SET paid_amount = ?, paid_at = ?, status = ?, closed_at = ?, updated_at = ? WHERE id = ?",
               (paid, row["last"] if full else None, "paid" if full else "issued", db.now_iso() if full else None,
                db.now_iso(), doc_id))


# --- export ---------------------------------------------------------------------------------------

def export_csv(person: Person, *, year: int | None = None, kind: str = "invoice") -> tuple[str, str]:
    """For the bookkeeping: one row per issued document (cancelled ones marked), semicolon-separated (Excel CH)."""
    items = [d for d in search(person, kind=kind, limit=100000)["items"] if d["number"]]
    if year:
        items = [d for d in items if (d["issue_date"] or "")[:4] == str(year)]
    rates = sorted({b["rate"] for d in items for b in d["vat_breakdown"]}, reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["number", "date", "due", "client", "title", "net", *[f"vat {r}%" for r in rates], "vat total", "rounding",
                "total", "paid", "paid on", "status", "reference"])
    for d in sorted(items, key=lambda x: x["number"]):
        by = {b["rate"]: b["vat"] for b in d["vat_breakdown"]}
        w.writerow([d["number"], d["issue_date"], d["due_date"] or "", d["recipient"].get("name", ""), d["title"],
                    f"{d['total_net']:.2f}", *[f"{by.get(r, 0):.2f}" for r in rates], f"{d['total_vat']:.2f}",
                    f"{d['rounding']:.2f}", f"{d['total']:.2f}", f"{d['paid_amount']:.2f}", d["paid_at"] or "",
                    d["state"], d["reference"]])
    name = f"{'invoices' if kind == 'invoice' else 'quotes'}-{year or 'all'}.csv"
    return "﻿" + buf.getvalue(), name
