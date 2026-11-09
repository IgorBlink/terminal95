"""Append-only Markdown output for the native terminal conversation."""

from __future__ import annotations

import io
import re

from markdown_it import MarkdownIt
from rich import box
from rich.console import Console
from rich.markdown import BlockQuote, CodeBlock, Heading, ImageItem, ListItem, Markdown, Paragraph, TableElement
from rich.segment import Segment
from rich.text import Text
from rich.table import Table
from rich.theme import Theme


def _clean(text: str) -> str:
    return "".join(char for char in str(text) if char.isprintable() or char in "\n\t")


_PARSER = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_REFERENCE = re.compile(r"(?<!\\)\[[^\]\n]+\](?:\[[^\]\n]*\])?")


def _tokens(text: str, environment: dict):
    tokens = _PARSER.parse(_clean(text), environment)

    def clean(token):
        # The parser decodes entities, including numeric control characters.
        token.content = _clean(token.content)
        token.info = _clean(token.info)
        token.attrs = {key: _clean(value) if isinstance(value, str) else value
                       for key, value in token.attrs.items()}
        for child in token.children or []:
            clean(child)

    for token in tokens:
        clean(token)
    return tokens


class _Paragraph(Paragraph):
    @classmethod
    def create(cls, markdown, token):
        # None keeps left alignment without padding every line to terminal width.
        return cls(justify=None)


class _Heading(Heading):
    def __rich_console__(self, console, options):
        self.text.justify = None
        yield self.text


class _Code(CodeBlock):
    def __rich_console__(self, console, options):
        # Treat code as literal text: no syntax colors, markup, or whitespace trim.
        yield Text(self.text.plain, style="markdown.code_block", end="",
                   no_wrap=True, overflow="ignore")


class _ListItem(ListItem):
    def _lines(self, console, options, width):
        # render_lines crops every line to the container width, even when a
        # literal code block deliberately keeps a longer line for soft wrap.
        segments = console.render(self.elements, options.update(width=max(1, width)))
        return Segment.split_lines(Segment.apply_style(segments, self.style))

    def render_bullet(self, console, options):
        style = console.get_style("markdown.item.bullet", default="none")
        for index, line in enumerate(self._lines(console, options, options.max_width - 2)):
            yield Segment("• " if index == 0 else "  ", style)
            yield from line
            yield Segment.line()

    def render_number(self, console, options, number, last_number):
        width = len(str(last_number - 1)) + 2
        style = console.get_style("markdown.item.number", default="none")
        for index, line in enumerate(self._lines(console, options, options.max_width - width)):
            yield Segment(f"{number}. ".rjust(width) if index == 0 else " " * width, style)
            yield from line
            yield Segment.line()


class _Quote(BlockQuote):
    def __rich_console__(self, console, options):
        # Preserve long nested code just as list items do; terminal soft wrap
        # handles its width instead of discarding the tail of the line.
        segments = console.render(self.elements,
                                  options.update(width=max(1, options.max_width - 2)))
        for line in Segment.split_lines(Segment.apply_style(segments, self.style)):
            yield Segment("▌ ", self.style)
            yield from line
            yield Segment.line()


class _Image(ImageItem):
    def __rich_console__(self, console, options):
        title = self.text if self.text else Text(self.destination)
        yield Text.assemble("[image: ", title, "]", end="")


class _Table(TableElement):
    def __rich_console__(self, console, options):
        headers = ([column.content for column in self.header.row.cells]
                   if self.header is not None and self.header.row is not None else [])
        rows = self.body.rows if self.body is not None else []
        if len(headers) * 4 > options.max_width:
            # Rich can assign zero-width columns when a table cannot fit, losing
            # entire cells. Stack labeled fields instead of cropping any data.
            if not rows:
                for header in headers:
                    yield Text.assemble(header, no_wrap=False, overflow="fold")
            for row_index, row in enumerate(rows):
                if row_index:
                    yield Text("")
                for index, cell in enumerate(row.cells):
                    header = headers[index] if index < len(headers) else Text(str(index + 1))
                    yield Text.assemble(header, ": ", cell.content,
                                        no_wrap=False, overflow="fold")
            return
        table = Table(box=box.SIMPLE_HEAVY, show_edge=False, pad_edge=False,
                      collapse_padding=True, border_style="markdown.table.border",
                      header_style="markdown.table.header")
        for header in headers:
            # Two columns accommodate a full-width glyph without replacement.
            table.add_column(header, min_width=2, overflow="fold", no_wrap=False)
        for row in rows:
            table.add_row(*(element.content for element in row.cells))
        yield table


