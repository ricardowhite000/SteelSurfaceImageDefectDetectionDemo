from __future__ import annotations

import csv
from pathlib import Path
import zipfile

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steel_platform.application.delivery_package import (
    DeliveryPackageError,
    build_demo_package,
    install_delivery_package,
    verify_delivery_package,
)
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    DatasetMemberModel,
    DatasetVersionModel,
    ModelVersionModel,
    ProjectModel,
)


CLASSES = ("Cr", "In", "Pa", "PS", "RS", "Sc")


def _seed_dataset(root: Path) -> Path:
    rows: list[dict[str, object]] = []
    for class_id, class_name in enumerate(CLASSES):
        for index in range(10):
            split = "train" if index < 8 else "val"
            stem = f"{class_name}_{index + 1}"
            image_path = root / "images" / split / f"{stem}.bmp"
            label_path = root / "labels" / split / f"{stem}.txt"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (class_id * 20, index * 10, 80)).save(image_path)
            label_path.write_text(f"{class_id} 0.5 0.5 0.5 0.5\n", encoding="utf-8")
            rows.append({"filename": image_path.name, "class": class_name, "split": split})
    with (root / "split_manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["filename", "class", "split"])
        writer.writeheader()
        writer.writerows(rows)
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\n"
        "names: {0: Cr, 1: In, 2: Pa, 3: PS, 4: RS, 5: Sc}\n",
        encoding="utf-8",
    )
    return root


def _package(tmp_path: Path) -> Path:
    dataset = _seed_dataset(tmp_path / "seed dataset")
    base = tmp_path / "yolov13n.pt"
    detector = tmp_path / "best.pt"
    base.write_bytes(b"base-weight")
    detector.write_bytes(b"detector-weight")
    output = tmp_path / "steel-platform-demo-1.0.0.zip"
    build_demo_package(
        dataset_root=dataset,
        base_weights=base,
        detector_weights=detector,
        output=output,
        classes=CLASSES,
    )
    return output


def _settings(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f'workspace_root: "{(tmp_path / "workspace").as_posix()}"\n'
        "project_name: steel-portable-demo\n"
        "classes: [Cr, In, Pa, PS, RS, Sc]\n"
        "device: cpu\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    upgrade_database(settings.database_url)
    return settings


def test_demo_package_build_is_deterministic_and_strictly_verifiable(tmp_path: Path) -> None:
    package = _package(tmp_path)

    verified = verify_delivery_package(package)

    assert verified.manifest["schema_version"] == 1
    assert verified.manifest["package_id"] == "steel-platform-demo"
    assert verified.manifest["package_version"] == "1.0.0"
    assert verified.manifest["project"]["classes"] == list(CLASSES)
    assert len(verified.manifest["dataset"]["members"]) == 60
    assert sum(item["split"] == "train" for item in verified.manifest["dataset"]["members"]) == 48
    assert sum(item["split"] == "val" for item in verified.manifest["dataset"]["members"]) == 12
    assert package.with_suffix(package.suffix + ".sha256").read_text(encoding="ascii").strip().endswith(
        package.name
    )


def test_demo_package_rejects_path_traversal_before_install(tmp_path: Path) -> None:
    package = _package(tmp_path)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("../escape.txt", "blocked")

    try:
        verify_delivery_package(package)
    except DeliveryPackageError as exc:
        assert exc.code == "unsafe_package_path"
    else:
        raise AssertionError("path traversal should be rejected")


def test_demo_package_install_registers_managed_lineage_and_is_idempotent(tmp_path: Path) -> None:
    package = _package(tmp_path)
    settings = _settings(tmp_path)

    first = install_delivery_package(settings, package)
    second = install_delivery_package(settings, package)

    assert second == first
    with Session(make_engine(settings.database_url)) as session:
        assert session.scalar(select(func.count()).select_from(ProjectModel)) == 1
        assert session.scalar(select(func.count()).select_from(AssetModel).where(AssetModel.kind == "image")) == 60
        assert session.scalar(select(func.count()).select_from(AnnotationRevisionModel)) == 60
        assert session.scalar(select(func.count()).select_from(DatasetVersionModel)) == 1
        assert session.scalar(select(func.count()).select_from(DatasetMemberModel)) == 60
        assert session.scalar(select(func.count()).select_from(ModelVersionModel)) == 2
        models = session.scalars(select(ModelVersionModel).order_by(ModelVersionModel.purpose)).all()
        assert {model.purpose for model in models} == {"base_weight", "detector"}
        assert all(model.verification_status == "ready" for model in models)
    materialized = settings.artifact_root / "materialized" / "datasets" / first["dataset_id"]
    assert len(list((materialized / "images/train").glob("*.bmp"))) == 48
    assert len(list((materialized / "images/val").glob("*.bmp"))) == 12
    assert (materialized / "data.yaml").is_file()
