"""Build the SQLite store from pinned CLDF releases."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import __version__, bibtex
from .cldf import CLDFDataset
from .fetch import ensure_files
from .registry import DatasetSpec, fingerprint

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE datasets (
  id TEXT PRIMARY KEY, role TEXT, title TEXT, short_title TEXT, version TEXT, edition_note TEXT,
  citation TEXT, license TEXT, homepage TEXT, repository TEXT,
  language_url TEXT, feature_url TEXT, contribution_url TEXT, source_url TEXT
);
CREATE TABLE languoids (
  glottocode TEXT PRIMARY KEY, name TEXT, level TEXT, category TEXT, family_id TEXT, parent_id TEXT,
  language_id TEXT, macroarea TEXT, latitude REAL, longitude REAL, iso TEXT, is_isolate INTEGER,
  lineage TEXT
);
CREATE TABLE languages (
  dataset TEXT, id TEXT, name TEXT, glottocode TEXT, iso TEXT, macroarea TEXT,
  latitude REAL, longitude REAL, genus TEXT, dataset_family TEXT,
  family_unit TEXT, family_name TEXT, family_basis TEXT, samples TEXT,
  PRIMARY KEY (dataset, id)
);
CREATE TABLE names (dataset TEXT, language_id TEXT, name TEXT, provider TEXT);
CREATE TABLE contributions (
  dataset TEXT, id TEXT, name TEXT, contributor TEXT, citation TEXT, PRIMARY KEY (dataset, id)
);
CREATE TABLE parameters (
  dataset TEXT, id TEXT, name TEXT, description TEXT, contribution_id TEXT, ord INTEGER,
  PRIMARY KEY (dataset, id)
);
CREATE TABLE codes (
  dataset TEXT, id TEXT, parameter_id TEXT, name TEXT, description TEXT, ord INTEGER,
  PRIMARY KEY (dataset, id)
);
CREATE TABLE datapoints (
  dataset TEXT, id TEXT, language_id TEXT, parameter_id TEXT, value TEXT, code_id TEXT,
  comment TEXT, source TEXT
);
CREATE TABLE sources (dataset TEXT, id TEXT, entry_type TEXT, citation TEXT, fields TEXT, PRIMARY KEY (dataset, id));
"""

INDEXES = """
CREATE INDEX dp_param ON datapoints (dataset, parameter_id, code_id);
CREATE INDEX dp_lang ON datapoints (dataset, language_id);
CREATE INDEX lang_gc ON languages (glottocode);
CREATE INDEX lang_iso ON languages (iso);
CREATE INDEX lo_parent ON languoids (parent_id);
CREATE INDEX lo_iso ON languoids (iso);
CREATE INDEX lo_family ON languoids (family_id);
CREATE INDEX names_lang ON names (dataset, language_id);
CREATE INDEX codes_param ON codes (dataset, parameter_id);
"""

PSEUDO_FAMILY = "Pseudo Family"


def _float(v: str) -> float | None:
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def _first(v: str, sep: str = ";") -> str:
    return v.split(sep)[0].strip() if v else ""


