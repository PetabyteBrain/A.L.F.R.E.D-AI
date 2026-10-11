"""Alfred's own email address: sending (SMTP), the message log, the NAS archive and retention.

- Sending goes through the SMTP account in .env (MAIL_ADDRESS, SMTP_*), from Alfred's address. Mail for
  someone else shows "<Name> via Alfred" as the sender; replies come back to Alfred (Reply-To), which
  files them and tells the person (mail_inbound.py). Layout: mail_templates.py ("alfred" / "company").
- Receiving: mail_inbound.py (provider webhook -> who sent it -> request / reply / inquiry).
- Every message in and out is a row in `emails` and an .eml file in the archive:
    personal mail -> <personal share>/<person's folder>/Mail/<year>/<month>/  (their Files tab)
    company mail  -> <company share>/Mail/<year>/<month>/                     (inquiries, company mail)
  Until the NAS has it, the .eml waits in data/mail/ (archive_pending retries).
- Retention (purge): personal mail MAIL_RETENTION_DAYS (default 2 years), company mail
  MAIL_COMPANY_RETENTION_DAYS (default 10 years: business correspondence, OR 958f), spam and automated
  mail 30 days (never archived on the NAS). 0 = keep forever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, mail_templates
from .config import settings

log = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$")
MAX_RECIPIENTS = 10
MAX_ATTACH_BYTES = 15_000_000
SMTP_TIMEOUT_S = 30
JUNK_KEEP_DAYS = 30
JUNK_KINDS = ("spam", "automated")
_LAST_RUN_KEY = "mail_retention_last_run"


class MailError(ValueError):
    """Message is safe to show to the user / the model."""


def enabled() -> bool:
    return settings.mail_enabled


def _domain() -> str:
    return settings.mail_address.rsplit("@", 1)[-1] if "@" in settings.mail_address else "localhost"


def clean_address(raw: str) -> str:
    addr = parseaddr(str(raw or ""))[1].strip().lower()
    if not ADDRESS_RE.match(addr):
        raise MailError(f"'{str(raw)[:80]}' isn't a valid email address.")
    return addr


def is_own_address(addr: str) -> bool:
    return bool(settings.mail_address) and (addr or "").strip().lower() == settings.mail_address


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- building and sending ------------------------------------------------------------------------

def build_message(*, to: list[str], subject: str, body_md: str, template: str = "alfred", cc: list[str] = (),
                  from_name: str = "", reply_to: str = "", in_reply_to: str = "", references: str = "",
                  attachments: list[tuple[str, bytes, str]] = (), auto: str = "", greeting: str = "",
                  on_behalf_of: str = "", sign: bool = True) -> EmailMessage:
    """The full message (HTML + text + inline banner + attachments). `auto`: Auto-Submitted value for
    machine-made mail ("auto-replied" / "auto-generated"), so other systems don't answer it."""
    r = mail_templates.render(template, body_md, subject=subject, greeting=greeting, on_behalf_of=on_behalf_of,
                              sign=sign)
    msg = EmailMessage()
    msg["From"] = formataddr((from_name or settings.mail_from_name or settings.assistant_name, settings.mail_address))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = " ".join(str(subject or "").split())[:200] or "(no subject)"
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=_domain())
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if in_reply_to not in references else references
    if auto:
        msg["Auto-Submitted"] = auto
        msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(r.text)
    msg.add_alternative(r.html, subtype="html")
    html_part = msg.get_payload()[-1]
    for cid, data, subtype in r.images:
        html_part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>",
                              filename=f"{cid.split('@')[0]}.{subtype}", disposition="inline")
    for name, data, mime in attachments:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return msg


def _smtp_send(msg: EmailMessage, recipients: list[str]) -> None:
    ctx = ssl.create_default_context()
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S)
    try:
        if settings.smtp_port != 465:
            server.starttls(context=ctx)
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg, from_addr=settings.mail_address, to_addrs=recipients)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


