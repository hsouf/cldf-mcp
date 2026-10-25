"""Regenerate the small test fixtures from full WALS and Glottolog CLDF directories.

    python tests/fixtures/make_fixtures.py PATH/TO/wals/cldf PATH/TO/glottolog/cldf

The fixtures are subsets of WALS (CC-BY-4.0, Dryer & Haspelmath 2013) and Glottolog
(CC-BY-4.0, Hammarström et al. 2026); see tests/fixtures/README.md.
"""

from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
WALS_LANGS = [
    "bsq",
    "bqb",
    "bso",
    "eng",
    "ger",
    "jpn",
    "mnd",
    "tag",
    "wel",
    "iri",
    "swa",
    "yor",
    "alb",
    "aze",
    "asl",
    "haw",
    "fin",
    "tur",
    "kor",
    "hin",
]
WALS_PARAMS = ["1A", "14A", "81A", "85A", "87A", "139A"]
EXTRA_GLOTTOCODES = ["nucl1418"]  # Bassa: ISO 'bsq', clashes with WALS code 'bsq' for Basque
BIB_KEYS_LIMIT = 400

csv.field_size_limit(2**31 - 1)


def read(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def write(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(wals: Path, glottolog: Path) -> None:
    out_w, out_g = HERE / "wals_mini", HERE / "glottolog_mini"
    for d in (out_w, out_g):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()

    # ---- WALS
    h, langs = read(wals / "languages.csv")
    langs = [r for r in langs if r["ID"] in WALS_LANGS]
    write(out_w / "languages.csv", h, langs)
    h, params = read(wals / "parameters.csv")
    params = [r for r in params if r["ID"] in WALS_PARAMS]
    write(out_w / "parameters.csv", h, params)
    h, codes = read(wals / "codes.csv")
    write(out_w / "codes.csv", h, [r for r in codes if r["Parameter_ID"] in WALS_PARAMS])
    h, values = read(wals / "values.csv")
    values = [r for r in values if r["Language_ID"] in WALS_LANGS and r["Parameter_ID"] in WALS_PARAMS]
    write(out_w / "values.csv", h, values)
    chapters = {r["Chapter_ID"] for r in params}
    h, ch = read(wals / "chapters.csv")
    write(out_w / "chapters.csv", h, [r for r in ch if r["ID"] in chapters])
    h, names = read(wals / "language_names.csv")
    write(out_w / "language_names.csv", h, [r for r in names if r["Language_ID"] in WALS_LANGS])
    keys = set()
    for r in values:
        for part in r["Source"].split(";"):
            if part:
                keys.add(part.split("[")[0].strip())
    text = (wals / "sources.bib").read_text(encoding="utf-8")
    entries = []
    for chunk in text.split("\n@")[:None]:
        chunk = chunk if chunk.startswith("@") else "@" + chunk
        key = chunk.split("{", 1)[1].split(",", 1)[0].strip() if "{" in chunk else ""
        if key in keys:
            entries.append(chunk.strip())
    (out_w / "sources.bib").write_text("\n\n".join(entries[:BIB_KEYS_LIMIT]) + "\n", encoding="utf-8")
    shutil.copy(wals / "StructureDataset-metadata.json", out_w)

    # ---- Glottolog: selected languoids, their dialects' parents and full lineages
    h, glangs = read(glottolog / "languages.csv")
    by_id = {r["ID"]: r for r in glangs}
    h_v, gvalues = read(glottolog / "values.csv")
    lineage = {r["Language_ID"]: r["Value"] for r in gvalues if r["Parameter_ID"] == "classification"}
    wanted = {r["Glottocode"] for r in langs if r["Glottocode"]} | set(EXTRA_GLOTTOCODES)
    wanted |= {"turk1311", "germ1287", "basq1248"}  # family matched by WALS genus; a subgroup; an isolate
    closure = set()
    for gc in list(wanted):
        closure.add(gc)
        lo = by_id.get(gc)
        if lo and lo["Language_ID"]:
            closure.add(lo["Language_ID"])
        for g in [gc, lo["Language_ID"] if lo else ""]:
            closure.update(x for x in lineage.get(g, "").split("/") if x)
    # every direct child of Germanic, so classification() has children to show
    closure |= {gc for gc, path in lineage.items() if path.split("/")[-1:] == ["germ1287"]}
    write(out_g / "languages.csv", h, [by_id[g] for g in sorted(closure) if g in by_id])
    keep_params = {"level", "category", "classification", "aes", "med"}
    write(
        out_g / "values.csv",
        h_v,
        [r for r in gvalues if r["Language_ID"] in closure and r["Parameter_ID"] in keep_params],
    )
    for name in ("parameters.csv", "codes.csv"):
        shutil.copy(glottolog / name, out_g)
    h, gnames = read(glottolog / "names.csv")
    write(out_g / "names.csv", h, [r for r in gnames if r["Language_ID"] in closure])
    shutil.copy(glottolog / "cldf-metadata.json", out_g)
    print(f"wals_mini: {len(langs)} languages, {len(values)} values; glottolog_mini: {len(closure)} languoids")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
