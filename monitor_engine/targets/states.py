"""US state name/abbreviation normalization and approximate centroids.

Used as a geo fallback: an account that carries only a state (no lat/lon) is
placed at the state's centroid so it still appears on the map. Centroids are
approximate population/area centers — good enough for a regional overview, never
presented as a precise location (the pin is flagged ``geo_approx``).
"""
from __future__ import annotations

# state abbr -> (full name, lat, lon)
_STATES: dict[str, tuple[str, float, float]] = {
    "AL": ("Alabama", 32.806, -86.791), "AK": ("Alaska", 61.370, -152.404),
    "AZ": ("Arizona", 33.729, -111.431), "AR": ("Arkansas", 34.969, -92.373),
    "CA": ("California", 36.116, -119.682), "CO": ("Colorado", 39.059, -105.311),
    "CT": ("Connecticut", 41.597, -72.755), "DE": ("Delaware", 39.318, -75.507),
    "DC": ("District of Columbia", 38.897, -77.026), "FL": ("Florida", 27.766, -81.686),
    "GA": ("Georgia", 33.040, -83.643), "HI": ("Hawaii", 21.094, -157.498),
    "ID": ("Idaho", 44.240, -114.478), "IL": ("Illinois", 40.349, -88.986),
    "IN": ("Indiana", 39.849, -86.258), "IA": ("Iowa", 42.011, -93.210),
    "KS": ("Kansas", 38.526, -96.726), "KY": ("Kentucky", 37.668, -84.670),
    "LA": ("Louisiana", 31.169, -91.867), "ME": ("Maine", 44.693, -69.381),
    "MD": ("Maryland", 39.064, -76.741), "MA": ("Massachusetts", 42.230, -71.530),
    "MI": ("Michigan", 43.326, -84.536), "MN": ("Minnesota", 45.694, -93.900),
    "MS": ("Mississippi", 32.741, -89.678), "MO": ("Missouri", 38.456, -92.288),
    "MT": ("Montana", 46.921, -110.454), "NE": ("Nebraska", 41.125, -98.268),
    "NV": ("Nevada", 38.313, -117.055), "NH": ("New Hampshire", 43.452, -71.564),
    "NJ": ("New Jersey", 40.298, -74.521), "NM": ("New Mexico", 34.840, -106.248),
    "NY": ("New York", 42.165, -74.948), "NC": ("North Carolina", 35.630, -79.806),
    "ND": ("North Dakota", 47.528, -99.784), "OH": ("Ohio", 40.388, -82.764),
    "OK": ("Oklahoma", 35.565, -96.928), "OR": ("Oregon", 44.572, -122.071),
    "PA": ("Pennsylvania", 40.590, -77.209), "RI": ("Rhode Island", 41.680, -71.511),
    "SC": ("South Carolina", 33.856, -80.945), "SD": ("South Dakota", 44.299, -99.438),
    "TN": ("Tennessee", 35.747, -86.692), "TX": ("Texas", 31.054, -97.563),
    "UT": ("Utah", 40.150, -111.862), "VT": ("Vermont", 44.045, -72.710),
    "VA": ("Virginia", 37.769, -78.170), "WA": ("Washington", 47.400, -121.490),
    "WV": ("West Virginia", 38.491, -80.954), "WI": ("Wisconsin", 44.268, -89.616),
    "WY": ("Wyoming", 42.756, -107.302),
}

_NAME_TO_ABBR = {full.lower(): abbr for abbr, (full, _la, _lo) in _STATES.items()}


def normalize_state(value: str | None) -> str | None:
    """Return the 2-letter abbreviation for a state given an abbr or full name."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 2 and v.upper() in _STATES:
        return v.upper()
    return _NAME_TO_ABBR.get(v.lower())


def state_centroid(value: str | None) -> tuple[float, float] | None:
    """Approximate (lat, lon) for a state, or None if unrecognized."""
    abbr = normalize_state(value)
    if abbr is None:
        return None
    _full, lat, lon = _STATES[abbr]
    return lat, lon