# Tests replace this with a recorder; production sends over SMTP.
transport = _smtp_send


async def send(*, to: list[str], subject: str, body_md: str, template: str = "alfred", cc: list[str] = (),
               user_id: int | None = None, scope: str = "personal", kind: str = "sent", on_behalf_of: str = "",
               from_name: str = "", reply_to: str = "", parent: dict | None = None,
               attachments: list[tuple[str, bytes, str]] = (), auto: str = "", greeting: str = "",
               sign: bool = True, summary: str = "") -> dict:
    """Send one email and log + archive it. `parent`: the email row this answers (threading).
    Raises MailError (nothing logged as sent) if it couldn't go out."""
    if not enabled():
        raise MailError("Email isn't set up yet (MAIL_ADDRESS / SMTP_* in .env).")
    to = list(dict.fromkeys(clean_address(a) for a in to if a))
    cc = [a for a in dict.fromkeys(clean_address(a) for a in cc if a) if a not in to]
    if not to:
        raise MailError("Who should it go to? No valid address.")
    if len(to) + len(cc) > MAX_RECIPIENTS:
        raise MailError(f"At most {MAX_RECIPIENTS} recipients per email.")
    if any(is_own_address(a) for a in to + cc):
        raise MailError("That's my own address.")
    if sum(len(a[1]) for a in attachments) > MAX_ATTACH_BYTES:
        raise MailError("The attachments are too big together (max 15 MB).")
    in_reply_to = references = ""
    if parent:
        in_reply_to = parent.get("message_id") or ""
        references = await asyncio.to_thread(_references_of, parent)
    msg = build_message(to=to, cc=cc, subject=subject, body_md=body_md, template=template, from_name=from_name,
                        reply_to=reply_to, in_reply_to=in_reply_to, references=references, attachments=attachments,
                        auto=auto, greeting=greeting, on_behalf_of=on_behalf_of, sign=sign)
    try:
        await asyncio.to_thread(transport, msg, to + cc)
    except smtplib.SMTPRecipientsRefused:
        raise MailError("The mail server refused that address.")
    except (smtplib.SMTPException, OSError) as e:
        log.warning("SMTP send failed: %s", e)
        db.set_health("email", False, f"sending failed: {type(e).__name__}")
        raise MailError("The email couldn't be sent just now (mail server problem). Try again shortly.")
    db.set_health("email", True, f"sent as {settings.mail_address}")
    row_id = record(direction="out", kind=kind, status="sent", scope=scope, user_id=user_id,
                    message_id=msg["Message-ID"], in_reply_to=in_reply_to, from_addr=settings.mail_address,
                    from_name=parseaddr(msg["From"])[0], to=to, cc=cc, subject=msg["Subject"],
                    body_text=mail_templates.md_to_text(body_md), summary=summary,
                    attachments=[{"name": n, "size": len(d), "type": m} for n, d, m in attachments],
                    thread_id=(parent or {}).get("thread_id") or (parent or {}).get("id"))
    spool(row_id, msg.as_bytes())
    asyncio.get_running_loop().run_in_executor(None, _archive_quietly, row_id)
    db.log_activity("message", f"Email sent to {', '.join(to)[:120]}: {msg['Subject'][:80]}", user_id=user_id)
    return get(row_id)


def _references_of(parent: dict) -> str:
    """References chain of a stored message (from its .eml headers when available)."""
    raw = read_raw(parent, quiet=True)
    refs = ""
    if raw:
        from email import message_from_bytes, policy
        try:
            refs = str(message_from_bytes(raw, policy=policy.default).get("References", "") or "")
        except Exception:  # noqa: BLE001
            refs = ""
    return " ".join(refs.split()[-10:])


async def send_verification_code(address: str, code: str) -> None:
    await send(to=[address], subject=f"Your {settings.assistant_name} verification code: {code}",
               body_md=(f"Your code to confirm this address on the dashboard is **{code}**.\n\n"
                        "It is valid for 10 minutes. If you didn't ask for it, simply ignore this email."),
               template="alfred", kind="notice", scope="company", auto="auto-generated",
               summary="verification code")


