"""Migration & import APIs: convert exports, build test packages, validate, instructions."""
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import get_current_user
from app.database import get_session
from app.models import ChatExport, ImportPackage, MigrationJob, TelegramSession, UserAccount
from app.schemas.migration import (
    ImportPackagePublic,
    InstructionsResult,
    MigrationCreate,
    MigrationPublic,
    TestPackageCreate,
    ValidationRequest,
    ValidationResult,
)
from app.services import import_assistant, test_builder
from app.services.converter import build_whatsapp_package
from app.services.telegram_utils import safe_filename

router = APIRouter(tags=["migrations"], dependencies=[Depends(get_current_user)])

DbSession = Annotated[AsyncSession, Depends(get_session)]


def _export_query(user_id: int):
    return select(ChatExport).join(
        TelegramSession, ChatExport.telegram_session_id == TelegramSession.id
    ).where(TelegramSession.user_account_id == user_id)


async def _owned_export(export_id: int, db: AsyncSession, user: UserAccount) -> ChatExport:
    export = await db.scalar(_export_query(user.id).where(ChatExport.id == export_id))
    if export is None:
        raise HTTPException(status_code=404, detail="Export not found")
    return export


def _job_public(job: MigrationJob) -> MigrationPublic:
    return MigrationPublic(
        id=job.id,
        chat_export_id=job.chat_export_id,
        format=job.format,
        status=job.status,
        messages_converted=job.messages_converted,
        media_copied=job.media_copied,
        output_dir=job.output_dir,
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _package_public(pkg: ImportPackage) -> ImportPackagePublic:
    return ImportPackagePublic(
        id=pkg.id,
        migration_job_id=pkg.migration_job_id,
        name=pkg.name,
        package_path=pkg.package_path,
        format=pkg.format,
        messages_count=pkg.messages_count,
        media_count=pkg.media_count,
        users_detected=pkg.users_detected,
        date_min=pkg.date_min,
        date_max=pkg.date_max,
        validation_status=pkg.validation_status,
        validation_report=pkg.validation_report,
        created_at=pkg.created_at,
    )


def _dt(value) -> datetime | None:
    """Parse an ISO timestamp from a converter manifest into a datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/api/migrations", response_model=MigrationPublic, status_code=201)
async def create_migration(
    payload: MigrationCreate,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    export = await _owned_export(payload.export_id, db, user)
    # Allow migrating a partial/in-progress export for testing: any export that
    # has messages on disk is eligible, not only completed ones.
    if not export.export_dir or export.messages_processed <= 0:
        raise HTTPException(status_code=400, detail="Export has no data yet to migrate")

    job = MigrationJob(chat_export_id=export.id, format=payload.format, status="running")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    settings = get_settings()
    out_dir = settings.exports_dir / "migrations" / f"{job.id}_{safe_filename(export.chat_title or 'chat')}"
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_whatsapp_package(Path(export.export_dir), out_dir)
        job.status = "completed"
        job.messages_converted = manifest["messages"]
        job.media_copied = manifest["media"]
        job.output_dir = str(out_dir)
        job.finished_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        await db.commit()
        raise HTTPException(status_code=422, detail=job.error) from exc

    pkg = ImportPackage(
        migration_job_id=job.id,
        name=f"{export.chat_title} (WhatsApp)",
        package_path=str(out_dir),
        format="whatsapp",
        messages_count=manifest["messages"],
        media_count=manifest["media"],
        users_detected=manifest.get("users"),
        date_min=_dt(manifest.get("date_min")),
        date_max=_dt(manifest.get("date_max")),
        validation_status="pending",
    )
    db.add(pkg)
    await db.commit()
    await db.refresh(job)
    return _job_public(job)


@router.get("/api/migrations", response_model=list[MigrationPublic])
async def list_migrations(db: DbSession, user: Annotated[UserAccount, Depends(get_current_user)]):
    rows = (
        await db.scalars(
            select(MigrationJob)
            .join(ChatExport, MigrationJob.chat_export_id == ChatExport.id)
            .join(TelegramSession, ChatExport.telegram_session_id == TelegramSession.id)
            .where(TelegramSession.user_account_id == user.id)
            .order_by(MigrationJob.created_at.desc())
        )
    ).all()
    return [_job_public(r) for r in rows]


@router.post("/api/migrations/test", response_model=ImportPackagePublic, status_code=201)
async def create_test_package(
    payload: TestPackageCreate,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    settings = get_settings()
    out_dir = settings.exports_dir / "migrations" / "test" / f"test_{payload.count}_{int(datetime.now().timestamp())}"
    try:
        manifest = test_builder.build_test_package(payload.count, out_dir)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pkg = ImportPackage(
        migration_job_id=None,
        name=f"Test package ({payload.count} messages)",
        package_path=str(out_dir),
        format="whatsapp",
        messages_count=manifest["messages"],
        media_count=manifest["media"],
        users_detected=manifest.get("users"),
        date_min=_dt(manifest.get("date_min")),
        date_max=_dt(manifest.get("date_max")),
        validation_status="pending",
    )
    db.add(pkg)
    await db.commit()
    await db.refresh(pkg)
    return _package_public(pkg)


@router.get("/api/import/packages", response_model=list[ImportPackagePublic])
async def list_packages(db: DbSession, user: Annotated[UserAccount, Depends(get_current_user)]):
    rows = (
        await db.scalars(select(ImportPackage).order_by(ImportPackage.created_at.desc()))
    ).all()
    return [_package_public(r) for r in rows]


async def _owned_package(package_id: int, db: AsyncSession) -> ImportPackage:
    pkg = await db.get(ImportPackage, package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return pkg


@router.post("/api/import/validate", response_model=ValidationResult)
async def validate_package(
    payload: ValidationRequest,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    pkg = await _owned_package(payload.package_id, db)
    report = import_assistant.validate_package(Path(pkg.package_path))
    pkg.validation_status = report["validation_status"]
    pkg.validation_report = report
    await db.commit()
    return ValidationResult(
        validation_status=report["validation_status"],
        issues=report["issues"],
        stats=report["stats"],
    )


@router.get("/api/import/{package_id}/instructions", response_model=InstructionsResult)
async def package_instructions(
    package_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    pkg = await _owned_package(package_id, db)
    instructions = import_assistant.generate_instructions(Path(pkg.package_path))
    instructions_path = import_assistant.write_instructions(Path(pkg.package_path))
    return InstructionsResult(
        package_id=package_id,
        instructions=instructions,
        instructions_path=str(instructions_path),
    )
