from __future__ import annotations

import csv
import json
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform.application.bootstrap import bootstrap_project, create_review_round
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import database_version, make_engine, upgrade_database
from steel_platform.infrastructure.models import AssetModel, ImportEntryModel, ImportSessionModel, ProjectModel, ReviewItemModel, ReviewRoundModel, SourceRootModel
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
source_app = typer.Typer(help="data source verification and rebinding")
import_app = typer.Typer(help="import status")

for name, group in (
    ("db", db_app),
    ("project", project_app),
    ("review", review_app),
    ("dataset", dataset_app),
    ("jobs", jobs_app),
    ("runs", runs_app),
    ("inference", inference_app),
    ("backup", backup_app),
    ("source", source_app),
    ("import", import_app),
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
    from steel_platform.application.maintenance import create_backup, snapshot_database_counts, verify_upgrade_counts
    current, head = database_version(settings.database_url)
    backup = create_backup(settings, verify_artifact_references=False) if current and current != head else None
    if backup is not None:
        typer.echo(f"Backup created: {backup.resolve()}. If migration fails, manually restore this backup; automatic overwrite is disabled.")
    before = snapshot_database_counts(settings.database_path) if current else None
    try:
        upgrade_database(settings.database_url)
        verify_upgrade_counts(settings.database_path, before)
    except Exception:
        if backup is not None:
            typer.echo(f"Migration failed. Backup remains at: {backup.resolve()}. Manually restore it if needed.", err=True)
        raise
    if backup is not None:
        typer.echo(f"备份：{backup}")
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
    if current != head or not settings.database_path.is_file():
        typer.echo("Database upgrade required: steel-platform db upgrade --config <yaml>", err=True)
        raise typer.Exit(2)
    if current == head and settings.database_path.is_file():
        from steel_platform.application.maintenance import verify_external_sources
        report = verify_external_sources(settings)
        for source in report["by_source"]:
            typer.echo(
                f"数据源：{source['kind']}；已检查：{source['checked']}；"
                f"异常：{source['invalid']}；路径：{source['path']}"
            )
        typer.echo(
            f"数据库版本：{current}；数据源：{report['sources']}；"
            f"来源资产：{report['source_assets']}；登记原图：{report['images']}；"
            f"候选标签：{report['candidate_labels']}；哈希异常：{report['invalid']}"
        )
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
    project_id: str = typer.Option(..., "--project"),
    round_id: str = typer.Option(..., "--round-id"),
    config: Path = typer.Option(..., "--config", "-c"),
    output: Path = typer.Option(Path("review_progress.csv"), "--output", "-o"),
) -> None:
    review_export(project_id=project_id, round_id=round_id, config=config, output=output)


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

    settings = _config(config)
    current, head = database_version(settings.database_url)
    verify_artifact_references = current == head
    backup = create_backup(settings, verify_artifact_references=verify_artifact_references)
    if verify_artifact_references:
        typer.echo(f"Backup created: {backup}")
    else:
        typer.echo(
            f"Backup created: {backup}; artifact verification status: "
            "deferred_until_schema_upgrade (run db upgrade before verifying artifacts)."
        )


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


@project_app.command("list")
def project_list(
    as_json: bool = typer.Option(False, "--json"),
    config: Path = typer.Option(..., "--config", "-c"),
) -> None:
    if not as_json:
        raise typer.BadParameter("project list requires --json")
    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        rows = [
            {"id": item.id, "name": item.name, "revision": item.revision}
            for item in session.scalars(select(ProjectModel).order_by(ProjectModel.id))
        ]
    typer.echo(json.dumps(rows, ensure_ascii=False, sort_keys=True))


