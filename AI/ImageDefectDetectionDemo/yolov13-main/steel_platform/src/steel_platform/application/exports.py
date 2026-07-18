from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy.orm import Session

from steel_platform.application.errors import NotFoundError
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import ReviewItemModel, ReviewRoundModel


def export_review_progress(settings: PlatformSettings, *, round_number: int, output: Path) -> int:
    with Session(make_engine(settings.database_url)) as session:
        review_round = session.scalar(
            select(ReviewRoundModel).where(ReviewRoundModel.number == round_number, ReviewRoundModel.kind == "training")
        )
        if review_round is None:
            raise NotFoundError(f"复核轮次 {round_number} 不存在")
        items = session.scalars(
            select(ReviewItemModel).where(ReviewItemModel.round_id == review_round.id).order_by(ReviewItemModel.rank)
        ).all()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        fields = ["item_id", "filename", "class_id", "split", "selection_reason", "source_status", "state", "revision", "note"]
        with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
            for item in items:
                writer.writerow({"item_id":item.id,"filename":item.filename,"class_id":item.expected_class_id,"split":item.split_role,"selection_reason":item.selection_reason,"source_status":item.source_status,"state":item.state,"revision":item.revision,"note":item.note})
        temporary.replace(output)
        return len(items)


def review_round_summary(settings: PlatformSettings, *, round_number: int) -> dict[str, object]:
    with Session(make_engine(settings.database_url)) as session:
        review_round = session.scalar(select(ReviewRoundModel).where(ReviewRoundModel.number == round_number, ReviewRoundModel.kind == "training"))
        if review_round is None:
            raise NotFoundError(f"复核轮次 {round_number} 不存在")
        class_counts = dict(session.execute(select(ReviewItemModel.expected_class_id, func.count()).where(ReviewItemModel.round_id == review_round.id).group_by(ReviewItemModel.expected_class_id)).all())
        split_counts = dict(session.execute(select(ReviewItemModel.split_role, func.count()).where(ReviewItemModel.round_id == review_round.id).group_by(ReviewItemModel.split_role)).all())
        return {"total":sum(class_counts.values()),"classes":class_counts,"splits":split_counts}
