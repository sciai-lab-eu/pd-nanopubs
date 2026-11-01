#!/usr/bin/env python3
"""sciai-lab → nanopublication demonstrator (read-only, demo).

Turns real knowledge-graph assertions (kg.assertions) from the GLP-1 pilot
corpus (207 PMIDs, evidence-state labels v2) into signed nanopublications
(TriG named-graph bundles), for the Knowledge Pixels / Tobias Kuhn demo.

Assertion-graph shape (documented in README.md):
  * a plain SPO triple  <entity> <pdo:relation/REL> <entity>   — directly
    consumable by SPO tooling, and
  * a reified claim node  sub:claim a sciai:Assertion  carrying the graded
    metadata a plain triple cannot (effect direction, confidence, evidence
    tier, assertion id). Provenance attaches to the claim node.

IRI conventions reuse the ontology exporter (export_ontology_v01.py):
  entity:   https://pd.sciai.eu/ontology/entity/<percent-encoded canonical_id>
  relation: https://pd.sciai.eu/ontology/relation/<relation_type>
  type:     https://pd.sciai.eu/ontology/type/<entity_type>
  property: https://pd.sciai.eu/ontology/property/<name>   (demo extension)

Selection: confidence >= 0.8 (fallback 0.7 if short), all assertions of
contested-label papers first (the interesting cases), then round-robin over
supported/single_source papers with relation-type diversity, until --n.

Signing: with --sign, each unsigned TriG is signed via the official
knowledgepixels library (@nanopub/nanopub-js, js/sign_nanopubs.mjs) using a
freshly generated DEMO RSA key (js/demo_key.pem). Production signing will use
jk's ORCID-linked key. The demo key is not a credential.

Run:
    /55a11/sciai/.venv/bin/python /55a11/output/nanopub_demo/sciai_nanopub.py --dry-run
    /55a11/sciai/.venv/bin/python /55a11/output/nanopub_demo/sciai_nanopub.py --sign
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg
from rdflib import Dataset, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS, XSD

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, "/55a11/maintenance")
from sciai_automation_lib import get_dsn, load_dotenv_repo

logger = logging.getLogger("sciai_nanopub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")

DEMO_DIR = Path("/55a11/output/nanopub_demo")
LABELS_FILE = Path("/55a11/eval_reports/evidence_state_literature_20260921/literature_labels_v2.jsonl")
JS_DIR = DEMO_DIR / "js"
DEMO_KEY = JS_DIR / "demo_key.pem"
NODE = "/55a11/tools/node/bin/node"

# IRI scheme — byte-identical to maintenance/export_ontology_v01.py.
BASE_IRI = "https://pd.sciai.eu/ontology"
PROP_IRI = f"{BASE_IRI}/property"
NP_BASE = "https://pd.sciai.eu/np"  # demo nanopub base; signing appends the trusty artifact code

NP = Namespace("http://www.nanopub.org/nschema#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SCIAI = Namespace(f"{PROP_IRI}/")
CREATOR_ORCID = URIRef("https://orcid.org/0009-0008-6409-2596")
LICENSE_CC_BY_SA = URIRef("https://creativecommons.org/licenses/by-sa/4.0/")
SCIAI_LAB = URIRef("https://sciai.eu/#sciai-lab")

EVIDENCE_STATE_NOTE = (
    "Evidence-state label of the citing paper within the GLP-1 pilot corpus "
    "(207 papers; supported / contested / single-source / unlabeled), computed "
    "from cross-paper agreement over kg.assertions with author-overlap "
    "independence exclusions (v2, 2026-09-21)."
)


def entity_iri(canonical_id: str) -> URIRef:
    return URIRef(f"{BASE_IRI}/entity/{quote(canonical_id, safe='')}")


def relation_iri(relation_type: str) -> URIRef:
    return URIRef(f"{BASE_IRI}/relation/{relation_type}")


def type_iri(entity_type: str) -> URIRef:
    return URIRef(f"{BASE_IRI}/type/{entity_type}")


def pubmed_iri(pmid: int) -> URIRef:
    return URIRef(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")


def load_labels(path: Path) -> dict[int, str]:
    labels = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        labels[int(row["pmid"])] = row.get("label", "unlabeled")
    return labels


def fetch_assertions(conn, pmids: list[int], min_confidence: float) -> list[dict]:
    """Read-only fetch of assertions on the pilot PMIDs at/above the confidence floor."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT assertion_id, source_canonical_id, relation_type, target_canonical_id,
                   pmid, paper_type, species, effect_direction, context, confidence,
                   evidence_tier, year, source_schema, created_at
            FROM kg.assertions
            WHERE pmid = ANY(%s) AND confidence >= %s
            ORDER BY pmid, confidence DESC, assertion_id
            """,
            (pmids, min_confidence),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_entity_metadata(conn, canonical_ids: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """(canonical_id -> preferred_name, canonical_id -> entity_type) for the used canonicals.

    Simplified mirror of ingest_edge_summaries_to_neo4j._load_entity_metadata:
    curated surface forms first, then confidence-weighted majority vote;
    authority_alias rows (preserved loser codes) never vote.
    """
    name_counter: dict[str, Counter] = defaultdict(Counter)
    type_counter: dict[str, Counter] = defaultdict(Counter)
    curated_name: dict[str, tuple[float, str]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_id, surface_form, entity_type, confidence, curated
            FROM kg.entity_synonyms
            WHERE canonical_id = ANY(%s)
              AND (canonical_source IS NULL OR canonical_source <> 'authority_alias')
            """,
            (list(canonical_ids),),
        )
        for canonical_id, surface_form, entity_type, confidence, curated in cur:
            weight = float(confidence) if confidence is not None else 1.0
            if surface_form:
                name_counter[canonical_id][surface_form] += weight
                if curated:
                    prev = curated_name.get(canonical_id)
                    if prev is None or weight > prev[0]:
                        curated_name[canonical_id] = (weight, surface_form)
            if entity_type:
                type_counter[canonical_id][entity_type] += weight
    preferred_name = {}
    for cid, counter in name_counter.items():
        preferred_name[cid] = curated_name.get(cid, (0, counter.most_common(1)[0][0]))[1]
    entity_type = {cid: counter.most_common(1)[0][0] for cid, counter in type_counter.items()}
    return preferred_name, entity_type


