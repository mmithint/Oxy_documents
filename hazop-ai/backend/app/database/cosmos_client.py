"""
MongoDB Client — Local MongoDB or Azure Cosmos DB MongoDB vCore

Handles two responsibilities:
  1. Structured data storage — Nodes, Equipment, HAZOP reports, SME reviews
  2. Vector search — Knowledge document embeddings for similarity retrieval

Collections (in database "Oxy"):
  - nodes              : PIDNode documents (validated equipment & instruments)
  - hazop_reports      : Generated HAZOP reports with deviations
  - knowledge_chunks   : Embedded knowledge documents for vector similarity search
  - sme_reviews        : Review audit trail

Vector Search:
  - On Cosmos DB MongoDB vCore: uses native cosmosSearch HNSW index
  - On Local MongoDB: uses brute-force cosine similarity (no special index needed)
  The client auto-detects which mode to use.
"""

import math
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from typing import Any
from app.core.config import get_settings

settings = get_settings()


class MongoDBClient:
    """MongoDB client for structured and vector data. Works with local MongoDB and Cosmos DB."""

    def __init__(self):
        self._client: MongoClient | None = None
        self._db: Database | None = None
        self._is_cosmos: bool = False

    def connect(self) -> None:
        """Establish connection to MongoDB."""
        self._client = MongoClient(settings.COSMOS_CONNECTION_STRING)
        self._db = self._client[settings.COSMOS_DATABASE_NAME]

        # Detect if this is Cosmos DB or local MongoDB
        self._is_cosmos = "cosmos.azure.com" in settings.COSMOS_CONNECTION_STRING

        # Verify connection
        self._client.admin.command("ping")
        print(f"Connected to MongoDB: {settings.COSMOS_DATABASE_NAME} ({'Cosmos DB' if self._is_cosmos else 'Local'})")

    def close(self) -> None:
        """Close the connection."""
        if self._client:
            self._client.close()

    @property
    def db(self) -> Database:
        if self._db is None:
            self.connect()
        return self._db

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    @property
    def nodes_collection(self) -> Collection:
        return self.db["nodes"]

    @property
    def hazop_reports_collection(self) -> Collection:
        return self.db["hazop_reports"]

    @property
    def knowledge_chunks_collection(self) -> Collection:
        return self.db["knowledge_chunks"]

    @property
    def sme_reviews_collection(self) -> Collection:
        return self.db["sme_reviews"]

    # ------------------------------------------------------------------
    # Index Setup
    # ------------------------------------------------------------------

    def setup_indexes(self) -> None:
        """Create required indexes. Call once during app startup."""
        # Standard indexes
        self.nodes_collection.create_index("node_id", unique=True)
        self.hazop_reports_collection.create_index("node_id")
        self.hazop_reports_collection.create_index("report_id", unique=True)
        self.hazop_reports_collection.create_index("status")
        self.sme_reviews_collection.create_index("deviation_id")

        # Knowledge chunks indexes
        self.knowledge_chunks_collection.create_index("document_type")
        self.knowledge_chunks_collection.create_index("source_document")
        self.knowledge_chunks_collection.create_index("chunk_id", unique=True)

        # Vector index — only on Cosmos DB
        if self._is_cosmos:
            self._create_cosmos_vector_index()

        print("Database indexes created successfully")

    def _create_cosmos_vector_index(self) -> None:
        """Create Cosmos DB native vector search index (HNSW)."""
        try:
            self.db.command({
                "createIndexes": "knowledge_chunks",
                "indexes": [
                    {
                        "name": "vector_search_index",
                        "key": {"embedding": "cosmosSearch"},
                        "cosmosSearchOptions": {
                            "kind": "vector-hnsw",
                            "similarity": "COS",
                            "dimensions": 1536,
                            "m": 16,
                            "efConstruction": 64,
                        },
                    }
                ],
            })
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"Vector index creation skipped: {e}")

    # ------------------------------------------------------------------
    # Node Operations
    # ------------------------------------------------------------------

    async def save_node(self, node_data: dict) -> str:
        """Save or update a PIDNode."""
        self.nodes_collection.update_one(
            {"node_id": node_data["node_id"]},
            {"$set": node_data},
            upsert=True,
        )
        return node_data["node_id"]

    async def get_node(self, node_id: str) -> dict | None:
        """Retrieve a node by ID."""
        return self.nodes_collection.find_one(
            {"node_id": node_id},
            {"_id": 0},
        )

    async def get_all_nodes(self) -> list[dict]:
        """Retrieve all nodes."""
        return list(self.nodes_collection.find({}, {"_id": 0}))

    # ------------------------------------------------------------------
    # HAZOP Report Operations
    # ------------------------------------------------------------------

    async def save_hazop_report(self, report_data: dict) -> str:
        """Save or update a HAZOP report."""
        report_id = report_data.get("report_id")
        self.hazop_reports_collection.update_one(
            {"report_id": report_id},
            {"$set": report_data},
            upsert=True,
        )
        return report_id

    async def get_hazop_report(self, report_id: str) -> dict | None:
        """Retrieve a HAZOP report by ID."""
        return self.hazop_reports_collection.find_one(
            {"report_id": report_id},
            {"_id": 0},
        )

    async def get_hazop_by_node(self, node_id: str) -> dict | None:
        """Retrieve the latest HAZOP report for a node."""
        cursor = self.hazop_reports_collection.find(
            {"node_id": node_id},
            {"_id": 0},
        ).sort("version", -1).limit(1)

        results = list(cursor)
        return results[0] if results else None

    async def update_deviation_status(
        self,
        report_id: str,
        deviation_id: str,
        status: str,
        reviewer: str | None = None,
        comments: str | None = None,
    ) -> bool:
        """Update the review status of a single deviation within a report."""
        update_fields: dict[str, Any] = {
            "deviations.$[dev].status": status,
        }
        if reviewer:
            update_fields["deviations.$[dev].reviewed_by"] = reviewer
        if comments:
            update_fields["deviations.$[dev].review_comments"] = comments

        result = self.hazop_reports_collection.update_one(
            {"report_id": report_id},
            {"$set": update_fields},
            array_filters=[{"dev.deviation_id": deviation_id}],
        )
        return result.modified_count > 0

    # ------------------------------------------------------------------
    # Knowledge Chunk Operations
    # ------------------------------------------------------------------

    async def store_knowledge_chunk(self, chunk_data: dict) -> str:
        """Store a knowledge document chunk with its embedding."""
        result = self.knowledge_chunks_collection.insert_one(chunk_data)
        return str(result.inserted_id)

    async def store_knowledge_chunks_batch(self, chunks: list[dict]) -> int:
        """Store multiple knowledge chunks at once."""
        if not chunks:
            return 0
        result = self.knowledge_chunks_collection.insert_many(chunks)
        return len(result.inserted_ids)

    async def vector_search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        document_type: str | None = None,
    ) -> list[dict]:
        """
        Perform vector similarity search on knowledge chunks.

        - Cosmos DB: uses native $search with cosmosSearch
        - Local MongoDB: uses brute-force cosine similarity in Python

        Args:
            query_embedding: Query vector (1536 dimensions)
            limit: Max results to return
            document_type: Optional filter by document type

        Returns:
            List of matching chunks with similarity scores, sorted by relevance
        """
        if self._is_cosmos:
            return self._cosmos_vector_search(query_embedding, limit, document_type)
        else:
            return self._local_vector_search(query_embedding, limit, document_type)

    def _cosmos_vector_search(
        self,
        query_embedding: list[float],
        limit: int,
        document_type: str | None,
    ) -> list[dict]:
        """Cosmos DB native vector search using HNSW index."""
        pipeline: list[dict] = [
            {
                "$search": {
                    "cosmosSearch": {
                        "vector": query_embedding,
                        "path": "embedding",
                        "k": limit,
                    },
                    "returnStoredSource": True,
                }
            },
            {
                "$addFields": {
                    "similarity_score": {"$meta": "searchScore"},
                }
            },
        ]

        if document_type:
            pipeline.append({"$match": {"document_type": document_type}})

        pipeline.append({"$project": {"_id": 0, "embedding": 0}})
        pipeline.append({"$limit": limit})

        return list(self.knowledge_chunks_collection.aggregate(pipeline))

    def _local_vector_search(
        self,
        query_embedding: list[float],
        limit: int,
        document_type: str | None,
    ) -> list[dict]:
        """
        Brute-force cosine similarity search for local MongoDB.
        Fetches all chunks with embeddings and computes similarity in Python.
        Works fine for prototype-scale data (< 10K chunks).
        """
        query_filter: dict = {}
        if document_type:
            query_filter["document_type"] = document_type

        # Only fetch chunks that have embeddings
        query_filter["embedding"] = {"$exists": True, "$ne": None}

        chunks = list(self.knowledge_chunks_collection.find(
            query_filter,
            {"_id": 0},
        ))

        if not chunks:
            return []

        # Compute cosine similarity for each chunk
        scored_chunks = []
        for chunk in chunks:
            embedding = chunk.get("embedding")
            if not embedding:
                continue

            score = _cosine_similarity(query_embedding, embedding)

            # Remove the embedding from output (too large)
            chunk_copy = {k: v for k, v in chunk.items() if k != "embedding"}
            chunk_copy["similarity_score"] = score
            scored_chunks.append(chunk_copy)

        # Sort by similarity (descending) and return top results
        scored_chunks.sort(key=lambda x: x["similarity_score"], reverse=True)
        return scored_chunks[:limit]

    async def delete_knowledge_by_document(self, source_document: str) -> int:
        """Delete all chunks from a specific source document (for re-ingestion)."""
        result = self.knowledge_chunks_collection.delete_many(
            {"source_document": source_document}
        )
        return result.deleted_count


# --------------------------------------------------------------------------
# Cosine Similarity (for local MongoDB vector search)
# --------------------------------------------------------------------------

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Returns value between -1 and 1 (higher = more similar).
    """
    if len(vec_a) != len(vec_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    magnitude_a = math.sqrt(sum(a * a for a in vec_a))
    magnitude_b = math.sqrt(sum(b * b for b in vec_b))

    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0

    return dot_product / (magnitude_a * magnitude_b)


# Module-level singleton
cosmos_client = MongoDBClient()
