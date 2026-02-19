"""
Knowledge Service — Document Ingestion & RAG Retrieval

Handles:
  1. Ingesting knowledge documents (Consequence Guidance, Risk Matrix, SOPs, etc.)
  2. Chunking documents into manageable pieces (table-aware — tables are never split)
  3. Embedding chunks using Azure OpenAI
  4. Storing in Cosmos DB with vector embeddings
  5. Retrieving relevant context via vector similarity search

This provides the RAG (Retrieval-Augmented Generation) layer that grounds
the LLM cause/consequence generation in actual company knowledge.

Chunking strategy:
  - Content is extracted per-page (PDF) or as a single block (DOCX)
  - [TABLE]...[/TABLE] blocks are treated as atomic units — never split mid-row
  - Regular text paragraphs are chunked at CHUNK_SIZE with sentence-boundary overlap
  - Each chunk carries page_number in its metadata for traceability
"""

import asyncio
import uuid
import re
from datetime import datetime

from app.services.openai_service import openai_service
from app.services.document_intelligence_knowledge import knowledge_doc_intelligence
from app.database.cosmos_client import cosmos_client
from app.core.config import get_settings

settings = get_settings()


# Chunking configuration
CHUNK_SIZE = 1400         # Characters per chunk (raised from 800 — HAZOP scenarios need room)
CHUNK_OVERLAP = 150       # Target overlap in chars (sentence-boundary aware)
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
            file_content:  Raw file bytes (PDF or DOCX)
            filename:      Original filename (used for file-type detection)
            document_type: Category (e.g., 'consequence_guidance', 'risk_matrix',
                          'barrier_philosophy', 'sop', 'hazop_reference')

        Returns:
            Summary of ingestion results
        """
        # Step 1: Extract structured pages (text + markdown tables)
        pages = await self._extract_pages(file_content, filename)
        if not pages:
            return {"status": "error", "message": "Could not extract text from document"}

        # Step 2: Chunk pages — tables are atomic, text uses sentence-boundary overlap
        chunks = self._chunk_pages(pages, filename)

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
        total_chars = sum(len(p["content"]) for p in pages)

        return {
            "status": "success",
            "filename": filename,
            "document_type": document_type,
            "total_chunks": len(chunks),
            "stored_chunks": stored_count,
            "text_length": total_chars,
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
            query:         Natural language query
            limit:         Max chunks to retrieve
            document_type: Optional filter by document type

        Returns:
            Concatenated context string from relevant chunks
        """
        # Pre-check: skip embedding call if no chunks exist for this type
        has_chunks = await cosmos_client.check_knowledge_chunks_exist(document_type)
        if not has_chunks:
            print(f"[RAG] No knowledge chunks found for type: {document_type or 'any'}")
            return ""

        query_embedding = await openai_service.generate_embedding(query)

        results = await cosmos_client.vector_search(
            query_embedding=query_embedding,
            limit=limit,
            document_type=document_type,
        )

        if not results:
            return ""

        context_parts = []
        for r in results:
            source = r.get("source_document", "Unknown")
            page = r.get("metadata", {}).get("page")
            page_info = f", Page {page}" if page else ""
            score = r.get("similarity_score", 0)
            content = r.get("content", "")
            context_parts.append(
                f"[Source: {source}{page_info} | Relevance: {score:.2f}]\n{content}"
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
        """
        query = (
            f"{deviation} in {equipment_type} - "
            f"intermediate consequences, final impacts, scenario, "
            f"mitigations, control category, CME, KME, "
            f"personnel exposure, residual risk, recommendations, responsibility"
        )
        return await self.retrieve_relevant_context(query=query, limit=limit)

    async def retrieve_overpressure_table_context(self, limit: int = 3) -> str:
        """
        Targeted retrieval for the pressure significance / hole size table.

        Fetches the document chunk(s) containing the pressure ratio → leak size
        lookup table (e.g., Guideline for Consequence Development in PHA Studies,
        Document #60.400.301.07, Page 14).  The LLM uses this table to determine
        the appropriate assumed hole size and consequence description for a given
        calculated pressure ratio — nothing is hardcoded in Python.
        """
        query = (
            "pressure significance MAWP overpressure ratio hole size leak consequence "
            "vessel rupture flange instrumentation 1.1 1.3 2.0 "
            "guideline consequence development PHA studies"
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
        # Pre-check: skip embedding call if no chunks exist for this type
        has_chunks = await cosmos_client.check_knowledge_chunks_exist(document_type)
        if not has_chunks:
            print(f"[RAG] No knowledge chunks found for type: {document_type or 'any'}")
            return []

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
                "page": r.get("metadata", {}).get("page"),
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Text Extraction
    # ------------------------------------------------------------------

    async def _extract_pages(self, file_content: bytes, filename: str) -> list[dict]:
        """Extract structured pages (text + markdown tables) from a document."""
        try:
            return await knowledge_doc_intelligence.extract_pages(file_content, filename)
        except Exception as e:
            print(f"[Knowledge] Page extraction failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Table-Aware Chunking
    # ------------------------------------------------------------------

    def _chunk_pages(self, pages: list[dict], filename: str) -> list[dict]:
        """
        Chunk a list of page dicts into embedding-ready chunks.

        Tables (marked with [TABLE]...[/TABLE]) are treated as atomic units
        and placed in their own chunk — they are never split mid-row.
        Regular text is chunked at CHUNK_SIZE with sentence-boundary overlap.
        Each chunk carries page_number for source traceability.
        """
        all_chunks: list[dict] = []
        chunk_index = 0

        for page_doc in pages:
            page_num = page_doc["page"]
            content = self._clean_text(page_doc["content"])

            segments = self._split_into_segments(content)
            page_chunks, chunk_index = self._chunk_segments(
                segments, filename, page_num, chunk_index
            )
            all_chunks.extend(page_chunks)

            if len(all_chunks) >= MAX_CHUNKS_PER_DOC:
                break

        return all_chunks

    def _split_into_segments(self, text: str) -> list[dict]:
        """
        Split text into an ordered list of segments.

        Each segment is either:
          {"type": "table",  "content": "[TABLE]...[/TABLE]"}
          {"type": "text",   "content": "paragraph text"}

        Tables are extracted first so the chunker can treat them as atomic units.
        """
        segments: list[dict] = []
        table_pattern = re.compile(r'\[TABLE\].*?\[/TABLE\]', re.DOTALL)
        last_end = 0

        for match in table_pattern.finditer(text):
            # Text before this table
            before = text[last_end:match.start()]
            if before.strip():
                for para in re.split(r'\n\s*\n', before):
                    para = para.strip()
                    if para:
                        segments.append({"type": "text", "content": para})

            segments.append({"type": "table", "content": match.group()})
            last_end = match.end()

        # Remaining text after the last table
        remaining = text[last_end:]
        if remaining.strip():
            for para in re.split(r'\n\s*\n', remaining):
                para = para.strip()
                if para:
                    segments.append({"type": "text", "content": para})

        return segments

    def _chunk_segments(
        self,
        segments: list[dict],
        filename: str,
        page_num: int,
        start_index: int,
    ) -> tuple[list[dict], int]:
        """
        Build chunks from a list of segments.

        Tables → always their own chunk (even if they exceed CHUNK_SIZE).
        Text  → accumulated up to CHUNK_SIZE, then saved with sentence-boundary overlap.

        Returns:
            (list of chunk dicts, next chunk_index)
        """
        chunks: list[dict] = []
        current_chunk = ""
        idx = start_index

        def _save_text_chunk(content: str) -> None:
            nonlocal idx
            if content.strip():
                chunks.append({
                    "content": content.strip(),
                    "metadata": {"chunk_index": idx, "source": filename, "page": page_num},
                })
                idx += 1

        for seg in segments:
            if seg["type"] == "table":
                # Flush any accumulated text first
                _save_text_chunk(current_chunk)
                current_chunk = ""

                # Table gets its own chunk
                chunks.append({
                    "content": seg["content"].strip(),
                    "metadata": {
                        "chunk_index": idx,
                        "source": filename,
                        "page": page_num,
                        "is_table": True,
                    },
                })
                idx += 1

            else:
                para = seg["content"]

                if len(current_chunk) + len(para) + 2 > CHUNK_SIZE and current_chunk:
                    _save_text_chunk(current_chunk)

                    # Sentence-boundary aware overlap
                    overlap = self._extract_overlap(current_chunk)
                    current_chunk = (overlap + "\n\n" + para) if overlap else para
                else:
                    current_chunk = (current_chunk + "\n\n" + para) if current_chunk else para

                # Safety: if a single paragraph is very long, split by sentences
                if len(current_chunk) > CHUNK_SIZE * 2:
                    sentence_chunks, idx = self._split_long_text(
                        current_chunk, filename, page_num, idx
                    )
                    chunks.extend(sentence_chunks)
                    current_chunk = ""

        # Last accumulated text
        _save_text_chunk(current_chunk)

        return chunks, idx

    def _extract_overlap(self, text: str) -> str:
        """
        Extract trailing content from text for use as overlap in the next chunk.

        Tries to end on a sentence boundary so the overlap makes semantic sense.
        Returns up to CHUNK_OVERLAP characters, trimmed to the last complete sentence.
        """
        if len(text) <= CHUNK_OVERLAP:
            return text.strip()

        tail = text[-(CHUNK_OVERLAP * 2):]
        sentences = re.split(r'(?<=[.!?])\s+', tail)

        result = ""
        for sent in reversed(sentences):
            candidate = (sent + " " + result).strip() if result else sent
            if len(candidate) <= CHUNK_OVERLAP:
                result = candidate
            else:
                break

        return result.strip() if result else text[-CHUNK_OVERLAP:].strip()

    def _split_long_text(
        self,
        text: str,
        filename: str,
        page_num: int,
        start_index: int,
    ) -> tuple[list[dict], int]:
        """Split an oversized paragraph by sentence boundaries."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: list[dict] = []
        current = ""
        idx = start_index

        for sentence in sentences:
            if len(current) + len(sentence) > CHUNK_SIZE and current:
                chunks.append({
                    "content": current.strip(),
                    "metadata": {"chunk_index": idx, "source": filename, "page": page_num},
                })
                idx += 1
                current = sentence
            else:
                current = (current + " " + sentence) if current else sentence

        if current.strip():
            chunks.append({
                "content": current.strip(),
                "metadata": {"chunk_index": idx, "source": filename, "page": page_num},
            })
            idx += 1

        return chunks, idx

    def _clean_text(self, text: str) -> str:
        """Clean extracted text before chunking."""
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ------------------------------------------------------------------
    # Embedding Helpers
    # ------------------------------------------------------------------

    async def _generate_embeddings_batched(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """
        Generate embeddings in TPM-aware batches with inter-batch delays.

        Batch size and delay are calculated from the configured TPM limit so
        large document ingestions don't hit Azure OpenAI rate limits.

        Adapted from retriever.py handle_documents() — same thresholds and
        formula, but uses asyncio.sleep() instead of time.sleep() to avoid
        blocking FastAPI's event loop.
        """
        total = len(texts)
        if total == 0:
            return []

        # Dynamic batch size based on total chunk count (same as retriever.py)
        if total < 300:
            batch_size = 50
            category = "SMALL"
        elif total < 1000:
            batch_size = 40
            category = "MEDIUM"
        elif total < 2000:
            batch_size = 30
            category = "LARGE"
        else:
            batch_size = 25
            category = "XLARGE"

        # Delay formula: stay safely under the TPM quota (same as retriever.py)
        tpm_limit = settings.EMBEDDING_TPM_LIMIT
        safety_factor = 0.95
        avg_tokens_per_chunk = 375          # ~1400 chars ÷ ~3.7 chars/token
        processing_time_sec = 1.0           # approximate API round-trip time

        safe_tokens_per_sec = (tpm_limit * safety_factor) / 60
        tokens_per_batch = batch_size * avg_tokens_per_chunk
        delay = max(0.1, (tokens_per_batch / safe_tokens_per_sec) - processing_time_sec)

        total_batches = (total + batch_size - 1) // batch_size
        print(
            f"[Embedding] {total} chunks [{category}] — "
            f"batch_size={batch_size}, delay={delay:.1f}s, "
            f"TPM_limit={tpm_limit}"
        )

        all_embeddings: list[list[float]] = []

        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            print(f"[Embedding] Batch {batch_num}/{total_batches} ({len(batch)} chunks)")

            batch_embeddings = await openai_service.generate_embeddings_batch(batch)
            all_embeddings.extend(batch_embeddings)

            # Throttle between batches — skip delay after the last batch
            if i + batch_size < total:
                await asyncio.sleep(delay)

        return all_embeddings


# Module-level singleton
knowledge_service = KnowledgeService()
