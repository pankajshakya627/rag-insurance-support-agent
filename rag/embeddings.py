"""
Embeddings module — Bedrock Titan Embeddings wrapper.

Provides both single-text and batch embedding for the RAG pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3

from config.settings import settings

logger = logging.getLogger(__name__)

bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws.region)


class BedrockEmbeddings:
    """
    Wrapper around Amazon Bedrock Titan Text Embeddings V2.

    Usage:
        embeddings = BedrockEmbeddings()
        vector = embeddings.embed_query("What is my deductible?")
        vectors = embeddings.embed_documents(["doc1", "doc2"])
    """

    def __init__(
        self,
        model_id: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self.model_id = model_id or settings.bedrock.embedding_model_id
        self.dimension = dimension or settings.opensearch.embedding_dimension

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding vector for a single query text."""
        return self._invoke(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embedding vectors for a batch of documents.

        Note: Titan Embeddings processes one text at a time.
        For large batches, consider parallelizing with ThreadPoolExecutor.
        """
        vectors: list[list[float]] = []
        for i, text in enumerate(texts):
            try:
                vector = self._invoke(text)
                vectors.append(vector)
            except Exception as e:
                logger.error("Embedding failed for document %d: %s", i, e)
                vectors.append([0.0] * self.dimension)

            if (i + 1) % 100 == 0:
                logger.info("Embedded %d/%d documents", i + 1, len(texts))

        return vectors

    def _invoke(self, text: str) -> list[float]:
        """Invoke the Bedrock Titan Embeddings model."""
        # Titan V2 supports configurable output dimensions
        body = {
            "inputText": text,
            "dimensions": self.dimension,
            "normalize": True,
        }

        response = bedrock_runtime.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )

        result = json.loads(response["body"].read().decode("utf-8"))
        return result["embedding"]
