"""
Upload Routes — P&ID and Knowledge Document Upload

Endpoints:
  POST /api/upload/pid              → Upload P&ID (PDF only), parse, extract equipment & instruments
  POST /api/upload/knowledge        → Upload knowledge doc, chunk, embed, store for RAG
  GET  /api/upload/pid/list         → List all uploaded P&ID files
  GET  /api/upload/nodes            → List all extracted nodes
  GET  /api/upload/nodes/{node_id}  → Get a specific node by ID
"""

import re

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.blob_storage import blob_storage
from app.services.document_intelligence import doc_intelligence
from app.services.knowledge_service import knowledge_service
from app.database.cosmos_client import cosmos_client
from app.models.api_models import UploadResponse
from app.models.pid_models import PIDNode

router = APIRouter()


@router.post("/pid", response_model=UploadResponse)
async def upload_pid(
    file: UploadFile = File(..., description="P&ID file (PDF only)"),
    node_id: str = Form(default=None, description="Optional node ID to assign"),
):
    """
    Upload a P&ID PDF for processing.

    Flow:
      1. Validate PDF format
      2. Upload to Azure Blob Storage
      3. Azure Document Intelligence OCR → text chunks → Claude text extraction
      4. Convert PDF pages to images → Claude Vision extraction
      5. Merge both extraction results (deduplicate by tag)
      6. Enrich from tables (design pressure, temperature)
      7. Store extracted node in Cosmos DB
      8. Return structured extraction result for SME review
    """
    original_filename = file.filename or "unknown.pdf"
    content_type = file.content_type or ""

    if content_type and content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {content_type}. Only PDF files are accepted.",
        )

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Check if this file was already processed
    existing = await cosmos_client.get_node_by_filename(original_filename)
    if existing:
        node = PIDNode(**{k: v for k, v in existing.items() if k in PIDNode.model_fields})
        return UploadResponse(
            message=f"File '{original_filename}' was already processed. Returning cached extraction.",
            file_name=original_filename,
            blob_url=existing.get("pid_drawings", [""])[0] if existing.get("pid_drawings") else "",
            nodes=[node],
            confidence_score=None,
            ocr_chunks=existing.get("ocr_chunks", []),
            llm_raw_output=existing.get("llm_raw_output"),
            vision_raw_output=None,
            merge_summary=existing.get("merge_summary"),
        )

    # Step 1: Upload to Blob Storage
    blob_result = await blob_storage.upload_pid_file(
        file_content=file_content,
        original_filename=original_filename,
        content_type="application/pdf",
    )

    # Step 2: Parse PDF — OCR text (Path 1) + Vision (Path 2) → merge
    extraction = await doc_intelligence.parse_pid(
        file_content=file_content,
        source_filename=original_filename,
        node_id=node_id,
    )

    # Step 3: Attach blob URL + normalize drawing number
    for node in extraction.nodes:
        node.pid_drawings.append(blob_result["blob_url"])
        node.drawing_number = _normalize_drawing_number(node.drawing_number)

    # Step 4: Store extracted nodes in Cosmos DB
    for node in extraction.nodes:
        node_doc = node.model_dump(mode="json")
        node_doc["ocr_chunks"] = extraction.ocr_chunks
        node_doc["llm_raw_output"] = extraction.llm_raw_output
        node_doc["source_file"] = original_filename
        await cosmos_client.save_node(node_doc)

    return UploadResponse(
        message=(
            f"P&ID parsed successfully. "
            f"Found {sum(len(n.equipment) for n in extraction.nodes)} equipment "
            f"and {sum(len(n.instruments) for n in extraction.nodes)} instruments."
        ),
        file_name=original_filename,
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

    blob_result = await blob_storage.upload_knowledge_document(
        file_content=file_content,
        original_filename=file.filename or "unknown.pdf",
        content_type=file.content_type or "application/pdf",
    )

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


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    """Get a specific node by ID."""
    node = await cosmos_client.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return node


def _normalize_drawing_number(raw: str | None) -> str | None:
    """Strip trailing revision suffix, e.g. "APC No. 4020(c)" → "APC No. 4020"."""
    if not raw:
        return None
    cleaned = re.sub(r'\s*\([^)]+\)\s*$', '', raw.strip())
    return cleaned or None
