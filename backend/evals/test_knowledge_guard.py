
import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys; sys.path.insert(0,"/Users/abhiramkolluri/Projects/asv/ask-sai-baba/backend")
from dotenv import load_dotenv; load_dotenv()
from search import knowledge

class _Meta:
    def __init__(s, sc): s.score = sc
class _Obj:
    def __init__(s, props, sc): s.properties, s.metadata = props, _Meta(sc)
class _Resp:
    def __init__(s, objs): s.objects = objs

class _Art:
    uuid="fake-id"
    properties={"title":"Some Discourse","content":"A sentence about it. Another one.",
                "location":"","occasion":"","link":"","collection_name":"X","date":""}

def fake_lookup(entity_props, score=0.9):
    """Force lookup_entity to see exactly this entity, no network. with_retries
    serves both the Entity query and the Article fetch, so dispatch on `what`."""
    def _stub(fn, **kw):
        if kw.get("what") == "Entity lookup":
            return _Resp([_Obj(entity_props, score)])
        return [knowledge._article_to_result(_Art())]
    knowledge.with_retries = _stub
    knowledge.select_best_sentences = lambda q, d: d   # avoid a Voyage call
    return knowledge

AHAMKARA = {"name":"Ahamkara","aliases":"ego; egoism","in_corpus":True,
            "canonical_article_ids":'["fake-id"]'}
EASWARAMMA = {"name":"Easwaramma","aliases":"Swami's mother; Baba's mother","in_corpus":True,
              "canonical_article_ids":'["fake-id"]'}

fails=[]
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ")+name); fails.append(name) if not cond else None

print("[1] discourse-title question must MISS despite containing the entity name")
k = fake_lookup(AHAMKARA)
r = k.lookup_entity("the discourse titled Ahamkara Causes Ashanti", [])
check("status == miss", r["status"] == "miss")
check("entity not claimed", r["entity"] is None)

print("[2] genuine entity question must NOT be blocked by the guard")
k = fake_lookup(AHAMKARA)
r = k.lookup_entity("what is Ahamkara", [])
check("status == hit", r["status"] == "hit" and r["entity"] == "Ahamkara")

print("[3] paraphrase with curated alias still passes")
k = fake_lookup(EASWARAMMA)
r = k.lookup_entity("who was Swami's mother", [])
check("status == hit", r["status"] == "hit" and r["entity"] == "Easwaramma")

print("[4] long title with one incidental match is rejected")
k = fake_lookup({"name":"Jnanam","aliases":"","in_corpus":True,"canonical_article_ids":'["x"]'})
r = k.lookup_entity("the discourse titled Jnanam Born of Shanti is the True Jewel of Man", [])
check("status == miss", r["status"] == "miss")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
