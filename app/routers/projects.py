import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import Project, ProjectStatus, ScenePrompt
from app.prompt_language import normalize_video_prompt_language
from app.services.entra_auth import create_default_entra_auth_status
from app.services.media_backend_health import create_default_media_backend_status
from app.templating import templates
from app.tasks.pipeline import get_task_for_project, start_pipeline

router = APIRouter(prefix="/projects", tags=["projects"])


def _cancel_project_task(project_id: int) -> None:
    task = get_task_for_project(project_id)
    if task and not task.done():
        task.cancel()


def _delete_project_media(project_id: int) -> None:
    shutil.rmtree(settings.media_dir / str(project_id), ignore_errors=True)


async def _load_project_with_scenes(db: AsyncSession, project_id: int) -> Project | None:
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.scenes).selectinload(ScenePrompt.video))
        .where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def list_projects(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "projects": projects,
            "entra_auth_status": getattr(
                request.app.state,
                "entra_auth_status",
                create_default_entra_auth_status(),
            ),
            "media_backend_status": getattr(
                request.app.state,
                "media_backend_status",
                create_default_media_backend_status(),
            ),
        },
    )


@router.post("/", response_class=HTMLResponse)
async def create_project(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    url = form.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    video_prompt_language = normalize_video_prompt_language(
        str(form.get("video_prompt_language", "zh-TW"))
    )

    project = Project(url=str(url), status=ProjectStatus.PENDING)
    db.add(project)
    await db.flush()

    # 啟動前置流程（擷取 → 摘要 → 生成提示詞）
    start_pipeline(project.id, video_prompt_language=video_prompt_language)
    project.status = ProjectStatus.SCRAPING
    await db.commit()

    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="components/history_panel.html",
        context={"projects": projects},
    )


@router.get("/{project_id}", response_class=HTMLResponse)
async def get_project(request: Request, project_id: int, db: AsyncSession = Depends(get_db)):
    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="project_detail.html",
        context={"project": project},
    )


@router.get("/{project_id}/content", response_class=HTMLResponse)
async def get_project_content(
    request: Request,
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project_with_scenes(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="components/project_detail_content.html",
        context={"project": project},
    )


@router.get("/{project_id}/status")
async def get_project_status(project_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"id": project.id, "status": project.status.value, "error": project.error_message}


@router.delete("/{project_id}", response_class=HTMLResponse)
async def delete_project(request: Request, project_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _cancel_project_task(project_id)
    await db.delete(project)
    await db.commit()
    _delete_project_media(project_id)

    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="components/history_panel.html",
        context={"projects": projects},
    )


@router.delete("/", response_class=HTMLResponse)
async def clear_projects_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = list(result.scalars().all())

    for project in projects:
        _cancel_project_task(project.id)
        await db.delete(project)

    await db.commit()

    for project in projects:
        _delete_project_media(project.id)

    return templates.TemplateResponse(
        request=request,
        name="components/history_panel.html",
        context={"projects": []},
    )
