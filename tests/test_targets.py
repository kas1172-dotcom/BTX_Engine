"""Tests for the account-map module: CSV/API sources, fit scoring, the builder,
and the generated artifact. No network."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from monitor_engine.models import (
    AccountMapConfig,
    ApiGeoSource,
    Branding,
    Cadence,
    ClientConfig,
    ClientProfile,
    ConnectorSpec,
    CostCaps,
    CsvGeoSource,
    Edition,
    FactMapping,
    GeoPoint,
    KeywordPrefilter,
    NamedEntities,
    RssSource,
    ScoringRubric,
    TierThresholds,
)
from monitor_engine.targets.build import build_map_data, write_map_site
from monitor_engine.targets.fit import score_fit
from monitor_engine.targets.sources import load_source
from monitor_engine.targets.states import normalize_state, state_centroid


# ─── states ─────────────────────────────────────────────────────────────────

def test_normalize_state_handles_abbr_and_full_name():
    assert normalize_state("tx") == "TX"
    assert normalize_state("Texas") == "TX"
    assert normalize_state("  California ") == "CA"
    assert normalize_state("Atlantis") is None


def test_state_centroid_known_and_unknown():
    assert state_centroid("TX") is not None
    assert state_centroid("Narnia") is None


# ─── fit scoring ────────────────────────────────────────────────────────────

def _profile() -> ClientProfile:
    return ClientProfile(
        capabilities=["precision machining", "5-axis CNC machining", "build-to-print manufacturing"],
        industries_served=["defense", "aerospace"],
        named_entities=NamedEntities(customers=["Lockheed Martin", "RTX"], programs=["F-35", "B-21"]),
    )


def test_fit_customer_and_program_scores_hot():
    from monitor_engine.models import EnrichmentFact
    facts = [EnrichmentFact(enricher_id="x", entity="A", label="Profile",
                            value="F-35 final assembly; build-to-print machining demand", kind="text")]
    score, tier, serve, rationale = score_fit(
        _profile(), name="Lockheed Martin Aeronautics", segment="Prime",
        state_abbr="TX", facts=facts,
    )
    assert tier == "hot"             # customer (Lockheed) + program (F-35) + capability (build-to-print)
    assert score >= 70
    assert "Lockheed Martin" in rationale


def test_fit_capability_match_populates_serve_with():
    from monitor_engine.models import EnrichmentFact
    facts = [EnrichmentFact(enricher_id="x", entity="A", label="Profile",
                            value="5-axis CNC precision machining; build-to-print", kind="text")]
    score, tier, serve, _ = score_fit(
        _profile(), name="Summit CNC", segment="Regional Manufacturer",
        state_abbr="OH", facts=facts,
    )
    assert "precision machining" in serve
    assert "5-axis CNC machining" in serve
    assert score >= 40


def test_fit_no_overlap_is_cool():
    score, tier, serve, _ = score_fit(
        _profile(), name="City Bakery", segment="Food", state_abbr="OH", facts=[],
    )
    assert tier == "cool"
    assert serve == []


def test_fit_without_profile_is_neutral():
    score, tier, serve, _ = score_fit(None, name="X", segment=None, state_abbr=None, facts=[])
    assert (score, tier) == (50, "warm")


# ─── CSV source ─────────────────────────────────────────────────────────────

def _csv(tmp_path: Path, rows: str) -> Path:
    p = tmp_path / "t.csv"
    p.write_text("name,segment,state,lat,lon,desc\n" + rows, encoding="utf-8")
    return p


def test_csv_source_reads_geo_and_facts(tmp_path):
    _csv(tmp_path, "Acme,Prime,TX,32.7,-97.4,5-axis machining\n")
    src = CsvGeoSource(type="csv", id="c", label="C", path="t.csv",
                       fact_columns={"Profile": "desc"})
    out = load_source(src, base_dir=tmp_path, session=MagicMock())
    assert len(out) == 1
    assert out[0].name == "Acme"
    assert out[0].geo == GeoPoint(lat=32.7, lon=-97.4)
    assert out[0].geo_approx is False
    assert out[0].facts[0].value == "5-axis machining"


def test_csv_source_state_centroid_fallback(tmp_path):
    _csv(tmp_path, "NoGeo,Tier 1 Supplier,TX,,,parts\n")
    src = CsvGeoSource(type="csv", id="c", label="C", path="t.csv")
    out = load_source(src, base_dir=tmp_path, session=MagicMock())
    assert out[0].geo is not None
    assert out[0].geo_approx is True


def test_csv_source_skips_blank_names(tmp_path):
    _csv(tmp_path, ",Prime,TX,1,2,x\nReal,Prime,TX,1,2,x\n")
    src = CsvGeoSource(type="csv", id="c", label="C", path="t.csv")
    out = load_source(src, base_dir=tmp_path, session=MagicMock())
    assert [t.name for t in out] == ["Real"]


def test_csv_source_missing_file_raises(tmp_path):
    src = CsvGeoSource(type="csv", id="c", label="C", path="nope.csv")
    with pytest.raises(FileNotFoundError):
        load_source(src, base_dir=tmp_path, session=MagicMock())


# ─── API source ─────────────────────────────────────────────────────────────

def test_api_source_maps_records():
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"results": [
        {"recipient": "RTX", "st": "AZ", "y": 32.2, "x": -110.9, "amt": "$5M"},
    ]}
    resp.raise_for_status.return_value = None
    session.post.return_value = resp
    session.get.return_value = resp
    src = ApiGeoSource(
        type="api", id="usa", label="USA", query="defense",
        connector=ConnectorSpec(url="https://api/?q={query}", item_path="$.results"),
        field_map={"name": "recipient", "state": "st", "lat": "y", "lon": "x"},
        fact_map=[FactMapping(label="Award", field="amt", kind="money")],
    )
    out = load_source(src, base_dir=Path("."), session=session)
    assert out[0].name == "RTX"
    assert out[0].geo == GeoPoint(lat=32.2, lon=-110.9)
    assert out[0].facts[0].label == "Award"


# ─── builder ────────────────────────────────────────────────────────────────

def _config(tmp_path: Path) -> ClientConfig:
    (tmp_path / "t.csv").write_text(
        "name,segment,state,lat,lon,desc\n"
        "Lockheed Martin,Prime,TX,32.7,-97.4,F-35 build-to-print machining\n"
        "Summit CNC,Regional Manufacturer,OH,41.5,-81.7,5-axis precision machining\n"
        "Dupe,Prime,TX,32.7,-97.4,precision machining\n"
        "Dupe,Prime,TX,,,,\n",
        encoding="utf-8",
    )
    return ClientConfig(
        branding=Branding(name="BTX", accent_color="#1E3A8A"),
        editions=[Edition(id="bd", label="BD", audience_description="a",
                          analysis_instructions="i", categories=[])],
        scoring_rubric=ScoringRubric(thresholds=TierThresholds(), never_discard=[]),
        sources=[RssSource(type="rss", id="s", name="F", url="https://example.com/feed")],
        keyword_prefilter=KeywordPrefilter(include=[]),
        cadence=Cadence(cron="0 7 * * 1"), cost_caps=CostCaps(),
        profile=_profile(),
        account_map=AccountMapConfig(
            title="BTX Account Map", segments=["Prime", "Regional Manufacturer"],
            sources=[CsvGeoSource(type="csv", id="c", label="C", path="t.csv",
                                  fact_columns={"Profile": "desc"})],
        ),
    )


def test_build_map_data_scores_and_sorts(tmp_path):
    md = build_map_data(_config(tmp_path), base_dir=tmp_path, session=MagicMock())
    # 4 rows minus one dedup = 3 accounts
    assert len(md.targets) == 3
    # sorted by fit desc → Lockheed (customer+program) first
    assert md.targets[0].name == "Lockheed Martin"
    assert md.targets[0].fit_tier == "hot"
    assert md.config.title == "BTX Account Map"
    assert md.placed_count == 3


def test_build_dedups_and_merges_geo(tmp_path):
    md = build_map_data(_config(tmp_path), base_dir=tmp_path, session=MagicMock())
    dupe = next(t for t in md.targets if t.name == "Dupe")
    # the precise-geo row wins over the geo-less duplicate
    assert dupe.geo is not None


def test_write_map_site_emits_selfcontained_html(tmp_path):
    md = build_map_data(_config(tmp_path), base_dir=tmp_path, session=MagicMock())
    out = tmp_path / "out"
    write_map_site(md, out)
    html = (out / "map.html").read_text()
    assert "PLACEHOLDER" not in html
    assert "leaflet" in html
    data = json.loads((out / "map_targets.json").read_text())
    assert data["config"]["name"] == "BTX"
    assert len(data["targets"]) == 3


def test_required_env_vars_includes_api_geo_source():
    cfg = ClientConfig(
        branding=Branding(name="X", accent_color="#1E3A8A"),
        editions=[Edition(id="bd", label="BD", audience_description="a",
                          analysis_instructions="i", categories=[])],
        scoring_rubric=ScoringRubric(thresholds=TierThresholds(), never_discard=[]),
        sources=[RssSource(type="rss", id="s", name="F", url="https://example.com/feed")],
        keyword_prefilter=KeywordPrefilter(include=[]),
        cadence=Cadence(cron="0 7 * * 1"), cost_caps=CostCaps(),
        account_map=AccountMapConfig(sources=[ApiGeoSource(
            type="api", id="a", label="A", query="q",
            connector=ConnectorSpec(url="https://api/?q={query}", auth_header="X-Key",
                                    auth_env_var="MAP_API_KEY"),
        )]),
    )
    assert "MAP_API_KEY" in cfg.required_env_vars()
