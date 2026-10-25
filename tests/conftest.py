from __future__ import annotations

from pathlib import Path

import pytest

from cldf_mcp.build import build
from cldf_mcp.registry import load_registry
from cldf_mcp.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def write_fixture_config(path: Path) -> Path:
    path.write_text(
        f"""
[datasets.wals]
local_path = "{(FIXTURES / "wals_mini").as_posix()}"
files = {{}}

[datasets.glottolog]
local_path = "{(FIXTURES / "glottolog_mini").as_posix()}"
files = {{}}
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="session")
def fixture_config(tmp_path_factory) -> Path:
    return write_fixture_config(tmp_path_factory.mktemp("config") / "fixtures.toml")


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory, fixture_config) -> Path:
    d = tmp_path_factory.mktemp("data")
    build(load_registry(fixture_config), d / "cldf.sqlite", d / "raw", log=lambda _: None)
    return d


@pytest.fixture(scope="session")
def store(data_dir) -> Store:
    return Store(data_dir / "cldf.sqlite")


@pytest.fixture
def anyio_backend():
    return "asyncio"
