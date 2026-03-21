from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus
from app.services.entra_auth import EntraAuthStatus, EntraTokenCheck
from app.services.media_backend_health import MediaBackendCheck, MediaBackendStatus


async def _create_projects(session_factory, urls: list[str]) -> list[int]:
    async with session_factory() as session:
        projects: list[Project] = []
        for url in urls:
            project = Project(url=url, status=ProjectStatus.COMPLETED)
            session.add(project)
            projects.append(project)

        await session.flush()
        project_ids = [project.id for project in projects]
        await session.commit()
        return project_ids


@pytest.mark.asyncio
async def test_clear_projects_history_removes_projects_and_media(
    client,
    db_engine,
    monkeypatch,
    tmp_path,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project_ids = await _create_projects(
        session_factory,
        ["https://example.com/a", "https://example.com/b"],
    )

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    for project_id in project_ids:
        project_dir = tmp_path / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "artifact.txt").write_text("generated", encoding="utf-8")

    response = await client.request("DELETE", "/projects/")

    assert response.status_code == 200
    assert "清除歷史紀錄" in response.text
    assert "目前沒有歷史紀錄" in response.text
    assert "disabled" in response.text

    async with session_factory() as session:
        projects = list((await session.execute(select(Project))).scalars().all())

    assert projects == []
    for project_id in project_ids:
        assert not (tmp_path / str(project_id)).exists()


@pytest.mark.asyncio
async def test_clear_projects_history_cancels_running_tasks(client, db_engine, monkeypatch):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project_ids = await _create_projects(
        session_factory,
        ["https://example.com/a", "https://example.com/b"],
    )

    fake_tasks = {project_id: Mock(done=Mock(return_value=False), cancel=Mock()) for project_id in project_ids}

    monkeypatch.setattr(
        "app.routers.projects.get_task_for_project",
        lambda project_id: fake_tasks[project_id],
    )

    response = await client.request("DELETE", "/projects/")

    assert response.status_code == 200
    for task in fake_tasks.values():
        task.cancel.assert_called_once_with()


@pytest.mark.asyncio
async def test_project_detail_shows_actual_audio_and_requested_video_durations(client, db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()
        session.add_all(
            [
                ScenePrompt(
                    project_id=project.id,
                    sequence_order=0,
                    narration_text="完成場景",
                    video_prompt="Completed prompt",
                    duration_estimate=6.0,
                    status=SceneStatus.COMPLETED,
                ),
                ScenePrompt(
                    project_id=project.id,
                    sequence_order=1,
                    narration_text="待處理場景",
                    video_prompt="Pending prompt",
                    duration_estimate=6.2,
                    status=SceneStatus.PENDING,
                ),
            ]
        )
        await session.commit()
        project_id = project.id

    response = await client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert "實際音訊時長：6.0 秒" in response.text
    assert "請求影片秒數：8 秒" in response.text
    assert "預估旁白時長：6.2 秒" in response.text


@pytest.mark.asyncio
async def test_index_defaults_video_prompt_language_to_chinese(client):
    response = await client.get("/projects/")

    assert response.status_code == 200
    assert 'name="video_prompt_language" value="zh-TW" checked' in response.text
    assert 'name="video_prompt_language" value="en"' in response.text


@pytest.mark.asyncio
async def test_index_shows_entra_auth_warning_when_startup_check_failed(client, monkeypatch):
    monkeypatch.setattr(
        app.state,
        "entra_auth_status",
        EntraAuthStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時無法透過 Microsoft Entra ID 取得 Azure OpenAI 所需 token。",
            checks=[
                EntraTokenCheck(
                    service_name="Azure OpenAI",
                    success=False,
                    detail="login required",
                )
            ],
        ),
        raising=False,
    )

    response = await client.get("/projects/")

    assert response.status_code == 200
    assert "Microsoft Entra ID 驗證尚未就緒" in response.text
    assert "Azure OpenAI" in response.text
    assert "login required" in response.text


@pytest.mark.asyncio
async def test_index_shows_media_backend_warning_when_gstreamer_check_failed(client, monkeypatch):
    monkeypatch.setattr(
        app.state,
        "media_backend_status",
        MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時發現 GStreamer 媒體後端尚未就緒。",
            checks=[
                MediaBackendCheck(
                    component_name="avenc_aac",
                    success=False,
                    detail="No such element or plugin 'avenc_aac'",
                )
            ],
        ),
        raising=False,
    )

    response = await client.get("/projects/")

    assert response.status_code == 200
    assert "GStreamer 媒體後端尚未就緒" in response.text
    assert "avenc_aac" in response.text
    assert "No such element or plugin" in response.text


@pytest.mark.asyncio
async def test_delete_project_removes_single_history_entry_and_media(
    client,
    db_engine,
    monkeypatch,
    tmp_path,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project_ids = await _create_projects(
        session_factory,
        ["https://example.com/delete-me", "https://example.com/keep-me"],
    )

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    for project_id in project_ids:
        project_dir = tmp_path / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "artifact.txt").write_text("generated", encoding="utf-8")

    response = await client.request("DELETE", f"/projects/{project_ids[0]}")

    assert response.status_code == 200
    assert "https://example.com/delete-me" not in response.text
    assert "https://example.com/keep-me" in response.text

    async with session_factory() as session:
        projects = list((await session.execute(select(Project).order_by(Project.id))).scalars().all())

    assert [project.url for project in projects] == ["https://example.com/keep-me"]
    assert not (tmp_path / str(project_ids[0])).exists()
    assert (tmp_path / str(project_ids[1])).exists()