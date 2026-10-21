"""Write the static site data: etfs.json, funds.vec(+.json), docs/{ISIN}.json, meta.json, review.json."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import numpy as np

from .download import ROOT
from .embed import DIM, MODEL

SITE_DATA = ROOT / "site" / "data"
REVIEW = ROOT / "data" / "review.json"


def publish(records: list[dict]) -> dict:
    records = sorted(records, key=lambda r: r["fund"]["isin"])
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    (SITE_DATA / "docs").mkdir(parents=True, exist_ok=True)

    funds = []
    for r in records:
        f = dict(r["fund"])
        f.pop("issues", None)
        funds.append(f)
    _write_json(SITE_DATA / "etfs.json", {"version": version, "funds": funds})

    vecs = np.stack([np.frombuffer(base64.b64decode(r["profile"]["vec"]), dtype=np.int8) for r in records]) \
        if records else np.zeros((0, DIM), dtype=np.int8)
    (SITE_DATA / "funds.vec").write_bytes(vecs.tobytes())
    _write_json(SITE_DATA / "funds.vec.json", {
        "version": version, "dim": DIM, "isins": [r["fund"]["isin"] for r in records],
        "scales": [round(r["profile"]["scale"], 8) for r in records],
        "texts": [r["profile"]["text"] for r in records]})

    live = set()
    for r in records:
        isin = r["fund"]["isin"]
        live.add(f"{isin}.json")
        _write_json(SITE_DATA / "docs" / f"{isin}.json", {
            "isin": isin, "version": version,
            "docs": [{k: d[k] for k in ("doc_id", "doc_type", "url", "as_of", "pages")} for d in r["fund"]["docs"]],
            "chunks": [{"id": c["chunk_id"], "doc": c["doc_id"], "p": c["page"], "t": c["text"],
                        "v": c["vec"], "s": round(c["scale"], 8)} for c in r["chunks"]],
        })
    for stale in (SITE_DATA / "docs").glob("*.json"):
        if stale.name not in live:
            stale.unlink()

    meta = {
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL, "dim": DIM,
        "funds": len(records),
        "chunks": sum(len(r["chunks"]) for r in records),
        "providers": sorted({r["fund"]["provider"] for r in records}),
        "as_of": max((d.get("as_of") or "" for r in records for d in r["fund"]["docs"]), default=None),
    }
    _write_json(SITE_DATA / "meta.json", meta)

    review = [{"isin": r["fund"]["isin"], "name": r["fund"]["name"]["v"], "issues": r["fund"]["issues"]}
              for r in records if r["fund"]["issues"]]
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps(review, indent=1))
    return meta


def _write_json(path, obj) -> None:
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
