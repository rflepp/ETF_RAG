"""End-to-end agent eval: runs the cases in eval/agent_cases.json through the full graph and scores each answer.

  LLM_CHAIN=mistral python -m agent.eval_agent                  # LangSmith experiment if LANGSMITH_API_KEY is set
  LLM_CHAIN=ollama  python -m agent.eval_agent --local --json eval/results-agent.json [--cases id,id] [--judge]

With LangSmith the cases are synced into a dataset (new case ids are added) and each run becomes an experiment, so
prompt or model changes can be compared side by side. Without it the same evaluators run locally.

Evaluators (score 1 = good, None = not applicable to the case):
  expected_fund       one of the expected ISINs is picked or named
  expected_tool       the agent used one of the expected tools
  cites_sources       the answer cites at least one source id that a tool returned
  grounded_first_try  the verify node accepted the first final answer (no repair round)
  grounded_final      no unsupported numbers left after verification
  admits_gap          for questions the documents cannot answer: says so instead of guessing
  faithfulness        optional LLM judge (--judge): share of claims supported by the tool results
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from . import llm
from .run import make_agent, run_config, summarize
from .sql import FundsDB
from .tools import Ctx

CASES = Path(__file__).resolve().parent.parent / "eval" / "agent_cases.json"
DATASET = "etf-compass-agent"
GAP = re.compile(r"\b(not (?:relevant|covered|available|included|mentioned|listed|provided|disclosed|stated|found|in the)|"
                 r"do(?:es)? not (?:contain|cover|provide|include|mention|disclose|say)|no (?:information|data|details)|"
                 r"(?:can ?not|unable to|can't) (?:find|answer|say|recommend|advise)|outside (?:the|my) scope|"
                 r"only (?:help|cover|answer) .{0,40}(?:ETF|fund))", re.I)


def make_target(retriever, judge_context_chars: int = 12000):
    funds = retriever.all_funds()
    db = FundsDB(funds)

    def target(inputs: dict) -> dict:
        profile = inputs.get("profile") or {}
        ctx = Ctx(retriever, profile=profile, funds_cache=funds, db_cache=db)
        t0 = time.perf_counter()
        state = make_agent(ctx).invoke({"messages": [HumanMessage(inputs["question"])], "profile": profile},
                                       config=run_config(profile, tags=["eval"]))
        out = summarize(state)
        answers = {c["id"] for m in state["messages"] for c in getattr(m, "tool_calls", None) or []
                   if c["name"] == "final_answer"}
        context = "\n".join(str(m.content) for m in state["messages"]
                            if isinstance(m, ToolMessage) and m.tool_call_id not in answers)
        return {"reply": out["answer"].get("reply", ""), "picks": [p["isin"] for p in out["answer"].get("picks", [])],
                "sources": sorted(out["sources"]), "tools": [c["name"] for c in out["tool_calls"]],
                "repairs": out["repairs"], "warnings": out["warnings"],
                "latency_s": round(time.perf_counter() - t0, 1), "context": context[:judge_context_chars]}

    return target


# ---------- evaluators: (outputs, reference_outputs) -> {"key", "score"}; LangSmith passes these by name ----------

def _r(key: str, score, comment: str | None = None) -> dict:
    return {"key": key, "score": score, **({"comment": comment} if comment else {})}


def expected_fund(outputs: dict, reference_outputs: dict) -> dict:
    want = reference_outputs.get("isins_any") or []
    if not want:
        return _r("expected_fund", None)
    hit = [i for i in want if i in outputs["picks"] or i in outputs["reply"]]
    return _r("expected_fund", int(bool(hit)), None if hit else f"none of {want}; picked {outputs['picks']}")


def expected_tool(outputs: dict, reference_outputs: dict) -> dict:
    want = reference_outputs.get("tools_any") or []
    if not want:
        return _r("expected_tool", None)
    return _r("expected_tool", int(any(t in outputs["tools"] for t in want)), f"used {outputs['tools']}")


def cites_sources(outputs: dict, reference_outputs: dict) -> dict:
    if not reference_outputs.get("must_cite"):
        return _r("cites_sources", None)
    return _r("cites_sources", int(bool(outputs["sources"])))


def grounded_first_try(outputs: dict, reference_outputs: dict) -> dict:
    return _r("grounded_first_try", int(outputs["repairs"] == 0))


def grounded_final(outputs: dict, reference_outputs: dict) -> dict:
    return _r("grounded_final", int(not outputs["warnings"]), "; ".join(outputs["warnings"]) or None)


def admits_gap(outputs: dict, reference_outputs: dict) -> dict:
    if not reference_outputs.get("admits_gap"):
        return _r("admits_gap", None)
    ok = bool(GAP.search(outputs["reply"])) and not outputs["picks"]
    return _r("admits_gap", int(ok))


class Grade(BaseModel):
    supported: float = Field(ge=0, le=1, description="Share of the answer's factual claims about funds that the "
                                                     "tool results support (1 = all, 0 = none)")
    unsupported_claims: list[str] = Field(default_factory=list, description="Claims not backed by the tool results")


JUDGE = """You check an ETF research assistant for hallucinations. Compare the ANSWER with the TOOL RESULTS it was
given. A claim is supported if the tool results state it or it follows directly from them. General investing
advice without fund-specific facts counts as supported. Return the share of supported claims."""


def make_faithfulness(model_name: str):
    judge = llm.make(model_name).with_structured_output(Grade)

    def faithfulness(inputs: dict, outputs: dict) -> dict:
        g = judge.invoke([("system", JUDGE), ("user", f"QUESTION: {inputs['question']}\n\nTOOL RESULTS:\n"
                                                      f"{outputs['context'] or '(none)'}\n\nANSWER:\n{outputs['reply']}")])
        return _r("faithfulness", g.supported, "; ".join(g.unsupported_claims[:5]) or None)

    return faithfulness


EVALUATORS = [expected_fund, expected_tool, cites_sources, grounded_first_try, grounded_final, admits_gap]


# ---------- runners ----------

def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run_langsmith(cases: list[dict], target, evaluators, dataset: str) -> None:
    from langsmith import Client, evaluate

    client = Client()
    if not client.has_dataset(dataset_name=dataset):
        client.create_dataset(dataset, description="ETF Compass agent: end-to-end questions with expectations")
    have = {(e.metadata or {}).get("case_id") for e in client.list_examples(dataset_name=dataset)}
    new = [{"inputs": {"question": c["question"], "profile": c.get("profile")}, "outputs": c["expect"],
            "metadata": {"case_id": c["id"]}} for c in cases if c["id"] not in have]
    if new:
        client.create_examples(dataset_name=dataset, examples=new)
        print(f"added {len(new)} examples to dataset {dataset}")
    chain = ",".join(llm.chain(False))
    evaluate(target, data=dataset, evaluators=evaluators, experiment_prefix=f"agent-{chain}", max_concurrency=1,
             metadata={"llm_chain": chain, "git_sha": _git_sha()})


def run_local(cases: list[dict], target, evaluators) -> dict:
    rows = []
    for c in cases:
        inputs = {"question": c["question"], "profile": c.get("profile")}
        try:
            out = target(inputs)
        except Exception as e:  # a failed run is a result, not a crash of the whole eval
            rows.append({"id": c["id"], "error": repr(e), "scores": {}})
            print(f"{c['id']:26} ERROR {e!r}")
            continue
        scores, comments = {}, {}
        for ev in evaluators:
            args = {"inputs": inputs, "outputs": out, "reference_outputs": c["expect"]}
            names = ev.__code__.co_varnames[:ev.__code__.co_argcount]
            r = ev(**{k: v for k, v in args.items() if k in names})
            scores[r["key"]] = r["score"]
            if r.get("comment") and r["score"] not in (None, 1):
                comments[r["key"]] = r["comment"]
        rows.append({"id": c["id"], "scores": scores, "comments": comments, "tools": out["tools"], "picks": out["picks"],
                     "repairs": out["repairs"], "latency_s": out["latency_s"]})
        shown = " ".join(f"{k}={'-' if v is None else round(v, 2)}" for k, v in scores.items())
        print(f"{c['id']:26} {out['latency_s']:6.1f}s  {shown}", flush=True)
    keys = list(dict.fromkeys(k for r in rows for k in r["scores"]))
    summary = {}
    for k in keys:
        vals = [r["scores"][k] for r in rows if r["scores"].get(k) is not None]
        summary[k] = {"mean": round(sum(vals) / len(vals), 3) if vals else None, "n": len(vals)}
    ok = [r for r in rows if "error" not in r]
    summary["latency_s_mean"] = round(sum(r["latency_s"] for r in ok) / len(ok), 1) if ok else None
    summary["errors"] = len(rows) - len(ok)
    print("\n" + "\n".join(f"{k:22} {v}" for k, v in summary.items()))
    return {"llm_chain": llm.chain(False), "git_sha": _git_sha(), "summary": summary, "cases": rows}


def main() -> None:
    from pipeline.index_qdrant import client

    from .retrieval import Retriever

    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--local", action="store_true", help="run locally even if LANGSMITH_API_KEY is set")
    ap.add_argument("--judge", nargs="?", const=os.environ.get("JUDGE_LLM", "mistral"), default=None,
                    help="add the LLM faithfulness judge (provider name, default JUDGE_LLM or mistral)")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--json", help="write local results to this file")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    target = make_target(Retriever(client()))
    evaluators = EVALUATORS + ([make_faithfulness(args.judge)] if args.judge else [])
    if os.environ.get("LANGSMITH_API_KEY") and not args.local:
        run_langsmith(cases, target, evaluators, args.dataset)
        return
    res = run_local(cases, target, evaluators)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
