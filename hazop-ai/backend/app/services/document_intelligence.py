"""
Azure AI Document Intelligence Service — P&ID Parser

Extracts structured data from P&ID drawings (PDF/images):
  - Equipment tags and names
  - Instrument tags
  - Text content and layout
  - Table data

Dual Extraction Pipeline:
  P&ID file (PDF/image)
      ├── Path 1: Document Intelligence (OCR) → text chunks → LLM text extraction
      ├── Path 2: Convert to images → GPT-4 Vision → LLM *sees* the diagram
      └── Merge both results (deduplicate by tag) → PIDNode

  Path 2 (Vision) catches tags that OCR misses:
    - Tags inside circles / instrument bubbles
    - Tags inside equipment boxes / symbols
    - Small or distorted text
"""

import re
import base64
import fitz  # PyMuPDF — PDF to image conversion
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential

from app.core.config import get_settings
from app.models.pid_models import (
    PIDNode, Equipment, Instrument,
    PIDExtractionResult,
    LineConnection, ControlLoop, DeviationLocation,
)
from app.services.openai_service import openai_service
from app.services.dxf_extractor import (
    extract_entities_from_dxf,
    format_entities_for_llm,
    get_layer_summary,
)

settings = get_settings()


# --------------------------------------------------------------------------
# Tag Pattern Definitions (kept for regex fallback)
# --------------------------------------------------------------------------

# Equipment: tag prefix → human-readable type
EQUIPMENT_TAG_PATTERNS: dict[str, str] = {
    r"\bV-\d{3,5}\b": "Separator",
    r"\bD-\d{3,5}\b": "Vessel",
    r"\bE-\d{3,5}\b": "Heat Exchanger",
    r"\bP-\d{3,5}\b": "Pump",
    r"\bC-\d{3,5}\b": "Compressor",
    r"\bK-\d{3,5}\b": "Compressor",
    r"\bT-\d{3,5}\b": "Tank",
    r"\bHDR-\d{3,5}\b": "Header",
    r"\bFL-\d{3,5}\b": "Flare",
    r"\bS-\d{3,5}\b": "Scrubber",
    r"\bKO-\d{3,5}\b": "Knockout Drum",
}

# Instrument: tag prefix → human-readable type
INSTRUMENT_TAG_PATTERNS: dict[str, str] = {
    r"\bPSHH-\d{3,5}\b": "Pressure Switch High High",
    r"\bPSH-\d{3,5}\b": "Pressure Switch High High",
    r"\bPSV-\d{3,5}\b": "Pressure Safety Valve",
    r"\bPCV-\d{3,5}\b": "Pressure Control Valve",
    r"\bPT-\d{3,5}\b": "Pressure Transmitter",
    r"\bPI-\d{3,5}\b": "Pressure Indicator",
    r"\bPDI-\d{3,5}\b": "Pressure Differential Indicator",
    r"\bLSHH-\d{3,5}\b": "Level Switch High High",
    r"\bLSH-\d{3,5}\b": "Level Switch High High",
    r"\bLSLL-\d{3,5}\b": "Level Switch Low Low",
    r"\bLSL-\d{3,5}\b": "Level Switch Low Low",
    r"\bLCV-\d{3,5}\b": "Level Control Valve",
    r"\bLT-\d{3,5}\b": "Level Transmitter",
    r"\bLG-\d{3,5}\b": "Level Gauge",
    r"\bLI-\d{3,5}\b": "Level Indicator",
    r"\bTSHH-\d{3,5}\b": "Temperature Switch High High",
    r"\bTSH-\d{3,5}\b": "Temperature Switch High High",
    r"\bTSLL-\d{3,5}\b": "Temperature Switch Low Low",
    r"\bTT-\d{3,5}\b": "Temperature Transmitter",
    r"\bTI-\d{3,5}\b": "Temperature Indicator",
    r"\bFSV-\d{3,5}\b": "Flow Safety Valve",
    r"\bFCV-\d{3,5}\b": "Flow Control Valve",
    r"\bFT-\d{3,5}\b": "Flow Transmitter",
    r"\bSDV-\d{3,5}\b": "Emergency Shutdown Valve",
    r"\bESD-\d{3,5}\b": "Emergency Shutdown Valve",
    r"\bXV-\d{3,5}\b": "Emergency Shutdown Valve",
    r"\bBDV-\d{3,5}\b": "Blowdown Valve",
    r"\bGD-\d{3,5}\b": "Gas Detector",
    r"\bFD-\d{3,5}\b": "Fire Detector",
}