class Builder:
    def __init__(self, conn: sqlite3.Connection, log: Callable[[str], None]):
        self.c = conn
        self.log = log
        self.languoids: dict[str, dict] = {}
        self.top_families_by_name: dict[str, str] = {}

    # ---------------------------------------------------------------- catalog
    def load_catalog(self, spec: DatasetSpec, ds: CLDFDataset) -> None:
        lt, vt, ct, pt = (ds.table(x) for x in ("LanguageTable", "ValueTable", "CodeTable", "ParameterTable"))
        if not (lt and vt):
            raise ValueError(f"{spec.id}: catalog dataset needs LanguageTable and ValueTable")
        code_names = {row.get("ID", ""): row.get("Name", "") for row in (ct.rows() if ct else [])}
        for r in lt.rows():
            gc = lt.get(r, "glottocode") or lt.get(r, "id")
            self.languoids[gc] = {
                "glottocode": gc,
                "name": lt.get(r, "name"),
                "level": (r.get("Level") or "").strip(),
                "category": "",
                "family_id": (r.get("Family_ID") or "").strip(),
                "parent_id": "",
                "language_id": lt.get(r, "parentLanguageGlottocode") or (r.get("Language_ID") or "").strip(),
                "macroarea": lt.get(r, "macroarea"),
                "latitude": _float(lt.get(r, "latitude")),
                "longitude": _float(lt.get(r, "longitude")),
                "iso": lt.get(r, "iso639P3code"),
                "is_isolate": 1 if (r.get("Is_Isolate") or "").strip().lower() == "true" else 0,
                "lineage": "",
            }
        feature_params = set(spec.feature_parameters or ())
        feature_rows = []
        for r in vt.rows():
            pid, lid = vt.get(r, "parameterReference"), vt.get(r, "languageReference")
            lo = self.languoids.get(lid)
            if lo is None:
                continue
            if pid == "classification":
                lo["lineage"] = vt.get(r, "value")
                lo["parent_id"] = lo["lineage"].rsplit("/", 1)[-1] if lo["lineage"] else ""
            elif pid == "category":
                cid = vt.get(r, "codeReference")
                lo["category"] = code_names.get(cid) or vt.get(r, "value").replace("_", " ")
            if pid in feature_params:
                feature_rows.append(r)
        for lo in self.languoids.values():
            if lo["level"] == "family" and not lo["parent_id"]:
                self.top_families_by_name.setdefault(lo["name"].casefold(), lo["glottocode"])
        self.c.executemany(
            "INSERT INTO languoids VALUES (:glottocode,:name,:level,:category,:family_id,:parent_id,"
            ":language_id,:macroarea,:latitude,:longitude,:iso,:is_isolate,:lineage)",
            self.languoids.values(),
        )
        self.log(f"  {spec.id}: {len(self.languoids):,} languoids")

        if feature_params and pt:
            self._insert_dataset_row(spec)
            params = [p for p in pt.rows() if pt.get(p, "id") in feature_params]
            self.c.executemany(
                "INSERT INTO parameters VALUES (?,?,?,?,?,?)",
                [
                    (spec.id, pt.get(p, "id"), pt.get(p, "name"), pt.get(p, "description"), None, i)
                    for i, p in enumerate(params)
                ],
            )
            if ct:
                rows = []
                for i, r in enumerate(ct.rows()):
                    if ct.get(r, "parameterReference") in feature_params:
                        num = r.get("numerical_value") or r.get("Number") or ""
                        ord_ = int(num) if num.strip().lstrip("-").isdigit() else i
                        rows.append(
                            (
                                spec.id,
                                ct.get(r, "id"),
                                ct.get(r, "parameterReference"),
                                ct.get(r, "name"),
                                ct.get(r, "description"),
                                ord_,
                            )
                        )
                self.c.executemany("INSERT INTO codes VALUES (?,?,?,?,?,?)", rows)
            self.c.executemany(
                "INSERT INTO datapoints VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        spec.id,
                        vt.get(r, "id"),
                        vt.get(r, "languageReference"),
                        vt.get(r, "parameterReference"),
                        vt.get(r, "value"),
                        vt.get(r, "codeReference"),
                        vt.get(r, "comment"),
                        vt.get(r, "source"),
                    )
                    for r in feature_rows
                ],
            )
            coded = {vt.get(r, "languageReference") for r in feature_rows}
            lang_rows = []
            for gc in coded:
                lo = self.languoids[gc]
                lang_rows.append(
                    self._language_row(
                        spec.id,
                        gc,
                        lo["name"],
                        gc,
                        lo["iso"],
                        _first(lo["macroarea"]),
                        lo["latitude"],
                        lo["longitude"],
                        "",
                        "",
                    )
                )
            self.c.executemany("INSERT INTO languages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lang_rows)
            self.log(f"  {spec.id}: features {sorted(feature_params)} for {len(coded):,} languages")

        if spec.names_table and (nt := ds.table_by_url(spec.names_table)):
            self.c.executemany(
                "INSERT INTO names VALUES (?,?,?,?)",
                (
                    (
                        spec.id,
                        (r.get("Language_ID") or "").strip(),
                        (r.get("Name") or "").strip(),
                        (r.get("Provider") or "").strip(),
                    )
                    for r in nt.rows()
                    if (r.get("Language_ID") or "").strip() in self.languoids
                ),
            )

    # ---------------------------------------------------------------- family units
    def family_unit(
        self, glottocode: str, dataset_family: str, lang_id: str, dataset: str, genus: str = ""
    ) -> tuple[str, str, str]:
        """Return (unit_key, display_name, basis) used to count families."""
        lo = self.languoids.get(glottocode) if glottocode else None
        if lo and lo["level"] == "family":
            top = lo["lineage"].split("/")[0] if lo["lineage"] else glottocode
            return top, self.languoids.get(top, lo)["name"], "glottolog"
        if lo:
            lang_gc = lo["language_id"] if lo["level"] == "dialect" and lo["language_id"] else glottocode
            fam = lo["family_id"]
            if not fam or fam == lang_gc:
                name = self.languoids.get(lang_gc, lo)["name"]
                return lang_gc, f"{name} (isolate)", "glottolog"
            famlo = self.languoids.get(fam)
            if famlo and famlo["category"] == PSEUDO_FAMILY:
                name = self.languoids.get(lang_gc, lo)["name"]
                return lang_gc, f"{name} ({famlo['name']}, counted individually)", "glottolog"
            return fam, famlo["name"] if famlo else fam, "glottolog"
        for label in (dataset_family, genus):
            gc = self.top_families_by_name.get(label.casefold()) if label else None
            if gc:
                return gc, self.languoids[gc]["name"], "dataset family/genus name matched to glottolog"
        if dataset_family and dataset_family.casefold() not in ("other", "unclassified", "unknown"):
            return (
                f"{dataset}-family:{dataset_family}",
                f"{dataset_family} ({dataset} classification)",
                dataset,
            )
        return f"{dataset}-unclassified:{lang_id}", f"unclassified ({lang_id})", dataset

    def _language_row(self, ds, lid, name, gc, iso, macro, lat, lon, genus, dfam, samples=""):
        lo = self.languoids.get(gc) if gc else None
        if lo:
            macro = macro or _first(lo["macroarea"])
            lat = lat if lat is not None else lo["latitude"]
            lon = lon if lon is not None else lo["longitude"]
        unit, fname, basis = self.family_unit(gc, dfam, lid, ds, genus)
        return (
            ds,
            lid,
            name,
            gc or None,
            iso or None,
            macro or None,
            lat,
            lon,
            genus or None,
            dfam or None,
            unit,
            fname,
            basis,
            samples or None,
        )

    def _insert_dataset_row(self, spec: DatasetSpec) -> None:
        self.c.execute(
            "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                spec.id,
                spec.role,
                spec.title,
                spec.short_title or spec.title,
                spec.version,
                spec.edition_note,
                spec.citation,
                spec.license,
                spec.homepage,
                spec.repository,
                spec.language_url,
                spec.feature_url,
                spec.contribution_url,
                spec.source_url,
            ),
        )

    # ---------------------------------------------------------------- feature datasets
    def load_features(self, spec: DatasetSpec, ds: CLDFDataset) -> None:
        lt, pt, ct, vt = (ds.table(x) for x in ("LanguageTable", "ParameterTable", "CodeTable", "ValueTable"))
        if not (lt and pt and vt):
            raise ValueError(f"{spec.id}: a features dataset needs LanguageTable, ParameterTable and ValueTable")
        self._insert_dataset_row(spec)

        dp_rows, coded = [], set()
        for r in vt.rows():
            lid = vt.get(r, "languageReference")
            value, code = vt.get(r, "value"), vt.get(r, "codeReference")
            if not lid or (value == "" and code == ""):
                continue
            coded.add(lid)
            dp_rows.append(
                (
                    spec.id,
                    vt.get(r, "id"),
                    lid,
                    vt.get(r, "parameterReference"),
                    value,
                    code or None,
                    vt.get(r, "comment") or None,
                    vt.get(r, "source") or None,
                )
            )
        self.c.executemany("INSERT INTO datapoints VALUES (?,?,?,?,?,?,?,?)", dp_rows)

        fam_col = next((c for c in ("Family", "Family_Name", "family") if c in lt.columns), None)
        genus_col = next((c for c in ("Genus", "genus") if c in lt.columns), None)
        sample_cols = [c for c in lt.columns if c.lower().startswith("samples_")]
        lang_rows = []
        for r in lt.rows():
            lid = lt.get(r, "id")
            if lid not in coded:
                continue  # e.g. WALS genus/family rows in LanguageTable
            lang_rows.append(
                self._language_row(
                    spec.id,
                    lid,
                    lt.get(r, "name"),
                    lt.get(r, "glottocode"),
                    lt.get(r, "iso639P3code"),
                    _first(lt.get(r, "macroarea")),
                    _float(lt.get(r, "latitude")),
                    _float(lt.get(r, "longitude")),
                    (r.get(genus_col) or "").strip() if genus_col else "",
                    (r.get(fam_col) or "").strip() if fam_col else "",
                    ",".join(
                        spec.id + c.split("_", 1)[1]
                        for c in sample_cols
                        if (r.get(c) or "").strip().lower() in ("true", "1", "yes")
                    ),
                )
            )
        self.c.executemany("INSERT INTO languages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lang_rows)

        contrib_col = pt.col("contributionReference") or next(
            (c for c in ("Chapter_ID", "Contribution_ID") if c in pt.columns), None
        )
        self.c.executemany(
            "INSERT INTO parameters VALUES (?,?,?,?,?,?)",
            [
                (
                    spec.id,
                    pt.get(p, "id"),
                    pt.get(p, "name"),
                    pt.get(p, "description") or None,
                    (p.get(contrib_col) or "").strip() or None if contrib_col else None,
                    i,
                )
                for i, p in enumerate(pt.rows())
            ],
        )
        if ct:
            rows = []
            for i, r in enumerate(ct.rows()):
                num = (r.get("Number") or r.get("numerical_value") or "").strip()
                rows.append(
                    (
                        spec.id,
                        ct.get(r, "id"),
                        ct.get(r, "parameterReference"),
                        ct.get(r, "name"),
                        ct.get(r, "description") or None,
                        int(num) if num.lstrip("-").isdigit() else i,
                    )
                )
            self.c.executemany("INSERT INTO codes VALUES (?,?,?,?,?,?)", rows)
        if cont := ds.table("ContributionTable"):
            self.c.executemany(
                "INSERT INTO contributions VALUES (?,?,?,?,?)",
                [
                    (
                        spec.id,
                        cont.get(r, "id"),
                        cont.get(r, "name"),
                        cont.get(r, "contributor") or None,
                        cont.get(r, "citation") or None,
                    )
                    for r in cont.rows()
                ],
            )
        if spec.names_table and (nt := ds.table_by_url(spec.names_table)):
            known = {row[1] for row in lang_rows}
            self.c.executemany(
                "INSERT INTO names VALUES (?,?,?,?)",
                (
                    (
                        spec.id,
                        (r.get("Language_ID") or "").strip(),
                        (r.get("Name") or "").strip(),
                        (r.get("Provider") or "").strip(),
                    )
                    for r in nt.rows()
                    if (r.get("Language_ID") or "").strip() in known
                ),
            )
        if spec.bibliography and (ds.directory / spec.bibliography).exists():
            text = (ds.directory / spec.bibliography).read_text(encoding="utf-8")
            self.c.executemany(
                "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?)",
                (
                    (
                        spec.id,
                        key,
                        etype,
                        bibtex.format_citation(etype, fields),
                        json.dumps(fields, ensure_ascii=False),
                    )
                    for etype, key, fields in bibtex.parse(text)
                ),
            )
        n_params = self.c.execute("SELECT count(*) FROM parameters WHERE dataset=?", (spec.id,)).fetchone()[0]
        self.log(f"  {spec.id}: {len(lang_rows):,} languages, {n_params} features, {len(dp_rows):,} datapoints")


def build(specs: dict[str, DatasetSpec], db_path: Path, raw_root: Path, log: Callable[[str], None] = print) -> Path:
    """Fetch (if needed) and load all datasets into a fresh SQLite file, replacing db_path atomically."""
    started = time.time()
    dirs = {}
    for spec in specs.values():
        dirs[spec.id] = ensure_files(spec, raw_root, log)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=db_path.parent, prefix=".build-", suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp)
        conn.executescript(SCHEMA)
        b = Builder(conn, log)
        ordered = sorted(specs.values(), key=lambda s: s.role != "catalog")  # catalog first
        for spec in ordered:
            log(f"loading {spec.id} {spec.version}")
            ds = CLDFDataset(dirs[spec.id], spec.metadata)
            if spec.role == "catalog":
                b.load_catalog(spec, ds)
                if not spec.feature_parameters:
                    b._insert_dataset_row(spec)
            else:
                b.load_features(spec, ds)
        conn.executescript(INDEXES)
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("schema_version", SCHEMA_VERSION),
                ("fingerprint", fingerprint(specs)),
                ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                ("package_version", __version__),
            ],
        )
        conn.commit()
        conn.execute("ANALYZE")
        conn.close()
        os.replace(tmp, db_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    log(f"built {db_path} in {time.time() - started:.1f}s")
    return db_path


def is_current(db_path: Path, specs: dict[str, DatasetSpec]) -> bool:
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        conn.close()
    except sqlite3.Error:
        return False
    return meta.get("schema_version") == SCHEMA_VERSION and meta.get("fingerprint") == fingerprint(specs)
