#!/usr/bin/env python3
"""
Markdown → Atlassian Document Format (ADF) converter.

Block elements supported:
  Headings (h1–h6), paragraphs, fenced code blocks (``` or ~~~, with
  optional language tag), indented code blocks (4-space), blockquotes
  (including nested), bullet lists, ordered lists, task lists
  (- [ ] / - [x]), GFM tables, and horizontal rules.

Inline elements supported:
  **bold**, *italic*, ***bold italic***, ~~strikethrough~~,
  `inline code`, [link text](url "optional title"),
  ![image alt](url) → rendered as a hyperlinked text node,
  backslash escapes, and hard line breaks (two trailing spaces or \\n).

ADF spec:
  https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
"""

from __future__ import annotations

import re
from typing import Any

Node = dict[str, Any]

# ──────────────────────────────────────────────────────────────────────────────
# ADF node / mark constructors
# ──────────────────────────────────────────────────────────────────────────────

def _text_node(t: str, marks: list | None = None) -> Node:
    n: Node = {"type": "text", "text": t}
    if marks:
        n["marks"] = marks
    return n

def _mark(type_: str, **attrs) -> dict:
    m: dict = {"type": type_}
    if attrs:
        m["attrs"] = attrs
    return m

_STRONG  = _mark("strong")
_EM      = _mark("em")
_CODE_MK = _mark("code")
_STRIKE  = _mark("strike")

def _link_mark(href: str, title: str = "") -> dict:
    a: dict[str, Any] = {"href": href}
    if title:
        a["title"] = title
    return _mark("link", **a)

def _para(inlines: list[Node]) -> Node:
    return {"type": "paragraph", "content": inlines or [_text_node("")]}

def _heading_node(level: int, inlines: list[Node]) -> Node:
    return {"type": "heading", "attrs": {"level": level}, "content": inlines}

def _code_block(code: str, language: str = "") -> Node:
    return {"type": "codeBlock",
            "attrs": {"language": language},
            "content": [_text_node(code)]}

def _blockquote(blocks: list[Node]) -> Node:
    return {"type": "blockquote", "content": blocks}

def _rule() -> Node:
    return {"type": "rule"}

def _bullet_list(items: list[Node]) -> Node:
    return {"type": "bulletList", "content": items}

def _ordered_list(items: list[Node], order: int = 1) -> Node:
    return {"type": "orderedList", "attrs": {"order": order}, "content": items}

def _list_item(blocks: list[Node]) -> Node:
    return {"type": "listItem", "content": blocks}

def _table_block(rows: list[Node]) -> Node:
    return {"type": "table",
            "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
            "content": rows}

def _table_row(cells: list[Node]) -> Node:
    return {"type": "tableRow", "content": cells}

def _th(blocks: list[Node]) -> Node:
    return {"type": "tableHeader",
            "attrs": {"colspan": 1, "rowspan": 1, "colwidth": []},
            "content": blocks}

def _td(blocks: list[Node]) -> Node:
    return {"type": "tableCell",
            "attrs": {"colspan": 1, "rowspan": 1, "colwidth": []},
            "content": blocks}

def _hard_break() -> Node:
    return {"type": "hardBreak"}

# ──────────────────────────────────────────────────────────────────────────────
# Inline parser
# ──────────────────────────────────────────────────────────────────────────────
# Strategy: scan left-to-right; at each position find the earliest match
# across all patterns, emit plain text before it, recurse for nested marks.

_INLINE_PATS: list[tuple[str, re.Pattern]] = [
    # backslash escape — always wins over formatting chars
    ("escape",
     re.compile(r'\\([\\`*_{}\[\]()#+\-!.])')),
    # images before links so `![...]` doesn't consume `[...]` first
    ("image",
     re.compile(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"([^"]*)")?\)')),
    ("link",
     re.compile(r'\[([^\]]*)\]\(([^\s)]+)(?:\s+"([^"]*)")?\)')),
    # code span: matching backtick sequences; opaque (no inner formatting)
    ("code_span",
     re.compile(r'(`+)(.*?)\1', re.DOTALL)),
    # bold+italic before bold so *** wins over **
    ("bold_italic",
     re.compile(r'\*{3}(.+?)\*{3}|_{3}(.+?)_{3}', re.DOTALL)),
    ("bold",
     re.compile(r'\*{2}(.+?)\*{2}|_{2}(.+?)_{2}', re.DOTALL)),
    ("italic",
     re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)'
                r'|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)')),
    ("strike",
     re.compile(r'~~(.+?)~~', re.DOTALL)),
    # hard line break: two trailing spaces before newline, or backslash-newline
    ("hard_break",
     re.compile(r'(?<=\S)  (?=\n|$)|\\\n')),
]


