"""
DWG Conversion Service — DWG → DXF via ODA File Converter

The ODA File Converter (https://www.opendesign.com/guestfiles/oda_file_converter)
is a free command-line tool from the Open Design Alliance.  It converts DWG/DXF
files to many formats including DXF, PDF, DWF, DWFX, and SVG.

We convert DWG → DXF (not PDF) so that the downstream dxf_extractor can read
CAD text entities directly — giving exact tag strings without OCR uncertainty.

Installation
------------
  1. Download from https://www.opendesign.com/guestfiles/oda_file_converter
  2. Install / extract the executable
  3. Set ODA_CONVERTER_PATH in your .env file to the full executable path:
       Windows: ODA_CONVERTER_PATH=C:\\Program Files\\ODA\\ODAFileConverter\\ODAFileConverter.exe
       Linux:   ODA_CONVERTER_PATH=/usr/bin/ODAFileConverter

ODA File Converter CLI syntax
------------------------------
  ODAFileConverter <InputFolder> <OutputFolder> <OutputType> <OutputVersion>
                   <RecurseSubfolders> <AuditAlways> [<InputFilter>]

  OutputType    : DXF | PDF | DWF | DWFX | SVG | ...
  OutputVersion : ACAD2018 (DXF version to write)
  RecurseSubfolders : 0 = no, 1 = yes
  AuditAlways       : 0 = no, 1 = yes
  InputFilter       : glob, e.g. "*.dwg"

  Example:
    ODAFileConverter "C:\\input" "C:\\output" DXF ACAD2018 0 1 "*.dwg"
"""

import os
import asyncio
from pathlib import Path
from shutil import which
import tempfile
import re

from app.core.config import get_settings

settings = get_settings()


class DWGConversionError(Exception):
    """Raised when DWG conversion fails or ODA converter is unavailable."""


class DWGConverterService:
    """Converts DWG files to DXF using the ODA File Converter CLI."""

    # MIME types that browsers may send for DWG files
    DWG_MIME_TYPES: frozenset[str] = frozenset({
        "application/dwg",
        "application/acad",
        "application/x-acad",
        "image/vnd.dwg",
        "image/x-dwg",
        "application/vnd.dwg",
    })

    # ------------------------------------------------------------------ #
    # Availability
    # ------------------------------------------------------------------ #

    def _get_converter_path(self) -> str | None:
        """
        Resolve the ODA File Converter executable path.

        Tries (in order):
          1. ODA_CONVERTER_PATH from .env / settings
          2. Common Windows install locations
          3. System PATH
        """
        configured = getattr(settings, "ODA_CONVERTER_PATH", "").strip()
        if configured and os.path.isfile(configured):
            return configured

        # Well-known Windows install paths
        windows_candidates = [
            r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\ODA\ODAFileConverter.exe",
            r"C:\ODAFileConverter\ODAFileConverter.exe",
        ]
        for path in windows_candidates:
            if os.path.isfile(path):
                return path

        # System PATH
        found = which("ODAFileConverter") or which("ODAFileConverter.exe")
        return found  # None if not found

    def is_available(self) -> bool:
        """Return True if ODA File Converter is found on this machine."""
        return self._get_converter_path() is not None

    def is_dwg_file(self, filename: str, content_type: str | None) -> bool:
        """
        Return True if the uploaded file is a DWG.

        Checks both the content-type header (unreliable for DWG) and the
        filename extension.
        """
        if filename.lower().endswith(".dwg"):
            return True
        if content_type and content_type.lower() in self.DWG_MIME_TYPES:
            return True
        return False

    # ------------------------------------------------------------------ #
    # Conversion: DWG → DXF
    # ------------------------------------------------------------------ #

    async def convert_dwg_to_dxf(
        self,
        dwg_content: bytes,
        filename: str,
    ) -> tuple[bytes, str]:
        """
        Convert DWG file bytes to DXF bytes using ODA File Converter.

        DXF is chosen over PDF because:
        - DXF preserves text entities as exact strings (no OCR required)
        - Spatial coordinates (x, y) are retained for proximity analysis
        - Layer names classify tags (equipment vs instrument vs annotation)

        Parameters
        ----------
        dwg_content : raw bytes of the uploaded DWG file
        filename    : original filename (used to derive the output DXF name)

        Returns
        -------
        (dxf_bytes, dxf_filename)

        Raises
        ------
        DWGConversionError if ODA is not found, conversion fails, or times out.
        """
        converter_path = self._get_converter_path()
        if not converter_path:
            raise DWGConversionError(
                "ODA File Converter not found. "
                "Download it free from https://www.opendesign.com/guestfiles/oda_file_converter "
                "and set ODA_CONVERTER_PATH in your .env file."
            )

        stem = Path(filename).stem
        safe_name = _safe_filename(stem) + ".dwg"
        dxf_filename = _safe_filename(stem) + ".dxf"

        # Use a temporary directory so we don't leave files on disk
        with tempfile.TemporaryDirectory(prefix="hazop_dwg_") as tmp_dir:
            input_dir = os.path.join(tmp_dir, "input")
            output_dir = os.path.join(tmp_dir, "output")
            os.makedirs(input_dir)
            os.makedirs(output_dir)

            # Write the DWG bytes to the input directory
            input_path = os.path.join(input_dir, safe_name)
            with open(input_path, "wb") as fh:
                fh.write(dwg_content)

            print(
                f"[DWG Converter] Converting {filename} → DXF "
                f"using {converter_path}"
            )

            # Build the ODA File Converter command (DXF output)
            cmd = [
                converter_path,
                input_dir,    # InputFolder
                output_dir,   # OutputFolder
                "DXF",        # OutputType — text-based CAD format
                "ACAD2018",   # OutputVersion — DXF 2018 format
                "0",          # RecurseSubfolders: no
                "1",          # AuditAlways: yes
                "*.dwg",      # InputFilter
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=180  # 3-minute timeout
                )
            except asyncio.TimeoutError:
                raise DWGConversionError(
                    "ODA File Converter timed out after 180 seconds."
                )
            except Exception as exc:
                raise DWGConversionError(
                    f"Failed to launch ODA File Converter: {exc}"
                )

            stdout_text = stdout.decode(errors="replace").strip()
            stderr_text = stderr.decode(errors="replace").strip()

            if stdout_text:
                print(f"[DWG Converter] stdout: {stdout_text[:300]}")
            if stderr_text:
                print(f"[DWG Converter] stderr: {stderr_text[:300]}")

            # ODA returns 0 on success; non-zero indicates an error
            if proc.returncode != 0:
                raise DWGConversionError(
                    f"ODA File Converter exited with code {proc.returncode}. "
                    f"stderr: {stderr_text[:500]}"
                )

            # Locate output DXF — ODA names it <stem>.dxf
            output_dxfs = list(Path(output_dir).glob("*.dxf"))
            if not output_dxfs:
                raise DWGConversionError(
                    "ODA File Converter ran but produced no DXF output. "
                    f"stdout: {stdout_text[:300]} | stderr: {stderr_text[:300]}"
                )

            with open(output_dxfs[0], "rb") as fh:
                dxf_bytes = fh.read()

            print(
                f"[DWG Converter] Conversion complete: "
                f"{filename} → {dxf_filename} ({len(dxf_bytes):,} bytes)"
            )
            return dxf_bytes, dxf_filename


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Strip characters that are unsafe in temp filenames."""
    return re.sub(r"[^\w\-.]", "_", name)


# Module-level singleton
dwg_converter = DWGConverterService()
