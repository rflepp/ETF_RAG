"""Upload the processed cache to Qdrant: `passages` (document chunks) and `funds` (one point per ETF).

Both collections carry a dense vector (bge-small, reused from the processing cache) and a BM25 sparse vector
(Qdrant applies IDF server-side), so retrieval can fuse lexical and semantic matches inside Qdrant. The `funds`
payload holds the complete fact sheet of each fund, which makes Qdrant the single source of truth for the agent.

  QDRANT_URL / QDRANT_API_KEY  -> Qdrant Cloud;  unset -> local embedded store in data/qdrant
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
from qdrant_client import QdrantClient, models

from .download import ROOT
from .embed import DIM, MODEL

SPARSE_MODEL = "Qdrant/bm25"
LOCAL_PATH = ROOT / "data" / "qdrant"
NS = uuid.UUID("2f1d7a0e-8c1b-4f0e-9a51-5b6f0e0c1e11")
KEYWORD_FIELDS = {
    "passages": ["isin", "doc_type", "provider", "asset_class", "tags"],
    "funds": ["isin", "provider", "asset_class", "tags", "domicile", "use_of_income", "replication", "six_ccy"],
}


def client() -> QdrantClient:
    url = os.environ.get("QDRANT_URL")
    if url:
        return QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"), timeout=120)
    LOCAL_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(LOCAL_PATH))


def point_id(key: str) -> str:
    return str(uuid.uuid5(NS, key))


def dequant(b64: str, scale: float) -> list[float]:
    v = np.frombuffer(base64.b64decode(b64), dtype=np.int8).astype(np.float32) * scale
    return (v / (np.linalg.norm(v) + 1e-12)).tolist()


@lru_cache(maxsize=1)
def sparse_model():
    from fastembed import SparseTextEmbedding
    return SparseTextEmbedding(SPARSE_MODEL)


def _sparse(texts: list[str]) -> list[models.SparseVector]:
    return [models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
            for e in sparse_model().embed(texts, batch_size=256)]


def _ensure(qc: QdrantClient, name: str) -> None:
    if qc.collection_exists(name):
        qc.delete_collection(name)
    qc.create_collection(
        name,
        vectors_config={"dense": models.VectorParams(size=DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in KEYWORD_FIELDS[name]:
        qc.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
    if name == "funds":
        qc.create_payload_index(name, "ter", models.PayloadSchemaType.FLOAT)
        qc.create_payload_index(name, "hedged", models.PayloadSchemaType.BOOL)


def _six_ccy(f: dict) -> str | None:
    ls = (f.get("listings") or {}).get("v") or []
    six = [l for l in ls if l["exchange"] == "SIX Swiss Exchange"]
    chf = [l for l in six if l.get("ccy") == "CHF"]
    return (chf or six or [{}])[0].get("ccy")


def fund_payload(f: dict, version: str) -> dict:
    v = lambda k: (f.get(k) or {}).get("v")  # noqa: E731
    return {
        "isin": f["isin"], "name": f["name"]["v"], "provider": f["provider"], "version": version,
        "asset_class": v("asset_class"), "tags": f.get("theme_tags", []), "domicile": v("domicile"),
        "use_of_income": v("use_of_income"), "hedged": bool(v("hedged")), "hedge_currency": v("hedge_currency"),
        "replication": v("replication"), "ter": v("ter"), "td_avg_3y": f.get("td_avg_3y"),
        "td_avg_5y": f.get("td_avg_5y"), "fund_size_m": v("fund_size_m"), "six_ccy": _six_ccy(f),
        "fund": f,  # complete facts with sources, served by get_fund_facts
    }


def index(records: list[dict], batch: int = 256) -> dict:
    qc = client()
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    for name in ("passages", "funds"):
        _ensure(qc, name)

    funds = [r["fund"] for r in records]
    profiles = [r["profile"]["text"] for r in records]
    sparse = _sparse(profiles)
    pts = [models.PointStruct(id=point_id(f["isin"]),
                              vector={"dense": dequant(r["profile"]["vec"], r["profile"]["scale"]), "bm25": sp},
                              payload={**fund_payload(f, version), "profile": r["profile"]["text"]})
           for f, r, sp in zip(funds, records, sparse)]
    for i in range(0, len(pts), batch):
        qc.upsert("funds", pts[i:i + batch], wait=True)

    n = 0
    for r in records:
        f = r["fund"]
        docs = {d["doc_id"]: d for d in f["docs"]}
        chunks = r["chunks"]
        if not chunks:
            continue
        sp = _sparse([c["text"] for c in chunks])
        pts = []
        for c, s in zip(chunks, sp):
            d = docs.get(c["doc_id"], {})
            pts.append(models.PointStruct(
                id=point_id(c["chunk_id"]),
                vector={"dense": dequant(c["vec"], c["scale"]), "bm25": s},
                payload={"chunk_id": c["chunk_id"], "isin": f["isin"], "doc_id": c["doc_id"],
                         "doc_type": c["doc_type"], "page": c["page"], "text": c["text"], "name": f["name"]["v"],
                         "provider": f["provider"], "asset_class": (f.get("asset_class") or {}).get("v"),
                         "tags": f.get("theme_tags", []), "url": d.get("url"), "as_of": d.get("as_of"),
                         "version": version}))
        for i in range(0, len(pts), batch):
            qc.upsert("passages", pts[i:i + batch], wait=True)
        n += len(pts)
    meta = {"version": version, "funds": len(funds), "passages": n, "dense_model": MODEL, "sparse_model": SPARSE_MODEL}
    qc.close()
    return meta
