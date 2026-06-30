"""Entity graph: link items that share named entities.

Deterministic, network-free. Two outputs:
  * ``related`` edges attached to each AnalyzedItem (other items sharing an
    entity, with the shared names in ``via``), so a card can say "this connects
    to N other stories";
  * a run-level ``entity_index`` of connective entities (those appearing in 2+
    items) powering the site's entity explorer.
"""
from __future__ import annotations

from monitor_engine.models import AnalyzedItem, EntityIndexEntry, RelatedRef

MAX_RELATED_PER_ITEM = 6


def _entity_map(items: list[AnalyzedItem]) -> dict[str, dict]:
    """``{lower_name: {"display", "type", "item_ids"}}`` in first-seen order."""
    out: dict[str, dict] = {}
    for item in items:
        seen_in_item: set[str] = set()
        for ent in item.entities:
            key = ent.name.lower()
            if key in seen_in_item:
                continue
            seen_in_item.add(key)
            node = out.setdefault(key, {"display": ent.name, "type": ent.type, "item_ids": []})
            node["item_ids"].append(item.item_id)
    return out


def build_entity_graph(
    items: list[AnalyzedItem],
) -> tuple[list[AnalyzedItem], list[EntityIndexEntry]]:
    """Return (items with ``related`` populated, entity_index).

    Items are matched by shared entity names. ``related`` is ranked by the number
    of shared entities (then by recency-stable input order) and capped.
    """
    if not items:
        return items, []

    emap = _entity_map(items)
    by_id = {it.item_id: it for it in items}

    # item_id -> {other_item_id -> [shared display names]}
    shared: dict[str, dict[str, list[str]]] = {it.item_id: {} for it in items}
    for node in emap.values():
        ids = node["item_ids"]
        if len(ids) < 2:
            continue
        display = node["display"]
        for a in ids:
            for b in ids:
                if a == b:
                    continue
                shared[a].setdefault(b, [])
                if display not in shared[a][b]:
                    shared[a][b].append(display)

    updated: list[AnalyzedItem] = []
    for it in items:
        edges = shared[it.item_id]
        if not edges:
            updated.append(it)
            continue
        ranked = sorted(edges.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        related = [
            RelatedRef(
                item_id=other_id,
                title=by_id[other_id].title,
                url=by_id[other_id].url,
                via=via,
            )
            for other_id, via in ranked[:MAX_RELATED_PER_ITEM]
        ]
        updated.append(it.model_copy(update={"related": related}))

    index = [
        EntityIndexEntry(name=node["display"], type=node["type"], item_ids=node["item_ids"])
        for node in emap.values()
        if len(node["item_ids"]) >= 2
    ]
    index.sort(key=lambda e: (-len(e.item_ids), e.name.lower()))
    return updated, index
