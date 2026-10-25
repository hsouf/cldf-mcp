from cldf_mcp import bibtex
from cldf_mcp.cldf import split_sources
from cldf_mcp.store import areas, norm, stem, tokens


def test_split_sources_pages_and_lists():
    assert split_sources("Nekitel-1985[94]") == [{"id": "Nekitel-1985", "pages": "94"}]
    assert split_sources("Gecadze-1979;Hewitt-1979") == [{"id": "Gecadze-1979"}, {"id": "Hewitt-1979"}]
    # semicolons and commas inside page brackets must not split the reference
    assert split_sources("Foo-1990[1; 3, 5];Bar-2000") == [{"id": "Foo-1990", "pages": "1; 3, 5"}, {"id": "Bar-2000"}]
    assert split_sources("") == []


def test_bibtex_parse_and_format():
    text = """
@comment{ignored}
@book{Abbott-2000,
    author = {Abbott, Clifford},
    address = {M{\\"u}nchen},
    publisher = {Lincom Europa},
    title = {{Oneida}},
    year = {2000}
}
@article{Doe-1999,
    author = "Doe, Jane and Roe, Richard",
    title = {On {SOV} order},
    journal = {Linguistic Typology},
    volume = 3,
    number = {2},
    pages = {1--20},
    year = {1999}
}
"""
    entries = list(bibtex.parse(text))
    assert [k for _, k, _ in entries] == ["Abbott-2000", "Doe-1999"]
    _, _, f = entries[0]
    assert f["title"] == "Oneida" and f["address"] == "Munchen"
    assert bibtex.format_citation("book", f) == "Abbott, Clifford. 2000. Oneida. Munchen: Lincom Europa."
    etype, _, f2 = entries[1]
    assert f2["volume"] == "3"
    assert bibtex.format_citation(etype, f2) == (
        "Doe, Jane and Roe, Richard. 1999. On SOV order. Linguistic Typology 3(2): 1--20."
    )


def test_normalisation_and_stemming():
    assert norm("!Xóõ") == "xoo"
    assert norm("  Arapesh (Abu) ") == "arapesh abu"
    assert stem("negation") == stem("negative")
    assert stem("plurality") == stem("plural")
    assert stem("class") == "class"
    assert tokens("Order of Subjects") == ["order", "of", "subject"]


def test_areas():
    assert areas("") is None
    assert areas("Eurasia") == "Eurasia"
    assert areas("Africa;Eurasia") == ["Africa", "Eurasia"]
