"""
Safeguard Classifier — Instrument Matching Only

Maps detected P&ID instruments to the equipment they protect.
PR classification (PR-1, PR-4, PC-4, etc.) and CME/KME designation
are determined by the LLM using the HSE Risk Assessment knowledge document,
NOT by hardcoded rules here.

This module is 100% deterministic for MATCHING.
Classification is delegated to openai_service.generate_safeguard_content().
"""

import re
from app.models.pid_models import Equipment, Instrument


def match_safeguards_to_equipment(
    equipment: Equipment,
    instruments: list[Instrument],
) -> list[dict]:
    """
    Match instruments to equipment and return raw instrument data for LLM classification.

    Returns a list of dicts with {tag, instrument_type, pid_reference} for each
    matched instrument. The LLM will classify PR category, CME/KME, CME Name,
    and CME ID from the HSE Risk Assessment knowledge document.

    Matching logic:
      1. Explicit association (instrument.associated_equipment_tag matches)
      2. Tag number pattern (e.g., PSHH-1210 → equipment V-1210 by suffix)
      3. All fire & gas instruments apply to all equipment in the node
    """
    matched: list[dict] = []
    seen_tags: set[str] = set()

    for instrument in instruments:
        if instrument.tag in seen_tags:
            continue

        matched_flag = False

        # Rule 1: Explicit association
        if instrument.associated_equipment_tag:
            if instrument.associated_equipment_tag == equipment.tag:
                matched_flag = True

        # Rule 2: Tag number suffix matching
        # e.g., PSHH-1210 shares "1210" with V-1210
        if not matched_flag:
            matched_flag = _match_by_tag_number(instrument.tag, equipment.tag)

        # Rule 3: Fire & gas instruments apply to all equipment in the node
        if not matched_flag:
            if instrument.instrument_type in {
                "Gas Detector", "Fire Detector", "Deluge System",
            }:
                matched_flag = True

        if matched_flag:
            matched.append({
                "tag": instrument.tag,
                "instrument_type": instrument.instrument_type,
                "pid_reference": instrument.pid_reference,
            })
            seen_tags.add(instrument.tag)

    return matched


# --------------------------------------------------------------------------
# Internal Helpers
# --------------------------------------------------------------------------

def _match_by_tag_number(instrument_tag: str, equipment_tag: str) -> bool:
    """
    Match instrument to equipment by shared numeric suffix.
    e.g., PSHH-1210 matches V-1210 (both share "1210")
    """
    inst_numbers = re.findall(r'\d+', instrument_tag)
    equip_numbers = re.findall(r'\d+', equipment_tag)

    if not inst_numbers or not equip_numbers:
        return False

    inst_primary = inst_numbers[-1]
    equip_primary = equip_numbers[-1]

    return inst_primary == equip_primary and len(inst_primary) >= 3


def _build_safeguard_description(instrument: Instrument) -> str:
    """Build a basic human-readable description for a safeguard instrument."""
    if instrument.instrument_type == "Other":
        return f"{instrument.tag}"
    return f"{instrument.instrument_type} ({instrument.tag})"
