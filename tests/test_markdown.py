"""Markdown stays readable and safe across arbitrary transport boundaries."""

import re

import pytest

from codex95.markdown import MarkdownStream, render_markdown


SGR = re.compile(r"\x1b\[[0-9;]*m")


def streamed(source, size):
    renderer = MarkdownStream(width=70)
    return "".join(renderer.feed(source[index:index + size])
                   for index in range(0, len(source), size)) + renderer.finish()


@pytest.mark.parametrize("source", [
    "# Title\n\n**bold**, *italic*, ~~removed~~ and `inline()`\n\nNext paragraph.",
    "Paragraph\n---\n\nA hard break  \nnext line\n\nEnd.",
    "- first\n- **second**\n  - nested\n\n  continuation\n\nNext paragraph.",
    "8. eighth\n9. ninth\n\nNext.",
    "| Name | Value |\n| --- | --- |\n| one | **two** |\n| three | four |\n\nNext.",
    "```python\nif True:\n    value = '**literal**'  \n\n    print(value)\n```\n\nEnd.",
    "~~~shell\nprintf '`code`'\n\n~~~\n\nEnd.",
    "```unfinished\n  keep indentation  \n\n",
    "> a quote\n> with **bold**\n\nAfter.",
    "[website][ref]\n\n[ref]: https://example.com/path\n\nNext.",
    "[website]\n\n[website]: https://example.com\n",
    "&lt;file&gt; &#x1b;[31m &#x9b;2J [red]safe[/red]\n\nDone.",
])
def test_render_is_independent_of_delta_boundaries(source):
    expected = streamed(source, max(1, len(source)))
    for size in [1, 2, 3, 7, 19]:
        assert streamed(source, size) == expected


def test_markdown_structure_without_raw_delimiters():
    value = streamed("# Heading\n\n**bold** *italic* `code`\n\n"
                     "- first\n- second\n\n[OpenAI](https://openai.com)\n", 1)
    assert value.startswith("Heading\n\nbold italic code")
    assert "• first\n• second" in value
    assert "OpenAI (https://openai.com)" in value
    assert "**" not in value and "`" not in value
    assert "\x1b" not in value


def test_code_content_and_whitespace_survive_without_interpretation():
    source = "```text\n  **literal** `ticks` <file>  \n\n    [red]code[/red]\n\n```"
    value = render_markdown(source, width=100)
    assert value == "  **literal** `ticks` <file>  \n\n    [red]code[/red]\n"
    assert "```" not in value


def test_incomplete_fence_flushes_on_finish_and_finish_is_idempotent():
    renderer = MarkdownStream()
    assert renderer.feed("```\nline\n\n") == ""
    assert renderer.finish() == "line\n"
    assert renderer.finish() == ""
    with pytest.raises(RuntimeError, match="finished"):
        renderer.feed("later")


def test_simple_heading_and_closed_fence_stream_before_turn_end():
    renderer = MarkdownStream()
    assert renderer.feed("# Title\n") == "Title"
    assert renderer.feed("\n```text\na  \n```\n") == "\n\na  "
    assert renderer.finish() == ""


def test_partial_inline_markup_does_not_escape_before_block_is_complete():
    renderer = MarkdownStream()
    assert renderer.feed("**bo") == ""
    assert renderer.feed("ld**\n") == ""
    assert renderer.feed("\n") == "bold"
    assert renderer.finish() == ""


