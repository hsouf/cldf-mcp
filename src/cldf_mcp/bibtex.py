"""Minimal BibTeX reader, enough for CLDF sources.bib files."""

from __future__ import annotations

import re
from collections.abc import Iterator

_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*[{(]")
_FIELD = re.compile(r"\s*([A-Za-z0-9_\-:.]+)\s*=\s*")


def _read_braced(text: str, i: int) -> tuple[str, int]:
    """text[i] == '{' ; returns (content, index after the closing brace)."""
    depth, start = 0, i
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _read_value(text: str, i: int) -> tuple[str, int]:
    if i < len(text) and text[i] == "{":
        return _read_braced(text, i)
    if i < len(text) and text[i] == '"':
        j = i + 1
        depth = 0
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            elif text[j] == '"' and depth == 0:
                return text[i + 1 : j], j + 1
            j += 1
        return text[i + 1 :], len(text)
    m = re.match(r"[^,}\s]+", text[i:])
    if m:
        return m.group(0), i + m.end()
    return "", i


def parse(text: str) -> Iterator[tuple[str, str, dict[str, str]]]:
    """Yield (entry_type, key, fields)."""
    pos = 0
    while True:
        m = _ENTRY.search(text, pos)
        if not m:
            return
        etype = m.group(1).lower()
        body, end = _read_braced(text, m.end() - 1) if text[m.end() - 1] == "{" else ("", m.end())
        pos = end
        if etype in ("comment", "string", "preamble") or not body:
            continue
        key, _, rest = body.partition(",")
        fields: dict[str, str] = {}
        i = 0
        while i < len(rest):
            fm = _FIELD.match(rest, i)
            if not fm:
                nxt = rest.find(",", i)
                if nxt == -1:
                    break
                i = nxt + 1
                continue
            name = fm.group(1).lower()
            value, i = _read_value(rest, fm.end())
            fields[name] = clean(value)
            while i < len(rest) and rest[i] in " \t\r\n,":
                i += 1
        yield etype, key.strip(), fields


def clean(value: str) -> str:
    value = re.sub(r"\\[`'^\"~=.uvHcdbk]\s*\{?([A-Za-z])\}?", r"\1", value)  # crude accent stripping fallback
    value = value.replace("{", "").replace("}", "").replace("\\&", "&")
    return re.sub(r"\s+", " ", value).strip()


def format_citation(entry_type: str, f: dict[str, str]) -> str:
    who = f.get("author") or f.get("editor") or "Anonymous"
    if not f.get("author") and f.get("editor"):
        who += " (ed.)"
    parts = [who.rstrip("."), f.get("year", "n.d."), f.get("title", "").rstrip(".")]
    if entry_type in ("article",) and f.get("journal"):
        j = f["journal"]
        if f.get("volume"):
            j += f" {f['volume']}"
            if f.get("number"):
                j += f"({f['number']})"
        if f.get("pages"):
            j += f": {f['pages']}"
        parts.append(j)
    elif entry_type in ("incollection", "inproceedings") and f.get("booktitle"):
        inner = f"In {f['editor']} (ed.), " if f.get("editor") and f.get("author") else "In "
        inner += f["booktitle"]
        if f.get("pages"):
            inner += f", {f['pages']}"
        parts.append(inner)
    elif entry_type in ("phdthesis", "mastersthesis") and f.get("school"):
        parts.append(("PhD thesis" if entry_type == "phdthesis" else "MA thesis") + f", {f['school']}")
    place = ": ".join(x for x in (f.get("address"), f.get("publisher")) if x)
    if place and entry_type not in ("article",):
        parts.append(place)
    return ". ".join(p for p in parts if p) + "."
