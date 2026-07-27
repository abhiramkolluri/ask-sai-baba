"""Pure unit tests for structured search: metadata normalization, router plan
validation, and Weaviate filter composition. No network, no LLM — safe to run
anywhere the venv is:

    source venv/bin/activate
    python test_router_unit.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


from metadata_norm import (
    normalize_collection,
    extract_year,
    date_sort_key,
    derive_article_metadata,
    slug_key,
)
from search.query_planning import _validate_filters
from search.retrieval import build_passage_filter
from search.listing import build_article_filter

CATALOG = {"books": {"Geeta Vahini": 27, "Sathya Sai Speaks": 517, "Summer Showers": 298}}

PASSED = 0


def check(name, condition):
    global PASSED
    assert condition, f"FAIL: {name}"
    PASSED += 1
    print(f"  ok  {name}")


def test_normalize_collection():
    print("normalize_collection:")
    check("SSS volume+disc", normalize_collection("SSS, Vol 14Disc. 10") == ("Sathya Sai Speaks", 14, 9, None))
    check("SSS trailing text", normalize_collection("SSS, Vol 8Disc. 3* alternative translation") == ("Sathya Sai Speaks", 8, 2, None))
    check("year-suffix book", normalize_collection("Summer Showers 1976") == ("Summer Showers", None, None, 1976))
    check("year-suffix long name", normalize_collection("Women's Role in Rejuvenating the Culture of Bharat 1968")
          == ("Women's Role in Rejuvenating the Culture of Bharat", None, None, 1968))
    check("plain vahini", normalize_collection("Geeta Vahini") == ("Geeta Vahini", None, None, None))
    check("empty", normalize_collection("") == (None, None, None, None))


def test_extract_year():
    print("extract_year:")
    check("full date", extract_year("17 October 1953") == 1953)
    check("abbrev comma date", extract_year("Apr 10, 1993") == 1993)
    check("month-year", extract_year("May, 1972") == 1972)
    check("bare year", extract_year("2006") == 2006)
    check("no year", extract_year("Dasara") is None)
    check("none input", extract_year(None) is None)


def test_date_sort_key():
    print("date_sort_key:")
    check("day-month-year", date_sort_key("17 October 1953") == (1953, 10, 17))
    check("month-day-year", date_sort_key("Apr 10, 1993") == (1993, 4, 10))
    check("month-year", date_sort_key("May, 1972") == (1972, 5, 0))
    check("full month-year", date_sort_key("April 1957") == (1957, 4, 0))
    check("bare year", date_sort_key("2006") == (2006, 0, 0))
    check("empty sorts last", date_sort_key("") == (9999, 99, 99))
    check("ordering", date_sort_key("May, 1972") < date_sort_key("17 October 1972") < date_sort_key("Apr 10, 1993"))


def test_derive_article_metadata():
    print("derive_article_metadata:")
    m = derive_article_metadata("SSS, Vol 14Disc. 10", "17 October 1981")
    check("SSS full derivation", m == {"book": "Sathya Sai Speaks", "volume": 14, "chapter_index": 9, "year": 1981})
    m = derive_article_metadata("Geeta Vahini", "", crawler_chapter_index="4")
    check("crawler chapter wins (string cast)", m["chapter_index"] == 4 and m["year"] is None)
    m = derive_article_metadata("Summer Showers 1976", "")
    check("year falls back to name", m["year"] == 1976)
    m = derive_article_metadata("Summer Showers 1976", "May, 1977")
    check("date year beats name year", m["year"] == 1977)


def test_slug_key():
    print("slug_key:")
    check("cross-site title/slug match",
          slug_key("Women Are The Embodiments Of Nobility And Virtue")
          == slug_key("women-are-embodiments-nobility-and-virtue".replace("-", " ")))
    check("stopwords stripped", slug_key("The Bubble of Pride") == "bubble-pride")
    check("punctuation ignored", slug_key("Sanctify Your Life, With Sacred Feelings!") == slug_key("sanctify your life sacred feelings"))
    check("empty input", slug_key("") == "")


def test_validate_plan():
    """Filter validation after the router merge.

    The old SearchPlan carried intent AND filters, and validation downgraded a
    bad plan to intent="semantic". The merged router keeps the 12-intent
    taxonomy (see KNOWN_INTENTS) and validates filters independently, so the
    equivalent assertion is now "the bad filter is DROPPED" rather than "the
    intent is downgraded". Same protection: nothing unvalidated reaches Weaviate.
    """
    print("_validate_filters:")
    f = _validate_filters(
        {"book": "Geeta Vahini", "chapter_start": 1, "chapter_end": 5}, CATALOG)
    check("clean listing", f["book"] == "Geeta Vahini" and f["chapter_end"] == 5)

    f = _validate_filters({"book": "geeta vahini"}, CATALOG)
    check("book match is case-insensitive", f.get("book") == "Geeta Vahini")

    f = _validate_filters({"book": "Unknown Book"}, CATALOG)
    check("unknown book dropped", not f)

    f = _validate_filters({"chapter_start": 3}, CATALOG)
    check("chapter without book dropped", "chapter_start" not in f)

    f = _validate_filters({"volume": 14, "chapter_start": 10}, CATALOG)
    check("volume infers SSS", f.get("book") == "Sathya Sai Speaks" and f["chapter_end"] == 10)

    f = _validate_filters({"year_start": 1979, "year_end": 1970}, CATALOG)
    check("reversed years swapped", f["year_start"] == 1970 and f["year_end"] == 1979)

    f = _validate_filters({"year_start": 1850}, CATALOG)
    check("out-of-range year dropped", "year_start" not in f)

    f = _validate_filters({"location": "Brindavan, KA!"}, CATALOG)
    check("location keyword cleaned", f.get("location") == "brindavan ka")

    f = _validate_filters("garbage", CATALOG)
    check("garbage -> empty filters", f == {})

    f = _validate_filters({"year_start": "1976"}, CATALOG)
    check("numeric strings coerced", f["year_start"] == 1976)


def test_filter_builders():
    print("filter builders:")
    check("empty passage filter is None", build_passage_filter({}) is None)
    check("empty article filter is None", build_article_filter({}) is None)
    full = {"book": "Geeta Vahini", "volume": 2, "chapter_start": 1, "chapter_end": 5,
            "year_start": 1970, "year_end": 1979, "location": "brindavan", "occasion": "dasara"}
    check("full passage filter builds", build_passage_filter(full) is not None)
    check("full article filter builds", build_article_filter(full) is not None)
    check("single-clause filter builds", build_passage_filter({"year_start": 1976}) is not None)


if __name__ == "__main__":
    test_normalize_collection()
    test_extract_year()
    test_date_sort_key()
    test_derive_article_metadata()
    test_slug_key()
    test_validate_plan()
    test_filter_builders()
    print(f"\nAll {PASSED} checks passed.")
