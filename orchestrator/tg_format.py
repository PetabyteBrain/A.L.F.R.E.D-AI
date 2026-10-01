"""Markdown (as Alfred writes it) -> Telegram's HTML subset (parse_mode="HTML").

HTML mode is used rather than Telegram's MarkdownV2, which rejects a message over any single
unescaped character. Supported: **bold**, *italic* / _italic_, ~~strike~~, `code`, fenced code,
[links](https://…), # headings (bold), > quotes, - lists (•), and tables (monospaced block,
Telegram has no tables). Everything else is escaped, so model text can never inject markup.
"""
from __future__ import annotations

import html
import re

_FENCE = re.compile(r"```[^\n`]*\n?(.*?)(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*|___(?=\S)(.+?)(?<=\S)___")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
_RULE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", re.M)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _tables_to_pre(text: str, keep) -> str:
    out, block = [], []

    def flush():
        if block:
            rows = [ln for ln in block if not _TABLE_SEP.match(ln)]
            out.append(keep("<pre>" + html.escape("\n".join(r.strip() for r in rows)) + "</pre>"))
            block.clear()
    for line in text.split("\n"):
        if line.strip().startswith("|") and line.strip().endswith("|"):
            block.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


_TAG = re.compile(r"<(/?)(b|i|s|code|pre|a|blockquote)\b[^>]*>")


def well_formed(fragment: str) -> bool:
    """Tags properly nested and closed (Telegram rejects the whole message otherwise)."""
    stack: list[str] = []
    for m in _TAG.finditer(fragment):
        closing, tag = m.group(1), m.group(2)
        if not closing:
            stack.append(tag)
        elif not stack or stack.pop() != tag:
            return False
    return not stack


def md_to_html(text: str) -> str:
    """Convert; if some markup still comes out malformed, only the paragraphs concerned are sent as
    plain (escaped) text, so one odd construct doesn't cost the whole message its formatting."""
    out = _convert(text)
    if well_formed(out):
        return out
    parts = []
    for para in re.split(r"\n\s*\n", text):
        conv = _convert(para)
        parts.append(conv if well_formed(conv) else html.escape(para.strip(), quote=False))
    return "\n\n".join(p for p in parts if p)


def _convert(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = _FENCE.sub(lambda m: keep("<pre>" + html.escape(m.group(1).rstrip("\n"), quote=False) + "</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: keep("<code>" + html.escape(m.group(1), quote=False) + "</code>"), text)
    text = _tables_to_pre(text, keep)
    text = _LINK.sub(lambda m: keep(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'),
                     text)
    text = html.escape(text, quote=False)
    text = _RULE.sub("———", text)
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BOLD_ITALIC.sub(lambda m: f"<b><i>{m.group(1) or m.group(2)}</i></b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _BULLET.sub(lambda m: m.group(1) + "• ", text)
    # > quotes (escaped to &gt; by now): consecutive lines become one blockquote
    text = re.sub(r"(?m)((?:^&gt; ?.*(?:\n|$))+)",
                  lambda m: "<blockquote>" + re.sub(r"(?m)^&gt; ?", "", m.group(1)).rstrip("\n") + "</blockquote>\n",
                  text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text).strip()
