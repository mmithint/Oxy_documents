"""
Local File Storage Service

Saves uploaded files to local disk instead of Azure Blob Storage.
Same interface — swap to Azure Blob later by changing this one file.

Storage structure:
  backend/uploads/
    ├── pid/          ← P&ID files
    └── knowledge/    ← Knowledge documents
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

settings = get_settings()

# Local storage root
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


class LocalStorageService:
    """Saves files to local filesystem. Drop-in replacement for Azure Blob."""

    def __init__(self):
        self.base_dir = UPLOAD_DIR

    async def ensure_container_exists(self) -> None:
        """Create upload directories if they don't exist."""
        (self.base_dir / "pid").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "knowledge").mkdir(parents=True, exist_ok=True)
        print(f"Local storage ready: {self.base_dir}")

    async def upload_pid_file(
        self,
        file_content: bytes,
        original_filename: str,
        content_type: str = "application/pdf",
    ) -> dict:
        """Save a P&ID file to local disk."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        extension = original_filename.rsplit(".", 1)[-1] if "." in original_filename else "pdf"
        filename = f"{timestamp}_{unique_id}.{extension}"
        file_path = self.base_dir / "pid" / filename

        file_path.write_bytes(file_content)

        return {
            "blob_name": f"pid/{filename}",
            "blob_url": str(file_path),
            "original_filename": original_filename,
            "size_bytes": len(file_content),
        }

    async def upload_knowledge_document(
        self,
        file_content: bytes,
        original_filename: str,
        content_type: str = "application/pdf",
    ) -> dict:
        """Save a knowledge document to local disk."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        extension = original_filename.rsplit(".", 1)[-1] if "." in original_filename else "pdf"
        filename = f"{timestamp}_{unique_id}.{extension}"
        file_path = self.base_dir / "knowledge" / filename

        file_path.write_bytes(file_content)

        return {
            "blob_name": f"knowledge/{filename}",
            "blob_url": str(file_path),
            "original_filename": original_filename,
            "size_bytes": len(file_content),
        }

    def _resolve_safe_path(self, blob_name: str) -> Optional[Path]:
        """Resolve blob_name to a path under base_dir; return None if path escapes (security)."""
        # Restrict to pid/ and knowledge/ subdirs
        normalized = blob_name.replace("\\", "/").strip("/")
        if ".." in normalized or normalized.startswith("/"):
            return None
        if not (normalized.startswith("pid/") or normalized.startswith("knowledge/")):
            return None
        file_path = (self.base_dir / normalized).resolve()
        try:
            file_path.relative_to(self.base_dir.resolve())
        except ValueError:
            return None
        return file_path

    async def get_blob_content(self, blob_name: str) -> bytes:
        """Read file content from local disk."""
        file_path = self._resolve_safe_path(blob_name)
        if file_path is None or not file_path.is_file():
            raise FileNotFoundError(f"Blob not found or invalid: {blob_name}")
        return file_path.read_bytes()

    async def delete_blob(self, blob_name: str) -> bool:
        """Delete a file from local disk."""
        file_path = self._resolve_safe_path(blob_name)
        if file_path is None:
            return False
        try:
            file_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    async def list_pid_files(self) -> list[dict]:
        """List all uploaded P&ID files."""
        pid_dir = self.base_dir / "pid"
        if not pid_dir.exists():
            return []

        files = []
        for f in pid_dir.iterdir():
            if f.is_file():
                stat = f.stat()
                files.append({
                    "blob_name": f"pid/{f.name}",
                    "size_bytes": stat.st_size,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        return files


# Module-level singleton
blob_storage = LocalStorageService()
