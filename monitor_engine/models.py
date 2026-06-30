from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, HttpUrl, computed_field, model_validator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COLLECTION — normalized item produced by any source handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RawItem(BaseModel):
    id: str                                          # stable hash of (source_id, url)
    title: str
    summary: str | None
    url: str
    published_date: datetime | None
    date_unknown: bool = False                       # True when the source carries no date
    discovery_date: datetime                         # wall-clock time the engine saw this item
    source_name: str
    source_type: Literal["rss", "json_api", "html_list"]

    @model_validator(mode="after")
    def _date_consistency(self) -> "RawItem":
        if self.published_date is None and not self.date_unknown:
            raise ValueError(
                "published_date is None but date_unknown is False; "
                "set date_unknown=True when no date is available from the source"
            )
        return self


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COLLECTION HEALTH — produced by collect_all, feeds into RunMeta
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SourceHealth(BaseModel):
    source_id: str
    items_collected: int
    zero_results: bool
    error: str | None = None           # set when the handler raised an exception
    date_parse_failures: int = 0       # items where a date string existed but could not be parsed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLIENT CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class Branding(BaseModel):
    name: str
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class Edition(BaseModel):
    id: str
    label: str
    audience_description: str
    analysis_instructions: str
    categories: list[str]


class TierThresholds(BaseModel):
    tier_1_min: int = 80
    tier_2_min: int = 50
    tier_3_min: int = 20          # below this → discard (unless never_discard matches)


class ScoringRubric(BaseModel):
    thresholds: TierThresholds
    never_discard: list[str]      # keyword patterns; any match → item survives regardless of score


class RssSource(BaseModel):
    type: Literal["rss"]
    id: str
    name: str
    url: HttpUrl
    timeout: int | None = None      # per-source HTTP timeout in seconds; falls back to global default
    days_back: int | None = None    # per-source lookback window; falls back to global
    user_agent: str | None = None   # override the session's default User-Agent for this source


class JsonApiSource(BaseModel):
    type: Literal["json_api"]
    id: str
    name: str
    url: HttpUrl
    item_path: str                          # dot-notation path to item array, e.g. "$.opportunitiesData"
    field_map: dict[str, str]               # engine field name → API response field name
    url_template: str | None = None         # build item URL from record fields, e.g.
                                            # "https://host/page?ID={k_number}"; takes precedence
                                            # over field_map["url"]. Items missing a referenced
                                            # field are skipped.
    url_template_map: dict[str, dict[str, str]] | None = None
                                            # per-field value translation for url_template, keyed
                                            # by field name, e.g. {"type": {"HR": "house-bill"}}.
                                            # A field value absent from its map skips the item.
    base_url: str | None = None             # resolve relative item URLs (e.g. "/opinion/1/") against this
    auth_header: str | None = None
    auth_env_var: str | None = None
    method: Literal["GET", "POST"] = Field(
        default="GET",
        description="HTTP method. Use POST for search APIs that take a query body; "
                    "request_body is then sent as JSON.",
    )
    request_body: dict | None = Field(
        default=None,
        description="JSON payload sent with POST requests (ignored for GET).",
    )
    timeout: int | None = None
    days_back: int | None = None
    user_agent: str | None = None           # override the session's default User-Agent for this source


class HtmlListSource(BaseModel):
    type: Literal["html_list"]
    id: str
    name: str
    url: HttpUrl
    item_selector: str
    title_selector: str
    link_selector: str
    date_selector: str | None = None
    timeout: int | None = None
    days_back: int | None = None
    user_agent: str | None = None   # override the session's default User-Agent for this source


Source = Annotated[
    Union[RssSource, JsonApiSource, HtmlListSource],
    Field(discriminator="type"),
]


class KeywordPrefilter(BaseModel):
    include: list[str]            # OR logic: any match passes
    exclude: list[str] = []       # any match drops the item, overrides include


class Cadence(BaseModel):
    cron: str                     # standard 5-field cron expression
    timezone: str = "UTC"


