"""
Unit tests for the RAG Retriever module.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag.retriever import Retriever, RetrievalContext
from rag.vector_store import SearchResult


class TestRetrievalContext:
    """Tests for RetrievalContext model."""

    def test_sufficient_context_formatting(self):
        ctx = RetrievalContext(
            chunks=[
                {"content": "Policy covers dental.", "source": "policy.pdf", "doc_type": "policy"},
                {"content": "Deductible is $500.", "source": "faq.md", "doc_type": "faq"},
            ],
            has_sufficient_context=True,
        )
        formatted = ctx.formatted_context
        assert "Policy covers dental" in formatted
        assert "Deductible is $500" in formatted

    def test_insufficient_context_message(self):
        ctx = RetrievalContext(has_sufficient_context=False)
        assert "No relevant context found" in ctx.formatted_context

    def test_empty_chunks(self):
        ctx = RetrievalContext(chunks=[], has_sufficient_context=True)
        assert "No relevant context found" in ctx.formatted_context


class TestStrictMode:
    """Tests for strict_mode RAG behavior."""

    def test_strict_mode_insufficient_score(self):
        """When best score is below threshold, context should be marked insufficient."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024

        mock_store = MagicMock()
        mock_store.search_all_indices.return_value = [
            SearchResult(
                content="Some text",
                source="doc.pdf",
                doc_type="policy",
                section="1",
                score=0.3,  # Below default threshold (0.7)
                metadata={},
            ),
        ]

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=mock_store,
            strict_mode=True,
        )

        result = retriever.retrieve("What is my coverage?")
        assert result.has_sufficient_context is False
        assert result.max_similarity_score == 0.3

    def test_strict_mode_sufficient_score(self):
        """When best score is above threshold, context should be available."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024

        mock_store = MagicMock()
        mock_store.search_all_indices.return_value = [
            SearchResult(
                content="Your dental coverage includes...",
                source="policy.pdf",
                doc_type="policy",
                section="4.2",
                score=0.85,
                metadata={},
            ),
        ]

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=mock_store,
            strict_mode=True,
        )

        result = retriever.retrieve("What dental coverage do I have?")
        assert result.has_sufficient_context is True
        assert len(result.chunks) > 0

    def test_non_strict_mode_returns_context(self):
        """In non-strict mode, low-score results are still returned."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024

        mock_store = MagicMock()
        mock_store.search_all_indices.return_value = [
            SearchResult(
                content="Something",
                source="doc.pdf",
                doc_type="policy",
                section="1",
                score=0.3,
                metadata={},
            ),
        ]

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=mock_store,
            strict_mode=False,
        )

        result = retriever.retrieve("Query")
        assert result.has_sufficient_context is True

    def test_empty_results(self):
        """No search results → insufficient context."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024

        mock_store = MagicMock()
        mock_store.search_all_indices.return_value = []

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=mock_store,
        )

        result = retriever.retrieve("Query")
        assert result.has_sufficient_context is False

    def test_embedding_failure(self):
        """Embedding failure → insufficient context."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.side_effect = Exception("API error")

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=MagicMock(),
        )

        result = retriever.retrieve("Query")
        assert result.has_sufficient_context is False


class TestDeduplication:
    """Tests for result deduplication."""

    def test_duplicate_content_removed(self):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024

        duplicate_result = SearchResult(
            content="Your policy covers outpatient visits with a $50 copay.",
            source="policy.pdf",
            doc_type="policy",
            section="3.1",
            score=0.9,
            metadata={},
        )

        mock_store = MagicMock()
        mock_store.search_all_indices.return_value = [
            duplicate_result,
            duplicate_result,  # Same content from different index
        ]

        retriever = Retriever(
            embeddings=mock_embeddings,
            vector_store=mock_store,
            strict_mode=False,
        )

        result = retriever.retrieve("Outpatient coverage")
        assert len(result.chunks) == 1  # Deduplicated
