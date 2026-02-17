"""
Vector Store — OpenSearch Serverless k-NN client for RAG.

Handles indexing, querying, and metadata filtering across the three
knowledge base indices: Policy Documents, Historical Tickets, Compliance Rules.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single vector search result with metadata."""

    content: str
    source: str
    doc_type: str
    section: str
    score: float
    metadata: dict[str, Any]


class VectorStore:
    """
    OpenSearch Serverless vector store client.

    Manages three indices:
    - policy-documents: Product policy PDFs
    - historical-tickets: Resolved support tickets (few-shot examples)
    - compliance-rules: Regulatory and compliance guidelines
    """

    def __init__(
        self,
        endpoint: str | None = None,
        region: str | None = None,
    ) -> None:
        self.endpoint = endpoint or settings.opensearch.endpoint
        self.region = region or settings.aws.region

        if self.endpoint:
            self.client = self._create_client()
        else:
            self.client = None
            logger.warning("No OpenSearch endpoint configured")

    def _create_client(self) -> OpenSearch:
        """Create an authenticated OpenSearch client."""
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, self.region, "aoss")

        # Clean endpoint URL
        host = self.endpoint.replace("https://", "").replace("http://", "")
        if host.endswith("/"):
            host = host[:-1]

        return OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20,
            timeout=30,
        )

    def create_index(self, index_name: str) -> None:
        """
        Create a k-NN vector index with the standard mapping.

        Uses HNSW algorithm for approximate nearest neighbor search.
        """
        if not self.client:
            raise RuntimeError("OpenSearch client not initialized")

        body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 512,
                },
            },
            "mappings": {
                "properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": settings.opensearch.embedding_dimension,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {
                                "ef_construction": 512,
                                "m": 16,
                            },
                        },
                    },
                    "content": {"type": "text"},
                    "source": {"type": "keyword"},
                    "doc_type": {"type": "keyword"},
                    "section": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": False},
                },
            },
        }

        if self.client.indices.exists(index=index_name):
            logger.info("Index %s already exists", index_name)
            return

        self.client.indices.create(index=index_name, body=body)
        logger.info("Created index: %s", index_name)

    def index_documents(
        self,
        index_name: str,
        documents: list[dict[str, Any]],
        batch_size: int = 50,
    ) -> int:
        """
        Bulk upsert documents into the specified index.

        Each document must have: embedding, content, source, doc_type, section, metadata
        Returns the number of successfully indexed documents.
        """
        if not self.client:
            raise RuntimeError("OpenSearch client not initialized")

        success_count = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            bulk_body: list[str] = []

            for doc in batch:
                doc_id = doc.get("id", f"{doc['source']}_{i}")
                action = json.dumps({"index": {"_index": index_name, "_id": doc_id}})
                bulk_body.append(action)
                bulk_body.append(json.dumps(doc))

            try:
                response = self.client.bulk(body="\n".join(bulk_body) + "\n")
                if not response.get("errors", True):
                    success_count += len(batch)
                else:
                    # Count individual successes
                    for item in response.get("items", []):
                        if item.get("index", {}).get("status") in (200, 201):
                            success_count += 1
            except Exception as e:
                logger.error("Bulk indexing failed for batch starting at %d: %s", i, e)

            if (i + batch_size) % 500 == 0:
                logger.info("Indexed %d/%d documents", i + batch_size, len(documents))

        logger.info(
            "Indexed %d/%d documents into %s", success_count, len(documents), index_name
        )
        return success_count

    def similarity_search(
        self,
        index_name: str,
        query_vector: list[float],
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """
        Perform k-NN similarity search on the specified index.

        Args:
            index_name: Target index to search
            query_vector: Query embedding vector
            top_k: Number of results to return (default from settings)
            filters: Optional metadata filters (e.g., {"doc_type": "policy"})

        Returns:
            List of SearchResult ordered by similarity score (descending)
        """
        if not self.client:
            raise RuntimeError("OpenSearch client not initialized")

        k = top_k or settings.opensearch.top_k

        # Build k-NN query
        knn_query: dict[str, Any] = {
            "knn": {
                "embedding": {
                    "vector": query_vector,
                    "k": k,
                },
            },
        }

        # Add filters if provided
        if filters:
            filter_clauses = []
            for field, value in filters.items():
                if isinstance(value, list):
                    filter_clauses.append({"terms": {field: value}})
                else:
                    filter_clauses.append({"term": {field: value}})

            knn_query = {
                "bool": {
                    "must": [knn_query],
                    "filter": filter_clauses,
                },
            }

        body = {
            "size": k,
            "query": knn_query,
            "_source": ["content", "source", "doc_type", "section", "metadata"],
        }

        response = self.client.search(index=index_name, body=body)

        results: list[SearchResult] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            results.append(
                SearchResult(
                    content=source.get("content", ""),
                    source=source.get("source", ""),
                    doc_type=source.get("doc_type", ""),
                    section=source.get("section", ""),
                    score=hit.get("_score", 0.0),
                    metadata=source.get("metadata", {}),
                )
            )

        return results

    def search_all_indices(
        self,
        query_vector: list[float],
        top_k: int | None = None,
    ) -> list[SearchResult]:
        """
        Search across all three knowledge base indices and merge results.

        Results are sorted by score across all indices.
        """
        k = top_k or settings.opensearch.top_k
        all_results: list[SearchResult] = []

        indices = [
            settings.opensearch.policy_index,
            settings.opensearch.historical_index,
            settings.opensearch.compliance_index,
        ]

        for index_name in indices:
            try:
                results = self.similarity_search(
                    index_name=index_name,
                    query_vector=query_vector,
                    top_k=k,
                )
                all_results.extend(results)
            except Exception as e:
                logger.error("Search failed on index %s: %s", index_name, e)

        # Sort by score and take top-k overall
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:k]

    def delete_index(self, index_name: str) -> None:
        """Delete an index and all its documents."""
        if not self.client:
            raise RuntimeError("OpenSearch client not initialized")

        if self.client.indices.exists(index=index_name):
            self.client.indices.delete(index=index_name)
            logger.info("Deleted index: %s", index_name)