class _Markdown(Markdown):
    elements = {**Markdown.elements, "paragraph_open": _Paragraph,
                "heading_open": _Heading, "fence": _Code, "code_block": _Code,
                "list_item_open": _ListItem, "blockquote_open": _Quote,
                "table_open": _Table, "image": _Image}

    def __init__(self, source: str, environment: dict):
        super().__init__("", hyperlinks=False)
        self.parsed = _tokens(source, environment)

    def __rich_console__(self, console, options):
        # Rich inserts a leading separator while closing a list/table's child
        # elements. The native prompt already supplies its own block spacing.
        skip_first_break = bool(self.parsed and self.parsed[0].type in {
            "bullet_list_open", "ordered_list_open", "table_open",
        })
        for segment in super().__rich_console__(console, options):
            if skip_first_break:
                skip_first_break = False
                if isinstance(segment, Segment) and segment.text == "\n":
                    continue
            yield segment


_STYLES = {
    "markdown.paragraph": "black on #cccccc",
    "markdown.code": "black on #aaaaaa",
    "markdown.code_block": "black on #cccccc",
    "markdown.block_quote": "black on #cccccc",
    "markdown.link": "underline black on #cccccc",
    "markdown.link_url": "black on #cccccc",
    "markdown.item": "black on #cccccc",
    "markdown.item.bullet": "black on #cccccc",
    "markdown.item.number": "black on #cccccc",
    "markdown.em": "italic black on #cccccc",
    "markdown.strong": "bold black on #cccccc",
    "markdown.s": "strike black on #cccccc",
    "markdown.hr": "black on #cccccc",
    **{f"markdown.h{number}": "bold black on #cccccc" for number in range(1, 7)},
    "markdown.table.header": "bold black on #cccccc",
    "markdown.table.border": "black on #cccccc",
}


def _render(source: str, *, width: int, color: bool, environment: dict) -> str:
    output = io.StringIO()
    console = Console(file=output, width=max(4, width), force_terminal=color,
                      force_interactive=False, color_system="truecolor" if color else None,
                      no_color=not color, theme=Theme(_STYLES),
                      style="black on #cccccc" if color else None,
                      markup=False, emoji=False, highlight=False)
    console.print(_Markdown(source, environment), end="", crop=False)
    # Rich emits a final line ending. Remove just that ending, never code spaces
    # or additional empty lines that belong to a code block.
    return output.getvalue().removesuffix("\n")


def render_markdown(text: str, *, width: int = 78, color: bool = False) -> str:
    """Render a complete message; only fixed SGR styles may be emitted."""
    return _render(text, width=width, color=color, environment={})


class MarkdownStream:
    """Render completed blocks once, independent of incoming delta boundaries.

    Returned strings have no prompt prefix. They include the separation from
    previous output, so callers append them verbatim. An ambiguous final block
    remains buffered until more input or :meth:`finish`.
    """

    def __init__(self, width: int = 78, color: bool = False):
        self.width = width
        self.color = color
        self._pending = ""
        self._partial = ""
        self._environment = {}
        self._emitted = False
        self._finished = False

    def _emit(self, source: str) -> str:
        rendered = _render(source, width=self.width, color=self.color,
                           environment=self._environment)
        if not rendered:
            return ""
        prefix = "\n\n" if self._emitted else ""
        self._emitted = True
        return prefix + rendered

    @staticmethod
    def _unresolved_reference(tokens):
        # A reference definition can follow the paragraph that uses it. Keep
        # unresolved bracket text until its definition arrives or the turn ends.
        def unresolved(token):
            return ((token.type == "text" and _REFERENCE.search(token.content))
                    or any(unresolved(child) for child in token.children or []))
        return any(unresolved(token) for token in tokens)

    def _drain(self) -> str:
        output = []
        while self._pending:
            tokens = _tokens(self._pending, self._environment)
            blocks = [token for token in tokens
                      if token.level == 0 and token.nesting >= 0 and token.map]
            if not blocks or self._unresolved_reference(tokens):
                break
            first = blocks[0]
            lines = self._pending.splitlines(keepends=True)
            complete = len(blocks) > 1
            if first.type in {"heading_open", "hr"}:
                complete = True
            elif first.type == "paragraph_open" and self._pending.endswith("\n\n"):
                complete = True
            elif first.type == "fence" and first.map[1] - first.map[0] >= 2:
                marker = re.escape(first.markup[0])
                closing = re.compile(r" {0,3}" + marker + "{" + str(len(first.markup)) + r",}[ \t]*\n?\Z")
                complete = bool(closing.fullmatch(lines[first.map[1] - 1]))
            if not complete:
                break
            end = first.map[1]
            source = "".join(lines[:end])
            self._pending = "".join(lines[end:]).lstrip("\n")
            output.append(self._emit(source))
        return "".join(output)

    def feed(self, text: str) -> str:
        if self._finished:
            raise RuntimeError("Markdown stream is already finished")
        # Process complete lines in order even when a whole answer arrives in
        # one delta. This gives character deltas and large deltas equal output.
        self._partial += _clean(text)
        output = []
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self._pending += line + "\n"
            output.append(self._drain())
        return "".join(output)

    def finish(self) -> str:
        if self._finished:
            return ""
        self._finished = True
        self._pending += self._partial
        self._partial = ""
        result = self._emit(self._pending) if self._pending else ""
        self._pending = ""
        return result
