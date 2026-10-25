# Using cross-linguistic data responsibly

Read this before turning counts from these tools into claims about "most languages",
"universals" or "tendencies".

## 1. Languages are not independent data points

Related languages inherit features from a common ancestor. A count of 400 SVO
languages may reflect a few large families (Atlantic-Congo, Austronesian,
Indo-European) more than any general preference. That is why every count from
`get_feature`, `filter_languages` and `crosstab` also reports:

- **families**: distinct Glottolog top-level families (isolates count as their own
  family; members of pseudo-families such as sign languages, pidgins or
  unclassifiable languages are counted individually);
- **genera** (WALS only): WALS's mid-level groupings, comparable in depth to
  Germanic or Romance, the unit WALS authors often use when counting;
- **largest_family**: how much of a cell comes from a single lineage.

Rules of thumb:

- If the language count and the family count point in different directions, report
  both and lead with families.
- A cell where `largest_family.share` is above ~0.3 is heavily shaped by one lineage.
- A pattern attested in many languages but few families is a fact about those
  families, not about human language.

## 2. Areas matter too

Neighbouring languages converge through contact regardless of family. Before calling
a pattern universal, check it with `by_macroarea=true`: a tendency that holds in every
macroarea is far stronger than one driven by Eurasia or Africa alone.

## 3. Coverage is uneven

- WALS has data on about 2,660 languages, but most features are coded for only a
  few hundred. Always read `coverage` / `coded_for_all_conditions`: percentages
  are of the coded languages, not of the world's ~7,000 languages.
- The WALS 100- and 200-language samples were designed to be more balanced; the
  rest of the data is a convenience sample, skewed towards well-described languages.
  Pass `sample="wals100"` or `sample="wals200"` to `get_feature`, `filter_languages`
  or `crosstab` to restrict counts to them.
- "No data" and "not in dataset" mean the language was not coded. They never mean
  the feature is absent.
- A zero cell in a cross-tabulation means "unattested in this sample", not
  "impossible".

## 4. Datasets define features differently

Each dataset has its own definitions and coding decisions. A WALS feature and a
similarly named feature in another dataset are not interchangeable; report each
with its own ID and do not merge them silently. Within WALS, check the chapter text
(`get_feature` gives the URL) for how a value such as "No dominant order" is defined.

## 5. Identity and classification

- Languages are joined across datasets by Glottocode. Several WALS entries can share
  one Glottocode (e.g. Basque and Basque dialects); tools say which entry they used
  and list the others.
- Family assignments follow Glottolog, which is conservative: it does not recognise
  proposed macro-families such as Altaic or Nostratic. A few WALS languages without
  a Glottocode fall back to WALS's own family label; `get_feature` and the other
  tools mark these.
- WALS reflects the literature up to about 2013. Descriptions, and sometimes
  analyses, have changed since.

## 6. Cite what you use

Every answer includes `provenance` (dataset, release, citation). When reporting a
specific datapoint, cite its source(s) from `get_values` (`get_source` gives the
full reference) and the WALS chapter for the feature.
