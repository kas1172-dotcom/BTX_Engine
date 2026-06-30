"""Build the account-map artifact + page.

Pulls accounts from every configured source, scores each for fit against the
client profile, and writes:
  map_targets.json  — MapData artifact (the contract the page reads)
  map.html          — self-contained interactive map (CSS+JS inlined; Leaflet
                      from CDN; tiles from OpenStreetMap)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from monitor_engine.collectors.base import make_session
from monitor_engine.models import (
    ClientConfig,
    MapConfigInfo,
    MapData,
    TargetAccount,
)
from monitor_engine.targets.fit import score_fit
from monitor_engine.targets.sources import RawTarget, load_source
from monitor_engine.targets.states import normalize_state

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).parent / "_assets"
_STYLE_MARKER = "/* STYLE_PLACEHOLDER */"
_SCRIPT_MARKER = "/* SCRIPT_PLACEHOLDER */"
_DATA_MARKER = "DATA_FILENAME_PLACEHOLDER"


def _account_id(name: str, state: str | None) -> str:
    return hashlib.sha256(f"{name.lower()}:{(state or '').lower()}".encode()).hexdigest()[:12]


def _dedup(raws: list[RawTarget]) -> list[RawTarget]:
    """Collapse the same account seen from multiple sources, merging facts and
    preferring a precise (non-approx) geo."""
    by_key: dict[str, RawTarget] = {}
    for r in raws:
        key = f"{r.name.lower()}|{(r.state or '').lower()}"
        if key not in by_key:
            by_key[key] = r
            continue
        existing = by_key[key]
        existing.facts.extend(r.facts)
        if existing.geo is None or (existing.geo_approx and r.geo is not None and not r.geo_approx):
            existing.geo, existing.geo_approx = r.geo, r.geo_approx
        existing.url = existing.url or r.url
        existing.segment = existing.segment or r.segment
        existing.city = existing.city or r.city
    return list(by_key.values())


def build_map_data(
    config: ClientConfig,
    *,
    base_dir: Path,
    session: requests.Session | None = None,
) -> MapData:
    """Assemble the MapData artifact from the client's account_map config."""
    am = config.account_map
    if am is None:
        raise ValueError("build_map_data called without an account_map config block")

    session = session or make_session()
    raws: list[RawTarget] = []
    for source in am.sources:
        loaded = load_source(source, base_dir=base_dir, session=session)
        logger.info("Account source %s: %d account(s)", source.id, len(loaded))
        raws.extend(loaded)

    targets: list[TargetAccount] = []
    for r in _dedup(raws):
        score, tier, serve_with, rationale = score_fit(
            config.profile, name=r.name, segment=r.segment,
            state_abbr=normalize_state(r.state), facts=r.facts,
        )
        targets.append(TargetAccount(
            id=_account_id(r.name, r.state),
            name=r.name, segment=r.segment, city=r.city, state=r.state,
            geo=r.geo, geo_approx=r.geo_approx, facts=r.facts,
            fit_score=score, fit_tier=tier, fit_rationale=rationale,
            serve_with=serve_with, source_id=r.source_id, url=r.url,
        ))

    targets.sort(key=lambda t: (-t.fit_score, t.name.lower()))

    info = MapConfigInfo(
        title=am.title, region=am.region, center=am.center, zoom=am.zoom,
        segments=am.segments, accent_color=config.branding.accent_color,
        name=config.branding.name,
        capabilities=(config.profile.capabilities if config.profile else []),
    )
    return MapData(generated_at=datetime.now(timezone.utc), config=info, targets=targets)


def write_map_site(
    map_data: MapData, output_dir: Path, *, data_filename: str = "map_targets.json"
) -> None:
    """Write map_targets.json + a self-contained map.html into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / data_filename).write_text(map_data.model_dump_json(indent=2), encoding="utf-8")

    css = (_ASSETS / "map.css").read_text(encoding="utf-8")
    js = (_ASSETS / "map.js").read_text(encoding="utf-8")
    html = (
        (_ASSETS / "map.html").read_text(encoding="utf-8")
        .replace(_STYLE_MARKER, css, 1)
        .replace(_SCRIPT_MARKER, js, 1)
        .replace(_DATA_MARKER, data_filename, 1)
    )
    (output_dir / "map.html").write_text(html, encoding="utf-8")
