from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus
from app.services.media_backend_health import (
    MediaBackendStatus,
    create_default_media_backend_status,
)
from app.templating import templates
from app.tasks.pipeline import (
    get_task_for_project,
    start_generation,
    start_scene_audio_regeneration,
    start_scene_video_regeneration,
    start_stitching,
)

router = APIRouter(prefix="/videos", tags=["videos"])


async def _load_project_with_scenes(db: AsyncSession, project_id: int) -> Project | None:
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.scenes).selectinload(ScenePrompt.video))
        .where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


def _project_has_active_task(project_id: int) -> bool:
    task = get_task_for_project(project_id)
    return task is not None and not task.done()


def _all_scenes_completed(project: Project) -> bool:
    return bool(project.scenes) and all(scene.status == SceneStatus.COMPLETED for scene in project.scenes)


def _get_media_backend_status(request: Request) -> MediaBackendStatus:
    return getattr(
        request.app.state,
        "media_backend_status",
        create_default_media_backend_status(),
    )


def _ensure_media_backend_ready(request: Request) -> None:
    status = _get_media_backend_status(request)
    if status.enabled and not status.ready:
        raise HTTPException(
            status_code=503,
            detail=status.warning_message or "Configured media backend is not ready",
        )


@router.post("/{project_id}/generate", response_class=HTMLResponse)
async def trigger_generation(
    request: Request, project_id: int, db: AsyncSession = Depends(get_db)
):
    _ensure_media_backend_ready(request)

    if _project_has_active_task(project_id):
        raise HTTPException(status_code=409, detail="Project is already processing another task")

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.scenes:
        raise HTTPException(status_code=400, detail="Project has no scenes to generate")

    if _all_scenes_completed(project):
        raise HTTPException(
            status_code=400,
            detail="All scene segments are already generated",
        )

    project.status = ProjectStatus.GENERATING
    project.final_video_path = None
    project.error_message = None
    await db.commit()
    start_generation(project_id)

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="components/project_detail_content.html",
        context={"project": project},
    )


async def _trigger_scene_regeneration(
    request: Request,
    project_id: int,
    scene_id: int,
    *,
    start_regeneration,
    db: AsyncSession = Depends(get_db),
):
    _ensure_media_backend_ready(request)

    if _project_has_active_task(project_id):
        raise HTTPException(status_code=409, detail="Project is already processing another task")

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    scene = next((candidate for candidate in project.scenes if candidate.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")

    if scene.status not in {SceneStatus.COMPLETED, SceneStatus.FAILED}:
        raise HTTPException(
            status_code=400,
            detail="Only completed or failed scenes can be regenerated",
        )

    project.status = ProjectStatus.GENERATING
    project.final_video_path = None
    project.error_message = None
    await db.commit()
    start_regeneration(project_id, scene_id)

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="components/project_detail_content.html",
        context={"project": project},
    )


@router.post("/{project_id}/scenes/{scene_id}/regenerate-video", response_class=HTMLResponse)
async def trigger_scene_video_regeneration(
    request: Request,
    project_id: int,
    scene_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await _trigger_scene_regeneration(
        request,
        project_id,
        scene_id,
        start_regeneration=start_scene_video_regeneration,
        db=db,
    )


@router.post("/{project_id}/scenes/{scene_id}/regenerate-audio", response_class=HTMLResponse)
async def trigger_scene_audio_regeneration(
    request: Request,
    project_id: int,
    scene_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await _trigger_scene_regeneration(
        request,
        project_id,
        scene_id,
        start_regeneration=start_scene_audio_regeneration,
        db=db,
    )


@router.post("/{project_id}/stitch", response_class=HTMLResponse)
async def trigger_stitching(
    request: Request,
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    _ensure_media_backend_ready(request)

    if _project_has_active_task(project_id):
        raise HTTPException(status_code=409, detail="Project is already processing another task")

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _all_scenes_completed(project):
        raise HTTPException(
            status_code=400,
            detail="All scenes must finish generating before stitching",
        )

    project.status = ProjectStatus.STITCHING
    project.final_video_path = None
    project.error_message = None
    await db.commit()
    start_stitching(project_id)

    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="components/project_detail_content.html",
        context={"project": project},
    )


@router.get("/{project_id}/status", response_class=HTMLResponse)
async def video_status(request: Request, project_id: int, db: AsyncSession = Depends(get_db)):
    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="components/generation_status.html",
        context={"project": project},
    )
