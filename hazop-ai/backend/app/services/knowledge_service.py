"""
Knowledge Service — Document Ingestion & RAG Retrieval

Handles:
  1. Ingesting knowledge documents (Consequence Guidance, Risk Matrix, SOPs, etc.)
  2. Chunking documents into manageable pieces
  3. Embedding chunks using Azure OpenAI
  4. Storing in Cosmos DB with vector embeddings
  5. Retrieving relevant context via vector similarity search

This provides the RAG (Retrieval-Augmented Generation) layer that grounds
the LLM cause/consequence generation in actual company knowledge.
"""

import uuid
import re
from datetime import datetime

from app.services.openai_service import openai_service
from app.services.document_intelligence_knowledge import knowledge_doc_intelligence
from app.database.cosmos_client import cosmos_client


# Chunking configuration
CHUNK_SIZE = 800          # Characters per chunk
CHUNK_OVERLAP = 100       # Overlap between chunks for context continuity
MAX_CHUNKS_PER_DOC = 500  # Safety limit


class KnowledgeService:
    """Manages knowledge document ingestion and retrieval."""

    # ------------------------------------------------------------------
    # Document Ingestion
    # ------------------------------------------------------------------

    async def ingest_document(
        self,
        file_content: bytes,
        filename: str,
        document_type: str,
    ) -> dict:
        """
        Ingest a knowledge document: parse → chunk → embed → store.

        Args:
            file_content: Raw file bytes (PDF)
            filename: Original filename
            document_type: Category (e.g., 'consequence_guidance', 'risk_matrix',
                          'barrier_philosophy', 'sop', 'hazop_reference')

        Returns:
            Summary of ingestion results
        """
        # Step 1: Extract text using Document Intelligence
        text = await self._extract_text(file_content)
        if not text:
            return {"status": "error", "message": "Could not extract text from document"}

        # Step 2: Chunk the text
        chunks = self._chunk_text(text, filename)

        # Step 3: Generate embeddings for all chunks
        chunk_texts = [c["content"] for c in chunks]
        embeddings = await self._generate_embeddings_batched(chunk_texts)

        # Step 4: Store chunks with embeddings in Cosmos DB
        documents = []
        for i, chunk in enumerate(chunks):
            doc = {
                "chunk_id": str(uuid.uuid4()),
                "source_document": filename,
                "document_type": document_type,
                "content": chunk["content"],
                "embedding": embeddings[i],
                "metadata": chunk["metadata"],
                "created_at": datetime.utcnow().isoformat(),
            }
            documents.append(doc)

        stored_count = await cosmos_client.store_knowledge_chunks_batch(documents)

        return {
            "status": "success",
            "filename": filename,
            "document_type": document_type,
            "total_chunks": len(chunks),
            "stored_chunks": stored_count,
            "text_length": len(text),
        }

    async def re_ingest_document(
        self,
        file_content: bytes,
        filename: str,
        document_type: str,
    ) -> dict:
        """Delete existing chunks for this document and re-ingest."""
        deleted = await cosmos_client.delete_knowledge_by_document(filename)
        result = await self.ingest_document(file_content, filename, document_type)
        result["previous_chunks_deleted"] = deleted
        return result

    # ------------------------------------------------------------------
    # RAG Retrieval
    # ------------------------------------------------------------------

    async def retrieve_relevant_context(
        self,
        query: str,
        limit: int = 5,
        document_type: str | None = None,
    ) -> str:
        """
        Retrieve relevant knowledge context for a query.

        Used by the LLM service to ground cause/consequence generation
        in actual company knowledge documents.

        Args:
            query: Natural language query (e.g., "High Pressure in Separator consequences")
            limit: Max chunks to retrieve
            document_type: Optional filter by document type

        Returns:
            Concatenated context string from relevant chunks
        """
        # Generate embedding for the query
        query_embedding = await openai_service.generate_embedding(query)

        # Search Cosmos DB vector index
        results = await cosmos_client.vector_search(
            query_embedding=query_embedding,
            limit=limit,
            document_type=document_type,
        )

        if not results:
            return ""

        # Build context string
        context_parts = []
        for r in results:
            source = r.get("source_document", "Unknown")
            content = r.get("content", "")
            score = r.get("similarity_score", 0)
            context_parts.append(
                f"[Source: {source} | Relevance: {score:.2f}]\n{content}"
            )

        return "\n\n---\n\n".join(context_parts)

    async def retrieve_full_hazop_context(
        self,
        equipment_type: str,
        deviation: str,
        limit: int = 5,
    ) -> str:
        """
        Retrieve comprehensive knowledge context for full HAZOP field extraction.

        Uses a richer query to increase the chance of retrieving knowledge chunks
        about mitigations, control categories, residual risk, PEC, incidents, etc.

        Args:
            equipment_type: Type of equipment (e.g., "Separator")
            deviation: Deviation name (e.g., "High Pressure")
            limit: Max chunks to retrieve

        Returns:
            Concatenated context string from relevant knowledge chunks
        """
        query = (
            f"{deviation} in {equipment_type} - "
            f"intermediate consequences, final impacts, scenario, "
            f"mitigations, control category, CME, KME, "
            f"personnel exposure, residual risk, recommendations, responsibility"
        )
        return await self.retrieve_relevant_context(query=query, limit=limit)

    async def search_knowledge(
        self,
        query: str,
        limit: int = 10,
        document_type: str | None = None,
    ) -> list[dict]:
        """
        Search knowledge base and return structured results.
        Used by the API for direct knowledge queries.
        """
        query_embedding = await openai_service.generate_embedding(query)

        results = await cosmos_client.vector_search(
            query_embedding=query_embedding,
            limit=limit,
            document_type=document_type,
        )

        return [
            {
                "chunk_id": r.get("chunk_id"),
                "source_document": r.get("source_document"),
                "document_type": r.get("document_type"),
                "content": r.get("content"),
                "similarity_score": r.get("similarity_score", 0),
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Text Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str, filename: str) -> list[dict]:
        """
        Split text into overlapping chunks for embedding.

        Strategy:
          - Split by paragraphs first (preserve natural boundaries)
          - If paragraph too long, split by sentences
          - Maintain overlap for context continuity
        """
        # Clean the text
        text = self._clean_text(text)

        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks = []
        current_chunk = ""
        chunk_index = 0

        for para in paragraphs:
            # If adding this paragraph exceeds chunk size, save current and start new
            if len(current_chunk) + len(para) > CHUNK_SIZE and current_chunk:
                chunks.append({
                    "content": current_chunk.strip(),
                    "metadata": {
                        "chunk_index": chunk_index,
                        "source": filename,
                    },
                })
                chunk_index += 1

                # Keep overlap from end of previous chunk
                if len(current_chunk) > CHUNK_OVERLAP:
                    current_chunk = current_chunk[-CHUNK_OVERLAP:] + "\n\n" + para
                else:
                    current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para

            # Safety: if a single paragraph is very long, split by sentences
            if len(current_chunk) > CHUNK_SIZE * 2:
                sentence_chunks = self._split_long_text(current_chunk, filename, chunk_index)
                chunks.extend(sentence_chunks)
                chunk_index += len(sentence_chunks)
                current_chunk = ""

            if len(chunks) >= MAX_CHUNKS_PER_DOC:
                break

        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append({
                "content": current_chunk.strip(),
                "metadata": {
                    "chunk_index": chunk_index,
                    "source": filename,
                },
            })

        return chunks

    def _split_long_text(self, text: str, filename: str, start_index: int) -> list[dict]:
        """Split overly long text by sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current = ""
        idx = start_index

        for sentence in sentences:
            if len(current) + len(sentence) > CHUNK_SIZE and current:
                chunks.append({
                    "content": current.strip(),
                    "metadata": {"chunk_index": idx, "source": filename},
                })
                idx += 1
                current = sentence
            else:
                current = current + " " + sentence if current else sentence

        if current.strip():
            chunks.append({
                "content": current.strip(),
                "metadata": {"chunk_index": idx, "source": filename},
            })

        return chunks

    def _clean_text(self, text: str) -> str:
        """Clean extracted text for chunking."""
        # Remove excessive whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        # Remove excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ------------------------------------------------------------------
    # Embedding Helpers
    # ------------------------------------------------------------------

    async def _extract_text(self, file_content: bytes) -> str:
        """Extract text from a knowledge document using dedicated OCR service."""
        try:
            text = await knowledge_doc_intelligence.extract_text(file_content)
            return text
        except Exception as e:
            print(f"[Knowledge] Text extraction failed: {e}")
            return ""

    async def _generate_embeddings_batched(
        self,
        texts: list[str],
        batch_size: int = 16,
    ) -> list[list[float]]:
        """Generate embeddings in batches to respect API limits."""
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = await openai_service.generate_embeddings_batch(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings


# Module-level singleton
knowledge_service = KnowledgeService()
