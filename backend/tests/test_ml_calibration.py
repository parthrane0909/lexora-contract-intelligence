"""Calibration unit tests (pure, hand-computed)."""
from app.modules.ml_evaluation import calibration as calib


def test_calibration_perfect_vs_miscalibrated():
    perfect = calib.reliability([0.9] * 4, [True] * 4, n_bins=10)
    assert perfect["ece"] == 0.1  # |1.0 - 0.9| in one bin
    bad = calib.reliability([0.9] * 4, [False] * 4, n_bins=10)
    assert bad["ece"] == 0.9
    stats = calib.confidence_stats([0.9, 0.9, 0.4, 0.4], [True, True, False, False])
    assert stats["mean_correct"] == 0.9 and stats["mean_incorrect"] == 0.4
    assert sum(stats["histogram_10"]) == 4
    empty = calib.reliability([], [], n_bins=5)
    assert empty["ece"] == 0.0 and len(empty["bins"]) == 5


def test_reliability_bin_boundaries():
    out = calib.reliability([0.0, 0.5, 1.0], [False, True, True], n_bins=2)
    assert len(out["bins"]) == 2
    assert out["bins"][0]["n"] == 1  # 0.0 lands in bin 0
    assert out["bins"][1]["n"] == 2  # 0.5 and clamped 1.0 land in bin 1
