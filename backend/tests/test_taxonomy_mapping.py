"""Label-mapping audits: explicit coverage, no silent forcing."""
from app.modules.ml_nlp import mapping
from app.modules.ml_nlp.mapping import (
    CUAD_EXCLUDED,
    CUAD_TO_TAXONOMY,
    LEDGAR_OTHER_IDS,
    LEDGAR_TO_TAXONOMY,
    extract_cuad_category,
    validate_mapping,
)
from app.modules.ml_nlp.taxonomy import CATEGORIES, MODEL_LABELS


def test_mapping_validates_clean():
    assert validate_mapping() == []


def test_ledgar_partition_complete():
    assert len(LEDGAR_OTHER_IDS) + len(LEDGAR_TO_TAXONOMY) == 100
    assert set(LEDGAR_OTHER_IDS) | set(LEDGAR_TO_TAXONOMY) == set(range(100))


def test_ledgar_spot_checks():
    assert LEDGAR_TO_TAXONOMY[47] == "governing_law"
    assert LEDGAR_TO_TAXONOMY[49] == "indemnification"
    assert LEDGAR_TO_TAXONOMY[20] == "confidentiality"
    assert LEDGAR_TO_TAXONOMY[68] == "payment"
    assert LEDGAR_TO_TAXONOMY[88] == "termination"
    assert LEDGAR_TO_TAXONOMY[51] == "insurance"
    assert LEDGAR_TO_TAXONOMY[98] == "warranty"
    assert LEDGAR_TO_TAXONOMY[6] == "dispute_resolution"
    assert LEDGAR_TO_TAXONOMY[21] == "jurisdiction"
    # Judgment calls stay documented in "other", never forced:
    for lid in (74, 85, 89, 65, 38):  # Remedies, Survival, Terms, Notices, Entire Agreements
        assert lid in LEDGAR_OTHER_IDS


def test_cuad_spot_checks():
    assert CUAD_TO_TAXONOMY["cap on liability"] == "liability"
    assert CUAD_TO_TAXONOMY["renewal term"] == "renewal"
    assert CUAD_TO_TAXONOMY["audit rights"] == "audit"
    assert CUAD_TO_TAXONOMY["non compete"] == "other"
    assert "document name" in CUAD_EXCLUDED  # metadata excluded, not trained


def test_cuad_extraction_variants():
    q = 'Highlight the parts (if any) of this contract related to "Ip Ownership Assignment" that ...'
    assert extract_cuad_category(q) == "ip ownership assignment"
    q2 = 'Highlight the parts (if any) of this contract related to "Unlimited/All-You-Can-Eat-License" that ...'
    assert extract_cuad_category(q2) == "unlimited all you can eat license"
    q3 = 'Highlight the parts (if any) of this contract related to "Rofr/Rofo/Rofn" that ...'
    assert extract_cuad_category(q3) == "rofr rofo rofn"
    try:
        extract_cuad_category("No category here at all")
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        extract_cuad_category('Highlight related to "Something Brand New" ...')
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_all_targets_in_model_labels():
    for cat in list(LEDGAR_TO_TAXONOMY.values()) + list(CUAD_TO_TAXONOMY.values()):
        assert cat in MODEL_LABELS, cat
    assert "data_privacy" in CATEGORIES and "data_privacy" not in MODEL_LABELS
