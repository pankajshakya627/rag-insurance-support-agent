"""
Knowledge Base Indexing Pipeline — processes documents into the vector store.

CLI script that reads documents (PDFs, text files), extracts text,
chunks them, generates embeddings, and loads into OpenSearch.

Usage:
    python -m rag.indexing_pipeline --source-dir ./documents --index policy-documents --doc-type policy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import boto3

from config.settings import settings
from rag.embeddings import BedrockEmbeddings
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

textract = boto3.client("textract")


def main() -> None:
    """CLI entry point for the indexing pipeline."""
    parser = argparse.ArgumentParser(description="Index documents into the RAG knowledge base")
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Directory containing documents to index",
    )
    parser.add_argument(
        "--index",
        required=True,
        help="Target OpenSearch index name",
    )
    parser.add_argument(
        "--doc-type",
        required=True,
        choices=["policy", "historical", "compliance"],
        help="Document type category",
    )
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size in tokens")
    parser.add_argument("--chunk-overlap", type=int, default=64, help="Overlap between chunks")
    parser.add_argument("--batch-size", type=int, default=25, help="Indexing batch size")
    parser.add_argument("--create-index", action="store_true", help="Create index if not exists")
    parser.add_argument("--s3-source", help="S3 URI prefix instead of local dir")

    args = parser.parse_args()

    # Initialize components
    embeddings = BedrockEmbeddings()
    vector_store = VectorStore()

    # Optionally create the index
    if args.create_index:
        vector_store.create_index(args.index)

    # Load documents
    if args.s3_source:
        documents = _load_from_s3(args.s3_source)
    else:
        documents = _load_from_directory(args.source_dir)

    logger.info("Loaded %d documents from source", len(documents))

    if not documents:
        logger.warning("No documents found, exiting")
        sys.exit(0)

    # Chunk documents
    all_chunks: list[dict[str, Any]] = []
    for doc in documents:
        chunks = _chunk_text(
            text=doc["text"],
            source=doc["source"],
            doc_type=args.doc_type,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        all_chunks.extend(chunks)

    logger.info("Created %d chunks from %d documents", len(all_chunks), len(documents))

    # Generate embeddings
    texts = [c["content"] for c in all_chunks]
    vectors = embeddings.embed_documents(texts)

    # Attach embeddings to chunks
    for chunk, vector in zip(all_chunks, vectors):
        chunk["embedding"] = vector

    # Index into OpenSearch
    indexed = vector_store.index_documents(
        index_name=args.index,
        documents=all_chunks,
        batch_size=args.batch_size,
    )

    logger.info(
        "Pipeline complete: %d/%d chunks indexed into %s",
        indexed,
        len(all_chunks),
        args.index,
    )


def _load_from_directory(dir_path: str) -> list[dict[str, str]]:
    """Load text documents from a local directory."""
    documents: list[dict[str, str]] = []
    source_dir = Path(dir_path)

    if not source_dir.exists():
        logger.error("Source directory does not exist: %s", dir_path)
        return documents

    for file_path in source_dir.rglob("*"):
        if file_path.is_file():
            try:
                text = _extract_file_text(file_path)
                if text.strip():
                    documents.append({
                        "source": file_path.name,
                        "text": text,
                        "path": str(file_path),
                    })
            except Exception as e:
                logger.error("Failed to load %s: %s", file_path, e)

    return documents


def _extract_file_text(file_path: Path) -> str:
    """Extract text content from a file based on its extension."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf_text(file_path)
    elif suffix in (".txt", ".md", ".csv"):
        return file_path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".json":
        data = json.loads(file_path.read_text())
        if isinstance(data, dict):
            return data.get("text", data.get("content", json.dumps(data, indent=2)))
        return json.dumps(data, indent=2)
    else:
        logger.warning("Unsupported file type: %s", suffix)
        return ""


def _extract_pdf_text(file_path: Path) -> str:
    """Extract text from PDF using pypdf (local) as primary method."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(file_path))
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        logger.warning("pypdf not installed, cannot extract PDF locally")
        return ""


def _load_from_s3(s3_prefix: str) -> list[dict[str, str]]:
    """Load documents from an S3 prefix."""
    s3 = boto3.client("s3")
    bucket, prefix = _parse_s3_uri(s3_prefix)

    documents: list[dict[str, str]] = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            try:
                response = s3.get_object(Bucket=bucket, Key=key)
                content = response["Body"].read().decode("utf-8", errors="replace")
                documents.append({
                    "source": key.split("/")[-1],
                    "text": content,
                    "path": f"s3://{bucket}/{key}",
                })
            except Exception as e:
                logger.error("Failed to load s3://%s/%s: %s", bucket, key, e)

    return documents


def _chunk_text(
    text: str,
    source: str,
    doc_type: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[dict[str, Any]]:
    """
    Split text into overlapping chunks.

    Uses a simple word-based chunking strategy. Each chunk includes
    metadata for retrieval filtering.
    """
    words = text.split()
    chunks: list[dict[str, Any]] = []

    if not words:
        return chunks

    step = max(chunk_size - chunk_overlap, 1)

    for i in range(0, len(words), step):
        chunk_words = words[i : i + chunk_size]
        chunk_text = " ".join(chunk_words)

        if len(chunk_text.strip()) < 20:
            continue

        chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()

        chunks.append({
            "id": chunk_id,
            "content": chunk_text,
            "source": source,
            "doc_type": doc_type,
            "section": f"chunk_{i // step + 1}",
            "metadata": {
                "word_offset": i,
                "word_count": len(chunk_words),
                "source_path": source,
            },
        })

    return chunks


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3://bucket/prefix into (bucket, prefix)."""
    path = uri.replace("s3://", "")
    parts = path.split("/", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


if __name__ == "__main__":
    main()