def _parse_inline(s: str, extra: list | None = None) -> list[Node]:
    """Recursively parse inline Markdown into ADF inline nodes."""
    cur   = extra or []
    nodes: list[Node] = []
    pos   = 0

    while pos < len(s):
        # find the leftmost match across all patterns
        best_m: re.Match | None = None
        best_k = ""
        for key, pat in _INLINE_PATS:
            m = pat.search(s, pos)
            if m and (best_m is None or m.start() < best_m.start()):
                best_m, best_k = m, key

        if best_m is None:
            if pos < len(s):
                nodes.append(_text_node(s[pos:], cur or None))
            break

        # emit plain text that precedes the token
        if best_m.start() > pos:
            nodes.append(_text_node(s[pos:best_m.start()], cur or None))

        g, k = best_m, best_k

        if k == "escape":
            nodes.append(_text_node(g.group(1), cur or None))

        elif k == "image":
            href  = g.group(2)
            alt   = g.group(1) or href
            title = g.group(3) or ""
            lm    = _link_mark(href, title)
            nodes.append(_text_node(alt, cur + [lm] if cur else [lm]))

        elif k == "link":
            href  = g.group(2)
            title = g.group(3) or ""
            lm    = _link_mark(href, title)
            nodes.extend(_parse_inline(g.group(1), cur + [lm] if cur else [lm]))

        elif k == "code_span":
            code = g.group(2)
            # CommonMark §6.4: strip one leading/trailing space when both present
            if len(code) >= 2 and code[0] == " " and code[-1] == " " and code.strip():
                code = code[1:-1]
            nodes.append(_text_node(code, cur + [_CODE_MK] if cur else [_CODE_MK]))

        elif k == "bold_italic":
            inner = g.group(1) or g.group(2) or ""
            add   = [_STRONG, _EM]
            nodes.extend(_parse_inline(inner, cur + add if cur else add))

        elif k == "bold":
            inner = g.group(1) or g.group(2) or ""
            nodes.extend(_parse_inline(inner, cur + [_STRONG] if cur else [_STRONG]))

        elif k == "italic":
            inner = g.group(1) or g.group(2) or ""
            nodes.extend(_parse_inline(inner, cur + [_EM] if cur else [_EM]))

        elif k == "strike":
            inner = g.group(1)
            nodes.extend(_parse_inline(inner, cur + [_STRIKE] if cur else [_STRIKE]))

        elif k == "hard_break":
            nodes.append(_hard_break())

        pos = g.end()

    return nodes


def _ipara(s: str) -> Node:
    """Inline-parse `s` and return a paragraph node."""
    return _para(_parse_inline(s))

# ──────────────────────────────────────────────────────────────────────────────
# Block parser
# ──────────────────────────────────────────────────────────────────────────────

_RE_HEADING   = re.compile(r'^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$')
_RE_FENCE     = re.compile(r'^(`{3,}|~{3,})(.*)')
# HR: 3+ of the same character (* - _) with optional spaces between; nothing else
_RE_HR        = re.compile(r'^\s*(?:(\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})\s*$')
_RE_BQ        = re.compile(r'^>\s?(.*)')
_RE_UL        = re.compile(r'^(\s*)[-*+]\s+(\[[ xX]\]\s+)?(.*)')
_RE_OL        = re.compile(r'^(\s*)(\d+)\.\s+(.*)')
_RE_TABLE_ROW = re.compile(r'^\|(.+)\|')
_RE_TABLE_SEP = re.compile(r'^\|[\s\-:|]+\|')
_RE_INDENT4   = re.compile(r'^    (.*)')


def _is_list(line: str) -> bool:
    return bool(_RE_UL.match(line) or _RE_OL.match(line))


def _nspaces(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _parse_list(lines: list[str], start: int) -> tuple[Node, int]:
    """
    Parse a (possibly nested) list starting at lines[start].
    Returns (list_node, next_line_index).
    """
    first   = lines[start].rstrip()
    is_ol   = bool(_RE_OL.match(first))
    base_sp = _nspaces(first)
    order   = int(_RE_OL.match(first).group(2)) if is_ol else 1
    items: list[Node] = []
    i = start

    while i < len(lines):
        s = lines[i].rstrip()

        if not s:
            i += 1
            continue

        sp = _nspaces(s)
        if sp < base_sp:
            break  # back out to parent list or block

        if sp == base_sp:
            m_ul = _RE_UL.match(s)
            m_ol = _RE_OL.match(s)
            if not (m_ul or m_ol):
                break  # same indent but not a list line → paragraph ends list

            task_tag  = (m_ul.group(2) or "") if m_ul else ""
            item_text = m_ul.group(3) if m_ul else m_ol.group(3)
            i += 1

            extra_text: list[str] = []
            sub_node: Node | None = None

            while i < len(lines):
                nxt = lines[i].rstrip()
                if not nxt:
                    i += 1
                    # peek past blank lines — if next content is still indented,
                    # stay in the item (loose list)
                    j = i
                    while j < len(lines) and not lines[j].rstrip():
                        j += 1
                    if j < len(lines) and _nspaces(lines[j].rstrip()) > base_sp:
                        continue
                    break
                nsp = _nspaces(nxt)
                if nsp > base_sp:
                    if _is_list(nxt):
                        sub_node, i = _parse_list(lines, i)
                        break
                    else:
                        extra_text.append(nxt.strip())
                        i += 1
                else:
                    break

            full_text = item_text
            if extra_text:
                full_text += " " + " ".join(extra_text)

            inlines = _parse_inline(full_text)
            if task_tag:
                checked = task_tag.strip().lower() in ("[x]",)
                inlines = [_text_node("☑ " if checked else "☐ ")] + inlines

            blocks: list[Node] = [_para(inlines)]
            if sub_node:
                blocks.append(sub_node)
            items.append(_list_item(blocks))
        else:
            # indented more than base but not a list marker → stop
            break

    list_node = _ordered_list(items, order) if is_ol else _bullet_list(items)
    return list_node, i


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip('|').split('|')]


