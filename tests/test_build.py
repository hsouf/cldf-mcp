from __future__ import annotations

import dataclasses
import shutil
import sqlite3
from pathlib import Path

import pytest

from cldf_mcp import fetch
from cldf_mcp.build import build, is_current
from cldf_mcp.registry import load_registry

from .conftest import FIXTURES


def test_built_counts(data_dir):
    c = sqlite3.connect(data_dir / "cldf.sqlite")
    q = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    assert q("SELECT count(*) FROM languages WHERE dataset='wals'") == 20
    assert q("SELECT count(*) FROM parameters WHERE dataset='wals'") == 6
    assert q("SELECT count(*) FROM parameters WHERE dataset='glottolog'") == 2  # aes, med
    assert q("SELECT count(*) FROM languoids") == 124
    assert q("SELECT count(*) FROM sources WHERE dataset='wals'") > 10
    # Glottolog's stale metadata title is overridden by the pinned registry
    assert q("SELECT citation FROM datasets WHERE id='glottolog'").startswith(
        "Hammarström, Harald & Forkel, Robert & Haspelmath, Martin & Bank, Sebastian. 2026. Glottolog 5.3."
    )


@pytest.mark.parametrize(
    "wals_id, unit, basis",
    [
        ("eng", "indo1319", "glottolog"),
        ("bsq", "basq1248", "glottolog"),  # isolate is its own family
        ("bso", "basq1248", "glottolog"),  # a dialect of an isolate counts with it
        ("asl", "amer1248", "glottolog"),  # pseudo-family member counted individually
        ("alb", "indo1319", "dataset family/genus name matched to glottolog"),  # no Glottocode in WALS
        ("aze", "turk1311", "dataset family/genus name matched to glottolog"),  # WALS 'Altaic' -> genus 'Turkic'
    ],
)
def test_family_units(store, wals_id, unit, basis):
    lang = store.langs["wals"][wals_id]
    assert (lang.family_unit, lang.family_basis) == (unit, basis)


def test_samples_flags(store):
    assert store.langs["wals"]["eng"].samples == "wals100,wals200"
    assert store.langs["wals"]["iri"].samples == "wals200"
    assert store.langs["wals"]["wel"].samples is None


def test_is_current(data_dir, fixture_config, tmp_path):
    specs = load_registry(fixture_config)
    assert is_current(data_dir / "cldf.sqlite", specs)
    changed = {**specs, "wals": dataclasses.replace(specs["wals"], version="v0")}
    assert not is_current(data_dir / "cldf.sqlite", changed)
    assert not is_current(tmp_path / "missing.sqlite", specs)


def _remote_config(tmp_path: Path, checksums: dict[str, str]) -> Path:
    files = "\n".join(f'"{k}" = "{v}"' for k, v in checksums.items())
    cfg = tmp_path / "remote.toml"
    cfg.write_text(
        f"""
[datasets.glottolog]
enabled = false

[datasets.wals]
base_url = "https://example.invalid/wals/"
version = "test"
names_table = ""
bibliography = ""

[datasets.wals.files]
{files}
"""
    )
    return cfg


def test_download_verifies_checksums(tmp_path, monkeypatch):
    src = FIXTURES / "wals_mini"
    names = ["StructureDataset-metadata.json", "languages.csv", "parameters.csv", "codes.csv", "values.csv"]
    good = {n: fetch.sha256_of(src / n) for n in names}
    calls = []

    def fake_download(url, dest, timeout=120.0):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / url.rsplit("/", 1)[1], dest)

    monkeypatch.setattr(fetch, "_download", fake_download)
    specs = load_registry(_remote_config(tmp_path, good))
    db = build(specs, tmp_path / "db.sqlite", tmp_path / "raw", log=lambda _: None)
    assert sqlite3.connect(db).execute("SELECT count(*) FROM languages").fetchone()[0] == 20
    assert len(calls) == len(names)
    # A second build reuses verified files instead of downloading again.
    build(specs, tmp_path / "db.sqlite", tmp_path / "raw", log=lambda _: None)
    assert len(calls) == len(names)

    bad = {**good, "values.csv": "0" * 64}
    sub = tmp_path / "bad"
    sub.mkdir()
    with pytest.raises(fetch.ChecksumError):
        build(load_registry(_remote_config(sub, bad)), tmp_path / "db2.sqlite", tmp_path / "raw2", log=lambda _: None)
    assert not (tmp_path / "raw2" / "wals" / "test" / "values.csv").exists()  # bad file is discarded


def test_registry_validation(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[datasets.foo]\ntitle = "Foo"\nrole = "features"\nversion = "1"\n')
    with pytest.raises(ValueError, match="missing required keys"):
        load_registry(cfg)
    cfg.write_text(
        '[datasets.foo]\ntitle = "Foo"\nrole = "catalog"\nversion = "1"\nmetadata = "m.json"\nlocal_path = "."\n'
    )
    with pytest.raises(ValueError, match="only one catalog"):
        load_registry(cfg)


def test_builtin_registry_is_pinned():
    specs = load_registry()
    assert set(specs) == {"wals", "glottolog"}
    for spec in specs.values():
        assert spec.version in spec.base_url, "base_url must point at the pinned tag"
        assert all(v and len(v) == 64 for v in spec.files.values()), "every file needs a sha256"
