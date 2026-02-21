"""
DXF Entity Extractor — Direct CAD Text Extraction for P&ID Analysis

Parses DXF files (converted from DWG via ODA File Converter) using ezdxf to
extract all TEXT and MTEXT entities. This gives exact tag strings with spatial
coordinates and layer names — no OCR required, no misreads.

Why DXF over PDF-OCR:
  - Tags like "V-1210" are stored as exact strings — no risk of "V-l210" misread
  - x/y coordinates let us infer which instrument is spatially near which equipment
  - Layer names (e.g. "INSTRUMENT-TAG", "EQUIPMENT", "ANNOTATION") classify entities
  - No image quality concerns (no scan noise, no resolution limitations)

Typical DXF layer naming conventions in P&IDs:
  Equipment tags  : "EQUIP-TAG", "EQUIPMENT", "EQUIP_TAGS", "TAG-EQUIP", etc.
  Instrument tags : "INSTR-TAG", "INSTRUMENT", "INSTRUMENT_TAG", "TAG-INSTR", etc.
  Annotations     : "TEXT", "ANNOTATION", "NOTES", "GENERAL", "TITLE", etc.
  Title block     : "TITLEBLOCK", "TITLE_BLOCK", "BORDER", "FRAME", etc.
"""

import io
import re
from typing import NamedTuple

try:
    import ezdxf
    from ezdxf.document import Drawing as DXFDocument
    _EZDXF_AVAILABLE = True
except ImportError:
    _EZDXF_AVAILABLE = False


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

class DXFEntity(NamedTuple):
    text: str       # Cleaned text content
    x: float        # Insertion point X coordinate
    y: float        # Insertion point Y coordinate
    layer: str      # DXF layer name


# --------------------------------------------------------------------------
# Main extraction functions
# --------------------------------------------------------------------------

def extract_entities_from_dxf(dxf_bytes: bytes) -> list[DXFEntity]:
    """
    Parse a DXF file and extract all TEXT / MTEXT entities.

    Parameters
    ----------
    dxf_bytes : Raw bytes of the DXF file (UTF-8 or Latin-1 encoded text)

    Returns
    -------
    List of DXFEntity(text, x, y, layer) — deduplicated, sorted by y then x
    (top-to-bottom, left-to-right — mirrors reading order on a drawing).

    Raises
    ------
    ImportError  if ezdxf is not installed
    ValueError   if the DXF bytes cannot be parsed
    """
    if not _EZDXF_AVAILABLE:
        raise ImportError(
            "ezdxf is not installed. Run: pip install ezdxf==1.3.4"
        )

    # ezdxf.read() expects a file-like object (text mode)
    try:
        text_content = dxf_bytes.decode("utf-8", errors="replace")
        doc: DXFDocument = ezdxf.read(io.StringIO(text_content))
    except Exception as exc:
        raise ValueError(f"Failed to parse DXF file: {exc}") from exc

    msp = doc.modelspace()
    entities: list[DXFEntity] = []
    seen: set[tuple[str, int, int]] = set()

    for ent in msp:
        dxf_type = ent.dxftype()

        if dxf_type == "TEXT":
            raw = getattr(ent.dxf, "text", "") or ""
            insert = getattr(ent.dxf, "insert", None)
            if insert is None:
                continue
            x, y = insert.x, insert.y
            layer = getattr(ent.dxf, "layer", "0") or "0"

        elif dxf_type == "MTEXT":
            # ezdxf provides .text property which strips formatting codes
            raw = getattr(ent, "text", "") or ""
            insert = getattr(ent.dxf, "insert", None)
            if insert is None:
                continue
            x, y = insert.x, insert.y
            layer = getattr(ent.dxf, "layer", "0") or "0"

        else:
            continue

        cleaned = _clean_dxf_text(raw)
        if not cleaned:
            continue

        # Deduplicate by (text, rounded_x, rounded_y)
        key = (cleaned, round(x), round(y))
        if key in seen:
            continue
        seen.add(key)

        entities.append(DXFEntity(
            text=cleaned,
            x=round(x, 2),
            y=round(y, 2),
            layer=layer,
        ))

    # Sort top-to-bottom (descending y), left-to-right (ascending x)
    entities.sort(key=lambda e: (-e.y, e.x))

    print(
        f"[DXF Extractor] Extracted {len(entities)} text entities "
        f"across {len({e.layer for e in entities})} layers"
    )
    return entities


