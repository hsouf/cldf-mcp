# Test fixtures

Small subsets of two CC-BY-4.0 datasets, used only by the offline test suite:

- `wals_mini/`: 20 languages and 6 features from WALS Online, CLDF release v2020.6
  (Dryer & Haspelmath 2013, https://wals.info).
- `glottolog_mini/`: the 124 Glottolog 5.3 languoids needed to classify them
  (Hammarström et al. 2026, https://glottolog.org).

The languages were chosen to exercise edge cases: an isolate with dialect entries (Basque), a
3-letter code that is both a WALS code and a different language's ISO code (`bsq`), WALS entries
without Glottocodes (Albanian, Azerbaijani), a sign language in a Glottolog pseudo-family, and
members of the WALS 100/200 samples.

Regenerate from full CLDF directories with:

```bash
python tests/fixtures/make_fixtures.py PATH/TO/wals/cldf PATH/TO/glottolog/cldf
```
