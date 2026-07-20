from __future__ import annotations

from dataclasses import dataclass
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any
import zipfile

from PIL import Image
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    ClassSchemaModel,
    CollectionMemberModel,
    CollectionModel,
    DatasetMemberModel,
    DatasetVersionModel,
    IdempotencyRecordModel,
    ModelVersionModel,
    ProjectModel,
    SourceRootModel,
)
from steel_platform.infrastructure.yolo import parse_yolo_text


PACKAGE_ID = "steel-platform-demo"
PACKAGE_VERSION = "1.0.0"
MAX_FILES = 256
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


class DeliveryPackageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedDeliveryPackage:
    path: Path
    sha256: str
    manifest: dict[str, Any]


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _safe_name(name: str) -> PurePosixPath:
    if "\\" in name:
        raise DeliveryPackageError("unsafe_package_path", f"包内路径不能使用反斜杠：{name}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise DeliveryPackageError("unsafe_package_path", f"包内路径不安全：{name}")
    return path


def _zip_write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content)


def _media_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".bmp": "image/bmp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".txt": "text/yolo",
        ".yaml": "application/yaml",
        ".csv": "text/csv",
        ".pt": "application/x-pytorch",
    }.get(suffix, "application/octet-stream")


def build_demo_package(
    *,
    dataset_root: Path,
    base_weights: Path,
    detector_weights: Path,
    output: Path,
    classes: tuple[str, ...],
) -> VerifiedDeliveryPackage:
    root = dataset_root.resolve()
    if not classes or len(set(classes)) != len(classes):
        raise DeliveryPackageError("invalid_class_schema", "类别不能为空或重复")
    if not base_weights.is_file() or not detector_weights.is_file():
        raise DeliveryPackageError("missing_model", "基础权重或检测器权重不存在")
    members: list[dict[str, str]] = []
    contents: dict[str, bytes] = {}
    class_counts: dict[tuple[str, str], int] = {}
    for split in ("train", "val"):
        image_dir = root / "images" / split
        label_dir = root / "labels" / split
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise DeliveryPackageError("invalid_dataset", f"数据集缺少{split}图片或标签目录")
        images = sorted(
            (item for item in image_dir.iterdir() if item.is_file() and item.suffix.lower() in {".bmp", ".jpg", ".jpeg", ".png"}),
            key=lambda item: item.name,
        )
        for image in images:
            label = label_dir / f"{image.stem}.txt"
            if not label.is_file():
                raise DeliveryPackageError("missing_label", f"图片缺少标签：{image.name}")
            prefix = image.stem.split("_", 1)[0]
            if prefix not in classes:
                raise DeliveryPackageError("unknown_class_prefix", f"文件名前缀不属于类别模式：{image.name}")
            class_id = classes.index(prefix)
            boxes = parse_yolo_text(label.read_text(encoding="utf-8-sig"), source=label)
            if not boxes or any(box.class_id != class_id for box in boxes):
                raise DeliveryPackageError("label_class_mismatch", f"标签类别与文件名前缀不一致：{label.name}")
            image_key = f"dataset/images/{split}/{image.name}"
            label_key = f"dataset/labels/{split}/{label.name}"
            contents[image_key] = image.read_bytes()
            contents[label_key] = label.read_bytes()
            members.append({"image": image_key, "label": label_key, "split": split, "class_name": prefix})
            class_counts[(prefix, split)] = class_counts.get((prefix, split), 0) + 1
    expected = {(name, "train"): 8 for name in classes} | {(name, "val"): 2 for name in classes}
    if class_counts != expected:
        raise DeliveryPackageError("invalid_split", f"Demo数据必须每类8张训练、2张验证：{class_counts}")

    data_yaml = yaml.safe_dump(
        {"path": ".", "train": "images/train", "val": "images/val", "names": {index: name for index, name in enumerate(classes)}},
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    split_stream = StringIO(newline="")
    writer = csv.DictWriter(split_stream, fieldnames=["filename", "class", "split"])
    writer.writeheader()
    for member in members:
        writer.writerow({"filename": PurePosixPath(member["image"]).name, "class": member["class_name"], "split": member["split"]})
    contents["dataset/data.yaml"] = data_yaml
    contents["dataset/split_manifest.csv"] = ("\ufeff" + split_stream.getvalue()).encode("utf-8")
    contents["models/yolov13n.pt"] = base_weights.read_bytes()
    contents["models/best.pt"] = detector_weights.read_bytes()

    file_entries = [
        {
            "path": name,
            "sha256": _digest(content),
            "size_bytes": len(content),
            "media_type": _media_type(name),
        }
        for name, content in sorted(contents.items())
    ]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "project": {
            "name": "steel-surface-defect-demo",
            "schema_version": "steel-defects-v1",
            "classes": list(classes),
            "annotation_policy": {"mode": "single_class_locked", "allow_empty_labels": False, "class_inference": "filename_prefix"},
        },
        "dataset": {"name": "steel-seed-demo-v1", "members": members},
        "models": [
            {"name": "YOLOv13n基础权重", "path": "models/yolov13n.pt", "purpose": "base_weight", "class_names": None},
            {"name": "钢材六类种子检测器", "path": "models/best.pt", "purpose": "detector", "class_names": list(classes)},
        ],
        "files": file_entries,
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            _zip_write(archive, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            for name, content in sorted(contents.items()):
                _zip_write(archive, name, content)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    package_sha = _digest(output.read_bytes())
    output.with_suffix(output.suffix + ".sha256").write_text(f"{package_sha}  {output.name}\n", encoding="ascii")
    return verify_delivery_package(output)


def verify_delivery_package(path: Path) -> VerifiedDeliveryPackage:
    package = path.resolve()
    if not package.is_file():
        raise DeliveryPackageError("package_missing", f"Demo包不存在：{package}")
    package_sha = _digest(package.read_bytes())
    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise DeliveryPackageError("package_too_large", "Demo包文件数量或解压大小超出限制")
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise DeliveryPackageError("duplicate_package_path", "Demo包包含重复路径")
            for name in names:
                _safe_name(name)
            if "manifest.json" not in names:
                raise DeliveryPackageError("manifest_missing", "Demo包缺少manifest.json")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            _validate_manifest(manifest, archive, set(names))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DeliveryPackageError("package_corrupt", f"Demo包无法解析：{exc}") from exc
    return VerifiedDeliveryPackage(package, package_sha, manifest)


def _validate_manifest(manifest: dict[str, Any], archive: zipfile.ZipFile, names: set[str]) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("package_id") != PACKAGE_ID:
        raise DeliveryPackageError("unsupported_package", "Demo包模式或标识不受支持")
    project = manifest.get("project") or {}
    classes = project.get("classes") or []
    if not classes or len(classes) != len(set(classes)):
        raise DeliveryPackageError("invalid_class_schema", "Demo包类别模式无效")
    files = manifest.get("files") or []
    paths = [str(item.get("path", "")) for item in files]
    if len(paths) != len(set(paths)):
        raise DeliveryPackageError("duplicate_manifest_path", "清单包含重复路径")
    expected_names = {"manifest.json", *paths}
    if names != expected_names:
        extras = sorted(names - expected_names)
        missing = sorted(expected_names - names)
        unsafe = next((name for name in extras if ".." in PurePosixPath(name).parts), None)
        if unsafe:
            raise DeliveryPackageError("unsafe_package_path", f"包内路径不安全：{unsafe}")
        raise DeliveryPackageError("manifest_file_mismatch", f"包内容与清单不一致；多余={extras}；缺失={missing}")
    by_path = {str(item["path"]): item for item in files}
    for name, item in by_path.items():
        _safe_name(name)
        content = archive.read(name)
        if len(content) != item.get("size_bytes") or _digest(content) != item.get("sha256"):
            raise DeliveryPackageError("artifact_hash_mismatch", f"文件哈希不一致：{name}")
    members = (manifest.get("dataset") or {}).get("members") or []
    if len(members) != 60 or sum(item.get("split") == "train" for item in members) != 48 or sum(item.get("split") == "val" for item in members) != 12:
        raise DeliveryPackageError("invalid_split", "Demo包必须包含48张训练图和12张验证图")
    class_counts: dict[tuple[str, str], int] = {}
    for member in members:
        image_path = str(member.get("image", ""))
        label_path = str(member.get("label", ""))
        split = str(member.get("split", ""))
        class_name = str(member.get("class_name", ""))
        if image_path not in by_path or label_path not in by_path or split not in {"train", "val"} or class_name not in classes:
            raise DeliveryPackageError("invalid_dataset_member", f"数据集成员无效：{member}")
        try:
            with Image.open(BytesIO(archive.read(image_path))) as image:
                image.verify()
        except Exception as exc:
            raise DeliveryPackageError("invalid_image", f"图片无法解析：{image_path}") from exc
        text = archive.read(label_path).decode("utf-8-sig")
        boxes = parse_yolo_text(text, source=Path(label_path))
        class_id = classes.index(class_name)
        if not boxes or any(box.class_id != class_id for box in boxes):
            raise DeliveryPackageError("label_class_mismatch", f"标签类别不一致：{label_path}")
        class_counts[(class_name, split)] = class_counts.get((class_name, split), 0) + 1
    expected = {(name, "train"): 8 for name in classes} | {(name, "val"): 2 for name in classes}
    if class_counts != expected:
        raise DeliveryPackageError("invalid_split", "Demo包逐类划分不是8张训练、2张验证")
    models = manifest.get("models") or []
    if {item.get("purpose") for item in models} != {"base_weight", "detector"} or any(item.get("path") not in by_path for item in models):
        raise DeliveryPackageError("invalid_models", "Demo包必须包含基础权重和检测器")


def install_delivery_package(settings: PlatformSettings, path: Path) -> dict[str, Any]:
    verified = verify_delivery_package(path)
    manifest = verified.manifest
    package_key = f"package:{manifest['package_id']}:{verified.sha256[:32]}"
    engine = make_engine(settings.database_url)
    store = LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        prior = session.get(IdempotencyRecordModel, package_key)
        if prior is not None:
            return dict(prior.response_json)
    with zipfile.ZipFile(verified.path) as archive:
        with Session(engine) as session, session.begin():
            prior = session.get(IdempotencyRecordModel, package_key)
            if prior is not None:
                return dict(prior.response_json)
            project_spec = manifest["project"]
            conflict = session.scalar(select(ProjectModel).where(ProjectModel.name == project_spec["name"]))
            if conflict is not None:
                raise DeliveryPackageError("project_name_conflict", "同名项目已存在，但不是同一个Demo包")
            project = ProjectModel(
                name=project_spec["name"],
                schema_version=project_spec["schema_version"],
                annotation_policy_json=project_spec["annotation_policy"],
            )
            session.add(project)
            session.flush()
            schema = ClassSchemaModel(project_id=project.id, name="steel-defects", version=1, names_json=tuple(project_spec["classes"]))
            session.add(schema)
            session.flush()
            project.class_schema_id = schema.id
            source = SourceRootModel(
                project_id=project.id,
                name="标准Demo包",
                kind="demo_package",
                mode="managed",
                status="available",
                path=f"package://{manifest['package_id']}/{manifest['package_version']}",
                read_only=True,
                manifest_sha256=verified.sha256,
            )
            collection = CollectionModel(project_id=project.id, name="60张种子数据")
            session.add_all([source, collection])
            session.flush()

            member_rows: list[tuple[AssetModel, AnnotationRevisionModel, str, str, str]] = []
            for member in manifest["dataset"]["members"]:
                image_content = archive.read(member["image"])
                label_content = archive.read(member["label"])
                image_ref = store.put_bytes(image_content, media_type=_media_type(member["image"]))
                image = AssetModel(
                    project_id=project.id,
                    source_root_id=source.id,
                    kind="image",
                    relative_path=member["image"],
                    storage_key=image_ref.storage_key,
                    sha256=image_ref.sha256,
                    size_bytes=image_ref.size_bytes,
                    media_type=image_ref.media_type,
                )
                session.add(image)
                session.flush()
                label_ref = store.put_bytes(label_content, media_type="text/yolo")
                boxes = parse_yolo_text(label_content.decode("utf-8-sig"), source=Path(member["label"]))
                revision = AnnotationRevisionModel(
                    project_id=project.id,
                    image_asset_id=image.id,
                    origin="import",
                    decision="package_verified",
                    storage_key=label_ref.storage_key,
                    sha256=label_ref.sha256,
                    box_count=len(boxes),
                    created_by="delivery-package",
                )
                session.add_all([revision, CollectionMemberModel(collection_id=collection.id, asset_id=image.id)])
                session.flush()
                member_rows.append((image, revision, member["split"], member["image"], member["label"]))

            dataset_manifest = {
                "schema_version": 1,
                "package_sha256": verified.sha256,
                "classes": project_spec["classes"],
                "members": [
                    {"image_asset_id": image.id, "annotation_revision_id": revision.id, "split": split, "image": image_path, "label": label_path}
                    for image, revision, split, image_path, label_path in member_rows
                ],
            }
            dataset_manifest_ref = store.put_bytes(
                json.dumps(dataset_manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
                media_type="application/json",
            )
            dataset = DatasetVersionModel(
                project_id=project.id,
                name=manifest["dataset"]["name"],
                schema_version=project_spec["schema_version"],
                manifest_key=dataset_manifest_ref.storage_key,
                sha256=dataset_manifest_ref.sha256,
            )
            session.add(dataset)
            session.flush()
            for image, revision, split, _, _ in member_rows:
                session.add(DatasetMemberModel(dataset_version_id=dataset.id, image_asset_id=image.id, annotation_revision_id=revision.id, split=split))
            _materialize_dataset(settings, dataset.id, archive, manifest)

            model_ids: list[str] = []
            for model_spec in manifest["models"]:
                content = archive.read(model_spec["path"])
                ref = store.put_bytes(content, media_type="application/x-pytorch")
                source_asset = AssetModel(
                    project_id=project.id,
                    source_root_id=source.id,
                    kind="model",
                    relative_path=model_spec["path"],
                    storage_key=ref.storage_key,
                    sha256=ref.sha256,
                    size_bytes=ref.size_bytes,
                    media_type=ref.media_type,
                )
                session.add(source_asset)
                session.flush()
                model_manifest_ref = store.put_bytes(
                    json.dumps({**model_spec, "sha256": ref.sha256, "package_sha256": verified.sha256}, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    media_type="application/json",
                )
                model = ModelVersionModel(
                    project_id=project.id,
                    source_asset_id=source_asset.id,
                    name=model_spec["name"],
                    format="pt",
                    purpose=model_spec["purpose"],
                    verification_status="ready",
                    evaluation_status="not_evaluated",
                    class_schema_json=model_spec["class_names"],
                    weights_sha256=ref.sha256,
                    source_note=f"标准Demo包 {manifest['package_version']}",
                    weights_key=ref.storage_key,
                    manifest_key=model_manifest_ref.storage_key,
                )
                session.add(model)
                session.flush()
                model_ids.append(model.id)
            response = {"project_id": project.id, "dataset_id": dataset.id, "model_ids": model_ids, "package_sha256": verified.sha256}
            session.add(IdempotencyRecordModel(key=package_key, scope=f"package-install:{verified.sha256}", response_json=response))
        return response


def _materialize_dataset(settings: PlatformSettings, dataset_id: str, archive: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    parent = settings.artifact_root / "materialized" / "datasets"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / dataset_id
    temporary = Path(tempfile.mkdtemp(prefix=f".{dataset_id}-", dir=parent))
    try:
        for member in manifest["dataset"]["members"]:
            image_target = temporary / "images" / member["split"] / PurePosixPath(member["image"]).name
            label_target = temporary / "labels" / member["split"] / PurePosixPath(member["label"]).name
            image_target.parent.mkdir(parents=True, exist_ok=True)
            label_target.parent.mkdir(parents=True, exist_ok=True)
            image_target.write_bytes(archive.read(member["image"]))
            label_target.write_bytes(archive.read(member["label"]))
        data = {
            "path": target.as_posix(),
            "train": "images/train",
            "val": "images/val",
            "names": {index: name for index, name in enumerate(manifest["project"]["classes"])},
        }
        (temporary / "data.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if target.exists():
            raise DeliveryPackageError("dataset_materialization_exists", f"数据集物化目录已存在：{target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