def format_entities_for_llm(entities: list[DXFEntity]) -> str:
    """
    Format DXF entities as a readable text block for the LLM.

    Groups entities by layer so the LLM can use layer names as context
    for classification (e.g. "INSTRUMENT-TAG" layer → instruments).
    Within each layer, entities appear in reading order (top-left first).

    Returns a multi-line string like:
        [Layer: INSTRUMENT-TAG]
          'PSHH-1210'  at (123.4, 456.7)
          'PT-1210'    at (150.0, 456.7)

        [Layer: EQUIPMENT]
          'V-1210'     at (200.0, 400.0)
    """
    if not entities:
        return "(No text entities found in DXF file)"

    # Group by layer
    by_layer: dict[str, list[DXFEntity]] = {}
    for ent in entities:
        by_layer.setdefault(ent.layer, []).append(ent)

    lines: list[str] = []
    for layer in sorted(by_layer.keys()):
        lines.append(f"[Layer: {layer}]")
        for ent in by_layer[layer]:
            lines.append(f"  '{ent.text}'  at ({ent.x}, {ent.y})")
        lines.append("")  # blank line between layers

    return "\n".join(lines).rstrip()


def get_layer_summary(entities: list[DXFEntity]) -> dict[str, int]:
    """Return a count of entities per layer (useful for logging/debug)."""
    summary: dict[str, int] = {}
    for ent in entities:
        summary[ent.layer] = summary.get(ent.layer, 0) + 1
    return dict(sorted(summary.items()))


# --------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------

# DXF inline formatting codes that appear in MTEXT and sometimes TEXT entities:
#   \P  = paragraph break
#   \n  = newline
#   \~  = non-breaking space
#   \H<n>; = height override
#   \W<n>; = width override
#   \Q<n>; = oblique angle
#   \S<a>^<b>; = stacked fraction
#   \C<n>;  = color change
#   \f<font>|... ; = font change
#   {\ ... } = grouped formatting
_DXF_FORMAT_CODE = re.compile(
    r"\\[PpNn~]|"           # paragraph, newline, nbsp
    r"\\[HhWwQqCcAa]\d*\.?\d*;|"  # height/width/angle/color overrides
    r"\\[Ff][^;]*;|"        # font changes
    r"\\[Ll]|"              # underline on/off
    r"\\[Oo]|"              # overline on/off
    r"\{\\[^}]*\}|"         # grouped format blocks
    r"%%[uUoOdDpPcC]",      # special character codes
)

# Unicode / OEM code-point escapes that appear in some DXF files
_UNICODE_ESCAPE = re.compile(r"\\U\+[0-9A-Fa-f]{4}")


def _clean_dxf_text(raw: str) -> str:
    """
    Strip DXF inline formatting codes from raw TEXT/MTEXT content.

    Returns a clean plain-text string, or empty string if nothing remains.
    """
    if not raw:
        return ""

    text = raw

    # Remove unicode escapes (keep the literal characters if needed,
    # but for tag extraction they're usually noise)
    text = _UNICODE_ESCAPE.sub("", text)

    # Remove DXF formatting codes
    text = _DXF_FORMAT_CODE.sub("", text)

    # Replace remaining backslash sequences with a space
    text = re.sub(r"\\.", " ", text)

    # Collapse whitespace
    text = " ".join(text.split())

    # Strip surrounding braces that sometimes wrap MTEXT content
    text = text.strip("{}")

    return text.strip()