class CostCaps(BaseModel):
    max_items_per_run: int = 50
    max_output_tokens_per_run: int = 8000


class DeepAnalysisSection(BaseModel):
    id: str
    label: str                                # display label shown in the UI
    kind: Literal["text", "list"] = "text"    # "text" → string; "list" → list of strings
    instruction: str                          # drives what the LLM writes for this section


class DeepAnalysisConfig(BaseModel):
    """Config-driven definition of per-item in-depth analysis.

    The engine builds the deep-analysis prompt and response schema entirely
    from this block, so changing what "in-depth" means is a config edit.
    """
    instruction: str                          # overall framing for the analyst
    sections: list[DeepAnalysisSection]
    deep_batch_size: int = 3                   # items per deep-analysis call; kept small
                                               # (independent of classification batch size)
                                               # because per-item deep output is large and
                                               # big batches truncate at the token ceiling
    agentic: bool = False                      # when True, top-tier items get a tool-using
                                               # research pass (fetch_url + query_api) before
                                               # the deep-analysis call; default off = unchanged
    agentic_tiers: list[int] = [1]             # which tiers get the agentic research pass
    max_research_steps: int = 4                # cap on tool-call rounds per researched item
    allow_fetch: bool = True                   # expose the fetch_url tool to the research agent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENRICHMENT CONFIG — generic cross-API connectors
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# An enricher takes entities the analyst extracted from an item and looks each
# one up in an external API, attaching structured facts back to the item. The
# engine stays generic: which APIs, which fields, and which entity types are all
# config. The same connector shape powers the agentic research `query_api` tool.


class FactMapping(BaseModel):
    """Map one field of an API response record to a displayed fact."""
    label: str                                # human label, e.g. "Total obligated"
    field: str                                # dot-path into the record, e.g. "Award Amount"
    kind: Literal["text", "money", "number", "date", "url"] = "text"


class ConnectorSpec(BaseModel):
    """A parameterized HTTP/JSON call to a third-party API. The literal token
    ``{query}`` anywhere in ``url`` or ``request_body`` is replaced with the
    entity name being looked up (URL-encoded inside ``url``)."""
    url: str                                   # may contain {query}
    method: Literal["GET", "POST"] = "GET"
    request_body: dict | None = None           # POST payload; {query} substituted recursively
    item_path: str = "$"                        # dot-path to the result array (or "$" for root list)
    fact_map: list[FactMapping] = []           # fields to surface as facts
    url_field: str | None = None               # record field holding a canonical link for the fact
    max_facts: int = 3                          # cap facts taken from the first matching record(s)
    auth_header: str | None = None
    auth_env_var: str | None = None
    timeout: int | None = None
    user_agent: str | None = None


class EnricherConfig(BaseModel):
    """One enrichment source: for each matching entity on an item, query an API
    and attach facts. Generic — the client/industry specifics live in the
    client config, never in engine code."""
    id: str
    label: str
    applies_to_entity_types: list[str] = []    # entity types that trigger this; empty = all types
    max_entities_per_run: int = 8              # cap distinct entities looked up across the run
    connector: ConnectorSpec


class NamedEntities(BaseModel):
    """Names that matter to this client, grouped by relationship. Consumed by the
    analysis prompt so 'why it matters' can reference real customers, rivals,
    agencies, and programs by name. All optional; empty groups are simply omitted."""
    customers: list[str] = []
    competitors: list[str] = []
    agencies: list[str] = []
    programs: list[str] = []


class ClientProfile(BaseModel):
    """Structured description of the client the monitor serves. This is config
    CONTENT consumed by the analysis prompt — not a database — so each item's
    so-what/now-what speaks to the client's actual capabilities and goals rather
    than generically. Every field is optional; the prompt only emits the parts
    that are present. Do not infer facts beyond what is stated here."""
    capabilities: list[str] = []          # what the client does / makes / offers
    certifications: list[str] = []        # e.g. AS9100, ITAR, ISO 13485
    industries_served: list[str] = []
    customer_types: list[str] = []        # e.g. "defense primes", "Tier 1 suppliers"
    geographic_focus: list[str] = []
    strategic_goals: list[str] = []       # what the client is trying to achieve
    risks: list[str] = []                 # what the client is exposed to / worried about
    named_entities: NamedEntities = Field(default_factory=NamedEntities)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ACCOUNT MAP CONFIG — geographic view of potential accounts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A revenue/BD view: pull a list of potential accounts from one or more sources,