# Generic catch-all: any 2-5 letter prefix followed by dash and 3-5 digits
# Used to pick up tags the specific patterns above miss (e.g. new types)
GENERIC_TAG_PATTERN = r"\b([A-Z]{2,5})-(\d{3,5})\b"

EQUIPMENT_KEYWORD_MAP: dict[str, str] = {
    "separator": "Separator",
    "production separator": "Separator",
    "test separator": "Separator",
    "3-phase separator": "Separator",
    "2-phase separator": "Separator",
    "header": "Header",
    "production header": "Header",
    "compressor": "Compressor",
    "pump": "Pump",
    "heat exchanger": "Heat Exchanger",
    "cooler": "Heat Exchanger",
    "heater": "Heat Exchanger",
    "scrubber": "Scrubber",
    "suction scrubber": "Scrubber",
    "knockout drum": "Knockout Drum",
    "ko drum": "Knockout Drum",
    "flare": "Flare",
    "tank": "Tank",
    "storage tank": "Tank",
}

# Known equipment tag prefixes (used to distinguish equipment vs instrument
# in the generic catch-all regex)
_EQUIPMENT_PREFIXES = {"V", "D", "E", "P", "C", "K", "T", "HDR", "FL", "S", "KO"}


class DocumentIntelligenceService:
    """Parses P&ID drawings using Azure AI Document Intelligence."""

    def __init__(self):
        self._client: DocumentIntelligenceClient | None = None

    @property
    def client(self) -> DocumentIntelligenceClient:
        if self._client is None:
            self._client = DocumentIntelligenceClient(
                endpoint=settings.AZURE_DOC_INTELLIGENCE_ENDPOINT,
                credential=AzureKeyCredential(settings.AZURE_DOC_INTELLIGENCE_KEY),
            )
        return self._client

    async def parse_pid(
        self,
        file_content: bytes,
        source_filename: str,
        node_id: str | None = None,
    ) -> PIDExtractionResult:
        """
        Parse a P&ID file and extract equipment and instruments.

        Dual Extraction Pipeline:
          1. Azure Document Intelligence OCR → text
          2. Chunk OCR text → LLM text extraction (Path 1)
          3. Convert file to images → GPT-4 Vision extraction (Path 2)
          4. Merge results from both paths (deduplicate by tag)
          5. Enrich from tables
          6. Fallback to regex if both LLM paths fail
        """
        # ---- STEP 1: Azure Document Intelligence OCR ----
        poller = self.client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=AnalyzeDocumentRequest(bytes_source=file_content),
        )
        result = poller.result()

        raw_text = self._extract_full_text(result)

        # ---- STEP 2: Chunk OCR text ----
        chunks = self._chunk_ocr_text(raw_text, chunk_size=1000, overlap=100)
        self._log_chunks(chunks, source_filename)

        # ---- PATH 1: LLM text extraction (existing) ----
        llm_result = None
        use_llm = True

        try:
            llm_result = await openai_service.extract_pid_data(chunks, source_filename)
            print(f"[P&ID LLM] Text extraction complete for {source_filename}")
        except Exception as e:
            print(f"[P&ID LLM] Text extraction failed: {e}")
            use_llm = False

        if use_llm and llm_result:
            text_equipment = self._parse_llm_equipment(llm_result)
            text_instruments = self._parse_llm_instruments(llm_result)
            node_name = llm_result.get("node_name") or self._detect_node_name(raw_text) or f"Node from {source_filename}"
            system = llm_result.get("system") or "Hydrocarbon Processing Systems"
            description = llm_result.get("description") or f"Extracted from {source_filename}"
            drawing_number = llm_result.get("drawing_number")  # e.g. "APC No. 4020(c)"
            pid_summary = llm_result.get("pid_summary") or None
            flow_description = llm_result.get("flow_description") or None
            line_connectivity = _parse_line_connectivity(llm_result)
            control_loops = _parse_control_loops(llm_result)
            deviation_locations = _parse_deviation_locations(llm_result)
        else:
            # Regex fallback for text path
            text_equipment = self._detect_equipment(raw_text)
            text_instruments = self._detect_instruments(raw_text)
            node_name = self._detect_node_name(raw_text) or f"Node from {source_filename}"
            system = "Hydrocarbon Processing Systems"
            description = f"Regex-extracted from {source_filename}"
            drawing_number = None
            pid_summary = None
            flow_description = None
            line_connectivity = []
            control_loops = []
            deviation_locations = []

        # ---- PATH 2: Vision extraction (NEW) ----
        vision_equipment: list[Equipment] = []
        vision_instruments: list[Instrument] = []
        vision_result: dict | None = None

        try:
            images_base64 = self._convert_to_images(
                file_content=file_content,
                content_type=self._guess_content_type(source_filename),
            )

            if images_base64:
                print(f"[P&ID Vision] Sending {len(images_base64)} page(s) to GPT-4 Vision...")
                vision_result = await openai_service.extract_pid_data_with_vision(
                    images_base64=images_base64,
                    source_filename=source_filename,
                    ocr_hint=raw_text[:3000] if raw_text else None,
                )
                vision_equipment = self._parse_llm_equipment(vision_result)
                vision_instruments = self._parse_llm_instruments(vision_result)
                # Vision has a direct view of the diagram — prefer its results if available
                if vision_result.get("pid_summary"):
                    pid_summary = vision_result["pid_summary"]
                if vision_result.get("flow_description"):
                    flow_description = vision_result["flow_description"]
                if vision_result.get("line_connectivity"):
                    line_connectivity = _parse_line_connectivity(vision_result)
                if vision_result.get("control_loops"):
                    control_loops = _parse_control_loops(vision_result)
                if vision_result.get("deviation_locations"):
                    deviation_locations = _parse_deviation_locations(vision_result)
                print(
                    f"[P&ID Vision] Found {len(vision_equipment)} equipment, "
                    f"{len(vision_instruments)} instruments via vision"
                )
        except Exception as e:
            print(f"[P&ID Vision] Vision extraction failed (non-blocking): {e}")

        # ---- STEP 4: Merge text + vision results (deduplicate by tag) ----
        equipment_list = self._merge_equipment(text_equipment, vision_equipment)
        instrument_list = self._merge_instruments(text_instruments, vision_instruments)

        print(
            f"[P&ID Merge] Final: {len(equipment_list)} equipment "
            f"(text={len(text_equipment)}, vision={len(vision_equipment)}), "
            f"{len(instrument_list)} instruments "
            f"(text={len(text_instruments)}, vision={len(vision_instruments)})"
        )

        # ---- STEP 5: Enrich from tables ----
        self._enrich_from_tables(result, equipment_list)

        # ---- Build node ----
        auto_node_id = node_id or self._generate_node_id(source_filename)

        node = PIDNode(
            node_id=auto_node_id,
            node_name=node_name,
            system=system,
            equipment=equipment_list,
            instruments=instrument_list,
            pid_drawings=[source_filename],
            description=description,
            drawing_number=drawing_number,
            pid_summary=pid_summary,
            flow_description=flow_description,
            line_connectivity=line_connectivity,
            control_loops=control_loops,
            deviation_locations=deviation_locations,
        )

        confidence = self._calculate_confidence(equipment_list, instrument_list, raw_text)

        # ---- Build merge summary ----
        text_eq_tags = sorted({eq.tag for eq in text_equipment})
        text_inst_tags = sorted({inst.tag for inst in text_instruments})
        vision_eq_tags = sorted({eq.tag for eq in vision_equipment})
        vision_inst_tags = sorted({inst.tag for inst in vision_instruments})
        final_eq_tags = sorted({eq.tag for eq in equipment_list})
        final_inst_tags = sorted({inst.tag for inst in instrument_list})

        # Tags added only by vision (not found by text)
        vision_only_eq = sorted(set(vision_eq_tags) - set(text_eq_tags))
        vision_only_inst = sorted(set(vision_inst_tags) - set(text_inst_tags))

        merge_summary = {
            "path1_text": {
                "equipment_count": len(text_equipment),
                "instrument_count": len(text_instruments),
                "equipment_tags": text_eq_tags,
                "instrument_tags": text_inst_tags,
            },
            "path2_vision": {
                "equipment_count": len(vision_equipment),
                "instrument_count": len(vision_instruments),
                "equipment_tags": vision_eq_tags,
                "instrument_tags": vision_inst_tags,
            },
            "merged_final": {
                "equipment_count": len(equipment_list),
                "instrument_count": len(instrument_list),
                "equipment_tags": final_eq_tags,
                "instrument_tags": final_inst_tags,
            },
            "vision_added": {
                "equipment_tags": vision_only_eq,
                "instrument_tags": vision_only_inst,
                "equipment_count": len(vision_only_eq),
                "instrument_count": len(vision_only_inst),
            },
        }

        return PIDExtractionResult(
            source_file=source_filename,
            nodes=[node],
            raw_text=raw_text[:5000],
            confidence_score=confidence,
            ocr_chunks=chunks,
            llm_raw_output=llm_result,
            vision_raw_output=vision_result,
            merge_summary=merge_summary,
        )

    # ------------------------------------------------------------------
    # DXF Extraction Path (DWG uploads — no OCR, no Vision)
    # ------------------------------------------------------------------

    async def parse_pid_from_dxf(
        self,
        dxf_content: bytes,
        source_filename: str,
        node_id: str | None = None,
    ) -> PIDExtractionResult:
        """
        Parse a DXF file and extract equipment and instruments directly from
        CAD text entities — no Azure Document Intelligence OCR, no Vision.

        This is used when the user uploads a DWG file.  The DWG is first
        converted to DXF by ODA File Converter (in the upload route), then
        this method reads the text entities using ezdxf.

        Why this is better than PDF-OCR for DWG:
          - Tags are exact strings — no misreads (no "V-l210" for "V-1210")
          - Spatial coordinates help associate instruments with equipment
          - Layer names classify entity types before sending to the LLM

        Pipeline:
          1. ezdxf extracts TEXT/MTEXT entities → [{text, x, y, layer}]
          2. Entities are formatted and sent to the LLM
          3. LLM returns equipment[] and instruments[] (same JSON schema as OCR path)
          4. Build PIDNode and PIDExtractionResult (same structure as parse_pid())
        """
        # ---- Step 1: Extract text entities from DXF ----
        try:
            entities = extract_entities_from_dxf(dxf_content)
        except Exception as exc:
            print(f"[DXF Extract] Entity extraction failed: {exc}")
            entities = []

        layer_summary = get_layer_summary(entities)
        print(f"[DXF Extract] Layer summary: {layer_summary}")

        # Format for LLM
        entity_text = format_entities_for_llm(entities)

        # ---- Step 2: LLM extraction from DXF entities ----
        llm_result: dict | None = None
        try:
            llm_result = await openai_service.extract_pid_from_dxf_entities(
                entity_text=entity_text,
                source_filename=source_filename,
            )
        except Exception as exc:
            print(f"[DXF LLM] Failed: {exc}")

        # ---- Step 3: Parse LLM output ----
        if llm_result:
            equipment_list = self._parse_llm_equipment(llm_result)
            instrument_list = self._parse_llm_instruments(llm_result)
            node_name = (
                llm_result.get("node_name")
                or f"Node from {source_filename}"
            )
            system = llm_result.get("system") or "Hydrocarbon Processing Systems"
            description = (
                llm_result.get("description")
                or f"Extracted from DWG: {source_filename}"
            )
            drawing_number = llm_result.get("drawing_number")
            pid_summary = llm_result.get("pid_summary") or None
            flow_description = llm_result.get("flow_description") or None
            line_connectivity = _parse_line_connectivity(llm_result)
            control_loops = _parse_control_loops(llm_result)
            deviation_locations = _parse_deviation_locations(llm_result)
        else:
            # Regex fallback on the raw entity text
            print("[DXF Extract] LLM failed — falling back to regex on entity text")
            equipment_list = self._detect_equipment(entity_text)
            instrument_list = self._detect_instruments(entity_text)
            node_name = f"Node from {source_filename}"
            system = "Hydrocarbon Processing Systems"
            description = f"Regex-extracted from DWG: {source_filename}"
            drawing_number = None
            pid_summary = None
            flow_description = None
            line_connectivity = []
            control_loops = []
            deviation_locations = []

        # ---- Step 4: Build node ----
        auto_node_id = node_id or self._generate_node_id(source_filename)

        node = PIDNode(
            node_id=auto_node_id,
            node_name=node_name,
            system=system,
            equipment=equipment_list,
            instruments=instrument_list,
            pid_drawings=[source_filename],
            description=description,
            drawing_number=_normalize_drawing_number(drawing_number),
            pid_summary=pid_summary,
            flow_description=flow_description,
            line_connectivity=line_connectivity,
            control_loops=control_loops,
            deviation_locations=deviation_locations,
        )

        confidence = self._calculate_confidence(equipment_list, instrument_list, entity_text)

        # Build a DXF-specific extraction summary
        dxf_summary = {
            "extraction_method": "dxf_entity_extraction",
            "entity_count": len(entities),
            "layer_summary": layer_summary,
            "equipment_count": len(equipment_list),
            "instrument_count": len(instrument_list),
            "equipment_tags": sorted({eq.tag for eq in equipment_list}),
            "instrument_tags": sorted({inst.tag for inst in instrument_list}),
        }

        return PIDExtractionResult(
            source_file=source_filename,
            nodes=[node],
            raw_text=entity_text[:5000],
            confidence_score=confidence,
            ocr_chunks=[entity_text],   # single "chunk" — already structured
            llm_raw_output=llm_result,
            vision_raw_output=None,     # no Vision pass for DXF path
            merge_summary=dxf_summary,
        )

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_ocr_text(
        self, text: str, chunk_size: int = 1000, overlap: int = 100
    ) -> list[str]:
        """
        Split OCR text into chunks by paragraph boundaries.
        Falls back to character-based splitting if paragraphs are too large.
        Max 20 chunks.
        """
        if not text:
            return []

        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) + 2 <= chunk_size:
                current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # If a single paragraph exceeds chunk_size, split it
                if len(para) > chunk_size:
                    start = 0
                    while start < len(para):
                        end = start + chunk_size
                        chunks.append(para[start:end])
                        start = end - overlap
                    current_chunk = ""
                else:
                    # Start new chunk with overlap from previous
                    if current_chunk and overlap > 0:
                        overlap_text = current_chunk[-overlap:]
                        current_chunk = f"{overlap_text}\n\n{para}"
                    else:
                        current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        # Cap at 20 chunks
        return chunks[:20]

    def _log_chunks(self, chunks: list[str], filename: str) -> None:
        """Print each OCR chunk to console for debugging."""
        print(f"\n{'='*60}")
        print(f"[OCR Chunks] {filename} — {len(chunks)} chunks")
        print(f"{'='*60}")
        for i, chunk in enumerate(chunks):
            print(f"\n--- Chunk {i+1}/{len(chunks)} ({len(chunk)} chars) ---")
            print(chunk[:500])
            if len(chunk) > 500:
                print(f"  ... ({len(chunk) - 500} more chars)")
        print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # LLM Output Parsers
    # ------------------------------------------------------------------

    def _parse_llm_equipment(self, llm_result: dict) -> list[Equipment]:
        """Build Equipment list from LLM output. Accepts any type string."""
        equipment_list: list[Equipment] = []
        seen_tags: set[str] = set()

        for item in llm_result.get("equipment", []):
            tag = item.get("tag", "").strip().upper()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)

            raw_type = (item.get("equipment_type") or "Other").strip()
            # Normalize pipeline / pipe variants → "Piping" (SME-preferred term)
            if raw_type.lower() in ("pipeline", "pipe line", "pipe", "piping", "pipework"):
                raw_type = "Piping"

            equipment_list.append(Equipment(
                tag=tag,
                name=item.get("name") or tag,
                equipment_type=raw_type,
                design_pressure=_safe_float(item.get("design_pressure")),
                design_temperature=_safe_float(item.get("design_temperature")),
                operating_pressure=_safe_float(item.get("operating_pressure")),
                operating_temperature=_safe_float(item.get("operating_temperature")),
            ))

        return equipment_list

    def _parse_llm_instruments(self, llm_result: dict) -> list[Instrument]:
        """Build Instrument list from LLM output. Accepts any type string."""
        instrument_list: list[Instrument] = []
        seen_tags: set[str] = set()

        for item in llm_result.get("instruments", []):
            tag = item.get("tag", "").strip().upper()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)

            instrument_list.append(Instrument(
                tag=tag,
                instrument_type=item.get("instrument_type") or "Other",
                setpoint=_safe_float(item.get("setpoint")),
                associated_equipment_tag=item.get("associated_equipment_tag"),
            ))

        return instrument_list

    # ------------------------------------------------------------------
    # OCR Helpers
    # ------------------------------------------------------------------

    def _extract_full_text(self, result) -> str:
        """Extract all text content from Document Intelligence result."""
        if not result or not result.content:
            return ""
        return result.content

    # ------------------------------------------------------------------
    # Regex Fallback Methods
    # ------------------------------------------------------------------

    def _detect_equipment(self, text: str) -> list[Equipment]:
        """Detect equipment tags from OCR text using pattern matching."""
        equipment_list: list[Equipment] = []
        seen_tags: set[str] = set()

        for pattern, eq_type in EQUIPMENT_TAG_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                tag = match.upper()
                if tag not in seen_tags:
                    seen_tags.add(tag)
                    equipment_list.append(Equipment(
                        tag=tag,
                        name=self._find_equipment_name(text, tag),
                        equipment_type=eq_type,
                    ))

        # Generic catch-all for equipment tags not in specific patterns
        for prefix, number in re.findall(GENERIC_TAG_PATTERN, text):
            prefix = prefix.upper()
            tag = f"{prefix}-{number}"
            if tag not in seen_tags and prefix in _EQUIPMENT_PREFIXES:
                seen_tags.add(tag)
                equipment_list.append(Equipment(
                    tag=tag,
                    name=self._find_equipment_name(text, tag),
                    equipment_type="Other",
                ))

        return equipment_list

    def _detect_instruments(self, text: str) -> list[Instrument]:
        """Detect instrument tags from OCR text using pattern matching."""
        instrument_list: list[Instrument] = []
        seen_tags: set[str] = set()

        for pattern, inst_type in INSTRUMENT_TAG_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                tag = match.upper()
                if tag not in seen_tags:
                    seen_tags.add(tag)
                    associated_eq = self._find_associated_equipment(tag)
                    instrument_list.append(Instrument(
                        tag=tag,
                        instrument_type=inst_type,
                        associated_equipment_tag=associated_eq,
                    ))

        # Generic catch-all for instrument tags not matched above
        for prefix, number in re.findall(GENERIC_TAG_PATTERN, text):
            prefix = prefix.upper()
            tag = f"{prefix}-{number}"
            if tag not in seen_tags and prefix not in _EQUIPMENT_PREFIXES:
                seen_tags.add(tag)
                instrument_list.append(Instrument(
                    tag=tag,
                    instrument_type=f"{prefix} (unclassified)",
                    associated_equipment_tag=self._find_associated_equipment(tag),
                ))

        return instrument_list

    def _find_equipment_name(self, text: str, tag: str) -> str:
        """Try to find the descriptive name near an equipment tag."""
        text_lower = text.lower()
        for keyword, eq_type in EQUIPMENT_KEYWORD_MAP.items():
            if keyword in text_lower:
                tag_pos = text_lower.find(tag.lower())
                kw_pos = text_lower.find(keyword)
                if tag_pos >= 0 and kw_pos >= 0 and abs(tag_pos - kw_pos) < 200:
                    return f"{keyword.title()} ({tag})"
        return tag

    def _find_associated_equipment(self, instrument_tag: str) -> str | None:
        """
        Try to find associated equipment tag by shared numeric suffix.
        e.g., PSHH-1210 → V-1210
        """
        numbers = re.findall(r'\d+', instrument_tag)
        if numbers:
            return numbers[-1]
        return None

    # ------------------------------------------------------------------
    # Table Enrichment
    # ------------------------------------------------------------------

    def _enrich_from_tables(self, result, equipment_list: list[Equipment]) -> None:
        """Extract design parameters from tables in the document."""
        if not result or not hasattr(result, 'tables') or not result.tables:
            return

        for table in result.tables:
            if not hasattr(table, 'cells'):
                continue
            for cell in table.cells:
                content = cell.content if hasattr(cell, 'content') else ""
                content_lower = content.lower()

                if "design pressure" in content_lower or "design press" in content_lower:
                    pressure_match = re.search(r'(\d+\.?\d*)\s*(psig|psi|bar|kpa)', content_lower)
                    if pressure_match:
                        pressure_val = float(pressure_match.group(1))
                        for eq in equipment_list:
                            if eq.design_pressure is None:
                                eq.design_pressure = pressure_val

                if "design temp" in content_lower:
                    temp_match = re.search(r'(\d+\.?\d*)\s*(°?f|°?c)', content_lower)
                    if temp_match:
                        temp_val = float(temp_match.group(1))
                        for eq in equipment_list:
                            if eq.design_temperature is None:
                                eq.design_temperature = temp_val

    # ------------------------------------------------------------------
    # Image Conversion (for Vision Extraction)
    # ------------------------------------------------------------------

    def _convert_to_images(
        self,
        file_content: bytes,
        content_type: str,
    ) -> list[dict]:
        """
        Convert uploaded file to base64-encoded images for GPT-4 Vision.

        For PDFs: renders each page as a PNG image using PyMuPDF.
        For images: encodes the raw bytes directly to base64.

        Returns list of {"base64": str, "media_type": str}
        """
        images: list[dict] = []

        if content_type == "application/pdf":
            # PDF → render each page as PNG
            try:
                doc = fitz.open(stream=file_content, filetype="pdf")
                # Limit to first 5 pages to keep token usage reasonable
                max_pages = min(len(doc), 5)
                for page_num in range(max_pages):
                    page = doc.load_page(page_num)
                    # Render at 2x zoom for better readability of small tags
                    mat = fitz.Matrix(2.0, 2.0)
                    pix = page.get_pixmap(matrix=mat)
                    img_bytes = pix.tobytes("png")
                    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                    images.append({
                        "base64": img_b64,
                        "media_type": "image/png",
                    })
                    print(f"[P&ID Vision] Rendered page {page_num + 1}/{max_pages} ({pix.width}x{pix.height})")
                doc.close()
            except Exception as e:
                print(f"[P&ID Vision] PDF to image conversion failed: {e}")
        else:
            # Direct image (PNG, JPEG, TIFF, BMP)
            media_type = content_type if content_type else "image/png"
            img_b64 = base64.b64encode(file_content).decode("utf-8")
            images.append({
                "base64": img_b64,
                "media_type": media_type,
            })

        return images

    def _guess_content_type(self, filename: str) -> str:
        """Guess content type from filename extension."""
        lower = filename.lower()
        if lower.endswith(".pdf"):
            return "application/pdf"
        elif lower.endswith(".png"):
            return "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        elif lower.endswith((".tif", ".tiff")):
            return "image/tiff"
        elif lower.endswith(".bmp"):
            return "image/bmp"
        return "application/pdf"  # Default assumption

    # ------------------------------------------------------------------
    # Merge Logic (Text + Vision Deduplication)
    # ------------------------------------------------------------------

    def _merge_equipment(
        self,
        text_list: list[Equipment],
        vision_list: list[Equipment],
    ) -> list[Equipment]:
        """
        Merge equipment from text extraction and vision extraction.
        Text results are primary; vision adds any tags not already found.
        """
        merged: list[Equipment] = list(text_list)
        seen_tags = {eq.tag.upper() for eq in text_list}

        for eq in vision_list:
            tag = eq.tag.upper()
            if tag not in seen_tags:
                seen_tags.add(tag)
                merged.append(eq)
                print(f"[P&ID Merge] Vision added equipment: {tag} ({eq.equipment_type})")

        return merged

    def _merge_instruments(
        self,
        text_list: list[Instrument],
        vision_list: list[Instrument],
    ) -> list[Instrument]:
        """
        Merge instruments from text extraction and vision extraction.
        Text results are primary; vision adds any tags not already found.
        If vision finds a better associated_equipment_tag, update it.
        """
        merged: list[Instrument] = list(text_list)
        seen_tags = {inst.tag.upper(): i for i, inst in enumerate(text_list)}

        for inst in vision_list:
            tag = inst.tag.upper()
            if tag not in seen_tags:
                # New instrument found by vision — add it
                seen_tags[tag] = len(merged)
                merged.append(inst)
                print(f"[P&ID Merge] Vision added instrument: {tag} ({inst.instrument_type})")
            else:
                # Already found by text — check if vision has better association
                idx = seen_tags[tag]
                existing = merged[idx]
                if not existing.associated_equipment_tag and inst.associated_equipment_tag:
                    existing.associated_equipment_tag = inst.associated_equipment_tag

        return merged

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def _detect_node_name(self, text: str) -> str | None:
        """Try to detect the node name from the document text."""
        text_lower = text.lower()
        patterns = [
            r'node[:\s]+(\d+)[.\s]*[-–]\s*(.+?)(?:\n|$)',
            r'nodes?[:\s]+(.+?)(?:\n|$)',
            r'(hp|lp|mp)\s+(oil|gas|water)\s+production\s+(header|separator).+?(?:no\.?\s*\d+)?',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                return match.group(0).strip().title()
        return None

    def _generate_node_id(self, filename: str) -> str:
        """Generate a node ID from filename."""
        numbers = re.findall(r'\d+', filename)
        if numbers:
            return numbers[0]
        return "1"

    def _calculate_confidence(
        self,
        equipment: list[Equipment],
        instruments: list[Instrument],
        raw_text: str,
    ) -> float:
        """
        Calculate extraction confidence score (0-1).
        Higher confidence = more tags detected and text quality is good.
        """
        score = 0.0

        if len(equipment) >= 3:
            score += 0.4
        elif len(equipment) >= 1:
            score += 0.2

        if len(instruments) >= 5:
            score += 0.4
        elif len(instruments) >= 2:
            score += 0.2
        elif len(instruments) >= 1:
            score += 0.1

        if len(raw_text) > 500:
            score += 0.2
        elif len(raw_text) > 100:
            score += 0.1

        return min(score, 1.0)


def _safe_float(value) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_line_connectivity(llm_result: dict) -> list[LineConnection]:
    """Parse line_connectivity from LLM JSON output into LineConnection objects."""
    items: list[LineConnection] = []
    for lc in llm_result.get("line_connectivity", []):
        if not isinstance(lc, dict):
            continue
        from_tag = (lc.get("from_tag") or "").strip()
        to_tag = (lc.get("to_tag") or "").strip()
        if not from_tag or not to_tag:
            continue
        items.append(LineConnection(
            from_tag=from_tag,
            to_tag=to_tag,
            line_id=lc.get("line_id"),
            fluid_phase=lc.get("fluid_phase"),
            pipe_size=lc.get("pipe_size"),
            description=lc.get("description"),
        ))
    return items


def _parse_control_loops(llm_result: dict) -> list[ControlLoop]:
    """Parse control_loops from LLM JSON output into ControlLoop objects."""
    items: list[ControlLoop] = []
    for cl in llm_result.get("control_loops", []):
        if not isinstance(cl, dict):
            continue
        final_element = (cl.get("final_element") or "").strip()
        controlled_equipment = (cl.get("controlled_equipment") or "").strip()
        controlled_variable = (cl.get("controlled_variable") or "").strip()
        if not final_element or not controlled_equipment or not controlled_variable:
            continue
        items.append(ControlLoop(
            loop_id=cl.get("loop_id"),
            controlled_variable=controlled_variable,
            measuring_element=cl.get("measuring_element"),
            controller=cl.get("controller"),
            final_element=final_element,
            controlled_equipment=controlled_equipment,
            description=cl.get("description"),
        ))
    return items


def _parse_deviation_locations(llm_result: dict) -> list[DeviationLocation]:
    """Parse deviation_locations from LLM JSON output into DeviationLocation objects."""
    items: list[DeviationLocation] = []
    for dl in llm_result.get("deviation_locations", []):
        if not isinstance(dl, dict):
            continue
        equipment_tag = (dl.get("equipment_tag") or "").strip()
        if not equipment_tag:
            continue
        items.append(DeviationLocation(
            equipment_tag=equipment_tag,
            susceptible_deviations=dl.get("susceptible_deviations", []),
            drawing_reference=dl.get("drawing_reference"),
            location_description=dl.get("location_description"),
        ))
    return items


def _normalize_drawing_number(raw: str | None) -> str | None:
    """
    Strip trailing revision suffix from an APC / drawing number.
    e.g. "APC No. 4020(c)" → "APC No. 4020"
         "DWG-4020-A"      → "DWG-4020-A"  (unchanged)
    """
    if not raw:
        return None
    cleaned = re.sub(r'\s*\([^)]+\)\s*$', '', raw.strip())
    return cleaned or None


# Module-level singleton
doc_intelligence = DocumentIntelligenceService()
