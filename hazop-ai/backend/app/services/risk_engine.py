"""
Risk Matrix Engine — Deterministic Risk Calculation

This is the most safety-critical module in the system.
ALL risk calculations are deterministic matrix lookups.
NO LLM is involved in risk scoring.

Risk is evaluated separately for three consequence categories:
  - PAF   (People at Facility)
  - PD/LOR (Property Damage / Loss of Revenue)
  - ECR   (Environmental)

Each uses:
  Risk Level = Matrix[Consequence Severity][Probability]

The matrices, severity definitions, and probability reduction logic
are based on OXY's HAZOP methodology and consequence guidance.

IMPORTANT: This engine must be auditable and explainable.
Every risk calculation must be traceable to its inputs.
"""

from app.models.hazop_models import (
    RiskScore, RiskAssessment, Safeguard,
    PRClassification, MitigationType,
)


# ==========================================================================
# RISK MATRICES — Lookup Tables
# ==========================================================================
# Format: MATRIX[consequence_severity][probability] = risk_level
# Consequence: 1 (minor) to 5 (catastrophic)
# Probability: 1 (extremely unlikely) to 5 (frequent)
# Risk Level: A (lowest) to E (highest)

PAF_RISK_MATRIX: dict[int, dict[int, str]] = {
    1: {1: "A", 2: "A", 3: "A", 4: "B", 5: "B"},
    2: {1: "A", 2: "A", 3: "B", 4: "B", 5: "C"},
    3: {1: "A", 2: "B", 3: "B", 4: "C", 5: "C"},
    4: {1: "B", 2: "B", 3: "C", 4: "C", 5: "D"},
    5: {1: "B", 2: "C", 3: "C", 4: "D", 5: "E"},
}

PD_LOR_RISK_MATRIX: dict[int, dict[int, str]] = {
    1: {1: "A", 2: "A", 3: "A", 4: "B", 5: "B"},
    2: {1: "A", 2: "A", 3: "B", 4: "B", 5: "C"},
    3: {1: "A", 2: "B", 3: "B", 4: "C", 5: "C"},
    4: {1: "B", 2: "B", 3: "C", 4: "C", 5: "D"},
    5: {1: "B", 2: "C", 3: "C", 4: "D", 5: "E"},
}

ECR_RISK_MATRIX: dict[int, dict[int, str]] = {
    1: {1: "A", 2: "A", 3: "A", 4: "B", 5: "B"},
    2: {1: "A", 2: "A", 3: "B", 4: "B", 5: "C"},
    3: {1: "A", 2: "B", 3: "B", 4: "C", 5: "C"},
    4: {1: "B", 2: "B", 3: "C", 4: "C", 5: "D"},
    5: {1: "B", 2: "C", 3: "C", 4: "D", 5: "E"},
}


# ==========================================================================
# CONSEQUENCE SEVERITY DEFINITIONS
# ==========================================================================

PAF_SEVERITY: dict[int, dict] = {
    1: {
        "level": "Negligible",
        "description": "First aid injury only",
        "guidance": "Minor bruise, cut requiring first aid. No lost time.",
    },
    2: {
        "level": "Minor",
        "description": "Medical treatment case",
        "guidance": "Injury requiring medical treatment but no hospitalization. 1-2 people affected.",
    },
    3: {
        "level": "Moderate",
        "description": "Serious injury / lost time incident",
        "guidance": "Hospitalization required. Permanent partial disability possible. 1-3 people.",
    },
    4: {
        "level": "Major",
        "description": "Single fatality or permanent total disability",
        "guidance": "One fatality or life-altering injury. Up to 3 people severely affected.",
    },
    5: {
        "level": "Catastrophic",
        "description": "Multiple fatalities",
        "guidance": "Multiple fatalities possible. Large area of impact. >3 people at risk of fatality.",
    },
}

PD_LOR_SEVERITY: dict[int, dict] = {
    1: {
        "level": "Negligible",
        "description": "Minor repair, minimal production impact",
        "guidance": "< $100K damage. < 1 day downtime.",
    },
    2: {
        "level": "Minor",
        "description": "Moderate equipment damage",
        "guidance": "$100K - $1M damage. Days of downtime.",
    },
    3: {
        "level": "Moderate",
        "description": "Significant damage, weeks of downtime",
        "guidance": "$1M - $10M damage. 1-4 weeks downtime.",
    },
    4: {
        "level": "Major",
        "description": "Major damage, months offline",
        "guidance": "$10M - $100M damage. 1-6 months downtime.",
    },
    5: {
        "level": "Catastrophic",
        "description": "Catastrophic facility loss",
        "guidance": "> $100M damage. > 6 months downtime. Possible total loss.",
    },
}

