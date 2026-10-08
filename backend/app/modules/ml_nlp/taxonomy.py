"""Application clause taxonomy (spec §7).

15 categories. 14 are trainable in v1; DATA_PRIVACY is RESERVED — neither
LEDGAR nor CUAD provides clean training examples for it, and a zero-example
softmax class would be dishonest. It stays in the taxonomy so later phases
(policies, risk, new data) can adopt it without renaming anything.
"""
CATEGORIES = [
    "liability",
    "indemnification",
    "termination",
    "payment",
    "confidentiality",
    "intellectual_property",
    "data_privacy",
    "renewal",
    "governing_law",
    "jurisdiction",
    "warranty",
    "insurance",
    "audit",
    "dispute_resolution",
    "other",
]

# Classes the v1 model actually predicts (everything except reserved ones).
RESERVED_CATEGORIES = ("data_privacy",)
MODEL_LABELS = [c for c in CATEGORIES if c not in RESERVED_CATEGORIES]