@review_app.command("round-list")
def review_round_list(
    project_id: str = typer.Option(..., "--project"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path = typer.Option(..., "--config", "-c"),
) -> None:
    if not as_json:
        raise typer.BadParameter("review round-list requires --json")
    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        rows = [
            {"id": item.id, "project_id": item.project_id, "number": item.number, "kind": item.kind, "name": item.name, "status": item.status, "target_count": item.target_count}
            for item in session.scalars(select(ReviewRoundModel).where(ReviewRoundModel.project_id == project_id).order_by(ReviewRoundModel.number, ReviewRoundModel.id))
        ]
    typer.echo(json.dumps(rows, ensure_ascii=False, sort_keys=True))


@review_app.command("export")
def review_export(
    project_id: str = typer.Option(..., "--project"),
    round_id: str = typer.Option(..., "--round-id"),
    config: Path = typer.Option(..., "--config", "-c"),
    output: Path = typer.Option(Path("review_progress.csv"), "--output", "-o"),
) -> None:
    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        review_round = session.scalar(select(ReviewRoundModel).where(ReviewRoundModel.project_id == project_id, ReviewRoundModel.id == round_id))
        if review_round is None:
            raise typer.BadParameter("review round does not exist in the selected project")
        items = session.scalars(select(ReviewItemModel).where(ReviewItemModel.round_id == round_id).order_by(ReviewItemModel.rank)).all()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        fields = ["item_id", "filename", "class_id", "split", "selection_reason", "source_status", "state", "revision", "note"]
        with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for item in items:
                writer.writerow({"item_id": item.id, "filename": item.filename, "class_id": item.expected_class_id, "split": item.split_role, "selection_reason": item.selection_reason, "source_status": item.source_status, "state": item.state, "revision": item.revision, "note": item.note})
        temporary.replace(output)
    typer.echo(f"exported {len(items)} rows: {output.resolve()}")


@source_app.command("verify")
def source_verify(
    project_id: str = typer.Option(..., "--project"),
    source_id: str = typer.Option(..., "--source"),
    config: Path = typer.Option(..., "--config", "-c"),
) -> None:
    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        source = session.scalar(select(SourceRootModel).where(SourceRootModel.project_id == project_id, SourceRootModel.id == source_id))
        if source is None:
            raise typer.BadParameter("source does not exist in the selected project")
        checked = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(AssetModel).where(AssetModel.project_id == project_id, AssetModel.source_root_id == source_id)) or 0
        report = {"id": source.id, "project_id": source.project_id, "status": source.status, "checked": checked, "available": Path(source.path).is_dir()}
    typer.echo(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report["available"]:
        raise typer.Exit(2)


@source_app.command("rebind")
def source_rebind(
    project_id: str = typer.Option(..., "--project"),
    source_id: str = typer.Option(..., "--source"),
    path: Path = typer.Option(..., "--path"),
    config: Path = typer.Option(..., "--config", "-c"),
) -> None:
    from sqlalchemy.orm import sessionmaker
    from steel_platform.application.imports import DataSourceImportService
    from steel_platform.infrastructure.artifacts import LocalArtifactStore
    from steel_platform.infrastructure.directory_picker import LocalFolderReader
    from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork

    settings = _config(config)
    session_factory = sessionmaker(bind=make_engine(settings.database_url))
    service = DataSourceImportService(lambda: SqlAlchemyUnitOfWork(session_factory), LocalArtifactStore(settings.artifact_root), LocalFolderReader())
    source = service.rebind(project_id, source_id, path)
    typer.echo(json.dumps({"id": source.id, "project_id": source.project_id, "path": source.root_path, "status": source.status.value}, ensure_ascii=False, sort_keys=True))


@import_app.command("status")
def import_status(
    project_id: str = typer.Option(..., "--project"),
    import_id: str | None = typer.Option(None, "--import-id"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path = typer.Option(..., "--config", "-c"),
) -> None:
    if not as_json:
        raise typer.BadParameter("import status requires --json")
    settings = _config(config)
    with Session(make_engine(settings.database_url)) as session:
        statement = select(ImportSessionModel).where(ImportSessionModel.project_id == project_id).order_by(ImportSessionModel.created_at)
        if import_id is not None:
            statement = statement.where(ImportSessionModel.id == import_id)
        rows = []
        for item in session.scalars(statement):
            entries = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(ImportEntryModel).where(ImportEntryModel.project_id == project_id, ImportEntryModel.import_session_id == item.id)) or 0
            rows.append({"id": item.id, "project_id": item.project_id, "source_id": item.data_source_id, "collection_id": item.collection_id, "status": item.status, "entries": entries})
    typer.echo(json.dumps(rows, ensure_ascii=False, sort_keys=True))


@app.command("serve")
def serve(config: Path = typer.Option(..., "--config", "-c")) -> None:
    import uvicorn

    from steel_platform.interfaces.api import create_app

    settings = _config(config)
    current, head = database_version(settings.database_url)
    if current != head:
        typer.echo("Database upgrade required: steel-platform db upgrade --config <yaml>", err=True)
        raise typer.Exit(2)
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