async def notify_person(user_id: int, subject: str, body_md: str, *, template: str = "alfred") -> dict:
    """An email to a registered person's primary address (fallback when they have no Telegram)."""
    from .identity import registry
    addr = registry.email_for(user_id)
    if not addr:
        raise MailError("They have no verified email address.")
    person = registry.get_person(user_id)
    return await send(to=[addr], subject=subject, body_md=body_md, template=template, user_id=user_id,
                      scope="personal", kind="notice", auto="auto-generated",
                      greeting=f"Dear {person.display_name.split()[0]}," if person else "")


# --- the log -------------------------------------------------------------------------------------

def record(*, direction: str, kind: str, status: str, scope: str, user_id: int | None = None, message_id: str = "",
           in_reply_to: str = "", from_addr: str = "", from_name: str = "", to: list[str] = (), cc: list[str] = (),
           subject: str = "", body_text: str = "", own_text: str = "", summary: str = "", attachments: list[dict] = (),
           auth: dict | None = None, provider_id: str | None = None, delete_url: str | None = None,
           thread_id: int | None = None) -> int:
    row_id = db.execute(
        "INSERT INTO emails(direction, kind, status, scope, user_id, message_id, in_reply_to, thread_id, from_addr, "
        "from_name, to_addrs, cc_addrs, subject, body_text, own_text, attachments, auth, summary, provider_id, "
        "delete_url, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (direction, kind, status, scope, user_id, message_id or None, in_reply_to or None, thread_id, from_addr,
         from_name[:200], json.dumps(list(to)), json.dumps(list(cc)), subject[:300], body_text[:200_000],
         own_text[:50_000], json.dumps(list(attachments)), json.dumps(auth or {}), summary[:2000], provider_id, delete_url,
         db.now_iso()))
    if thread_id is None:
        db.execute("UPDATE emails SET thread_id = id WHERE id = ?", (row_id,))
    return row_id


def update(row_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE emails SET {cols} WHERE id = ?", (*fields.values(), row_id))


def get(row_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM emails WHERE id = ?", (row_id,))
    if row:
        for k in ("to_addrs", "cc_addrs", "attachments", "auth"):
            try:
                row[k] = json.loads(row[k] or ("{}" if k == "auth" else "[]"))
            except ValueError:
                row[k] = {} if k == "auth" else []
    return row


def by_message_id(message_ids: list[str]) -> dict | None:
    """The newest stored email with one of these Message-IDs (threading)."""
    ids = [m.strip() for m in message_ids if m and m.strip()][:30]
    if not ids:
        return None
    row = db.query_one(f"SELECT id FROM emails WHERE message_id IN ({','.join('?' * len(ids))}) "
                       "ORDER BY id DESC LIMIT 1", ids)
    return get(row["id"]) if row else None


def local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.timezone)).strftime("%a %d %b %Y %H:%M")


def may_read(row: dict, person) -> bool:
    """Own mail (personal scope) for everyone; company mail for admins and cofounders."""
    from .nas import COMPANY_ROLES
    if row["scope"] == "company":
        return person.role in COMPANY_ROLES
    return row["user_id"] == person.id


# --- archive -------------------------------------------------------------------------------------

def _spool_dir() -> Path:
    d = settings.db_path.parent / "mail"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spool(row_id: int, raw: bytes) -> Path:
    path = _spool_dir() / f"{row_id}.eml"
    path.write_bytes(raw)
    update(row_id, spool_path=path.name)
    return path


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _file_name(row: dict) -> str:
    when = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(settings.timezone))
    who = row["from_addr"] if row["direction"] == "in" else (json.loads(row["to_addrs"] or "[]") or [""])[0]
    who = who.split("@")[0][:30]
    subject = _UNSAFE.sub(" ", row["subject"] or "no subject")
    subject = " ".join(subject.split()).strip(" .")[:70] or "no subject"
    arrow = "from" if row["direction"] == "in" else "to"
    return f"{when:%Y-%m-%d %H%M} {arrow} {_UNSAFE.sub('', who) or 'unknown'} - {subject} #{row['id']}.eml"


