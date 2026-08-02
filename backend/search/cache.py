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


def _scope_key(scope):
    """Stable, hashable rendering of a caller scope dict (or None)."""
    if not scope:
        return ""
    return "|".join(f"{k}={scope[k]}" for k in sorted(scope))


def _key(query, history, exact_phrase, allow_empty, return_trace, defer_grading, scope=None):
    hist = "|".join(h for h in (history or []) if isinstance(h, str))
    norm = " ".join((query or "").lower().split())
    # `defer_grading` is part of the key because the two modes return genuinely
    # different payloads for the same question — ungraded provisional citations vs
    # verified quotes. Sharing one entry would let a phase-1 caller be handed
    # graded results (harmless) or, far worse, a phase-2 caller be handed
    # unverified ones presented as verified.
    #
    # `scope` is part of it for a blunter reason: the same word searched inside
    # two different collections is two different questions. Leaving it out let
    # an unscoped "love" populate the entry that a Prema-Vahini-scoped "love"
    # then read, so the scoped search answered from the whole corpus — measured,
    # not hypothetical.
    return (norm, hist, (exact_phrase or "").lower(),
            bool(allow_empty), bool(return_trace), bool(defer_grading),
            _scope_key(scope))


def get(query, history, exact_phrase, allow_empty, return_trace, defer_grading=False, scope=None):
    """Return the cached value for these args, or None on miss/disabled."""
    if not SEMANTIC_CACHE_ENABLED:
        return None
    k = _key(query, history, exact_phrase, allow_empty, return_trace, defer_grading, scope)
    if k not in _cache:
        return None
    _cache.move_to_end(k)  # LRU: mark most-recently-used
    return _cache[k]


def put(query, history, exact_phrase, allow_empty, return_trace, defer_grading, scope, value):
    """Store a computed result; evict the oldest entry past the size cap.

    `scope` sits before `value` so callers can keep passing one `cache_args`
    tuple to both get() and put().
    """
    if not SEMANTIC_CACHE_ENABLED:
        return
    k = _key(query, history, exact_phrase, allow_empty, return_trace, defer_grading, scope)
    _cache[k] = value
    _cache.move_to_end(k)
    while len(_cache) > SEMANTIC_CACHE_SIZE:
        _cache.popitem(last=False)
