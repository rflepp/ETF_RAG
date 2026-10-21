"""Per-fund processing: docs -> facts -> validation -> chunks -> embeddings, cached by document hashes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .chunk import Chunk, chunk_doc, data_chunks
from .derive import theme_tags, tracking_difference
from .download import ROOT, fetch, fresh, pdf_path
from .embed import b64, embed, quantize
from .extract.kid import parse_kid
from .net import Client
from .parse import read_pages
from .providers.base import DocLink, Listing, Provider
from .schema import Fund
from .validate import validate

PROCESSED = ROOT / "data" / "cache" / "processed"
CODE_VERSION = "3"  # bump to force re-processing after parser changes


def build_fund(provider: Provider, client: Client, listing: Listing, manifest: dict) -> tuple[Fund, list[Chunk]]:
    f = listing.fund
    api_ter = f.ter.v if f.ter else None
    cached = [DocLink(doc_type=t, url=manifest[f"{f.isin}-{t}"]["url"]) for t in ("factsheet", "kid")
              if fresh(manifest.get(f"{f.isin}-{t}"), pdf_path(f"{f.isin}-{t}"))]
    try:
        links = cached if cached else provider.doc_links(client, listing)
    except RuntimeError as e:
        links = []
        f.issues.append(f"doc links: {e}")
    docs, pages = {}, {}
    for link in links:
        try:
            d = fetch(client, f.isin, link, manifest)
        except RuntimeError as e:
            f.issues.append(f"download {link.doc_type}: {e}")
            continue
        docs[link.doc_type] = d
        pages[link.doc_type] = read_pages(pdf_path(d.doc_id))
        d.pages = len(pages[link.doc_type])
    f.docs = list(docs.values())

    provider.apply_docs(client, listing, docs, pages)
    if "kid" in pages:
        for k, v in parse_kid(pages["kid"], docs["kid"].doc_id).items():
            setattr(f, k, v)

    tracking_difference(f)
    f.theme_tags = theme_tags(f)
    f.issues += validate(f, api_ter)

    chunks: list[Chunk] = []
    for doc_type, ps in pages.items():
        chunks += chunk_doc(ps, docs[doc_type].doc_id, doc_type, f.name.v)
    chunks += data_chunks(f)
    return f, chunks


CURRENCY_WORDS = {"€": "EUR (euro) ", "$": "USD (US dollar) ", "£": "GBP (pound) "}


def fund_profile_text(f: Fund) -> str:
    """Text behind the fund-level index. Currency symbols are spelled out: BM25 tokenizers drop them."""
    name = f.name.v
    for sym, words in CURRENCY_WORDS.items():
        name = name.replace(sym, words)
    parts = [" ".join(name.split())]
    if f.benchmark:
        parts.append(f"tracks {f.benchmark.v}")
    for attr in ("asset_class", "sub_asset_class", "region", "market_type"):
        x = getattr(f, attr)
        if x:
            parts.append(x.v)
    if f.theme_tags:
        parts.append("themes: " + ", ".join(f.theme_tags))
    if f.top_holdings:
        parts.append("top holdings: " + ", ".join(h.name for h in f.top_holdings.v[:10]))
    if f.sectors:
        parts.append("sectors: " + ", ".join(f"{w.name} {w.w:.0f}%" for w in f.sectors.v[:6]))
    if f.countries:
        parts.append("countries: " + ", ".join(f"{w.name} {w.w:.0f}%" for w in f.countries.v[:6]))
    return ". ".join(parts)


def _cache_key(f: Fund) -> str:
    h = hashlib.sha256(CODE_VERSION.encode())
    for d in sorted(f.docs, key=lambda d: d.doc_id):
        h.update(f"{d.doc_id}:{d.sha256}".encode())
    return h.hexdigest()[:16]


def process(provider: Provider, client: Client, listing: Listing, manifest: dict) -> dict:
    """Build one fund and embed its chunks; reuse cached embeddings when no document changed."""
    f, chunks = build_fund(provider, client, listing, manifest)
    key = _cache_key(f)
    cache = PROCESSED / f"{f.isin}.json"
    if cache.exists():
        old = json.loads(cache.read_text())
        if old.get("key") == key:
            # Facts are re-derived every run (cheap); only the embeddings are reused. The data chunks render
            # extracted facts, so they are rebuilt too and re-embedded only when their text changed.
            old["fund"] = json.loads(f.model_dump_json())
            old_vecs = {c["text"]: (c["vec"], c["scale"]) for c in old["chunks"]}
            new_texts = [c.text for c in chunks if c.text not in old_vecs]
            if new_texts:
                q, sc = quantize(embed(new_texts))
                old_vecs.update({t: (b64(q[i]), float(sc[i])) for i, t in enumerate(new_texts)})
            old["chunks"] = [{**asdict(c), "vec": old_vecs[c.text][0], "scale": old_vecs[c.text][1]} for c in chunks]
            profile = fund_profile_text(f)
            if old["profile"]["text"] != profile:
                q, sc = quantize(embed([profile]))
                old["profile"] = {"text": profile, "vec": b64(q[0]), "scale": float(sc[0])}
            cache.write_text(json.dumps(old))
            return old
    texts = [c.text for c in chunks] + [fund_profile_text(f)]
    q, scales = quantize(embed(texts))
    rec = {
        "key": key,
        "fund": json.loads(f.model_dump_json()),
        "chunks": [{**asdict(c), "vec": b64(q[i]), "scale": float(scales[i])} for i, c in enumerate(chunks)],
        "profile": {"text": texts[-1], "vec": b64(q[-1]), "scale": float(scales[-1])},
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rec))
    return rec


def load_processed() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(PROCESSED.glob("*.json"))]


def cache_path(isin: str) -> Path:
    return PROCESSED / f"{isin}.json"


def prune(provider_label: str, live_isins: set[str]) -> list[str]:
    """Drop cached funds of this provider that are no longer listed (closed or excluded funds)."""
    removed = []
    for path in PROCESSED.glob("*.json"):
        if path.stem in live_isins:
            continue
        rec = json.loads(path.read_text())
        if rec["fund"]["provider"] == provider_label:
            path.unlink()
            removed.append(path.stem)
    return removed