def _target(row: dict) -> tuple[str, str] | None:
    """(UNC folder, label prefix) for this email's archive copy, or None if it isn't archived on the NAS."""
    if row["kind"] in JUNK_KINDS:
        return None
    when = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(settings.timezone))
    sub = f"{settings.mail_archive_folder}\\{when:%Y}\\{when:%m}"
    h = settings.nas_host
    if row["scope"] == "personal" and row["user_id"]:
        from . import nas
        from .identity.registry import Person
        u = db.query_one("SELECT id, display_name, role FROM users WHERE id = ?", (row["user_id"],))
        if not u:
            return None
        folder = nas.personal_folder(Person(**u))
        return rf"\\{h}\{settings.nas_share_personal}\{folder}\{sub}", f"personal:{folder}\\{sub}"
    return rf"\\{h}\{settings.nas_share_company}\{sub}", f"company:{sub}"


def archive(row_id: int) -> bool:
    """Copy the spooled .eml to the NAS (blocking). True once the NAS has it (or it isn't archived there)."""
    from . import nas
    row = db.query_one("SELECT * FROM emails WHERE id = ?", (row_id,))
    if not row or row["nas_path"] or not row["spool_path"]:
        return bool(row and row["nas_path"])
    target = _target(row)
    if target is None or not nas.enabled():
        return target is None
    local = _spool_dir() / row["spool_path"]
    if not local.is_file():
        return False
    folder, label = target
    name = _file_name(row)
    smb = nas._smb()

    def go():
        nas._ensure_dir(smb, folder)
        with smb.open_file(folder + "\\" + name, mode="wb") as f:
            f.write(local.read_bytes())
    nas._call(go)
    update(row_id, nas_path=f"{label}\\{name}", spool_path=None)
    local.unlink(missing_ok=True)
    return True


def _archive_quietly(row_id: int) -> None:
    try:
        archive(row_id)
    except Exception as e:  # noqa: BLE001 — retried by archive_pending
        log.warning("Mail archive of #%s failed (will retry): %s", row_id, e)


