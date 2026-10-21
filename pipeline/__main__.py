"""CLI.

  python -m pipeline build [--providers ishares,vanguard] [--isins A,B] [--limit N]
  python -m pipeline publish          # rewrite site/data from the processed cache
  python -m pipeline guard [--min-funds N] [--max-issue-share X]   # fail if the build looks broken
  python -m pipeline summary          # markdown summary (fund counts, review list) for CI
  python -m pipeline index            # upload the processed cache to Qdrant (QDRANT_URL or local data/qdrant)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .build import load_processed, process, prune
from .download import load_manifest, save_manifest
from .net import Client
from .providers import PROVIDERS
from .publish import REVIEW, SITE_DATA, publish


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--providers", default=",".join(PROVIDERS))
    b.add_argument("--isins", default="")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--no-publish", action="store_true")
    b.add_argument("--max-age-days", type=float, default=0.0,
                   help="reuse documents fetched within this many days (local rebuilds after parser changes)")
    sub.add_parser("publish")
    g = sub.add_parser("guard")
    g.add_argument("--min-funds", type=int, default=500)
    g.add_argument("--max-issue-share", type=float, default=0.25)
    sub.add_parser("summary")
    sub.add_parser("index")
    args = ap.parse_args(argv)

    if args.cmd == "index":
        from .index_qdrant import index
        print("indexed", index(load_processed()))
        return 0
    if args.cmd == "guard":
        return guard(args.min_funds, args.max_issue_share)
    if args.cmd == "summary":
        print(summary())
        return 0

    if args.cmd == "build":
        from . import download
        download.MAX_AGE_DAYS = args.max_age_days
        client = Client(min_interval=1.0)
        manifest = load_manifest()
        wanted = {i.strip() for i in args.isins.split(",") if i.strip()}
        for name in args.providers.split(","):
            provider = PROVIDERS[name]()
            listings = provider.list_funds(client)
            if not wanted and not args.limit and listings:
                gone = prune(listings[0].fund.provider, {l.fund.isin for l in listings})
                if gone:
                    print(f"{name}: removed {len(gone)} funds no longer listed: {', '.join(gone)}", flush=True)
            if wanted:
                listings = [l for l in listings if l.fund.isin in wanted]
            if args.limit:
                listings = listings[: args.limit]
            print(f"{name}: {len(listings)} funds", flush=True)
            for n, listing in enumerate(listings, 1):
                t0 = time.time()
                try:
                    rec = process(provider, client, listing, manifest)
                    issues = rec["fund"]["issues"]
                    print(f"  [{n}/{len(listings)}] {listing.fund.isin} {len(rec['chunks'])} chunks "
                          f"{time.time() - t0:.1f}s" + (f"  issues: {issues}" if issues else ""), flush=True)
                except Exception as e:  # one broken fund must not stop the run
                    print(f"  [{n}/{len(listings)}] {listing.fund.isin} FAILED: {e}", flush=True)
                if n % 20 == 0:
                    save_manifest(manifest)
            save_manifest(manifest)
        if args.no_publish:
            return 0
    meta = publish(load_processed())
    print("published", meta)
    return 0


def guard(min_funds: int, max_issue_share: float) -> int:
    meta = json.loads((SITE_DATA / "meta.json").read_text())
    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else []
    problems = []
    if meta["funds"] < min_funds:
        problems.append(f"only {meta['funds']} funds (< {min_funds})")
    # "no KID" alone is a known gap for some share classes; count funds with other problems
    serious = [r for r in review if any(not i.startswith("no KID") for i in r["issues"])]
    if meta["funds"] and len(serious) / meta["funds"] > max_issue_share:
        problems.append(f"{len(serious)} of {meta['funds']} funds have validation issues")
    for p in problems:
        print("GUARD:", p)
    return 1 if problems else 0


def summary() -> str:
    meta = json.loads((SITE_DATA / "meta.json").read_text())
    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else []
    lines = [f"## ETF index {meta['version']}", "",
             f"- funds: **{meta['funds']}** ({', '.join(meta['providers'])}), chunks: {meta['chunks']}",
             f"- documents as of: {meta['as_of']}", f"- funds with issues: {len(review)}", ""]
    if review:
        lines += ["| ISIN | Fund | Issues |", "|---|---|---|"]
        lines += [f"| {r['isin']} | {r['name']} | {'; '.join(r['issues'])} |" for r in review[:200]]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
