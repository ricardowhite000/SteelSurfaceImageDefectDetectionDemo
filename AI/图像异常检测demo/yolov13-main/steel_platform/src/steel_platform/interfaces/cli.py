from __future__ import annotations

import csv
from pathlib import Path

import typer

from steel_platform.application.bootstrap import bootstrap_project, create_review_round
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import database_version, upgrade_database
from steel_platform.infrastructure.workspace_lock import WorkspaceLockedError, single_instance_lock
from steel_platform.infrastructure.logging import configure_logging


app = typer.Typer(help="钢材表面异常视觉系统统一命令行入口", no_args_is_help=True)
db_app = typer.Typer(help="数据库版本管理")
project_app = typer.Typer(help="项目初始化与检查")
review_app = typer.Typer(help="复核轮次和进度")
dataset_app = typer.Typer(help="不可变数据集版本")
jobs_app = typer.Typer(help="训练、评估与推理任务")
runs_app = typer.Typer(help="实验和推理结果导入")
inference_app = typer.Typer(help="流式推理任务")
backup_app = typer.Typer(help="一致性备份")
artifacts_app = typer.Typer(help="资产校验和清理预览")
for name, group in (
    ("db", db_app),
    ("project", project_app),
    ("review", review_app),
    ("dataset", dataset_app),
    ("jobs", jobs_app),
    ("runs", runs_app),
    ("inference", inference_app),
    ("backup", backup_app),
    ("artifacts", artifacts_app),
):
    app.add_typer(group, name=name)


def _resolve_config_path(path: Path) -> Path:
    if path.is_file():
        return path.resolve()
    parts = list(path.parts)
    alias = next((index for index, part in enumerate(parts) if part == "steel-platform"), None)
    suggested = None
    if alias is not None:
        parts[alias] = "steel_platform"
        suggested = Path(*parts)
        if suggested.is_file():
            return suggested.resolve()
    hint = f"；是否想使用：{suggested}" if suggested is not None else ""
    raise typer.BadParameter(f"配置文件不存在：{path}{hint}")


def _config(path: Path):
    resolved = _resolve_config_path(path)
    if resolved != path.resolve():
        typer.echo(f"[提示] 已自动修正配置路径：{resolved}")
    return load_settings(resolved)


@db_app.command("upgrade")
def db_upgrade(config: Path = typer.Option(Path("platform.yaml"), "--config", "-c")) -> None:
    settings = _config(config)
    configure_logging(settings.artifact_root / "logs")
    upgrade_database(settings.database_url)
    typer.echo("数据库已升级到当前版本。")


@project_app.command("init")
def project_init(config: Path = typer.Option(..., "--config", "-c")) -> None:
    settings = _config(config)
    project_id = bootstrap_project(settings)
    typer.echo(f"项目已登记：{project_id}")


@project_app.command("check")
def project_check(config: Path = typer.Option(..., "--config", "-c")) -> None:
    settings = _config(config)
    problems = []
    for label, path, kind in (
        ("原图目录", settings.source_images, "dir"),
        ("候选标签目录", settings.candidate_labels, "dir"),
        ("复核清单", settings.review_csv, "file"),
        ("种子清单", settings.seed_manifest, "file"),
        ("种子数据集", settings.seed_dataset, "dir"),
    ):
        valid = path.is_dir() if kind == "dir" else path.is_file()
        if not valid:
            problems.append(f"{label}不存在：{path}")
    if problems:
        for problem in problems:
            typer.echo(f"[错误] {problem}", err=True)
        raise typer.Exit(2)
    current, head = database_version(settings.database_url)
    if current == head and settings.database_path.is_file():
        from steel_platform.application.maintenance import verify_external_sources
        report = verify_external_sources(settings)
        typer.echo(f"数据库版本：{current}；登记原图：{report['images']}；候选标签：{report['candidate_labels']}；哈希异常：{report['invalid']}")
        if report["invalid"]:
            raise typer.Exit(2)
    else:
        typer.echo("源路径检查通过；数据库尚未升级，未执行登记资产哈希核对。")


