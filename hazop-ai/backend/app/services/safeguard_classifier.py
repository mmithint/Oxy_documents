"""
Safeguard Classifier — Rule-Based PR Classification & Instrument Matching

Maps detected instruments to Protection Requirement (PR) categories.
Matches safeguards to equipment based on tag patterns and associations.

This module is 100% deterministic. No LLM involved.

PR Classification (from OXY HAZOP methodology):
  PR-1  → Automatic shutdown system (SIS / ESD action)
  PR-2  → Process alarm + operator action
  PR-3  → Mechanical integrity / inspection
  PR-4  → Relief device (PSV)
  PR-5  → Fire & gas detection
  PR-20 → Monitoring / procedural safeguard
  PR-21 → Corrosion control / integrity management
"""

import re
from app.models.pid_models import Equipment, Instrument
from app.models.hazop_models import Safeguard, PRClassification, MitigationType


# --------------------------------------------------------------------------
# PR Classification Rules
# --------------------------------------------------------------------------
# Maps instrument type (plain string) to its Protection Requirement category.
# This is the core safety classification logic — must be deterministic.

INSTRUMENT_TYPE_TO_PR: dict[str, PRClassification] = {
    # PR-1: Automatic shutdown (SIS/ESD)
    "Pressure Switch High High": PRClassification.PR_1,
    "Pressure Switch High Low": PRClassification.PR_1,
    "Level Switch High High": PRClassification.PR_1,
    "Level Switch Low Low": PRClassification.PR_1,
    "Temperature Switch High High": PRClassification.PR_1,
    "Temperature Switch Low Low": PRClassification.PR_1,
    "Emergency Shutdown Valve": PRClassification.PR_1,

    # PR-2: Process alarm + operator response
    "Pressure Transmitter": PRClassification.PR_2,
    "Pressure Indicator": PRClassification.PR_2,
    "Pressure Differential Indicator": PRClassification.PR_2,
    "Level Transmitter": PRClassification.PR_2,
    "Level Gauge": PRClassification.PR_2,
    "Level Indicator": PRClassification.PR_2,
    "Temperature Transmitter": PRClassification.PR_2,
    "Temperature Indicator": PRClassification.PR_2,
    "Flow Transmitter": PRClassification.PR_2,

    # PR-4: Relief devices
    "Pressure Safety Valve": PRClassification.PR_4,
    "Flow Safety Valve": PRClassification.PR_4,
    "Blowdown Valve": PRClassification.PR_4,

    # PR-5: Fire & gas detection
    "Gas Detector": PRClassification.PR_5,
    "Fire Detector": PRClassification.PR_5,
    "Deluge System": PRClassification.PR_5,

    # Control valves — PR-2 (operator/control system response)
    "Pressure Control Valve": PRClassification.PR_2,
    "Level Control Valve": PRClassification.PR_2,
    "Flow Control Valve": PRClassification.PR_2,
}

# Tag prefix patterns for classification when InstrumentType is OTHER
TAG_PREFIX_TO_PR: dict[str, PRClassification] = {
    "PSHH": PRClassification.PR_1,
    "PSHL": PRClassification.PR_1,
    "PSH": PRClassification.PR_1,
    "PSL": PRClassification.PR_1,
    "LSHH": PRClassification.PR_1,
    "LSLL": PRClassification.PR_1,
    "LSH": PRClassification.PR_1,
    "LSL": PRClassification.PR_1,
    "TSHH": PRClassification.PR_1,
    "TSLL": PRClassification.PR_1,
    "TSH": PRClassification.PR_1,
    "TSL": PRClassification.PR_1,
    "ESD": PRClassification.PR_1,
    "SDV": PRClassification.PR_1,
    "XV": PRClassification.PR_1,
    "PSV": PRClassification.PR_4,
    "PRV": PRClassification.PR_4,
    "BDV": PRClassification.PR_4,
    "PT": PRClassification.PR_2,
    "LT": PRClassification.PR_2,
    "TT": PRClassification.PR_2,
    "FT": PRClassification.PR_2,
    "PCV": PRClassification.PR_2,
    "LCV": PRClassification.PR_2,
    "FCV": PRClassification.PR_2,
    "TCV": PRClassification.PR_2,
    "GD": PRClassification.PR_5,
    "FD": PRClassification.PR_5,
}

# Keyword-based classification for non-standard descriptions
KEYWORD_TO_PR: dict[str, PRClassification] = {
    "corrosion inhibit": PRClassification.PR_21,
    "corrosion coupon": PRClassification.PR_21,
    "corrosion monitor": PRClassification.PR_21,
    "inspection": PRClassification.PR_3,
    "pigging": PRClassification.PR_3,
    "thickness monitoring": PRClassification.PR_3,
    "gas detection": PRClassification.PR_5,
    "fire detection": PRClassification.PR_5,
    "deluge": PRClassification.PR_5,
    "firewater": PRClassification.PR_5,
    "check valve": PRClassification.PR_3,
    "operating procedure": PRClassification.PR_20,
    "operator response": PRClassification.PR_20,
    "manual intervention": PRClassification.PR_20,
    "heat tracing": PRClassification.PR_20,
    "chemical injection": PRClassification.PR_20,
}

# --------------------------------------------------------------------------
# CME / KME Classification Rules
# --------------------------------------------------------------------------
# CME (Critical Mitigation Element): Failure = direct escalation to major event
# KME (Key Mitigation Element): Failure = increased risk but not direct escalation

CME_PR_CATEGORIES = {PRClassification.PR_1, PRClassification.PR_4, PRClassification.PR_5}
KME_PR_CATEGORIES = {PRClassification.PR_2, PRClassification.PR_3, PRClassification.PR_20, PRClassification.PR_21}


