"""A small, dependency-free reader for CLDF StructureDatasets.

It reads the JSON metadata to find the standard components (LanguageTable,
ParameterTable, CodeTable, ValueTable, ContributionTable) and maps their columns
to CLDF ontology terms, so datasets with non-default column names still load.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

TERMS = "http://cldf.clld.org/v1.0/terms.rdf#"

# Fallback column names when a column has no propertyUrl.
DEFAULT_COLUMNS = {
    "id": "ID",
    "name": "Name",
    "description": "Description",
    "glottocode": "Glottocode",
    "iso639P3code": "ISO639P3code",
    "macroarea": "Macroarea",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "languageReference": "Language_ID",
    "parameterReference": "Parameter_ID",
    "codeReference": "Code_ID",
    "value": "Value",
    "comment": "Comment",
    "source": "Source",
    "contributionReference": "Contribution_ID",
    "contributor": "Contributor",
    "citation": "Citation",
}

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


@dataclass
class Table:
    path: Path
    columns: list[str]
    terms: dict[str, str]  # CLDF term -> column name
    separators: dict[str, str]  # column name -> list separator

    def col(self, term: str) -> str | None:
        if term in self.terms:
            return self.terms[term]
        fallback = DEFAULT_COLUMNS.get(term)
        return fallback if fallback in self.columns else None

    def rows(self) -> Iterator[dict[str, str]]:
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)

    def get(self, row: dict[str, str], term: str) -> str:
        c = self.col(term)
        return (row.get(c) or "").strip() if c else ""


class CLDFDataset:
    def __init__(self, directory: Path, metadata_file: str):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / metadata_file).read_text(encoding="utf-8"))
        self.tables: dict[str, Table] = {}
        self.by_url: dict[str, Table] = {}
        for t in self.metadata.get("tables", []):
            cols = t.get("tableSchema", {}).get("columns", [])
            terms, seps, names = {}, {}, []
            for c in cols:
                name = c["name"]
                names.append(name)
                prop = c.get("propertyUrl", "")
                if prop.startswith(TERMS):
                    term = prop[len(TERMS) :]
                    # The first column wins (e.g. WALS LanguageTable has Parent_ID as a 2nd languageReference).
                    terms.setdefault(term, name)
                if c.get("separator"):
                    seps[name] = c["separator"]
            table = Table(self.directory / t["url"], names, terms, seps)
            self.by_url[t["url"]] = table
            component = t.get("dc:conformsTo") or ""
            if component.startswith(TERMS):
                self.tables.setdefault(component[len(TERMS) :], table)

    def table(self, component: str) -> Table | None:
        t = self.tables.get(component)
        return t if t and t.path.exists() else None

    def table_by_url(self, url: str) -> Table | None:
        t = self.by_url.get(url)
        if t is None and (self.directory / url).exists():
            with open(self.directory / url, encoding="utf-8-sig", newline="") as f:
                header = next(csv.reader(f), [])
            t = Table(self.directory / url, header, {}, {})
        return t if t and t.path.exists() else None


def split_sources(raw: str) -> list[dict[str, str]]:
    """'Nekitel-1985[94];Foo-1990' -> [{'id': 'Nekitel-1985', 'pages': '94'}, {'id': 'Foo-1990'}]"""
    parts, depth, buf = [], 0, []
    for ch in raw:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.endswith("]") and "[" in part:
            key, pages = part[:-1].split("[", 1)
            out.append({"id": key.strip(), "pages": pages.strip()})
        else:
            out.append({"id": part})
    return out
