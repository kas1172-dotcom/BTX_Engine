"""Account fit scoring.

Deterministic, rule-based match of a potential account against the client
profile: which of the client's capabilities map to the account, plus its
named customers/programs and geographic focus. No LLM, no network — so the map
is reproducible and auditable in git, like the rest of the engine.

Generic: reads only ClientProfile (client specifics live in config), never any
hard-coded industry term.
"""
from __future__ import annotations

import re

from monitor_engine.models import ClientProfile, EnrichmentFact

_TIER_HOT = 70
_TIER_WARM = 40
_STOPWORDS = {"and", "of", "the", "for", "to", "a", "manufacturing", "services", "inc", "llc", "corp"}


def _significant_words(phrase: str) -> list[str]:
    words = re.findall(r"[a-z0-9\-]+", phrase.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _phrase_matches(phrase: str, haystack: str) -> bool:
    """A capability/term matches if its full phrase appears, or (for multi-word
    phrases) all of its significant words appear somewhere in the haystack."""
    p = phrase.lower().strip()
    if not p:
        return False
    if p in haystack:
        return True
    words = _significant_words(p)
    return bool(words) and all(w in haystack for w in words)


def _account_text(name: str, segment: str | None, facts: list[EnrichmentFact]) -> str:
    parts = [name, segment or ""]
    parts += [f"{f.label} {f.value}" for f in facts]
    return " ".join(parts).lower()


def score_fit(
    profile: ClientProfile | None,
    *,
    name: str,
    segment: str | None,
    state_abbr: str | None,
    facts: list[EnrichmentFact],
) -> tuple[int, str, list[str], str]:
    """Return (score 0–100, tier, serve_with, rationale).

    Without a profile, everything is a neutral "warm" lead (no basis to rank).
    """
    if profile is None:
        return 50, "warm", [], "No client profile configured — shown as a neutral lead."

    hay = _account_text(name, segment, facts)
    serve_with = [cap for cap in profile.capabilities if _phrase_matches(cap, hay)]
    program_hits = [p for p in profile.named_entities.programs if _phrase_matches(p, hay)]
    customer_hits = [c for c in profile.named_entities.customers if _phrase_matches(c, hay)]
    industry_hits = [i for i in profile.industries_served if _phrase_matches(i, hay)]

    score = 20
    score += 18 * len(serve_with)
    score += 15 * len(program_hits)
    score += 22 * len(customer_hits)
    score += 8 * len(industry_hits)

    in_focus = False
    if state_abbr and profile.geographic_focus:
        # geographic_focus is usually country-level ("United States"); a state match
        # is a light positive signal, not a strong one.
        in_focus = any(_phrase_matches(g, (segment or "")) for g in profile.geographic_focus)
    if in_focus:
        score += 5

    score = max(0, min(100, score))
    tier = "hot" if score >= _TIER_HOT else "warm" if score >= _TIER_WARM else "cool"

    bits: list[str] = []
    if customer_hits:
        bits.append("a known customer/prime (" + ", ".join(customer_hits) + ")")
    if program_hits:
        bits.append("active on " + ", ".join(program_hits))
    if serve_with:
        bits.append("served by " + ", ".join(serve_with))
    elif industry_hits:
        bits.append("in a served industry (" + ", ".join(industry_hits) + ")")
    rationale = (
        ("Strong fit — " if tier == "hot" else "Possible fit — " if tier == "warm" else "Low signal — ")
        + ("; ".join(bits) if bits else "no profile overlap detected")
        + "."
    )
    return score, tier, serve_with, rationale