@review_app.command("round-create")
def round_create(
    round_number: int = typer.Option(..., "--round", min=1),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    settings = _config(config)
    project_id = bootstrap_project(settings)
    round_id = create_review_round(settings, project_id=project_id, round_number=round_number)
    from steel_platform.application.exports import review_round_summary
    summary = review_round_summary(settings, round_number=round_number)
    typer.echo(f"复核轮次已就绪：{round_id}；总数={summary['total']}；逐类={summary['classes']}；划分={summary['splits']}")


@review_app.command("export-progress")
def export_progress(
    round_number: int = typer.Option(..., "--round", min=1),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
    output: Path = typer.Option(Path("review_progress.csv"), "--output", "-o"),
) -> None:
    from steel_platform.application.exports import export_review_progress

    count = export_review_progress(_config(config), round_number=round_number, output=output)
    typer.echo(f"已导出 {count} 条：{output.resolve()}")


@review_app.command("repair-rounding")
def repair_rounding(
    round_number: int = typer.Option(..., "--round", min=1),
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.maintenance import repair_review_rounding

    report = repair_review_rounding(_config(config), round_number=round_number, apply=not dry_run)
    mode = "预览" if dry_run else "执行"
    typer.echo(
        f"舍入修复{mode}：扫描={report['scanned']}，异常={report['invalid']}，"
        f"可修复={report['repairable']}，已修复={report['repaired']}，"
        f"未解决={len(report['unresolved'])}"
    )
    for problem in report["unresolved"]:
        typer.echo(f"[未解决] {problem['filename']}：{problem['reason']}", err=True)
    if report["unresolved"]:
        raise typer.Exit(2)


@dataset_app.command("publish")
def dataset_publish(
    round_number: int = typer.Option(..., "--round", min=1),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import publish_dataset

    dataset_id = publish_dataset(_config(config), round_number=round_number)
    typer.echo(f"数据集版本已发布：{dataset_id}")


@jobs_app.command("prepare-training")
def prepare_training(
    dataset_id: str = typer.Option(..., "--dataset"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import prepare_training_jobs

    jobs = prepare_training_jobs(_config(config), dataset_id=dataset_id)
    typer.echo("\n".join(jobs))


@jobs_app.command("show")
def show_job(
    job_id: str = typer.Option(..., "--job"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from sqlalchemy.orm import Session
    from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
    from steel_platform.infrastructure.database import make_engine
    from steel_platform.infrastructure.models import JobModel

    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        job = session.get(JobModel, job_id)
        if job is None or job.command_key is None:
            raise typer.BadParameter("任务不存在或尚未生成命令")
        path = LocalArtifactStore(settings.artifact_root).resolve(ArtifactRef(job.command_key, "", 0, "text/plain"))
        typer.echo(f"状态：{job.status}\n工作目录：{job.spec_json.get('cwd')}\n命令：\n{path.read_text(encoding='utf-8')}")


@jobs_app.command("run")
def run_job_command(
    job_id: str = typer.Option(..., "--job"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import run_manual_job

    status = run_manual_job(_config(config), job_id=job_id)
    typer.echo(f"任务最终状态：{status}")
    if status != "succeeded":
        raise typer.Exit(1)


@runs_app.command("ingest-training")
def ingest_training(
    job_id: str = typer.Option(..., "--job"),
    path: Path = typer.Option(..., "--path"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import ingest_training_run

    typer.echo(f"模型版本已登记：{ingest_training_run(_config(config), job_id=job_id, run_dir=path)}")


@inference_app.command("prepare")
def prepare_inference(
    model_id: str = typer.Option(..., "--model"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import prepare_inference_job

    typer.echo(prepare_inference_job(_config(config), model_id=model_id))


@runs_app.command("ingest-inference")
def ingest_inference(
    job_id: str = typer.Option(..., "--job"),
    path: Path = typer.Option(..., "--path"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import ingest_inference_run

    typer.echo(f"推理运行已登记：{ingest_inference_run(_config(config), job_id=job_id, prediction_dir=path)}")


@review_app.command("audit-create")
def audit_create(
    inference_id: str = typer.Option(..., "--inference"),
    per_class: int = typer.Option(10, "--per-class", min=1),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.workflows import create_audit_round

    typer.echo(f"抽查轮次已创建：{create_audit_round(_config(config), inference_id, per_class)}")


@backup_app.command("create")
def backup_create(config: Path = typer.Option(Path("platform.yaml"), "--config", "-c")) -> None:
    from steel_platform.application.maintenance import create_backup

    typer.echo(f"备份已创建：{create_backup(_config(config))}")


@artifacts_app.command("verify")
def artifacts_verify(config: Path = typer.Option(Path("platform.yaml"), "--config", "-c")) -> None:
    from steel_platform.application.maintenance import verify_artifacts

    report = verify_artifacts(_config(config))
    typer.echo(f"已检查 {report['checked']} 项，异常 {report['invalid']} 项。")
    if report["invalid"]:
        raise typer.Exit(2)


@artifacts_app.command("gc")
def artifacts_gc(
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
    config: Path = typer.Option(Path("platform.yaml"), "--config", "-c"),
) -> None:
    from steel_platform.application.maintenance import find_orphan_artifacts

    orphans = find_orphan_artifacts(_config(config))
    if not dry_run:
        raise typer.BadParameter("Demo仅允许--dry-run，避免误删不可变资产")
    typer.echo(f"发现 {len(orphans)} 个无引用资产（未删除）。")


@app.command("serve")
def serve(config: Path = typer.Option(..., "--config", "-c")) -> None:
    import uvicorn

    from steel_platform.interfaces.api import create_app

    settings = _config(config)
    configure_logging(settings.artifact_root / "logs")
    lock_path = settings.artifact_root / ".steel-platform.lock"
    try:
        with single_instance_lock(lock_path):
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    except WorkspaceLockedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


if __name__ == "__main__":
    app()
