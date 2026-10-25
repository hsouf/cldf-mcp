"""End-to-end check against the real pinned releases (downloads ~40 MB).

Run with: pytest -m network
"""

from __future__ import annotations

import pytest

from cldf_mcp.build import build
from cldf_mcp.registry import load_registry
from cldf_mcp.store import Store

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def real_store(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    build(load_registry(), d / "cldf.sqlite", d / "raw", log=lambda _: None)
    return Store(d / "cldf.sqlite")


def test_real_release_loads(real_store):
    ds = {d["id"]: d for d in real_store.list_datasets()["datasets"]}
    assert ds["wals"]["languages_with_data"] == 2660
    assert ds["wals"]["features"] == 192
    assert ds["glottolog"]["languoids"] > 27000


def test_wals_81a_matches_published_counts(real_store):
    # Feature 81A counts in the pinned CLDF release v2020.6.
    by = {v["value"]: v["languages"] for v in real_store.get_feature("wals:81A")["values"]}
    assert by == {"SOV": 564, "SVO": 488, "VSO": 95, "VOS": 25, "OVS": 11, "OSV": 4, "No dominant order": 189}


def test_real_family_assignment_quality(real_store):
    langs = real_store.langs["wals"].values()
    fallback = [x for x in langs if x.family_basis == "wals"]
    assert len(fallback) == 7  # all but 7 of 2,660 WALS languages get a Glottolog family
    assert real_store.langs["wals"]["aze"].family_name == "Turkic"  # WALS 'Altaic', matched via genus


def test_real_resolution(real_store):
    assert real_store.resolve_language("Euskara").glottocode == "basq1248"
    assert real_store.resolve_language("Mandarin").entries["wals"][0].id == "mnd"
