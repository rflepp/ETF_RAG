import json

import pipeline.build as build
from pipeline.chunk import Chunk
from pipeline.schema import Fact, Fund


class FakeProvider:
    name = "fake"


def test_cache_hit_writes_updated_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "PROCESSED", tmp_path)
    calls = []

    def fake_embed(texts):
        import numpy as np
        calls.append(list(texts))
        return np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384)

    monkeypatch.setattr(build, "embed", fake_embed)
    state = {"ter": 0.2, "chunk": "same text"}

    def fake_build_fund(provider, client, listing, manifest):
        f = Fund(isin="IE00B4L5Y983", provider="t", name=Fact(v="X", s="x"), product_url="u",
                 ter=Fact(v=state["ter"], s="x"))
        return f, [Chunk(chunk_id="c0", doc_id="d", doc_type="kid", page=1, text=state["chunk"])]

    monkeypatch.setattr(build, "build_fund", fake_build_fund)
    build.process(FakeProvider(), None, None, {})
    state["ter"] = 0.15  # a parser fix changes a fact, documents unchanged
    build.process(FakeProvider(), None, None, {})
    on_disk = json.loads((tmp_path / "IE00B4L5Y983.json").read_text())
    assert on_disk["fund"]["ter"]["v"] == 0.15
    assert len(calls) == 1  # embeddings reused, nothing re-embedded

    state["chunk"] = "new data chunk text"
    build.process(FakeProvider(), None, None, {})
    assert calls[-1] == ["new data chunk text"]  # only the changed chunk is embedded