# place them on a map, and score each for fit against the client profile. Generic
# — which sources, regions, and columns are all config; client specifics live in
# the client config + its data files, never in engine code.


class GeoPoint(BaseModel):
    lat: float
    lon: float


class CsvGeoSource(BaseModel):
    """Accounts from a committed CSV (e.g. the team's target-account list).
    ``field_map`` maps engine field → CSV column header. Any column named in
    ``fact_columns`` becomes a displayed fact. Rows may carry lat/lon directly;
    rows with only a US state fall back to a state-centroid pin."""
    type: Literal["csv"]
    id: str
    label: str
    path: str                                  # relative to the client config dir
    field_map: dict[str, str] = {}             # name/segment/city/state/lat/lon/url → column
    fact_columns: dict[str, str] = {}          # display label → column header


class ApiGeoSource(BaseModel):
    """Accounts from a third-party API via the generic connector. The connector's
    records are mapped to accounts through ``field_map`` (name/city/state/lat/lon)
    and ``fact_map`` (displayed facts). ``query`` is substituted for {query}."""
    type: Literal["api"]
    id: str
    label: str
    query: str                                 # substituted into the connector's {query}
    connector: "ConnectorSpec"
    field_map: dict[str, str] = {}             # name/segment/city/state/lat/lon/url → record field
    fact_map: list["FactMapping"] = []
    max_accounts: int = 50


GeoSource = Annotated[Union[CsvGeoSource, ApiGeoSource], Field(discriminator="type")]


class AccountMapConfig(BaseModel):
    """Definition of the account-map view. Omit to disable the map."""
    title: str = "Account Map"
    region: str | None = None                  # display label, e.g. "Northeast US"
    center: GeoPoint | None = None             # initial map center; defaults to US
    zoom: int = 5
    segments: list[str] = []                   # account categories for filtering/legend
    sources: list[GeoSource] = []


class ClientConfig(BaseModel):
    branding: Branding
    editions: list[Edition]
    scoring_rubric: ScoringRubric
    sources: list[Source]
    keyword_prefilter: KeywordPrefilter
    cadence: Cadence
    cost_caps: CostCaps
    profile: ClientProfile | None = None              # client-specific context for analysis
    deep_analysis: DeepAnalysisConfig | None = None   # omit to disable in-depth analysis
    enrichers: list[EnricherConfig] = []              # cross-API enrichment; empty = disabled
    account_map: AccountMapConfig | None = None       # geographic account view; omit to disable

    def required_env_vars(self) -> list[str]:
        """Collect every auth_env_var declared across JSON API sources,
        enrichment connectors, and account-map API sources."""
        out = [
            s.auth_env_var
            for s in self.sources
            if isinstance(s, JsonApiSource) and s.auth_env_var
        ]
        out += [e.connector.auth_env_var for e in self.enrichers if e.connector.auth_env_var]
        if self.account_map:
            out += [
                src.connector.auth_env_var
                for src in self.account_map.sources
                if isinstance(src, ApiGeoSource) and src.connector.auth_env_var
            ]
        return out


class Feedback(BaseModel):
    """Client feedback that deterministically adjusts the next run.

    A committed file (``clients/<name>/feedback.json``) — NOT a database and NOT
    a learning loop. Each field is applied as a plain, reproducible rule, so the
    effect is auditable in git. An absent file (or empty lists) means no change.
    """
    mute_terms: list[str] = []      # title/summary matches dropped before analysis ("less of this")
    boost_terms: list[str] = []     # matches force-kept: prefilter include + never_discard ("more of this")
    mute_sources: list[str] = []    # source names (as shown on cards) whose items are dropped
    suppress_urls: list[str] = []   # specific item URLs to drop from the brief ("never show this")
    pin_urls: list[str] = []        # specific item URLs to force to tier 1 ("always surface this")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OUTPUT / ARTIFACT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DollarAmount(BaseModel):
    raw_text: str                 # LLM-extracted string, always present when model is present
    value: float | None = None    # Python parser fills; None on parse failure
    currency: str | None = None   # e.g. "USD"


