from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus
from app.services.entra_auth import EntraAuthStatus, EntraTokenCheck
from app.video_timing import get_max_scene_duration_seconds


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
    assert 'data-scene-editor' in response.text
    assert 'data-max-narration-chars="36"' in response.text
    assert 'data-max-scene-seconds="12"' in response.text
    assert 'maxlength="36"' in response.text
    assert "建議不超過 36 字" in response.text
    assert "儲存前會顯示字數與預估秒數" in response.text


def test_settings_default_scene_limits():
    assert settings.max_scenes_per_project == 300
    assert get_max_scene_duration_seconds() == 12


@pytest.mark.asyncio
async def test_project_detail_disables_save_when_narration_exceeds_limit(client, db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    over_limit_narration = "超" * 37

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()
        session.add(
            ScenePrompt(
                project_id=project.id,
                sequence_order=0,
                narration_text=over_limit_narration,
                video_prompt="Prompt",
                status=SceneStatus.PENDING,
            )
        )
        await session.commit()
        project_id = project.id

    response = await client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    assert 'maxlength="36"' in response.text
    assert 'data-save-prompt-button disabled' in response.text
    assert 'class="is-invalid"' in response.text
    assert "旁白預估時長已超過 12 秒上限" in response.text


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