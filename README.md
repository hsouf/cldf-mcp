# cldf-mcp

<!-- mcp-name: io.github.soufianehajazi23/cldf-mcp -->

An [MCP](https://modelcontextprotocol.io) server that gives AI assistants grounded access to
cross-linguistic data published in the [CLDF](https://cldf.clld.org) format. Version 0.1 ships with:

- **[WALS](https://wals.info)**, the World Atlas of Language Structures: 192 typological features
  (word order, phonology, morphology…) for 2,660 languages, with sources for every datapoint;
- **[Glottolog](https://glottolog.org)**: the catalog of the world's ~27,000 languoids, their
  classification, alternate names, endangerment and description status.

Language models know WALS exists but are unreliable on specifics: which value a language has for
feature 85A, or how many languages combine SOV order with prepositions. This server returns the
actual data, with the release it came from and the reference behind it.

## What makes it different from a lookup table

- **Family-aware counts.** Related languages inherit features, so "400 languages do X" can mean
  "three big families do X". Every count comes with the number of distinct **families** (from
  Glottolog) and WALS **genera**, plus the share of the largest family, so the model can tell a
  cross-linguistic tendency from the signature of one lineage. WALS's balanced 100- and
  200-language samples are available as filters.
- **Provenance everywhere.** Each answer names the dataset, release and citation; datapoints carry
  their bibliographic sources and page numbers.
- **No silent guessing.** `bsq` is WALS's code for Basque and the ISO code for Bassa, so the server
  asks instead of picking one. "No data" is never reported as "absent".
- **Pinned, verified data.** Datasets are fetched from tagged releases and checked against SHA-256
  checksums before loading.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip).

**Claude Code**

```bash
claude mcp add cldf -- uvx cldf-mcp
```

**Claude Desktop** and other clients that use an `mcpServers` config:

```json
{
  "mcpServers": {
    "cldf": {
      "command": "uvx",
      "args": ["cldf-mcp"]
    }
  }
}
```

On first use the server downloads the pinned releases (about 40 MB) and builds a local SQLite
database (about 35 MB) in a few seconds. To do this ahead of time:

```bash
uvx cldf-mcp setup
```

Then ask things like:

- *Which languages in WALS are SOV but use prepositions? Are they spread across families?*
- *Is "VSO implies prepositions" a real tendency? Check it within the WALS 200-language sample.*
- *Give me Basque's word-order features, with sources.*
- *How does WALS cover the Austronesian family? Which branches have the most data?*

## Tools

| Tool | What it does |
|---|---|
| `list_datasets` | Loaded datasets, release versions, coverage, licenses and citations |
| `find_language` | Names (including alternate names and misspellings), ISO 639-3 codes, Glottocodes or dataset codes → ranked candidates with family, macroarea and which entries have data |
| `search_features` | Keyword search over feature names, chapters and value labels → feature IDs and possible values |
| `get_feature` | Definition, chapter, authors and value distribution, counted by languages, families and genera; optional filters by macroarea, family/subgroup or WALS sample |
| `get_values` | Language × feature grid with values, comments, sources and full citations; unresolvable names are reported with candidates instead of failing the call |
| `language_profile` | Everything about one language: Glottolog identity, lineage, endangerment, description status and all feature values |
| `filter_languages` | Languages matching up to 8 conditions (AND, with `exclude`), across datasets, with the denominator (languages coded for all conditions) and family counts |
| `crosstab` | Two features cross-tabulated: languages, families, genera and largest-family share per cell, zero cells kept, optional macroarea breakdown |
| `classification` | Glottolog lineage, children (with how many dataset entries fall under each), sisters |
| `get_source` | Full bibliographic references for the source IDs cited in datapoints |

Resources:

- `cldf://guide/typology-caveats`: how to use counts responsibly (sampling bias, areal effects,
  coverage, citation). The server's instructions point the model to it.
- `cldf://datasets`: versions, licenses and citations of the loaded data.

All tools are read-only and work offline once the data is prepared.

## Configuration

| Setting | Default |
|---|---|
| `--data-dir` / `CLDF_MCP_DATA_DIR` | `~/.cache/cldf-mcp` (Linux), `~/Library/Caches/cldf-mcp` (macOS), `%LOCALAPPDATA%\cldf-mcp` (Windows) |
| `--config` / `CLDF_MCP_CONFIG` | none: extra TOML to add or override datasets (experimental, see below) |
| `--transport` | `stdio`; `streamable-http` with `--host` / `--port` for remote use |

Other commands: `cldf-mcp info` (where data lives, whether it is current) and
`cldf-mcp setup --force` (rebuild).

### Adding a dataset (experimental)

Any CLDF StructureDataset whose languages carry Glottocodes can be loaded alongside WALS. Feature
tools, family counting and cross-dataset filters work on it automatically:

```toml
# my-datasets.toml
[datasets.mydata]
title = "My typological survey"
role = "features"
version = "v1.0"
base_url = "https://raw.githubusercontent.com/me/mydata/v1.0/cldf/"   # or: local_path = "/path/to/cldf"
metadata = "StructureDataset-metadata.json"
license = "CC-BY-4.0"
citation = "Me. 2026. My typological survey."

[datasets.mydata.files]   # every file the loader needs, with optional sha256 checksums
"StructureDataset-metadata.json" = ""
"languages.csv" = ""
"parameters.csv" = ""
"codes.csv" = ""
"values.csv" = ""
```

```bash
cldf-mcp setup --config my-datasets.toml
```

## Data, licenses and citation

The server code is MIT-licensed. The data is not part of this package: it is downloaded from the
original releases, which are licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
If you publish results obtained with this server, cite the datasets (see `DATA_LICENSES.md`):

> Dryer, Matthew S. & Haspelmath, Martin (eds.) 2013. *The World Atlas of Language Structures
> Online.* Leipzig: Max Planck Institute for Evolutionary Anthropology. https://wals.info
> (CLDF release v2020.6)

> Hammarström, Harald & Forkel, Robert & Haspelmath, Martin & Bank, Sebastian. 2026. *Glottolog 5.3.*
> Leipzig: Max Planck Institute for Evolutionary Anthropology. https://glottolog.org

This is an independent project and is not affiliated with the WALS, Glottolog or CLDF teams.

## Limitations

- WALS reflects the literature up to about 2013, and most features are coded for a few hundred
  languages. Percentages are of coded languages, not of the world's languages.
- Family counts follow Glottolog, which does not recognise proposed macro-families (e.g. Altaic).
  Of the 17 WALS languages without a Glottocode, 10 are matched to a Glottolog family through
  WALS's family or genus name; the other 7 keep WALS's own label (e.g. "Niger-Congo").
- Glottolog's full bibliography is not bundled; its references are returned as links.

## Roadmap

- **0.2**: [Grambank](https://grambank.clld.org), the first dataset that overlaps with WALS, with
  side-by-side comparison of related features.
- **0.3**: [PHOIBLE](https://phoible.org) sound inventories and Lexibank wordlists
  (`get_inventory`, `get_forms`).

## Development

```bash
git clone https://github.com/soufianehajazi23/cldf-mcp && cd cldf-mcp
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                # offline tests on small fixtures (tests/fixtures)
pytest -m network     # downloads the real pinned releases and checks known WALS counts
ruff check src tests && ruff format --check src tests
```

See `RELEASING.md` for publishing to PyPI and the MCP Registry.
