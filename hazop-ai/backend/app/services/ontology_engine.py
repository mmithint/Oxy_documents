"""
Equipment Ontology Engine — Rule-Based Domain Knowledge

This is the deterministic brain of the HAZOP system.
NO LLM is used here. All mappings are based on engineering standards
and process safety domain knowledge.

For each equipment type, this engine defines:
- Applicable process parameters (Pressure, Level, Flow, etc.)
- Possible deviations (guideword + parameter combinations)
- Typical causes for each deviation
- Typical consequences for each deviation
- Typical safeguards expected

This ontology drives the Deviation Generator and provides context
for the LLM cause/consequence reasoning step.
"""

from app.models.hazop_models import DeviationParameter, Guideword


# --------------------------------------------------------------------------
# Standard Deviation Types — Single source of truth for ALL equipment
# --------------------------------------------------------------------------

STANDARD_DEVIATIONS: dict[str, dict] = {
    "High Pressure": {
        "guideword": Guideword.HIGH,
        "parameter": DeviationParameter.PRESSURE,
        "deviation_name": "High Pressure",
        "typical_causes": [
            "Blocked outlet or downstream restriction",
            "Control valve failure (fails closed)",
            "External fire / heat input",
            "Upstream overpressure propagation",
            "Operator error — manual valve left closed",
        ],
        "typical_consequences": [
            "Equipment exceeds design pressure",
            "Overpressure leading to vessel/pipe rupture",
            "Hydrocarbon release and potential fire/explosion",
            "Personnel injury or fatality",
        ],
        "expected_safeguards": ["PSHH", "PSV", "PCV", "ESD_VALVE"],
    },
    "Low Pressure": {
        "guideword": Guideword.LOW,
        "parameter": DeviationParameter.PRESSURE,
        "deviation_name": "Low Pressure",
        "typical_causes": [
            "Upstream shut-in or production decline",
            "Relief valve stuck open or leaking",
            "Control valve failure (fails open)",
            "Pipe rupture or leak upstream",
            "Instrument failure giving false reading",
        ],
        "typical_consequences": [
            "Loss of process efficiency",
            "Vacuum condition in vessel",
            "Gas breakout in liquid lines",
            "Production loss",
        ],
        "expected_safeguards": ["PSLL", "PCV", "PT"],
    },
    "High Level": {
        "guideword": Guideword.HIGH,
        "parameter": DeviationParameter.LEVEL,
        "deviation_name": "High Level",
        "typical_causes": [
            "Liquid outlet valve fails closed or blocked",
            "Level control valve failure (LCV fails closed)",
            "Excessive liquid inflow",
            "Downstream backpressure",
            "Level transmitter failure (reads low falsely)",
        ],
        "typical_consequences": [
            "Liquid carryover to gas system",
            "Downstream equipment damage",
            "Process upset / shutdown",
        ],
        "expected_safeguards": ["LSHH", "LCV", "LT", "ESD_VALVE"],
    },
    "Low Level": {
        "guideword": Guideword.LOW,
        "parameter": DeviationParameter.LEVEL,
        "deviation_name": "Low Level",
        "typical_causes": [
            "Liquid outlet valve fails open",
            "Level control valve failure (LCV fails open)",
            "Loss of inlet flow",
            "Level transmitter failure (reads high falsely)",
            "Drain valve left open",
        ],
        "typical_consequences": [
            "Gas blowby through liquid outlet",
            "Loss of liquid seal",
            "Downstream overpressure",
            "Potential rupture of downstream equipment",
        ],
        "expected_safeguards": ["LSLL", "LCV", "LT"],
    },
    "High Temperature": {
        "guideword": Guideword.HIGH,
        "parameter": DeviationParameter.TEMPERATURE,
        "deviation_name": "High Temperature",
        "typical_causes": [
            "Fire exposure (external heat)",
            "Hot fluid from upstream (heater malfunction)",
            "Loss of cooling",
            "Exothermic reaction",
        ],
        "typical_consequences": [
            "Reduction in material strength",
            "Potential equipment failure at lower pressure",
            "Increased vapor pressure / relief valve lift",
            "Seal / gasket degradation",
        ],
        "expected_safeguards": ["TSHH", "TT", "PSV", "Deluge System", "Fire Detector"],
    },
    "Low Temperature": {
        "guideword": Guideword.LOW,
        "parameter": DeviationParameter.TEMPERATURE,
        "deviation_name": "Low Temperature",
        "typical_causes": [
            "Joule-Thomson cooling from pressure drop",
            "Cold ambient conditions",
            "Cold fluid from upstream",
            "Loss of heat tracing",
        ],
        "typical_consequences": [
            "Hydrate formation causing blockage",
            "Brittle fracture risk (low temp steel issue)",
            "Wax deposition",
            "Instrument freezing / malfunction",
        ],
        "expected_safeguards": ["TSLL", "TT", "Heat Tracing", "Chemical Injection"],
    },
    "No/Low Flow": {
        "guideword": Guideword.NO,
        "parameter": DeviationParameter.FLOW,
        "deviation_name": "No/Low Flow",
        "typical_causes": [
            "Inlet valve closed (manual or automated)",
            "Upstream blockage or shut-in",
            "Pipeline rupture upstream",
            "Hydrate or wax plug formation",
            "Pump failure or trip",
        ],
        "typical_consequences": [
            "Loss of production",
            "Equipment drains down (low level)",
            "Gas blowby if level drops",
            "Downstream starvation",
        ],
        "expected_safeguards": ["FSLL", "FT", "LSLL"],
    },
    "More/High Flow": {
        "guideword": Guideword.HIGH,
        "parameter": DeviationParameter.FLOW,
        "deviation_name": "More/High Flow",
        "typical_causes": [
            "Additional source brought online",
            "Slug flow from pipeline",
            "Control valve fails open",
            "Pressure surge from upstream",
        ],
        "typical_consequences": [
            "Equipment flooding (high level)",
            "Liquid carryover to gas system",
            "Overpressure if outlets cannot handle flow",
            "Erosion of internals / piping",
        ],
        "expected_safeguards": ["FSHH", "LSHH", "PSHH", "PSV"],
    },
    "Reverse / Misdirected Flow": {
        "guideword": Guideword.REVERSE,
        "parameter": DeviationParameter.FLOW,
        "deviation_name": "Reverse / Misdirected Flow",
        "typical_causes": [
            "Check valve failure",
            "Pressure reversal between systems",
            "Backflow from higher pressure downstream",
            "Compressor or pump shutdown causing backflow",
        ],
        "typical_consequences": [
            "Contamination of upstream equipment",
            "Overpressure of upstream low-pressure equipment",
            "Process upset",
            "Potential vessel failure if pressure rating exceeded",
        ],
        "expected_safeguards": ["Check Valve", "PSHH", "ESD_VALVE"],
    },
    "Tube Leak": {
        "guideword": Guideword.OTHER,
        "parameter": DeviationParameter.COMPOSITION,
        "deviation_name": "Tube Leak",
        "typical_causes": [
            "Internal or external corrosion",
            "Erosion from sand or particles",
            "Vibration fatigue",
            "Thermal cycling stress",
        ],
        "typical_consequences": [
            "Cross-contamination of fluids",
            "Overpressure of low-pressure side",
            "Process upset",
            "Potential hydrocarbon release",
        ],
        "expected_safeguards": ["PSHH", "PSV", "Inspection Program", "Gas Detector"],
    },
    "Composition / Contamination": {
        "guideword": Guideword.OTHER,
        "parameter": DeviationParameter.COMPOSITION,
        "deviation_name": "Composition / Contamination",
        "typical_causes": [
            "Off-spec feed from upstream",
            "Chemical injection failure or overdose",
            "Cross-contamination from interconnected systems",
            "Incorrect fluid introduced during maintenance",
        ],
        "typical_consequences": [
            "Equipment corrosion or degradation",
            "Off-spec product",
            "Foaming or emulsion problems",
            "Catalyst poisoning (if applicable)",
        ],
        "expected_safeguards": ["Analyzer", "Sampling Point", "Chemical Injection System"],
    },
    "Human Factors": {
        "guideword": Guideword.OTHER,
        "parameter": DeviationParameter.COMPOSITION,
        "deviation_name": "Human Factors",
        "typical_causes": [],  # LLM-generated from knowledge context
        "typical_consequences": [],
        "expected_safeguards": [],
        "llm_generated": True,
    },
    "Previous Incidents / Learnings": {
        "guideword": Guideword.OTHER,
        "parameter": DeviationParameter.COMPOSITION,
        "deviation_name": "Previous Incidents / Learnings",
        "typical_causes": [],  # LLM-generated from knowledge context
        "typical_consequences": [],
        "expected_safeguards": [],
        "llm_generated": True,
    },
    "Other": {
        "guideword": Guideword.OTHER,
        "parameter": DeviationParameter.COMPOSITION,
        "deviation_name": "Other",
        "typical_causes": [],
        "typical_consequences": [],
        "expected_safeguards": [],
    },
}


