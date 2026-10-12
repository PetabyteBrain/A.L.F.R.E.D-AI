"""PDF for quotes and invoices (orchestrator/invoicing.py): A4, the company email layout's banner and accent colour,
address block for a window envelope, lines with VAT, totals, the footer text, and on invoices the Swiss QR-bill
(qrbill + svglib) at the bottom of the last page. Drafts carry a DRAFT mark."""
from __future__ import annotations

import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .invoicing import BillingError, fmt_chf

QR_HEIGHT = 106 * mm          # the payment part: 210 x 105 mm, plus the separation line
BOTTOM = 12 * mm              # page bottom margin: the payment part reaches below it, down to the paper's edge


def _style() -> tuple[colors.Color, colors.Color, colors.Color, tuple[bytes, str] | None, str]:
    """accent, ink, muted colours, banner, font family from the company email layout."""
    from . import email_templates
    try:
        lay = email_templates.layout_for("company")
        st = lay.get("style") or {}
        banner = email_templates.banner_bytes(lay["id"])
    except Exception:  # noqa: BLE001 — a plain document is better than none
        st, banner = {}, None
    hexc = lambda v, d: colors.HexColor(v) if isinstance(v, str) and v.startswith("#") and len(v) == 7 else colors.HexColor(d)  # noqa: E731
    serif = str(st.get("font", "")).lower() == "serif"
    return (hexc(st.get("accent"), "#1f7a46"), hexc(st.get("ink"), "#1b1b1a"), hexc(st.get("muted"), "#6b6860"),
            banner, "Times-Roman" if serif else "Helvetica")


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def _date(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else "—"


def _qty(q: float) -> str:
    return f"{q:g}" if q != int(q) else f"{int(q)}"


class _QRPart(Flowable):
    """The payment part, always at the very bottom of a page (a new page if the current one is too full)."""

    def __init__(self, drawing):
        super().__init__()
        self.drawing = drawing

    def wrap(self, aw, ah):
        self._ah = ah
        return aw, ah if ah >= QR_HEIGHT - BOTTOM else ah + 1   # too little room: push to the next page

    def draw(self):
        x, y = self.canv.absolutePosition(0, 0)
        from reportlab.graphics import renderPDF
        renderPDF.draw(self.drawing, self.canv, -x, -y)


def _qr_drawing(d: dict, s: dict):
    from qrbill import QRBill
    from svglib.svglib import svg2rlg
    rec = d["recipient"]
    debtor = None
    if rec.get("name") and rec.get("pcode") and rec.get("city"):
        debtor = {"name": rec["name"][:70], "street": rec.get("street", "")[:70], "house_num": rec.get("house_num", "")[:16],
                  "pcode": rec["pcode"][:16], "city": rec["city"][:35], "country": rec.get("country") or "CH"}
    try:
        bill = QRBill(account=s["iban"], creditor={"name": s["name"][:70], "street": s["street"][:70],
                                                   "house_num": s["house_num"][:16], "pcode": s["pcode"][:16],
                                                   "city": s["city"][:35], "country": s["country"] or "CH"},
                      amount=f"{d['total'] - d['paid_amount'] if d['paid_amount'] else d['total']:.2f}",
                      currency=d["currency"], debtor=debtor, reference_number=d["reference"] or None,
                      additional_information=f"Invoice {d['number']}"[:140], language="en")
    except ValueError as e:
        raise BillingError(f"The QR-bill can't be made: {e}. Check the billing settings and the recipient's address.")
    buf = io.StringIO()
    bill.as_svg(buf, full_page=False)
    return svg2rlg(io.BytesIO(buf.getvalue().encode("utf-8")))


def render(d: dict, s: dict, *, draft: bool = False) -> bytes:
    accent, ink, muted, banner, font = _style()
    bold = f"{font}-Bold" if font == "Helvetica" else "Times-Bold"
    base = ParagraphStyle("base", fontName=font, fontSize=9.5, leading=13, textColor=ink)
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=muted)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=base, fontName=bold, fontSize=18, leading=22, textColor=accent)
    strong = ParagraphStyle("strong", parent=base, fontName=bold)
    is_invoice = d["kind"] == "invoice"
    story = []

    # Letterhead: the banner, else the company name.
    if banner:
        try:
            img = Image(io.BytesIO(banner[0]))
            ratio = img.imageHeight / float(img.imageWidth or 1)
            w = 170 * mm
            h = min(w * ratio, 22 * mm)
            img.drawWidth, img.drawHeight = h / ratio, h
            img.hAlign = "LEFT"
            story += [img, Spacer(1, 5 * mm)]
        except Exception:  # noqa: BLE001
            banner = None
    if not banner:
        story += [Paragraph(escape(s["name"] or ""), ParagraphStyle("brand", parent=head, fontSize=16)), Spacer(1, 4 * mm)]

    sender = " · ".join(x for x in (s["name"], f"{s['street']} {s['house_num']}".strip(), f"{s['pcode']} {s['city']}".strip()) if x)
    rec = d["recipient"]
    rec_lines = [rec.get("name", "")] + ([f"Attn. {rec['attention']}"] if rec.get("attention") else []) + [
        f"{rec.get('street', '')} {rec.get('house_num', '')}".strip(),
        f"{'' if (rec.get('country') or 'CH') == 'CH' else rec['country'] + '-'}{rec.get('pcode', '')} {rec.get('city', '')}".strip()]
    contact = [x for x in (s.get("email"), s.get("phone"), s.get("website")) if x]
    addr = Table([[_p("\n".join([s["name"], f"{s['street']} {s['house_num']}".strip(), f"{s['pcode']} {s['city']}".strip(),
                                 *contact, *([f"VAT {s['vat_number']}"] if s["vat_enabled"] and s["vat_number"] else [])]), small),
                   [_p(sender, small), Spacer(1, 1.5 * mm), _p("\n".join(x for x in rec_lines if x), base)]]],
                 colWidths=[95 * mm, 75 * mm])
    addr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [addr, Spacer(1, 8 * mm)]

    title = ("Invoice" if is_invoice else "Quote") + (f" {d['number']}" if d.get("number") else " (draft)")
    story += [Paragraph(escape(title), head)]
    if d.get("title"):
        story += [_p(d["title"], strong)]
    meta = [["Date", _date(d.get("issue_date") or date.today().isoformat())],
            ["Payable by" if is_invoice else "Valid until", _date(d.get("due_date"))]]
    if is_invoice and d.get("reference"):
        meta.append(["Reference", d["reference"]])
    if d.get("owner_name"):
        meta.append(["Your contact", d["owner_name"]])
    mt = Table([[_p(k, small), _p(v, base)] for k, v in meta], colWidths=[30 * mm, 80 * mm], hAlign="LEFT")
    mt.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story += [Spacer(1, 2 * mm), mt, Spacer(1, 4 * mm)]
    if d.get("intro"):
        story += [_p(d["intro"], base), Spacer(1, 5 * mm)]

    vat = bool(s["vat_enabled"])
    header = ["Pos", "Description", "Qty", "Unit", "Price", *(["VAT"] if vat else []), "Amount"]
    rows = [[_p(h, ParagraphStyle("th", parent=small, fontName=bold, textColor=colors.white,
                                  alignment=TA_RIGHT if i >= 2 else 0)) for i, h in enumerate(header)]]
    for i, ln in enumerate(d["lines"], 1):
        desc = ln["description"].split("\n", 1)
        cell = [_p(desc[0], strong)] + ([_p(desc[1], small)] if len(desc) > 1 else [])
        if ln["discount"]:
            cell.append(_p(f"less {ln['discount']:g} % discount", small))
        rows.append([_p(str(i), small), cell, _p(_qty(ln["quantity"]), right), _p(ln["unit"], base),
                     _p(fmt_chf(ln["unit_price"], "").strip(), right),
                     *([_p(f"{ln['vat_rate']:g} %", right)] if vat else []), _p(fmt_chf(ln["net"], "").strip(), right)])
    widths = [10 * mm, (72 if vat else 86) * mm, 14 * mm, 14 * mm, 22 * mm, *([14 * mm] if vat else []), 24 * mm]
    lt = Table(rows, colWidths=widths, repeatRows=1)
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.Color(0, 0, 0, alpha=0.12)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [lt, Spacer(1, 4 * mm)]

    tot = [["Subtotal" + (" (excl. VAT)" if vat else ""), fmt_chf(d["total_net"], d["currency"])]]
    for b in d["vat_breakdown"]:
        tot.append([f"VAT {b['rate']:g} % on {fmt_chf(b['base'], '').strip()}", fmt_chf(b["vat"], d["currency"])])
    if d.get("rounding"):
        tot.append(["Rounding", fmt_chf(d["rounding"], d["currency"])])
    tot.append(["Total" + (" incl. VAT" if vat and d["vat_breakdown"] else ""), fmt_chf(d["total"], d["currency"])])
    if is_invoice and d.get("paid_amount"):
        tot += [["Paid", fmt_chf(-d["paid_amount"], d["currency"])], ["Open", fmt_chf(d["open_amount"], d["currency"])]]
    last = len(tot) - (3 if is_invoice and d.get("paid_amount") else 1)     # the "Total" row
    total_style = ParagraphStyle("total", parent=right, fontName=bold, fontSize=11, textColor=accent)
    tt = Table([[_p(k, total_style if i == last else right), _p(v, total_style if i == last else right)]
                for i, (k, v) in enumerate(tot)], colWidths=[60 * mm, 32 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("LINEABOVE", (0, last), (-1, last), 0.8, accent),
                            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [KeepTogether([tt]), Spacer(1, 5 * mm)]

    footer = d.get("footer") or s[f"{d['kind']}_footer"]
    if footer:
        from .invoicing import _fill
        story += [_p(_fill(footer, d, s), base)]
    if vat and s["vat_number"]:
        story += [Spacer(1, 2 * mm), _p(f"VAT number: {s['vat_number']}", small)]
    elif not vat and is_invoice:
        story += [Spacer(1, 2 * mm), _p("Not subject to VAT.", small)]
    if is_invoice and not draft:
        story += [_QRPart(_qr_drawing(d, s))]
    elif is_invoice and draft and not _settings_incomplete(s):
        try:
            story += [_QRPart(_qr_drawing({**d, "number": d.get("number") or "DRAFT"}, s))]
        except BillingError:
            pass

    def page(canv, doc):
        canv.saveState()
        canv.setFont(font, 7.5)
        canv.setFillColor(muted)
        canv.drawRightString(A4[0] - 20 * mm, A4[1] - 8 * mm, f"{title} · page {doc.page}")   # top: the QR-bill owns the bottom
        if draft:
            canv.setFont(bold, 70)
            canv.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.10))
            canv.translate(A4[0] / 2, A4[1] / 2)
            canv.rotate(35)
            canv.drawCentredString(0, 0, "DRAFT")
        canv.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=14 * mm,
                            bottomMargin=BOTTOM, title=title, author=s["name"] or "", creator="Alfred")
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


def _settings_incomplete(s: dict) -> bool:
    from .invoicing import missing_for_invoices
    return bool(missing_for_invoices(s))
