"""Source-label → taxonomy mapping (Phase 3 dataset pipeline)."""
import re

from app.modules.ml_nlp.taxonomy import MODEL_LABELS

# ------------------------------------------------------------------
# LEDGAR (provision type) → taxonomy. Only clean, defensible mappings.
# ------------------------------------------------------------------
LEDGAR_TO_TAXONOMY: dict[int, str] = {
    # indemnification
    49: "indemnification",  # Indemnifications
    50: "indemnification",  # Indemnity
    # termination
    88: "termination",  # Terminations
    # payment (money-flow obligations: amounts, fees, taxes, costs)
    25: "payment",  # Costs
    41: "payment",  # Expenses
    42: "payment",  # Fees
    68: "payment",  # Payments
    86: "payment",  # Tax Withholdings
    87: "payment",  # Taxes
    99: "payment",  # Withholdings
    # confidentiality
    20: "confidentiality",  # Confidentiality
    # intellectual_property
    53: "intellectual_property",  # Intellectual Property
    # governing_law
    47: "governing_law",  # Governing Laws
    # jurisdiction (forum / submission to courts)
    21: "jurisdiction",  # Consent To Jurisdiction
    56: "jurisdiction",  # Jurisdictions
    82: "jurisdiction",  # Submission To Jurisdiction
    94: "jurisdiction",  # Venues
    # warranty (representations & warranties are one negotiated block)
    76: "warranty",  # Representations
    98: "warranty",  # Warranties
    # insurance
    51: "insurance",  # Insurances
    # audit — APPROXIMATE (documented): LEDGAR has no audit-rights class;
    # books/records provisions are what audit clauses inspect. CUAD's
    # "Audit Rights" (below) is the direct source; these only supplement it.
    14: "audit",  # Books
    73: "audit",  # Records
    # dispute_resolution
    6: "dispute_resolution",  # Arbitration
    58: "dispute_resolution",  # Litigations
    96: "dispute_resolution",  # Waiver Of Jury Trials
}

# Every other LEDGAR label (0-99 not listed above) → "other". Listed here so
# the fallback is auditable, not silent. Notable judgment calls:
# - 74 Remedies → other (remedies ≠ liability cap; liability comes from CUAD).
# - 85 Survival → other (post-termination survival is not termination itself).
# - 89 Terms → other (duration term ≠ renewal; renewal comes from CUAD).
# - 65 Notices, 38 Entire Agreements, 26 Counterparts, 97 Waivers → other
#   (boilerplate with no taxonomy home).
LEDGAR_OTHER_IDS: tuple[int, ...] = tuple(
    i for i in range(100) if i not in LEDGAR_TO_TAXONOMY
)

LEDGAR_LABEL_NAMES: dict[int, str] = {
    0: "Adjustments", 1: "Agreements", 2: "Amendments", 3: "Anti-Corruption Laws",
    4: "Applicable Laws", 5: "Approvals", 6: "Arbitration", 7: "Assignments",
    8: "Assigns", 9: "Authority", 10: "Authorizations", 11: "Base Salary",
    12: "Benefits", 13: "Binding Effects", 14: "Books", 15: "Brokers",
    16: "Capitalization", 17: "Change In Control", 18: "Closings",
    19: "Compliance With Laws", 20: "Confidentiality", 21: "Consent To Jurisdiction",
    22: "Consents", 23: "Construction", 24: "Cooperation", 25: "Costs",
    26: "Counterparts", 27: "Death", 28: "Defined Terms", 29: "Definitions",
    30: "Disability", 31: "Disclosures", 32: "Duties", 33: "Effective Dates",
    34: "Effectiveness", 35: "Employment", 36: "Enforceability", 37: "Enforcements",
    38: "Entire Agreements", 39: "Erisa", 40: "Existence", 41: "Expenses",
    42: "Fees", 43: "Financial Statements", 44: "Forfeitures", 45: "Further Assurances",
    46: "General", 47: "Governing Laws", 48: "Headings", 49: "Indemnifications",
    50: "Indemnity", 51: "Insurances", 52: "Integration", 53: "Intellectual Property",
    54: "Interests", 55: "Interpretations", 56: "Jurisdictions", 57: "Liens",
    58: "Litigations", 59: "Miscellaneous", 60: "Modifications", 61: "No Conflicts",
    62: "No Defaults", 63: "No Waivers", 64: "Non-Disparagement", 65: "Notices",
    66: "Organizations", 67: "Participations", 68: "Payments", 69: "Positions",
    70: "Powers", 71: "Publicity", 72: "Qualifications", 73: "Records",
    74: "Remedies", 75: "Releases", 76: "Representations", 77: "Sales",
    78: "Sanctions", 79: "Severability", 80: "Solvency", 81: "Specific Performance",
    82: "Submission To Jurisdiction", 83: "Subsidiaries", 84: "Successors",
    85: "Survival", 86: "Tax Withholdings", 87: "Taxes", 88: "Terminations",
    89: "Terms", 90: "Titles", 91: "Transactions With Affiliates",
    92: "Use Of Proceeds", 93: "Vacations", 94: "Venues", 95: "Vesting",
    96: "Waiver Of Jury Trials", 97: "Waivers", 98: "Warranties", 99: "Withholdings",
}

