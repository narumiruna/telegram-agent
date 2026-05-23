from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser

import telegraph
from telegraph.exceptions import ParsingException
from telegraph.exceptions import TelegraphException

DEFAULT_TELEGRAPH_SHORT_NAME = "Narumi's Bot"
DEFAULT_TELEGRAPH_TITLE = "Telegram Agent Reply"

_FENCED_CODE_RE = re.compile(r"```(?:([^\n`]*)\n)?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)

_TELEGRAPH_ALLOWED_TAGS: set[str] = {
    "a",
    "aside",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "figcaption",
    "figure",
    "h3",
    "h4",
    "hr",
    "i",
    "iframe",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "u",
    "ul",
    "video",
}

_TELEGRAPH_VOID_TAGS: set[str] = {"br", "hr", "img"}

_TELEGRAPH_TAG_REMAP: dict[str, str] = {
    "del": "s",
    "h1": "h3",
    "h2": "h3",
    "h5": "h4",
    "h6": "h4",
    "strike": "s",
}

_TELEGRAPH_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href"},
    "iframe": {"src"},
    "img": {"src", "alt"},
    "video": {"src"},
}


class TelegraphPublishError(RuntimeError):
    """Raised when a Telegraph page cannot be created."""


class TelegraphPagePublisher:
    def __init__(self, *, short_name: str = DEFAULT_TELEGRAPH_SHORT_NAME, timeout_seconds: float = 30.0) -> None:
        self.short_name = short_name
        self.timeout_seconds = timeout_seconds

    async def publish(self, text: str) -> str:
        title = telegraph_page_title(text)
        html_content = format_telegraph_html(text)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._create_page, title, html_content), timeout=self.timeout_seconds
            )
        except (OSError, TimeoutError, ValueError, ParsingException, TelegraphException, TelegraphPublishError) as exc:
            raise TelegraphPublishError("Failed to create Telegraph page") from exc

    def _create_page(self, title: str, html_content: str) -> str:
        client = telegraph.Telegraph()
        client.create_account(short_name=self.short_name)
        response = client.create_page(title=title, html_content=html_content)
        if not isinstance(response, dict):
            raise TelegraphPublishError("Telegraph create_page returned a non-object response")
        url = response.get("url")
        if not isinstance(url, str) or not url:
            raise TelegraphPublishError("Telegraph create_page returned no URL")
        return url


class _TelegraphHTMLSanitizer(HTMLParser):
    """Tolerant sanitizer for Telegraph's limited HTML subset."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._open_tags: list[str] = []

    def get_html(self) -> str:
        for tag in reversed(self._open_tags):
            self._parts.append(f"</{tag}>")
        self._open_tags.clear()
        return "".join(self._parts)

    def handle_data(self, data: str) -> None:
        self._parts.append(html.escape(data, quote=False))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapped = _TELEGRAPH_TAG_REMAP.get(tag, tag)
        if mapped not in _TELEGRAPH_ALLOWED_TAGS:
            self._parts.append(html.escape(self.get_starttag_text() or f"<{tag}>", quote=False))
            return

        attr_allowlist = _TELEGRAPH_ALLOWED_ATTRS.get(mapped, set())
        rendered_attrs: list[str] = []
        for key, value in attrs:
            if key not in attr_allowlist or value is None:
                continue
            rendered_attrs.append(f'{key}="{html.escape(value, quote=True)}"')

        attrs_str = (" " + " ".join(rendered_attrs)) if rendered_attrs else ""
        self._parts.append(f"<{mapped}{attrs_str}>")

        if mapped not in _TELEGRAPH_VOID_TAGS:
            self._open_tags.append(mapped)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        mapped = _TELEGRAPH_TAG_REMAP.get(tag, tag)
        if mapped in _TELEGRAPH_VOID_TAGS:
            return

        if mapped not in _TELEGRAPH_ALLOWED_TAGS:
            self._parts.append(html.escape(f"</{tag}>", quote=False))
            return

        if not self._open_tags or self._open_tags[-1] != mapped:
            self._parts.append(html.escape(f"</{tag}>", quote=False))
            return

        self._open_tags.pop()
        self._parts.append(f"</{mapped}>")


def telegraph_page_title(text: str) -> str:
    for line in text.splitlines():
        title = line.strip()
        if not title:
            continue
        title = _HEADING_RE.sub(lambda match: match.group(2).strip(), title)
        title = re.sub(r"[*_`]+", "", title).strip()
        if title:
            return _truncate_title(title)
    return DEFAULT_TELEGRAPH_TITLE


def format_telegraph_html(text: str) -> str:
    normalized = _sanitize_text(text)
    html_content = _format_telegraph_markdown(normalized)
    return _sanitize_telegraph_html(html_content)


def _sanitize_telegraph_html(html_content: str) -> str:
    parser = _TelegraphHTMLSanitizer()
    parser.feed(html_content)
    parser.close()
    return parser.get_html()


def _format_telegraph_markdown(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _FENCED_CODE_RE.finditer(text):
        parts.extend(_format_telegraph_blocks(text[cursor : match.start()]))
        parts.append(f"<pre>{html.escape(match.group(2), quote=False)}</pre>")
        cursor = match.end()
    parts.extend(_format_telegraph_blocks(text[cursor:]))
    return "\n".join(part for part in parts if part) or "<p> </p>"


def _format_telegraph_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for block in re.split(r"\n{2,}", text.strip()):
        block = block.strip("\n")
        if not block:
            continue
        blocks.extend(_format_telegraph_block(block))
    return blocks


def _format_telegraph_block(block: str) -> list[str]:
    lines = block.splitlines()
    rendered: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        rendered.append(f"<p>{'<br>'.join(_format_inline_telegraph_html(line) for line in paragraph_lines)}</p>")
        paragraph_lines.clear()

    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if heading_match is None:
            paragraph_lines.append(line)
            continue
        flush_paragraph()
        level = len(heading_match.group(1))
        tag = "h3" if level <= 3 else "h4"
        rendered.append(f"<{tag}>{_format_inline_telegraph_html(heading_match.group(2).strip())}</{tag}>")

    flush_paragraph()
    return rendered


def _format_inline_telegraph_html(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_format_markdown_text_html(text[cursor : match.start()]))
        parts.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        cursor = match.end()
    parts.append(_format_markdown_text_html(text[cursor:]))
    return "".join(parts)


def _format_markdown_text_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return _BOLD_RE.sub(_bold_replacement, escaped)


def _bold_replacement(match: re.Match[str]) -> str:
    return f"<b>{match.group(1)}</b>"


def _sanitize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(character for character in normalized if _is_allowed_text_character(character))


def _is_allowed_text_character(character: str) -> bool:
    codepoint = ord(character)
    return character in {"\n", "\t"} or (codepoint >= 0x20 and not 0xD800 <= codepoint <= 0xDFFF)


def _truncate_title(title: str, limit: int = 80) -> str:
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "..."
