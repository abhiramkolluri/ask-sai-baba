"""Chat orchestration — query classification and the RAG answer entrypoint.

This is the request-handling layer that ties the other pieces together: it
classifies the incoming question, runs the ``search`` pipeline to gather grounding
discourses, asks the fine-tuned model for an answer, and persists the turn via
``persistence``. It is the only module that depends on all three of search,
persistence, and the fine-tuned model — keeping that orchestration out of the
search package so the pipeline stays a pure, reusable retrieval library.

Request flow:
    Frontend → API Gateway → app.py → chat.handle_user_query
        → search (retrieve)  → OpenAI (answer)  → persistence (save)
"""

import json
import logging

from fine_tuning import load_fine_tuned_model_id_from_file
from search import (
    openai_client,
    extract_quoted_phrase,
    search_browse,
    search_exact,
    format_docs,
)
from persistence import (
    load_conversation_history,
    save_conversation_turn,
    store_new_user_query,
)


# ===========================================================================
# Query classification (fine-tuned safety classifier)
# ===========================================================================

def classify_query(query: str):
    """
    Classify if a query is inappropriate.
    Returns: (is_allowed, confidence_score, reason)
    """
    try:
        classification_prompt = '''You are a query classifier for an AI assistant. Your task is to block queries that are:
- Vulgar, hateful, or explicit
- Seeking medical advice (e.g., asking for prescriptions, diagnoses, or treatment recommendations)
- Seeking legal advice (e.g., asking for legal interpretations or recommendations)
- Seeking financial advice (e.g., asking for investment, tax, or financial planning advice)
Otherwise, allow the query and provide a broad category (spiritual, personal, general, etc.).

Return your classification as a JSON object with these fields:
{
    "is_allowed": boolean,
    "confidence": float between 0 and 1,
    "category": string (one of: "medical", "legal", "financial", "vulgar", "spiritual", "personal", "general"),
    "reason": string explaining your decision
}'''
        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": query}
            ],
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)
        return result.get("is_allowed", True), result.get("confidence", 1.0), result.get("reason", "")
    except Exception as e:
        logging.error(f"Error in classify_query: {e}")
        return True, 0.5, "Error in classification"


# ===========================================================================
# RAG answer entrypoint — classify → retrieve → generate → persist
# ===========================================================================

def handle_user_query(query: str, collection=None, session_id: str = None, user_id: str = None, user_email: str = None, search_results = None):
    """Handle user query completely without LangChain memory structures."""
    try:
        is_allowed, confidence, reason = classify_query(query)
        if not is_allowed:
            suggestion = (
                "Please avoid asking questions that are vulgar, or seek medical, legal, or financial advice. "
                "Try rephrasing your question to focus on spiritual, personal, or general topics. "
                "If you believe your question is valid and this was a mistake, please try asking again in a different way, as the website can sometimes make errors."
            )
            return (
                f"I'm sorry, but I cannot answer this question. Reason: {reason}\n"
                f"{suggestion}",
                ""
            )

        # Extract any quoted phrase from the user's query for exact/title-priority search.
        # This handles natural language queries like: retrieve the discourse on "Love and Truth"
        # as well as fully-quoted queries like: "Love and Truth"
        exact_phrase = extract_quoted_phrase(query)
        if exact_phrase:
            if search_results is None:
                search_results = search_exact(exact_phrase, limit=5)
        else:
            if search_results is None:
                search_results = search_browse(query, limit=5, allow_empty=False)

        # Ensure we have at least 5 results (by grabbing random docs if search fails)
        # Assuming we aren't performing random augmentation here anymore due to semantic search efficiency,
        # but the fallback might just use what we have.

        context = format_docs(search_results)

        chat_history = []
        if session_id:
            chat_history = load_conversation_history(session_id, user_id)

        system_prompt = """You are an AI assistant that provides spiritual guidance based on Sathya Sai Baba's teachings.

        Your response should be simple and direct:
        "Here are some discourses where you can start learning about the topic:"

        Then list ONLY the actual titles of the discourses provided to you, one per line with a dash, like this:
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]

        Do not provide any descriptions, summaries, or quotes. Do not use placeholder text like "[title of discourse 1]". Use the real titles from the discourses provided.

        Always provide spiritual guidance based on the provided discourses, even if the question seems unrelated. Use the discourses to offer relevant wisdom and insights that can help the user in their spiritual journey."""

        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history:
            messages.append(msg)

        messages.append({
            "role": "user",
            "content": f"Answer this query: {query}\n\nBased on the following discourses: {context}"
        })

        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=messages
        )
        answer = response.choices[0].message.content

        # Post-process message
        if "misunderstanding" in answer.lower() or "seems like there might be" in answer.lower():
            answer = "Based on Sai Baba's teachings, here is spiritual guidance that can help you: " + (answer.split(".")[-1] if "." in answer else answer)

        if session_id:
            save_conversation_turn(session_id, user_id, query, answer)

        store_new_user_query(query, answer, search_results, user_email)

        return answer
    except Exception as e:
        logging.error(f"Error in handle_user_query: {e}")
        return "An error occurred while processing your query.", ""