def select_assertions(rows: list[dict], labels: dict[int, str], n: int) -> list[dict]:
    """Contested papers first (all interesting cases), then diverse round-robin."""
    by_pmid: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_pmid[row["pmid"]].append(row)

    chosen: list[dict] = []
    seen_triples: set[tuple] = set()

    def take(row: dict) -> bool:
        key = (row["source_canonical_id"], row["relation_type"], row["target_canonical_id"], row["pmid"])
        if key in seen_triples:
            return False
        seen_triples.add(key)
        chosen.append(row)
        return True

    contested_pmids = sorted(p for p, label in labels.items() if label == "contested")
    for pmid in contested_pmids:
        for row in by_pmid.get(pmid, [])[:3]:
            if len(chosen) >= n:
                break
            take(row)

    rest_pmids = sorted(p for p in by_pmid if p not in contested_pmids)
    while len(chosen) < n:
        progress = False
        for pmid in rest_pmids:
            if len(chosen) >= n:
                break
            pool = by_pmid[pmid]
            for row in pool:
                if take(row):
                    progress = True
                    break
        if not progress:
            break
    return chosen[:n]


def build_nanopub(row: dict, label: str, name: str | None, names: dict[str, str],
                  types: dict[str, str], now: str) -> Dataset:
    """Build one nanopublication as an rdflib Dataset (Head/assertion/provenance/pubinfo)."""
    base = f"{NP_BASE}/{row['assertion_id']}"
    this = URIRef(base)
    head = URIRef(f"{base}/Head")
    assertion_g = URIRef(f"{base}/assertion")
    provenance_g = URIRef(f"{base}/provenance")
    pubinfo_g = URIRef(f"{base}/pubinfo")
    claim = URIRef(f"{base}/claim")

    src = entity_iri(row["source_canonical_id"])
    rel = relation_iri(row["relation_type"])
    tgt = entity_iri(row["target_canonical_id"])

    ds = Dataset()
    ds.bind("np", NP)
    ds.bind("prov", PROV)
    ds.bind("dct", DCTERMS)
    ds.bind("xsd", XSD)
    ds.bind("pdo", Namespace(f"{BASE_IRI}/"))
    ds.bind("sciai", SCIAI)

    g = ds.graph(head)
    g.add((this, RDF.type, NP.Nanopublication))
    g.add((this, NP.hasAssertion, assertion_g))
    g.add((this, NP.hasProvenance, provenance_g))
    g.add((this, NP.hasPublicationInfo, pubinfo_g))

    # --- assertion graph: plain SPO triple + reified claim node ---
    g = ds.graph(assertion_g)
    g.add((src, rel, tgt))
    g.add((claim, RDF.type, SCIAI.Assertion))
    g.add((claim, SCIAI.hasSubject, src))
    g.add((claim, SCIAI.hasRelation, rel))
    g.add((claim, SCIAI.hasObject, tgt))
    if row.get("effect_direction"):
        g.add((claim, SCIAI.effectDirection, Literal(row["effect_direction"])))
    if row.get("confidence") is not None:
        g.add((claim, SCIAI.confidence, Literal(float(row["confidence"]), datatype=XSD.decimal)))
    if row.get("evidence_tier"):
        g.add((claim, SCIAI.evidenceTier, Literal(row["evidence_tier"])))
    g.add((claim, SCIAI.assertionId, Literal(int(row["assertion_id"]), datatype=XSD.integer)))
    for entity in (src, tgt):
        cid = row["source_canonical_id"] if entity == src else row["target_canonical_id"]
        if names.get(cid):
            g.add((entity, RDFS.label, Literal(names[cid])))
        if types.get(cid):
            g.add((entity, RDF.type, type_iri(types[cid])))

    # --- provenance graph ---
    # Nanopub convention: provenance triples have the ASSERTION GRAPH IRI as
    # subject (enforced by nanopub-rs check()).
    g = ds.graph(provenance_g)
    g.add((assertion_g, PROV.wasDerivedFrom, pubmed_iri(int(row["pmid"]))))
    g.add((assertion_g, PROV.wasGeneratedBy, SCIAI_LAB))
    if row.get("context"):
        g.add((assertion_g, SCIAI.verbatimContext, Literal(row["context"])))
    if row.get("source_schema"):
        g.add((assertion_g, SCIAI.extractionSchema, Literal(row["source_schema"])))
    if row.get("created_at"):
        ts = row["created_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        g.add((assertion_g, SCIAI.extractedAt, Literal(ts.isoformat(), datatype=XSD.dateTime)))
    if row.get("paper_type"):
        g.add((assertion_g, SCIAI.paperType, Literal(row["paper_type"])))
    if row.get("species"):
        g.add((assertion_g, SCIAI.species, Literal(row["species"])))
    g.add((assertion_g, SCIAI.evidenceStateLabel, Literal(label)))
    g.add((assertion_g, RDFS.comment, Literal(EVIDENCE_STATE_NOTE)))

    # --- pubinfo graph ---
    g = ds.graph(pubinfo_g)
    g.add((this, DCTERMS.creator, CREATOR_ORCID))
    g.add((this, DCTERMS.license, LICENSE_CC_BY_SA))
    g.add((this, DCTERMS.created, Literal(now, datatype=XSD.dateTime)))
    g.add((this, PROV.wasAttributedTo, SCIAI_LAB))
    return ds


def sign_nanopubs(unsigned_dir: Path, signed_dir: Path) -> dict[str, dict]:
    """Sign all unsigned TriG files via js/sign_nanopubs.mjs; return id -> sign info."""
    signed_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [NODE, str(JS_DIR / "sign_nanopubs.mjs"), str(unsigned_dir), str(signed_dir), str(DEMO_KEY)],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if result.returncode != 0:
        logger.error("signer failed:\n%s\n%s", result.stdout, result.stderr)
        raise RuntimeError("nanopub signing failed — see log")
    return json.loads(result.stdout)


def validate_trig(path: Path) -> int:
    ds = Dataset()
    ds.parse(path.as_posix(), format="trig")
    return sum(1 for _ in ds.quads())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=50, help="number of nanopublications (default 50)")
    ap.add_argument("--out", type=Path, default=DEMO_DIR, help="output directory")
    ap.add_argument("--labels", type=Path, default=LABELS_FILE, help="evidence-state labels JSONL")
    ap.add_argument("--min-confidence", type=float, default=0.8)
    ap.add_argument("--sign", action="store_true", help="sign with the demo RSA key via nanopub-js")
    ap.add_argument("--dry-run", action="store_true", help="select + report only, write nothing")
    args = ap.parse_args()

    labels = load_labels(args.labels)
    pmids = sorted(labels)
    logger.info("labels loaded: %d PMIDs (%s)", len(pmids), dict(Counter(labels.values())))

    load_dotenv_repo()
    conn = psycopg.connect(get_dsn(), autocommit=True)
    rows = fetch_assertions(conn, pmids, args.min_confidence)
    if len(rows) < args.n:
        logger.info("only %d rows at conf>=%.2f; relaxing to 0.7", len(rows), args.min_confidence)
        rows = fetch_assertions(conn, pmids, 0.7)
    logger.info("assertions fetched: %d (over %d papers)", len(rows), len({r['pmid'] for r in rows}))

    chosen = select_assertions(rows, labels, args.n)
    logger.info("selected %d assertions over %d papers; labels: %s",
                len(chosen), len({r['pmid'] for r in chosen}),
                dict(Counter(labels[r["pmid"]] for r in chosen)))

    if args.dry_run:
        for row in chosen[:10]:
            logger.info("  [%s] PMID %s: %s -%s-> %s (conf %.2f, %s)",
                        labels[row["pmid"]], row["pmid"], row["source_canonical_id"],
                        row["relation_type"], row["target_canonical_id"],
                        float(row["confidence"] or 0), row["effect_direction"])
        logger.info("dry-run: nothing written")
        conn.close()
        return 0

    canonical_ids = {r["source_canonical_id"] for r in chosen} | {r["target_canonical_id"] for r in chosen}
    names, types = fetch_entity_metadata(conn, canonical_ids)
    conn.close()

    unsigned_dir = args.out / "unsigned"
    signed_dir = args.out / "trig"
    unsigned_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    index: list[dict] = []
    for row in chosen:
        ds = build_nanopub(row, labels[row["pmid"]], names.get(row["source_canonical_id"]), names, types, now)
        fname = f"np_{row['assertion_id']}.trig"
        ds.serialize(destination=(unsigned_dir / fname).as_posix(), format="trig")
        index.append({
            "nanopub_id": f"np_{row['assertion_id']}",
            "assertion_id": row["assertion_id"],
            "pmid": row["pmid"],
            "label": labels[row["pmid"]],
            "claim": (f"{names.get(row['source_canonical_id'], row['source_canonical_id'])} "
                      f"{row['relation_type']} "
                      f"{names.get(row['target_canonical_id'], row['target_canonical_id'])}"),
            "direction": row["effect_direction"],
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
            "source_schema": row["source_schema"],
            "file_unsigned": f"unsigned/{fname}",
        })
    logger.info("wrote %d unsigned nanopubs to %s", len(index), unsigned_dir)

    if args.sign:
        sign_info = sign_nanopubs(unsigned_dir, signed_dir)
        for entry in index:
            info = sign_info.get(entry["nanopub_id"], {})
            entry["file"] = f"trig/{entry['nanopub_id']}.trig"
            entry["trusty_uri"] = info.get("trusty_uri")
            entry["signature_valid"] = info.get("signature_valid")
        logger.info("signed %d nanopubs into %s", len(sign_info), signed_dir)

    (args.out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    logger.info("index.json written (%d entries)", len(index))

    bad = 0
    for entry in index:
        path = args.out / (entry["file"] if args.sign else entry["file_unsigned"])
        try:
            validate_trig(path)
        except Exception as exc:  # noqa: BLE001  (any parse failure counts as invalid)
            bad += 1
            logger.error("rdflib validation FAILED for %s: %s", path, exc)
    logger.info("rdflib validation: %d/%d clean", len(index) - bad, len(index))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
