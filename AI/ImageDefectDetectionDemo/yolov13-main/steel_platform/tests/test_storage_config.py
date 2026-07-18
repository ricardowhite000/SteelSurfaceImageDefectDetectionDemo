from __future__ import annotations

from pathlib import Path

from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import load_settings


def test_local_artifact_store_is_content_addressed_and_verifiable(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")

    first = store.put_bytes(b"same content", media_type="text/plain")
    second = store.put_bytes(b"same content", media_type="text/plain")

    assert first.sha256 == second.sha256
    assert first.storage_key == second.storage_key
    assert store.verify(first)
    assert store.resolve(first).read_bytes() == b"same content"


def test_config_paths_are_resolved_relative_to_yaml(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = config_dir / "platform.yaml"
    config.write_text(
        """
project_name: demo
database_url: sqlite:///workspace/platform.db
artifact_root: workspace/artifacts
source_images: ../images
candidate_labels: ../labels
review_csv: ../labels/pseudo_review.csv
seed_manifest: ../seed_manifest.csv
seed_dataset: ../dataset
classes: [Cr, In, Pa, PS, RS, Sc]
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.artifact_root == (config_dir / "workspace/artifacts").resolve()
    assert settings.source_images == (config_dir / "../images").resolve()
    assert settings.database_path == (config_dir / "workspace/platform.db").resolve()


def test_deployment_environment_can_override_host_and_device(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "platform.yaml"
    config.write_text(
        """
project_name: demo
database_url: sqlite:///platform.db
artifact_root: artifacts
source_images: images
candidate_labels: labels
review_csv: review.csv
seed_manifest: seed.csv
seed_dataset: dataset
classes: [Cr, In, Pa, PS, RS, Sc]
""".strip(), encoding="utf-8"
    )
    monkeypatch.setenv("STEEL_PLATFORM_HOST", "127.0.0.2")
    monkeypatch.setenv("STEEL_PLATFORM_DEVICE", "cpu")
    settings = load_settings(config)
    assert settings.host == "127.0.0.2"
    assert settings.device == "cpu"
