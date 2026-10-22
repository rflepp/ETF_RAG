"""The agent as an explicit LangGraph state machine.

    START -> agent --(research tool calls)--> tools -> agent ...
                   --(final_answer call)-----> verify --(ok / repaired / stripped)--> END
                   --(plain text)------------> verify          --(issues, first time)--> agent
                   --(tool budget used up)---> force -> agent

The verify node is deterministic: citations must exist in the evidence registry, picks and ISINs named in the text
must have appeared in tool results (or the user's messages), and every percentage in the reply must occur in a tool
result, the user's messages or the profile. One repair round is offered to the model; after that, unsupported
citations, picks and ISINs are stripped and a warning is attached.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from .prompts import SYSTEM, profile_note
from .tools import Ctx, make_tools

MAX_ROUNDS = 6
_ID = r"[A-Z]{2}[A-Z0-9]{9}\d-[a-z]+(?:#[dp]?\d+)?"
# a source id, or anything else written like one ([compare_costs#p1]), which then fails the evidence check
CITE = re.compile(rf"\[({_ID}|[\w.-]+#[\w.-]+)\]")
CITE_GROUP = re.compile(rf"\[\s*({_ID}(?:\s*[,;]\s*{_ID})+)\s*\]")
PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s?%")
ISIN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")


class Pick(BaseModel):
    isin: str
    role: str = Field(description="core | home-bias | satellite | bonds | alternative | other")
    why: str = Field(description="One sentence")
    citations: list[str] = Field(default_factory=list)


class Alloc(BaseModel):
    isin: str
    weight: float


class Answer(BaseModel):
    """Final answer to the investor. Call exactly once when research is done."""
    reply: str = Field(description="Markdown answer with [id] citations")
    picks: list[Pick] = Field(default_factory=list)
    allocation: list[Alloc] | None = None
    follow_up_questions: list[str] = Field(default_factory=list)


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


def _union(a: list, b: list) -> list:
    return list(dict.fromkeys([*a, *b]))


class State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    profile: dict
    evidence: Annotated[dict, _merge]
    seen_isins: Annotated[list, _union]
    rounds: int
    repairs: int
    answer: dict
    warnings: list[str]


def final_answer_tool() -> StructuredTool:
    return StructuredTool.from_function(func=lambda **kw: "received", name="final_answer", args_schema=Answer,
                                        description=Answer.__doc__)


def build(ctx: Ctx, model) -> Any:
    """model: a chat model (or fallback runnable) already bound to make_tools(ctx) + final_answer."""
    tools = {t.name: t for t in make_tools(ctx)}

    def agent(state: State) -> dict:
        sys = SystemMessage(SYSTEM + "\n\n" + profile_note(state.get("profile")))
        return {"messages": [model.invoke([sys, *state["messages"]])], "rounds": state.get("rounds", 0) + 1}

    def run_tools(state: State) -> dict:
        last: AIMessage = state["messages"][-1]
        msgs, evidence, isins = [], {}, []
        for call in last.tool_calls:
            tool = tools.get(call["name"])
            if tool is None:
                msgs.append(ToolMessage(f"unknown tool {call['name']}", tool_call_id=call["id"], status="error"))
                continue
            try:
                msg = tool.invoke({**call, "type": "tool_call"})
            except Exception as e:  # bad arguments from the model: report back instead of failing the run
                msg = ToolMessage(f"tool error: {e}", tool_call_id=call["id"], status="error")
            art = getattr(msg, "artifact", None) or {}
            evidence.update(art.get("evidence", {}))
            isins += art.get("isins", [])
            msgs.append(msg)
        return {"messages": msgs, "evidence": evidence, "seen_isins": isins}

    def force(state: State) -> dict:
        last: AIMessage = state["messages"][-1]
        msgs = [ToolMessage("not executed: research budget used up", tool_call_id=c["id"]) for c in last.tool_calls]
        nudge = HumanMessage("Research budget used up. Call final_answer now with what you have.", name="force")
        return {"messages": [*msgs, nudge]}

    def verify(state: State) -> dict:
        last: AIMessage = state["messages"][-1]
        call = next((c for c in last.tool_calls if c["name"] == "final_answer"), None)
        if call:
            try:
                ans = Answer(**call["args"]).model_dump()
            except Exception as e:
                ans = {"reply": str(call["args"].get("reply", "")), "picks": [], "allocation": None,
                       "follow_up_questions": [], "_error": str(e)}
        else:
            ans = Answer(reply=str(last.content or "")).model_dump()
        ans["reply"] = split_citations(ans["reply"])
        problems = check(ans, state)
        replies = [ToolMessage("ignored: answer submitted" if c["name"] != "final_answer" else "received",
                               tool_call_id=c["id"]) for c in last.tool_calls]
        if problems and state.get("repairs", 0) < 1 and state.get("rounds", 0) <= MAX_ROUNDS + 1:
            if call:
                replies = [r if r.tool_call_id != call["id"] else ToolMessage(
                    "Rejected, please fix and call final_answer again: " + "; ".join(problems), tool_call_id=call["id"])
                    for r in replies]
                return {"messages": replies, "repairs": state.get("repairs", 0) + 1}
            nudge = HumanMessage("Call final_answer with your answer. Fix: " + "; ".join(problems), name="verify")
            return {"messages": [*replies, nudge], "repairs": state.get("repairs", 0) + 1}
        ans, warnings = strip(ans, state, problems)
        return {"messages": replies, "answer": ans, "warnings": warnings}

    def route_agent(state: State) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if any(c["name"] == "final_answer" for c in calls):
            return "verify"
        if calls:
            return "tools" if state.get("rounds", 0) < MAX_ROUNDS else "force"
        return "verify"

    def route_verify(state: State) -> str:
        return END if state.get("answer") else "agent"

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", run_tools)
    g.add_node("force", force)
    g.add_node("verify", verify)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent, {"tools": "tools", "force": "force", "verify": "verify"})
    g.add_edge("tools", "agent")
    g.add_edge("force", "agent")
    g.add_conditional_edges("verify", route_verify, {END: END, "agent": "agent"})
    return g.compile()


def split_citations(reply: str) -> str:
    """[id1, id2] -> [id1] [id2], so each id is checked (and stripped) on its own."""
    return CITE_GROUP.sub(lambda m: " ".join(f"[{i}]" for i in re.split(r"\s*[,;]\s*", m.group(1))), reply)


def _known_numbers(state: State) -> set[float]:
    """Numbers the reply may use: research tool output, the user's own messages and the interview profile. Not
    verify's replies to final_answer or its injected prompts (name="verify"/"force"): they quote rejected numbers."""
    answers = {c["id"] for m in state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls
               if c["name"] == "final_answer"}
    texts = [json.dumps(state.get("profile") or {})]
    for m in state["messages"]:
        if isinstance(m, ToolMessage) and m.tool_call_id not in answers:
            texts.append(str(m.content))
        elif isinstance(m, HumanMessage) and m.name is None:
            texts.append(str(m.content))
    return {round(float(x), 3) for t in texts for x in re.findall(r"-?\d+(?:\.\d+)?", t)}


