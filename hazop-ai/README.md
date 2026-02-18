# HAZOP AI Assistant

AI-assisted HAZOP (Hazard and Operability) study system: upload P&ID drawings, validate equipment with SME review, generate deviation-based HAZOP reports with deterministic risk scoring and LLM enrichment, and manage approval workflows.

## Table of Contents

0. [Quick Start](#quick-start)
1. [Technology Stack](#1-technology-stack)
2. [Project Structure](#2-project-structure)
3. [System Architecture Overview](#3-system-architecture-overview)
4. [Phase 1 — P&ID Upload & Extraction](#4-phase-1--pid-upload--extraction)
5. [Phase 2 — SME Validation (Equipment Review)](#5-phase-2--sme-validation-equipment-review)
6. [Phase 3 — HAZOP Generation](#6-phase-3--hazop-generation)
7. [Phase 4 — SME Review & Approval](#7-phase-4--sme-review--approval)
8. [Knowledge Document Ingestion (RAG)](#8-knowledge-document-ingestion-rag)
9. [Data Models Reference](#9-data-models-reference)
10. [API Endpoints Reference](#10-api-endpoints-reference)
11. [Frontend Component Map](#11-frontend-component-map)
12. [Configuration & Environment](#12-configuration--environment)

---

## Quick Start

**Prerequisites:** Python 3.10+, Node.js 18+, MongoDB (or Azure Cosmos DB), Azure OpenAI and Document Intelligence credentials.

```bash
# Backend
cd backend
pip install -r requirements.txt
# Set env vars (see Section 12) then:
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev   # http://localhost:5173
```

The frontend proxies `/api` to the backend. Start with **Upload** → upload a P&ID → **Validate** equipment → **Generate** HAZOP → **Review** and approve deviations.

---

## 1. Technology Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 18 + TypeScript + Vite + Tailwind CSS + Axios |
| **Backend** | Python 3 + FastAPI + Uvicorn + Pydantic |
| **Database** | MongoDB (local) / Azure Cosmos DB (production) |
| **OCR** | Azure AI Document Intelligence (prebuilt-layout model) |
| **LLM** | Azure OpenAI (GPT-4 / GPT-4o deployment) |
| **Embeddings** | Azure OpenAI Embedding model (1536 dimensions) |
| **File Storage** | Local filesystem (dev) / Azure Blob Storage (prod) |
| **Excel Export** | ExcelJS (browser-side .xlsx generation) |

---

## 2. Project Structure

```
hazop-ai/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── UploadPanel.tsx           # P&ID + knowledge doc upload UI
│   │   │   ├── EquipmentReviewTable.tsx  # Equipment/Instrument review with Edit, Add, Remove, Download
│   │   │   ├── ExtractionDetails.tsx     # OCR chunks & LLM output debug view
│   │   │   ├── HazopTable.tsx            # Main HAZOP deviation table
│   │   │   ├── ApprovalControls.tsx      # SME Approve/Reject/Revise buttons
│   │   │   ├── RiskBadge.tsx             # Risk level display (A-E)
│   │   │   └── StatusBadge.tsx           # Review status display
│   │   ├── pages/
│   │   │   └── Dashboard.tsx             # Main page with 4-step workflow
│   │   ├── services/
│   │   │   └── api.ts                    # Axios API client (all endpoints)
│   │   ├── types/
│   │   │   └── hazop.ts                  # TypeScript type definitions
│   │   ├── App.tsx                       # Root component
│   │   └── main.tsx                      # Entry point
│   ├── package.json
│   └── vite.config.ts
│
└── backend/
    ├── app/
    │   ├── core/
    │   │   └── config.py                 # Environment settings (Azure keys, DB strings)
    │   ├── database/
    │   │   └── cosmos_client.py          # MongoDB/Cosmos DB client with vector search
│   ├── models/
│   │   ├── pid_models.py             # Equipment, Instrument, PIDNode models
│   │   ├── hazop_models.py           # Deviation, Safeguard, RiskScore, HAZOPReport models
│   │   ├── knowledge_models.py       # Knowledge chunk / RAG models
│   │   └── api_models.py             # Request/Response models for API
    │   ├── routes/
    │   │   ├── upload.py                 # POST /api/upload/pid, /knowledge, GET /nodes
    │   │   ├── hazop.py                  # POST /api/hazop/generate, GET /report
    │   │   └── sme_review.py             # POST /api/review/validate-equipment, PUT /approve
    │   ├── services/
    │   │   ├── document_intelligence.py  # Azure OCR + LLM extraction for P&IDs
    │   │   ├── document_intelligence_knowledge.py  # Azure OCR for knowledge docs
    │   │   ├── openai_service.py         # Azure OpenAI (embeddings + chat completions)
    │   │   ├── knowledge_service.py      # Document ingestion + RAG retrieval
    │   │   ├── ontology_engine.py        # Rule-based HAZOP domain knowledge
    │   │   ├── deviation_generator.py    # Creates HAZOP deviation rows from ontology
    │   │   ├── safeguard_classifier.py   # PR classification of instruments
    │   │   ├── risk_engine.py            # Deterministic 5x5 risk matrix calculator
    │   │   ├── hazop_generator.py        # Main orchestrator (ties everything together)
    │   │   └── blob_storage.py           # Local file storage service
    │   └── main.py                       # FastAPI app setup, CORS, routes
    └── requirements.txt
```

---

## 3. System Architecture Overview

The system follows a 4-phase pipeline. Each phase maps to a workflow step in the UI.

```
┌────────────────────────────────────────────────────────────────────┐
│                         USER WORKFLOW                              │
│                                                                    │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐   │
│   │ 1.UPLOAD │───>│2.VALIDATE│───>│3.GENERATE│───>│ 4.REVIEW │   │
│   └──────────┘    └──────────┘    └──────────┘    └──────────┘   │
│                                                                    │
│   Upload P&ID      SME reviews     HAZOP report    SME approves   │
│   files             equipment       generated       each deviation │
│                     + instruments                                  │
└────────────────────────────────────────────────────────────────────┘
```

**What is deterministic vs LLM:**

| Component | Type | Description |
|-----------|------|-------------|
| OCR Text Extraction | Azure Service | Document Intelligence parses P&ID images |
| Equipment/Instrument Extraction | LLM + Regex fallback | LLM identifies tags from OCR text |
| Deviation Generation | 100% Deterministic | Ontology engine creates rows from rules |
| Safeguard Classification | 100% Deterministic | PR-1 through PR-21 mapping by instrument type/tag |
| Cause/Consequence Enrichment | LLM | Enhances ontology defaults with AI reasoning |
| Severity Estimation | LLM (suggestion only) | Suggests 1-5 severity, SME validates |
| Risk Calculation | 100% Deterministic | 5x5 matrix lookup, no LLM involved |
| Recommendations | LLM | Generated only for risk level C, D, E |

---

## 4. Phase 1 — P&ID Upload & Extraction

This is the entry point. The user uploads P&ID drawings and the system extracts equipment and instruments.

### 4.1 Frontend Flow

**Component:** `UploadPanel.tsx`

1. User selects one or more P&ID files (PDF, PNG, JPEG, TIFF, BMP)
2. `handlePIDUpload()` is called
3. A progress modal opens showing each file's status
4. For each file, the component calls `api.uploadPID(file)` sequentially
5. `uploadPID()` in `api.ts` sends a `POST /api/upload/pid` with the file as `FormData`
6. On success, extracted nodes are passed to `Dashboard.tsx` via the `onNodesExtracted` callback
7. OCR chunks and LLM raw output are passed via `onExtractionDetails` callback
8. Dashboard moves to the **Validate** step

**Component:** `Dashboard.tsx`

```
handleNodesExtracted(nodes, file) → sets selectedNode → moves to "validate" step
handleExtractionDetails(chunks, llmOutput) → stores for ExtractionDetails component
```

### 4.2 Backend Flow

**Route:** `POST /api/upload/pid` in `routes/upload.py`

```
Request: FormData { file: UploadFile, node_id?: string }
```

**Step-by-step execution:**

```
upload.py: upload_pid()
  │
  ├─ 1. Validate file type (PDF, PNG, JPEG, TIFF, BMP)
  │
  ├─ 2. blob_storage.upload_pid_file(file_content, filename)
  │     └─ blob_storage.py: LocalStorageService.upload_pid_file()
  │        - Saves file to backend/uploads/pid/ with timestamp+UUID name
  │        - Returns { blob_name, blob_url, original_filename, size_bytes }
  │
  ├─ 3. doc_intelligence.parse_pid(file_content, filename, node_id)
  │     └─ document_intelligence.py: DocumentIntelligenceService.parse_pid()
  │        │
  │        ├─ Step 1: Azure Document Intelligence OCR
  │        │   client.begin_analyze_document("prebuilt-layout", file_content)
  │        │   → result.content → raw_text (full OCR output)
  │        │
  │        ├─ Step 2: Chunk OCR text
  │        │   _chunk_ocr_text(raw_text, chunk_size=1000, overlap=100)
  │        │   - Splits by paragraph boundaries (\n\n)
  │        │   - Falls back to character splitting if paragraphs too large
  │        │   - Caps at 20 chunks maximum
  │        │   → chunks: list[str]
  │        │
  │        ├─ Step 3: LLM Extraction (primary path)
  │        │   openai_service.extract_pid_data(chunks, filename)
  │        │   └─ openai_service.py: OpenAIService.extract_pid_data()
  │        │      - Joins chunks with "---CHUNK BOUNDARY---" separator
  │        │      - Sends system prompt (expert P&ID engineer) + user prompt (OCR text)
  │        │      - LLM returns structured JSON: { equipment[], instruments[], node_name, ... }
  │        │      - temperature=0.1, response_format=json_object
  │        │      → llm_result: dict
  │        │
  │        ├─ Step 3b: Regex Fallback (if LLM fails)
  │        │   _detect_equipment(raw_text) → scans for tag patterns like V-\d{3,5}, D-\d{3,5}
  │        │   _detect_instruments(raw_text) → scans for PSHH-\d{3,5}, LT-\d{3,5}, etc.
  │        │   + GENERIC_TAG_PATTERN catch-all: [A-Z]{2,5}-\d{3,5}
  │        │
  │        ├─ Step 4: Parse & validate LLM output (no type restriction)
  │        │   _parse_llm_equipment(llm_result) → Equipment objects (any type string accepted)
  │        │   _parse_llm_instruments(llm_result) → Instrument objects (any type string accepted)
  │        │
  │        ├─ Step 5: Enrich from tables
  │        │   _enrich_from_tables(result, equipment_list)
  │        │   - Scans table cells for "design pressure", "design temp" values
  │        │   - Fills in missing Equipment.design_pressure / design_temperature
  │        │
  │        ├─ Step 6: Build PIDNode
  │        │   PIDNode(node_id, node_name, system, equipment, instruments, pid_drawings)
  │        │
  │        └─ Step 7: Calculate confidence score
  │           _calculate_confidence(equipment, instruments, raw_text) → 0.0 to 1.0
  │           - 3+ equipment = +0.4, 5+ instruments = +0.4, 500+ chars text = +0.2
  │           → PIDExtractionResult
  │
  ├─ 4. Save each node to database
  │     cosmos_client.save_node(node_data)
  │     └─ cosmos_client.py: MongoDBClient.save_node()
  │        - Upserts by node_id into "nodes" collection
  │
  └─ 5. Return UploadResponse
        { message, file_name, blob_url, nodes[], confidence_score, ocr_chunks[], llm_raw_output }
```

### 4.3 What the LLM Prompt Looks Like

The LLM receives OCR text and is asked to extract equipment and instruments. It is NOT restricted to a fixed list — it can return any type:

```
System: "You are an expert oil & gas process engineer reading P&ID OCR text...
         You are NOT limited to a fixed list of types. Extract every device you find
         and provide its full descriptive type name based on ISA/industry standards."

User:   "Extract all equipment and instruments from this P&ID OCR text.
         Source file: drawing-13.pdf
         OCR Text: [chunks joined by ---CHUNK BOUNDARY---]"

Response: JSON with equipment[] and instruments[]
```

### 4.4 How ExtractionDetails Displays

**Component:** `ExtractionDetails.tsx`

- Shows collapsible "OCR Chunks" section — each chunk with index and character count
- Shows collapsible "LLM Output" section — raw JSON from the LLM prettified
- Helps the SME understand what the AI saw and how it interpreted the P&ID

---

## 5. Phase 2 — SME Validation (Equipment Review)

The extracted equipment and instruments are shown in a table for the SME to review, edit, add, remove, and then validate.

### 5.1 Frontend Flow

**Component:** `EquipmentReviewTable.tsx`

**Display:**
- Equipment table: Tag | Type | Name | Design Pressure (PSIG) | Actions
- Instruments table: Tag | Type | Associated Equipment | Actions
- Upstream Pressure input field
- SME name input + Validate button

**Features:**

| Feature | How It Works |
|---------|-------------|
| **Remove** | `removeEquipment(index)` / `removeInstrument(index)` — filters item from state array |
| **Add** | Shows inline form row. `addEquipment()` / `addInstrument()` — pushes new item to state array. Tags auto-uppercased. |
| **Edit** | `startEditEquipment(index)` — copies item to `editEq` state, row becomes editable inputs. `saveEditEquipment()` — replaces item in array. `cancelEditEquipment()` — discards changes. |
| **Download** | `handleDownload()` — generates .xlsx using ExcelJS with bold headers, borders, section titles. Exports only equipment + instruments (no raw OCR/LLM data). |
| **Type Selection** | Uses `<input type="text" list="...">` + `<datalist>` combo-box. User can pick from common types OR type any custom value. |

**All changes are LOCAL STATE only until the user clicks "Validate Equipment".**

### 5.2 Validation Backend Call

When SME clicks "Validate Equipment":

```
Frontend: validateEquipment(nodeId, equipment[], instruments[], smeName, comments, upstreamPressure)
  │
  └─ api.ts: POST /api/review/validate-equipment
     │
     └─ sme_review.py: validate_equipment(request)
        │
        ├─ Fetch existing node: cosmos_client.get_node(node_id)
        │
        ├─ FULL REPLACEMENT of equipment and instruments:
        │   node_data["equipment"] = request.confirmed_equipment
        │   node_data["instruments"] = request.confirmed_instruments
        │
        ├─ Set validation metadata:
        │   node_data["validated_by"] = sme_name
        │   node_data["validated_at"] = timestamp
        │   node_data["upstream_pressure_psig"] = value
        │
        ├─ Save: cosmos_client.save_node(node_data)
        │
        └─ Return: { message, node_id, equipment_count, instrument_count, validated: true }
```

After validation, Dashboard moves to the **Generate** step.

---

## 6. Phase 3 — HAZOP Generation

This is the core of the system. It takes the validated node and generates a full HAZOP report.

### 6.1 Frontend Trigger

**Component:** `Dashboard.tsx`

```
handleGenerate(mode: "full" | "quick")
  │
  ├─ mode="full"  → api.generateHAZOP(nodeId, includeRecommendations=true)
  │                  POST /api/hazop/generate
  │
  └─ mode="quick" → api.generateHAZOPQuick(nodeId)
                     POST /api/hazop/generate/quick
```

### 6.2 Full HAZOP Pipeline (8 Steps)

**Route:** `POST /api/hazop/generate` in `routes/hazop.py`

```
hazop.py: generate_hazop(request)
  │
  ├─ Fetch validated node: cosmos_client.get_node(node_id)
  │  → PIDNode with confirmed equipment + instruments
  │
  └─ hazop_generator.generate_full_hazop(node, include_recommendations)
     └─ hazop_generator.py: HAZOPGeneratorService.generate_full_hazop()
```

**Step 1: Deviation Generation (100% Deterministic)**

```
deviation_generator.generate_deviations_for_node(node)
  └─ deviation_generator.py: DeviationGeneratorService
     │
     ├─ For EACH equipment in node:
     │   │
     │   ├─ ontology_engine.get_deviations_for_equipment(equipment.equipment_type)
     │   │   └─ ontology_engine.py: Looks up EQUIPMENT_ONTOLOGY dictionary
     │   │      - Key = equipment_type string (e.g. "Separator")
     │   │      - Returns: list of deviation definitions with:
     │   │        - guideword (High, Low, No, Reverse, Other)
     │   │        - parameter (Pressure, Level, Flow, Temperature, Composition)
     │   │        - typical_causes[]
     │   │        - typical_consequences[]
     │   │        - expected_safeguards[]
     │   │      - If equipment type not in ontology → creates generic "General Review Required" deviation
     │   │
     │   ├─ safeguard_classifier.match_safeguards_to_equipment(equipment, node.instruments)
     │   │   └─ safeguard_classifier.py
     │   │      │
     │   │      ├─ For each instrument in node:
     │   │      │   ├─ Rule 1: Explicit match (instrument.associated_equipment_tag == equipment.tag)
     │   │      │   ├─ Rule 2: Tag number suffix match (PSHH-1210 → V-1210 share "1210")
     │   │      │   └─ Rule 3: Fire & gas instruments match ALL equipment
     │   │      │
     │   │      └─ For each matched instrument:
     │   │          classify_instrument(instrument)
     │   │          │
     │   │          ├─ Step 1: INSTRUMENT_TYPE_TO_PR.get(instrument.instrument_type)
     │   │          │   e.g., "Pressure Switch High High" → PR-1
     │   │          │         "Level Gauge" → PR-2
     │   │          │         "Pressure Safety Valve" → PR-4
     │   │          │
     │   │          ├─ Step 2: If not found, _classify_by_tag_prefix(tag)
     │   │          │   TAG_PREFIX_TO_PR: "PSHH" → PR-1, "PT" → PR-2, "PSV" → PR-4, etc.
     │   │          │
     │   │          ├─ Step 3: Default → PR-Other
     │   │          │
     │   │          └─ Determine CME or KME:
     │   │              PR-1, PR-4, PR-5 → CME (Critical Mitigation Element)
     │   │              PR-2, PR-3, PR-20, PR-21 → KME (Key Mitigation Element)
     │   │          → Safeguard object
     │   │
     │   └─ _create_deviation_from_ontology()
     │      - Creates Deviation object with: guideword, parameter, causes, consequences, safeguards
     │      - Status = "draft"
     │      - deviation_id = UUID
     │      → Deviation
     │
     └─ Returns: list[Deviation] (all deviations for all equipment)
```

**Steps 2-6: Enrich Each Deviation**

For EACH deviation, `_enrich_deviation()` runs this pipeline:

```
hazop_generator.py: _enrich_deviation(deviation, node)
  │
  ├─ Step 2: RAG Knowledge Retrieval
  │   _get_knowledge_context(equipment_type, deviation_name)
  │   └─ knowledge_service.retrieve_relevant_context(query, limit=3)
  │      └─ knowledge_service.py:
  │         ├─ openai_service.generate_embedding(query)
  │         │   → 1536-dimension vector
  │         ├─ cosmos_client.vector_search(query_embedding, limit=3)
  │         │   └─ cosmos_client.py:
  │         │      ├─ Cosmos DB: Native HNSW cosmosSearch pipeline
  │         │      └─ Local MongoDB: Brute-force cosine similarity
  │         └─ Concatenate top-K chunks into context string
  │            "[Source: filename | Relevance: 0.85]\n content..."
  │   → knowledge_context: str
  │
  ├─ Step 3: LLM Cause/Consequence Enrichment
  │   _enrich_causes_consequences(deviation, equipment_type, ...)
  │   └─ openai_service.generate_causes_and_consequences(
  │        equipment_type, equipment_tag, deviation, design_pressure,
  │        existing_safeguards, knowledge_context
  │      )
  │      └─ openai_service.py:
  │         - System prompt: "You are a senior process safety engineer..."
  │         - User prompt: Equipment details + deviation + knowledge context
  │         - temperature=0.2, response_format=json_object
  │         → { causes[], consequences[], worst_credible_scenario, personnel_exposure }
  │
  │   Then: _merge_lists(ontology_causes, llm_causes) → deduplicated merged list
  │         _merge_lists(ontology_consequences, llm_consequences)
  │         (keeps ontology first, adds unique LLM items. Dedup via >70% word overlap.)
  │
  ├─ Step 4: LLM Severity Estimation
  │   _estimate_severity(equipment_type, deviation, ...)
  │   └─ openai_service.estimate_consequence_severity(...)
  │      └─ openai_service.py:
  │         - Prompt asks for 1-5 severity for PAF, PD/LOR, ECR
  │         - temperature=0.1
  │         → { paf_severity: {value, reasoning}, pd_lor_severity: {...}, ecr_severity: {...}, base_probability: {...} }
  │
  ├─ Step 5: Deterministic Risk Calculation
  │   _calculate_risk(deviation, severity_data)
  │   └─ risk_engine.calculate_risk(paf_c, pd_lor_c, ecr_c, base_prob, safeguards)
  │      └─ risk_engine.py: RiskEngineService.calculate_risk()
  │         │
  │         ├─ calculate_adjusted_probability(base_probability, safeguards)
  │         │   - Collects unique PR types from safeguards
  │         │   - PR-1 & PR-4 give -2 probability reduction each
  │         │   - PR-2, PR-3, PR-5, PR-21 give -1 each
  │         │   - PR-20 gives 0 (procedural, no probability credit)
  │         │   - Total reduction capped at 4
  │         │   - adjusted = max(base - total_reduction, 1)
  │         │   → adjusted_probability (1-5)
  │         │
  │         ├─ For each category (PAF, PD/LOR, ECR):
  │         │   _lookup_risk(consequence, adjusted_probability, matrix)
  │         │   → RiskScore { consequence, probability, risk_level }
  │         │
  │         │   Risk Matrix (same for all 3 categories):
  │         │   ┌─────────┬────┬────┬────┬────┬────┐
  │         │   │ C\P     │  1 │  2 │  3 │  4 │  5 │
  │         │   ├─────────┼────┼────┼────┼────┼────┤
  │         │   │ 1       │  A │  A │  A │  B │  B │
  │         │   │ 2       │  A │  A │  B │  B │  C │
  │         │   │ 3       │  A │  B │  B │  C │  C │
  │         │   │ 4       │  B │  B │  C │  C │  D │
  │         │   │ 5       │  B │  C │  C │  D │  E │
  │         │   └─────────┴────┴────┴────┴────┴────┘
  │         │   C = Consequence (1-5), P = Probability (1-5)
  │         │   A = Low, B = Medium-Low, C = Medium, D = High, E = Extreme
  │         │
  │         └─ → RiskAssessment { paf: RiskScore, pd_lor: RiskScore, ecr: RiskScore }
  │
  └─ Step 6: LLM Recommendations (only if risk >= C)
     risk_engine.requires_recommendation(deviation.risk)
     └─ If any category has risk level C, D, or E:
        _generate_recommendations(deviation, knowledge_context)
        └─ openai_service.generate_recommendations(
              deviation, consequences, risk_level, existing_safeguards, knowledge_context
           )
           - temperature=0.2, max 5 recommendations
           → list[str] of actionable recommendations
```

**Step 7: Assemble Report**

```
HAZOPReport(
  report_id=UUID,
  node_id, node_name, system,
  deviations=enriched_deviations,
  status="draft",
  created_at, updated_at,
  version=1
)
```

**Step 8: Store in Database**

```
cosmos_client.save_hazop_report(report_data)
  → Upserts into "hazop_reports" collection by report_id
```

### 6.3 Quick HAZOP (No LLM)

```
hazop_generator.generate_hazop_ontology_only(node)
  │
  ├─ Same Step 1: Deviation generation from ontology
  ├─ Skip Steps 2-4: No RAG, no LLM enrichment, no LLM severity
  ├─ Step 5: Risk calculation with default severity (PAF=3, PD/LOR=3, ECR=2, Prob=3)
  ├─ Step 6: No LLM recommendations (manual review message instead)
  └─ Steps 7-8: Same report assembly and storage
```

### 6.4 Frontend Display

**Component:** `HazopTable.tsx`

- Displays all deviations in a filterable, expandable table
- Columns: Equipment | Deviation | Causes | Consequences | Safeguards | Risk | Status | Review
- Risk badges color-coded: A=Green, B=Yellow, C=Orange, D=Red, E=Dark Red
- Expandable rows show full details including safeguard PR classifications and recommendations

---

## 7. Phase 4 — SME Review & Approval

Every HAZOP deviation starts as "draft" and requires SME review.

### 7.1 Review Actions

**Component:** `ApprovalControls.tsx` (inside `HazopTable.tsx`)

| Action | API Call | Backend Route |
|--------|---------|---------------|
| **Approve** | `approveDeviation(devId, name, comments)` | `PUT /api/review/approve/{deviation_id}` |
| **Reject** | `rejectDeviation(devId, name, comments)` | `PUT /api/review/reject/{deviation_id}` |
| **Request Revision** | `requestRevision(devId, name, comments)` | `PUT /api/review/revision/{deviation_id}` |
| **Edit Deviation** | `editDeviation(devId, causes, consequences, ...)` | `PUT /api/review/edit-deviation` |
| **Bulk Approve** | `bulkApprove(reportId, devIds, name)` | `PUT /api/review/bulk-approve` |

### 7.2 Backend Review Flow

```
sme_review.py: approve_deviation(deviation_id, request)
  │
  ├─ _find_report_for_deviation(deviation_id)
  │   → MongoDB query: { "deviations.deviation_id": deviation_id }
  │
  ├─ Update deviation in report:
  │   dev["status"] = "approved"
  │   dev["reviewed_by"] = reviewer_name
  │   dev["review_comments"] = comments
  │   dev["reviewed_at"] = timestamp
  │
  └─ cosmos_client.save_hazop_report(report)
```

### 7.3 Deviation Regeneration

If SME requests revision:

```
POST /api/hazop/regenerate-deviation
  │
  └─ hazop_generator.regenerate_deviation(report_id, deviation_id, node)
     ├─ Fetch report from DB
     ├─ Find the specific deviation
     ├─ Re-run _enrich_deviation() (full LLM pipeline)
     ├─ Reset status to "draft"
     └─ Save updated report
```

### 7.4 Progress Tracking

```
GET /api/review/stats/{report_id}
  → { total, approved, progress_percent, breakdown: {draft: N, approved: N, ...}, is_complete }
```

Displayed in Dashboard left sidebar as a progress bar.

---

## 8. Knowledge Document Ingestion (RAG)

Knowledge documents (Consequence Guidance, Risk Matrices, SOPs, etc.) are ingested to provide context for LLM reasoning.

### 8.1 Ingestion Flow

```
Frontend: UploadPanel.tsx → handleKnowledgeUpload()
  │
  └─ api.uploadKnowledgeDocument(file, documentType)
     POST /api/upload/knowledge

Backend: upload.py → upload_knowledge()
  │
  └─ knowledge_service.ingest_document(file_content, filename, document_type)
     └─ knowledge_service.py: KnowledgeService.ingest_document()
        │
        ├─ Step 1: Extract text via Document Intelligence OCR
        │   knowledge_doc_intelligence.extract_text(file_content)
        │
        ├─ Step 2: Chunk text (800 chars, 100 overlap)
        │   _chunk_text(text, filename)
        │   - Splits by paragraphs first
        │   - Falls back to sentence splitting for long paragraphs
        │   - Maintains overlap for context continuity
        │   - Max 500 chunks per document
        │
        ├─ Step 3: Generate embeddings (batched, 16 per batch)
        │   openai_service.generate_embeddings_batch(chunk_texts)
        │   → list of 1536-dimension vectors
        │
        └─ Step 4: Store in Cosmos DB
           cosmos_client.store_knowledge_chunks_batch(documents)
           Each document: { chunk_id, source_document, document_type, content, embedding, metadata }
```

### 8.2 RAG Retrieval (During HAZOP Generation)

```
knowledge_service.retrieve_relevant_context(query, limit=3)
  │
  ├─ Generate query embedding: openai_service.generate_embedding(query)
  │
  ├─ Vector search: cosmos_client.vector_search(query_embedding, limit)
  │   ├─ Cosmos DB: Uses native $search with cosmosSearch HNSW index
  │   └─ Local MongoDB: Brute-force cosine similarity across all chunks
  │
  └─ Concatenate top-K results into context string
     → "[Source: doc.pdf | Relevance: 0.87]\n Relevant content here..."
```

---

## 9. Data Models Reference

### 9.1 P&ID Models (`pid_models.py`)

```python
Equipment:
  tag: str                    # "V-1210"
  name: str                   # "HP Oil Production Separator No. 2"
  equipment_type: str         # Free-text: "Separator", "Vessel", or anything
  design_pressure: float?     # PSIG
  design_temperature: float?  # Fahrenheit
  operating_pressure: float?
  operating_temperature: float?

Instrument:
  tag: str                          # "PSHH-1210"
  instrument_type: str              # Free-text: "Level Gauge", "Pressure Transmitter", etc.
  setpoint: float?
  associated_equipment_tag: str?    # "V-1210"
  pid_reference: str?

PIDNode:
  node_id: str
  node_name: str
  system: str
  equipment: list[Equipment]
  instruments: list[Instrument]
  pid_drawings: list[str]
  description: str?
  upstream_pressure_psig: float?
  validated_by: str?           # Added after SME validation
  validated_at: str?
```

### 9.2 HAZOP Models (`hazop_models.py`)

```python
Safeguard:
  instrument_tag: str              # "PSHH-1210"
  description: str                 # "Pressure Switch High High (PSHH-1210)"
  pr_classification: PRClassification  # PR-1, PR-2, PR-3, PR-4, PR-5, PR-20, PR-21
  mitigation_type: MitigationType?     # CME or KME
  pid_reference: str?

RiskScore:
  consequence: int       # 1-5
  probability: int       # 1-5
  risk_level: str        # A, B, C, D, E

RiskAssessment:
  paf: RiskScore?        # People at Facility
  pd_lor: RiskScore?     # Property Damage / Loss of Revenue
  ecr: RiskScore?        # Environmental

Deviation:
  deviation_id: str
  node_id: str
  equipment_tag: str
  guideword: Guideword              # High, Low, No, More, Less, Reverse, Other
  parameter: DeviationParameter     # Pressure, Level, Flow, Temperature, Composition
  deviation: str                    # "High Pressure"
  causes: list[str]
  consequences: list[str]
  consequence_category: str?
  safeguards: list[Safeguard]
  risk: RiskAssessment?
  recommendations: list[str]
  status: ReviewStatus              # draft, pending_review, approved, rejected, revision_requested
  reviewed_by: str?
  review_comments: str?
  reviewed_at: str?
  created_at: datetime
  updated_at: datetime
  generated_by: str                 # "ai_draft"

HAZOPReport:
  report_id: str
  node_id: str
  node_name: str
  system: str
  deviations: list[Deviation]
  status: ReviewStatus
  created_at: datetime
  updated_at: datetime
  version: int
```

### 9.3 PR Classification Reference

| PR Code | Category | Description | Mitigation Type |
|---------|----------|-------------|-----------------|
| PR-1 | Automatic Shutdown | SIS / ESD action | CME |
| PR-2 | Process Alarm | Alarm + operator action | KME |
| PR-3 | Mechanical Integrity | Inspection / integrity management | KME |
| PR-4 | Relief Device | PSV / relief systems | CME |
| PR-5 | Fire & Gas Detection | Detectors + deluge | CME |
| PR-20 | Monitoring/Procedural | Operating procedures | KME |
| PR-21 | Corrosion Control | Corrosion management | KME |

---

## 10. API Endpoints Reference

### Upload Routes (`/api/upload`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/pid` | Upload P&ID file, extract equipment & instruments |
| POST | `/knowledge` | Upload knowledge document for RAG |
| GET | `/pid/list` | List all uploaded P&ID files |
| GET | `/nodes` | List all extracted nodes |
| GET | `/nodes/{node_id}` | Get specific node details |

### HAZOP Routes (`/api/hazop`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Generate full HAZOP (LLM + ontology) |
| POST | `/generate/quick` | Generate quick HAZOP (ontology only) |
| GET | `/report/{report_id}` | Get report by ID |
| GET | `/node/{node_id}` | Get latest HAZOP for a node |
| GET | `/report/{report_id}/summary` | Report statistics |
| POST | `/explain-risk` | Audit trail for risk calculation |
| POST | `/regenerate-deviation` | Re-generate a single deviation |
| GET | `/risk-matrix` | Risk matrix definitions |

### SME Review Routes (`/api/review`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/validate-equipment` | SME confirms equipment list |
| PUT | `/approve/{deviation_id}` | Approve a deviation |
| PUT | `/reject/{deviation_id}` | Reject a deviation |
| PUT | `/revision/{deviation_id}` | Request revision |
| PUT | `/edit-deviation` | Edit causes/consequences/recommendations |
| PUT | `/bulk-approve` | Approve multiple deviations |
| GET | `/pending/{report_id}` | Get pending deviations |
| GET | `/stats/{report_id}` | Review progress stats |

---

## 11. Frontend Component Map

```
App.tsx
└── Dashboard.tsx (main page, workflow state management)
    │
    ├── WorkflowSteps (visual step indicator: Upload → Validate → Generate → Review)
    │
    ├── LEFT SIDEBAR
    │   ├── UploadPanel.tsx
    │   │   └── File inputs for P&ID + Knowledge docs
    │   │   └── Progress modal (multi-file upload tracking)
    │   │
    │   ├── Node Info Card (node ID, name, equipment/instrument counts, validation status)
    │   │
    │   ├── Generate Controls ("Full HAZOP" + "Quick Draft" buttons)
    │   │
    │   └── Report Stats (progress bar, approved/total counts)
    │
    └── MAIN CONTENT (changes per workflow step)
        │
        ├── step="upload"   → Upload instructions
        │
        ├── step="validate" → ExtractionDetails.tsx (OCR + LLM debug)
        │                    + EquipmentReviewTable.tsx (review/edit/add/remove/download)
        │
        ├── step="generate" → Generation in progress indicator
        │
        └── step="review"   → HazopTable.tsx (deviation table)
                               ├── Filters (equipment, status)
                               ├── DeviationRow (expandable rows)
                               │   ├── Risk badges (PAF, PD/LOR, ECR)
                               │   └── Status badge
                               └── ExpandedDeviationDetail
                                   ├── Causes list
                                   ├── Consequences list
                                   ├── Safeguards with PR tags
                                   ├── Risk scores
                                   ├── Recommendations
                                   └── ApprovalControls.tsx
                                       └── Approve / Reject / Revise buttons + name/comments
```

---

## 12. Configuration & Environment

**Backend Configuration** (`backend/app/core/config.py`):

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI service URL |
| `AZURE_OPENAI_API_KEY` | API key for OpenAI |
| `AZURE_OPENAI_API_VERSION` | API version (2024-10-21) |
| `AZURE_OPENAI_DEPLOYMENT` | Chat model deployment name |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment name |
| `AZURE_DOC_INTELLIGENCE_ENDPOINT` | Document Intelligence service URL |
| `AZURE_DOC_INTELLIGENCE_KEY` | Document Intelligence API key |
| `AZURE_BLOB_CONNECTION_STRING` | Blob storage connection (or local path) |
| `AZURE_BLOB_CONTAINER_NAME` | Container name (default: pid-uploads) |
| `MONGODB_CONNECTION_STRING` | MongoDB / Cosmos DB connection string |
| `MONGODB_DATABASE_NAME` | Database name (default: Oxy) |

**Running the application:** See [Quick Start](#quick-start) above. The frontend Vite dev server proxies `/api` requests to the backend at `http://localhost:8000`.
