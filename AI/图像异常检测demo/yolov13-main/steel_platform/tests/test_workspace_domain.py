from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

from steel_platform.domain.ports import ReviewTaskRepository
from steel_platform.domain.workspace import ClassSchema, Collection, normalize_relative_path


def test_class_schema_is_ordered_and_immutable() -> None:
    schema = ClassSchema("schema-1", "steel-defects-v1", ("Cr", "In", "Pa", "PS", "RS", "Sc"))
    assert schema.class_name(3) == "PS"
    with pytest.raises(FrozenInstanceError):
        schema.names = ("other",)


@pytest.mark.parametrize("value", ["../secret.bmp", "/absolute.bmp", "C:/absolute.bmp", "a/../../b.bmp", ""])
def test_relative_path_rejects_escape(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path(value)


def test_collection_cannot_parent_itself() -> None:
    with pytest.raises(ValueError):
        Collection(id="c1", project_id="p1", name="bad", parent_id="c1", revision=0)


def test_review_task_port_type_hints_resolve() -> None:
    assert "return" in get_type_hints(ReviewTaskRepository.get_round)
