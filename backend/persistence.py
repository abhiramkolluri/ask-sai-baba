"""Weaviate persistence layer — conversation memory and query logging.

This module holds the thin read/write wrappers over the two Weaviate collections
that store *side-effects of a chat*, as opposed to the discourse corpus that
search.py reads:

  - ``Conversation`` — the running message history for a session, stored as a
    single JSON blob per ``session_id`` (load / append / clear).
  - ``UserQuery``    — a low-score audit log of questions whose answers were not
    confidently grounded, kept for later review/eval.

It is deliberately self-contained: it talks only to Weaviate (via
``weaviate_client.get_client``) and never imports from ``search`` or ``chat``.
That keeps the dependency graph one-way — ``chat`` orchestrates both ``search``
and ``persistence``; neither of those two depends on the other.

Request flow:
    Frontend → API Gateway → app.py → chat.py → persistence.py → Weaviate
"""

import json
import logging
from datetime import datetime

from weaviate_client import get_client
from weaviate.classes.query import Filter


# ===========================================================================
# Conversation memory (the ``Conversation`` collection)
#
# One Weaviate object per session_id holds the whole exchange as a JSON list in
# the ``messages_json`` property. We load it, append to it, or delete it — there
# is no per-message object, so "save" is read-modify-write of that one blob.
# ===========================================================================

def load_conversation_history(session_id: str, user_id: str = None) -> list:
    """Load previous conversation exchanges from Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            formatted = []
            for msg in messages:
                role = "user" if msg.get("type", "").lower() == "human" else "assistant"
                formatted.append({"role": role, "content": msg.get("content", "")})
            return formatted[-20:] # limit to last 10 exchanges
        return []
    except Exception as e:
        logging.error(f"Error loading conversation history: {e}")
        return []

def save_conversation_turn(session_id: str, user_id: str, query: str, answer: str):
    """Save the latest turn to Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )

        now = datetime.now()
        new_human = {"type": "human", "content": query, "timestamp": now.isoformat()}
        new_ai = {"type": "ai", "content": answer, "timestamp": now.isoformat()}

        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            messages.extend([new_human, new_ai])

            conv_col.data.update(
                uuid=obj.uuid,
                properties={
                    "messages_json": json.dumps(messages),
                    "last_updated": now
                }
            )
        else:
            messages = [new_human, new_ai]
            conv_col.data.insert(properties={
                "session_id": session_id,
                "user_id": user_id or "",
                "messages_json": json.dumps(messages),
                "created_at": now,
                "last_updated": now
            })
    except Exception as e:
        logging.error(f"save_conversation_turn error: {e}")

def clear_conversation_memory(session_id: str, user_id: str = None):
    """Clear conversation corresponding to a session_id."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        conv_col.data.delete_many(where=Filter.by_property("session_id").equal(session_id))
        return True
    except Exception as e:
        logging.error(f"clear_conversation_memory error: {e}")
        return False


# ===========================================================================
# Query audit log (the ``UserQuery`` collection)
#
# Only low-confidence answers are recorded (top score < 0.75), so the log
# surfaces the questions the corpus answered poorly — useful for eval and for
# spotting gaps in the discourse coverage.
# ===========================================================================

def store_new_user_query(query_text, response, get_knowledge, user_email=None):
    """Log the QA search metadata to Weaviate UserQuery."""
    try:
        client = get_client()
        query_col = client.collections.get("UserQuery")

        citationString = ''
        score = 0.0
        if get_knowledge:
            score = get_knowledge[0].get('score', 0.0)
            if score < 0.75: # Legacy condition migrated
                for knowledge in get_knowledge:
                    citationString += f"{knowledge.get('_id', '')} -- {knowledge.get('title', '')} -- {knowledge.get('score', 0)}\n"

                query_col.data.insert(properties={
                    "query_text": query_text,
                    "response": response,
                    "score": float(score),
                    "citation": citationString,
                    "user_email": user_email or "",
                    "created_at": datetime.now()
                })
    except Exception as exp:
        logging.error(f"Error storing user query: {exp}")
