"""Retrieval over Qdrant: hybrid (dense bge-small + BM25 sparse, fused with RRF inside Qdrant),
payload filters, and an optional cross-encoder rerank of the fused candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from qdrant_client import QdrantClient, models

from pipeline.embed import embed_query
from pipeline.index_qdrant import sparse_model

DEFAULT_RERANKER = "Xenova/ms-marco-MiniLM-L-6-v2"


@dataclass
class Passage:
    id: str
    isin: str
    name: str
    doc_type: str
    page: int
    text: str
    url: str | None
    score: float


@dataclass
class FundFilter:
    isins: list[str] | None = None
    asset_class: str | None = None          # "Equity" | "Fixed Income" | ...
    tags_any: list[str] | None = None       # at least one of these theme tags
    tags_all: list[str] | None = None       # all of these
    exclude_tags: list[str] | None = None
    domicile: str | None = None
    use_of_income: str | None = None        # "Accumulating" | "Distributing"
    hedged: bool | None = None
    max_ter: float | None = None
    providers: list[str] | None = None

    def to_qdrant(self) -> models.Filter | None:
        must, must_not = [], []
        if self.isins:
            must.append(models.FieldCondition(key="isin", match=models.MatchAny(any=self.isins)))
        if self.asset_class:
            must.append(models.FieldCondition(key="asset_class", match=models.MatchValue(value=self.asset_class)))
        if self.tags_any:
            must.append(models.FieldCondition(key="tags", match=models.MatchAny(any=self.tags_any)))
        for t in self.tags_all or []:
            must.append(models.FieldCondition(key="tags", match=models.MatchValue(value=t)))
        if self.exclude_tags:
            must_not.append(models.FieldCondition(key="tags", match=models.MatchAny(any=self.exclude_tags)))
        if self.domicile:
            must.append(models.FieldCondition(key="domicile", match=models.MatchValue(value=self.domicile)))
        if self.use_of_income:
            must.append(models.FieldCondition(key="use_of_income", match=models.MatchValue(value=self.use_of_income)))
        if self.hedged is not None:
            must.append(models.FieldCondition(key="hedged", match=models.MatchValue(value=self.hedged)))
        if self.max_ter is not None:
            must.append(models.FieldCondition(key="ter", range=models.Range(lte=self.max_ter)))
        if self.providers:
            must.append(models.FieldCondition(key="provider", match=models.MatchAny(any=self.providers)))
        return models.Filter(must=must, must_not=must_not) if (must or must_not) else None


@lru_cache(maxsize=2)
def _reranker(name: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(name)


def _sparse_query(text: str) -> models.SparseVector:
    e = next(iter(sparse_model().query_embed(text)))
    return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())


@dataclass
class Retriever:
    qc: QdrantClient
    reranker: str | None = DEFAULT_RERANKER
    fetch: int = 30                      # candidates per branch before fusion / rerank
    stats: dict = field(default_factory=lambda: {"queries": 0})

    def _query(self, collection: str, text: str, flt: models.Filter | None, mode: str, limit: int):
        self.stats["queries"] += 1
        dense = embed_query(text).tolist()
        if mode == "dense":
            return self.qc.query_points(collection, query=dense, using="dense", query_filter=flt, limit=limit,
                                        with_payload=True).points
        if mode == "sparse":
            return self.qc.query_points(collection, query=_sparse_query(text), using="bm25", query_filter=flt,
                                        limit=limit, with_payload=True).points
        return self.qc.query_points(
            collection,
            prefetch=[models.Prefetch(query=dense, using="dense", filter=flt, limit=self.fetch),
                      models.Prefetch(query=_sparse_query(text), using="bm25", filter=flt, limit=self.fetch)],
            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit, with_payload=True).points

    def _rerank(self, query: str, points, text_of) -> list[tuple[object, float]]:
        if not self.reranker or not points:
            return [(p, p.score) for p in points]
        scores = list(_reranker(self.reranker).rerank(query, [text_of(p) for p in points]))
        return sorted(zip(points, scores), key=lambda x: -x[1])

    def search_passages(self, query: str, isins: list[str] | None = None, doc_types: list[str] | None = None,
                        k: int = 8, per_fund: int = 3, mode: str = "hybrid", rerank: bool = True) -> list[Passage]:
        flt = FundFilter(isins=isins).to_qdrant()
        if doc_types:
            cond = models.FieldCondition(key="doc_type", match=models.MatchAny(any=doc_types))
            flt = models.Filter(must=[*(flt.must if flt else []), cond])
        pts = self._query("passages", query, flt, mode, self.fetch)
        ranked = self._rerank(query, pts, lambda p: p.payload["text"]) if rerank else [(p, p.score) for p in pts]
        out, per = [], {}
        for p, s in ranked:
            pl = p.payload
            if per.get(pl["isin"], 0) >= per_fund:
                continue
            per[pl["isin"]] = per.get(pl["isin"], 0) + 1
            out.append(Passage(id=pl["chunk_id"], isin=pl["isin"], name=pl["name"], doc_type=pl["doc_type"],
                               page=pl["page"], text=pl["text"], url=pl.get("url"), score=float(s)))
            if len(out) >= k:
                break
        return out

    def search_funds(self, query: str, flt: FundFilter | None = None, k: int = 8, mode: str = "hybrid",
                     rerank: bool = True, dedupe: bool = True) -> list[dict]:
        pts = self._query("funds", query, flt.to_qdrant() if flt else None, mode, self.fetch)
        ranked = self._rerank(query, pts, lambda p: p.payload["profile"]) if rerank else [(p, p.score) for p in pts]
        out, seen = [], set()
        for p, s in ranked:
            f = p.payload["fund"]
            key = f"{f['provider']}|{(f.get('benchmark') or {}).get('v') or f['name']['v']}" if dedupe else f["isin"]
            if key in seen:
                continue
            seen.add(key)
            out.append({**{k2: v for k2, v in p.payload.items() if k2 not in ("fund", "profile")}, "score": float(s)})
            if len(out) >= k:
                break
        return out

    def get_funds(self, isins: list[str]) -> dict[str, dict]:
        from pipeline.index_qdrant import point_id
        pts = self.qc.retrieve("funds", [point_id(i) for i in isins], with_payload=True)
        return {p.payload["isin"]: p.payload["fund"] for p in pts}

    def all_funds(self) -> list[dict]:
        out, offset = [], None
        while True:
            pts, offset = self.qc.scroll("funds", limit=256, offset=offset, with_payload=["fund"])
            out += [p.payload["fund"] for p in pts]
            if offset is None:
                return out
