"""Pure unit tests for structured search: metadata normalization, router plan
validation, and Weaviate filter composition. No network, no LLM — safe to run
anywhere the venv is:

    source venv/bin/activate
    python test_router_unit.py
"""

from metadata_norm import (
    normalize_collection,
    extract_year,
    date_sort_key,
    derive_article_metadata,
    slug_key,
)
from search.query_planning import _validate_plan, SearchPlan
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
    print("_validate_plan:")
    p = _validate_plan(
        {"intent": "listing", "queries": [],
         "filters": {"book": "Geeta Vahini", "chapter_start": 1, "chapter_end": 5},
         "sort": "chapter", "limit": 5},
        "msg", CATALOG)
    check("clean listing", p.intent == "listing" and p.filters["book"] == "Geeta Vahini" and p.limit == 5)

    p = _validate_plan({"intent": "listing", "filters": {"book": "geeta vahini"}}, "msg", CATALOG)
    check("book match is case-insensitive", p.filters.get("book") == "Geeta Vahini")

    p = _validate_plan({"intent": "listing", "filters": {"book": "Unknown Book"}}, "msg", CATALOG)
    check("unknown book -> semantic downgrade", p.intent == "semantic" and not p.filters and p.queries == ["msg"])

    p = _validate_plan({"intent": "listing", "filters": {"chapter_start": 3}}, "msg", CATALOG)
    check("chapter without book -> semantic", p.intent == "semantic")

    p = _validate_plan({"intent": "listing", "filters": {"volume": 14, "chapter_start": 10}}, "msg", CATALOG)
    check("volume infers SSS", p.filters.get("book") == "Sathya Sai Speaks" and p.filters["chapter_end"] == 10)

    p = _validate_plan({"intent": "hybrid", "queries": ["surrender"], "filters": {"year_start": 1979, "year_end": 1970}}, "msg", CATALOG)
    check("reversed years swapped", p.filters["year_start"] == 1970 and p.filters["year_end"] == 1979)

    p = _validate_plan({"intent": "hybrid", "filters": {"year_start": 1850}}, "msg", CATALOG)
    check("out-of-range year -> semantic", p.intent == "semantic")

    p = _validate_plan({"intent": "semantic", "queries": ["duty"], "filters": {"book": "Geeta Vahini"}}, "msg", CATALOG)
    check("semantic never filters", p.filters == {})

    p = _validate_plan({"intent": "hybrid", "filters": {"year_start": 1976}}, "msg", CATALOG)
    check("hybrid without queries uses message", p.queries == ["msg"])

    p = _validate_plan({"intent": "listing", "filters": {"location": "Brindavan, KA!"}}, "msg", CATALOG)
    check("location keyword cleaned", p.filters.get("location") == "brindavan ka")

    p = _validate_plan("garbage", "msg", CATALOG)
    check("garbage -> semantic passthrough", p.intent == "semantic" and p.queries == ["msg"])

    p = _validate_plan({"intent": "listing", "filters": {"year_start": "1976"}, "limit": 999}, "msg", CATALOG)
    check("numeric strings + limit cap", p.filters["year_start"] == 1976 and p.limit is None)


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
