"""Download documents into data/cache/pdf and keep a manifest of url + sha256 (committed)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .net import Client
from .providers.base import DocLink
from .schema import Doc

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "pdf"
MANIFEST = ROOT / "data" / "manifest.json"
MAX_AGE_DAYS = 0.0  # >0: reuse documents fetched within this many days (set by the CLI for local rebuilds)


def fresh(entry: dict | None, path: Path | None = None) -> bool:
    if MAX_AGE_DAYS <= 0 or not entry or not entry.get("fetched_at"):
        return False
    if path is not None and not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])
    return age < timedelta(days=MAX_AGE_DAYS)


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def save_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True))


def pdf_path(doc_id: str) -> Path:
    return CACHE / f"{doc_id}.pdf"


def fetch(client: Client, isin: str, link: DocLink, manifest: dict) -> Doc:
    doc_id = f"{isin}-{link.doc_type}"
    dest = pdf_path(doc_id)
    prev = manifest.get(doc_id, {})
    if prev.get("url") == link.url and fresh(prev, dest):
        return Doc(doc_id=doc_id, isin=isin, doc_type=link.doc_type, url=link.url.split("?")[0], sha256=prev["sha256"])
    sha = client.download(link.url, dest)
    changed = prev.get("sha256") != sha
    manifest[doc_id] = {
        "url": link.url,
        "sha256": sha,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if changed else prev.get("changed_at"),
    }
    return Doc(doc_id=doc_id, isin=isin, doc_type=link.doc_type, url=link.url.split("?")[0], sha256=sha)
