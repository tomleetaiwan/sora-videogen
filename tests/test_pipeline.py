import wave
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus, Video
from app.tasks import pipeline


def _write_silent_wav(output_path, duration_seconds: float, sample_rate: int = 8000):
    frame_count = int(duration_seconds * sample_rate)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)


async def _create_project_with_scenes(session_factory, scene_payloads: list[dict]) -> int:
    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()

        for sequence_order, payload in enumerate(scene_payloads):
            session.add(
                ScenePrompt(
                    project_id=project.id,
                    sequence_order=sequence_order,
                    narration_text=payload["narration_text"],
                    video_prompt=payload["video_prompt"],
                    duration_estimate=payload.get("duration_estimate"),
                )
            )

        await session.commit()
        return project.id


async def _create_project_with_completed_scene_and_video(session_factory, *, final_video_path: str | None):
    async with session_factory() as session:
        project = Project(
            url="https://example.com/article",
            status=ProjectStatus.COMPLETED,
            final_video_path=final_video_path,
        )
        session.add(project)
        await session.flush()

        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="重生場景旁白",
            video_prompt="Regenerate prompt",
            duration_estimate=6.0,
            status=SceneStatus.COMPLETED,
        )
        session.add(scene)
        await session.flush()

        video = Video(
            scene_prompt_id=scene.id,
            video_path=str((settings.media_dir / str(project.id) / "scene_000" / "video.mp4")),
            audio_path=str((settings.media_dir / str(project.id) / "scene_000" / "narration.wav")),
            last_frame_path=str((settings.media_dir / str(project.id) / "scene_000" / "last_frame.png")),
        )
        session.add(video)
        await session.commit()

        return project.id, scene.id


