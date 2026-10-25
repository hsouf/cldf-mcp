from __future__ import annotations

import pytest

from cldf_mcp.store import QueryError

# ------------------------------------------------------------------ language resolution


def test_resolve_by_name_code_and_alternate_name(store):
    assert store.resolve_language("English").glottocode == "stan1293"
    assert store.resolve_language("eng").glottocode == "stan1293"  # ISO and WALS code agree
    assert store.resolve_language("stan1295").name == "German"
    assert store.resolve_language("Euskara").glottocode == "basq1248"  # Glottolog alternate name
    assert store.resolve_language("Basque").entries["wals"][0].id == "bsq"  # main entry, not a dialect


def test_ambiguous_three_letter_code_is_not_guessed(store):
    # 'bsq' is the WALS code for Basque but the ISO 639-3 code for Bassa
    with pytest.raises(QueryError) as e:
        store.resolve_language("bsq")
    msg = str(e.value)
    assert "ambiguous" in msg and "basq1248" in msg and "nucl1418" in msg


def test_explicit_dataset_ref_pins_entry(store):
    r = store.resolve_language("wals:bso")
    assert r.glottocode == "basq1250" and [e.id for e in r.entries["wals"]] == ["bso"]
    r = store.resolve_language("wals:bsq")
    assert r.entries["wals"][0].id == "bsq" and r.name == "Basque"
    assert store.resolve_language("iso:eng").glottocode == "stan1293"


def test_language_without_glottocode(store):
    r = store.resolve_language("Albanian")
    assert r.glottocode is None and r.entries["wals"][0].id == "alb"
    cand = store.find_language("Albanian")["candidates"][0]
    assert cand["glottocode"] is None and "wals:alb" in cand["note"]


def test_unknown_language_suggests(store):
    with pytest.raises(QueryError, match="No language matches|no exact match"):
        store.resolve_language("Qwertyuiop")
    with pytest.raises(QueryError, match="Candidates"):
        store.resolve_language("Germ")  # prefix only: never auto-resolved


def test_find_language_ranks_and_reports_coverage(store):
    res = store.find_language("Basque")
    top = res["candidates"][0]
    assert top["glottocode"] == "basq1248" and top["family"] == "isolate"
    assert top["data"]["wals"][0]["ref"] == "wals:bsq"
    assert top["data_on_dialects"] == {"wals": ["wals:bso"]}
    fuzzy = store.find_language("Japanesse")["candidates"]
    assert fuzzy and fuzzy[0]["name"] == "Japanese"


# ------------------------------------------------------------------ features


def test_resolve_feature_forms(store):
    assert store.resolve_feature("81A") == ("wals", "81A")
    assert store.resolve_feature("wals:81a") == ("wals", "81A")
    assert store.resolve_feature("Order of Subject, Object and Verb") == ("wals", "81A")
    assert store.resolve_feature("glottolog:aes") == ("glottolog", "aes")
    with pytest.raises(QueryError, match="Closest matches"):
        store.resolve_feature("word order thing")
    with pytest.raises(QueryError, match="Unknown dataset"):
        store.resolve_feature("grambank:GB020")


def test_resolve_values(store):
    assert store.resolve_values("wals", "81A", ["SOV"]) == {"81A-1"}
    assert store.resolve_values("wals", "81A", ["81A-2", "3"]) == {"81A-2", "81A-3"}
    assert store.resolve_values("wals", "81A", ["no dominant"]) == {"81A-7"}  # unique partial label
    with pytest.raises(QueryError, match="Valid values: 1 = SOV"):
        store.resolve_values("wals", "81A", ["SOVV"])


def test_search_features(store):
    res = store.search_features("word order subject verb")
    assert res["features"][0]["feature"] == "wals:81A"
    assert "SOV" in res["features"][0]["values"]
    assert store.search_features("81A")["features"][0]["feature"] == "wals:81A"
    assert store.search_features("endangerment", dataset="glottolog")["features"][0]["feature"] == "glottolog:aes"
    with pytest.raises(QueryError):
        store.search_features("x", dataset="nope")


