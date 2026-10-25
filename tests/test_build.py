from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from cldf_mcp import build as build_module
from cldf_mcp import fetch
from cldf_mcp.build import DatabaseInUseError, build, is_current, readonly_uri
from cldf_mcp.registry import load_registry

from .conftest import FIXTURES


def count(db: Path, sql: str):
    # Always close connections: on Windows an open file cannot be replaced or deleted.
    with contextlib.closing(sqlite3.connect(db)) as c:
        return c.execute(sql).fetchone()[0]


def test_built_counts(data_dir):
    db = data_dir / "cldf.sqlite"
    q = lambda sql: count(db, sql)  # noqa: E731
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
    assert count(db, "SELECT count(*) FROM languages") == 20
    assert len(calls) == len(names)
    # A second build reuses verified files instead of downloading again, and replaces the database.
    build(specs, tmp_path / "db.sqlite", tmp_path / "raw", log=lambda _: None)
    assert len(calls) == len(names)
    assert count(db, "SELECT count(*) FROM languages") == 20
    assert not list(tmp_path.glob(".build-*"))  # no leftover temporary databases

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


def test_failed_build_leaves_no_temp_file(tmp_path, fixture_config, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("loader failed")

    monkeypatch.setattr(build_module.Builder, "load_features", boom)
    with pytest.raises(RuntimeError, match="loader failed"):
        build(load_registry(fixture_config), tmp_path / "db.sqlite", tmp_path / "raw", log=lambda _: None)
    assert not list(tmp_path.glob(".build-*")) and not (tmp_path / "db.sqlite").exists()


def test_install_explains_locked_database_on_windows(tmp_path, monkeypatch):
    def locked(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(build_module.os, "replace", locked)
    monkeypatch.setattr(build_module.sys, "platform", "win32")
    with pytest.raises(DatabaseInUseError, match="running cldf-mcp server"):
        build_module._install(tmp_path / "new.sqlite", tmp_path / "db.sqlite", attempts=2, delay=0)
    monkeypatch.setattr(build_module.sys, "platform", "linux")
    with pytest.raises(PermissionError):
        build_module._install(tmp_path / "new.sqlite", tmp_path / "db.sqlite", attempts=2, delay=0)


def test_readonly_uri_handles_unusual_paths(tmp_path):
    # Characters that are special in URIs; '?' is not allowed in Windows file names.
    odd = tmp_path / ("my data #1 %20" if sys.platform == "win32" else "my data #1 %20 ?x")
    odd.mkdir()
    db = odd / "cldf.sqlite"
    with contextlib.closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (42)")
        c.commit()
    with contextlib.closing(sqlite3.connect(readonly_uri(db), uri=True)) as c:
        assert c.execute("SELECT x FROM t").fetchone()[0] == 42
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO t VALUES (1)")


@pytest.mark.skipif(sys.platform != "win32", reason="file locking semantics are Windows-specific")
def test_rebuild_while_server_has_database_open_is_explained(data_dir, fixture_config, tmp_path):
    db = tmp_path / "db.sqlite"
    shutil.copy(data_dir / "cldf.sqlite", db)
    with contextlib.closing(sqlite3.connect(readonly_uri(db), uri=True)):
        with pytest.raises(DatabaseInUseError):
            build(load_registry(fixture_config), db, tmp_path / "raw", log=lambda _: None)
    assert os.path.exists(db)
