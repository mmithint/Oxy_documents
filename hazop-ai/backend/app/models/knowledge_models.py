"""
Knowledge Models — Represent embedded knowledge documents for RAG retrieval.
Used with Cosmos DB vector search for cause/consequence generation.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class KnowledgeChunk(BaseModel):
    """A chunk of knowledge document with its embedding vector."""
    chunk_id: Optional[str] = Field(None, description="Unique chunk identifier")
    source_document: str = Field(..., description="Original document name")
    document_type: str = Field(..., description="Type: 'consequence_guidance', 'risk_matrix', 'barrier_philosophy', 'sop', 'hazop_reference'")
    content: str = Field(..., description="Text content of the chunk")
    embedding: Optional[list[float]] = Field(None, description="Vector embedding for similarity search")
    metadata: dict = Field(default_factory=dict, description="Additional metadata (page, section, etc.)")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeSearchResult(BaseModel):
    """Result from vector similarity search."""
    chunk_id: str
    content: str
    source_document: str
    document_type: str
    similarity_score: float = Field(..., description="Cosine similarity score 0-1")