def test_get_feature_counts_families(store):
    f = store.get_feature("wals:81A")
    by = {v["value"]: v for v in f["values"]}
    assert by["SOV"]["languages"] == 6 and by["SOV"]["families"] == 5
    assert by["SVO"]["languages"] == 6 and by["SVO"]["families"] == 4
    assert f["coverage"]["languages"] == sum(v["languages"] for v in f["values"])
    assert f["chapter"]["url"] == "https://wals.info/chapter/81"
    assert f["url"] == "https://wals.info/feature/81A"
    assert f["provenance"][0]["dataset"] == "wals" and f["provenance"][0]["version"]


def test_get_feature_scopes(store):
    eu = store.get_feature("81A", macroarea="eurasia")
    assert eu["scope"] == {"macroarea": "Eurasia"}
    s100 = store.get_feature("81A", sample="wals100")
    assert s100["coverage"]["languages"] < store.get_feature("81A")["coverage"]["languages"]
    ie = store.get_feature("81A", family="Indo-European")
    assert {v["value"] for v in ie["values"] if v["languages"]} <= {"SOV", "SVO", "VSO", "No dominant order"}
    areas = store.get_feature("81A", by_macroarea=True)["by_macroarea"]
    assert "Eurasia" in areas and all(isinstance(v, dict) for v in areas.values())
    with pytest.raises(QueryError, match="Unknown macroarea"):
        store.get_feature("81A", macroarea="Atlantis")
    with pytest.raises(QueryError, match="Unknown sample"):
        store.get_feature("81A", sample="wals50")


# ------------------------------------------------------------------ values & profiles


def test_get_values_with_sources_and_errors(store):
    res = store.get_values(["Basque", "Swahili", "bsq", "Qwertyuiop"], ["81A", "wals:139A", "nonsense"])
    rows = {r["language"]["name"]: r for r in res["results"]}
    basque = rows["Basque"]["values"]
    assert basque["wals:81A"]["value"] == "SOV"
    assert basque["wals:81A"]["sources"][0] == {"id": "Lafitte-1962", "pages": "46"}
    assert basque["wals:139A"] == {"status": "no data"}
    assert rows["Basque"]["language"]["entries_used"] == {"wals": "wals:bsq"}
    assert "Lafitte-1962" in res["sources"]
    errs = {e.get("language") or e.get("feature") for e in res["errors"]}
    assert errs == {"bsq", "Qwertyuiop", "nonsense"}


def test_get_values_dialect_entry_and_glottolog_feature(store):
    res = store.get_values(["wals:bso", "English"], ["14A", "glottolog:aes"])
    rows = {r["language"]["query"]: r["values"] for r in res["results"]}
    assert rows["wals:bso"]["wals:14A"]["value"]
    assert rows["English"]["glottolog:aes"]["value"] == "not endangered"


def test_get_values_limits(store):
    with pytest.raises(QueryError):
        store.get_values([], ["81A"])
    with pytest.raises(QueryError, match="At most 50"):
        store.get_values(["English"] * 51, ["81A"])


def test_language_profile(store):
    p = store.language_profile("German")
    assert p["language"]["glottocode"] == "stan1295"
    assert p["language"]["classification"][0] == "Indo-European"
    assert p["language"]["endangerment"]
    wals = p["data"]["wals"]
    assert wals["entry"]["ref"] == "wals:ger" and wals["total_values"] == len(wals["values"])
    assert store.language_profile("German", limit=1)["data"]["wals"]["truncated"] is True
    assert "dialect_entries" in store.language_profile("Basque")
    alb = store.language_profile("Albanian")
    assert alb["language"]["ref"] == "wals:alb" and alb["data"]["wals"]["values"]


# ------------------------------------------------------------------ filter & crosstab


