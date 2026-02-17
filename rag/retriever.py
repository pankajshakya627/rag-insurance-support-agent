"""
RAG Retriever — orchestrates embedding, search, and context assembly.

Implements strict_mode: if no sufficiently relevant context is found,
returns an insufficient_context flag instead of fabricating an answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings
from rag.embeddings import BedrockEmbeddings
from rag.vector_store import SearchResult, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalContext:
    """Assembled context from RAG retrieval, ready for LLM consumption."""

    chunks: list[dict[str, Any]] = field(default_factory=list)
    has_sufficient_context: bool = True
    max_similarity_score: float = 0.0
    total_chunks_searched: int = 0
    indices_searched: list[str] = field(default_factory=list)

    @property
    def formatted_context(self) -> str:
        """Format chunks for prompt injection."""
        if not self.chunks:
            return "[No relevant context found]"

        parts: list[str] = []
        for i, chunk in enumerate(self.chunks, 1):
            parts.append(
                f"### Context {i} — {chunk['source']} ({chunk['doc_type']})\n"
                f"{chunk['content']}"
            )
        return "\n\n---\n\n".join(parts)


class Retriever:
    """
    End-to-end RAG retriever: query → embed → search → filter → assemble.

    In strict_mode (default), the retriever marks context as insufficient
    when the best similarity score falls below the threshold. This prevents
    the LLM from hallucinating when no relevant knowledge exists.
    """

    def __init__(
        self,
        embeddings: BedrockEmbeddings | None = None,
        vector_store: VectorStore | None = None,
        strict_mode: bool | None = None,
    ) -> None:
        self.embeddings = embeddings or BedrockEmbeddings()
        self.vector_store = vector_store or VectorStore()
        self.strict_mode = strict_mode if strict_mode is not None else settings.strict_rag_mode

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        index_filter: list[str] | None = None,
    ) -> RetrievalContext:
        """
        Full retrieval pipeline for a customer query.

        Args:
            query: The customer's (redacted) query text
            top_k: Number of chunks to retrieve
            index_filter: Specific indices to search (default: all)

        Returns:
            RetrievalContext with assembled chunks and metadata
        """
        k = top_k or settings.opensearch.top_k

        # Step 1: Generate query embedding
        try:
            query_vector = self.embeddings.embed_query(query)
        except Exception as e:
            logger.error("Query embedding failed: %s", e)
            return RetrievalContext(
                has_sufficient_context=False,
                indices_searched=[],
            )

        # Step 2: Search vector store
        if index_filter:
            results = self._search_specific_indices(query_vector, index_filter, k)
        else:
            results = self.vector_store.search_all_indices(query_vector, k)

        if not results:
            logger.warning("No results returned from vector search")
            return RetrievalContext(
                has_sufficient_context=False,
                total_chunks_searched=0,
            )

        # Step 3: Apply similarity threshold (strict_mode)
        max_score = max(r.score for r in results)
        threshold = settings.opensearch.similarity_threshold

        if self.strict_mode and max_score < threshold:
            logger.warning(
                "Best similarity score (%.3f) below threshold (%.3f) — strict mode",
                max_score,
                threshold,
            )
            return RetrievalContext(
                has_sufficient_context=False,
                max_similarity_score=max_score,
                total_chunks_searched=len(results),
                indices_searched=list({r.doc_type for r in results}),
            )

        # Step 4: Filter and deduplicate
        filtered = self._deduplicate(results)

        # Step 5: Assemble context
        chunks = [
            {
                "content": r.content,
                "source": r.source,
                "doc_type": r.doc_type,
                "section": r.section,
                "score": r.score,
            }
            for r in filtered[:k]
        ]

        return RetrievalContext(
            chunks=chunks,
            has_sufficient_context=True,
            max_similarity_score=max_score,
            total_chunks_searched=len(results),
            indices_searched=list({r.doc_type for r in results}),
        )

    def _search_specific_indices(
        self,
        query_vector: list[float],
        indices: list[str],
        top_k: int,
    ) -> list[SearchResult]:
        """Search only the specified indices."""
        all_results: list[SearchResult] = []

        for index_name in indices:
            try:
                results = self.vector_store.similarity_search(
                    index_name=index_name,
                    query_vector=query_vector,
                    top_k=top_k,
                )
                all_results.extend(results)
            except Exception as e:
                logger.error("Search failed on index %s: %s", index_name, e)

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results

    def _deduplicate(
        self,
        results: list[SearchResult],
        similarity_cutoff: float = 0.95,
    ) -> list[SearchResult]:
        """
        Remove near-duplicate results based on content overlap.

        Uses a simple content-hash approach to filter identical chunks
        that may appear across indices.
        """
        seen_hashes: set[int] = set()
        unique: list[SearchResult] = []

        for result in results:
            # Use first 200 chars as a fingerprint
            content_hash = hash(result.content[:200])
            if content_hash not in seen_hashes:
                seen_hashes.add(content_hash)
                unique.append(result)

        return unique
