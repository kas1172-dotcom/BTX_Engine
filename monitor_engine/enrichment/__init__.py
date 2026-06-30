"""Cross-API enrichment: look entities up in third-party APIs and attach the
results as structured facts. Generic — all API specifics come from client config.

Public surface:
    Connector            — executes a ConnectorSpec for a single query
    facts_from_records   — maps API records to EnrichmentFact objects
    enrich_items         — pipeline stage: enriches a list of AnalyzedItem
    build_entity_graph   — links items sharing entities (graph edges + index)
"""
from monitor_engine.enrichment.connector import (
    Connector,
    ConnectorError,
    facts_from_records,
    substitute_query,
)
from monitor_engine.enrichment.core import enrich_items
from monitor_engine.enrichment.graph import build_entity_graph

__all__ = [
    "Connector",
    "ConnectorError",
    "facts_from_records",
    "substitute_query",
    "enrich_items",
    "build_entity_graph",
]
