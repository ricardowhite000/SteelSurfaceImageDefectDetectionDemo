from __future__ import annotations

from pathlib import Path

from steel_platform.application.delivery_package import build_demo_package, install_delivery_package
from steel_platform.application.doctor import build_doctor_report
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import upgrade_database
from test_delivery_package import CLASSES, _seed_dataset


def _doctor_settings(tmp_path: Path):
    config = tmp_path / "platform.yaml"
    config.write_text(
        f'workspace_root: "{(tmp_path / "workspace").as_posix()}"\n'
        "project_name: steel-doctor-demo\n"
        "classes: [Cr, In, Pa, PS, RS, Sc]\n"
        "device: cpu\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    upgrade_database(settings.database_url)
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    return settings


def test_strict_doctor_requires_demo_project_and_available_runtime(tmp_path: Path) -> None:
    settings = _doctor_settings(tmp_path)
    system = {"windows": True, "powershell": True, "conda": True, "python": True}

    report = build_doctor_report(
        settings,
        strict=True,
        system_checks=system,
        runtime_reports=[],
        port_available=True,
    )

    assert report["ready"] is False
    assert "demo_package" in report["failed_checks"]
    assert "runtime_profiles" in report["failed_checks"]


def test_strict_doctor_accepts_installed_demo_and_cpu_runtime(tmp_path: Path) -> None:
    settings = _doctor_settings(tmp_path)
    dataset = _seed_dataset(tmp_path / "dataset")
    base = tmp_path / "base.pt"
    detector = tmp_path / "best.pt"
    base.write_bytes(b"base")
    detector.write_bytes(b"detector")
    package = tmp_path / "steel-platform-demo-1.0.0.zip"
    build_demo_package(
        dataset_root=dataset,
        base_weights=base,
        detector_weights=detector,
        output=package,
        classes=CLASSES,
    )
    install_delivery_package(settings, package)

    report = build_doctor_report(
        settings,
        strict=True,
        system_checks={"windows": True, "powershell": True, "conda": True, "python": True},
        runtime_reports=[
            {
                "available": True,
                "profile": {"name": "CPU", "backend": "cpu", "devices": ["cpu"]},
                "checks": {"python": True, "project_root": True, "torch": True, "yolo": True, "platform": True, "cuda": False},
            }
        ],
        port_available=True,
    )

    assert report["ready"] is True
    assert report["failed_checks"] == []
    assert report["demo_package"]["images"] == 60
    assert report["demo_package"]["annotations"] == 60
    assert report["demo_package"]["models"] == 2
