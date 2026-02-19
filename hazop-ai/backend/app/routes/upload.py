"""
Upload Routes — P&ID and Knowledge Document Upload

Endpoints:
  POST /api/upload/pid              → Upload P&ID, parse, extract equipment & instruments
  POST /api/upload/knowledge        → Upload knowledge doc, chunk, embed, store for RAG
  GET  /api/upload/pid/list         → List all uploaded P&ID files
"""

import re

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.blob_storage import blob_storage
from app.services.document_intelligence import doc_intelligence
from app.services.knowledge_service import knowledge_service
from app.database.cosmos_client import cosmos_client
from app.models.api_models import UploadResponse

router = APIRouter()


@router.post("/pid", response_model=UploadResponse)
async def upload_pid(
    file: UploadFile = File(..., description="P&ID file (PDF or image)"),
    node_id: str = Form(default=None, description="Optional node ID to assign"),
):
    """
    Upload a P&ID file for processing.

    Flow:
      1. Upload file to Azure Blob Storage
      2. Parse with Azure Document Intelligence
      3. Extract equipment and instrument tags
      4. Store extracted node in Cosmos DB
      5. Return structured extraction result for SME review
    """
    # Validate file type
    allowed_types = {
        "application/pdf", "image/png", "image/jpeg",
        "image/tiff", "image/bmp",
    }
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: PDF, PNG, JPEG, TIFF, BMP",
        )

    # Read file content
    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Step 1: Upload to Blob Storage
    blob_result = await blob_storage.upload_pid_file(
        file_content=file_content,
        original_filename=file.filename or "unknown.pdf",
        content_type=file.content_type or "application/pdf",
    )

    # Step 2: Parse with Document Intelligence
    extraction = await doc_intelligence.parse_pid(
        file_content=file_content,
        source_filename=file.filename or "unknown.pdf",
        node_id=node_id,
    )

    # Step 3: Update blob URL + normalize drawing number in extraction
    for node in extraction.nodes:
        node.pid_drawings.append(blob_result["blob_url"])
        node.drawing_number = _normalize_drawing_number(node.drawing_number)

    # Step 4: Store extracted nodes in Cosmos DB (include LLM data)
    for node in extraction.nodes:
        node_doc = node.model_dump(mode="json")
        node_doc["ocr_chunks"] = extraction.ocr_chunks
        node_doc["llm_raw_output"] = extraction.llm_raw_output
        await cosmos_client.save_node(node_doc)

    # Step 5: Return result
    return UploadResponse(
        message=f"P&ID parsed successfully. Found {sum(len(n.equipment) for n in extraction.nodes)} equipment and {sum(len(n.instruments) for n in extraction.nodes)} instruments.",
        file_name=file.filename or "unknown.pdf",
        blob_url=blob_result["blob_url"],
        nodes=extraction.nodes,
        confidence_score=extraction.confidence_score,
        ocr_chunks=extraction.ocr_chunks,
        llm_raw_output=extraction.llm_raw_output,
        vision_raw_output=extraction.vision_raw_output,
        merge_summary=extraction.merge_summary,
    )


@router.post("/knowledge")
async def upload_knowledge_document(
    file: UploadFile = File(..., description="Knowledge document (PDF, DOCX, or XLSX)"),
    document_type: str = Form(
        ...,
        description="Document type: consequence_guidance, risk_matrix, barrier_philosophy, sop, hazop_reference",
    ),
):
    """
    Upload a knowledge document for RAG retrieval.

    Flow:
      1. Upload to Blob Storage
      2. Extract text with Document Intelligence
      3. Chunk text into manageable pieces
      4. Generate embeddings with Azure OpenAI
      5. Store chunks + embeddings in Cosmos DB (vector search enabled)
    """
    valid_types = {
        "consequence_guidance", "risk_matrix", "barrier_philosophy",
        "sop", "hazop_reference",
    }
    if document_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid document_type. Allowed: {', '.join(valid_types)}",
        )

    allowed_knowledge_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if file.content_type and file.content_type not in allowed_knowledge_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: PDF, DOCX, XLSX",
        )

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Upload to blob
    blob_result = await blob_storage.upload_knowledge_document(
        file_content=file_content,
        original_filename=file.filename or "unknown.pdf",
        content_type=file.content_type or "application/pdf",
    )

    # Ingest: parse → chunk → embed → store
    ingestion_result = await knowledge_service.ingest_document(
        file_content=file_content,
        filename=file.filename or "unknown.pdf",
        document_type=document_type,
    )

    return {
        "message": "Knowledge document ingested successfully",
        "blob_url": blob_result["blob_url"],
        **ingestion_result,
    }


@router.get("/pid/list")
async def list_pid_files():
    """List all uploaded P&ID files."""
    files = await blob_storage.list_pid_files()
    return {"files": files, "count": len(files)}


@router.get("/nodes")
async def list_nodes():
    """List all extracted nodes."""
    nodes = await cosmos_client.get_all_nodes()
    return {"nodes": nodes, "count": len(nodes)}


def _normalize_drawing_number(raw: str | None) -> str | None:
    """
    Strip trailing revision suffix from an APC / drawing number.
    e.g. "APC No. 4020(c)" → "APC No. 4020"
         "DWG-4020-A"      → "DWG-4020-A"  (no trailing parens, unchanged)
    """
    if not raw:
        return None
    cleaned = re.sub(r'\s*\([^)]+\)\s*$', '', raw.strip())
    return cleaned or None


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    """Get a specific node by ID."""
    node = await cosmos_client.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return node
