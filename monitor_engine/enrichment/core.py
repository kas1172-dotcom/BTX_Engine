"""Enrichment stage: cross-reference each item's entities against external APIs.

For every configured enricher, the distinct matching entities across the run are
looked up once each (parallel, fail-soft, capped), and the resulting facts are
attached to every item that named that entity. This is the "integrate other APIs
and let them talk to each other" core — entities discovered in one source are
resolved against a second, third, … API and folded back onto the item.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from monitor_engine.collectors.base import make_session
from monitor_engine.enrichment.connector import (
    Connector,
    ConnectorError,
    facts_from_records,
)
from monitor_engine.models import (
    AnalyzedItem,
    ClientConfig,
    EnricherConfig,
    EnrichmentFact,
    ItemEnrichment,
)

logger = logging.getLogger(__name__)

ENRICH_MAX_WORKERS = 4


@dataclass
class EnricherStats:
    enricher_id: str
    entities_queried: int = 0
    facts_added: int = 0
    errors: int = 0


@dataclass
class EnrichmentResult:
    items: list[AnalyzedItem]
    stats: dict[str, EnricherStats] = field(default_factory=dict)

    @property
    def total_facts(self) -> int:
        return sum(s.facts_added for s in self.stats.values())

    @property
    def total_errors(self) -> int:
        return sum(s.errors for s in self.stats.values())


def _matching_entities(
    items: list[AnalyzedItem], enricher: EnricherConfig
) -> dict[str, tuple[str, list[str]]]:
    """Group entities (case-insensitively) that this enricher applies to.

    Returns ``{lower_name: (display_name, [item_id, ...])}`` in first-seen order,
    so the same entity named by several items is looked up once.
    """
    types = {t.strip().lower() for t in enricher.applies_to_entity_types}
    groups: dict[str, tuple[str, list[str]]] = {}
    for item in items:
        for ent in item.entities:
            if types and ent.type not in types:
                continue
            key = ent.name.lower()
            if key not in groups:
                groups[key] = (ent.name, [])
            groups[key][1].append(item.item_id)
    return groups


def _run_one_enricher(
    enricher: EnricherConfig,
    items: list[AnalyzedItem],
    connector: Connector,
    item_facts: dict[str, list[EnrichmentFact]],
    item_queried: dict[str, set[str]],
    max_workers: int,
) -> EnricherStats:
    stats = EnricherStats(enricher_id=enricher.id)
    groups = _matching_entities(items, enricher)
    selected = list(groups.items())[: enricher.max_entities_per_run]
    if not selected:
        return stats

    for _key, (display, item_ids) in selected:
        for iid in item_ids:
            item_queried[iid].add(display)
    stats.entities_queried = len(selected)

    def _lookup(display: str) -> tuple[str, list[EnrichmentFact] | ConnectorError]:
        try:
            records = connector.fetch(enricher.connector, display)
            return display, facts_from_records(
                enricher.connector, records, query=display, enricher_id=enricher.id
            )
        except ConnectorError as exc:
            return display, exc

    displays = [display for _key, (display, _ids) in selected]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(displays))) as pool:
        results = list(pool.map(_lookup, displays))

    facts_by_display = {d: r for d, r in results}
    for _key, (display, item_ids) in selected:
        outcome = facts_by_display.get(display)
        if isinstance(outcome, ConnectorError):
            stats.errors += 1
            logger.warning("Enricher %s failed for %r: %s", enricher.id, display, outcome)
            continue
        if not outcome:
            continue
        for iid in item_ids:
            item_facts[iid].extend(outcome)
            stats.facts_added += len(outcome)
    return stats


def enrich_items(
    config: ClientConfig,
    items: list[AnalyzedItem],
    *,
    session: requests.Session | None = None,
    max_workers: int = ENRICH_MAX_WORKERS,
) -> EnrichmentResult:
    """Attach cross-API facts to *items*. No-op (returns items unchanged) when no
    enrichers are configured or no items carry matching entities."""
    if not config.enrichers or not items:
        return EnrichmentResult(items=items)

    session = session or make_session()
    connector = Connector(session)

    item_facts: dict[str, list[EnrichmentFact]] = {it.item_id: [] for it in items}
    item_queried: dict[str, set[str]] = {it.item_id: set() for it in items}

    stats: dict[str, EnricherStats] = {}
    for enricher in config.enrichers:
        stats[enricher.id] = _run_one_enricher(
            enricher, items, connector, item_facts, item_queried, max_workers
        )

    updated: list[AnalyzedItem] = []
    for it in items:
        facts = item_facts[it.item_id]
        queried = item_queried[it.item_id]
        if not facts and not queried:
            updated.append(it)
            continue
        updated.append(
            it.model_copy(
                update={
                    "enrichment": ItemEnrichment(
                        facts=facts, queried_entities=sorted(queried)
                    )
                }
            )
        )
    return EnrichmentResult(items=updated, stats=stats)
