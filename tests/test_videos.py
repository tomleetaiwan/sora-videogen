from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus, Video
from app.services.media_backend_health import MediaBackendStatus


@pytest.mark.asyncio
async def test_trigger_generation_refreshes_project_detail_fragment(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()
        session.add(
            ScenePrompt(
                project_id=project.id,
                sequence_order=0,
                narration_text="場景旁白",
                video_prompt="A cinematic scene",
                status=SceneStatus.PENDING,
            )
        )
        await session.commit()
        project_id = project.id

    start_generation_mock = Mock()
    monkeypatch.setattr("app.routers.videos.start_generation", start_generation_mock)

    response = await client.post(f"/videos/{project_id}/generate")

    assert response.status_code == 200
    assert 'id="project-detail-content"' in response.text
    assert "生成狀態" in response.text
    assert "0 / 1 場景完成" in response.text
    assert response.text.count("progress-bar") == 1
    assert response.text.count(f'hx-get="/projects/{project_id}/content"') == 1
    assert "開始產生分鏡影片" not in response.text
    start_generation_mock.assert_called_once_with(project_id)


@pytest.mark.asyncio
async def test_trigger_scene_video_regeneration_refreshes_project_detail_fragment(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(
            url="https://example.com/article",
            status=ProjectStatus.COMPLETED,
            final_video_path="media/1/final_video.mp4",
        )
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="場景旁白",
            video_prompt="A cinematic scene",
            status=SceneStatus.COMPLETED,
        )
        session.add(scene)
        await session.commit()
        project_id = project.id
        scene_id = scene.id

    start_scene_video_regeneration_mock = Mock()
    monkeypatch.setattr(
        "app.routers.videos.start_scene_video_regeneration",
        start_scene_video_regeneration_mock,
    )

    detail_response = await client.get(f"/projects/{project_id}")
    assert detail_response.status_code == 200
    assert "重新產生影片" in detail_response.text
    assert "重新產生音檔" in detail_response.text

    response = await client.post(f"/videos/{project_id}/scenes/{scene_id}/regenerate-video")

    assert response.status_code == 200
    assert 'id="project-detail-content"' in response.text
    assert response.text.count(f'hx-get="/projects/{project_id}/content"') == 1
    assert response.text.count(f'hx-post="/videos/{project_id}/stitch"') == 0
    start_scene_video_regeneration_mock.assert_called_once_with(project_id, scene_id)


@pytest.mark.asyncio
async def test_trigger_scene_audio_regeneration_refreshes_project_detail_fragment(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(
            url="https://example.com/article",
            status=ProjectStatus.COMPLETED,
            final_video_path="media/1/final_video.mp4",
        )
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="場景旁白",
            video_prompt="A cinematic scene",
            status=SceneStatus.COMPLETED,
        )
        session.add(scene)
        await session.commit()
        project_id = project.id
        scene_id = scene.id

    start_scene_audio_regeneration_mock = Mock()
    monkeypatch.setattr(
        "app.routers.videos.start_scene_audio_regeneration",
        start_scene_audio_regeneration_mock,
    )

    response = await client.post(f"/videos/{project_id}/scenes/{scene_id}/regenerate-audio")

    assert response.status_code == 200
    assert 'id="project-detail-content"' in response.text
    assert response.text.count(f'hx-get="/projects/{project_id}/content"') == 1
    assert response.text.count(f'hx-post="/videos/{project_id}/stitch"') == 0
    start_scene_audio_regeneration_mock.assert_called_once_with(project_id, scene_id)


@pytest.mark.asyncio
async def test_trigger_stitching_refreshes_project_detail_fragment(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()
        session.add(
            ScenePrompt(
                project_id=project.id,
                sequence_order=0,
                narration_text="場景旁白",
                video_prompt="A cinematic scene",
                duration_estimate=6.0,
                status=SceneStatus.COMPLETED,
            )
        )
        await session.commit()
        project_id = project.id

    start_stitching_mock = Mock()
    monkeypatch.setattr("app.routers.videos.start_stitching", start_stitching_mock)

    response = await client.post(f"/videos/{project_id}/stitch")

    assert response.status_code == 200
    assert 'id="project-detail-content"' in response.text
    assert "影片串接中" in response.text
    assert "請求影片秒數：8 秒" in response.text
    assert response.text.count(f'hx-get="/projects/{project_id}/content"') == 1
    start_stitching_mock.assert_called_once_with(project_id)


@pytest.mark.asyncio
async def test_video_status_shows_project_and_scene_failure_messages(client, db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(
            url="https://example.com/article",
            status=ProjectStatus.FAILED,
            error_message="Video generation failed (status: failed, code: server_error, message: backend timeout)",
        )
        session.add(project)
        await session.flush()

        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="場景旁白",
            video_prompt="A cinematic scene",
            status=SceneStatus.FAILED,
        )
        session.add(scene)
        await session.flush()
        session.add(
            Video(
                scene_prompt_id=scene.id,
                error_message="Video generation failed (status: failed, code: server_error, message: backend timeout)",
            )
        )
        await session.commit()
        project_id = project.id

    response = await client.get(f"/videos/{project_id}/status")

    assert response.status_code == 200
    assert 'id="generation-status"' in response.text
    assert "Video generation failed (status: failed, code: server_error, message: backend timeout)" in response.text
    assert "失敗場景" in response.text
    assert "場景 #1" in response.text
    assert response.text.count("progress-bar") == 1


@pytest.mark.asyncio
async def test_trigger_generation_rejects_when_media_backend_not_ready(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()
        session.add(
            ScenePrompt(
                project_id=project.id,
                sequence_order=0,
                narration_text="場景旁白",
                video_prompt="A cinematic scene",
                status=SceneStatus.PENDING,
            )
        )
        await session.commit()
        project_id = project.id

    monkeypatch.setattr(
        app.state,
        "media_backend_status",
        MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時發現 GStreamer 媒體後端尚未就緒。",
        ),
        raising=False,
    )
    start_generation_mock = Mock()
    monkeypatch.setattr("app.routers.videos.start_generation", start_generation_mock)

    response = await client.post(f"/videos/{project_id}/generate")

    assert response.status_code == 503
    assert response.json()["detail"] == "啟動時發現 GStreamer 媒體後端尚未就緒。"
    start_generation_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_scene_video_regeneration_rejects_when_media_backend_not_ready(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="場景旁白",
            video_prompt="A cinematic scene",
            status=SceneStatus.COMPLETED,
        )
        session.add(scene)
        await session.commit()
        project_id = project.id
        scene_id = scene.id

    monkeypatch.setattr(
        app.state,
        "media_backend_status",
        MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時發現 GStreamer 媒體後端尚未就緒。",
        ),
        raising=False,
    )
    start_scene_video_regeneration_mock = Mock()
    monkeypatch.setattr(
        "app.routers.videos.start_scene_video_regeneration",
        start_scene_video_regeneration_mock,
    )

    response = await client.post(f"/videos/{project_id}/scenes/{scene_id}/regenerate-video")

    assert response.status_code == 503
    assert response.json()["detail"] == "啟動時發現 GStreamer 媒體後端尚未就緒。"
    start_scene_video_regeneration_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_scene_audio_regeneration_rejects_when_media_backend_not_ready(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="場景旁白",
            video_prompt="A cinematic scene",
            status=SceneStatus.COMPLETED,
        )
        session.add(scene)
        await session.commit()
        project_id = project.id
        scene_id = scene.id

    monkeypatch.setattr(
        app.state,
        "media_backend_status",
        MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時發現 GStreamer 媒體後端尚未就緒。",
        ),
        raising=False,
    )
    start_scene_audio_regeneration_mock = Mock()
    monkeypatch.setattr(
        "app.routers.videos.start_scene_audio_regeneration",
        start_scene_audio_regeneration_mock,
    )

    response = await client.post(f"/videos/{project_id}/scenes/{scene_id}/regenerate-audio")

    assert response.status_code == 503
    assert response.json()["detail"] == "啟動時發現 GStreamer 媒體後端尚未就緒。"
    start_scene_audio_regeneration_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_stitching_rejects_when_media_backend_not_ready(
    client,
    db_engine,
    monkeypatch,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()
        session.add(
            ScenePrompt(
                project_id=project.id,
                sequence_order=0,
                narration_text="場景旁白",
                video_prompt="A cinematic scene",
                duration_estimate=6.0,
                status=SceneStatus.COMPLETED,
            )
        )
        await session.commit()
        project_id = project.id

    monkeypatch.setattr(
        app.state,
        "media_backend_status",
        MediaBackendStatus(
            enabled=True,
            ready=False,
            warning_message="啟動時發現 GStreamer 媒體後端尚未就緒。",
        ),
        raising=False,
    )
    start_stitching_mock = Mock()
    monkeypatch.setattr("app.routers.videos.start_stitching", start_stitching_mock)

    response = await client.post(f"/videos/{project_id}/stitch")

    assert response.status_code == 503
    assert response.json()["detail"] == "啟動時發現 GStreamer 媒體後端尚未就緒。"
    start_stitching_mock.assert_not_called()