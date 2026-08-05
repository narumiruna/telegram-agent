from __future__ import annotations

from telegramagent.telegraph_pages import _sanitize_telegraph_html
from telegramagent.telegraph_pages import format_telegraph_html
from telegramagent.telegraph_pages import telegraph_page_title


def test_telegraph_html_formats_markdown_and_sanitizes_supported_tags() -> None:
    text = (
        "# Page title\n\n"
        "This is **bold** with `code` and <script>plain text</script>.\n\n"
        "```html\n<div>escaped code</div>\n```"
    )

    assert telegraph_page_title(text) == "Page title"
    rendered = format_telegraph_html(text)

    assert "<h3>Page title</h3>" in rendered
    assert "This is <b>bold</b> with <code>code</code>" in rendered
    assert "&lt;script&gt;plain text&lt;/script&gt;" in rendered
    assert "<pre>&lt;div&gt;escaped code&lt;/div&gt;\n</pre>" in rendered


def test_telegraph_html_sanitizer_remaps_and_escapes_unsupported_html() -> None:
    rendered = _sanitize_telegraph_html(
        '<h1>Title</h1><del>Gone</del><span class="x">No</span><a href="https://example.com" rel="x">Link</a>'
    )

    assert rendered == (
        '<h3>Title</h3><s>Gone</s>&lt;span class="x"&gt;No&lt;/span&gt;<a href="https://example.com">Link</a>'
    )
