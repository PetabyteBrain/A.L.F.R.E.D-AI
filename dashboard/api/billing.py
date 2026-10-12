"""Quotes and invoices on the CRM tab (billing.use). All rules live in orchestrator/invoicing.py: drafts change freely,
issuing numbers and freezes a document, anything sent to a client waits for approval (billing.approve)."""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import approvals, crm
from orchestrator import invoicing as billing
from orchestrator.identity.registry import Person

from .routes import current_person, requires

router = APIRouter(prefix="/api/billing", dependencies=[Depends(requires("billing.use"))])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocBody(Strict):
    fields: dict = Field(default_factory=dict)


class SendBody(Strict):
    to: str = Field(default="", max_length=254)
    message: str = Field(default="", max_length=2000)


class CancelBody(Strict):
    reason: str = Field(default="", max_length=200)


class CopyBody(Strict):
    kind: Literal["quote", "invoice"] | None = None


class Outcome(Strict):
    accepted: bool


class PaymentBody(Strict):
    amount: str | float | None = None
    paid_on: str | None = Field(default=None, max_length=10)
    note: str = Field(default="", max_length=200)


class SettingsBody(Strict):
    settings: dict


class ProductBody(Strict):
    fields: dict


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (billing.BillingError, crm.CrmError, approvals.ApprovalError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    s = billing.get_settings()
    return {"summary": _run(billing.summary, person), "missing": billing.missing_for_invoices(s),
            "vat_enabled": s["vat_enabled"], "vat_rates": s["vat_rates"], "default_vat": s["default_vat"],
            "payment_days": s["payment_days"], "quote_valid_days": s["quote_valid_days"],
            "products": billing.products(),
            "can": {"settings": person.can("billing.settings"), "approve": person.can("billing.approve")}}


@router.get("/docs")
async def list_docs(person: Person = Depends(current_person), kind: Literal["quote", "invoice", ""] = "invoice",
                    state: str = Query(default="", max_length=20), q: str = Query(default="", max_length=80),
                    company_id: int | None = Query(default=None, ge=1), deal_id: int | None = Query(default=None, ge=1),
                    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return _run(billing.search, person, kind=kind, state=state, q=q, company_id=company_id, deal_id=deal_id,
                offset=offset, limit=limit)


@router.post("/docs")
async def create_doc(body: DocBody, person: Person = Depends(current_person)) -> dict:
    return _run(billing.save, person, body.fields)


@router.get("/docs/{doc_id}")
async def get_doc(doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.get, person, doc_id)


@router.patch("/docs/{doc_id}")
async def update_doc(body: DocBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.save, person, body.fields, doc_id)


@router.delete("/docs/{doc_id}")
async def discard_doc(doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    _run(billing.discard, person, doc_id)
    return {"ok": True}


@router.get("/docs/{doc_id}/pdf")
async def doc_pdf(doc_id: int = Path(ge=1), person: Person = Depends(current_person), download: bool = False) -> Response:
    data, name = _run(billing.pdf_bytes, person, doc_id)
    disposition = "attachment" if download else "inline"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
                             "Cache-Control": "no-store"})


@router.post("/docs/{doc_id}/issue")
async def issue_doc(doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.issue, person, doc_id)


@router.post("/docs/{doc_id}/send")
async def send_doc(body: SendBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.request_send, person, doc_id, to=body.to, message=body.message)


@router.post("/docs/{doc_id}/remind")
async def remind_doc(body: SendBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.request_reminder, person, doc_id, message=body.message)


@router.post("/docs/{doc_id}/cancel")
async def cancel_doc(body: CancelBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.request_cancel, person, doc_id, body.reason)


@router.post("/docs/{doc_id}/copy")
async def copy_doc(body: CopyBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.copy, person, doc_id, body.kind)


@router.post("/docs/{doc_id}/outcome")
async def quote_outcome(body: Outcome, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.set_quote_outcome, person, doc_id, body.accepted)


@router.post("/docs/{doc_id}/payments")
async def add_payment(body: PaymentBody, doc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.add_payment, person, doc_id, body.amount, body.paid_on, body.note)


@router.delete("/payments/{payment_id}")
async def remove_payment(payment_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.remove_payment, person, payment_id)


@router.get("/recipient")
async def recipient(person: Person = Depends(current_person), company_id: int | None = Query(default=None, ge=1),
                    contact_id: int | None = Query(default=None, ge=1)) -> dict:
    return _run(billing.recipient_for, person, company_id, contact_id)


@router.get("/settings")
async def get_settings(person: Person = Depends(current_person)) -> dict:
    return billing.get_settings()


@router.put("/settings")
async def put_settings(body: SettingsBody, person: Person = Depends(current_person)) -> dict:
    return _run(billing.save_settings, person, body.settings)


@router.get("/products")
async def list_products(person: Person = Depends(current_person), all: bool = False) -> list[dict]:
    return billing.products(include_inactive=all)


@router.post("/products")
async def add_product(body: ProductBody, person: Person = Depends(current_person)) -> dict:
    return _run(billing.save_product, person, body.fields)


@router.patch("/products/{product_id}")
async def change_product(body: ProductBody, product_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(billing.save_product, person, body.fields, product_id)


@router.get("/export")
async def export(person: Person = Depends(current_person), kind: Literal["quote", "invoice"] = "invoice",
                 year: int | None = Query(default=None, ge=2000, le=2100)) -> Response:
    text, name = _run(billing.export_csv, person, year=year, kind=kind)
    return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