ECR_SEVERITY: dict[int, dict] = {
    1: {
        "level": "Negligible",
        "description": "Negligible environmental effect",
        "guidance": "Contained within facility. No reportable release.",
    },
    2: {
        "level": "Minor",
        "description": "Minor environmental impact",
        "guidance": "Small release. Contained on-site. Minor cleanup.",
    },
    3: {
        "level": "Moderate",
        "description": "Moderate environmental damage",
        "guidance": "Reportable release. Localized off-site impact. Significant cleanup.",
    },
    4: {
        "level": "Major",
        "description": "Major environmental damage",
        "guidance": "Significant off-site impact. Regulatory action. Extended remediation.",
    },
    5: {
        "level": "Catastrophic",
        "description": "Catastrophic environmental disaster",
        "guidance": "Widespread environmental damage. National attention. Years of remediation.",
    },
}


# ==========================================================================
# PROBABILITY DEFINITIONS
# ==========================================================================

PROBABILITY_LEVELS: dict[int, dict] = {
    1: {
        "level": "Extremely Unlikely",
        "frequency": "< 1 in 10,000 years",
        "description": "Has never occurred in industry. Requires multiple simultaneous failures.",
    },
    2: {
        "level": "Unlikely",
        "frequency": "1 in 1,000 - 10,000 years",
        "description": "Rare in industry. Has occurred but very infrequent.",
    },
    3: {
        "level": "Possible",
        "frequency": "1 in 100 - 1,000 years",
        "description": "Has occurred several times in industry.",
    },
    4: {
        "level": "Likely",
        "frequency": "1 in 10 - 100 years",
        "description": "Expected to occur during facility lifetime.",
    },
    5: {
        "level": "Frequent",
        "frequency": "1 in 1 - 10 years",
        "description": "Expected to occur multiple times during facility lifetime.",
    },
}


# ==========================================================================
# PROBABILITY REDUCTION FROM SAFEGUARDS
# ==========================================================================
# Each PR type provides a probability reduction credit.
# CME safeguards provide more reduction than KME.
# Multiple independent layers stack (reduce probability further).

PR_PROBABILITY_REDUCTION: dict[PRClassification, int] = {
    PRClassification.PR_1: 2,   # SIS/ESD — strong reduction
    PRClassification.PR_4: 2,   # Relief device — strong reduction
    PRClassification.PR_5: 1,   # Fire & gas — moderate reduction
    PRClassification.PR_2: 1,   # Alarm + operator — moderate reduction
    PRClassification.PR_3: 1,   # Mechanical integrity — moderate reduction
    PRClassification.PR_20: 0,  # Procedural — no probability credit (reduces consequence only)
    PRClassification.PR_21: 1,  # Corrosion management — moderate reduction
    PRClassification.OTHER: 0,  # Unclassified — no credit
}

# Maximum total probability reduction allowed
MAX_PROBABILITY_REDUCTION = 4  # Cannot reduce below probability 1


# ==========================================================================
# RISK LEVEL CLASSIFICATION
# ==========================================================================

RISK_LEVEL_DEFINITIONS: dict[str, dict] = {
    "A": {
        "name": "Low",
        "color": "green",
        "action": "Acceptable. No additional action required.",
        "requires_recommendation": False,
    },
    "B": {
        "name": "Medium-Low",
        "color": "yellow",
        "action": "Acceptable with existing safeguards. Monitor.",
        "requires_recommendation": False,
    },
    "C": {
        "name": "Medium",
        "color": "orange",
        "action": "Review required. Consider additional safeguards.",
        "requires_recommendation": True,
    },
    "D": {
        "name": "High",
        "color": "red",
        "action": "Unacceptable. Additional safeguards or design changes required.",
        "requires_recommendation": True,
    },
    "E": {
        "name": "Extreme",
        "color": "darkred",
        "action": "Unacceptable. Immediate action required. Stop operation if necessary.",
        "requires_recommendation": True,
    },
}


# ==========================================================================
# RISK ENGINE CLASS
# ==========================================================================

