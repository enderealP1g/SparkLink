"""Canonical user-facing names for SparkLink subscription nodes.

The fragment of a VLESS URI is a client display label (for example, the
remark shown by v2rayN).  This module deliberately treats it as presentation
metadata: changing it must never change the URI endpoint, query parameters,
credential identity, routing, Pool, or Usage association.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit


class SubscriptionNamingError(ValueError):
    """A fail-closed subscription naming or URI error."""


CANONICAL_PREFIX_BY_NODE = {
    "hypro02": "Pro-LA-02",
    "vmiss": "Pro-LA-01",
    "racknerd": "Standard-NY",
}
# DediRock exposes two separately managed egress variants. Keep the old
# singular constant as the HyTru compatibility/default value for admission
# callers that predate the dual-route projection.
CANONICAL_DEDIROCK_HYTRU_ALIAS = "Advanced-LA-HyTru-Direct-Reality"
CANONICAL_DEDIROCK_ORIGIN_ALIAS = "Advanced-LA-Origin-Direct-Reality"
CANONICAL_DEDIROCK_ALIAS = CANONICAL_DEDIROCK_HYTRU_ALIAS
CANONICAL_DEDIROCK_ALIASES = frozenset({
    CANONICAL_DEDIROCK_ORIGIN_ALIAS,
    CANONICAL_DEDIROCK_HYTRU_ALIAS,
})
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANONICAL_RE = re.compile(
    r"^(?P<prefix>Pro-LA-(?:01|02)|Standard-NY)-(?P<route>HyTru|Origin)-Direct-Reality$"
)
_LEGACY_RE = re.compile(
    r"^(?:Plus|Basic)-(?P<location>LA|NY)-Xray-VLESS-REALITY-(?P<ordinal>[0-9]+)$"
)
_DEDIROCK_RE = re.compile(r"^SparkLink-(?P<user>[A-Za-z0-9_.-]+)-DediRock-Advanced$")


def dedirock_alias(route: str) -> str:
    """Return the canonical DediRock alias for one explicit egress route."""

    value = str(route or "").strip().lower()
    if value == "origin":
        return CANONICAL_DEDIROCK_ORIGIN_ALIAS
    if value == "hytru":
        return CANONICAL_DEDIROCK_HYTRU_ALIAS
    raise SubscriptionNamingError("dedirock_route_unrecognized")


def _decoded_alias(value: str) -> str:
    if not isinstance(value, str):
        raise SubscriptionNamingError("subscription_alias_invalid")
    alias = unquote(value)
    if not alias or len(alias) > 128:
        raise SubscriptionNamingError("subscription_alias_invalid")
    return alias


def canonical_alias(node_id: str, current_alias: str) -> str:
    """Return the canonical display alias for a current subscription entry.

    Existing VeilShift labels are intentionally returned unchanged.  For the
    other legacy labels, the established odd/even suffix convention is used
    only when the old label is fully recognized; an unknown label raises
    instead of guessing.
    """

    node = str(node_id or "").strip().lower()
    alias = _decoded_alias(current_alias)
    if "veilshift" in alias.lower():
        return alias

    if node == "dedirock":
        if alias in CANONICAL_DEDIROCK_ALIASES:
            return alias
        if (alias == "SparkLink-DediRock-Advanced"
                or _DEDIROCK_RE.fullmatch(alias)):
            return CANONICAL_DEDIROCK_ALIAS
        raise SubscriptionNamingError("dedirock_alias_unrecognized")

    prefix = CANONICAL_PREFIX_BY_NODE.get(node)
    if prefix is None:
        raise SubscriptionNamingError("subscription_node_unrecognized")

    canonical = _CANONICAL_RE.fullmatch(alias)
    if canonical:
        if canonical.group("prefix") != prefix:
            raise SubscriptionNamingError("subscription_alias_node_mismatch")
        return alias

    legacy = _LEGACY_RE.fullmatch(alias)
    if not legacy:
        raise SubscriptionNamingError("subscription_alias_unrecognized")
    expected_location = "NY" if node == "racknerd" else "LA"
    if legacy.group("location") != expected_location:
        raise SubscriptionNamingError("subscription_alias_location_mismatch")
    ordinal = int(legacy.group("ordinal"))
    route = "HyTru" if ordinal % 2 else "Origin"
    return f"{prefix}-{route}-Direct-Reality"


def alias_from_uri(uri: str) -> str:
    """Read only the display fragment from a URI; never expose other fields."""

    parsed = urlsplit(str(uri or ""))
    return unquote(parsed.fragment)


def replace_uri_alias(uri: str, alias: str) -> str:
    """Replace only a VLESS URI fragment and preserve all other components."""

    value = str(uri or "")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "vless"
        or not parsed.netloc
        or any(char.isspace() for char in value)
        or len(value) > 4096
    ):
        raise SubscriptionNamingError("subscription_uri_invalid")
    normalized_alias = _decoded_alias(alias)
    if not _ALIAS_RE.fullmatch(normalized_alias):
        raise SubscriptionNamingError("subscription_alias_invalid")
    updated = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query,
                          quote(normalized_alias, safe="-._~")))
    old_core = (parsed.scheme, parsed.netloc, parsed.path, parsed.query)
    new_parsed = urlsplit(updated)
    if old_core != (new_parsed.scheme, new_parsed.netloc, new_parsed.path, new_parsed.query):
        raise SubscriptionNamingError("subscription_uri_core_changed")
    return updated


def uri_core(uri: str) -> tuple[str, str, str, str]:
    """Return URI components other than the display fragment for comparisons."""

    parsed = urlsplit(str(uri or ""))
    return parsed.scheme, parsed.netloc, parsed.path, parsed.query
