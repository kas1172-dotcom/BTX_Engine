"""Agentic research for deep analysis.

When ``deep_analysis.agentic`` is on, top-tier items get a tool-using research
pass before the deep-analysis write-up: Claude decides what it needs to know and
calls live tools to find out —

  * ``query_api``  — run any configured enricher connector with a free query
                     (entities discovered in one source resolved against others);
  * ``fetch_url``  — pull the text of a web page (the item's own source, or a
                     link surfaced while researching).

The loop is bounded by ``max_research_steps`` and fails soft: if research errors
or is disabled, the item simply gets the standard (non-agentic) deep analysis.
The output is a research note + the list of sources consulted, which the
deep-analysis prompt then folds in and cites.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from monitor_engine.collectors.base import _DEFAULT_TIMEOUT
from monitor_engine.enrichment.connector import Connector, ConnectorError
from monitor_engine.models import AnalyzedItem, ClientConfig, RawItem

logger = logging.getLogger(__name__)

RESEARCH_MAX_TOKENS = 2048
_FETCH_MAX_CHARS = 4000
_TOOL_RESULT_MAX_CHARS = 4000
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


@dataclass
class ResearchOutput:
    """Result of one item's research pass."""
    notes: str = ""
    sources: list[str] = field(default_factory=list)
    steps: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.notes.strip()


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _tool_defs(config: ClientConfig, *, allow_fetch: bool) -> list[dict]:
    enricher_lines = "\n".join(
        f"  - {e.id}: {e.label}" for e in config.enrichers
    ) or "  (none configured)"
    tools: list[dict] = [
        {
            "name": "query_api",
            "description": (
                "Look an entity up in a configured external API and get back JSON "
                "records. Available APIs (enricher_id: what it returns):\n"
                + enricher_lines
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "enricher_id": {"type": "string", "description": "one of the ids listed above"},
                    "query": {"type": "string", "description": "the entity name to look up"},
                },
                "required": ["enricher_id", "query"],
            },
        }
    ]
    if allow_fetch:
        tools.append(
            {
                "name": "fetch_url",
                "description": (
                    "Fetch the readable text of an http(s) web page (e.g. the item's "
                    "source article or a document it links to)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            }
        )
    return tools


class ResearchAgent:
    """Runs the bounded tool-use loop for a single item. The Anthropic client and
    cost callback are injected so this shares the Scorer's client and accounting."""

    def __init__(
        self,
        client: Any,
        config: ClientConfig,
        *,
        model: str,
        session: requests.Session,
        on_usage: Callable[[Any], None] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._model = model
        self._connector = Connector(session)
        self._session = session
        self._on_usage = on_usage or (lambda usage: None)
        self._enrichers = {e.id: e for e in config.enrichers}

    # ── tools ────────────────────────────────────────────────────────────────
    def _exec_query_api(self, args: dict) -> str:
        enricher = self._enrichers.get(args.get("enricher_id", ""))
        query = (args.get("query") or "").strip()
        if enricher is None:
            return f"error: unknown enricher_id {args.get('enricher_id')!r}"
        if not query:
            return "error: empty query"
        try:
            records = self._connector.fetch(enricher.connector, query)
        except ConnectorError as exc:
            return f"error: {exc}"
        return json.dumps(records[:5])[:_TOOL_RESULT_MAX_CHARS]

    def _exec_fetch_url(self, args: dict, sources: list[str]) -> str:
        url = (args.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            return "error: only http(s) URLs can be fetched"
        try:
            resp = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            return f"error: {exc}"
        if url not in sources:
            sources.append(url)
        return _strip_html(resp.text)[:_FETCH_MAX_CHARS]

    # ── loop ─────────────────────────────────────────────────────────────────
    def research(self, item: AnalyzedItem, raw: RawItem | None) -> ResearchOutput:
        da = self._config.deep_analysis
        assert da is not None
        tools = _tool_defs(self._config, allow_fetch=da.allow_fetch)
        sources: list[str] = []

        entity_hint = ", ".join(e.name for e in item.entities[:6]) or "(none extracted)"
        system = (
            "You are an intelligence analyst doing pre-writing research on a single "
            "item. Use the tools to gather concrete, verifiable detail (figures, "
            "agencies, dates, prior context). Make at most a few targeted calls, then "
            "STOP and reply with a tight factual brief of what you found — bullet "
            "points, each grounded in a tool result. Do not speculate; if a lookup "
            "returns nothing, say so. Do not write the final analysis, only the notes."
        )
        user = (
            f"ITEM: {item.title}\n"
            f"SOURCE URL: {item.url}\n"
            f"KNOWN ENTITIES: {entity_hint}\n"
        )
        if raw and raw.summary:
            user += f"SUMMARY: {raw.summary[:600]}\n"
        user += "\nResearch this item, then reply with your factual notes."

        messages: list[dict] = [{"role": "user", "content": user}]
        steps = 0
        final_text = ""
        for _ in range(max(1, da.max_research_steps)):
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=RESEARCH_MAX_TOKENS,
                    system=system,
                    messages=messages,
                    tools=tools,
                )
            except Exception as exc:  # noqa: BLE001 — research is best-effort
                logger.warning("Research call failed for %s: %s", item.item_id, exc)
                break
            self._on_usage(resp.usage)
            steps += 1

            blocks = list(resp.content)
            text_parts = [b.text for b in blocks if getattr(b, "type", None) == "text"]
            if text_parts:
                final_text = "\n".join(text_parts).strip()
            tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            if getattr(resp, "stop_reason", None) != "tool_use" or not tool_uses:
                break

            messages.append({"role": "assistant", "content": blocks})
            tool_results = []
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else {}
                if tu.name == "query_api":
                    result = self._exec_query_api(args)
                elif tu.name == "fetch_url":
                    result = self._exec_fetch_url(args, sources)
                else:
                    result = f"error: unknown tool {tu.name}"
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})

        return ResearchOutput(notes=final_text, sources=sources, steps=steps)