def _parse_blocks(lines: list[str]) -> list[Node]:  # noqa: C901  (complexity OK here)
    nodes: list[Node] = []
    i = 0
    n = len(lines)

    while i < n:
        raw = lines[i]
        s   = raw.rstrip()

        if not s:
            i += 1
            continue

        # ── heading (ATX style) ──────────────────────────────────────────────
        if m := _RE_HEADING.match(s):
            nodes.append(_heading_node(len(m.group(1)), _parse_inline(m.group(2))))
            i += 1
            continue

        # ── fenced code block ────────────────────────────────────────────────
        if m := _RE_FENCE.match(s):
            fc       = m.group(1)[0]          # ` or ~
            fl       = len(m.group(1))
            lang     = m.group(2).strip()
            close_re = re.compile(rf'^{re.escape(fc)}{{{fl},}}\s*$')
            i += 1
            code_lines: list[str] = []
            while i < n:
                ln = lines[i].rstrip('\n')
                if close_re.match(ln.strip()):
                    i += 1
                    break
                code_lines.append(ln)
                i += 1
            nodes.append(_code_block("\n".join(code_lines), lang))
            continue

        # ── horizontal rule ──────────────────────────────────────────────────
        # Must come before the list check so `---` isn't parsed as a list.
        if _RE_HR.match(s):
            nodes.append(_rule())
            i += 1
            continue

        # ── blockquote ───────────────────────────────────────────────────────
        if _RE_BQ.match(s):
            bq_lines: list[str] = []
            while i < n:
                ln = lines[i].rstrip('\n')
                bm = _RE_BQ.match(ln)
                if bm:
                    bq_lines.append(bm.group(1))
                    i += 1
                elif bq_lines and ln.strip() and not (
                    _RE_HEADING.match(ln) or _RE_FENCE.match(ln) or
                    _RE_HR.match(ln) or _is_list(ln) or _RE_TABLE_ROW.match(ln)
                ):
                    # lazy continuation of the blockquote
                    bq_lines.append(ln)
                    i += 1
                else:
                    break
            inner = _parse_blocks(bq_lines)
            nodes.append(_blockquote(inner or [_para([_text_node("")])]))
            continue

        # ── list (before indented code so `    - item` stays a list item) ───
        if _is_list(s):
            list_node, i = _parse_list(lines, i)
            nodes.append(list_node)
            continue

        # ── indented code block (4-space) ────────────────────────────────────
        if _RE_INDENT4.match(raw):
            code_lines = []
            while i < n:
                mi = _RE_INDENT4.match(lines[i])
                if mi:
                    code_lines.append(mi.group(1).rstrip('\n'))
                elif not lines[i].strip():
                    code_lines.append("")
                else:
                    break
                i += 1
            nodes.append(_code_block("\n".join(code_lines).rstrip()))
            continue

        # ── GFM table ────────────────────────────────────────────────────────
        if _RE_TABLE_ROW.match(s) and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1].rstrip()):
            headers = _split_table_row(s)
            i += 2  # skip header + separator
            data_rows: list[list[str]] = []
            while i < n and _RE_TABLE_ROW.match(lines[i].rstrip()):
                data_rows.append(_split_table_row(lines[i].rstrip()))
                i += 1
            hrow  = _table_row([_th([_ipara(h)]) for h in headers])
            brows = [_table_row([_td([_ipara(c)]) for c in r]) for r in data_rows]
            nodes.append(_table_block([hrow] + brows))
            continue

        # ── paragraph ────────────────────────────────────────────────────────
        # Collect soft-continuation lines until a block-level element or blank.
        para_lines = [s]
        i += 1
        while i < n:
            nxt = lines[i].rstrip()
            if not nxt:
                break
            if (_RE_HEADING.match(nxt) or _RE_FENCE.match(nxt) or
                    _RE_HR.match(nxt) or _RE_BQ.match(nxt) or
                    _RE_TABLE_ROW.match(nxt) or _is_list(nxt)):
                break
            para_lines.append(nxt)
            i += 1
        nodes.append(_para(_parse_inline(" ".join(para_lines))))

    return nodes

# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def markdown_to_adf(md: str) -> dict:
    """
    Convert a Markdown string to an ADF document dict.

    The returned dict is ready to be used as the ``body`` field in Jira
    REST API v3 requests (issue comments, descriptions, etc.).
    """
    content = _parse_blocks(md.splitlines())
    if not content:
        content = [_para([_text_node("")])]
    return {"type": "doc", "version": 1, "content": content}