# ------------------------------------------------------------------
# CUAD (answer span category) → taxonomy. Span → clause derivation lives
# in dataset.py; metadata-only categories are EXCLUDED from training.
# ------------------------------------------------------------------
def _norm(name: str) -> str:
    """Normalize CUAD category spellings ("IP"/"Ip", hyphens, commas)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", name.lower())).strip()


def extract_cuad_category(question: str) -> str:
    """Extract the normalized category from a CUAD question string.

    CUAD v1 uses fixed templates: Highlight ... related to "CATEGORY" ...
    Raises ValueError on unknown categories (fail loud, never silent).
    """
    m = re.search(r'related to "([^"]+)"', question)
    if not m:
        raise ValueError(f"Cannot extract CUAD category from question: {question[:100]!r}")
    cat = _norm(m.group(1))
    if cat in CUAD_TO_TAXONOMY or cat in CUAD_EXCLUDED:
        return cat
    raise ValueError(f"Unknown CUAD category {cat!r} — add explicit mapping, never force.")


# Keys are normalized category names as extracted from CUAD question strings:
#   Highlight ... related to "CATEGORY" ...
# (normalization absorbs "IP"/"Ip", hyphens, commas, slashes)
CUAD_TO_TAXONOMY: dict[str, str] = {
    # liability — the flagship gap LEDGAR cannot fill
    "cap on liability": "liability",
    "uncapped liability": "liability",
    "liquidated damages": "liability",
    # renewal
    "renewal term": "renewal",
    "notice period to terminate renewal": "renewal",
    # audit (direct source)
    "audit rights": "audit",
    # intellectual_property
    "ip ownership assignment": "intellectual_property",
    "joint ip ownership": "intellectual_property",
    "license grant": "intellectual_property",
    "non transferable license": "intellectual_property",
    "affiliate license licensor": "intellectual_property",
    "affiliate license licensee": "intellectual_property",
    "irrevocable or perpetual license": "intellectual_property",
    "unlimited all you can eat license": "intellectual_property",
    "source code escrow": "intellectual_property",
    # governing_law
    "governing law": "governing_law",
    # termination
    "termination for convenience": "termination",
    "post termination services": "termination",
    # insurance
    "insurance": "insurance",
    # warranty
    "warranty duration": "warranty",
    # payment (commercial-payment-adjacent; documented as approximate)
    "price restrictions": "payment",
    "minimum commitment": "payment",
    "revenue profit sharing": "payment",
    # other — real restrictive-covenant/structural clauses, kept as hard negatives
    "non compete": "other",
    "no solicit of employees": "other",
    "no solicit of customers": "other",
    "exclusivity": "other",
    "competitive restriction exception": "other",
    "competition restriction exception": "other",
    "change of control": "other",
    "anti assignment": "other",
    "non disparagement": "other",
    "rofr rofo rofn": "other",
    "most favored nation": "other",
    "covenant not to sue": "other",
    "third party beneficiary": "other",
}

# CUAD categories never used for training: contract metadata / dates are not
# clauses, and these spans would teach the model the wrong task.
CUAD_EXCLUDED: tuple[str, ...] = (
    "document name",
    "parties",
    "agreement date",
    "effective date",
    "expiration date",
    "volume restriction",
)


def validate_mapping() -> list[str]:
    """Return human-readable mapping problems (empty = clean). Used by tests."""
    problems = []
    for lid, cat in LEDGAR_TO_TAXONOMY.items():
        if cat not in MODEL_LABELS:
            problems.append(f"LEDGAR {lid} maps outside model labels: {cat}")
    for name, cat in CUAD_TO_TAXONOMY.items():
        if cat not in MODEL_LABELS:
            problems.append(f"CUAD {name} maps outside model labels: {cat}")
    overlap = set(LEDGAR_TO_TAXONOMY) & set(LEDGAR_OTHER_IDS)
    if overlap:
        problems.append(f"LEDGAR ids both mapped and other: {overlap}")
    if set(LEDGAR_TO_TAXONOMY) | set(LEDGAR_OTHER_IDS) != set(range(100)):
        problems.append("LEDGAR 0-99 not fully partitioned")
    return problems
