from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CandidateSample:
    filename: str
    class_id: int
    status: str
    min_confidence: float | None
    box_count: int
    diversity_hash: int
    selection_reason: str = ""


def _is_risk(sample: CandidateSample) -> bool:
    return sample.status == "no_box" or "class_mismatch" in sample.status


def _confidence_key(sample: CandidateSample) -> tuple[float, str]:
    return (sample.min_confidence if sample.min_confidence is not None else -1.0, sample.filename)


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _farthest_first(
    candidates: list[CandidateSample],
    selected: list[CandidateSample],
    count: int,
    rng: Random,
) -> list[CandidateSample]:
    pool = sorted(candidates, key=lambda item: item.filename)
    result: list[CandidateSample] = []
    anchors = [item.diversity_hash for item in selected]
    while pool and len(result) < count:
        if not anchors:
            index = rng.randrange(len(pool))
            choice = pool.pop(index)
        else:
            choice = max(
                pool,
                key=lambda item: (
                    min(_hamming(item.diversity_hash, anchor) for anchor in anchors),
                    item.filename,
                ),
            )
            pool.remove(choice)
        result.append(choice)
        anchors.append(choice.diversity_hash)
    return result


def select_balanced_round(
    candidates: Iterable[CandidateSample],
    *,
    per_class: int,
    risk_quota: int,
    uncertainty_quota: int,
    seed: int,
) -> list[CandidateSample]:
    if per_class <= 0 or risk_quota < 0 or uncertainty_quota < 0:
        raise ValueError("抽样配额必须是有效的非负数")
    if risk_quota + uncertainty_quota > per_class:
        raise ValueError("风险与不确定性配额不能超过每类总数")
    grouped: dict[int, list[CandidateSample]] = {}
    for item in candidates:
        grouped.setdefault(item.class_id, []).append(item)
    if not grouped:
        return []

    output: list[CandidateSample] = []
    for class_id in sorted(grouped):
        group = sorted(grouped[class_id], key=lambda item: item.filename)
        if len(group) < per_class:
            raise ValueError(f"类别{class_id}只有{len(group)}张，无法选择{per_class}张")
        rng = Random(f"{seed}:{class_id}")
        risk_pool = [item for item in group if _is_risk(item)]
        risk = _farthest_first(risk_pool, [], min(risk_quota, len(risk_pool)), rng)
        chosen_names = {item.filename for item in risk}

        remaining = [item for item in group if item.filename not in chosen_names]
        uncertainty_target = uncertainty_quota + (risk_quota - len(risk))
        uncertainty = sorted(remaining, key=_confidence_key)[:uncertainty_target]
        chosen_names.update(item.filename for item in uncertainty)

        remaining = [item for item in group if item.filename not in chosen_names]
        diversity_target = per_class - len(risk) - len(uncertainty)
        diversity = _farthest_first(remaining, risk + uncertainty, diversity_target, rng)

        output.extend(replace(item, selection_reason="risk") for item in risk)
        output.extend(replace(item, selection_reason="uncertainty") for item in uncertainty)
        output.extend(replace(item, selection_reason="diversity") for item in diversity)
    return output

