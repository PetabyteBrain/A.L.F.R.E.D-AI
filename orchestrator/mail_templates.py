"""Email layout: Markdown (as Alfred writes it) -> a branded HTML email + its plain-text twin.

Two templates, each with a banner image sent inline (Content-ID, so it shows without loading
anything from the web):
- Layouts come from the Template manager (orchestrator/email_templates.py): colours, font, signature, footer and
  banner per layout; the built-in "company" and "alfred" layouts start as the original designs below.
- "company": formal mail in the company's name (inquiries, mail sent for the company). Built-in banner from
  mail_assets/company-banner.png, which is git-ignored (it carries the real company's logo); the
  neutral company-banner.example.png is used when it's missing.
- "alfred": Alfred's own, personal mail (replies to registered people, reminders, codes, mail sent for
  someone personally). Built-in banner mail_assets/alfred-banner.png.

Model text is escaped first and only a small Markdown subset becomes markup (bold, italic, code,
https/mailto links, headings, lists, quotes, rules), so nothing in a reply can inject HTML.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

ASSETS = Path(__file__).parent / "mail_assets"
TEMPLATES = ("alfred", "company")

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:https://|mailto:)[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'=>/])\bhttps://[^\s<>()]+[^\s<>().,;:!?'\"]")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


@dataclass(frozen=True)
class Theme:
    page: str          # outer background
    card: str          # body background
    ink: str           # body text
    muted: str
    accent: str        # hairline under the banner, links
    foot_bg: str
    foot_ink: str
    foot_muted: str
    head_font: str
    body_font: str


THEMES = {
    # Black and ivory with a green hairline (the company site's palette), serif type.
    "company": Theme(page="#e9e6de", card="#f8f6f0", ink="#1b1b1a", muted="#6b6860", accent="#1f7a46",
                     foot_bg="#060606", foot_ink="#f4f1ea", foot_muted="#8d8a81",
                     head_font="'Cormorant Garamond', Garamond, Georgia, 'Times New Roman', serif",
                     body_font="Georgia, 'Times New Roman', serif"),
    # The butler: tailcoat black and silver, ivory paper.
    "alfred": Theme(page="#e6e5e1", card="#f6f5f1", ink="#1c1c1e", muted="#66666c", accent="#4a4c54",
                    foot_bg="#0c0c0e", foot_ink="#e8e6e0", foot_muted="#9c9ca2",
                    head_font="Garamond, Georgia, 'Times New Roman', serif",
                    body_font="Georgia, 'Times New Roman', serif"),
}


@dataclass
class Rendered:
    html: str
    text: str
    images: list[tuple[str, bytes, str]] = field(default_factory=list)   # (content-id, data, subtype)
    layout: object = None


def banner(template: str) -> tuple[bytes, str] | None:
    """(PNG bytes, file name) of the template's banner, or None if no image is available."""
    names = ("company-banner.png", "company-banner.example.png") if template == "company" else ("alfred-banner.png",)
    for name in names:
        f = ASSETS / name
        if f.is_file():
            return f.read_bytes(), name
    return None


# --- Markdown -> HTML (safe subset) -------------------------------------------------------------

def _inline(text: str, t: Theme) -> str:
    keep: list[str] = []

    def stash(fragment: str) -> str:
        keep.append(fragment)
        return f"\x00{len(keep) - 1}\x00"

    text = _INLINE_CODE.sub(lambda m: stash(
        f'<code style="font-family:Consolas,Menlo,monospace;font-size:14px;background:rgba(0,0,0,.05);'
        f'padding:1px 4px;border-radius:3px">{html.escape(m.group(1))}</code>'), text)
    text = _LINK.sub(lambda m: stash(
        f'<a href="{html.escape(m.group(2), quote=True)}" style="color:{t.accent};text-decoration:underline">'
        f'{html.escape(m.group(1))}</a>'), text)
    text = _BARE_URL.sub(lambda m: stash(
        f'<a href="{html.escape(m.group(0), quote=True)}" style="color:{t.accent};text-decoration:underline">'
        f'{html.escape(m.group(0))}</a>'), text)
    text = html.escape(text, quote=False)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)