def test_entity_decoded_controls_and_links_cannot_inject_terminal_sequences(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    source = "&#27;[2J &#x9b;2J &#7; [link](https://example.com/path) <file> [red]literal[/red]"
    plain = render_markdown(source, color=False)
    assert not any(ord(char) < 32 and char not in "\n\t" for char in plain)
    assert "\x9b" not in plain
    assert "https://example.com/path" in plain
    assert "<file>" in plain and "[red]literal[/red]" in plain
    styled = render_markdown(source + " **bold** `code`", color=True)
    assert "\x1b" not in SGR.sub("", styled)
    assert "\x9b" not in styled
    assert "\x1b]" not in styled
    assert "\x1b[" in styled


def test_late_reference_definitions_remain_resolved():
    renderer = MarkdownStream()
    assert renderer.feed("[website][ref]\n\n") == ""
    rendered = renderer.feed("[ref]: https://example.com\n\n") + renderer.finish()
    assert rendered == "website (https://example.com)"


def test_long_reply_prints_every_paragraph_once_without_screen_controls():
    source = "\n\n".join(f"Paragraph {index}: **unique-{index}**" for index in range(150))
    value = streamed(source, 7)
    for index in range(150):
        assert value.count(f"Paragraph {index}: unique-{index}\n") == (0 if index == 149 else 1)
    assert value.endswith("Paragraph 149: unique-149")
    assert "\x1b" not in value


def test_tables_and_images_use_only_monochrome_text_without_extra_chrome():
    source = "| A | B |\n| - | - |\n| x | y |\n"
    plain = render_markdown(source)
    assert plain.startswith("A")
    assert plain.splitlines()[-1].split() == ["x", "y"]
    styled = render_markdown(source, color=True)
    # All explicit RGB colors are one of the three fixed terminal shades.
    rgb = re.findall(r"(?:38|48);2;(\d+);(\d+);(\d+)", styled)
    assert rgb
    assert all(value in {("0", "0", "0"), ("204", "204", "204"), ("170", "170", "170")}
               for value in rgb)
    assert render_markdown("![sample](https://example.com/image.png)") == "[image: sample]"


def test_long_code_lines_keep_their_original_line_breaks():
    line = 'value = "' + 'a' * 200 + '"  '
    assert render_markdown("```python\n" + line + "\n```", width=20) == line


@pytest.mark.parametrize("container", ["list", "quote"])
def test_long_nested_code_lines_are_not_cropped_by_their_container(container):
    line = "keep-" + "x" * 200 + "-tail"
    if container == "list":
        source = "- item\n\n  ```text\n  " + line + "\n  ```"
    else:
        source = "> ```text\n> " + line + "\n> ```"
    rendered = render_markdown(source, width=20)
    assert line in rendered
    assert rendered.count("-tail") == 1
    # Streaming containers have the same preservation property.
    assert line in streamed(source, 1)


def test_narrow_table_wraps_long_cells_without_ellipsis_or_content_loss():
    source = "| A | B |\n| - | - |\n| " + "X" * 200 + " | tail |"
    rendered = render_markdown(source, width=20)
    assert rendered.count("X") == 200
    assert "tail" in rendered
    assert "…" not in rendered
    streamed_value = streamed(source, 1)
    assert streamed_value.count("X") == 200
    assert "tail" in streamed_value


@pytest.mark.parametrize(("columns", "width"), [(5, 10), (20, 78)])
def test_table_that_cannot_fit_stacks_fields_without_losing_columns(columns, width):
    source = "| " + " | ".join(f"H{i}" for i in range(columns)) + " |\n"
    source += "| " + " | ".join("-" for _ in range(columns)) + " |\n"
    source += "| " + " | ".join(f"V{i}Z" for i in range(columns)) + " |"
    rendered = render_markdown(source, width=width)
    for index in range(columns):
        assert f"H{index}: V{index}Z" in rendered
    assert rendered.count("V") == columns
    assert rendered.count("Z") == columns


def test_table_preserves_full_width_glyphs_in_normal_and_stacked_layouts():
    for columns, width in [(2, 10), (5, 10)]:
        source = "| " + " | ".join(f"H{i}" for i in range(columns)) + " |\n"
        source += "| " + " | ".join("-" for _ in range(columns)) + " |\n"
        source += "| " + " | ".join("字" * 4 for _ in range(columns)) + " |"
        assert render_markdown(source, width=width).count("字") == columns * 4