class AffectedPopulation(BaseModel):
    raw_text: str
    value: int | None = None
    unit: str | None = None       # e.g. "personnel", "aircraft", "facilities"


class Entity(BaseModel):
    """A named thing the analyst extracted from an item (organization, program,
    agency, person, place…). The ``type`` is a free lowercase string so the
    engine stays generic; clients decide which types matter via enricher config."""
    name: str
    type: str = "other"


class EnrichmentFact(BaseModel):
    """One structured fact attached to an item by an enricher (a cross-API
    lookup). ``value`` is always the display string; ``number`` carries a parsed
    numeric where ``kind`` is money/number so the frontend can format it."""
    enricher_id: str
    entity: str                   # the entity name this fact was looked up for
    label: str
    value: str
    kind: Literal["text", "money", "number", "date", "url"] = "text"
    number: float | None = None   # parsed numeric for money/number kinds
    url: str | None = None        # canonical link for this fact, when available


class ItemEnrichment(BaseModel):
    """All cross-API facts gathered for one item, plus which entities were
    queried (so the UI can show coverage even when a lookup returned nothing)."""
    facts: list[EnrichmentFact] = []
    queried_entities: list[str] = []


class RelatedRef(BaseModel):
    """A pointer from one item to another that shares one or more entities —
    the edge of the entity graph. ``via`` lists the shared entity names."""
    item_id: str
    title: str
    url: str
    via: list[str] = []


class EntityIndexEntry(BaseModel):
    """One node of the run-level entity graph: an entity and every item it
    appears in. Powers the site's entity explorer."""
    name: str
    type: str
    item_ids: list[str]


class EditionAnalysis(BaseModel):
    relevance_score: int          # 0–100; LLM assigns in one batch call covering all editions
    so_what: str
    now_what: str
    categories: list[str]         # subset of edition.categories that apply


class DeepAnalysis(BaseModel):
    """Precomputed in-depth analysis for one item.

    Keyed by the section ids defined in ``ClientConfig.deep_analysis``; each
    value is a string ("text" section) or list of strings ("list" section).
    """
    sections: dict[str, str | list[str]]


class CoverageRef(BaseModel):
    """A secondary source covering the same underlying event as a primary item.
    Listed on the primary card as 'also covered by'; the secondary is removed
    from the top-level item list to collapse duplicates."""
    item_id: str
    source_id: str
    title: str
    url: str


class AnalyzedItem(BaseModel):
    item_id: str                  # stable hash of (source_id + url)
    title: str                    # cleaned, human-readable headline (see raw_title for original)
    raw_title: str | None = None  # original source title before normalization; null if unchanged
    url: str
    source_id: str
    published_at: datetime | None
    collected_at: datetime
    tier: Literal[1, 2, 3]
    per_edition: dict[str, EditionAnalysis]   # keyed by edition.id
    dollar_amount: DollarAmount | None = None
    affected_population: AffectedPopulation | None = None
    action_deadline: date | None = None
    confidence_note: str | None = None
    unverified_claims: list[str] = []         # factual claims not found in source text
    deep_analysis: DeepAnalysis | None = None # precomputed in-depth analysis; null if not generated
    also_covered_by: list[CoverageRef] = []   # other sources on the same event (dedup grouping)
    entities: list[Entity] = []               # named things extracted by the analyst
    enrichment: ItemEnrichment | None = None  # cross-API facts; null when no enricher matched
    related: list[RelatedRef] = []            # other items sharing an entity (graph edges)

    @computed_field                # deterministic Python; LLM never assigns this directly
    @property
    def importance_score(self) -> int:
        if not self.per_edition:
            return 0
        return max(e.relevance_score for e in self.per_edition.values())


