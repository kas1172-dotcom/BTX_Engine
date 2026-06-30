"""Generic parameterized HTTP/JSON connector.

A ConnectorSpec describes a call to an arbitrary third-party API with a single
free variable, ``{query}``, substituted with the entity being looked up. The
same connector backs both the enrichment stage and the agentic ``query_api``
tool, so "integrate any API" is one well-tested code path.

No client/industry knowledge lives here — the URL, body, paths, and field maps
are all config.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import requests

from monitor_engine.analysis.validation import parse_dollar_amount
from monitor_engine.collectors.base import _DEFAULT_TIMEOUT
from monitor_engine.collectors.json_api import _resolve_path
from monitor_engine.models import ConnectorSpec, EnrichmentFact

logger = logging.getLogger(__name__)

_QUERY_TOKEN = "{query}"


class ConnectorError(Exception):
    """Raised when a connector call fails (network, status, or shape)."""


def substitute_query(value: Any, query: str, *, url_encode: bool) -> Any:
    """Recursively replace the literal ``{query}`` token in *value*.

    Strings are substituted; dicts/lists are walked. In URLs the query is
    URL-encoded; in JSON bodies it is inserted raw (the API encodes it itself).
    """
    if isinstance(value, str):
        if _QUERY_TOKEN not in value:
            return value
        return value.replace(_QUERY_TOKEN, quote(query, safe="") if url_encode else query)
    if isinstance(value, dict):
        return {k: substitute_query(v, query, url_encode=url_encode) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_query(v, query, url_encode=url_encode) for v in value]
    return value


def _get_field(record: Any, path: str) -> Any:
    """Safe dot-path getter into a record. Returns None on any miss rather than
    raising, so one odd record never aborts an enrichment."""
    try:
        cur = record
        for key in path.lstrip("$.").split("."):
            if isinstance(cur, dict):
                cur = cur[key]
            elif isinstance(cur, list):
                cur = cur[int(key)]
            else:
                return None
        return cur
    except (KeyError, IndexError, ValueError, TypeError):
        return None


class Connector:
    """Executes a ConnectorSpec for a single query string."""

    def __init__(self, session: requests.Session) -> None:
        self.session = session

    def fetch(self, spec: ConnectorSpec, query: str) -> list[dict]:
        """Call the API for *query* and return the result records (always a list).

        Raises ConnectorError on transport, status, auth, or shape failure so the
        caller can record the failure per-entity without aborting the run.
        """
        url = substitute_query(spec.url, query, url_encode=True)
        headers: dict[str, str] = {}
        if spec.user_agent:
            headers["User-Agent"] = spec.user_agent
        if spec.auth_env_var and spec.auth_header:
            secret = os.environ.get(spec.auth_env_var)
            if not secret:
                raise ConnectorError(f"missing env var {spec.auth_env_var}")
            headers[spec.auth_header] = secret

        timeout = spec.timeout if spec.timeout is not None else _DEFAULT_TIMEOUT
        try:
            if spec.method == "POST":
                body = substitute_query(spec.request_body or {}, query, url_encode=False)
                resp = self.session.post(url, headers=headers, json=body, timeout=timeout)
            else:
                resp = self.session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise ConnectorError(str(exc)) from exc
        except ValueError as exc:  # JSON decode
            raise ConnectorError(f"invalid JSON: {exc}") from exc

        records = _resolve_path(data, spec.item_path)
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise ConnectorError(
                f"item_path {spec.item_path!r} resolved to {type(records).__name__}, expected list"
            )
        return records


def _coerce_fact_value(kind: str, raw: Any) -> tuple[str, float | None]:
    """Render a raw field value as (display_string, number). ``number`` is set
    only for money/number kinds when a numeric could be parsed."""
    text = str(raw).strip()
    number: float | None = None
    if kind == "money":
        value, _currency = parse_dollar_amount(text)
        number = value
    elif kind == "number":
        try:
            number = float(str(raw).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            number = None
    return text, number


def facts_from_records(
    spec: ConnectorSpec,
    records: list[dict],
    *,
    query: str,
    enricher_id: str,
) -> list[EnrichmentFact]:
    """Map API *records* to EnrichmentFacts using ``spec.fact_map``.

    Reads from the first records until ``spec.max_facts`` facts are collected.
    Fields that are absent/empty in a record are skipped (an enrichment shows
    only the facts that actually exist).
    """
    facts: list[EnrichmentFact] = []
    for record in records:
        if len(facts) >= spec.max_facts:
            break
        link = _get_field(record, spec.url_field) if spec.url_field else None
        link_str = str(link) if isinstance(link, str) and link.startswith("http") else None
        for fm in spec.fact_map:
            if len(facts) >= spec.max_facts:
                break
            raw = _get_field(record, fm.field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            value, number = _coerce_fact_value(fm.kind, raw)
            if not value:
                continue
            facts.append(
                EnrichmentFact(
                    enricher_id=enricher_id,
                    entity=query,
                    label=fm.label,
                    value=value,
                    kind=fm.kind,
                    number=number,
                    url=(value if fm.kind == "url" and value.startswith("http") else link_str),
                )
            )
    return facts
