# PD Knowledge Pixels — nanopublication demonstrator (sciai-lab)

**50 signed nanopublications from a citation-backed Parkinson's-disease
knowledge graph** — a demonstrator for a possible common project with the
nanopublication community (contact: Johannes Keizer,
[ORCID 0009-0008-6409-2596](https://orcid.org/0009-0008-6409-2596)).

sciai-lab runs a PD literature analysis platform whose knowledge graph holds
**~1.1 million assertion rows**. Every assertion is **citation-backed by
construction**: extracted by an LLM pipeline from a specific PubMed paper, it
carries the PMID, the verbatim context sentence, an extraction confidence, an
effect direction, the extraction schema version, and a timestamp. Nothing is
combinatorially generated.

This demonstrator turns 50 real assertions into signed nanopublications
(TriG), drawn from the pilot slice we propose to start from: the **GLP-1 ×
Parkinson's 207-paper corpus** — the same corpus for which we computed
per-paper **evidence-state labels** (supported / contested / single-source /
unlabeled) from cross-paper agreement with author-overlap independence
exclusions (labels v2, 2026-09-21).

## The trusty URIs resolve

Every nanopublication in `trig/` is served at its own trusty URI:

- index of the demo set: <https://pd.sciai.eu/np/>
- example landing page: <https://pd.sciai.eu/np/2003458/>
- example signed TriG at its trusty URI:
  <https://pd.sciai.eu/np/2003458/RAUZBxcHu0-ewuBjBZ4E1KpCUccuPHFEYktaeCrFgqiVk>

## Representative examples (per evidence-state label)

We model **one nanopublication type** (assertion + provenance + pubinfo);
what differs per paper is the evidence-state label carried in the provenance
graph. One representative example per label:

| Label | Nanopub | Claim | PMID |
|---|---|---|---|
| contested | [np_2003458](https://pd.sciai.eu/np/2003458/) | Exendin-4 activates GLP-1 receptor | [33723752](https://pubmed.ncbi.nlm.nih.gov/33723752/) |
| supported | [np_2500736](https://pd.sciai.eu/np/2500736/) | Exendin-4 binds_to GLP-1 receptor | [17803225](https://pubmed.ncbi.nlm.nih.gov/17803225/) |
| single-source | [np_2500676](https://pd.sciai.eu/np/2500676/) | GLP-1 analogs regulates Inflammation | [24529526](https://pubmed.ncbi.nlm.nih.gov/24529526/) |

(If a *per-type* split is more useful to the community than our
per-assertion design, that is exactly one of the open questions below.)

## Structure of each nanopublication

Standard four-graph layout (`Head` / `assertion` / `provenance` /
`pubinfo`):

- **assertion** — the plain SPO triple (subject/relation/object using the
  published PD Ontology IRI scheme) **plus** a reified claim node carrying
  the graded metadata a triple cannot: `effectDirection`, `confidence`,
  `evidenceTier`, `assertionId`. Entity and relation IRIs follow the
  ontology published at [Zenodo (concept DOI
  10.5281/zenodo.22759155)](https://doi.org/10.5281/zenodo.22759155) and
  BioPortal (PDOSC).
- **provenance** — `prov:wasDerivedFrom` the PubMed IRI, `verbatimContext`
  (the exact source sentence), `extractionSchema`, `paperType`, `species`,
  `extractedAt`, and the `evidenceStateLabel`.
- **pubinfo** — creator ORCID, CC BY-SA 4.0 license, RSA signature
  (`npx:hasSignature`), signed with a demo key (production would use the
  ORCID-linked key + introduction nanopub).

## Validation

The 50 files in `trig/` were triple-validated at generation time
(2026-10-15):

- 50/50 parse clean with rdflib
- 50/50 valid signatures via the official `@nanopub/nanopub-js`
- 50/50 full checks including trusty-URI hash via `nanopub-rs`

`index.json` maps each nanopub to its assertion id, PMID, label, claim, and
trusty URI.

## Open questions for the community

1. Vocabulary alignment (e.g. with `biochementity-vocabulary`) — how much
   effort is proportionate for a pilot?
2. Granularity — we chose one nanopub per assertion; alternatives?
3. Publishing ~1.1M of these into the nanopub network — conventions for
   rate/batch/URI base; what is a sensible first subset?
4. Is there an established RDF pattern we should reuse for
   direction + confidence, and for evidence-state labels, instead of our
   demo `sciai:` properties?
5. Known pitfalls when literature-based graded claims meet the network.

## Contents

- `trig/` — the 50 signed nanopublications
- `unsigned/` — the same 50 before signing
- `index.json` — machine-readable index (assertion ↔ PMID ↔ label ↔ URI)
- `sciai_nanopub.py` — the generator (read-only against our PostgreSQL KG;
  `sciai_automation_lib.py` included for its DSN/dotenv helpers)

## License

Data (the nanopublications): **CC BY-SA 4.0** (see LICENSE) — share-alike instead of non-commercial (changed 2026-11-01): commercial use allowed, derived databases must be shared alike. Generator code (`sciai_nanopub.py`): MIT.
Code: MIT (the generator scripts).
