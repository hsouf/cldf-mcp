# Data licenses and attribution

`cldf-mcp` itself is MIT-licensed and does not redistribute the datasets below. They are downloaded
at setup time from their original, versioned releases. Both are licensed under the
[Creative Commons Attribution 4.0 International License (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/),
which requires attribution when you share results derived from them.

## WALS Online (CLDF release v2020.6)

- Source: https://github.com/cldf-datasets/wals (tag `v2020.6`), https://wals.info
- License: CC-BY-4.0
- Cite as: Dryer, Matthew S. & Haspelmath, Martin (eds.) 2013. *The World Atlas of Language
  Structures Online.* Leipzig: Max Planck Institute for Evolutionary Anthropology.
  (Available online at https://wals.info)
- When citing a particular feature, also cite its chapter; `get_feature` returns the chapter's
  citation and URL.

## Glottolog 5.3 (CLDF)

- Source: https://github.com/glottolog/glottolog-cldf (tag `v5.3`), https://glottolog.org
- License: CC-BY-4.0
- Cite as: Hammarström, Harald & Forkel, Robert & Haspelmath, Martin & Bank, Sebastian. 2026.
  *Glottolog 5.3.* Leipzig: Max Planck Institute for Evolutionary Anthropology.
  (Available online at https://glottolog.org)

## Test fixtures

`tests/fixtures/wals_mini` and `tests/fixtures/glottolog_mini` are small subsets of the two
releases above, used only for automated tests, and remain under CC-BY-4.0 with the attributions
above. They are regenerated with `tests/fixtures/make_fixtures.py`.
