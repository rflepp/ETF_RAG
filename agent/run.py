"""Build and run the agent; shared by the CLI below, the HTTP API (agent/api.py) and the eval (agent/eval_agent.py).

  LLM_CHAIN=ollama python -m agent.run "Which world ETF is cheapest for me?" [--profile '{"broker":"ibkr"}']

Tracing: with LANGSMITH_TRACING=true and LANGSMITH_API_KEY set, every run is traced to LangSmith (project from
LANGSMITH_PROJECT) with the tags and metadata from run_config().
"""
from __future__ import annotations

import argparse
import json
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from . import llm
from .graph import CITE, build, final_answer_tool
from .tools import Ctx, make_tools


def make_agent(ctx: Ctx, paid_allowed: bool = False, model=None):
    """model: a chat model already bound to the tools (tests pass a scripted one); default: the LLM_CHAIN providers."""
    tools = make_tools(ctx) + [final_answer_tool()]
    return build(ctx, model if model is not None else llm.with_tools(llm.chain(paid_allowed), tools))


def run_config(profile: dict | None, paid_allowed: bool = False, run_id: uuid.UUID | None = None,
               tags: list[str] | None = None, metadata: dict | None = None) -> dict:
    return {"run_name": "etf-agent", "run_id": run_id or uuid.uuid4(), "recursion_limit": 40,
            "tags": ["etf-agent", *(tags or [])],
            "metadata": {"llm_chain": ",".join(llm.chain(paid_allowed)), "has_profile": bool(profile),
                         "broker": (profile or {}).get("broker"), **(metadata or {})}}


def to_messages(history: list[dict]) -> list:
    return [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in history]


def summarize(state: dict) -> dict:
    """The parts of a finished run that callers need: answer, warnings, sources of the cited ids, tool trace."""
    ans = state.get("answer") or {}
    evidence = state.get("evidence", {})
    cited = list(dict.fromkeys(CITE.findall(ans.get("reply", "")) +
                               [c for p in ans.get("picks", []) for c in p.get("citations", [])]))
    executed = {m.tool_call_id for m in state.get("messages", []) if isinstance(m, ToolMessage)
                and not str(m.content).startswith(("not executed", "ignored"))}
    calls = [{"name": c["name"], "args": c["args"]} for m in state.get("messages", []) if isinstance(m, AIMessage)
             for c in m.tool_calls if c["name"] != "final_answer" and c["id"] in executed]
    return {"answer": ans, "warnings": state.get("warnings", []),
            "sources": {i: {k: evidence[i].get(k) for k in ("kind", "isin", "name", "url", "page")}
                        for i in cited if i in evidence},
            "tool_calls": calls, "repairs": state.get("repairs", 0), "rounds": state.get("rounds", 0)}


def main() -> None:
    from pipeline.index_qdrant import client

    from .retrieval import Retriever

    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--profile", default="{}")
    args = ap.parse_args()
    profile = json.loads(args.profile)
    graph = make_agent(Ctx(Retriever(client()), profile=profile))
    t0 = time.time()
    for update in graph.stream({"messages": [HumanMessage(args.question)], "profile": profile},
                               config=run_config(profile, tags=["cli"]), stream_mode="updates"):
        for node, delta in update.items():
            for m in delta.get("messages", []) or []:
                if getattr(m, "tool_calls", None):
                    for c in m.tool_calls:
                        print(f"[{time.time() - t0:5.1f}s] {node}: call {c['name']}({json.dumps(c['args'])[:160]})")
                elif m.type == "tool":
                    print(f"[{time.time() - t0:5.1f}s] {node}: {m.name or 'tool'} -> {str(m.content)[:120]}")
            if delta.get("answer"):
                a = delta["answer"]
                print(f"\n=== ANSWER ({time.time() - t0:.1f}s)\n{a['reply']}\n\npicks: {[p['isin'] for p in a['picks']]}")
                if delta.get("warnings"):
                    print("warnings:", delta["warnings"])


if __name__ == "__main__":
    main()