def archive_pending() -> int:
    """Retry archiving spooled mail (NAS was down, or not configured before). Returns how many moved."""
    from . import nas
    if not nas.enabled():
        return 0
    n = 0
    for r in db.query("SELECT id FROM emails WHERE spool_path IS NOT NULL AND nas_path IS NULL "
                      "AND status != 'received' AND kind NOT IN ('spam','automated') ORDER BY id LIMIT 200"):
        try:
            n += archive(r["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("Mail archive retry failed: %s", e)
            break
    return n


def _unc(nas_path: str) -> str:
    scope, _, rel = nas_path.partition(":")
    if ".." in rel.split("\\"):
        raise PermissionError("path escapes the archive")
    share = settings.nas_share_personal if scope == "personal" else settings.nas_share_company
    return rf"\\{settings.nas_host}\{share}\{rel}"


def read_raw(row: dict, quiet: bool = False) -> bytes | None:
    """The stored .eml (spool or NAS)."""
    try:
        if row.get("spool_path"):
            f = _spool_dir() / row["spool_path"]
            if f.is_file():
                return f.read_bytes()
        if row.get("nas_path"):
            from . import nas
            smb = nas._smb()

            def go():
                with smb.open_file(_unc(row["nas_path"]), mode="rb") as f:
                    return f.read()
            return nas._call(go)
    except Exception:  # noqa: BLE001
        if not quiet:
            raise
        log.info("Couldn't read the stored copy of email #%s", row.get("id"))
    return None


def _delete_copies(row: dict) -> None:
    if row.get("spool_path"):
        (_spool_dir() / row["spool_path"]).unlink(missing_ok=True)
    if row.get("nas_path"):
        from . import nas
        smb = nas._smb()

        def go():
            try:
                smb.remove(_unc(row["nas_path"]))
            except FileNotFoundError:
                pass
        nas._call(go)


# --- retention -----------------------------------------------------------------------------------

def purge(now: datetime | None = None) -> int:
    now = now or _now()
    rules = [("kind IN ('spam','automated')", JUNK_KEEP_DAYS),
             ("scope = 'personal' AND kind NOT IN ('spam','automated')", settings.mail_retention_days),
             ("scope = 'company' AND kind NOT IN ('spam','automated')", settings.mail_company_retention_days)]
    removed = 0
    for where, days in rules:
        if days <= 0:
            continue
        cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
        for row in db.query(f"SELECT * FROM emails WHERE {where} AND created_at < ? LIMIT 500", (cutoff,)):
            try:
                _delete_copies(row)
            except Exception:  # noqa: BLE001
                log.warning("Couldn't delete the archived copy of email #%s", row["id"], exc_info=True)
                continue
            db.execute("UPDATE escalations SET email_id = NULL WHERE email_id = ?", (row["id"],))
            db.execute("DELETE FROM emails WHERE id = ?", (row["id"],))
            removed += 1
    if removed:
        db.log_activity("system", f"Retention: deleted {removed} email(s) past their keeping period")
    return removed


def daily() -> None:
    """From the health loop: archive retries every run, retention once a day."""
    archive_pending()
    today = _now().date().isoformat()
    if db.get_setting(_LAST_RUN_KEY) == today:
        return
    purge()
    db.set_setting(_LAST_RUN_KEY, today)


def erase_user(user_id: int) -> int:
    """Data deletion: a person's personal mail (DB rows + spooled copies; the NAS copies go with their
    personal folder). Company mail they were part of stays, without the link to them."""
    rows = db.query("SELECT * FROM emails WHERE user_id = ? AND scope = 'personal'", (user_id,))
    for row in rows:
        if row.get("spool_path"):
            (_spool_dir() / row["spool_path"]).unlink(missing_ok=True)
    with db.tx() as conn:
        conn.execute("UPDATE escalations SET email_id = NULL WHERE email_id IN "
                     "(SELECT id FROM emails WHERE user_id = ? AND scope = 'personal')", (user_id,))
        conn.execute("DELETE FROM emails WHERE user_id = ? AND scope = 'personal'", (user_id,))
        conn.execute("UPDATE emails SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE escalations SET requester_id = NULL WHERE requester_id = ?", (user_id,))
    return len(rows)


# --- health --------------------------------------------------------------------------------------

_last_probe: tuple[float, tuple[bool, str]] | None = None
PROBE_EVERY_S = 1800


def _probe() -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    try:
        if settings.smtp_port != 465:
            server.starttls(context=ctx)
        server.login(settings.smtp_user, settings.smtp_password)
        server.noop()
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    return True, f"SMTP login ok as {settings.mail_address}"


async def health() -> tuple[bool, str]:
    """SMTP login (at most every 30 min) + whether inbound mail is set up."""
    global _last_probe
    import time
    if not enabled():
        return False, "not configured"
    now = time.monotonic()
    if _last_probe is None or now - _last_probe[0] > PROBE_EVERY_S or not _last_probe[1][0]:
        try:
            res = await asyncio.to_thread(_probe)
        except smtplib.SMTPAuthenticationError:
            res = (False, "SMTP login refused (check SMTP_USER / SMTP_PASSWORD)")
        except Exception as e:  # noqa: BLE001
            res = (False, f"SMTP unreachable: {type(e).__name__}")
        _last_probe = (now, res)
    ok, detail = _last_probe[1]
    if ok and not settings.mail_inbound_enabled:
        detail += " · receiving off (MAIL_INBOUND_TOKEN)"
    return ok, detail
