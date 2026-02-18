"""
Document Intelligence Service — Knowledge Document OCR

Dedicated OCR extraction for knowledge documents (PDFs).
Separate from the P&ID parser to avoid LLM extraction overhead,
text truncation, and unnecessary chunking.

Flow:
  Knowledge PDF → Document Intelligence (OCR + Layout) → Full raw text
"""

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential

from app.core.config import get_settings

settings = get_settings()


class KnowledgeDocIntelligenceService:
    """Extracts text from knowledge documents using Azure AI Document Intelligence."""

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

    async def extract_text(self, file_content: bytes) -> str:
        """
        Extract full text from a knowledge document using OCR.

        No LLM calls, no P&ID-specific logic, no truncation.
        Returns the complete raw text for chunking and embedding.
        """
        poller = self.client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=AnalyzeDocumentRequest(bytes_source=file_content),
        )
        result = poller.result()

        if not result or not result.content:
            print("[Knowledge OCR] No content extracted")
            return ""

        text = result.content
        print(f"[Knowledge OCR] Extracted {len(text)} chars")
        return text


# Module-level singleton
knowledge_doc_intelligence = KnowledgeDocIntelligenceService()