def md_to_html(md: str, t: Theme) -> str:
    p = f'style="margin:0 0 16px;font-family:{t.body_font};font-size:16px;line-height:1.6;color:{t.ink}"'
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""
    quote: list[str] = []

    def flush_para():
        if para:
            out.append(f"<p {p}>" + "<br>".join(_inline(x, t) for x in para) + "</p>")
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            out.append(f'<{list_tag} style="margin:0 0 16px;padding-left:22px;font-family:{t.body_font};'
                       f'font-size:16px;line-height:1.6;color:{t.ink}">'
                       + "".join(f'<li style="margin:0 0 6px">{_inline(i, t)}</li>' for i in items)
                       + f"</{list_tag}>")
            items.clear()
        list_tag = ""

    def flush_quote():
        if quote:
            out.append(f'<blockquote style="margin:0 0 16px;padding:4px 0 4px 14px;border-left:3px solid {t.accent};'
                       f'color:{t.muted};font-family:{t.body_font};font-size:15px;line-height:1.6">'
                       + "<br>".join(_inline(q, t) for q in quote) + "</blockquote>")
            quote.clear()

    def flush_all():
        flush_para()
        flush_list()
        flush_quote()

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_all()
            continue
        if _RULE.match(line):
            flush_all()
            out.append(f'<hr style="border:0;border-top:1px solid rgba(0,0,0,.12);margin:20px 0">')
            continue
        m = _HEADING.match(line)
        if m:
            flush_all()
            out.append(f'<h2 style="margin:22px 0 10px;font-family:{t.head_font};font-size:21px;font-weight:600;'
                       f'line-height:1.3;color:{t.ink}">{_inline(m.group(1), t)}</h2>')
            continue
        if line.lstrip().startswith(">"):
            flush_para()
            flush_list()
            quote.append(line.lstrip()[1:].strip())
            continue
        m = _BULLET.match(line) or _NUMBER.match(line)
        if m:
            flush_para()
            flush_quote()
            tag = "ul" if _BULLET.match(line) else "ol"
            if list_tag and tag != list_tag:
                flush_list()
            list_tag = tag
            items.append(m.group(1))
            continue
        if items and raw.startswith(("  ", "\t")):        # continuation of a list item
            items[-1] += " " + line.strip()
            continue
        flush_list()
        flush_quote()
        para.append(line.strip())
    flush_all()
    return "\n".join(out)


def md_to_text(md: str) -> str:
    """Plain-text twin: Markdown markers removed, links as 'text (url)'."""
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2).removeprefix('mailto:')})", md or "")
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _INLINE_CODE.sub(lambda m: m.group(1), text)
    lines = []
    for line in text.split("\n"):
        m = _HEADING.match(line)
        lines.append(m.group(1).upper() if m else re.sub(r"^\s*[*+]\s+", "- ", line))
    return "\n".join(lines).strip()


# --- the email -----------------------------------------------------------------------------------

SERIF_HEAD = "'Cormorant Garamond', Garamond, Georgia, 'Times New Roman', serif"
SERIF_BODY = "Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"


@dataclass
class Layout:
    """What render() needs from a layout (orchestrator/email_templates.py, or the built-in fallback)."""
    id: int | None
    key: str
    theme: Theme
    signature: str
    footer: str
    from_name: str = ""
    company: bool = False
    banner: tuple[bytes, str] | None = None


def _fallback(key: str) -> Layout:
    from . import email_templates as et
    k = "company" if key == "company" else "alfred"
    img = banner(k)
    return Layout(None, k, THEMES[k], et.BUILTIN_SIGNATURE[k], et.BUILTIN_FOOTER[k], company=k == "company",
                  banner=(img[0], "png") if img else None)


def resolve(template) -> Layout:
    """A layout by id / built-in key / name (inactive or missing ones fall back as email_templates.layout_for)."""
    from . import email_templates as et
    try:
        lay = et.layout_for(template)
    except Exception:  # noqa: BLE001 — no database (yet): the original designs
        return _fallback(str(template))
    st = lay["style"]
    sans = st.get("font") == "sans"
    theme = Theme(page=st["page"], card=st["card"], ink=st["ink"], muted=st["muted"], accent=st["accent"],
                  foot_bg=st["foot_bg"], foot_ink=st["foot_ink"], foot_muted=st["foot_muted"],
                  head_font=SANS if sans else SERIF_HEAD, body_font=SANS if sans else SERIF_BODY)
    return Layout(lay["id"], lay.get("builtin") or str(lay["id"]), theme, lay["signature_md"], lay["footer_md"],
                  lay["from_name"], lay["company"], et.banner_bytes(lay["id"]))


def _layout_values() -> dict:
    return {"assistant": settings.assistant_name, "company": settings.company_name,
            "website": settings.company_website, "tagline": settings.company_tagline}


def _fill_blank(text: str) -> str:
    """Layout fields; unknown or empty ones vanish (a footer without a tagline just loses that line)."""
    vals = _layout_values()
    return re.sub(r"\{([a-z_]+)\}", lambda m: str(vals.get(m.group(1)) or ""), text or "")


