"""The agent graph with a scripted model: grounding checks, the repair round, stripping and the tool budget."""
import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402

from agent.graph import MAX_ROUNDS  # noqa: E402
from agent.run import make_agent, summarize  # noqa: E402
from agent.tools import Ctx  # noqa: E402
from agent_fakes import FakeRetriever, ScriptedModel, call, final  # noqa: E402

WORLD = "IE00B4L5Y983"
CITE = f"{WORLD}-factsheet#p1"


def run(model, question="Which world ETF?", profile=None):
    graph = make_agent(Ctx(FakeRetriever(), profile=profile or {}), model=model)
    return graph.invoke({"messages": [HumanMessage(question)], "profile": profile or {}}, config={"recursion_limit": 40})


def pick(isin, cites=()):
    return {"isin": isin, "role": "core", "why": "Broad and cheap.", "citations": list(cites)}


def test_grounded_answer_passes():
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]),
                          final(f"A broad developed-markets fund with a low TER [{CITE}].", [pick(WORLD, [CITE])]))
    out = summarize(run(model))
    assert CITE in out["answer"]["reply"]
    assert out["answer"]["picks"][0]["isin"] == WORLD
    assert out["warnings"] == [] and out["repairs"] == 0
    assert out["sources"][CITE]["url"] == f"https://example.com/{WORLD}/fs.pdf"
    assert [c["name"] for c in out["tool_calls"]] == ["get_fund_facts"]


def test_invented_citation_gets_one_repair_round():
    bad = f"{WORLD}-kid#99"
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]),
                          final(f"Low costs [{bad}].", [pick(WORLD)]),
                          final(f"Low costs [{CITE}].", [pick(WORLD)]))
    out = summarize(run(model))
    rejection = model.seen[2][-1]
    assert isinstance(rejection, ToolMessage) and rejection.content.startswith("Rejected") and bad in rejection.content
    assert out["repairs"] == 1 and out["warnings"] == []
    assert CITE in out["answer"]["reply"]


def test_unfixed_answer_is_stripped():
    bad_reply = f"World fund [{WORLD}-kid#99] costs 0.99% a year."
    unseen = "IE00BK5BQT80"
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]),
                          final(bad_reply, [pick(WORLD, [f"{WORLD}-kid#99"]), pick(unseen)]),
                          final(bad_reply, [pick(WORLD, [f"{WORLD}-kid#99"]), pick(unseen)]))
    out = summarize(run(model))
    assert "kid#99" not in out["answer"]["reply"]
    assert [p["isin"] for p in out["answer"]["picks"]] == [WORLD]
    assert out["answer"]["picks"][0]["citations"] == []
    assert any("0.99" in w for w in out["warnings"])


def test_percentages_from_tools_are_accepted():
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]), final(f"TER 0.2% [{CITE}].", [pick(WORLD, [CITE])]))
    out = summarize(run(model))
    assert out["repairs"] == 0 and out["warnings"] == []


def test_tool_budget_forces_an_answer():
    calls = [call("search_documents", query=f"q{i}") for i in range(MAX_ROUNDS)]
    model = ScriptedModel(*calls, final("Here is what I found."))
    out = summarize(run(model))
    assert any(isinstance(m, HumanMessage) and "budget used up" in m.content for m in model.seen[-1])
    assert out["answer"]["reply"] == "Here is what I found."
    assert len(out["tool_calls"]) == MAX_ROUNDS - 1  # the last batch was answered with "not executed"


def test_bad_tool_arguments_are_reported_to_the_model():
    model = ScriptedModel(call("get_fund_facts", isins=5), final("I could not look that up."))
    run(model)
    err = model.seen[1][-1]
    assert isinstance(err, ToolMessage) and err.status == "error"


def test_sql_results_count_as_seen_funds():
    model = ScriptedModel(call("query_funds_sql", sql="SELECT isin, ter FROM funds WHERE ter < 0.15"),
                          final("The cheapest fund here is the SPI fund.", [pick("CH0237935652")]))
    out = summarize(run(model))
    assert [p["isin"] for p in out["answer"]["picks"]] == ["CH0237935652"] and out["repairs"] == 0


def test_shortlist_without_profile_points_to_search():
    model = ScriptedModel(call("shortlist"), final("Please complete the interview first."))
    run(model)
    assert "no investor profile" in model.seen[1][-1].content


def test_schema_violation_gets_a_repair_round_instead_of_losing_picks():
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]),
                          final(f"Core fund [{CITE}].", [pick(WORLD, [CITE])], allocation={}),
                          final(f"Core fund [{CITE}].", [pick(WORLD, [CITE])]))
    out = summarize(run(model))
    assert "schema" in model.seen[2][-1].content
    assert out["repairs"] == 1 and [p["isin"] for p in out["answer"]["picks"]] == [WORLD]


def test_numbers_from_the_question_and_profile_are_not_flagged():
    model = ScriptedModel(call("get_fund_facts", isins=[WORLD]),
                          final(f"With your 20% home bias and the 0.5% you mentioned [{CITE}].", [pick(WORLD, [CITE])]))
    out = summarize(run(model, question="Is 0.5% too much?", profile={"homeBias": 20}))
    assert out["repairs"] == 0 and out["warnings"] == []


def test_invented_isin_in_the_text_is_caught_and_removed():
    reply = "Consider the iShares Global Auto ETF (IE0031944153) instead."
    model = ScriptedModel(final(reply), final(reply))
    out = summarize(run(model, question="Should I buy Tesla shares?"))
    assert "IE0031944153" in model.seen[1][-1].content  # named in the repair request
    assert "IE0031944153" not in out["answer"]["reply"] and "unverified ISIN removed" in out["answer"]["reply"]
    assert any(w.startswith("ISINs") for w in out["warnings"])


def test_isins_from_tools_or_the_question_may_be_named():
    model = ScriptedModel(call("search_funds", query="world"),
                          final(f"{WORLD} and CH0237935652 both fit; you asked about IE00BK5BQT80 too."))
    out = summarize(run(model, question="How does IE00BK5BQT80 compare?"))
    assert out["repairs"] == 0 and out["warnings"] == []