# --------------------------------------------------------------------------
# Name mapping: equipment-specific ontology names → standard deviation names
# --------------------------------------------------------------------------

_DEVIATION_NAME_MAP: dict[str, str] = {
    "No Flow": "No/Low Flow",
    "High Flow": "More/High Flow",
    "Reverse Flow": "Reverse / Misdirected Flow",
    "High Discharge Pressure": "High Pressure",
    "Low Suction Pressure": "Low Pressure",
    "High Discharge Temperature": "High Temperature",
    "High Outlet Temperature": "High Temperature",
    "High Pressure (Tube Side)": "High Pressure",
    "No Flow / Dead Head": "No/Low Flow",
}


def _names_match(ontology_name: str, standard_name: str) -> bool:
    """Check if an equipment-specific ontology deviation name matches a standard name."""
    mapped = _DEVIATION_NAME_MAP.get(ontology_name, ontology_name)
    return mapped == standard_name


# --------------------------------------------------------------------------
# Master Ontology: Equipment Type → Parameters → Deviations → Causes/Consequences
# Keys are plain strings so they work with any equipment_type value from the LLM.
# --------------------------------------------------------------------------

EQUIPMENT_ONTOLOGY: dict = {

    # ======================================================================
    # SEPARATOR
    # ======================================================================
    "Separator": {
        "description": "Pressure vessel that separates oil, gas, and water phases",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.LEVEL,
            DeviationParameter.FLOW,
            DeviationParameter.TEMPERATURE,
            DeviationParameter.COMPOSITION,
        ],
        "deviations": {
            # --- PRESSURE ---
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Pressure",
                "typical_causes": [
                    "Gas outlet valve fails closed or blocked",
                    "Downstream restriction or blockage",
                    "Control valve failure (PCV fails closed)",
                    "Excessive inlet flow from wells",
                    "Fire case / external heat input",
                    "Operator error - manual valve left closed",
                ],
                "typical_consequences": [
                    "Separator exceeds design pressure",
                    "Overpressure leading to vessel rupture",
                    "Vapor cloud explosion (VCE)",
                    "Jet fire from pressurized release",
                    "Personnel injury or fatality",
                    "Production shutdown",
                ],
                "expected_safeguards": ["PSHH", "PSV", "PCV", "Gas Detector", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "If rupture: assess personnel exposure count. >10 people = severity 5",
                    "PD_LOR": "Vessel replacement + downtime. Months offline = severity 4-5",
                    "ECR": "Hydrocarbon release to atmosphere. Assess volume.",
                },
            },
            ("Low", "Pressure"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "Low Pressure",
                "typical_causes": [
                    "Upstream well shut-in or production decline",
                    "Relief valve stuck open or leaking",
                    "Control valve failure (PCV fails open)",
                    "Pipe rupture or leak upstream",
                    "Instrument failure giving false reading",
                ],
                "typical_consequences": [
                    "Loss of separation efficiency",
                    "Gas carryunder to liquid outlet",
                    "Compressor surge (if gas flow drops)",
                    "Vacuum condition in vessel",
                    "Production loss",
                ],
                "expected_safeguards": ["PSLL", "PCV", "PT"],
                "consequence_severity_guidance": {
                    "PAF": "Usually low unless vacuum causes collapse",
                    "PD_LOR": "Production loss. Assess duration.",
                    "ECR": "Minimal unless release occurs",
                },
            },
            # --- LEVEL ---
            ("High", "Level"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "High Level",
                "typical_causes": [
                    "Liquid outlet valve fails closed or blocked",
                    "Level control valve failure (LCV fails closed)",
                    "Excessive liquid inflow",
                    "Downstream equipment backpressure",
                    "Level transmitter failure (reads low falsely)",
                    "Emulsion buildup at oil-water interface",
                ],
                "typical_consequences": [
                    "Liquid carryover to gas outlet",
                    "Liquid enters compressor causing mechanical damage",
                    "Compressor failure / shutdown",
                    "Loss of gas processing capacity",
                    "Production shutdown",
                ],
                "expected_safeguards": ["LSHH", "LCV", "LT", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless compressor damage causes secondary event",
                    "PD_LOR": "Compressor repair + downtime. Severity 3-4",
                    "ECR": "Minimal",
                },
            },
            ("Low", "Level"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "Low Level",
                "typical_causes": [
                    "Liquid outlet valve fails open",
                    "Level control valve failure (LCV fails open)",
                    "Loss of inlet flow (well shut-in)",
                    "Level transmitter failure (reads high falsely)",
                    "Drain valve left open",
                ],
                "typical_consequences": [
                    "Gas blowby through liquid outlet",
                    "Gas enters downstream liquid equipment",
                    "Downstream separator overpressure",
                    "Potential rupture of downstream vessel",
                    "Flash fire or explosion",
                ],
                "expected_safeguards": ["LSLL", "LCV", "LT"],
                "consequence_severity_guidance": {
                    "PAF": "High if gas blowby causes downstream rupture",
                    "PD_LOR": "Downstream equipment damage. Severity 3-5",
                    "ECR": "Hydrocarbon release potential",
                },
            },
            # --- FLOW ---
            ("No", "Flow"): {
                "guideword": Guideword.NO,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "No Flow",
                "typical_causes": [
                    "Inlet valve closed (manual or automated)",
                    "Upstream blockage",
                    "Well shut-in",
                    "Pipeline rupture upstream",
                    "Hydrate or wax plug formation",
                ],
                "typical_consequences": [
                    "Loss of production",
                    "Separator drains down (low level)",
                    "Gas blowby if level drops",
                    "Potential downstream starvation",
                ],
                "expected_safeguards": ["FSLL", "FT", "LSLL"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless cascading failure",
                    "PD_LOR": "Production loss. Assess duration.",
                    "ECR": "Minimal",
                },
            },
            ("High", "Flow"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "High Flow",
                "typical_causes": [
                    "Additional well brought online",
                    "Slug flow from pipeline",
                    "Control valve fails open",
                    "Pressure surge from upstream",
                ],
                "typical_consequences": [
                    "Separator flooding (high level)",
                    "Liquid carryover to gas system",
                    "Overpressure if outlets cannot handle flow",
                    "Erosion of internals",
                ],
                "expected_safeguards": ["FSHH", "LSHH", "PSHH", "PSV"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if overpressure results",
                    "PD_LOR": "Equipment damage + downtime",
                    "ECR": "Possible if overflow/release",
                },
            },
            ("Reverse", "Flow"): {
                "guideword": Guideword.REVERSE,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "Reverse Flow",
                "typical_causes": [
                    "Check valve failure",
                    "Pressure reversal between systems",
                    "Backflow from higher pressure downstream",
                    "Compressor shutdown causing backflow",
                ],
                "typical_consequences": [
                    "Contamination of upstream equipment",
                    "Overpressure of upstream low-pressure equipment",
                    "Process upset",
                    "Potential vessel failure if pressure rating exceeded",
                ],
                "expected_safeguards": ["Check Valve", "PSHH", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Depends on upstream equipment rating",
                    "PD_LOR": "Equipment damage if pressure exceeded",
                    "ECR": "Cross-contamination risk",
                },
            },
            # --- TEMPERATURE ---
            ("High", "Temperature"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.TEMPERATURE,
                "deviation_name": "High Temperature",
                "typical_causes": [
                    "Fire exposure (external heat)",
                    "Hot fluid from upstream (heater malfunction)",
                    "Loss of cooling",
                    "Exothermic reaction (rare in separator)",
                ],
                "typical_consequences": [
                    "Reduction in vessel material strength",
                    "Potential vessel failure at lower pressure",
                    "Increased vapor pressure",
                    "Relief valve lift",
                    "Seal / gasket degradation",
                ],
                "expected_safeguards": ["TSHH", "TT", "PSV", "Deluge System", "Fire Detector"],
                "consequence_severity_guidance": {
                    "PAF": "High if vessel integrity compromised",
                    "PD_LOR": "Vessel damage + downtime",
                    "ECR": "Release if failure occurs",
                },
            },
            ("Low", "Temperature"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.TEMPERATURE,
                "deviation_name": "Low Temperature",
                "typical_causes": [
                    "Joule-Thomson cooling from pressure drop",
                    "Cold ambient conditions",
                    "Cold fluid from upstream",
                    "Loss of heat tracing",
                ],
                "typical_consequences": [
                    "Hydrate formation causing blockage",
                    "Brittle fracture risk (low temp steel issue)",
                    "Wax deposition",
                    "Instrument freezing / malfunction",
                ],
                "expected_safeguards": ["TSLL", "TT", "Heat Tracing", "Chemical Injection"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if brittle fracture leads to rupture",
                    "PD_LOR": "Blockage causing production loss",
                    "ECR": "Minimal unless rupture",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # HEADER
    # ======================================================================
    "Header": {
        "description": "Large diameter pipe collecting/distributing flow from multiple sources",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.FLOW,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Pressure",
                "typical_causes": [
                    "Blocked downstream (valve closed)",
                    "Surge from multiple wells",
                    "Downstream equipment overpressure",
                    "Control valve failure",
                ],
                "typical_consequences": [
                    "Header overpressure",
                    "Pipe rupture at weak point (flange, weld)",
                    "Hydrocarbon release",
                    "Fire / explosion",
                ],
                "expected_safeguards": ["PSHH", "PSV", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Depends on personnel exposure and release size",
                    "PD_LOR": "Pipe repair + downtime",
                    "ECR": "Hydrocarbon release volume",
                },
            },
            ("Low", "Pressure"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "Low Pressure",
                "typical_causes": [
                    "Well shut-in upstream",
                    "Pipe leak or rupture",
                    "Relief valve stuck open",
                    "Large consumer downstream drawing excess",
                ],
                "typical_consequences": [
                    "Loss of flow to downstream separator",
                    "Production loss",
                    "Gas breakout in header",
                ],
                "expected_safeguards": ["PSLL", "PT"],
                "consequence_severity_guidance": {
                    "PAF": "Low",
                    "PD_LOR": "Production loss",
                    "ECR": "Minimal",
                },
            },
            ("No", "Flow"): {
                "guideword": Guideword.NO,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "No Flow",
                "typical_causes": [
                    "All inlet valves closed",
                    "Complete well shut-in",
                    "Pipeline blockage (hydrate, wax, scale)",
                    "Header isolation for maintenance",
                ],
                "typical_consequences": [
                    "No feed to downstream separator",
                    "Loss of production",
                    "Downstream equipment starved",
                ],
                "expected_safeguards": ["FT", "LSLL"],
                "consequence_severity_guidance": {
                    "PAF": "Low",
                    "PD_LOR": "Full production loss. Duration dependent.",
                    "ECR": "Minimal",
                },
            },
            ("Reverse", "Flow"): {
                "guideword": Guideword.REVERSE,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "Reverse Flow",
                "typical_causes": [
                    "Pressure differential reversal",
                    "Check valve failure",
                    "Backflow from separator",
                ],
                "typical_consequences": [
                    "Contamination of upstream wells",
                    "Overpressure of low-rated upstream piping",
                    "Process upset",
                ],
                "expected_safeguards": ["Check Valve", "PSHH"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless upstream failure",
                    "PD_LOR": "Equipment damage if ratings exceeded",
                    "ECR": "Cross-contamination",
                },
            },
            ("High", "Flow"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "High Flow",
                "typical_causes": [
                    "Multiple wells brought online simultaneously",
                    "Slug flow",
                    "Upstream pressure surge",
                ],
                "typical_consequences": [
                    "Downstream separator flooding",
                    "Erosion of header piping",
                    "Vibration and mechanical fatigue",
                ],
                "expected_safeguards": ["FSHH", "PSHH"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless erosion causes leak",
                    "PD_LOR": "Piping damage, separator upset",
                    "ECR": "Minimal",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # COMPRESSOR
    # ======================================================================
    "Compressor": {
        "description": "Rotating equipment that increases gas pressure",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.FLOW,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Discharge Pressure",
                "typical_causes": [
                    "Downstream blockage or closed valve",
                    "Anti-surge valve failure",
                    "Control system malfunction",
                ],
                "typical_consequences": [
                    "Compressor trip on high discharge pressure",
                    "Overpressure of discharge piping",
                    "Mechanical seal failure",
                    "Gas release",
                ],
                "expected_safeguards": ["PSHH", "PSV", "Anti-Surge Valve", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Medium - gas release in compressor area",
                    "PD_LOR": "Compressor damage + downtime",
                    "ECR": "Gas release to atmosphere",
                },
            },
            ("Low", "Pressure"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "Low Suction Pressure",
                "typical_causes": [
                    "Loss of upstream feed",
                    "Suction filter blockage",
                    "Upstream shutdown",
                ],
                "typical_consequences": [
                    "Compressor surge",
                    "Mechanical damage to internals",
                    "Compressor trip",
                ],
                "expected_safeguards": ["PSLL", "Anti-Surge Valve", "Vibration Monitor"],
                "consequence_severity_guidance": {
                    "PAF": "Low",
                    "PD_LOR": "Compressor repair + downtime",
                    "ECR": "Minimal",
                },
            },
            ("No", "Flow"): {
                "guideword": Guideword.NO,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "No Flow",
                "typical_causes": [
                    "Suction valve closed",
                    "Loss of feed gas",
                    "Blocked suction scrubber",
                ],
                "typical_consequences": [
                    "Compressor deadheading",
                    "Overheating",
                    "Mechanical failure",
                    "Compressor trip",
                ],
                "expected_safeguards": ["FSLL", "TSHH", "Vibration Monitor"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless mechanical failure causes release",
                    "PD_LOR": "Compressor damage. Severity 3-4",
                    "ECR": "Minimal",
                },
            },
            ("High", "Temperature"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.TEMPERATURE,
                "deviation_name": "High Discharge Temperature",
                "typical_causes": [
                    "High compression ratio",
                    "Intercooler failure",
                    "Low coolant flow",
                    "Valve plate failure (reciprocating)",
                ],
                "typical_consequences": [
                    "Lube oil degradation",
                    "Seal failure and gas leak",
                    "Auto-ignition risk",
                    "Compressor trip",
                ],
                "expected_safeguards": ["TSHH", "TT", "Lube Oil System", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if gas leak and ignition",
                    "PD_LOR": "Major compressor overhaul",
                    "ECR": "Gas release",
                },
            },
        },
        "additional_deviations": [
            {
                "deviation_name": "Liquid Carryover",
                "typical_causes": [
                    "Suction scrubber high level",
                    "Suction scrubber LSHH failure",
                    "Slug from upstream separator",
                ],
                "typical_consequences": [
                    "Compressor mechanical damage (liquid slugging)",
                    "Valve plate damage (reciprocating)",
                    "Impeller damage (centrifugal)",
                    "Compressor trip or catastrophic failure",
                ],
                "expected_safeguards": ["LSHH", "Suction Scrubber", "Vibration Monitor"],
            },
        ],
    },

    # ======================================================================
    # PUMP
    # ======================================================================
    "Pump": {
        "description": "Rotating equipment that moves liquid",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.FLOW,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Discharge Pressure",
                "typical_causes": [
                    "Downstream valve closed",
                    "Blocked discharge line",
                    "Control valve failure",
                ],
                "typical_consequences": [
                    "Pump deadheading",
                    "Mechanical seal failure",
                    "Liquid release",
                    "Pump trip on high pressure",
                ],
                "expected_safeguards": ["PSHH", "PSV", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Low to medium depending on fluid",
                    "PD_LOR": "Pump repair + downtime",
                    "ECR": "Liquid spill if seal failure",
                },
            },
            ("No", "Flow"): {
                "guideword": Guideword.NO,
                "parameter": DeviationParameter.FLOW,
                "deviation_name": "No Flow / Dead Head",
                "typical_causes": [
                    "Suction valve closed",
                    "Loss of liquid level in feed vessel",
                    "Suction strainer blocked",
                    "Pump cavitation",
                ],
                "typical_consequences": [
                    "Pump overheating",
                    "Seal failure",
                    "Dry running damage",
                    "Pump trip",
                ],
                "expected_safeguards": ["FSLL", "LSLL", "TSHH", "Vibration Monitor"],
                "consequence_severity_guidance": {
                    "PAF": "Low",
                    "PD_LOR": "Pump repair. Severity 2-3",
                    "ECR": "Minimal",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # HEAT EXCHANGER
    # ======================================================================
    "Heat Exchanger": {
        "description": "Equipment that transfers heat between two fluid streams",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.FLOW,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Pressure (Tube Side)",
                "typical_causes": [
                    "Tube leak causing shell-tube pressure equalization",
                    "Blocked outlet",
                    "Thermal expansion of trapped liquid",
                ],
                "typical_consequences": [
                    "Overpressure of low-pressure side",
                    "Shell or tube rupture",
                    "Hydrocarbon release",
                ],
                "expected_safeguards": ["PSHH", "PSV", "TRV"],
                "consequence_severity_guidance": {
                    "PAF": "Medium to high if rupture",
                    "PD_LOR": "Exchanger replacement + downtime",
                    "ECR": "Fluid release",
                },
            },
            ("High", "Temperature"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.TEMPERATURE,
                "deviation_name": "High Outlet Temperature",
                "typical_causes": [
                    "Loss of cooling medium",
                    "Fouling reducing heat transfer",
                    "Bypass valve open",
                ],
                "typical_consequences": [
                    "Downstream equipment temperature exceedance",
                    "Material degradation",
                    "Process upset",
                ],
                "expected_safeguards": ["TSHH", "TCV", "TT"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless cascading failure",
                    "PD_LOR": "Process upset + quality loss",
                    "ECR": "Minimal",
                },
            },
        },
        "additional_deviations": [
            {
                "deviation_name": "Tube Leak",
                "typical_causes": [
                    "Corrosion (internal or external)",
                    "Erosion from sand or particles",
                    "Vibration fatigue",
                    "Thermal cycling stress",
                ],
                "typical_consequences": [
                    "Cross-contamination of fluids",
                    "Overpressure of low-pressure side",
                    "Process upset",
                    "Potential hydrocarbon release if shell-side breaches",
                ],
                "expected_safeguards": ["PSHH", "PSV", "Inspection Program", "Gas Detector"],
            },
        ],
    },

    # ======================================================================
    # VESSEL (Generic Pressure Vessel)
    # ======================================================================
    "Vessel": {
        "description": "Generic pressure vessel for fluid containment or processing",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.LEVEL,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Pressure",
                "typical_causes": [
                    "Blocked outlet",
                    "Control valve failure",
                    "External fire",
                    "Upstream overpressure",
                ],
                "typical_consequences": [
                    "Vessel overpressure",
                    "Vessel rupture",
                    "Hydrocarbon release",
                    "Explosion / fire",
                ],
                "expected_safeguards": ["PSHH", "PSV", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "High if rupture in occupied area",
                    "PD_LOR": "Vessel replacement + downtime",
                    "ECR": "Hydrocarbon release",
                },
            },
            ("High", "Level"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "High Level",
                "typical_causes": [
                    "Outlet valve closed",
                    "LCV failure",
                    "Excessive inflow",
                ],
                "typical_consequences": [
                    "Liquid carryover to gas system",
                    "Process upset downstream",
                ],
                "expected_safeguards": ["LSHH", "LCV", "LT"],
                "consequence_severity_guidance": {
                    "PAF": "Low to medium",
                    "PD_LOR": "Downstream equipment damage",
                    "ECR": "Minimal",
                },
            },
            ("Low", "Level"): {
                "guideword": Guideword.LOW,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "Low Level",
                "typical_causes": [
                    "Outlet valve fails open",
                    "Loss of inflow",
                    "Drain open",
                ],
                "typical_consequences": [
                    "Gas blowby",
                    "Loss of liquid seal",
                    "Downstream overpressure",
                ],
                "expected_safeguards": ["LSLL", "LCV", "LT"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if gas blowby causes downstream event",
                    "PD_LOR": "Downstream damage",
                    "ECR": "Gas release potential",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # SCRUBBER
    # ======================================================================
    "Scrubber": {
        "description": "Vessel designed to remove liquid droplets from gas stream",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.LEVEL,
            DeviationParameter.FLOW,
        ],
        "deviations": {
            ("High", "Level"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "High Level",
                "typical_causes": [
                    "Liquid dump valve fails closed",
                    "Excessive liquid in gas stream",
                    "LCV malfunction",
                ],
                "typical_consequences": [
                    "Liquid carryover to compressor",
                    "Compressor damage (liquid slugging)",
                    "Compressor trip",
                ],
                "expected_safeguards": ["LSHH", "LCV", "ESD_VALVE"],
                "consequence_severity_guidance": {
                    "PAF": "Low",
                    "PD_LOR": "Compressor damage. Severity 3-4",
                    "ECR": "Minimal",
                },
            },
            ("High", "Pressure"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.PRESSURE,
                "deviation_name": "High Pressure",
                "typical_causes": [
                    "Downstream blockage",
                    "Compressor trip causing pressure buildup",
                ],
                "typical_consequences": [
                    "Vessel overpressure",
                    "PSV lift",
                    "Potential rupture if PSV fails",
                ],
                "expected_safeguards": ["PSHH", "PSV"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if rupture",
                    "PD_LOR": "Vessel damage + downtime",
                    "ECR": "Gas release",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # KNOCKOUT DRUM
    # ======================================================================
    "Knockout Drum": {
        "description": "Vessel to remove liquid from gas before flare or vent",
        "applicable_parameters": [
            DeviationParameter.PRESSURE,
            DeviationParameter.LEVEL,
        ],
        "deviations": {
            ("High", "Level"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "High Level",
                "typical_causes": [
                    "Excessive liquid carryover during blowdown",
                    "Drain valve closed or blocked",
                    "Pump failure (if pump-out type)",
                ],
                "typical_consequences": [
                    "Liquid carryover to flare",
                    "Flare flame-out or unstable combustion",
                    "Burning liquid rain from flare tip",
                ],
                "expected_safeguards": ["LSHH", "LT", "Drain System"],
                "consequence_severity_guidance": {
                    "PAF": "Medium if burning liquid reaches grade",
                    "PD_LOR": "Flare damage",
                    "ECR": "Burning hydrocarbons released",
                },
            },
        },
        "additional_deviations": [],
    },

    # ======================================================================
    # TANK (Atmospheric Storage)
    # ======================================================================
    "Tank": {
        "description": "Atmospheric or low-pressure storage tank",
        "applicable_parameters": [
            DeviationParameter.LEVEL,
            DeviationParameter.TEMPERATURE,
        ],
        "deviations": {
            ("High", "Level"): {
                "guideword": Guideword.HIGH,
                "parameter": DeviationParameter.LEVEL,
                "deviation_name": "High Level",
                "typical_causes": [
                    "Outlet valve closed",
                    "Transfer pump failure",
                    "Overfill from upstream",
                    "Level instrument failure",
                ],
                "typical_consequences": [
                    "Tank overflow",
                    "Liquid spill to containment",
                    "Environmental contamination if containment breached",
                    "Fire risk if flammable",
                ],
                "expected_safeguards": ["LSHH", "LT", "Overflow Line", "Containment Bund"],
                "consequence_severity_guidance": {
                    "PAF": "Low unless fire",
                    "PD_LOR": "Cleanup + production loss",
                    "ECR": "High if containment breached. Severity 3-5",
                },
            },
        },
        "additional_deviations": [],
    },
}


# --------------------------------------------------------------------------
# Lookup Functions
# --------------------------------------------------------------------------

def get_equipment_ontology(equipment_type: str) -> dict | None:
    """Get full ontology for an equipment type (plain string key)."""
    return EQUIPMENT_ONTOLOGY.get(equipment_type)


def get_applicable_parameters(equipment_type: str) -> list[DeviationParameter]:
    """Get which process parameters apply to this equipment type."""
    ontology = EQUIPMENT_ONTOLOGY.get(equipment_type)
    if not ontology:
        return []
    return ontology.get("applicable_parameters", [])


def get_deviations_for_equipment(
    equipment_type: str,
    selected_deviation_types: list[str] | None = None,
) -> list[dict]:
    """
    Get all applicable deviations for an equipment type.

    Uses STANDARD_DEVIATIONS as the base list, then overlays
    equipment-specific causes/consequences/safeguards from EQUIPMENT_ONTOLOGY
    where available.

    Args:
        equipment_type: Equipment type string
        selected_deviation_types: If provided, only return these deviation types.
                                 If None, return all 14 standard types.
    """
    ontology = EQUIPMENT_ONTOLOGY.get(equipment_type)

    # Determine which deviation types to include
    types_to_use = selected_deviation_types or list(STANDARD_DEVIATIONS.keys())

    deviations = []
    for dev_type_name in types_to_use:
        base = STANDARD_DEVIATIONS.get(dev_type_name)
        if not base:
            continue

        # Start with a copy of the standard deviation
        dev = dict(base)

        # If we have equipment-specific data, merge richer content in
        if ontology:
            for _key, eq_specific in ontology.get("deviations", {}).items():
                if _names_match(eq_specific.get("deviation_name", ""), dev_type_name):
                    # Use equipment-specific data when richer
                    if eq_specific.get("typical_causes"):
                        dev["typical_causes"] = eq_specific["typical_causes"]
                    if eq_specific.get("typical_consequences"):
                        dev["typical_consequences"] = eq_specific["typical_consequences"]
                    if eq_specific.get("expected_safeguards"):
                        dev["expected_safeguards"] = eq_specific["expected_safeguards"]
                    if eq_specific.get("consequence_severity_guidance"):
                        dev["consequence_severity_guidance"] = eq_specific["consequence_severity_guidance"]
                    break

        deviations.append(dev)

    return deviations


def get_standard_deviation_types() -> list[str]:
    """Return the ordered list of all 14 standard deviation type names."""
    return list(STANDARD_DEVIATIONS.keys())


def get_all_equipment_types() -> list[str]:
    """Return all equipment types that have ontology defined."""
    return list(EQUIPMENT_ONTOLOGY.keys())
