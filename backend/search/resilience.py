"""Small retry helper for the pipeline's external calls (OpenAI, Weaviate, Cohere).

Real-traffic replay showed the pipeline had no resilience to transient API blips:
every stage swallowed its exception and returned degraded output (planner -> raw
message, retrieval -> empty list), so a momentary connection drop was
indistinguishable from a genuinely empty corpus and surfaced to the user as a
false "no discourses found". This module provides:

  - ``PipelineServiceError`` — raised when a stage exhausts its retries, so the
    orchestrator can tell an infrastructure failure apart from an empty result
    and surface an honest "temporary problem, try again" state instead.
  - ``with_retries`` — retry a callable a couple of times with short backoff.

Deliberately dependency-free and tiny. It targets fast-failing connection errors
(which return almost immediately), so a couple of retries add only the backoff,
not a full timeout — keeping worst-case latency well under the API Gateway budget.
"""

import time
import logging


class PipelineServiceError(Exception):
    """A pipeline stage failed to reach an external service after retries.

    Distinct from an empty result: empty means "the corpus had nothing"; this
    means "we couldn't complete the search" and the user should retry.
    """


def with_retries(fn, attempts=2, base_delay=0.4, exc=(Exception,), what="operation"):
    """Call ``fn()``; on a transient failure retry up to ``attempts`` extra times
    with exponential backoff (base_delay, 2x, 4x, ...). Re-raises the final error
    wrapped in ``PipelineServiceError`` once retries are exhausted.

    ``attempts`` is the number of RETRIES after the first try, so total calls =
    attempts + 1. ``exc`` narrows which exception types are considered transient.
    """
    last = None
    for i in range(attempts + 1):
        try:
            return fn()
        except exc as e:
            last = e
            if i < attempts:
                delay = base_delay * (2 ** i)
                logging.warning(f"{what} failed ({e}); retry {i + 1}/{attempts} in {delay:.1f}s")
                time.sleep(delay)
    raise PipelineServiceError(f"{what} failed after {attempts + 1} attempts: {last}")
