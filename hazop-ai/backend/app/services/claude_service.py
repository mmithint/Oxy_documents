"""
Claude Service — Anthropic Claude API wrapper for P&ID extraction.

Mirrors the extract_pid_data / extract_pid_data_with_vision interface of
openai_service.py so both can be called interchangeably in the comparison route.

Key differences from the GPT path:
  - Uses anthropic.Anthropic (sync client) wrapped in run_in_executor for async.
  - No response_format=json_object support — uses explicit "Return ONLY valid JSON"
    in the prompt and strips markdown code fences from the response.
  - Image format uses {"source": {"type": "base64", ...}} instead of image_url.
"""

import asyncio
import json
import anthropic

from app.core.config import get_settings
from app.services.extraction_prompts import PID_OCR_SYSTEM_PROMPT, PID_VISION_SYSTEM_PROMPT

settings = get_settings()


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that Claude sometimes wraps JSON in."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence line (e.g. ```json or ```)
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else ""
        # Remove closing fence
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


class ClaudeService:
    """Anthropic Claude client for P&ID data extraction."""

    @property
    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def extract_pid_data(self, chunks: list[str], source_filename: str) -> dict:
        """
        OCR text path — identical prompts and JSON schema as openai_service.extract_pid_data().

        Args:
            chunks: List of OCR text chunks from the P&ID.
            source_filename: Original filename for context.

        Returns:
            Dict with equipment[], instruments[], node_name, system, description.
        """
        combined_text = "\n\n---CHUNK BOUNDARY---\n\n".join(chunks)
        user_prompt = (
            f"Extract all equipment and instruments from this P&ID OCR text.\n\n"
            f"Source file: {source_filename}\n\n"
            f"OCR Text:\n{combined_text}\n\n"
            "Return ONLY valid JSON matching the schema above. No explanatory text."
        )

        def _call() -> dict:
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0,
                system=PID_OCR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = _strip_code_fences(response.content[0].text)
            print(f"[Claude] extract_pid_data complete for {source_filename}")
            return json.loads(text)

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _call)
        except Exception as e:
            print(f"[Claude] extract_pid_data error: {e}")
            return {
                "node_name": "",
                "system": "Hydrocarbon Processing Systems",
                "description": "",
                "equipment": [],
                "instruments": [],
            }

    async def extract_pid_data_with_vision(
        self,
        images_base64: list[dict],
        source_filename: str,
        ocr_hint: str | None = None,
    ) -> dict:
        """
        Vision path — sends PDF pages as PNG images to Claude.

        Args:
            images_base64: List of {"base64": str, "media_type": str} dicts.
            source_filename: Original filename for context.
            ocr_hint: Optional OCR text to cross-reference.

        Returns:
            Dict with equipment[], instruments[] (same format as extract_pid_data).
        """
        content: list[dict] = []

        # Optional OCR hint text first
        if ocr_hint:
            content.append({
                "type": "text",
                "text": (
                    f"Source file: {source_filename}\n\n"
                    f"OCR text extracted from this diagram (for cross-reference — "
                    f"some tags may be missing from OCR):\n{ocr_hint[:3000]}"
                ),
            })
        else:
            content.append({
                "type": "text",
                "text": (
                    f"Source file: {source_filename}\n\n"
                    "Analyze this P&ID diagram and extract all equipment and instrument tags."
                ),
            })

        # Add up to 5 pages (same limit as GPT path)
        for img in images_base64[:5]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],   # "image/png"
                    "data": img["base64"],
                },
            })

        content.append({
            "type": "text",
            "text": (
                f"Extract all P&ID data from these images of: {source_filename}\n"
                "Return ONLY valid JSON matching the schema above. No explanatory text."
            ),
        })

        def _call() -> dict:
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0,
                system=PID_VISION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            text = _strip_code_fences(response.content[0].text)
            print(f"[Claude] extract_pid_data_with_vision complete for {source_filename}")
            return json.loads(text)

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _call)
        except Exception as e:
            print(f"[Claude] extract_pid_data_with_vision error: {e}")
            return {"equipment": [], "instruments": []}


claude_service = ClaudeService()