class RiskEngineService:
    """
    Deterministic risk calculation engine.

    Calculates risk for PAF, PD/LOR, and ECR using:
      1. Consequence severity (from scenario analysis)
      2. Base probability (from deviation likelihood)
      3. Probability reduction (from safeguard PR credits)
      4. Matrix lookup (consequence × adjusted probability → risk level)
    """

    def calculate_risk(
        self,
        paf_consequence: int | None = None,
        pd_lor_consequence: int | None = None,
        ecr_consequence: int | None = None,
        base_probability: int = 3,
        safeguards: list[Safeguard] | None = None,
    ) -> RiskAssessment:
        """
        Calculate full risk assessment across all categories.

        Args:
            paf_consequence: PAF severity 1-5 (None to skip)
            pd_lor_consequence: PD/LOR severity 1-5 (None to skip)
            ecr_consequence: ECR severity 1-5 (None to skip)
            base_probability: Starting probability before safeguard credits
            safeguards: List of safeguards that reduce probability

        Returns:
            RiskAssessment with scores for each applicable category
        """
        # Calculate adjusted probability from safeguard credits
        adjusted_probability = self.calculate_adjusted_probability(
            base_probability=base_probability,
            safeguards=safeguards or [],
        )

        paf_score = None
        pd_lor_score = None
        ecr_score = None

        if paf_consequence is not None:
            paf_score = self._lookup_risk(
                consequence=paf_consequence,
                probability=adjusted_probability,
                matrix=PAF_RISK_MATRIX,
            )

        if pd_lor_consequence is not None:
            pd_lor_score = self._lookup_risk(
                consequence=pd_lor_consequence,
                probability=adjusted_probability,
                matrix=PD_LOR_RISK_MATRIX,
            )

        if ecr_consequence is not None:
            ecr_score = self._lookup_risk(
                consequence=ecr_consequence,
                probability=adjusted_probability,
                matrix=ECR_RISK_MATRIX,
            )

        return RiskAssessment(
            paf=paf_score,
            pd_lor=pd_lor_score,
            ecr=ecr_score,
        )

    def calculate_adjusted_probability(
        self,
        base_probability: int,
        safeguards: list[Safeguard],
    ) -> int:
        """
        Reduce probability based on safeguard PR credits.

        Rules:
          - Each unique PR type contributes its reduction value
          - Multiple safeguards of the same PR type count only once
          - Total reduction is capped at MAX_PROBABILITY_REDUCTION
          - Probability cannot go below 1

        Returns:
            Adjusted probability level (1-5)
        """
        if not safeguards:
            return base_probability

        # Collect unique PR types and their reduction values
        # Multiple instruments of same PR type only counted once
        unique_pr_reductions: dict[PRClassification, int] = {}
        for sg in safeguards:
            pr = sg.pr_classification
            if pr not in unique_pr_reductions:
                unique_pr_reductions[pr] = PR_PROBABILITY_REDUCTION.get(pr, 0)

        total_reduction = sum(unique_pr_reductions.values())
        total_reduction = min(total_reduction, MAX_PROBABILITY_REDUCTION)

        adjusted = base_probability - total_reduction
        return max(adjusted, 1)  # Never below 1

    def _lookup_risk(
        self,
        consequence: int,
        probability: int,
        matrix: dict[int, dict[int, str]],
    ) -> RiskScore:
        """
        Perform deterministic matrix lookup.

        Args:
            consequence: Severity level 1-5
            probability: Probability level 1-5
            matrix: Risk matrix dictionary

        Returns:
            RiskScore with consequence, probability, and looked-up risk level
        """
        # Clamp values to valid range
        consequence = max(1, min(5, consequence))
        probability = max(1, min(5, probability))

        risk_level = matrix[consequence][probability]

        return RiskScore(
            consequence=consequence,
            probability=probability,
            risk_level=risk_level,
        )

    def get_risk_level_info(self, risk_level: str) -> dict:
        """Get detailed info about a risk level (name, color, action required)."""
        return RISK_LEVEL_DEFINITIONS.get(risk_level, {
            "name": "Unknown",
            "color": "gray",
            "action": "Risk level not recognized.",
            "requires_recommendation": True,
        })

    def requires_recommendation(self, risk_assessment: RiskAssessment) -> bool:
        """
        Check if any risk category requires a recommendation.
        Risk levels C, D, E require recommendations.
        """
        for score in [risk_assessment.paf, risk_assessment.pd_lor, risk_assessment.ecr]:
            if score is not None:
                info = self.get_risk_level_info(score.risk_level)
                if info.get("requires_recommendation", False):
                    return True
        return False

    def get_highest_risk_level(self, risk_assessment: RiskAssessment) -> str | None:
        """Get the highest (worst) risk level across all categories."""
        risk_order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
        highest = None
        highest_rank = -1

        for score in [risk_assessment.paf, risk_assessment.pd_lor, risk_assessment.ecr]:
            if score is not None:
                rank = risk_order.get(score.risk_level, -1)
                if rank > highest_rank:
                    highest_rank = rank
                    highest = score.risk_level

        return highest

    def explain_risk_calculation(
        self,
        paf_consequence: int | None,
        pd_lor_consequence: int | None,
        ecr_consequence: int | None,
        base_probability: int,
        safeguards: list[Safeguard],
    ) -> dict:
        """
        Generate a full explainable audit trail for a risk calculation.
        This is critical for regulatory compliance — every step is documented.
        """
        # Probability reduction breakdown
        unique_pr: dict[str, int] = {}
        for sg in safeguards:
            pr_val = sg.pr_classification.value
            if pr_val not in unique_pr:
                unique_pr[pr_val] = PR_PROBABILITY_REDUCTION.get(sg.pr_classification, 0)

        total_reduction = min(sum(unique_pr.values()), MAX_PROBABILITY_REDUCTION)
        adjusted_prob = max(base_probability - total_reduction, 1)

        # Calculate risk
        risk = self.calculate_risk(
            paf_consequence=paf_consequence,
            pd_lor_consequence=pd_lor_consequence,
            ecr_consequence=ecr_consequence,
            base_probability=base_probability,
            safeguards=safeguards,
        )

        explanation = {
            "inputs": {
                "paf_consequence": paf_consequence,
                "pd_lor_consequence": pd_lor_consequence,
                "ecr_consequence": ecr_consequence,
                "base_probability": base_probability,
                "base_probability_definition": PROBABILITY_LEVELS.get(base_probability, {}),
            },
            "safeguard_credits": {
                "safeguard_count": len(safeguards),
                "unique_pr_types": unique_pr,
                "total_reduction": total_reduction,
                "max_reduction_allowed": MAX_PROBABILITY_REDUCTION,
                "reduction_capped": total_reduction >= MAX_PROBABILITY_REDUCTION,
            },
            "adjusted_probability": {
                "value": adjusted_prob,
                "calculation": f"{base_probability} - {total_reduction} = {adjusted_prob} (min 1)",
                "definition": PROBABILITY_LEVELS.get(adjusted_prob, {}),
            },
            "risk_results": {
                "paf": {
                    "consequence": paf_consequence,
                    "consequence_definition": PAF_SEVERITY.get(paf_consequence, {}) if paf_consequence else None,
                    "probability": adjusted_prob,
                    "risk_level": risk.paf.risk_level if risk.paf else None,
                    "risk_info": self.get_risk_level_info(risk.paf.risk_level) if risk.paf else None,
                } if paf_consequence else None,
                "pd_lor": {
                    "consequence": pd_lor_consequence,
                    "consequence_definition": PD_LOR_SEVERITY.get(pd_lor_consequence, {}) if pd_lor_consequence else None,
                    "probability": adjusted_prob,
                    "risk_level": risk.pd_lor.risk_level if risk.pd_lor else None,
                    "risk_info": self.get_risk_level_info(risk.pd_lor.risk_level) if risk.pd_lor else None,
                } if pd_lor_consequence else None,
                "ecr": {
                    "consequence": ecr_consequence,
                    "consequence_definition": ECR_SEVERITY.get(ecr_consequence, {}) if ecr_consequence else None,
                    "probability": adjusted_prob,
                    "risk_level": risk.ecr.risk_level if risk.ecr else None,
                    "risk_info": self.get_risk_level_info(risk.ecr.risk_level) if risk.ecr else None,
                } if ecr_consequence else None,
            },
            "requires_recommendation": self.requires_recommendation(risk),
            "highest_risk_level": self.get_highest_risk_level(risk),
        }

        return explanation


# Module-level instance
risk_engine = RiskEngineService()
