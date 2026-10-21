"""Retrieval eval on the server path: recall@k and MRR for dense, sparse, hybrid and hybrid + rerank.

  python -m agent.eval_retrieval [--k 5] [--rerankers Xenova/ms-marco-MiniLM-L-6-v2,...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from pipeline.index_qdrant import client

from .retrieval import Retriever

QUERIES = Path(__file__).resolve().parent.parent / "eval" / "queries.json"


def run(retriever: Retriever, k: int, mode: str, rerank: bool) -> dict:
    q = json.loads(QUERIES.read_text())
    res = {"passages": [0, 0.0, 0], "funds": [0, 0.0, 0], "misses": [], "ms": 0.0}
    t0 = time.time()
    for item in q["passages"]:
        hits = retriever.search_passages(item["q"], isins=item["isins"], k=k, per_fund=k, mode=mode, rerank=rerank)
        exp = item["expect"]
        rank = next((i for i, p in enumerate(hits) if p.isin == exp["isin"] and exp["text"].lower() in p.text.lower()), -1)
        _score(res["passages"], rank)
        if rank < 0:
            res["misses"].append("passage: " + item["q"])
    for item in q["funds"]:
        hits = retriever.search_funds(item["q"], k=k, mode=mode, rerank=rerank)
        rx = re.compile(item["expect_name"], re.I)
        rank = next((i for i, f in enumerate(hits) if rx.search(f["name"])), -1)
        _score(res["funds"], rank)
        if rank < 0:
            res["misses"].append("fund: " + item["q"])
    n = len(q["passages"]) + len(q["funds"])
    res["ms"] = (time.time() - t0) * 1000 / n
    return res


def _score(acc: list, rank: int) -> None:
    acc[2] += 1
    if rank >= 0:
        acc[0] += 1
        acc[1] += 1 / (rank + 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--rerankers", default="Xenova/ms-marco-MiniLM-L-6-v2")
    ap.add_argument("--json")
    args = ap.parse_args()
    qc = client()
    configs = [("dense", None), ("sparse", None), ("hybrid", None)] + [("hybrid", r) for r in args.rerankers.split(",") if r]
    rows = []
    print(f"k={args.k}")
    print(f"{'config':45} {'passages R/MRR':>16} {'funds R/MRR':>14} {'ms/query':>9}")
    for mode, rr in configs:
        r = run(Retriever(qc, reranker=rr), args.k, mode, rerank=bool(rr))
        p, f = r["passages"], r["funds"]
        name = f"{mode}" + (f" + rerank {rr.split('/')[-1]}" if rr else "")
        row = {"config": name, "passages_recall": p[0] / p[2], "passages_mrr": p[1] / p[2],
               "funds_recall": f[0] / f[2], "funds_mrr": f[1] / f[2], "ms_per_query": r["ms"], "misses": r["misses"]}
        rows.append(row)
        print(f"{name:45} {row['passages_recall']:6.2f} / {row['passages_mrr']:.2f}  {row['funds_recall']:5.2f} / "
              f"{row['funds_mrr']:.2f} {row['ms_per_query']:9.0f}")
    for row in rows:
        if row["misses"]:
            print(f"\n{row['config']} misses:\n  " + "\n  ".join(row["misses"]))
    if args.json:
        Path(args.json).write_text(json.dumps({"k": args.k, "results": rows}, indent=1))


if __name__ == "__main__":
    main()
