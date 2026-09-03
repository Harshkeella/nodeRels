import re
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "equation", "table", "code"] = "text"
    text: str = Field(default="", max_length=12000)
    rows: list[list[str]] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def rectangular(self):
        if self.kind == "table":
            if not self.rows or not 1 <= len(self.rows[0]) <= 12:
                raise ValueError("Tables need 1–12 columns.")
            if any(len(row) != len(self.rows[0]) or any(len(c) > 2000 for c in row) for row in self.rows):
                raise ValueError("Table cells must be bounded and rows rectangular.")
        elif not self.text.strip():
            raise ValueError("Empty content block.")
        return self


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=180)
    level: int = Field(default=2, ge=2, le=4)
    blocks: list[Block] = Field(min_length=1, max_length=100)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=180)
    sections: list[Section] = Field(min_length=1, max_length=60)
    sources: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json()) > 250_000:
            raise ValueError("Document is too large; request a narrower topic.")
        return self


def from_markdown(markdown: str, title: str, sources: list[str]) -> Document:
    """Parse structure, never shorten prose, round numbers, or rewrite formulas."""
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(markdown)
    sections, blocks, heading = [], [], title
    level = 2

    def flush():
        nonlocal blocks
        if blocks:
            sections.append(Section(title=heading, level=level, blocks=blocks))
            blocks = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            flush()
            # Models like to number the headings they were asked to write ("## Slide 3:
            # Results"), and the renderer already paginates and numbers, so the prefix
            # ships as visible duplication on every slide and PDF heading.
            heading = re.sub(r"^(?:slide|section|page)\s*\d+\s*[:.–—-]\s*", "",
                             tokens[i + 1].content, flags=re.I).strip() or tokens[i + 1].content
            level = max(2, min(4, int(token.tag[1:])))
            if not sections and token.tag == "h1":
                # "Video Script: ETL" -- the model naming the deliverable it was asked
                # for. The title is shown as the artifact's name, so it must be the
                # subject. Only a leading kind label followed by a separator goes.
                title = re.sub(r"^(?:video|presentation|deck|slide\s*deck|pdf|document|report)"
                               r"\s*(?:script|outline|overview|summary)?\s*[:–—-]\s+",
                               "", heading, flags=re.I).strip() or heading
            i += 3
            continue
        if token.type == "table_open":
            rows, row = [], []
            i += 1
            while tokens[i].type != "table_close":
                if tokens[i].type == "tr_open":
                    row = []
                elif tokens[i].type == "inline":
                    row.append(tokens[i].content)
                elif tokens[i].type == "tr_close":
                    rows.append(row)
                i += 1
            blocks.append(Block(kind="table", rows=rows))
        elif token.type in ("fence", "code_block") and token.content.strip():
            blocks.append(Block(kind="equation" if token.info.strip() in ("math", "latex") else "code", text=token.content.strip()))
        elif token.type == "inline" and token.content.strip():
            text = token.content.strip()
            math = (text.startswith("$$") and text.endswith("$$")) or (text.startswith(r"\[") and text.endswith(r"\]"))
            # Plain text is intentional: emphasis markers are formatting, source words stay intact.
            plain = "".join(c.content if c.type not in ("softbreak", "hardbreak") else "\n"
                            for c in token.children or [] if c.type not in ("image", "html_inline"))
            content = text[2:-2].strip() if math else plain.strip()
            if content:
                blocks.append(Block(kind="equation" if math else "text", text=content))
        i += 1
    flush()
    return Document(title=title, sections=sections, sources=list(dict.fromkeys(sources)))
