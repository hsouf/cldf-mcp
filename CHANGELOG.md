# Changelog

## 0.1.0 (unreleased)

First public release.

- Datasets: WALS Online (CLDF v2020.6) and Glottolog 5.3, fetched from pinned tags and verified
  against SHA-256 checksums, loaded into a local SQLite database.
- Tools: `list_datasets`, `find_language`, `search_features`, `get_feature`, `get_values`,
  `language_profile`, `filter_languages`, `crosstab`, `classification`, `get_source`.
- Family-aware counting: every count reports Glottolog families and WALS genera alongside
  languages; pseudo-families (sign languages, pidgins, unclassifiable) are counted per language.
- Filters by macroarea, Glottolog family or subgroup, and the WALS 100/200-language samples.
- Resources: `cldf://guide/typology-caveats` and `cldf://datasets`.
- CLI: `cldf-mcp` (stdio server), `serve --transport streamable-http`, `setup`, `info`.
- Experimental: extra CLDF StructureDatasets via a TOML config.
