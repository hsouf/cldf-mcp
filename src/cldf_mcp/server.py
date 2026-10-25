"""The MCP server: tools, resources and data lifecycle."""

from __future__ import annotations

import json
import logging
import sys
import threading
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .build import build, is_current
from .paths import db_path, default_data_dir, raw_dir
from .registry import load_registry
from .store import QueryError, Store

log = logging.getLogger("cldf_mcp")

INSTRUCTIONS = """\
Cross-linguistic data from CLDF releases: WALS (World Atlas of Language Structures; ~2,660 languages, 192
typological features) joined through Glottolog (language catalog, classification, endangerment).

Workflow:
1. Resolve names first. Use find_language for languages (returns Glottocodes such as 'basq1248') and
   search_features for features (returns IDs such as 'wals:81A'). Do not guess WALS codes.
2. Answer with get_values (specific datapoints, with sources), language_profile (everything about one
   language), get_feature (distribution of one feature), filter_languages (which languages have X and Y)
   or crosstab (how two features co-occur). classification gives Glottolog lineage.
3. Report family counts alongside language counts. Related languages share features, so 'families'
   (and WALS 'genera') are the fairer measure of how widespread a pattern is. Read resource
   cldf://guide/typology-caveats before making generalisations.
4. "no data" means not coded, never "absent". Cite datasets from 'provenance' and sources from get_values.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class Condition(BaseModel):
    feature: str = Field(description="Feature ID, e.g. 'wals:81A' (or '81A'), or 'glottolog:aes' for endangerment.")
    values: list[str] = Field(
        description="Values to match: labels ('SOV'), code IDs ('81A-1') or value numbers ('1'). A language matches "
        "if it has any of them.",
        min_length=1,
    )
    exclude: bool = Field(
        False, description="If true, match languages coded for this feature that have none of these values."
    )


class DataManager:
    """Builds the database on first use and holds the Store."""

    def __init__(self, data_dir: Path | None = None, config: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.config = config
        self._store: Store | None = None
        self._lock = threading.Lock()
        self._error: BaseException | None = None

    @property
    def db(self) -> Path:
        return db_path(self.data_dir)

    def prepare(self, force: bool = False, log_fn=None) -> Path:
        log_fn = log_fn or (lambda m: print(m, file=sys.stderr, flush=True))
        specs = load_registry(self.config)
        if force or not is_current(self.db, specs):
            log_fn(f"cldf-mcp: preparing data in {self.data_dir} (one-time download, ~40 MB)")
            build(specs, self.db, raw_dir(self.data_dir), log=log_fn)
        return self.db

    def store(self) -> Store:
        if self._store is not None:
            return self._store
        with self._lock:
            if self._store is None:
                try:
                    self._store = Store(self.prepare())
                    self._error = None
                except Exception as e:  # surfaced to the model as a tool error
                    self._error = e
                    raise ToolError(
                        f"The dataset could not be prepared: {e}. Run `cldf-mcp setup` in a terminal to see details."
                    ) from e
        return self._store

    def warm_up(self) -> None:
        threading.Thread(target=self._warm, name="cldf-mcp-warmup", daemon=True).start()

    def _warm(self) -> None:
        try:
            self.store()
        except Exception as e:
            log.warning("warm-up failed: %s", e)


def create_server(manager: DataManager | None = None) -> MCPServer:
    manager = manager or DataManager()
    mcp = MCPServer(
        name="cldf",
        title="CLDF linguistic data (WALS + Glottolog)",
        description="Typological and genealogical data on the world's languages, with provenance.",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://github.com/hsouf/cldf-mcp",
    )

    def call(fn, *args, **kwargs) -> dict[str, Any]:
        store = manager.store()
        try:
            return fn(store, *args, **kwargs)
        except QueryError as e:
            raise ToolError(str(e)) from None

    @mcp.tool(annotations=READ_ONLY)
    def list_datasets() -> dict[str, Any]:
        """List the loaded datasets with their release versions, coverage, licenses and how to cite them."""
        return call(Store.list_datasets)

    @mcp.tool(annotations=READ_ONLY)
    def find_language(
        query: Annotated[
            str,
            Field(
                description="Language name (any spelling or alternate name), ISO 639-3 code, "
                "Glottocode, or dataset code like 'wals:bsq'."
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=25)] = 8,
    ) -> dict[str, Any]:
        """Find languages by name or code. Returns Glottocodes, family, macroarea and which dataset entries have data.

        Use this before other tools when a name might be ambiguous (e.g. 'Arabic' is a family; WALS has
        Arabic varieties). Candidates are ranked: exact codes, exact names, alternate names, then partial matches.
        """
        return call(Store.find_language, query, limit)

    @mcp.tool(annotations=READ_ONLY)
    def search_features(
        query: Annotated[
            str,
            Field(
                description="Keywords such as 'word order', 'negation', 'tone', 'plural', or a feature ID like '81A'."
            ),
        ],
        dataset: Annotated[str | None, Field(description="Restrict to one dataset, e.g. 'wals'.")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 15,
    ) -> dict[str, Any]:
        """Search typological features by keyword. Returns feature IDs, names, possible values and how many
        languages are coded for each."""
        return call(Store.search_features, query, dataset, limit)

    @mcp.tool(annotations=READ_ONLY)
    def get_feature(
        feature: Annotated[str, Field(description="Feature ID, e.g. 'wals:81A'.")],
        by_macroarea: Annotated[bool, Field(description="Also break counts down by macroarea.")] = False,
        macroarea: Annotated[
            str | None,
            Field(
                description="Restrict to one macroarea: Africa, Australia, "
                "Eurasia, North America, Papunesia, South America."
            ),
        ] = None,
        family: Annotated[
            str | None,
            Field(description="Restrict to a Glottolog family or subgroup ('Austronesian', 'Germanic') or Glottocode."),
        ] = None,
        sample: Annotated[
            str | None, Field(description="Restrict to a balanced sample: 'wals100' or 'wals200'.")
        ] = None,
    ) -> dict[str, Any]:
        """Describe one feature: definition, chapter and authors, and how its values are distributed, counted by
        languages, families and genera."""
        return call(Store.get_feature, feature, by_macroarea, macroarea, family, sample)

    @mcp.tool(annotations=READ_ONLY)
    def get_values(
        languages: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=50,
                description="Languages: names, Glottocodes, ISO codes or dataset refs ('wals:bsq').",
            ),
        ],
        features: Annotated[
            list[str],
            Field(min_length=1, max_length=50, description="Feature IDs, e.g. ['wals:81A', 'wals:85A']."),
        ],
    ) -> dict[str, Any]:
        """Look up specific datapoints: the value of each feature for each language, with sources, comments and
        full citations. Unresolvable names are reported in 'errors' with candidates instead of failing the call.

        When several dataset entries share a Glottocode (e.g. Basque and Basque dialects), the main entry is used
        and the others are listed; pass a dataset ref like 'wals:bso' to pick a specific one.
        """
        return call(Store.get_values, languages, features)

    @mcp.tool(annotations=READ_ONLY)
    def language_profile(
        language: Annotated[str, Field(description="Name, Glottocode, ISO 639-3 code or dataset ref.")],
        dataset: Annotated[str | None, Field(description="Only show this dataset's values.")] = None,
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum values per dataset.")] = 120,
    ) -> dict[str, Any]:
        """Everything known about one language: Glottolog identity, classification, endangerment and description
        status, plus all its feature values per dataset."""
        return call(Store.language_profile, language, dataset, limit)

    @mcp.tool(annotations=READ_ONLY)
    def filter_languages(
        conditions: Annotated[
            list[Condition],
            Field(
                min_length=1,
                max_length=8,
                description="All conditions must hold (AND). Combine features across datasets freely.",
            ),
        ],
        macroarea: Annotated[str | None, Field(description="Restrict to one macroarea.")] = None,
        family: Annotated[
            str | None, Field(description="Restrict to a Glottolog family or subgroup, or Glottocode.")
        ] = None,
        sample: Annotated[str | None, Field(description="Restrict to 'wals100' or 'wals200'.")] = None,
        limit: Annotated[
            int, Field(ge=0, le=300, description="Maximum languages listed (counts are always complete).")
        ] = 50,
    ) -> dict[str, Any]:
        """Find languages matching feature conditions, e.g. SOV order AND postpositions.

        Returns match counts against the number of languages coded for all conditions (the denominator), with
        family and genus counts and the families contributing most matches, plus the matching languages.
        """
        return call(Store.filter_languages, [c.model_dump() for c in conditions], macroarea, family, sample, limit)

    @mcp.tool(annotations=READ_ONLY)
    def crosstab(
        feature_a: Annotated[str, Field(description="First feature ID, e.g. 'wals:81A'.")],
        feature_b: Annotated[str, Field(description="Second feature ID, e.g. 'wals:85A'.")],
        macroarea: Annotated[str | None, Field(description="Restrict to one macroarea.")] = None,
        family: Annotated[str | None, Field(description="Restrict to a Glottolog family or subgroup.")] = None,
        sample: Annotated[str | None, Field(description="Restrict to 'wals100' or 'wals200'.")] = None,
        by_macroarea: Annotated[bool, Field(description="Also give each cell per macroarea.")] = False,
    ) -> dict[str, Any]:
        """Cross-tabulate two features over the languages coded for both. Each cell gives languages, families and
        genera attesting the combination and the share of its largest family, so you can tell a cross-linguistic
        tendency from the signature of one big family. Use this to check implicational universals."""
        return call(Store.crosstab, feature_a, feature_b, macroarea, family, sample, by_macroarea)

    @mcp.tool(annotations=READ_ONLY)
    def classification(
        language: Annotated[str, Field(description="Language, family or subgroup: name or Glottocode.")],
        max_children: Annotated[int, Field(ge=0, le=200)] = 40,
    ) -> dict[str, Any]:
        """Glottolog classification of a language or group: lineage, children (with how many dataset entries
        fall under each), sisters, and the number of languages below."""
        return call(Store.classification, language, max_children)

    @mcp.tool(annotations=READ_ONLY)
    def get_source(
        source_ids: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=50,
                description="Source IDs as returned "
                "by get_values, e.g. ['Nekitel-1985'] (page suffixes like "
                "'[94]' are accepted).",
            ),
        ],
        dataset: Annotated[str | None, Field(description="Dataset the IDs come from, e.g. 'wals'.")] = None,
    ) -> dict[str, Any]:
        """Full bibliographic references for source IDs cited in datapoints."""
        return call(Store.get_source, source_ids, dataset)

    @mcp.resource(
        "cldf://guide/typology-caveats",
        name="typology-caveats",
        title="Using cross-linguistic data responsibly",
        description="Sampling bias, family counts, coverage and citation: read before generalising from counts.",
        mime_type="text/markdown",
    )
    def typology_caveats() -> str:
        return resources.files("cldf_mcp").joinpath("guide.md").read_text(encoding="utf-8")

    @mcp.resource(
        "cldf://datasets",
        name="datasets",
        title="Loaded datasets",
        description="Versions, licenses and citations of the loaded datasets.",
        mime_type="application/json",
    )
    def datasets_resource() -> str:
        return json.dumps(manager.store().list_datasets(), ensure_ascii=False, indent=2)

    mcp.data_manager = manager  # type: ignore[attr-defined]
    return mcp