def signature(template, on_behalf_of: str = "") -> str:
    lay = template if isinstance(template, Layout) else resolve(template)
    sig = _fill_blank(lay.signature).strip()
    if on_behalf_of:
        sig += f"\non behalf of {on_behalf_of}"
    return sig


def footer_lines(template) -> list[str]:
    lay = template if isinstance(template, Layout) else resolve(template)
    lines = [x.strip() for x in _fill_blank(lay.footer).split("\n")]
    return [x for x in lines if x]


# A closing line the model sometimes adds although the layout signs the email ("Best regards," + name lines).
_CLOSING = re.compile(
    r"^[*_\s]*(?:"
    r"(?:with\s+)?(?:(?:best|kind|kindest|warm|warmest|many|all\s+the\s+best)\s+)?(?:regards|wishes)"
    r"|(?:yours\s+)?(?:sincerely|faithfully|truly|respectfully)(?:\s+yours)?"
    r"|best|cheers|thanks(?:\s+again)?|thank\s+you|many\s+thanks|at\s+your\s+service"
    r"|mit\s+freundlichen\s+gr(?:ü|ue)ssen|freundliche\s+gr(?:ü|ue)sse|liebe\s+gr(?:ü|ue)sse"
    r")[*_\s]*[,.!]?[*_\s]*$", re.I)


def strip_signoff(body_md: str) -> str:
    """Drop a trailing sign-off the writer added ("Best regards,\nAlfred\nAI assistant"): the layout adds the real
    signature. Only the last few short lines are looked at, so a "thanks" inside the text stays."""
    lines = (body_md or "").rstrip().split("\n")
    for i in range(len(lines) - 1, max(-1, len(lines) - 7), -1):
        line = lines[i].strip()
        if not line:
            continue
        if _CLOSING.match(line):
            tail = [x.strip() for x in lines[i + 1:] if x.strip()]
            if len(tail) <= 4 and all(len(x.split()) <= 8 for x in tail):
                return "\n".join(lines[:i]).rstrip()
            break
        if len(line.split()) > 8:                    # a real sentence: no sign-off block below this point
            break
    return (body_md or "").rstrip()


def render(template, body_md: str, *, subject: str = "", greeting: str = "", on_behalf_of: str = "",
           sign: bool = True) -> Rendered:
    lay = template if isinstance(template, Layout) else resolve(template)
    t = lay.theme
    text = (body_md or "").strip()
    if sign and lay.signature.strip():
        text = strip_signoff(text)
    body = (f"{greeting}\n\n" if greeting else "") + text
    if sign and lay.signature.strip():
        body += "\n\n" + signature(lay, on_behalf_of)
    cid = f"banner-{lay.key}@alfred"
    alt = settings.company_name if lay.company else settings.assistant_name
    head = (f'<img src="cid:{cid}" width="600" alt="{html.escape(alt)}" style="display:block;width:100%;'
            f'max-width:600px;height:auto;border:0">' if lay.banner else
            f'<div style="padding:28px 32px;background:{t.foot_bg};color:{t.foot_ink};font-family:{t.head_font};'
            f'font-size:26px;letter-spacing:.18em">{html.escape(alt.upper())}</div>')
    foot = footer_lines(lay)
    foot_html = "".join(
        f'<div style="margin:0 0 6px;font-family:{t.body_font};font-size:{"12px;letter-spacing:.22em" if i == 0 else "12px"};'
        f'line-height:1.5;color:{t.foot_ink if i == 0 else t.foot_muted}">'
        f'{html.escape(line.upper()) if i == 0 else _inline(line, t)}</div>'
        for i, line in enumerate(foot))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:{t.page}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{t.page}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:{t.card};border-collapse:collapse">
<tr><td style="padding:0;background:{t.foot_bg}">{head}</td></tr>
<tr><td style="height:3px;line-height:3px;font-size:0;background:{t.accent}">&nbsp;</td></tr>
<tr><td style="padding:32px 36px 20px">
{md_to_html(body, t)}
</td></tr>
<tr><td style="padding:22px 36px 24px;background:{t.foot_bg}">{foot_html}</td></tr>
</table>
</td></tr></table>
</body></html>"""
    text = md_to_text(body) + ("\n\n-- \n" + "\n".join(md_to_text(x) for x in foot) if foot else "")
    out = Rendered(html=page, text=text, images=[(cid, lay.banner[0], lay.banner[1])] if lay.banner else [])
    out.layout = lay
    return out


def preview_html(r: Rendered) -> str:
    """The HTML with the inline banner as a data: URL, for showing in the browser (Template manager)."""
    import base64
    page = r.html
    for cid, data, subtype in r.images:
        page = page.replace(f"cid:{cid}", f"data:image/{subtype};base64,{base64.b64encode(data).decode()}")
    return page
