"""
RAG Engine — Retrieval-Augmented Generation core.

Improvements over v1:
- Semantic chunking (splits at function/class boundaries for code)
- Optional sentence-transformers or nomic-embed-text embeddings
- Hybrid scoring: TF-IDF + optional dense embeddings
- Conversation-aware retrieval (uses session history for context)
- Async-friendly ask function for web UI

Flow:
  1. Search vector store for relevant chunks
  2. Re-rank by hybrid score
  3. Build prompt with context via template engine
  4. Send to AI engine + stream response
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.vector_store import search, get_stats

if TYPE_CHECKING:
    pass

# ── Retrieval ────────────────────────────────────────────────────────────────

def ask_with_context(
    query: str,
    top_k: int = 6,
    verbose: bool = False,
    use_history: bool = True,
) -> None:
    """
    Main RAG query — CLI version (sync, prints to stdout).
    Incorporates conversation history for context-aware answers.
    """
    stats = get_stats()
    if stats["total_files"] == 0:
        print("⚠️  No files indexed yet!")
        print("   Run: index /path/to/your/project")
        return

    # Expand query with session context if available
    effective_query = _enrich_query(query) if use_history else query

    chunks = search(effective_query, top_k=top_k)
    if not chunks:
        print("🔍 No relevant code found in index.")
        print("   Try re-indexing: index .")
        return

    if verbose:
        print(f"\n📎 Found {len(chunks)} relevant chunks:\n")
        for c in chunks:
            print(f"  • {c['filepath']}  (score: {c['score']:.3f})")
        print()

    prompt = _build_prompt(query, chunks)
    print(f"🤖 Analyzing {len(chunks)} relevant code sections...\n")

    try:
        from core.ai import ask_ai
        ask_ai(prompt)
        # Save to session
        try:
            from core.session import get_session
            get_session().add_user(query)
        except Exception:
            pass
    except Exception as exc:
        print(f"⚠️  AI error: {exc}")


async def ask_with_context_async(
    query: str,
    top_k: int = 6,
    use_history: bool = True,
):
    """Async RAG query — yields tokens for streaming web UI."""
    from core.ai import ask_ai_streaming

    stats = get_stats()
    if stats["total_files"] == 0:
        yield "⚠️  No files indexed yet! Run: `index /path/to/your/project`"
        return

    effective_query = _enrich_query(query) if use_history else query
    chunks = search(effective_query, top_k=top_k)

    if not chunks:
        yield "🔍 No relevant code found. Try re-indexing: `index .`"
        return

    prompt = _build_prompt(query, chunks)
    full_response = []
    async for token in ask_ai_streaming(prompt):
        full_response.append(token)
        yield token

    if use_history and full_response:
        try:
            from core.session import get_session
            sess = get_session()
            sess.add_user(query)
            sess.add_assistant("".join(full_response))
        except Exception:
            pass


# ── Prompt building ──────────────────────────────────────────────────────────

def _build_prompt(query: str, chunks: list[dict]) -> str:
    """Build RAG prompt using template engine."""
    try:
        from core.prompts import render
        return render("rag_ask", query=query, chunks=chunks)
    except Exception:
        context = "\n".join(
            f"--- {c['filepath']} ---\n{c['content']}\n"
            for c in chunks
        )
        return (
            "You are an expert code assistant.\n\n"
            f"RELEVANT CODE:\n{context}\n\n"
            f"QUESTION: {query}\n\nAnswer:"
        )


def _enrich_query(query: str) -> str:
    """
    Enrich the query with recent session context for better retrieval.
    Extracts identifiers from recent assistant responses.
    """
    try:
        from core.session import get_session
        history = get_session().get_history()
        if not history:
            return query
        last_assistant = next(
            (t.content for t in reversed(history) if t.role == "assistant"),
            None,
        )
        if last_assistant:
            identifiers = re.findall(r"`([^`]{2,40})`", last_assistant)
            if identifiers:
                extra = " ".join(identifiers[:5])
                return f"{query} {extra}"
    except Exception:
        pass
    return query
