from __future__ import annotations

from collections import Counter

from steel_platform.application.sampling import CandidateSample, select_balanced_round


def _candidates() -> list[CandidateSample]:
    rows: list[CandidateSample] = []
    statuses = ("no_box", "class_mismatch;low_confidence", "low_confidence", "review")
    for class_id in range(6):
        for index in range(40):
            rows.append(
                CandidateSample(
                    filename=f"C{class_id}_{index:03d}.bmp",
                    class_id=class_id,
                    status=statuses[index % len(statuses)],
                    min_confidence=None if index % 4 == 0 else 0.2 + index / 100,
                    box_count=index % 9,
                    diversity_hash=(class_id << 56) | (index * 7919),
                )
            )
    return rows


def test_balanced_round_is_reproducible_and_balanced() -> None:
    first = select_balanced_round(_candidates(), per_class=30, risk_quota=18, uncertainty_quota=6, seed=42)
    second = select_balanced_round(list(reversed(_candidates())), per_class=30, risk_quota=18, uncertainty_quota=6, seed=42)

    assert [item.filename for item in first] == [item.filename for item in second]
    assert Counter(item.class_id for item in first) == Counter({class_id: 30 for class_id in range(6)})
    assert Counter(item.selection_reason for item in first) == Counter(
        {"risk": 18 * 6, "uncertainty": 6 * 6, "diversity": 6 * 6}
    )


def test_balanced_round_fills_missing_risk_slots_without_duplicates() -> None:
    rows = [
        CandidateSample(
            filename=row.filename,
            class_id=row.class_id,
            status="low_confidence" if row.class_id == 4 else row.status,
            min_confidence=row.min_confidence,
            box_count=row.box_count,
            diversity_hash=row.diversity_hash,
        )
        for row in _candidates()
    ]

    selected = select_balanced_round(rows, per_class=30, risk_quota=18, uncertainty_quota=6, seed=42)
    class_four = [item for item in selected if item.class_id == 4]

    assert len(class_four) == 30
    assert len({item.filename for item in class_four}) == 30
