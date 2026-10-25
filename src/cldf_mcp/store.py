"""Read-only query layer over the built SQLite store.

All tool logic lives here so it can be tested without an MCP client. Methods
return plain JSON-serialisable dicts and raise `QueryError` with a message
written for the model (what went wrong, and what to try instead).
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .build import readonly_uri
from .cldf import split_sources

GLOTTOCODE = re.compile(r"^[a-z0-9]{4}\d{4}$")
MACROAREAS = ("Africa", "Australia", "Eurasia", "North America", "Papunesia", "South America")
LEVEL_RANK = {"language": 0, "dialect": 1, "family": 2}


class QueryError(ValueError):
    """A problem with the request that the model can fix (unknown or ambiguous reference, bad value)."""


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", s).strip()


_SUFFIXES = (
    "ations",
    "ation",
    "ative",
    "ities",
    "ity",
    "ings",
    "ing",
    "ions",
    "ion",
    "ives",
    "ive",
    "als",
    "al",
    "ed",
    "s",
)


def stem(word: str) -> str:
    """Very light English stemmer so 'negation' and 'negative', 'plurality' and 'plural' meet."""
    for _ in range(2):
        for suf in _SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 4 and not (suf == "s" and word.endswith("ss")):
                word = word[: -len(suf)]
                break
        else:
            break
    return word


def tokens(s: str) -> list[str]:
    return [stem(t) for t in norm(s).split()]


def areas(value: str | None) -> str | list[str] | None:
    parts = [p.strip() for p in (value or "").split(";") if p.strip()]
    return None if not parts else parts[0] if len(parts) == 1 else parts


BIB_FIELDS = (
    "author",
    "editor",
    "year",
    "title",
    "booktitle",
    "journal",
    "volume",
    "number",
    "pages",
    "series",
    "edition",
    "publisher",
    "address",
    "school",
    "howpublished",
    "note",
    "url",
    "doi",
)


@dataclass
class Lang:
    """A dataset's language entry (e.g. WALS 'bsq')."""

    dataset: str
    id: str
    name: str
    glottocode: str | None
    iso: str | None
    macroarea: str | None
    latitude: float | None
    longitude: float | None
    genus: str | None
    dataset_family: str | None
    family_unit: str
    family_name: str
    family_basis: str
    samples: str | None = None
    n_values: int = 0

    @property
    def ref(self) -> str:
        return f"{self.dataset}:{self.id}"


@dataclass
class Resolved:
    """A language reference resolved to a Glottocode (if any) and dataset entries."""

    query: str
    glottocode: str | None
    name: str
    entries: dict[str, list[Lang]] = field(default_factory=dict)  # dataset -> entries, best first
    how: str = ""


