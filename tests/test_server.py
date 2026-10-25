from __future__ import annotations

import pytest
from mcp import Client

from cldf_mcp.cli import main
from cldf_mcp.server import DataManager, create_server

EXPECTED_TOOLS = {
    "list_datasets",
    "find_language",
    "search_features",
    "get_feature",
    "get_values",
    "language_profile",
    "filter_languages",
    "crosstab",
    "classification",
    "get_source",
}


@pytest.fixture
def server(data_dir, fixture_config):
    return create_server(DataManager(data_dir, fixture_config))


@pytest.mark.anyio
async def test_tools_are_listed_read_only(server):
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.description and t.annotations.read_only_hint and not t.annotations.open_world_hint
    cond = tools["filter_languages"].input_schema["$defs"]["Condition"]
    assert set(cond["properties"]) == {"feature", "values", "exclude"}


@pytest.mark.anyio
async def test_call_returns_structured_content(server):
    async with Client(server) as c:
        r = await c.call_tool(
            "filter_languages",
            {"conditions": [{"feature": "81A", "values": ["SOV"]}, {"feature": "85A", "values": ["Postpositions"]}]},
        )
    assert not r.is_error
    assert r.structured_content["matches"]["languages"] == 5


@pytest.mark.anyio
async def test_query_errors_become_tool_errors(server):
    async with Client(server) as c:
        r = await c.call_tool("get_feature", {"feature": "81A", "macroarea": "Atlantis"})
    assert r.is_error and "Unknown macroarea" in r.content[0].text


@pytest.mark.anyio
async def test_resources(server):
    async with Client(server) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        guide = await c.read_resource("cldf://guide/typology-caveats")
        ds = await c.read_resource("cldf://datasets")
    assert uris == {"cldf://guide/typology-caveats", "cldf://datasets"}
    assert "families" in guide.contents[0].text
    assert '"wals"' in ds.contents[0].text


def test_server_instructions(server):
    assert "find_language" in server.instructions and "families" in server.instructions


def test_cli_info_and_setup(data_dir, fixture_config, capsys):
    assert main(["info", "--data-dir", str(data_dir), "--config", str(fixture_config)]) == 0
    out = capsys.readouterr().out
    assert '"database_current": true' in out
    assert main(["setup", "--data-dir", str(data_dir), "--config", str(fixture_config)]) == 0
    assert '"wals"' in capsys.readouterr().out