class EscalatedItem(BaseModel):
    item_id: str
    previous_tier: int
    current_tier: int


class WhatsDiff(BaseModel):
    new_tier_1: list[str]              # item_ids new to Tier 1 vs. previous run
    new_tier_2: list[str]
    escalated: list[EscalatedItem]
    dropped: list[str]                 # tier-1/2 item_ids absent from current run
    deadline_imminent: list[str] = []  # item_ids with action_deadline within the alert window


class RunMeta(BaseModel):
    run_id: str                   # e.g. "20260611T060012-a3f9"
    run_at: datetime
    items_collected: int
    items_after_prefilter: int
    items_analyzed: int
    estimated_cost_usd: float | None
    engine_version: str


class EditorialSynthesis(BaseModel):
    theme_of_week: str
    editors_note: str
    whats_new_digest: str


class EditionInfo(BaseModel):
    id: str
    label: str
    categories: list[str]


class DeepAnalysisSectionInfo(BaseModel):
    """Presentation projection of a deep-analysis section (drops the prompt
    instruction the frontend doesn't need). Mirrors EditionInfo."""
    id: str
    label: str
    kind: Literal["text", "list"] = "text"


class EnricherInfo(BaseModel):
    """Presentation projection of an enricher — id + label for the site's
    'live integrations' badge and fact grouping."""
    id: str
    label: str


class SiteConfig(BaseModel):
    name: str
    accent_color: str
    editions: list[EditionInfo]
    deep_analysis_sections: list[DeepAnalysisSectionInfo] | None = None  # ordered; null if disabled
    enrichers: list[EnricherInfo] = []   # active cross-API integrations; [] if none configured
    account_map_url: str | None = None   # link to the account-map page; null if not configured


class RunOutput(BaseModel):
    meta: RunMeta
    items: list[AnalyzedItem]
    whats_new: WhatsDiff
    editorial: EditorialSynthesis | None = None
    site_config: SiteConfig | None = None          # embedded by the site builder from ClientConfig
    source_health: dict[str, SourceHealth] | None = None  # per-source collection stats
    entity_index: list[EntityIndexEntry] = []      # run-level entity graph nodes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ARCHIVE — rolling history of past runs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ArchivedRun(BaseModel):
    run_id: str
    run_at: datetime
    items: list[AnalyzedItem]


class Archive(BaseModel):
    runs: list[ArchivedRun] = []      # ordered oldest → newest; rolling window
    pinned: list[AnalyzedItem] = []   # high-importance items preserved beyond retention window


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ACCOUNT MAP OUTPUT — artifact consumed by the map page
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TargetAccount(BaseModel):
    """One potential account placed on the map and scored for fit."""
    id: str
    name: str
    segment: str | None = None
    city: str | None = None
    state: str | None = None
    geo: GeoPoint | None = None              # None → not placeable; surfaced in a list only
    geo_approx: bool = False                 # True when geo is a state-centroid fallback
    facts: list[EnrichmentFact] = []         # award $, agency, NAICS… (reuses the enrichment fact)
    fit_score: int = 0                       # 0–100 fit against the client profile
    fit_tier: Literal["hot", "warm", "cool"] = "cool"
    fit_rationale: str = ""
    serve_with: list[str] = []               # client capabilities that map to this account's need
    source_id: str = ""
    url: str | None = None


class MapConfigInfo(BaseModel):
    """Presentation projection of AccountMapConfig embedded in the artifact."""
    title: str
    region: str | None = None
    center: GeoPoint | None = None
    zoom: int = 5
    segments: list[str] = []
    accent_color: str
    name: str
    capabilities: list[str] = []             # client capabilities, for the "serve with" legend


class MapData(BaseModel):
    generated_at: datetime
    config: MapConfigInfo
    targets: list[TargetAccount] = []

    @computed_field
    @property
    def placed_count(self) -> int:
        return sum(1 for t in self.targets if t.geo is not None)