class Store:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(readonly_uri(self.path), uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        q = self._q
        self.meta = {r["key"]: r["value"] for r in q("SELECT key, value FROM meta")}
        self.datasets = {r["id"]: dict(r) for r in q("SELECT * FROM datasets")}
        self.catalog = next((d for d, r in self.datasets.items() if r["role"] == "catalog"), None)
        self.feature_datasets = [d for d in self.datasets if self._has_features(d)]

        self.languoids: dict[str, sqlite3.Row] = {r["glottocode"]: r for r in q("SELECT * FROM languoids")}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.languoid_by_iso: dict[str, list[str]] = defaultdict(list)
        for gc, r in self.languoids.items():
            if r["parent_id"]:
                self.children[r["parent_id"]].append(gc)
            if r["iso"]:
                self.languoid_by_iso[r["iso"]].append(gc)

        counts = Counter()
        for r in q("SELECT dataset, language_id, count(*) n FROM datapoints GROUP BY dataset, language_id"):
            counts[(r["dataset"], r["language_id"])] = r["n"]
        self.langs: dict[str, dict[str, Lang]] = defaultdict(dict)
        self.langs_by_gc: dict[str, dict[str, list[Lang]]] = defaultdict(lambda: defaultdict(list))
        self.langs_by_iso: dict[str, list[Lang]] = defaultdict(list)
        for r in q("SELECT * FROM languages"):
            lang = Lang(**{k: r[k] for k in r.keys()}, n_values=counts[(r["dataset"], r["id"])])
            self.langs[lang.dataset][lang.id] = lang
            if lang.glottocode:
                self.langs_by_gc[lang.glottocode][lang.dataset].append(lang)
            if lang.iso:
                self.langs_by_iso[lang.iso].append(lang)
        self.samples = sorted(
            {s for per in self.langs.values() for x in per.values() for s in (x.samples or "").split(",") if s}
        )
        for per_ds in self.langs_by_gc.values():
            for entries in per_ds.values():
                entries.sort(key=self._entry_rank)

        self.params: dict[tuple[str, str], sqlite3.Row] = {
            (r["dataset"], r["id"]): r for r in q("SELECT * FROM parameters ORDER BY dataset, ord")
        }
        self.param_ids_ci: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for ds, pid in self.params:
            self.param_ids_ci[pid.casefold()].append((ds, pid))
        self.codes: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        self.code_by_id: dict[tuple[str, str], sqlite3.Row] = {}
        for r in q("SELECT * FROM codes ORDER BY dataset, parameter_id, ord"):
            self.codes[(r["dataset"], r["parameter_id"])].append(r)
            self.code_by_id[(r["dataset"], r["id"])] = r
        self.coverage = Counter(
            {
                (r["dataset"], r["parameter_id"]): r["n"]
                for r in q(
                    "SELECT dataset, parameter_id, count(DISTINCT language_id) n FROM datapoints "
                    "GROUP BY dataset, parameter_id"
                )
            }
        )
        self.contributions = {(r["dataset"], r["id"]): r for r in q("SELECT * FROM contributions")}
        self._build_name_index()

    # ------------------------------------------------------------------ helpers
    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def _has_features(self, ds: str) -> bool:
        return bool(self._q("SELECT 1 FROM parameters WHERE dataset=? LIMIT 1", (ds,)))

    def _entry_rank(self, lang: Lang):
        lo = self.languoids.get(lang.glottocode or "")
        exact_name = lo is not None and norm(lo["name"]) == norm(lang.name)
        return (not exact_name, -lang.n_values, lang.name)

    def provenance(self, *datasets: str) -> list[dict]:
        out = []
        for ds in dict.fromkeys(datasets):
            d = self.datasets.get(ds)
            if d:
                out.append(
                    {
                        "dataset": ds,
                        "title": d["title"],
                        "version": d["version"],
                        "citation": d["citation"],
                        "license": d["license"],
                    }
                )
        return out

    def _languoid_brief(self, gc: str | None) -> dict | None:
        lo = self.languoids.get(gc or "")
        if not lo:
            return None
        return {"glottocode": gc, "name": lo["name"], "level": lo["level"]}

    def family_label(self, gc: str | None) -> str | None:
        lo = self.languoids.get(gc or "")
        if not lo:
            return None
        if lo["level"] == "family" and not lo["parent_id"]:
            return lo["name"]
        fam = lo["family_id"]
        if not fam:
            return "isolate" if lo["level"] != "family" else lo["name"]
        return self.languoids[fam]["name"] if fam in self.languoids else fam

    def _url(self, ds: str, kind: str, ident: str) -> str | None:
        tmpl = self.datasets.get(ds, {}).get(f"{kind}_url")
        return tmpl.format(id=ident) if tmpl else None

    def lang_brief(self, lang: Lang, extra: bool = False) -> dict:
        out = {
            "ref": lang.ref,
            "name": lang.name,
            "glottocode": lang.glottocode,
            "family": lang.family_name,
            "macroarea": lang.macroarea,
        }
        if lang.genus:
            out["genus"] = lang.genus
        if extra:
            out.update(
                {
                    "iso639_3": lang.iso,
                    "latitude": lang.latitude,
                    "longitude": lang.longitude,
                    "datapoints": lang.n_values,
                    "url": self._url(lang.dataset, "language", lang.id),
                }
            )
        return out

    # ------------------------------------------------------------------ name index
    def _build_name_index(self) -> None:
        # target key: glottocode, or "ds:id" for dataset languages without one
        self.exact: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)  # norm -> (target, name, primary)
        for gc, lo in self.languoids.items():
            self.exact[norm(lo["name"])].append((gc, lo["name"], True))
        for ds, entries in self.langs.items():
            if ds == self.catalog:
                continue
            for lang in entries.values():
                tgt = lang.glottocode if lang.glottocode in self.languoids else lang.ref
                self.exact[norm(lang.name)].append((tgt, lang.name, True))
        for r in self._q("SELECT dataset, language_id, name FROM names"):
            if r["dataset"] == self.catalog:
                tgt = r["language_id"]
            else:
                lang = self.langs.get(r["dataset"], {}).get(r["language_id"])
                if not lang:
                    continue
                tgt = lang.glottocode if lang.glottocode in self.languoids else lang.ref
            n = norm(r["name"])
            if n:
                self.exact[n].append((tgt, r["name"], False))
        self.name_keys = sorted(self.exact)

    def _target_info(self, target: str) -> tuple[str, str, int]:
        """(name, level, coverage) for ranking."""
        if target in self.languoids:
            lo = self.languoids[target]
            cov = sum(e[0].n_values for ds, e in self.langs_by_gc.get(target, {}).items() if e and ds != self.catalog)
            return lo["name"], lo["level"], cov
        ds, _, lid = target.partition(":")
        lang = self.langs[ds][lid]
        return lang.name, "language", lang.n_values

    def _entries_for(self, target: str) -> dict[str, list[Lang]]:
        if target in self.languoids:
            return {ds: list(v) for ds, v in self.langs_by_gc.get(target, {}).items()}
        ds, _, lid = target.partition(":")
        return {ds: [self.langs[ds][lid]]}

    def _search_targets(self, query: str, limit: int) -> list[tuple[str, int, str]]:
        """Return [(target, score, how)] best first."""
        raw = query.strip()
        q = norm(raw)
        hits: dict[str, tuple[int, str]] = {}

        def hit(target: str, score: int, how: str):
            if target not in hits or hits[target][0] < score:
                hits[target] = (score, how)

        low = raw.lower()
        if ":" in low:
            prefix, _, val = low.partition(":")
            if prefix in self.langs and val in self.langs[prefix]:
                lang = self.langs[prefix][val]
                hit(lang.glottocode if lang.glottocode in self.languoids else lang.ref, 100, f"{prefix} code")
            elif prefix == "iso":
                for gc in self.languoid_by_iso.get(val, []):
                    hit(gc, 100, "ISO 639-3 code")
                for lang in self.langs_by_iso.get(val, []):
                    hit(
                        lang.glottocode if lang.glottocode in self.languoids else lang.ref,
                        99,
                        "ISO 639-3 code",
                    )
            elif prefix in ("glottolog", "glottocode") and val in self.languoids:
                hit(val, 100, "glottocode")
            if hits:
                return self._rank(hits, limit)
        if low in self.languoids:
            hit(low, 100, "glottocode")
        if re.fullmatch(r"[a-z]{3}", low):
            for gc in self.languoid_by_iso.get(low, []):
                hit(gc, 95, "ISO 639-3 code")
            for lang in self.langs_by_iso.get(low, []):
                hit(lang.glottocode if lang.glottocode in self.languoids else lang.ref, 94, "ISO 639-3 code")
        for ds in self.feature_datasets:
            lang = self.langs[ds].get(low) if ds != self.catalog else None
            if lang:
                hit(lang.glottocode if lang.glottocode in self.languoids else lang.ref, 95, f"{ds} code")
        if q:
            for tgt, name, primary in self.exact.get(q, []):
                hit(tgt, 90 if primary else 80, "name" if primary else f"alternate name '{name}'")
            if len(hits) < limit:
                for key in self.name_keys:
                    if key == q:
                        continue
                    if key.startswith(q + " ") or key.startswith(q):
                        score, how = 60, "name starts with query"
                    elif f" {q}" in f" {key}":
                        score, how = 45, "name contains query"
                    else:
                        continue
                    for tgt, name, primary in self.exact[key]:
                        hit(
                            tgt,
                            score + (5 if primary else 0),
                            how if primary else f"{how} (alternate name '{name}')",
                        )
            if len(hits) < limit and len(q) >= 3:
                for key in difflib.get_close_matches(q, self.name_keys, n=limit, cutoff=0.8):
                    for tgt, name, _primary in self.exact[key]:
                        hit(tgt, 20, f"similar name '{name}'")
        return self._rank(hits, limit)

    def _rank(self, hits: dict[str, tuple[int, str]], limit: int) -> list[tuple[str, int, str]]:
        def key(item):
            tgt, (score, _) = item
            name, level, cov = self._target_info(tgt)
            return (-score, LEVEL_RANK.get(level, 3), -cov, name)

        return [(t, s, h) for t, (s, h) in sorted(hits.items(), key=key)[:limit]]

    def _candidate(self, target: str, how: str) -> dict:
        entries = self._entries_for(target)
        cov = {
            ds: [{"ref": e.ref, "name": e.name, "datapoints": e.n_values} for e in es[:5]]
            for ds, es in entries.items()
            if ds != self.catalog and es
        }
        if target in self.languoids:
            lo = self.languoids[target]
            out = {
                "glottocode": target,
                "name": lo["name"],
                "level": lo["level"],
                "family": self.family_label(target),
                "macroarea": areas(lo["macroarea"]),
                "iso639_3": lo["iso"] or None,
                "match": how,
                "data": cov,
            }
            if lo["level"] == "language":
                via = {}
                for child in self.children.get(target, []):
                    for ds, es in self.langs_by_gc.get(child, {}).items():
                        if ds != self.catalog:
                            via.setdefault(ds, []).extend(e.ref for e in es)
                if via:
                    out["data_on_dialects"] = {ds: refs[:5] for ds, refs in via.items()}
            return out
        lang = self._entries_for(target)
        ((ds, (e, *_)),) = lang.items()
        return {
            "glottocode": None,
            "name": e.name,
            "level": "language",
            "family": e.family_name,
            "macroarea": e.macroarea,
            "iso639_3": e.iso,
            "match": how,
            "data": cov,
            "note": f"{e.ref} has no Glottocode in {ds}; refer to it as '{e.ref}'",
        }

    # ------------------------------------------------------------------ resolution
    def resolve_language(self, ref: str) -> Resolved:
        if not ref or not ref.strip():
            raise QueryError("Empty language reference.")
        hits = self._search_targets(ref, 12)
        if not hits:
            raise QueryError(
                f"No language matches {ref!r}. Try find_language with a different spelling, "
                "an ISO 639-3 code, or a Glottocode."
            )
        top_score = hits[0][1]
        best = [h for h in hits if h[1] == top_score]
        if len(best) > 1 and top_score < 100:
            # Prefer a single language-level target among equally good hits.
            langs = [h for h in best if self._target_info(h[0])[1] == "language"]
            if len(langs) == 1:
                best = langs
        if len(best) > 1 or top_score < 80:
            cands = [self._candidate(t, how) for t, _, how in hits[:8]]
            lines = []
            for c in cands:
                use = c["glottocode"] or next(iter(c["data"].values()))[0]["ref"]
                lines.append(
                    f"- {c['name']} [{c['level']}, {c['family']}, {c['macroarea']}] -> use '{use}' ({c['match']})"
                )
            reason = "is ambiguous" if len(best) > 1 else "has no exact match"
            raise QueryError(f"Language reference {ref!r} {reason}. Candidates:\n" + "\n".join(lines))
        target, _, how = best[0]
        entries = self._entries_for(target)
        low = ref.strip().lower()
        if ":" in low:
            prefix, _, val = low.partition(":")
            if prefix in self.langs and val in self.langs[prefix]:
                entries[prefix] = [self.langs[prefix][val]]  # an explicit dataset code pins that entry
        elif low in self.langs.get(self._feature_ds_for_code(low) or "", {}):
            ds = self._feature_ds_for_code(low)
            entries[ds] = [self.langs[ds][low]]
        name = self._target_info(target)[0]
        pinned = [es[0] for ds, es in entries.items() if ":" in low and low.partition(":")[0] == ds]
        if pinned:
            name = pinned[0].name
        return Resolved(ref, target if target in self.languoids else None, name, entries, how)

    def _feature_ds_for_code(self, code: str) -> str | None:
        for ds in self.feature_datasets:
            if ds != self.catalog and code in self.langs[ds]:
                return ds
        return None

    def resolve_feature(self, ref: str) -> tuple[str, str]:
        raw = (ref or "").strip()
        if not raw:
            raise QueryError("Empty feature reference.")
        if ":" in raw:
            ds, _, pid = raw.partition(":")
            ds = ds.lower()
            if ds not in self.datasets:
                raise QueryError(f"Unknown dataset {ds!r}. Loaded datasets: {', '.join(self.feature_datasets)}.")
            for d, p in self.param_ids_ci.get(pid.casefold(), []):
                if d == ds:
                    return d, p
            raise QueryError(f"{ds} has no feature {pid!r}. Use search_features to find feature IDs.")
        matches = self.param_ids_ci.get(raw.casefold(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            opts = ", ".join(f"'{d}:{p}'" for d, p in matches)
            raise QueryError(f"Feature ID {raw!r} exists in several datasets; use one of {opts}.")
        by_name = [k for k, r in self.params.items() if norm(r["name"]) == norm(raw)]
        if len(by_name) == 1:
            return by_name[0]
        sugg = self.search_features(raw, limit=5)["features"]
        hint = "; ".join(f"'{f['feature']}' ({f['name']})" for f in sugg) or "none found"
        raise QueryError(f"Unknown feature {raw!r}. Closest matches: {hint}.")

    def resolve_values(self, ds: str, pid: str, wanted: list[str]) -> set[str]:
        codes = self.codes.get((ds, pid), [])
        out = set()
        for w in wanted:
            w = str(w).strip()
            nw = norm(w)
            found = [c["id"] for c in codes if c["id"].casefold() == w.casefold()]
            found = found or [c["id"] for c in codes if str(c["ord"]) == w]
            found = found or [c["id"] for c in codes if norm(c["name"]) == nw]
            if not found:
                partial = [c["id"] for c in codes if nw and nw in norm(c["name"])]
                found = partial if len(partial) == 1 else []
            if not found:
                valid = "; ".join(f"{c['ord']} = {c['name']}" for c in codes)
                raise QueryError(f"{ds}:{pid} has no value {w!r}. Valid values: {valid}.")
            out.update(found)
        return out

    def code_label(self, ds: str, code_id: str | None, raw_value: str | None) -> str:
        c = self.code_by_id.get((ds, code_id or ""))
        return c["name"] if c else (raw_value or "")

    # ------------------------------------------------------------------ datapoints
    def _datapoints(self, ds: str, pid: str) -> list[sqlite3.Row]:
        return self._q(
            "SELECT language_id, value, code_id, comment, source FROM datapoints WHERE dataset=? AND parameter_id=?",
            (ds, pid),
        )

    def _unit_codes(self, unit_ds: str, ds: str, pid: str) -> dict[str, set[str]]:
        """Map unit-dataset language ids -> set of code ids for (ds, pid), joining via Glottocode if needed."""
        rows = self._datapoints(ds, pid)
        out: dict[str, set[str]] = defaultdict(set)
        if ds == unit_ds:
            for r in rows:
                out[r["language_id"]].add(r["code_id"] or r["value"])
            return out
        by_gc: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            lang = self.langs[ds].get(r["language_id"])
            if lang and lang.glottocode:
                by_gc[lang.glottocode].add(r["code_id"] or r["value"])
        for lid, lang in self.langs[unit_ds].items():
            if lang.glottocode and lang.glottocode in by_gc:
                out[lid] = by_gc[lang.glottocode]
        return out

    def _scope(
        self, unit_ds: str, macroarea: str | None, family: str | None, sample: str | None = None
    ) -> tuple[set[str] | None, dict]:
        """Language ids of unit_ds allowed by macroarea/family/sample filters (None = no filter)."""
        allowed = None
        echo = {}
        if sample:
            s = sample.strip().lower().replace(" ", "").replace("-", "")
            if s not in self.samples:
                raise QueryError(f"Unknown sample {sample!r}. Available: {', '.join(self.samples) or 'none'}.")
            allowed = {lid for lid, lang in self.langs[unit_ds].items() if s in (lang.samples or "").split(",")}
            if not allowed:
                raise QueryError(f"Sample {s!r} contains no languages from {unit_ds}.")
            echo["sample"] = s
        if macroarea:
            m = next((a for a in MACROAREAS if norm(a) == norm(macroarea)), None)
            if not m:
                raise QueryError(f"Unknown macroarea {macroarea!r}. Use one of: {', '.join(MACROAREAS)}.")
            ids = {lid for lid, lang in self.langs[unit_ds].items() if lang.macroarea == m}
            allowed = ids if allowed is None else allowed & ids
            echo["macroarea"] = m
        if family:
            gc = self._resolve_group(family)
            fam_name = self.languoids[gc]["name"] if gc in self.languoids else family
            ids = set()
            for lid, lang in self.langs[unit_ds].items():
                lo = self.languoids.get(lang.glottocode or "")
                if (lo and gc in (lo["lineage"] or "").split("/")) or lang.family_unit == gc or lang.glottocode == gc:
                    ids.add(lid)
            allowed = ids if allowed is None else allowed & ids
            echo["family"] = {"glottocode": gc, "name": fam_name}
        return allowed, echo

    def _resolve_group(self, ref: str, allow_language: bool = True) -> str:
        low = ref.strip().lower()
        if low in self.languoids:
            return low
        q = norm(ref)
        fams = [gc for gc, lo in self.languoids.items() if lo["level"] == "family" and norm(lo["name"]) == q]
        if len(fams) == 1:
            return fams[0]
        if len(fams) > 1:
            top = [gc for gc in fams if not self.languoids[gc]["parent_id"]]
            if len(top) == 1:
                return top[0]
            opts = "; ".join(f"'{gc}' ({self.languoids[gc]['lineage'] or 'top level'})" for gc in fams[:8])
            raise QueryError(f"Several Glottolog groups are called {ref!r}; use a Glottocode: {opts}.")
        if allow_language:
            try:
                res = self.resolve_language(ref)
            except QueryError:
                res = None
            if res and res.glottocode:
                return res.glottocode
        raise QueryError(
            f"No Glottolog family or subgroup named {ref!r}. Pass a family name like 'Austronesian', "
            "a subgroup like 'Germanic', or a Glottocode."
        )

    def _family_stats(self, langs: list[Lang]) -> dict:
        fams = Counter(lang.family_unit for lang in langs)
        out = {"languages": len(langs), "families": len(fams)}
        genera = {lang.genus for lang in langs if lang.genus}
        if genera:
            out["genera"] = len(genera)
        return out

    # ================================================================== tools
    def list_datasets(self) -> dict:
        out = []
        for ds, d in self.datasets.items():
            n_lang = len(self.langs.get(ds, {}))
            n_feat = sum(1 for (x, _) in self.params if x == ds)
            n_dp = self._q("SELECT count(*) n FROM datapoints WHERE dataset=?", (ds,))[0]["n"]
            entry = {
                "id": ds,
                "title": d["title"],
                "role": d["role"],
                "version": d["version"],
                "edition": d["edition_note"],
                "languages_with_data": n_lang,
                "features": n_feat,
                "datapoints": n_dp,
                "license": d["license"],
                "citation": d["citation"],
                "homepage": d["homepage"],
                "repository": d["repository"],
            }
            if d["role"] == "catalog":
                entry["languoids"] = len(self.languoids)
                entry["note"] = "Language catalog and classification; every dataset is joined through Glottocodes."
            out.append(entry)
        return {
            "datasets": out,
            "built_at": self.meta.get("built_at"),
            "server_version": self.meta.get("package_version"),
        }

    def find_language(self, query: str, limit: int = 8) -> dict:
        limit = max(1, min(limit, 25))
        hits = self._search_targets(query, limit)
        return {
            "query": query,
            "candidates": [self._candidate(t, how) for t, _, how in hits],
            "hint": "Pass a candidate's glottocode (or a dataset ref like 'wals:bsq') to other tools.",
        }

    def search_features(self, query: str, dataset: str | None = None, limit: int = 15) -> dict:
        limit = max(1, min(limit, 50))
        if dataset and dataset not in self.datasets:
            raise QueryError(f"Unknown dataset {dataset!r}. Loaded: {', '.join(self.feature_datasets)}.")
        qt = tokens(query)
        exact_id = (query or "").strip().casefold()
        scored = []
        for (ds, pid), p in self.params.items():
            if dataset and ds != dataset:
                continue
            name_t = set(tokens(p["name"]))
            contrib = self.contributions.get((ds, p["contribution_id"] or ""))
            chap_t = set(tokens(contrib["name"])) if contrib else set()
            desc_t = set(tokens(p["description"] or ""))
            code_t = set(t for c in self.codes.get((ds, pid), []) for t in tokens(c["name"]))
            score = 1000 if pid.casefold() == exact_id or f"{ds}:{pid}".casefold() == exact_id else 0
            if norm(query) and norm(query) in norm(p["name"]):
                score += 100
            matched = 0
            for t in qt:
                s = 10 * (t in name_t) or 5 * (t in chap_t) or 3 * (t in code_t) or 2 * (t in desc_t)
                if not s and len(t) >= 4:
                    s = 4 if any(n.startswith(t) for n in name_t) else 0
                score += s
                matched += bool(s)
            if score and (score >= 100 or matched >= max(1, (len(qt) + 1) // 2)):
                scored.append((score, self.coverage[(ds, pid)], ds, pid))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
        out = []
        for _, cov, ds, pid in scored[:limit]:
            p = self.params[(ds, pid)]
            vals = [c["name"] for c in self.codes.get((ds, pid), [])]
            out.append(
                {
                    "feature": f"{ds}:{pid}",
                    "name": p["name"],
                    "languages_coded": cov,
                    "values": vals[:12] + (["…"] if len(vals) > 12 else []),
                }
            )
        return {
            "query": query,
            "features": out,
            "total_matches": len(scored),
            "truncated": len(scored) > limit,
        }

    def get_feature(
        self,
        feature: str,
        by_macroarea: bool = False,
        macroarea: str | None = None,
        family: str | None = None,
        sample: str | None = None,
    ) -> dict:
        ds, pid = self.resolve_feature(feature)
        p = self.params[(ds, pid)]
        allowed, scope_echo = self._scope(ds, macroarea, family, sample)
        by_code: dict[str, list[Lang]] = defaultdict(list)
        all_langs = []
        for r in self._datapoints(ds, pid):
            lang = self.langs[ds].get(r["language_id"])
            if lang and (allowed is None or lang.id in allowed):
                by_code[r["code_id"] or r["value"]].append(lang)
                all_langs.append(lang)
        order = [c["id"] for c in self.codes.get((ds, pid), [])]
        order += [code for code in by_code if code not in order]
        values = []
        for code in order:
            c = self.code_by_id.get((ds, code))
            ls = by_code.get(code, [])
            entry = {"value": c["name"] if c else code, "code": code if c else None, **self._family_stats(ls)}
            if c and c["description"] and norm(c["description"]) != norm(c["name"]):
                entry["description"] = c["description"]
            values.append(entry)
        out = {
            "feature": f"{ds}:{pid}",
            "name": p["name"],
            "description": p["description"],
            "scope": scope_echo or "all languages",
            "coverage": self._family_stats(all_langs),
            "values": values,
            "url": self._url(ds, "feature", pid),
        }
        contrib = self.contributions.get((ds, p["contribution_id"] or ""))
        if contrib:
            out["chapter"] = {
                "id": contrib["id"],
                "title": contrib["name"],
                "authors": contrib["contributor"],
                "citation": contrib["citation"],
                "url": self._url(ds, "contribution", contrib["id"]),
            }
        if by_macroarea:
            table: dict[str, dict[str, dict]] = {}
            for area in sorted({x.macroarea or "unknown" for x in all_langs}):
                row = {}
                for code in order:
                    ls = [x for x in by_code.get(code, []) if (x.macroarea or "unknown") == area]
                    if ls:
                        row[self.code_label(ds, code, code)] = {
                            "languages": len(ls),
                            "families": len({x.family_unit for x in ls}),
                        }
                table[area] = row
            out["by_macroarea"] = table
        out["note"] = (
            "'families' counts distinct Glottolog top-level families attesting each value; compare it "
            "with 'languages' before generalising (see resource cldf://guide/typology-caveats)."
        )
        out["provenance"] = self.provenance(ds, *([self.catalog] if self.catalog else []))
        return out

    def get_values(self, languages: list[str], features: list[str]) -> dict:
        if not languages or not features:
            raise QueryError("Pass at least one language and one feature.")
        if len(languages) > 50 or len(features) > 50:
            raise QueryError("At most 50 languages and 50 features per call; split the request.")
        errors, feats = [], []
        for f in features:
            try:
                feats.append(self.resolve_feature(f))
            except QueryError as e:
                errors.append({"feature": f, "error": str(e)})
        results, cited = [], defaultdict(set)
        for ref in languages:
            try:
                res = self.resolve_language(ref)
            except QueryError as e:
                errors.append({"language": ref, "error": str(e)})
                continue
            row = {"language": {"query": ref, "name": res.name, "glottocode": res.glottocode}, "values": {}}
            used = {}
            for ds, pid in feats:
                key = f"{ds}:{pid}"
                entries = res.entries.get(ds, [])
                if not entries:
                    status = "no data" if ds == self.catalog and res.glottocode else "language not in dataset"
                    row["values"][key] = {"status": status}
                    continue
                entry = entries[0]
                used[ds] = entry
                dps = self._q(
                    "SELECT value, code_id, comment, source FROM datapoints "
                    "WHERE dataset=? AND parameter_id=? AND language_id=?",
                    (ds, pid, entry.id),
                )
                if not dps:
                    row["values"][key] = {"status": "no data"}
                    continue
                vals = []
                for dp in dps:
                    v = {"value": self.code_label(ds, dp["code_id"], dp["value"]), "code": dp["code_id"]}
                    if dp["comment"]:
                        v["comment"] = dp["comment"]
                    if dp["source"]:
                        v["sources"] = split_sources(dp["source"])
                        for s in v["sources"]:
                            cited[ds].add(s["id"])
                    vals.append(v)
                row["values"][key] = vals[0] if len(vals) == 1 else vals
            row["language"]["entries_used"] = {ds: e.ref for ds, e in used.items()}
            others = {ds: [e.ref for e in es[1:6]] for ds, es in res.entries.items() if len(es) > 1 and ds in used}
            if others:
                row["language"]["other_entries_same_glottocode"] = others
            results.append(row)
        out = {"features": {f"{d}:{p}": self.params[(d, p)]["name"] for d, p in feats}, "results": results}
        if cited:
            out["sources"] = self._source_citations(cited)
        if errors:
            out["errors"] = errors
        out["provenance"] = self.provenance(*(d for d, _ in feats))
        return out

    def _source_citations(self, cited: dict[str, set[str]]) -> dict[str, str]:
        out = {}
        for ds, ids in cited.items():
            ids = sorted(ids)
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                for r in self._q(
                    f"SELECT id, citation FROM sources WHERE dataset=? AND id IN ({','.join('?' * len(chunk))})",
                    (ds, *chunk),
                ):
                    out[r["id"]] = r["citation"]
            for sid in ids:
                if sid not in out and (url := self._url(ds, "source", sid)):
                    out[sid] = url
        return out

    def language_profile(self, language: str, dataset: str | None = None, limit: int = 120) -> dict:
        limit = max(1, min(limit, 500))
        res = self.resolve_language(language)
        out: dict = {"query": language}
        lo = self.languoids.get(res.glottocode or "")
        if lo:
            out["language"] = {
                "name": lo["name"],
                "glottocode": res.glottocode,
                "level": lo["level"],
                "iso639_3": lo["iso"] or None,
                "macroarea": areas(lo["macroarea"]),
                "latitude": lo["latitude"],
                "longitude": lo["longitude"],
                "family": self.family_label(res.glottocode),
                "classification": [self.languoids[g]["name"] for g in (lo["lineage"] or "").split("/") if g],
                "url": self._url(self.catalog, "language", res.glottocode) if self.catalog else None,
            }
            if self.catalog:
                for r in self._q(
                    "SELECT parameter_id, value, code_id FROM datapoints WHERE dataset=? AND language_id=?",
                    (self.catalog, res.glottocode),
                ):
                    label = self.code_label(self.catalog, r["code_id"], r["value"])
                    name = {"aes": "endangerment", "med": "most_extensive_description"}.get(
                        r["parameter_id"], r["parameter_id"]
                    )
                    out["language"][name] = label
        else:
            e = next(iter(res.entries.values()))[0]
            out["language"] = {**self.lang_brief(e, extra=True), "note": "No Glottocode in the dataset."}
        datasets_out = {}
        for ds, entries in res.entries.items():
            if ds == self.catalog or (dataset and ds != dataset):
                continue
            entry = entries[0]
            rows = self._q(
                "SELECT d.parameter_id, d.value, d.code_id FROM datapoints d JOIN parameters p "
                "ON p.dataset=d.dataset AND p.id=d.parameter_id WHERE d.dataset=? AND d.language_id=? ORDER BY p.ord",
                (ds, entry.id),
            )
            vals = [
                {
                    "feature": f"{ds}:{r['parameter_id']}",
                    "name": self.params[(ds, r["parameter_id"])]["name"],
                    "value": self.code_label(ds, r["code_id"], r["value"]),
                }
                for r in rows[:limit]
            ]
            block = {
                "entry": self.lang_brief(entry, extra=True),
                "values": vals,
                "total_values": len(rows),
                "truncated": len(rows) > limit,
            }
            if len(entries) > 1:
                block["other_entries_same_glottocode"] = [
                    {"ref": e.ref, "name": e.name, "datapoints": e.n_values} for e in entries[1:10]
                ]
            datasets_out[ds] = block
        if lo and lo["level"] == "language":
            via = {}
            for child in self.children.get(res.glottocode, []):
                for ds, es in self.langs_by_gc.get(child, {}).items():
                    if ds != self.catalog and (not dataset or ds == dataset):
                        via.setdefault(ds, []).extend({"ref": e.ref, "name": e.name} for e in es)
            if via:
                out["dialect_entries"] = via
        out["data"] = datasets_out
        if not datasets_out:
            out["note"] = "No feature data for this language in the loaded datasets."
        out["hint"] = "Use get_values for sources and comments on specific features."
        out["provenance"] = self.provenance(*datasets_out, *([self.catalog] if self.catalog else []))
        return out

    def filter_languages(
        self,
        conditions: list[dict],
        macroarea: str | None = None,
        family: str | None = None,
        sample: str | None = None,
        limit: int = 50,
    ) -> dict:
        if not conditions:
            raise QueryError("Pass at least one condition, e.g. {'feature': 'wals:81A', 'values': ['SOV']}.")
        if len(conditions) > 8:
            raise QueryError("At most 8 conditions per call.")
        limit = max(0, min(limit, 300))
        resolved = []
        for c in conditions:
            ds, pid = self.resolve_feature(c["feature"])
            codes = self.resolve_values(ds, pid, list(c.get("values") or []))
            if not codes:
                raise QueryError(f"Condition on {ds}:{pid} needs at least one value.")
            resolved.append((ds, pid, codes, bool(c.get("exclude"))))
        unit_ds = next((ds for ds, *_ in resolved if ds != self.catalog), resolved[0][0])
        allowed, scope_echo = self._scope(unit_ds, macroarea, family, sample)
        coded: set[str] | None = None
        matches: set[str] | None = None
        for ds, pid, codes, exclude in resolved:
            unit_codes = self._unit_codes(unit_ds, ds, pid)
            have = set(unit_codes)
            ok = {lid for lid, cs in unit_codes.items() if bool(cs & codes) != exclude}
            coded = have if coded is None else coded & have
            matches = ok if matches is None else matches & ok
        if allowed is not None:
            coded &= allowed
            matches &= allowed
        mlangs = sorted((self.langs[unit_ds][lid] for lid in matches), key=lambda x: (x.family_name, x.name))
        clangs = [self.langs[unit_ds][lid] for lid in coded]
        fam_counts = Counter(lang.family_name for lang in mlangs)
        out = {
            "conditions": [
                {
                    "feature": f"{ds}:{pid}",
                    "name": self.params[(ds, pid)]["name"],
                    "values": sorted(self.code_label(ds, c, c) for c in codes),
                    "exclude": ex,
                }
                for ds, pid, codes, ex in resolved
            ],
            "scope": scope_echo or "all languages",
            "counted_in": unit_ds,
            "matches": self._family_stats(mlangs),
            "coded_for_all_conditions": self._family_stats(clangs),
            "share_of_coded_languages": round(len(mlangs) / len(clangs), 3) if clangs else None,
            "share_of_coded_families": (
                round(len({x.family_unit for x in mlangs}) / len({x.family_unit for x in clangs}), 3)
                if clangs
                else None
            ),
            "largest_families_in_matches": [{"family": f, "languages": n} for f, n in fam_counts.most_common(8)],
            "languages": [self.lang_brief(x) for x in mlangs[:limit]],
            "truncated": len(mlangs) > limit,
        }
        out["provenance"] = self.provenance(*(ds for ds, *_ in resolved))
        return out

    def crosstab(
        self,
        feature_a: str,
        feature_b: str,
        macroarea: str | None = None,
        family: str | None = None,
        sample: str | None = None,
        by_macroarea: bool = False,
    ) -> dict:
        (da, pa), (db, pb) = self.resolve_feature(feature_a), self.resolve_feature(feature_b)
        if (da, pa) == (db, pb):
            raise QueryError("feature_a and feature_b are the same feature; use get_feature for one feature.")
        unit_ds = da if da != self.catalog else db
        allowed, scope_echo = self._scope(unit_ds, macroarea, family, sample)
        ca, cb = self._unit_codes(unit_ds, da, pa), self._unit_codes(unit_ds, db, pb)
        both = set(ca) & set(cb)
        if allowed is not None:
            both &= allowed
        cells: dict[tuple[str, str], list[Lang]] = defaultdict(list)
        for lid in both:
            lang = self.langs[unit_ds][lid]
            for a in ca[lid]:
                for b in cb[lid]:
                    cells[(a, b)].append(lang)
        labels_a = [c["id"] for c in self.codes.get((da, pa), [])] or sorted({a for a, _ in cells})
        labels_b = [c["id"] for c in self.codes.get((db, pb), [])] or sorted({b for _, b in cells})
        rows = []
        for a in labels_a:
            for b in labels_b:
                ls = cells.get((a, b), [])
                row = {
                    "a": self.code_label(da, a, a),
                    "b": self.code_label(db, b, b),
                    **self._family_stats(ls),
                }
                if ls:
                    fam, n = Counter(x.family_name for x in ls).most_common(1)[0]
                    row["largest_family"] = {"name": fam, "languages": n, "share": round(n / len(ls), 2)}
                rows.append(row)
        rows.sort(key=lambda r: (-r["languages"], r["a"], r["b"]))
        all_langs = [self.langs[unit_ds][lid] for lid in both]

        def margins(codes_map, ds, labels):
            m = []
            for code in labels:
                ls = [self.langs[unit_ds][lid] for lid in both if code in codes_map[lid]]
                m.append({"value": self.code_label(ds, code, code), **self._family_stats(ls)})
            return m

        out = {
            "feature_a": {"feature": f"{da}:{pa}", "name": self.params[(da, pa)]["name"]},
            "feature_b": {"feature": f"{db}:{pb}", "name": self.params[(db, pb)]["name"]},
            "scope": scope_echo or "all languages",
            "counted_in": unit_ds,
            "coded_for_both": self._family_stats(all_langs),
            "cells": rows,
            "margins_a": margins(ca, da, labels_a),
            "margins_b": margins(cb, db, labels_b),
        }
        if by_macroarea:
            t = defaultdict(dict)
            for (a, b), ls in cells.items():
                for area, group in self._by_area(ls).items():
                    t[area][f"{self.code_label(da, a, a)} | {self.code_label(db, b, b)}"] = self._family_stats(group)
            out["by_macroarea"] = {k: dict(sorted(v.items())) for k, v in sorted(t.items())}
        out["note"] = (
            "A family can appear in several cells. Prefer the 'families' (and 'genera') columns over raw language "
            "counts when judging whether a combination is common, and check 'largest_family' for cells dominated "
            "by one lineage. Zero cells mean unattested in this sample, not impossible."
        )
        out["provenance"] = self.provenance(da, db)
        return out

    @staticmethod
    def _by_area(ls: list[Lang]) -> dict[str, list[Lang]]:
        g = defaultdict(list)
        for x in ls:
            g[x.macroarea or "unknown"].append(x)
        return g

    def classification(self, language: str, max_children: int = 40) -> dict:
        max_children = max(0, min(max_children, 200))
        low = (language or "").strip().lower()
        if low in self.languoids:
            gc = low
        else:
            try:
                gc = self._resolve_group(language, allow_language=False)
            except QueryError:
                res = self.resolve_language(language)
                if not res.glottocode:
                    raise QueryError(
                        f"{language!r} has no Glottocode, so it has no Glottolog classification."
                    ) from None
                gc = res.glottocode
        lo = self.languoids[gc]
        lineage = [g for g in (lo["lineage"] or "").split("/") if g]

        def coverage_under(node: str) -> dict:
            cov = Counter()
            for g, per_ds in self.langs_by_gc.items():
                x = self.languoids.get(g)
                if x is None or (g != node and node not in (x["lineage"] or "").split("/")):
                    continue
                for ds, es in per_ds.items():
                    if ds != self.catalog:
                        cov[ds] += len(es)
            return dict(cov)

        def brief(g: str, with_cov: bool = False) -> dict:
            x = self.languoids[g]
            b = {"glottocode": g, "name": x["name"], "level": x["level"]}
            if x["category"] and x["category"] not in ("Family", "Spoken L1 Language", "Dialect"):
                b["category"] = x["category"]
            if with_cov:
                b["data_entries_below"] = coverage_under(g)
            return b

        kids = sorted(
            self.children.get(gc, []),
            key=lambda g: (LEVEL_RANK.get(self.languoids[g]["level"], 3), self.languoids[g]["name"]),
        )
        parent = lo["parent_id"]
        sisters = (
            sorted((g for g in self.children.get(parent, []) if g != gc), key=lambda g: self.languoids[g]["name"])
            if parent
            else []
        )
        n_langs_below = sum(
            1 for g, x in self.languoids.items() if x["level"] == "language" and gc in (x["lineage"] or "").split("/")
        )
        out = {
            "languoid": {
                **brief(gc),
                "iso639_3": lo["iso"] or None,
                "macroarea": areas(lo["macroarea"]),
                "is_isolate": bool(lo["is_isolate"]),
                "url": self._url(self.catalog, "language", gc),
            },
            "lineage": [brief(g) for g in lineage],
            "family": self.family_label(gc),
            "languages_below": n_langs_below,
            "children": [brief(g, with_cov=True) for g in kids[:max_children]],
            "children_total": len(kids),
            "children_truncated": len(kids) > max_children,
            "sisters": [brief(g) for g in sisters[:max_children]],
            "sisters_total": len(sisters),
            "data_entries": {
                ds: [e.ref for e in es] for ds, es in self.langs_by_gc.get(gc, {}).items() if ds != self.catalog
            },
        }
        out["provenance"] = self.provenance(self.catalog) if self.catalog else []
        return out

    def get_source(self, source_ids: list[str], dataset: str | None = None) -> dict:
        if not source_ids:
            raise QueryError("Pass at least one source ID, e.g. 'Nekitel-1985'.")
        if len(source_ids) > 50:
            raise QueryError("At most 50 source IDs per call.")
        out, missing = [], []
        for raw in source_ids:
            parsed = split_sources(raw) or [{"id": raw}]
            for s in parsed:
                sid = s["id"]
                rows = self._q(
                    "SELECT * FROM sources WHERE id=?" + (" AND dataset=?" if dataset else ""),
                    (sid, dataset) if dataset else (sid,),
                )
                if rows:
                    for r in rows:
                        fields = json.loads(r["fields"])
                        entry = {
                            "id": sid,
                            "dataset": r["dataset"],
                            "type": r["entry_type"],
                            "citation": r["citation"],
                            "fields": {k: fields[k] for k in BIB_FIELDS if fields.get(k)},
                        }
                        if s.get("pages"):
                            entry["pages_cited"] = s["pages"]
                        out.append(entry)
                elif sid.isdigit() and self.catalog and (url := self._url(self.catalog, "source", sid)):
                    out.append(
                        {
                            "id": sid,
                            "dataset": self.catalog,
                            "url": url,
                            "note": "Glottolog reference; its full bibliography is not bundled, see the URL.",
                        }
                    )
                else:
                    missing.append(sid)
        res = {"sources": out}
        if missing:
            res["not_found"] = missing
        return res