def check(ans: dict, state: State) -> list[str]:
    evidence, seen = state.get("evidence", {}), set(state.get("seen_isins", []))
    problems = []
    if ans.get("_error"):
        problems.append(f"final_answer arguments do not match its schema ({ans['_error'][:300]}); picks were lost")
    bad_cites = sorted({c for c in CITE.findall(ans["reply"]) if c not in evidence}
                       | {c for p in ans["picks"] for c in p.get("citations", []) if c not in evidence})
    if bad_cites:
        problems.append(f"these citation ids were never returned by a tool: {', '.join(bad_cites[:6])}")
    bad_isins = [p["isin"] for p in ans["picks"] if p["isin"] not in seen]
    if bad_isins:
        problems.append(f"picks not found in tool results: {', '.join(bad_isins)}")
    bad_named = _unknown_isins(ans["reply"], state)
    if bad_named:
        problems.append(f"ISINs in the reply that no tool returned: {', '.join(bad_named[:6])}")
    nums = _known_numbers(state)
    unsupported = [m for m in PCT.findall(ans["reply"]) if round(float(m), 3) not in nums]
    if unsupported:
        problems.append("percentages not present in any tool result or the question: "
                        f"{', '.join(sorted(set(unsupported))[:6])}%")
    if not ans["reply"].strip():
        problems.append("empty reply")
    return problems


def _unknown_isins(reply: str, state: State) -> list[str]:
    """ISINs named in the prose (outside citations) that neither a tool nor the user mentioned."""
    allowed = set(state.get("seen_isins", []))
    allowed |= {i for m in state["messages"] if isinstance(m, HumanMessage) and m.name is None
                for i in ISIN.findall(str(m.content))}
    return sorted({i for i in ISIN.findall(CITE.sub("", reply)) if i not in allowed})


def strip(ans: dict, state: State, problems: list[str]) -> tuple[dict, list[str]]:
    evidence, seen = state.get("evidence", {}), set(state.get("seen_isins", []))
    ans = json.loads(json.dumps(ans))
    ans["reply"] = CITE.sub(lambda m: m.group(0) if m.group(1) in evidence else "", ans["reply"])
    for isin in _unknown_isins(ans["reply"], state):
        ans["reply"] = ans["reply"].replace(isin, "[unverified ISIN removed]")
    ans["picks"] = [{**p, "citations": [c for c in p.get("citations", []) if c in evidence]}
                    for p in ans["picks"] if p["isin"] in seen]
    if ans.get("allocation"):
        ans["allocation"] = [a for a in ans["allocation"] if a["isin"] in seen]
    warnings = [p for p in problems if p.startswith(("percentages", "ISINs"))]
    return ans, warnings
