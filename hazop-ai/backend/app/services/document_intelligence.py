"""
Azure AI Document Intelligence Service — P&ID Parser (PDF only)

Extracts structured data from P&ID drawings (PDF):
  - Equipment tags and names
  - Instrument tags
  - Text content and layout
  - Table data

Dual Extraction Pipeline:
  P&ID file (PDF)
      ├── Path 1: Document Intelligence (OCR) → text chunks → Claude text extraction
      ├── Path 2: Convert PDF pages to images → Claude Vision → LLM *sees* the diagram
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
from app.services.anthropic_pid_service import anthropic_pid_service

settings = get_settings()


# --------------------------------------------------------------------------
# Tag Pattern Definitions (kept for regex fallback)
# --------------------------------------------------------------------------

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

_EQUIPMENT_PREFIXES = {"V", "D", "E", "P", "C", "K", "T", "HDR", "FL", "S", "KO"}


class DocumentIntelligenceService:
    """Parses P&ID PDF drawings using Azure AI Document Intelligence + Claude."""

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
        Parse a P&ID PDF and extract equipment and instruments.

        Dual Extraction Pipeline:
          1. Azure Document Intelligence OCR → text
          2. Chunk OCR text → Claude text extraction (Path 1)
          3. Convert PDF pages to images → Claude Vision extraction (Path 2)
          4. Merge results from both paths (deduplicate by tag)
          5. Enrich from tables
          6. Fallback to regex if both Claude paths fail
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

        # ---- PATH 1: Claude text extraction ----
        llm_result = None
        use_llm = True

        try:
            llm_result = await anthropic_pid_service.extract_pid_data(chunks, source_filename)
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
            drawing_number = llm_result.get("drawing_number")
            pid_summary = llm_result.get("pid_summary") or None
            flow_description = llm_result.get("flow_description") or None
            line_connectivity = _parse_line_connectivity(llm_result)
            control_loops = _parse_control_loops(llm_result)
            deviation_locations = _parse_deviation_locations(llm_result)
        else:
            # Regex fallback
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

        # ---- PATH 2: Claude Vision extraction ----
        vision_equipment: list[Equipment] = []
        vision_instruments: list[Instrument] = []
        vision_result: dict | None = None

        try:
            images_base64 = self._pdf_to_images(file_content)

            if images_base64:
                print(f"[P&ID Vision] Sending {len(images_base64)} page(s) to Claude Vision...")
                vision_result = await anthropic_pid_service.extract_pid_data_with_vision(
                    images_base64=images_base64,
                    source_filename=source_filename,
                    ocr_hint=raw_text[:3000] if raw_text else None,
                )
                vision_equipment = self._parse_llm_equipment(vision_result)
                vision_instruments = self._parse_llm_instruments(vision_result)
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
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_ocr_text(
        self, text: str, chunk_size: int = 1000, overlap: int = 100
    ) -> list[str]:
        """Split OCR text into chunks by paragraph boundaries. Max 20 chunks."""
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
                if len(para) > chunk_size:
                    start = 0
                    while start < len(para):
                        end = start + chunk_size
                        chunks.append(para[start:end])
                        start = end - overlap
                    current_chunk = ""
                else:
                    if current_chunk and overlap > 0:
                        overlap_text = current_chunk[-overlap:]
                        current_chunk = f"{overlap_text}\n\n{para}"
                    else:
                        current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks[:20]

    def _log_chunks(self, chunks: list[str], filename: str) -> None:
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
        equipment_list: list[Equipment] = []
        seen_tags: set[str] = set()

        for item in llm_result.get("equipment", []):
            tag = item.get("tag", "").strip().upper()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)

            raw_type = (item.get("equipment_type") or "Other").strip()
            if raw_type.lower() in ("pipeline", "pipe line", "pipe", "piping", "pipework"):
                raw_type = "Piping"

            upstream = item.get("upstream_equipment") or []
            downstream = item.get("downstream_equipment") or []
            if isinstance(upstream, str):
                upstream = [t.strip() for t in upstream.split(",") if t.strip()]
            if isinstance(downstream, str):
                downstream = [t.strip() for t in downstream.split(",") if t.strip()]

            equipment_list.append(Equipment(
                tag=tag,
                name=item.get("name") or tag,
                equipment_type=raw_type,
                design_pressure=_safe_float(item.get("design_pressure")),
                design_temperature=_safe_float(item.get("design_temperature")),
                operating_pressure=_safe_float(item.get("operating_pressure")),
                operating_temperature=_safe_float(item.get("operating_temperature")),
                upstream_equipment=[t.strip().upper() for t in upstream if t],
                downstream_equipment=[t.strip().upper() for t in downstream if t],
            ))

        return equipment_list

    def _parse_llm_instruments(self, llm_result: dict) -> list[Instrument]:
        instrument_list: list[Instrument] = []
        seen_tags: set[str] = set()

        for item in llm_result.get("instruments", []):
            tag = item.get("tag", "").strip().upper()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)

            raw_role = (item.get("instrument_role") or "").strip().lower()
            instrument_role = raw_role if raw_role in ("cause", "safeguard") else None

            raw_position = (item.get("position") or "").strip().lower()
            position = raw_position if raw_position in ("upstream", "downstream") else None

            raw_phase = (item.get("line_phase") or "").strip().lower()
            line_phase = raw_phase if raw_phase in ("gas", "liquid") else None

            instrument_list.append(Instrument(
                tag=tag,
                instrument_type=item.get("instrument_type") or "Other",
                setpoint=_safe_float(item.get("setpoint")),
                associated_equipment_tag=item.get("associated_equipment_tag"),
                instrument_role=instrument_role,
                position=position,
                line_phase=line_phase,
            ))

        return instrument_list

    # ------------------------------------------------------------------
    # OCR Helpers
    # ------------------------------------------------------------------

    def _extract_full_text(self, result) -> str:
        if not result or not result.content:
            return ""
        return result.content

    # ------------------------------------------------------------------
    # Regex Fallback Methods
    # ------------------------------------------------------------------

    def _detect_equipment(self, text: str) -> list[Equipment]:
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
        instrument_list: list[Instrument] = []
        seen_tags: set[str] = set()

        for pattern, inst_type in INSTRUMENT_TAG_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                tag = match.upper()
                if tag not in seen_tags:
                    seen_tags.add(tag)
                    instrument_list.append(Instrument(
                        tag=tag,
                        instrument_type=inst_type,
                        associated_equipment_tag=self._find_associated_equipment(tag),
                    ))

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
        text_lower = text.lower()
        for keyword, eq_type in EQUIPMENT_KEYWORD_MAP.items():
            if keyword in text_lower:
                tag_pos = text_lower.find(tag.lower())
                kw_pos = text_lower.find(keyword)
                if tag_pos >= 0 and kw_pos >= 0 and abs(tag_pos - kw_pos) < 200:
                    return f"{keyword.title()} ({tag})"
        return tag

    def _find_associated_equipment(self, instrument_tag: str) -> str | None:
        numbers = re.findall(r'\d+', instrument_tag)
        if numbers:
            return numbers[-1]
        return None

    # ------------------------------------------------------------------
    # Table Enrichment
    # ------------------------------------------------------------------

    def _enrich_from_tables(self, result, equipment_list: list[Equipment]) -> None:
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
    # PDF to Images (for Claude Vision)
    # ------------------------------------------------------------------

    def _pdf_to_images(self, file_content: bytes) -> list[dict]:
        """
        Render each PDF page as a PNG image for Claude Vision.
        Renders at 2x zoom for better readability of small tags.
        Returns list of {"base64": str, "media_type": "image/png"}
        """
        images: list[dict] = []
        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
            max_pages = min(len(doc), 5)  # Cap at 5 pages
            for page_num in range(max_pages):
                page = doc.load_page(page_num)
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for small tag readability
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                images.append({"base64": img_b64, "media_type": "image/png"})
                print(f"[P&ID Vision] Rendered page {page_num + 1}/{max_pages} ({pix.width}x{pix.height})")
            doc.close()
        except Exception as e:
            print(f"[P&ID Vision] PDF to image conversion failed: {e}")
        return images

    # ------------------------------------------------------------------
    # Merge Logic (Text + Vision Deduplication)
    # ------------------------------------------------------------------

    def _merge_equipment(
        self,
        text_list: list[Equipment],
        vision_list: list[Equipment],
    ) -> list[Equipment]:
        """Text results are primary; vision adds any tags not already found."""
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
        """Text results are primary; vision adds missing tags and improves associations."""
        merged: list[Instrument] = list(text_list)
        seen_tags = {inst.tag.upper(): i for i, inst in enumerate(text_list)}

        for inst in vision_list:
            tag = inst.tag.upper()
            if tag not in seen_tags:
                seen_tags[tag] = len(merged)
                merged.append(inst)
                print(f"[P&ID Merge] Vision added instrument: {tag} ({inst.instrument_type})")
            else:
                # Already found by text — update association if vision found a better one
                idx = seen_tags[tag]
                existing = merged[idx]
                if not existing.associated_equipment_tag and inst.associated_equipment_tag:
                    existing.associated_equipment_tag = inst.associated_equipment_tag

        return merged

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def _detect_node_name(self, text: str) -> str | None:
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
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_line_connectivity(llm_result: dict) -> list[LineConnection]:
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


# Module-level singleton
doc_intelligence = DocumentIntelligenceService()
