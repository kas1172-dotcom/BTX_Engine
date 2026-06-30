"""Tests for the agentic research pass (analysis/research.py). The Anthropic
client and all HTTP are mocked — no network, no API calls."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from monitor_engine.analysis.research import ResearchAgent, _strip_html
from monitor_engine.models import (
    AnalyzedItem,
    Branding,
    Cadence,
    ClientConfig,
    ConnectorSpec,
    CostCaps,
    DeepAnalysisConfig,
    DeepAnalysisSection,
    Edition,
    EditionAnalysis,
    EnricherConfig,
    Entity,
    FactMapping,
    KeywordPrefilter,
    RawItem,
    RssSource,
    ScoringRubric,
    TierThresholds,
)


def _config(*, agentic=True, allow_fetch=True, steps=4) -> ClientConfig:
    return ClientConfig(
        branding=Branding(name="T", accent_color="#0066CC"),
        editions=[Edition(id="bd", label="BD", audience_description="a",
                          analysis_instructions="i", categories=[])],
        scoring_rubric=ScoringRubric(thresholds=TierThresholds(), never_discard=[]),
        sources=[RssSource(type="rss", id="s", name="Feed", url="https://example.com/feed")],
        keyword_prefilter=KeywordPrefilter(include=[]),
        cadence=Cadence(cron="0 7 * * 1"),
        cost_caps=CostCaps(),
        deep_analysis=DeepAnalysisConfig(
            instruction="Analyze.",
            sections=[DeepAnalysisSection(id="background", label="Background", instruction="x")],
            agentic=agentic, agentic_tiers=[1], max_research_steps=steps, allow_fetch=allow_fetch,
        ),
        enrichers=[EnricherConfig(
            id="awards", label="Awards", applies_to_entity_types=["organization"],
            connector=ConnectorSpec(
                url="https://api.example.com/?q={query}", item_path="$.results",
                fact_map=[FactMapping(label="Amount", field="amount", kind="money")],
            ),
        )],
    )


def _item() -> AnalyzedItem:
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    return AnalyzedItem(
        item_id="a", title="RTX wins contract", url="https://example.com/a",
        source_id="Feed", published_at=now, collected_at=now, tier=1,
        per_edition={"bd": EditionAnalysis(relevance_score=90, so_what="x", now_what="y", categories=[])},
        entities=[Entity(name="RTX", type="organization")],
    )


def _raw() -> RawItem:
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    return RawItem(id="a", title="RTX wins contract", summary="A defense award.",
                   url="https://example.com/a", published_date=now, discovery_date=now,
                   source_name="Feed", source_type="rss")


def _block(type_, **kw):
    b = MagicMock()
    b.type = type_
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def _resp(content, stop_reason):
    r = MagicMock()
    r.content = content
    r.stop_reason = stop_reason
    usage = MagicMock()
    usage.input_tokens = 50
    usage.output_tokens = 80
    r.usage = usage
    return r


def _http_session(json_payload=None, text="<html><body>Hello <b>world</b></body></html>"):
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = json_payload or {"results": [{"amount": "$5,000,000"}]}
    resp.text = text
    resp.raise_for_status.return_value = None
    session.get.return_value = resp
    session.post.return_value = resp
    return session


# ─── tool loop ──────────────────────────────────────────────────────────────

def test_research_runs_tool_loop_and_returns_notes():
    client = MagicMock()
    tool_block = _block("tool_use", name="query_api", id="t1",
                        input={"enricher_id": "awards", "query": "RTX"})
    client.messages.create.side_effect = [
        _resp([tool_block], stop_reason="tool_use"),
        _resp([_block("text", text="RTX has a $5M award.")], stop_reason="end_turn"),
    ]
    usages = []
    agent = ResearchAgent(client, _config(), model="m", session=_http_session(),
                          on_usage=lambda u: usages.append(u))
    out = agent.research(_item(), _raw())
    assert "RTX has a $5M award." in out.notes
    assert out.steps == 2
    assert len(usages) == 2                       # cost charged per call


def test_research_query_api_tool_executes_connector():
    client = MagicMock()
    captured = {}

    def create(**kwargs):
        # second call: assert the tool_result was fed back in
        if client.messages.create.call_count == 1:
            return _resp([_block("tool_use", name="query_api", id="t1",
                                 input={"enricher_id": "awards", "query": "RTX"})],
                         stop_reason="tool_use")
        captured["messages"] = kwargs["messages"]
        return _resp([_block("text", text="done")], stop_reason="end_turn")

    client.messages.create.side_effect = create
    session = _http_session()
    agent = ResearchAgent(client, _config(), model="m", session=session)
    agent.research(_item(), _raw())
    # connector hit the API with the query
    assert "q=RTX" in session.get.call_args[0][0]
    # the tool_result message carries the fetched JSON back to the model
    tool_result_msg = captured["messages"][-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert "5,000,000" in tool_result_msg["content"][0]["content"]


def test_research_fetch_url_tool_strips_html_and_records_source():
    client = MagicMock()
    client.messages.create.side_effect = [
        _resp([_block("tool_use", name="fetch_url", id="t1",
                     input={"url": "https://example.com/doc"})], stop_reason="tool_use"),
        _resp([_block("text", text="summary")], stop_reason="end_turn"),
    ]
    agent = ResearchAgent(client, _config(), model="m", session=_http_session())
    out = agent.research(_item(), _raw())
    assert "https://example.com/doc" in out.sources


def test_research_unknown_enricher_returns_error_string_not_crash():
    client = MagicMock()
    client.messages.create.side_effect = [
        _resp([_block("tool_use", name="query_api", id="t1",
                     input={"enricher_id": "nope", "query": "RTX"})], stop_reason="tool_use"),
        _resp([_block("text", text="could not look that up")], stop_reason="end_turn"),
    ]
    agent = ResearchAgent(client, _config(), model="m", session=_http_session())
    out = agent.research(_item(), _raw())
    assert out.steps == 2                          # loop continued past the bad tool call


def test_research_respects_max_steps():
    client = MagicMock()
    # always asks for a tool → bounded only by max_research_steps
    client.messages.create.return_value = _resp(
        [_block("tool_use", name="query_api", id="t", input={"enricher_id": "awards", "query": "RTX"})],
        stop_reason="tool_use",
    )
    agent = ResearchAgent(client, _config(steps=3), model="m", session=_http_session())
    out = agent.research(_item(), _raw())
    assert out.steps == 3
    assert client.messages.create.call_count == 3


def test_research_best_effort_on_api_failure():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("api down")
    agent = ResearchAgent(client, _config(), model="m", session=_http_session())
    out = agent.research(_item(), _raw())
    assert out.is_empty
    assert out.steps == 0


def test_fetch_tool_absent_when_allow_fetch_false():
    client = MagicMock()
    captured = {}

    def create(**kwargs):
        captured["tools"] = kwargs["tools"]
        return _resp([_block("text", text="ok")], stop_reason="end_turn")

    client.messages.create.side_effect = create
    agent = ResearchAgent(client, _config(allow_fetch=False), model="m", session=_http_session())
    agent.research(_item(), _raw())
    names = {t["name"] for t in captured["tools"]}
    assert "fetch_url" not in names
    assert "query_api" in names


def test_strip_html():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