@pytest.mark.asyncio
async def test_create_project(client):
    response = await client.post(
        "/projects/",
        data={"url": "https://example.com/article"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "example.com" in response.text


@pytest.mark.asyncio
async def test_create_project_uses_selected_video_prompt_language(client, db_engine, monkeypatch):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    start_pipeline_mock = Mock()
    monkeypatch.setattr("app.routers.projects.start_pipeline", start_pipeline_mock)

    response = await client.post(
        "/projects/",
        data={
            "url": "https://example.com/article",
            "video_prompt_language": "en",
        },
        follow_redirects=False,
    )

    async with session_factory() as session:
        project = (await session.execute(select(Project).order_by(Project.id.desc()))).scalar_one()

    assert response.status_code == 200
    start_pipeline_mock.assert_called_once_with(project.id, video_prompt_language="en")


@pytest.mark.asyncio
async def test_list_projects(client):
    response = await client.get("/projects/")
    assert response.status_code == 200
    assert "影片生成專案" in response.text


@pytest.mark.asyncio
async def test_run_generation_rewrites_long_audio_before_video(db_engine, monkeypatch, tmp_path):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project_id = await _create_project_with_scenes(
        session_factory,
        [{"narration_text": "原始過長旁白", "video_prompt": "Original prompt"}],
    )

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr(pipeline, "async_session", session_factory)

    async def fake_generate_narration(text, output_path):
        durations = {
            "原始過長旁白": 21.2,
            "精簡後旁白": 11.0,
        }
        _write_silent_wav(output_path, durations[text])
        return output_path

    async def fake_generate_video(prompt, output_path, **kwargs):
        output_path.write_bytes(prompt.encode("utf-8"))
        return output_path

    def fake_extract_last_frame(video_path, output_path, **kwargs):
        output_path.write_bytes(b"frame")
        return output_path

    rewrite_mock = AsyncMock(
        return_value=[
            {
                "narration_text": "精簡後旁白",
                "video_prompt": "Updated prompt",
                "duration_estimate": 6.0,
            }
        ]
    )

    generate_video_mock = AsyncMock(side_effect=fake_generate_video)
    extract_last_frame_mock = Mock(side_effect=fake_extract_last_frame)

    monkeypatch.setattr(pipeline, "generate_narration", fake_generate_narration)
    monkeypatch.setattr(pipeline, "rewrite_or_split_scene", rewrite_mock)
    monkeypatch.setattr(pipeline, "generate_video", generate_video_mock)
    monkeypatch.setattr(pipeline, "extract_last_frame", extract_last_frame_mock)
    stitch_videos_mock = Mock()
    monkeypatch.setattr(pipeline, "stitch_videos", stitch_videos_mock)

    await pipeline.run_generation(project_id)

    async with session_factory() as session:
        project = await session.get(Project, project_id)
        result = await session.execute(
            select(ScenePrompt)
            .where(ScenePrompt.project_id == project_id)
            .order_by(ScenePrompt.sequence_order)
        )
        scenes = list(result.scalars().all())
        video_result = await session.execute(select(Video).where(Video.scene_prompt_id == scenes[0].id))
        video = video_result.scalar_one()

    assert project is not None
    assert project.status == ProjectStatus.COMPLETED
    assert project.final_video_path is None
    assert len(scenes) == 1
    assert scenes[0].narration_text == "精簡後旁白"
    assert scenes[0].video_prompt == "Updated prompt"
    assert scenes[0].status == SceneStatus.COMPLETED
    assert scenes[0].duration_estimate == pytest.approx(11.0, rel=0.01)
    assert video.audio_path is not None and video.audio_path.endswith("narration.wav")
    assert rewrite_mock.await_count == 1
    assert generate_video_mock.await_count == 1
    stitch_videos_mock.assert_not_called()
    assert generate_video_mock.await_args.args[0] == "Updated prompt"
    assert generate_video_mock.await_args.kwargs["duration_seconds"] == 12
    assert extract_last_frame_mock.call_args.kwargs["effective_duration_seconds"] == pytest.approx(
        11.0,
        rel=0.01,
    )


@pytest.mark.asyncio
async def test_run_generation_splits_scene_and_resequences_following_scenes(
    db_engine,
    monkeypatch,
    tmp_path,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project_id = await _create_project_with_scenes(
        session_factory,
        [
            {"narration_text": "過長需拆分", "video_prompt": "Scene one prompt"},
            {"narration_text": "第二場旁白", "video_prompt": "Scene two prompt"},
        ],
    )

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr(pipeline, "async_session", session_factory)

    async def fake_generate_narration(text, output_path):
        durations = {
            "過長需拆分": 21.5,
            "拆分一": 6.0,
            "拆分二": 8.5,
            "第二場旁白": 10.0,
        }
        _write_silent_wav(output_path, durations[text])
        return output_path

    async def fake_generate_video(prompt, output_path, **kwargs):
        output_path.write_bytes(prompt.encode("utf-8"))
        return output_path

    def fake_extract_last_frame(video_path, output_path, **kwargs):
        output_path.write_bytes(b"frame")
        return output_path

    rewrite_mock = AsyncMock(
        return_value=[
            {
                "narration_text": "拆分一",
                "video_prompt": "Split prompt one",
                "duration_estimate": 3.0,
            },
            {
                "narration_text": "拆分二",
                "video_prompt": "Split prompt two",
                "duration_estimate": 3.0,
            },
        ]
    )
    generate_video_mock = AsyncMock(side_effect=fake_generate_video)
    extract_last_frame_mock = Mock(side_effect=fake_extract_last_frame)

    monkeypatch.setattr(pipeline, "generate_narration", fake_generate_narration)
    monkeypatch.setattr(pipeline, "rewrite_or_split_scene", rewrite_mock)
    monkeypatch.setattr(pipeline, "generate_video", generate_video_mock)
    monkeypatch.setattr(pipeline, "extract_last_frame", extract_last_frame_mock)
    stitch_videos_mock = Mock()
    monkeypatch.setattr(pipeline, "stitch_videos", stitch_videos_mock)

    await pipeline.run_generation(project_id)

    async with session_factory() as session:
        project = await session.get(Project, project_id)
        result = await session.execute(
            select(ScenePrompt)
            .where(ScenePrompt.project_id == project_id)
            .order_by(ScenePrompt.sequence_order)
        )
        scenes = list(result.scalars().all())
        videos = list((await session.execute(select(Video))).scalars().all())

    assert project is not None
    assert project.status == ProjectStatus.COMPLETED
    assert [scene.narration_text for scene in scenes] == ["拆分一", "拆分二", "第二場旁白"]
    assert [scene.sequence_order for scene in scenes] == [0, 1, 2]
    assert [scene.status for scene in scenes] == [
        SceneStatus.COMPLETED,
        SceneStatus.COMPLETED,
        SceneStatus.COMPLETED,
    ]
    assert len(videos) == 3
    assert rewrite_mock.await_count == 1
    assert generate_video_mock.await_count == 3
    stitch_videos_mock.assert_not_called()
    assert [
        call.kwargs["duration_seconds"] for call in generate_video_mock.await_args_list
    ] == [8, 12, 12]
    assert [
        call.kwargs["effective_duration_seconds"]
        for call in extract_last_frame_mock.call_args_list
    ] == pytest.approx([6.0, 8.5, 10.0], rel=0.01)


@pytest.mark.asyncio
async def test_run_scene_regeneration_refreshes_scene_media_and_clears_final_video(
    db_engine,
    monkeypatch,
    tmp_path,
):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    project_id, scene_id = await _create_project_with_completed_scene_and_video(
        session_factory,
        final_video_path=str(tmp_path / "placeholder" / "old_final_video.mp4"),
    )

    project_dir = tmp_path / str(project_id)
    old_final_path = project_dir / "final_video.mp4"
    old_final_path.parent.mkdir(parents=True, exist_ok=True)
    old_final_path.write_bytes(b"stale-final")

    scene_dir = project_dir / "scene_000"
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "video.mp4").write_bytes(b"old-video")
    _write_silent_wav(scene_dir / "narration.wav", 5.0)
    (scene_dir / "last_frame.png").write_bytes(b"old-frame")

    monkeypatch.setattr(pipeline, "async_session", session_factory)

    async def fake_generate_narration(text, output_path):
        _write_silent_wav(output_path, 7.5)
        return output_path

    async def fake_generate_video(prompt, output_path, **kwargs):
        output_path.write_bytes(b"new-video")
        return output_path

    def fake_extract_last_frame(video_path, output_path, **kwargs):
        output_path.write_bytes(b"new-frame")
        return output_path

    generate_video_mock = AsyncMock(side_effect=fake_generate_video)

    monkeypatch.setattr(pipeline, "generate_narration", fake_generate_narration)
    monkeypatch.setattr(pipeline, "generate_video", generate_video_mock)
    monkeypatch.setattr(pipeline, "extract_last_frame", Mock(side_effect=fake_extract_last_frame))

    await pipeline.run_scene_regeneration(project_id, scene_id)

    async with session_factory() as session:
        project = await session.get(Project, project_id)
        scene = await session.get(ScenePrompt, scene_id)
        result = await session.execute(select(Video).where(Video.scene_prompt_id == scene_id))
        video = result.scalar_one()

    assert project is not None
    assert scene is not None
    assert project.status == ProjectStatus.COMPLETED
    assert project.final_video_path is None
    assert not old_final_path.exists()
    assert scene.status == SceneStatus.COMPLETED
    assert scene.duration_estimate == pytest.approx(7.5, rel=0.01)
    assert video.video_path is not None and video.video_path.endswith("scene_000\\video.mp4")
    assert video.audio_path is not None and video.audio_path.endswith("scene_000\\narration.wav")
    assert video.last_frame_path is not None and video.last_frame_path.endswith("scene_000\\last_frame.png")
    assert generate_video_mock.await_count == 1
    assert generate_video_mock.await_args.kwargs["reference_image_path"] is None


@pytest.mark.asyncio
async def test_run_stitching_uses_latest_scene_media(db_engine, monkeypatch, tmp_path):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.COMPLETED)
        session.add(project)
        await session.flush()

        for sequence_order in range(2):
            scene = ScenePrompt(
                project_id=project.id,
                sequence_order=sequence_order,
                narration_text=f"場景 {sequence_order + 1}",
                video_prompt=f"Prompt {sequence_order + 1}",
                duration_estimate=6.0,
                status=SceneStatus.COMPLETED,
            )
            session.add(scene)
            await session.flush()

            scene_dir = tmp_path / str(project.id) / f"scene_{sequence_order:03d}"
            scene_dir.mkdir(parents=True, exist_ok=True)
            video_path = scene_dir / "video.mp4"
            audio_path = scene_dir / "narration.wav"
            frame_path = scene_dir / "last_frame.png"
            video_path.write_bytes(f"video-{sequence_order}".encode("utf-8"))
            _write_silent_wav(audio_path, 6.0)
            frame_path.write_bytes(b"frame")

            session.add(
                Video(
                    scene_prompt_id=scene.id,
                    video_path=str(video_path),
                    audio_path=str(audio_path),
                    last_frame_path=str(frame_path),
                )
            )

        await session.commit()
        project_id = project.id

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr(pipeline, "async_session", session_factory)

    stitched_inputs = {}

    def fake_stitch_videos(video_paths, audio_paths, output_path, *, scene_durations_seconds=None):
        stitched_inputs["video_paths"] = [path.name for path in video_paths]
        stitched_inputs["audio_paths"] = [path.name for path in audio_paths]
        stitched_inputs["scene_durations_seconds"] = scene_durations_seconds
        output_path.write_bytes(b"final-video")
        return output_path

    monkeypatch.setattr(pipeline, "stitch_videos", fake_stitch_videos)

    await pipeline.run_stitching(project_id)

    async with session_factory() as session:
        project = await session.get(Project, project_id)

    assert project is not None
    assert project.status == ProjectStatus.COMPLETED
    assert project.final_video_path is not None and project.final_video_path.endswith("final_video.mp4")
    assert stitched_inputs == {
        "video_paths": ["video.mp4", "video.mp4"],
        "audio_paths": ["narration.wav", "narration.wav"],
        "scene_durations_seconds": [8, 8],
    }
    assert (tmp_path / str(project_id) / "final_video.mp4").exists()
