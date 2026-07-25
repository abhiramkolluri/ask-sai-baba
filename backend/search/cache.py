"""Server-side result cache for repeated questions (Phase 4).

A small in-memory LRU keyed on the normalized question plus the context that
changes results (history, exact phrase, allow_empty, whether a trace was asked
for). Popular questions asked identically across users/sessions short-circuit the
whole plan→retrieve→rerank→grade pipeline.

Deliberately a *normalized-exact* cache, not an embedding-similarity cache: it's
simple, has zero per-query embedding cost, and safely catches the common case
(the same question typed again). Upgrading to embedding-based near-duplicate
matching is a future step — do it only if the hit rate proves too low.

Off unless config.SEMANTIC_CACHE_ENABLED; invalidate by process restart (also
what a corpus re-index requires). Callers pass the exact value they'd return so a
hit is indistinguishable from a miss except for latency; the trace is tagged
`cached` so the timings aren't misread as a live run.
"""

from collections import OrderedDict

from .config import SEMANTIC_CACHE_ENABLED, SEMANTIC_CACHE_SIZE

_cache = OrderedDict()


def _key(query, history, exact_phrase, allow_empty, return_trace):
    hist = "|".join(h for h in (history or []) if isinstance(h, str))
    norm = " ".join((query or "").lower().split())
    return (norm, hist, (exact_phrase or "").lower(), bool(allow_empty), bool(return_trace))


def get(query, history, exact_phrase, allow_empty, return_trace):
    """Return the cached value for these args, or None on miss/disabled."""
    if not SEMANTIC_CACHE_ENABLED:
        return None
    k = _key(query, history, exact_phrase, allow_empty, return_trace)
    if k not in _cache:
        return None
    _cache.move_to_end(k)  # LRU: mark most-recently-used
    return _cache[k]


def put(query, history, exact_phrase, allow_empty, return_trace, value):
    """Store a computed result; evict the oldest entry past the size cap."""
    if not SEMANTIC_CACHE_ENABLED:
        return
    k = _key(query, history, exact_phrase, allow_empty, return_trace)
    _cache[k] = value
    _cache.move_to_end(k)
    while len(_cache) > SEMANTIC_CACHE_SIZE:
        _cache.popitem(last=False)
