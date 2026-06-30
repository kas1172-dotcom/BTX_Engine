"""Tests for the cross-API enrichment stage, the generic connector, and the
entity graph. All HTTP is mocked — no network."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from monitor_engine.enrichment.connector import (
    Connector,
    ConnectorError,
    facts_from_records,
    substitute_query,
)
from monitor_engine.enrichment.core import enrich_items
from monitor_engine.enrichment.graph import build_entity_graph
from monitor_engine.models import (
    AnalyzedItem,
    Branding,
    Cadence,
    ClientConfig,
    ConnectorSpec,
    CostCaps,
    Edition,
    EditionAnalysis,
    EnricherConfig,
    Entity,
    FactMapping,
    KeywordPrefilter,
    RssSource,
    ScoringRubric,
    TierThresholds,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def _item(item_id: str, title: str, entities: list[tuple[str, str]], tier: int = 1) -> AnalyzedItem:
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    return AnalyzedItem(
        item_id=item_id, title=title, url=f"https://example.com/{item_id}",
        source_id="Feed", published_at=now, collected_at=now, tier=tier,
        per_edition={"bd": EditionAnalysis(relevance_score=90, so_what="x", now_what="y", categories=[])},
        entities=[Entity(name=n, type=t) for n, t in entities],
    )


def _config(enrichers: list[EnricherConfig]) -> ClientConfig:
    return ClientConfig(
        branding=Branding(name="T", accent_color="#0066CC"),
        editions=[Edition(id="bd", label="BD", audience_description="a",
                          analysis_instructions="i", categories=[])],
        scoring_rubric=ScoringRubric(thresholds=TierThresholds(), never_discard=[]),
        sources=[RssSource(type="rss", id="s", name="Feed", url="https://example.com/feed")],
        keyword_prefilter=KeywordPrefilter(include=[]),
        cadence=Cadence(cron="0 7 * * 1"),
        cost_caps=CostCaps(),
        enrichers=enrichers,
    )


def _spec(**kw) -> ConnectorSpec:
    base = dict(
        url="https://api.example.com/search?q={query}",
        item_path="$.results",
        fact_map=[FactMapping(label="Amount", field="amount", kind="money")],
    )
    base.update(kw)
    return ConnectorSpec(**base)


def _enricher(types=("organization",), **spec_kw) -> EnricherConfig:
    return EnricherConfig(
        id="awards", label="Awards", applies_to_entity_types=list(types),
        connector=_spec(**spec_kw),
    )


def _mock_session(json_payload, *, raise_exc=None) -> MagicMock:
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    if raise_exc is not None:
        session.get.side_effect = raise_exc
        session.post.side_effect = raise_exc
    else:
        session.get.return_value = resp
        session.post.return_value = resp
    return session


# ─── substitute_query ───────────────────────────────────────────────────────

def test_substitute_query_url_encodes_in_url():
    out = substitute_query("https://x/?q={query}", "Lockheed Martin", url_encode=True)
    assert out == "https://x/?q=Lockheed%20Martin"


def test_substitute_query_raw_in_body_recursive():
    body = {"filters": {"recipient": ["{query}"], "n": 3}}
    out = substitute_query(body, "RTX", url_encode=False)
    assert out == {"filters": {"recipient": ["RTX"], "n": 3}}


def test_substitute_query_leaves_non_template_strings():
    assert substitute_query("static", "x", url_encode=True) == "static"


# ─── Connector.fetch ────────────────────────────────────────────────────────

def test_connector_get_resolves_item_path():
    session = _mock_session({"results": [{"amount": "$5M"}, {"amount": "$2M"}]})
    records = Connector(session).fetch(_spec(), "RTX")
    assert records == [{"amount": "$5M"}, {"amount": "$2M"}]
    # query substituted + URL-encoded into the request URL
    assert "q=RTX" in session.get.call_args[0][0]


def test_connector_post_sends_substituted_body():
    session = _mock_session({"results": []})
    spec = _spec(method="POST", request_body={"recipient_search_text": ["{query}"]})
    Connector(session).fetch(spec, "Boeing")
    assert session.post.call_args.kwargs["json"] == {"recipient_search_text": ["Boeing"]}


def test_connector_single_object_wrapped_in_list():
    session = _mock_session({"results": {"amount": "$1M"}})
    records = Connector(session).fetch(_spec(), "x")
    assert records == [{"amount": "$1M"}]


def test_connector_raises_connectorerror_on_http_error():
    import requests
    session = _mock_session(None, raise_exc=requests.RequestException("boom"))
    with pytest.raises(ConnectorError):
        Connector(session).fetch(_spec(), "x")


def test_connector_raises_on_non_list_item_path():
    session = _mock_session({"results": "not-a-list"})
    with pytest.raises(ConnectorError):
        Connector(session).fetch(_spec(), "x")


# ─── facts_from_records ─────────────────────────────────────────────────────

def test_facts_from_records_parses_money_and_skips_missing():
    spec = _spec(fact_map=[
        FactMapping(label="Amount", field="amount", kind="money"),
        FactMapping(label="Agency", field="agency", kind="text"),
    ])
    records = [{"amount": "$1,240,500,000", "agency": "DoD"}, {"amount": "$2M"}]
    facts = facts_from_records(spec, records, query="RTX", enricher_id="awards")
    assert facts[0].label == "Amount" and facts[0].number == 1240500000.0
    assert facts[1].label == "Agency" and facts[1].value == "DoD"
    # second record's missing agency is skipped, not emitted as blank
    assert all(f.value for f in facts)


def test_facts_from_records_respects_max_facts():
    spec = _spec(max_facts=1, fact_map=[FactMapping(label="A", field="amount")])
    records = [{"amount": "1"}, {"amount": "2"}, {"amount": "3"}]
    facts = facts_from_records(spec, records, query="x", enricher_id="e")
    assert len(facts) == 1


def test_facts_from_records_url_field_attaches_link():
    spec = _spec(url_field="link", fact_map=[FactMapping(label="Title", field="t", kind="text")])
    records = [{"t": "A rule", "link": "https://gov/d/1"}]
    facts = facts_from_records(spec, records, query="F-35", enricher_id="fr")
    assert facts[0].url == "https://gov/d/1"


# ─── enrich_items ───────────────────────────────────────────────────────────

def test_enrich_items_noop_without_enrichers():
    cfg = _config([])
    items = [_item("a", "T", [("RTX", "organization")])]
    result = enrich_items(cfg, items)
    assert result.items[0].enrichment is None


def test_enrich_items_attaches_facts_to_matching_entities():
    cfg = _config([_enricher()])
    items = [_item("a", "RTX wins", [("RTX", "organization"), ("F-35", "program")])]
    session = _mock_session({"results": [{"amount": "$5,000,000"}]})
    result = enrich_items(cfg, items, session=session)
    enr = result.items[0].enrichment
    assert enr is not None
    assert enr.facts[0].entity == "RTX"           # only the organization was queried
    assert enr.queried_entities == ["RTX"]
    assert result.total_facts == 1


def test_enrich_items_dedups_shared_entity_across_items():
    cfg = _config([_enricher()])
    items = [
        _item("a", "RTX one", [("RTX", "organization")]),
        _item("b", "RTX two", [("RTX", "organization")]),
    ]
    session = _mock_session({"results": [{"amount": "$1"}]})
    result = enrich_items(cfg, items, session=session)
    # one lookup, facts fanned out to both items
    assert session.get.call_count == 1
    assert all(it.enrichment and it.enrichment.facts for it in result.items)


def test_enrich_items_failsoft_records_error_without_aborting():
    import requests
    cfg = _config([_enricher()])
    items = [_item("a", "RTX", [("RTX", "organization")])]
    session = _mock_session(None, raise_exc=requests.RequestException("down"))
    result = enrich_items(cfg, items, session=session)
    assert result.total_errors == 1
    assert result.items[0].enrichment.facts == []        # queried, no facts
    assert result.items[0].enrichment.queried_entities == ["RTX"]


def test_enrich_items_caps_distinct_entities():
    cfg = _config([_enricher(max_entities_per_run=2)])
    cfg.enrichers[0].max_entities_per_run = 2
    items = [_item(f"i{n}", f"co{n}", [(f"Co{n}", "organization")]) for n in range(5)]
    session = _mock_session({"results": [{"amount": "$1"}]})
    enrich_items(cfg, items, session=session)
    assert session.get.call_count == 2


# ─── entity graph ───────────────────────────────────────────────────────────

def test_graph_links_items_sharing_entity():
    items = [
        _item("a", "Lockheed and F-35", [("Lockheed Martin", "organization"), ("F-35", "program")]),
        _item("b", "F-35 rule", [("F-35", "program")]),
        _item("c", "Lockheed facility", [("Lockheed Martin", "organization")]),
        _item("d", "Unrelated", [("Boeing", "organization")]),
    ]
    linked, index = build_entity_graph(items)
    by_id = {it.item_id: it for it in linked}
    a_related = {r.item_id for r in by_id["a"].related}
    assert a_related == {"b", "c"}
    assert by_id["d"].related == []
    # via carries the shared entity name
    b_edge = next(r for r in by_id["a"].related if r.item_id == "b")
    assert b_edge.via == ["F-35"]


def test_graph_index_only_connective_entities_sorted_by_degree():
    items = [
        _item("a", "x", [("F-35", "program"), ("Lockheed Martin", "organization")]),
        _item("b", "y", [("F-35", "program"), ("Lockheed Martin", "organization")]),
        _item("c", "z", [("F-35", "program")]),
        _item("d", "w", [("OnlyOnce", "program")]),
    ]
    _linked, index = build_entity_graph(items)
    names = [e.name for e in index]
    assert "OnlyOnce" not in names                # appears once → not connective
    assert names[0] == "F-35"                     # degree 3 ranks first
    assert set(names) == {"F-35", "Lockheed Martin"}


def test_graph_related_ranked_by_shared_count():
    items = [
        _item("a", "a", [("E1", "program"), ("E2", "program")]),
        _item("b", "b", [("E1", "program"), ("E2", "program")]),   # shares 2
        _item("c", "c", [("E1", "program")]),                      # shares 1
    ]
    linked, _ = build_entity_graph(items)
    a = next(it for it in linked if it.item_id == "a")
    assert [r.item_id for r in a.related] == ["b", "c"]


def test_graph_empty_input():
    assert build_entity_graph([]) == ([], [])
