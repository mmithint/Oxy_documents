import axios from "axios";
import type {
  UploadResponse,
  HAZOPGenerateResponse,
  PIDNode,
  HAZOPReport,
  ReviewStats,
  Equipment,
  Instrument,
  RiskAssessment,
  DeviationCauses,
  GenerateCausesResponse,
  DeviationConsequences,
  ConsequenceGenerationResponse,
  DeviationSafeguards,
  SafeguardsGenerationResponse,
  ExtractionCompareResponse,
} from "../types/hazop";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

/** Check that the backend is reachable (via Vite proxy). */
export async function checkBackendConnection(): Promise<{ ok: boolean; message?: string }> {
  try {
    const res = await api.get<{ status: string; version: string }>("/health");
    return res.data?.status === "healthy" ? { ok: true } : { ok: false, message: "Unexpected response" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Backend unreachable";
    return { ok: false, message };
  }
}

// ============================================================
// Upload Endpoints
// ============================================================

export async function uploadPID(file: File, nodeId?: string): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (nodeId) formData.append("node_id", nodeId);

  const res = await api.post<UploadResponse>("/upload/pid", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

export async function comparePIDExtraction(file: File): Promise<ExtractionCompareResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await api.post<ExtractionCompareResponse>("/upload/pid/compare", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

export async function uploadKnowledgeDocument(
  file: File,
  documentType: string
): Promise<Record<string, unknown>> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("document_type", documentType);

  const res = await api.post("/upload/knowledge", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

export async function listNodes(): Promise<{ nodes: PIDNode[]; count: number }> {
  const res = await api.get("/upload/nodes");
  return res.data;
}

export async function getNode(nodeId: string): Promise<PIDNode> {
  const res = await api.get(`/upload/nodes/${nodeId}`);
  return res.data;
}

// ============================================================
// HAZOP Endpoints
// ============================================================

export async function generateHAZOP(
  nodeId: string,
  includeRecommendations = true,
  selectedDeviationTypes?: string[],
): Promise<HAZOPGenerateResponse> {
  const res = await api.post<HAZOPGenerateResponse>("/hazop/generate", {
    node_id: nodeId,
    include_recommendations: includeRecommendations,
    selected_deviation_types: selectedDeviationTypes ?? null,
  });
  return res.data;
}

export async function generateHAZOPQuick(
  nodeId: string,
  selectedDeviationTypes?: string[],
): Promise<HAZOPGenerateResponse> {
  const res = await api.post<HAZOPGenerateResponse>("/hazop/generate/quick", {
    node_id: nodeId,
    selected_deviation_types: selectedDeviationTypes ?? null,
  });
  return res.data;
}

export async function getDeviationTypes(): Promise<{ deviation_types: string[] }> {
  const res = await api.get<{ deviation_types: string[] }>("/hazop/deviation-types");
  return res.data;
}

export async function getHAZOPReport(reportId: string): Promise<HAZOPReport> {
  const res = await api.get(`/hazop/report/${reportId}`);
  return res.data;
}

export async function getHAZOPByNode(nodeId: string): Promise<HAZOPReport> {
  const res = await api.get(`/hazop/node/${nodeId}`);
  return res.data;
}

export async function getReportSummary(
  reportId: string
): Promise<Record<string, unknown>> {
  const res = await api.get(`/hazop/report/${reportId}/summary`);
  return res.data;
}

export async function getRiskMatrix(): Promise<Record<string, unknown>> {
  const res = await api.get("/hazop/risk-matrix");
  return res.data;
}

export async function regenerateDeviation(
  reportId: string,
  deviationId: string,
  nodeId: string
): Promise<Record<string, unknown>> {
  const res = await api.post("/hazop/regenerate-deviation", {
    report_id: reportId,
    deviation_id: deviationId,
    node_id: nodeId,
  });
  return res.data;
}

// ============================================================
// SME Review Endpoints
// ============================================================

export async function validateEquipment(
  nodeId: string,
  equipment: Equipment[],
  instruments: Instrument[],
  smeName: string,
  comments?: string,
  upstreamPressurePsig?: number | null,
): Promise<Record<string, unknown>> {
  const res = await api.post("/review/validate-equipment", {
    node_id: nodeId,
    confirmed_equipment: equipment,
    confirmed_instruments: instruments,
    sme_name: smeName,
    comments,
    upstream_pressure_psig: upstreamPressurePsig ?? null,
  });
  return res.data;
}

export async function approveDeviation(
  deviationId: string,
  reviewerName: string,
  comments?: string
): Promise<Record<string, unknown>> {
  const res = await api.put(`/review/approve/${deviationId}`, {
    deviation_id: deviationId,
    action: "approved",
    reviewer_name: reviewerName,
    comments,
  });
  return res.data;
}

export async function rejectDeviation(
  deviationId: string,
  reviewerName: string,
  comments?: string
): Promise<Record<string, unknown>> {
  const res = await api.put(`/review/reject/${deviationId}`, {
    deviation_id: deviationId,
    action: "rejected",
    reviewer_name: reviewerName,
    comments,
  });
  return res.data;
}

export async function requestRevision(
  deviationId: string,
  reviewerName: string,
  comments?: string
): Promise<Record<string, unknown>> {
  const res = await api.put(`/review/revision/${deviationId}`, {
    deviation_id: deviationId,
    action: "revision_requested",
    reviewer_name: reviewerName,
    comments,
  });
  return res.data;
}

export async function editDeviation(
  deviationId: string,
  editorName: string,
  updates: {
    causes?: string[];
    consequences?: string[];
    recommendations?: string[];
    risk_override?: RiskAssessment;
  }
): Promise<Record<string, unknown>> {
  const res = await api.put("/review/edit-deviation", {
    deviation_id: deviationId,
    editor_name: editorName,
    ...updates,
  });
  return res.data;
}

export async function bulkApprove(
  reportId: string,
  deviationIds: string[],
  reviewerName: string,
  comments?: string
): Promise<Record<string, unknown>> {
  const res = await api.put("/review/bulk-approve", {
    report_id: reportId,
    deviation_ids: deviationIds,
    reviewer_name: reviewerName,
    comments,
  });
  return res.data;
}

export async function getPendingDeviations(
  reportId: string
): Promise<{ pending_count: number; deviations: Record<string, unknown>[] }> {
  const res = await api.get(`/review/pending/${reportId}`);
  return res.data;
}

export async function getReviewStats(reportId: string): Promise<ReviewStats> {
  const res = await api.get<ReviewStats>(`/review/stats/${reportId}`);
  return res.data;
}

// ============================================================
// Causes Review Endpoints
// ============================================================

export async function generateCauses(
  nodeId: string,
  selectedDeviationTypes?: string[],
  causeIncludedTags?: string[],
  causeExcludedTags?: string[],
): Promise<GenerateCausesResponse> {
  const res = await api.post<GenerateCausesResponse>("/hazop/generate-causes", {
    node_id: nodeId,
    selected_deviation_types: selectedDeviationTypes ?? null,
    cause_included_tags: causeIncludedTags ?? null,
    cause_excluded_tags: causeExcludedTags ?? null,
  });
  return res.data;
}

export async function approveCauses(
  nodeId: string,
  smeName: string,
  deviationCauses: DeviationCauses[],
  comments?: string,
): Promise<Record<string, unknown>> {
  const res = await api.post("/review/approve-causes", {
    node_id: nodeId,
    sme_name: smeName,
    deviation_causes: deviationCauses,
    comments,
  });
  return res.data;
}

export async function generateConsequences(
  nodeId: string,
  selectedDeviationTypes?: string[],
): Promise<ConsequenceGenerationResponse> {
  const res = await api.post<ConsequenceGenerationResponse>("/hazop/generate-consequences", {
    node_id: nodeId,
    selected_deviation_types: selectedDeviationTypes ?? null,
  });
  return res.data;
}

export async function approveConsequences(
  nodeId: string,
  smeName: string,
  deviationConsequences: DeviationConsequences[],
  comments?: string,
): Promise<Record<string, unknown>> {
  const res = await api.post("/review/approve-consequences", {
    node_id: nodeId,
    sme_name: smeName,
    deviation_consequences: deviationConsequences,
    comments,
  });
  return res.data;
}

// ============================================================
// Safeguards Review Endpoints
// ============================================================

export async function generateSafeguards(
  nodeId: string,
  selectedDeviationTypes?: string[],
  safeguardIncludedTags?: string[],
  safeguardExcludedTags?: string[],
): Promise<SafeguardsGenerationResponse> {
  const res = await api.post<SafeguardsGenerationResponse>("/hazop/generate-safeguards", {
    node_id: nodeId,
    selected_deviation_types: selectedDeviationTypes ?? null,
    safeguard_included_tags: safeguardIncludedTags ?? null,
    safeguard_excluded_tags: safeguardExcludedTags ?? null,
  });
  return res.data;
}

export async function approveSafeguards(
  nodeId: string,
  smeName: string,
  deviationSafeguards: DeviationSafeguards[],
  comments?: string,
): Promise<Record<string, unknown>> {
  const res = await api.post("/review/approve-safeguards", {
    node_id: nodeId,
    sme_name: smeName,
    deviation_safeguards: deviationSafeguards,
    comments,
  });
  return res.data;
}
