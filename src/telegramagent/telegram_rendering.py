from __future__ import annotations

import html
import re
from urllib.parse import quote

TELEGRAM_PARSE_MODE = "HTML"
_FENCED_CODE_RE = re.compile(r"```(?:([^\n`]*)\n)?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>()]+", flags=re.IGNORECASE)


def telegram_html_chunks(text: str, *, limit: int = 4096) -> list[str]:
    sanitized = sanitize_telegram_text(text)
    return [_format_for_telegram(chunk) for chunk in _chunk_text(sanitized, limit=limit)]


def sanitize_telegram_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(character for character in normalized if _is_allowed_telegram_text_character(character))


def trim_url(url: str) -> str:
    return url.strip().rstrip(".,，。!！?)）]}>")


def _format_for_telegram(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _FENCED_CODE_RE.finditer(text):
        parts.append(_format_inline_telegram_html(text[cursor : match.start()]))
        parts.append(f"<pre>{html.escape(match.group(2), quote=False)}</pre>")
        cursor = match.end()
    parts.append(_format_inline_telegram_html(text[cursor:]))
    return "".join(parts)


def _format_inline_telegram_html(text: str) -> str:
    return "".join(_format_inline_telegram_line(line) for line in text.splitlines(keepends=True))


def _format_inline_telegram_line(line: str) -> str:
    content = line.removesuffix("\n")
    newline = "\n" if content != line else ""
    heading_match = _HEADING_RE.match(content)
    if heading_match is None:
        return _format_inline_markdown_html(content, convert_bold=True) + newline

    heading_text = heading_match.group(2).strip()
    return f"<b>{_format_inline_markdown_html(heading_text, convert_bold=False)}</b>{newline}"


def _format_inline_markdown_html(text: str, *, convert_bold: bool) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_format_markdown_text_html(text[cursor : match.start()], convert_bold=convert_bold))
        parts.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        cursor = match.end()
    parts.append(_format_markdown_text_html(text[cursor:], convert_bold=convert_bold))
    return "".join(parts)


def _format_markdown_text_html(text: str, *, convert_bold: bool) -> str:
    escaped = _escape_text_with_links(text)
    replacement = (lambda match: f"<b>{match.group(1)}</b>") if convert_bold else (lambda match: match.group(1))
    return _BOLD_RE.sub(replacement, escaped)


def _escape_text_with_links(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _PLAIN_URL_RE.finditer(text):
        raw_url = match.group(0)
        url = trim_url(raw_url)
        suffix = raw_url[len(url) :]
        parts.append(html.escape(text[cursor : match.start()], quote=False))
        parts.append(_plain_url_html_link(url))
        parts.append(html.escape(suffix, quote=False))
        cursor = match.end()
    parts.append(html.escape(text[cursor:], quote=False))
    return "".join(parts)


def _plain_url_html_link(url: str) -> str:
    href = quote(url, safe=":/?#[]@!$&'()*+,;=%")
    escaped_href = html.escape(href, quote=True)
    escaped_label = html.escape(url, quote=False)
    return f'<a href="{escaped_href}">{escaped_label}</a>'


def _is_allowed_telegram_text_character(character: str) -> bool:
    codepoint = ord(character)
    return character in {"\n", "\t"} or (codepoint >= 0x20 and not 0xD800 <= codepoint <= 0xDFFF)


def _chunk_text(text: str, limit: int = 4096) -> list[str]:
    if not text:
        return [" "]
    return [text[index : index + limit] for index in range(0, len(text), limit)]