# --------------------------------------------------------------------------
# Classification Functions
# --------------------------------------------------------------------------

def classify_instrument(instrument: Instrument) -> Safeguard:
    """
    Classify a single instrument into its PR category and create a Safeguard.

    Classification priority:
      1. Known instrument_type string → direct PR mapping
      2. Tag prefix pattern matching
      3. Default to OTHER
    """
    # Step 1: Try direct type mapping (plain string lookup)
    pr_class = INSTRUMENT_TYPE_TO_PR.get(instrument.instrument_type)

    # Step 2: If type not found, try tag prefix
    if pr_class is None:
        pr_class = _classify_by_tag_prefix(instrument.tag)

    # Step 3: Default
    if pr_class is None:
        pr_class = PRClassification.OTHER

    # Determine CME or KME
    mitigation_type = _classify_mitigation_type(pr_class)

    return Safeguard(
        instrument_tag=instrument.tag,
        description=_build_safeguard_description(instrument),
        pr_classification=pr_class,
        mitigation_type=mitigation_type,
        pid_reference=instrument.pid_reference,
    )


def classify_by_keyword(description: str) -> PRClassification:
    """
    Classify a safeguard by keyword matching in its description.
    Used for non-instrument safeguards (procedures, systems, etc.)
    """
    desc_lower = description.lower()
    for keyword, pr_class in KEYWORD_TO_PR.items():
        if keyword in desc_lower:
            return pr_class
    return PRClassification.OTHER


def match_safeguards_to_equipment(
    equipment: Equipment,
    instruments: list[Instrument],
) -> list[Safeguard]:
    """
    Match instruments to equipment and classify each as a Safeguard.

    Matching logic:
      1. Explicit association (instrument.associated_equipment_tag matches)
      2. Tag number pattern (e.g., PSHH-1210 → equipment V-1210 by suffix)
      3. All fire & gas instruments apply to all equipment in the node
    """
    safeguards: list[Safeguard] = []
    seen_tags: set[str] = set()  # Avoid duplicates

    for instrument in instruments:
        if instrument.tag in seen_tags:
            continue

        matched = False

        # Rule 1: Explicit association
        if instrument.associated_equipment_tag:
            if instrument.associated_equipment_tag == equipment.tag:
                matched = True

        # Rule 2: Tag number suffix matching
        # e.g., PSHH-1210 shares "1210" with V-1210
        if not matched:
            matched = _match_by_tag_number(instrument.tag, equipment.tag)

        # Rule 3: Fire & gas instruments apply to all equipment
        if not matched:
            if instrument.instrument_type in {
                "Gas Detector", "Fire Detector", "Deluge System",
            }:
                matched = True

        if matched:
            safeguard = classify_instrument(instrument)
            safeguards.append(safeguard)
            seen_tags.add(instrument.tag)

    return safeguards


# --------------------------------------------------------------------------
# Internal Helpers
# --------------------------------------------------------------------------

def _classify_by_tag_prefix(tag: str) -> PRClassification | None:
    """Match tag against known prefix patterns."""
    tag_upper = tag.upper().strip()
    # Sort by length descending so longer prefixes match first
    # (e.g., "PSHH" before "PSH")
    sorted_prefixes = sorted(TAG_PREFIX_TO_PR.keys(), key=len, reverse=True)
    for prefix in sorted_prefixes:
        if tag_upper.startswith(prefix):
            return TAG_PREFIX_TO_PR[prefix]
    return None


def _classify_mitigation_type(pr_class: PRClassification) -> MitigationType | None:
    """Determine if safeguard is CME or KME based on PR classification."""
    if pr_class in CME_PR_CATEGORIES:
        return MitigationType.CME
    elif pr_class in KME_PR_CATEGORIES:
        return MitigationType.KME
    return None


def _match_by_tag_number(instrument_tag: str, equipment_tag: str) -> bool:
    """
    Match instrument to equipment by shared numeric suffix.
    e.g., PSHH-1210 matches V-1210 (both share "1210")
    """
    inst_numbers = re.findall(r'\d+', instrument_tag)
    equip_numbers = re.findall(r'\d+', equipment_tag)

    if not inst_numbers or not equip_numbers:
        return False

    # Check if the primary number (last significant number) matches
    # Instrument: PSHH-1210 → "1210"
    # Equipment: V-1210 → "1210"
    inst_primary = inst_numbers[-1]
    equip_primary = equip_numbers[-1]

    return inst_primary == equip_primary and len(inst_primary) >= 3


def _build_safeguard_description(instrument: Instrument) -> str:
    """Build a human-readable description for a safeguard."""
    if instrument.instrument_type == "Other":
        return f"{instrument.tag}"
    return f"{instrument.instrument_type} ({instrument.tag})"


# --------------------------------------------------------------------------
# Utility: Get all PR classifications summary for a node
# --------------------------------------------------------------------------

def get_safeguard_summary(safeguards: list[Safeguard]) -> dict:
    """Summarize safeguards by PR classification."""
    summary = {
        "total": len(safeguards),
        "by_pr_class": {},
        "cme_count": 0,
        "kme_count": 0,
    }

    for sg in safeguards:
        pr = sg.pr_classification.value
        summary["by_pr_class"][pr] = summary["by_pr_class"].get(pr, 0) + 1

        if sg.mitigation_type == MitigationType.CME:
            summary["cme_count"] += 1
        elif sg.mitigation_type == MitigationType.KME:
            summary["kme_count"] += 1

    return summary