def test_filter_languages_reports_denominator(store):
    res = store.filter_languages(
        [{"feature": "81A", "values": ["SOV"]}, {"feature": "85A", "values": ["Postpositions"]}]
    )
    assert res["matches"]["languages"] == 5
    assert res["coded_for_all_conditions"]["languages"] == 15
    assert res["share_of_coded_languages"] == round(5 / 15, 3)
    assert {x["name"] for x in res["languages"]} >= {"Japanese", "Turkish", "Basque"}
    assert res["counted_in"] == "wals" and not res["truncated"]


def test_filter_exclude_family_and_cross_dataset(store):
    not_sov = store.filter_languages([{"feature": "81A", "values": ["SOV"], "exclude": True}])
    sov = store.filter_languages([{"feature": "81A", "values": ["SOV"]}])
    assert not_sov["matches"]["languages"] + sov["matches"]["languages"] == sov["coded_for_all_conditions"]["languages"]
    germanic = store.filter_languages(
        [{"feature": "81A", "values": ["SOV", "SVO", "No dominant order"]}], family="Germanic"
    )
    assert {x["name"] for x in germanic["languages"]} == {"English", "German"}
    safe = store.filter_languages(
        [{"feature": "81A", "values": ["SVO"]}, {"feature": "glottolog:aes", "values": ["not endangered"]}]
    )
    assert "English" in {x["name"] for x in safe["languages"]}
    assert store.filter_languages([{"feature": "81A", "values": ["SOV"]}], limit=0)["truncated"] is True


def test_crosstab_cells_add_up(store):
    ct = store.crosstab("81A", "85A")
    total = ct["coded_for_both"]["languages"]
    assert total == 15
    assert sum(c["languages"] for c in ct["cells"]) == total  # one value per language in both features
    sov_po = next(c for c in ct["cells"] if c["a"] == "SOV" and c["b"] == "Postpositions")
    assert sov_po["languages"] == 5 and sov_po["families"] == 5 and "largest_family" in sov_po
    assert any(c["languages"] == 0 for c in ct["cells"])  # zero cells are kept
    assert sum(m["languages"] for m in ct["margins_a"]) == total
    by = store.crosstab("81A", "85A", by_macroarea=True)["by_macroarea"]
    assert "Eurasia" in by
    with pytest.raises(QueryError, match="same feature"):
        store.crosstab("81A", "wals:81A")


# ------------------------------------------------------------------ classification & sources


def test_classification(store):
    c = store.classification("Germanic")
    assert c["languoid"]["glottocode"] == "germ1287"
    assert [x["name"] for x in c["lineage"]][0] == "Indo-European"
    assert {x["name"] for x in c["children"]} == {"East Germanic", "Northwest Germanic"}
    nw = next(x for x in c["children"] if x["name"] == "Northwest Germanic")
    assert nw["data_entries_below"]["wals"] == 2  # English, German
    eng = store.classification("English")
    assert eng["family"] == "Indo-European" and eng["data_entries"] == {"wals": ["wals:eng"]}
    with pytest.raises(QueryError, match="no Glottocode"):
        store.classification("Albanian")


def test_get_source(store):
    res = store.get_source(["Lafitte-1962[46]", "550905", "Nope-1900"], dataset=None)
    by = {s["id"]: s for s in res["sources"]}
    assert by["Lafitte-1962"]["pages_cited"] == "46" and by["Lafitte-1962"]["citation"]
    assert set(by["Lafitte-1962"]["fields"]) <= {
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
    }
    assert by["550905"]["url"].endswith("/550905")
    assert res["not_found"] == ["Nope-1900"]


def test_list_datasets(store):
    res = store.list_datasets()
    by = {d["id"]: d for d in res["datasets"]}
    assert by["wals"]["languages_with_data"] == 20 and by["wals"]["features"] == 6
    assert by["glottolog"]["role"] == "catalog" and by["glottolog"]["languoids"] == 124
    assert all(d["citation"] and d["license"] == "CC-BY-4.0" for d in res["datasets"])
