"""HTML→readable-text converter — the ONE golden implementation.

Added 2026-09-09 (design-board-v2.md D1, review warning W7): the dump CSV
needs job descriptions as clean text. `sources.base.clean_html` strips tags
but flattens structure (no line breaks, bullets lost); this module renders
HTML to readable text with structure preserved:

- <br> and block boundaries (<p>, <div>, <li>, headings, <tr>) → newline
- <li> → "• " bullet
- entities unescaped (&amp; &nbsp; &#43; …)
- <style>/<script> content dropped
- runs of whitespace collapsed per line, 3+ consecutive blank lines → 1

Pure-stdlib (html.parser.HTMLParser) — no new dependency. One golden
fixture (tests/test_htmltext.py) covers real NVIDIA jobDescription shapes.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "table", "tr", "blockquote", "section",
    "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
}
_DROP_TAGS = {"style", "script", "head"}
_LIST_TAGS = {"ul", "ol"}


class _TextRenderer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self._out.append("\n")
        elif tag == "li":
            self._out.append("\n• ")
        elif tag in _BLOCK_TAGS:
            # block boundary — newline (double for list containers so items
            # separate visually)
            self._out.append("\n\n" if tag in _LIST_TAGS else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS and tag not in _LIST_TAGS:
            self._out.append("\n")
        elif tag in _LIST_TAGS:
            self._out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(data)


def html_to_text(html_str: str | None) -> str:
    """Render an HTML fragment to clean readable text (see module doc)."""
    if not html_str:
        return ""
    parser = _TextRenderer()
    try:
        parser.feed(html_str)
        parser.close()
    except Exception:
        # malformed HTML — fall back to tag-stripping rather than dying
        return re.sub(r"<[^>]+>", " ", html_str).strip()
    text = _html.unescape("".join(parser._out))
    # normalize: strip each line, collapse inner runs, cap blank runs at 1
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
