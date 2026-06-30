"""Account sources for the map. Pluggable: a committed CSV (your target list)
or any API via the generic connector. New source types are added here in one
place; the builder and config discriminate on ``type``.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

import requests

from monitor_engine.enrichment.connector import Connector, ConnectorError, _get_field
from monitor_engine.models import (
    ApiGeoSource,
    CsvGeoSource,
    EnrichmentFact,
    GeoPoint,
    GeoSource,
)
from monitor_engine.targets.states import normalize_state, state_centroid

logger = logging.getLogger(__name__)


@dataclass
class RawTarget:
    """A located account before fit scoring."""
    name: str
    source_id: str
    segment: str | None = None
    city: str | None = None
    state: str | None = None
    geo: GeoPoint | None = None
    geo_approx: bool = False
    url: str | None = None
    facts: list[EnrichmentFact] = field(default_factory=list)


def _to_float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_geo(
    lat, lon, state: str | None
) -> tuple[GeoPoint | None, bool]:
    """Prefer explicit lat/lon; fall back to the state centroid (approx)."""
    flat, flon = _to_float(lat), _to_float(lon)
    if flat is not None and flon is not None:
        return GeoPoint(lat=flat, lon=flon), False
    centroid = state_centroid(state)
    if centroid is not None:
        return GeoPoint(lat=centroid[0], lon=centroid[1]), True
    return None, False


def _load_csv(source: CsvGeoSource, base_dir: Path) -> list[RawTarget]:
    fm = source.field_map
    path = (base_dir / source.path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"CSV source {source.id!r}: file not found: {path}")

    out: list[RawTarget] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get(fm.get("name", "name")) or "").strip()
            if not name:
                continue
            state = normalize_state(row.get(fm.get("state", "state")))
            geo, approx = _resolve_geo(
                row.get(fm.get("lat", "lat")), row.get(fm.get("lon", "lon")), state
            )
            facts = [
                EnrichmentFact(
                    enricher_id=source.id, entity=name, label=label,
                    value=str(row[col]).strip(), kind="text",
                )
                for label, col in source.fact_columns.items()
                if row.get(col) and str(row[col]).strip()
            ]
            out.append(RawTarget(
                name=name, source_id=source.id,
                segment=(row.get(fm.get("segment", "segment")) or "").strip() or None,
                city=(row.get(fm.get("city", "city")) or "").strip() or None,
                state=state, geo=geo, geo_approx=approx,
                url=(row.get(fm.get("url", "url")) or "").strip() or None,
                facts=facts,
            ))
    return out


def _load_api(source: ApiGeoSource, session: requests.Session) -> list[RawTarget]:
    fm = source.field_map
    try:
        records = Connector(session).fetch(source.connector, source.query)
    except ConnectorError as exc:
        logger.warning("API source %s failed: %s", source.id, exc)
        return []

    out: list[RawTarget] = []
    for rec in records[: source.max_accounts]:
        name = _get_field(rec, fm.get("name", "name"))
        if not name:
            continue
        name = str(name).strip()
        state = normalize_state(_get_field(rec, fm.get("state", "state")))
        geo, approx = _resolve_geo(
            _get_field(rec, fm.get("lat", "lat")),
            _get_field(rec, fm.get("lon", "lon")),
            state,
        )
        facts: list[EnrichmentFact] = []
        for mapping in source.fact_map:
            raw = _get_field(rec, mapping.field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            facts.append(EnrichmentFact(
                enricher_id=source.id, entity=name, label=mapping.label,
                value=str(raw).strip(), kind=mapping.kind,
            ))
        url = _get_field(rec, fm["url"]) if fm.get("url") else None
        out.append(RawTarget(
            name=name, source_id=source.id,
            segment=(str(_get_field(rec, fm["segment"])).strip()
                     if fm.get("segment") and _get_field(rec, fm["segment"]) else None),
            city=(str(_get_field(rec, fm["city"])).strip()
                  if fm.get("city") and _get_field(rec, fm["city"]) else None),
            state=state, geo=geo, geo_approx=approx,
            url=str(url).strip() if isinstance(url, str) else None,
            facts=facts,
        ))
    return out


def load_source(
    source: GeoSource, *, base_dir: Path, session: requests.Session
) -> list[RawTarget]:
    if isinstance(source, CsvGeoSource):
        return _load_csv(source, base_dir)
    if isinstance(source, ApiGeoSource):
        return _load_api(source, session)
    raise TypeError(f"unknown geo source type: {type(source).__name__}")
