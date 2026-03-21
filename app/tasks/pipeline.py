import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus, Video
from app.prompt_language import DEFAULT_VIDEO_PROMPT_LANGUAGE
from app.services.prompt_generator import generate_scene_prompts, rewrite_or_split_scene
from app.services.scraper import scrape_url
from app.services.summarizer import summarize_content
from app.services.tts import generate_narration, get_audio_duration_seconds
from app.services.video_generator import extract_last_frame, generate_video
from app.services.video_stitcher import stitch_videos
from app.video_timing import get_max_scene_duration_seconds, resolve_video_duration_seconds

logger = logging.getLogger(__name__)
MAX_SCENE_ADJUSTMENT_ATTEMPTS = 3
MAX_SCENE_DURATION_SECONDS = get_max_scene_duration_seconds()

# 追蹤執行中的任務，以便查詢狀態
_running_tasks: dict[int, asyncio.Task] = {}


def get_task_for_project(project_id: int) -> asyncio.Task | None:
    return _running_tasks.get(project_id)


async def _update_project_status(
    session: AsyncSession, project_id: int, status: ProjectStatus, error: str | None = None
) -> None:
    project = await session.get(Project, project_id)
    if project:
        project.status = status
        project.error_message = error
        await session.commit()


async def _load_project_scenes(session: AsyncSession, project_id: int) -> list[ScenePrompt]:
    result = await session.execute(
        select(ScenePrompt)
        .options(selectinload(ScenePrompt.video))
        .where(ScenePrompt.project_id == project_id)
        .order_by(ScenePrompt.sequence_order)
    )
    return list(result.scalars().all())


def _get_scene_dir(project_dir: Path, scene: ScenePrompt) -> Path:
    return project_dir / f"scene_{scene.sequence_order:03d}"


def _get_scene_audio_path(project_dir: Path, scene: ScenePrompt) -> Path:
    return _get_scene_dir(project_dir, scene) / "narration.wav"


def _get_scene_video_path(project_dir: Path, scene: ScenePrompt) -> Path:
    return _get_scene_dir(project_dir, scene) / "video.mp4"


def _get_scene_frame_path(project_dir: Path, scene: ScenePrompt) -> Path:
    return _get_scene_dir(project_dir, scene) / "last_frame.png"


def _resolve_scene_asset_path(stored_path: str | None, fallback_path: Path) -> Path | None:
    if stored_path:
        candidate = Path(stored_path)
        if candidate.exists():
            return candidate

    if fallback_path.exists():
        return fallback_path

    return None


def _resolve_audio_path(project_dir: Path, scene: ScenePrompt) -> Path | None:
    return _resolve_scene_asset_path(
        scene.video.audio_path if scene.video else None,
        _get_scene_audio_path(project_dir, scene),
    )


def _resolve_video_path(project_dir: Path, scene: ScenePrompt) -> Path | None:
    return _resolve_scene_asset_path(
        scene.video.video_path if scene.video else None,
        _get_scene_video_path(project_dir, scene),
    )


def _resolve_frame_path(project_dir: Path, scene: ScenePrompt) -> Path | None:
    return _resolve_scene_asset_path(
        scene.video.last_frame_path if scene.video else None,
        _get_scene_frame_path(project_dir, scene),
    )


def _scene_has_generated_media(project_dir: Path, scene: ScenePrompt) -> bool:
    return (
        scene.status == SceneStatus.COMPLETED
        and _resolve_audio_path(project_dir, scene) is not None
        and _resolve_video_path(project_dir, scene) is not None
    )


async def _prepare_project_for_media_refresh(
    session: AsyncSession,
    project_id: int,
    project_dir: Path,
    *,
    status: ProjectStatus,
) -> None:
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    if project.final_video_path:
        Path(project.final_video_path).unlink(missing_ok=True)

    (project_dir / "final_video.mp4").unlink(missing_ok=True)
    project.final_video_path = None
    project.status = status
    project.error_message = None
    await session.commit()


async def _finalize_project_after_partial_generation(
    session: AsyncSession,
    project_id: int,
) -> None:
    project = await session.get(Project, project_id)
    if project is None:
        return

    scenes = await _load_project_scenes(session, project_id)
    if scenes and all(scene.status == SceneStatus.COMPLETED for scene in scenes):
        project.status = ProjectStatus.COMPLETED
    elif any(scene.status == SceneStatus.FAILED for scene in scenes):
        project.status = ProjectStatus.FAILED
    else:
        project.status = ProjectStatus.PROMPTS_READY

    project.error_message = None
    await session.commit()


async def _replace_scene_with_rewrites(
    session: AsyncSession,
    project_id: int,
    scene_id: int,
    rewritten_scenes: list[dict],
) -> None:
    ordered_scenes = await _load_project_scenes(session, project_id)
    current_index = next(
        (index for index, candidate in enumerate(ordered_scenes) if candidate.id == scene_id),
        None,
    )
    if current_index is None:
        raise ValueError(f"Scene {scene_id} no longer exists")

    replacement_count = len(rewritten_scenes)
    total_scene_count = len(ordered_scenes) - 1 + replacement_count
    if total_scene_count > settings.max_scenes_per_project:
        raise ValueError(
            "Cannot split scene because it would exceed the maximum project scene count"
        )

    # 第一個重寫後的分鏡沿用原本那筆資料，確保既有外鍵參照維持穩定，
    # 其餘拆分出來的新分鏡再插到後面。
    current_scene = ordered_scenes[current_index]
    first_rewrite = rewritten_scenes[0]
    current_scene.narration_text = first_rewrite["narration_text"]
    current_scene.video_prompt = first_rewrite["video_prompt"]
    current_scene.duration_estimate = first_rewrite.get("duration_estimate")
    current_scene.status = SceneStatus.PENDING

    inserted_count = 0
    for offset, rewritten_scene in enumerate(rewritten_scenes[1:], start=1):
        session.add(
            ScenePrompt(
                project_id=project_id,
                sequence_order=current_scene.sequence_order + offset,
                narration_text=rewritten_scene["narration_text"],
                video_prompt=rewritten_scene["video_prompt"],
                duration_estimate=rewritten_scene.get("duration_estimate"),
                status=SceneStatus.PENDING,
            )
        )
        inserted_count += 1

    # 重新編號後續分鏡，讓生成順序仍對應更新後的敘事流程。
    next_sequence_order = current_scene.sequence_order + inserted_count + 1
    for later_scene in ordered_scenes[current_index + 1 :]:
        later_scene.sequence_order = next_sequence_order
        next_sequence_order += 1

    await session.flush()
    await session.commit()


async def _prepare_scene_audio(
    session: AsyncSession,
    project_id: int,
    scene_index: int,
    project_dir: Path,
) -> tuple[ScenePrompt, Path, float]:
    adjustment_attempts = 0

    while True:
        scenes = await _load_project_scenes(session, project_id)
        if scene_index >= len(scenes):
            raise ValueError("Scene queue changed unexpectedly during generation")

        scene = scenes[scene_index]
        scene_dir = _get_scene_dir(project_dir, scene)
        scene_dir.mkdir(parents=True, exist_ok=True)

        scene.status = SceneStatus.GENERATING_AUDIO
        await session.commit()

        audio_path = _get_scene_audio_path(project_dir, scene)
        await generate_narration(scene.narration_text, audio_path)

        actual_duration = get_audio_duration_seconds(audio_path)
        scene.duration_estimate = actual_duration

        # 硬性上限以合成後的實際音訊時長判定，而不是文字長度估算值。
        if actual_duration <= MAX_SCENE_DURATION_SECONDS:
            await session.commit()
            return scene, audio_path, actual_duration

        logger.warning(
            "Scene %d narration measured %.2f seconds, exceeding the hard %d-second limit",
            scene.sequence_order,
            actual_duration,
            MAX_SCENE_DURATION_SECONDS,
        )

        if adjustment_attempts >= MAX_SCENE_ADJUSTMENT_ATTEMPTS:
            scene.status = SceneStatus.FAILED
            await session.commit()
            raise ValueError(
                "Scene narration still exceeds the hard duration limit after automatic "
                f"rewrite attempts: {scene.sequence_order + 1}"
            )

        rewritten_scenes = await rewrite_or_split_scene(
            scene.narration_text,
            scene.video_prompt,
            actual_duration_seconds=actual_duration,
            max_duration_seconds=MAX_SCENE_DURATION_SECONDS,
            max_scenes=settings.max_scenes_per_project - len(scenes) + 1,
        )
        # 取代分鏡時可能會插入額外資料列，因此呼叫端必須重新載入分鏡清單。
        await _replace_scene_with_rewrites(session, project_id, scene.id, rewritten_scenes)
        adjustment_attempts += 1


async def _generate_scene_from_index(
    session: AsyncSession,
    project_id: int,
    scene_index: int,
    project_dir: Path,
    *,
    reference_image_path: Path | None,
) -> Path:
    scene: ScenePrompt | None = None
    video_record: Video | None = None

    try:
        scene, audio_path, actual_duration = await _prepare_scene_audio(
            session,
            project_id,
            scene_index,
            project_dir,
        )
        scene_dir = _get_scene_dir(project_dir, scene)
        video_duration_seconds = resolve_video_duration_seconds(actual_duration)

        scene.status = SceneStatus.GENERATING_VIDEO
        video_record = scene.video
        if video_record is None:
            video_record = Video(scene_prompt_id=scene.id)
            session.add(video_record)
            await session.flush()

        video_record.audio_path = str(audio_path)
        video_record.video_path = None
        video_record.last_frame_path = None
        video_record.error_message = None
        await session.commit()

        video_path = _get_scene_video_path(project_dir, scene)
        await generate_video(
            scene.video_prompt,
            video_path,
            reference_image_path=reference_image_path,
            duration_seconds=video_duration_seconds,
        )
        video_record.video_path = str(video_path)

        frame_path = _get_scene_frame_path(project_dir, scene)
        extract_last_frame(
            video_path,
            frame_path,
            effective_duration_seconds=actual_duration,
        )
        video_record.last_frame_path = str(frame_path)

        scene.status = SceneStatus.COMPLETED
        await session.commit()
        return frame_path

    except Exception as e:
        scene_label = scene.sequence_order if scene is not None else scene_index
        logger.exception("Failed generating scene %d", scene_label)
        if scene is not None:
            scene.status = SceneStatus.FAILED
        if video_record is not None:
            video_record.error_message = str(e)
        await session.commit()
        raise


async def _generate_project_scenes(
    session: AsyncSession,
    project_id: int,
    project_dir: Path,
) -> None:
    scenes = await _load_project_scenes(session, project_id)
    if not scenes:
        raise ValueError("No scenes found for project")

    last_frame_path: Path | None = None
    scene_index = 0

    while scene_index < len(scenes):
        current_scene = scenes[scene_index]
        if _scene_has_generated_media(project_dir, current_scene):
            last_frame_path = _resolve_frame_path(project_dir, current_scene)
            scene_index += 1
            continue

        last_frame_path = await _generate_scene_from_index(
            session,
            project_id,
            scene_index,
            project_dir,
            reference_image_path=last_frame_path,
        )
        scene_index += 1
        scenes = await _load_project_scenes(session, project_id)


async def _regenerate_scene_slice(
    session: AsyncSession,
    project_id: int,
    scene_id: int,
    project_dir: Path,
) -> None:
    scenes = await _load_project_scenes(session, project_id)
    current_index = next(
        (index for index, candidate in enumerate(scenes) if candidate.id == scene_id),
        None,
    )
    if current_index is None:
        raise ValueError(f"Scene {scene_id} no longer exists")

    next_scene_id = scenes[current_index + 1].id if current_index + 1 < len(scenes) else None
    reference_image_path = (
        _resolve_frame_path(project_dir, scenes[current_index - 1]) if current_index > 0 else None
    )
    scene_index = current_index

    while True:
        reference_image_path = await _generate_scene_from_index(
            session,
            project_id,
            scene_index,
            project_dir,
            reference_image_path=reference_image_path,
        )
        scenes = await _load_project_scenes(session, project_id)
        next_index = scene_index + 1

        if next_scene_id is None:
            if next_index >= len(scenes):
                break
        else:
            if next_index >= len(scenes) or scenes[next_index].id == next_scene_id:
                break

        scene_index += 1


def _collect_scene_media_paths(
    scenes: list[ScenePrompt],
    project_dir: Path,
) -> tuple[list[Path], list[Path]]:
    video_paths: list[Path] = []
    audio_paths: list[Path] = []

    for scene in scenes:
        video_path = _resolve_video_path(project_dir, scene)
        audio_path = _resolve_audio_path(project_dir, scene)
        if video_path is None or audio_path is None:
            raise ValueError(f"Scene {scene.sequence_order + 1} is missing generated media")

        video_paths.append(video_path)
        audio_paths.append(audio_path)

    return video_paths, audio_paths


async def run_pipeline(
    project_id: int,
    *,
    video_prompt_language: str = DEFAULT_VIDEO_PROMPT_LANGUAGE,
) -> None:
    """執行專案完整的影片生成前置流程。

    流程：擷取 → 摘要 → 生成提示詞 →（各分鏡 TTS + 影片）→ 串接
    """
    project_dir = settings.media_dir / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                logger.error("Project %d not found", project_id)
                return

            # 步驟 1：擷取內容
            await _update_project_status(session, project_id, ProjectStatus.SCRAPING)
            raw_content = await scrape_url(str(project.url))
            project.raw_content = raw_content
            await session.commit()

            # 步驟 2：產生摘要
            await _update_project_status(session, project_id, ProjectStatus.SUMMARIZING)
            summary = await summarize_content(raw_content)
            project.summary = summary
            await session.commit()

            # 步驟 3：生成分鏡提示詞
            scenes_data = await generate_scene_prompts(
                summary,
                video_prompt_language=video_prompt_language,
            )
            for i, scene_data in enumerate(scenes_data):
                scene = ScenePrompt(
                    project_id=project_id,
                    sequence_order=i,
                    narration_text=scene_data["narration_text"],
                    video_prompt=scene_data["video_prompt"],
                    duration_estimate=scene_data.get("duration_estimate"),
                )
                session.add(scene)
            await session.commit()

            project.status = ProjectStatus.PROMPTS_READY
            await session.commit()

    except Exception as e:
        logger.exception("Pipeline failed during preparation for project %d", project_id)
        async with async_session() as session:
            await _update_project_status(session, project_id, ProjectStatus.FAILED, str(e))
    finally:
        _running_tasks.pop(project_id, None)


async def run_generation(project_id: int) -> None:
    """為專案生成尚未完成的分鏡媒體，但不串接最終影片。"""
    project_dir = settings.media_dir / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_session() as session:
            await _prepare_project_for_media_refresh(
                session,
                project_id,
                project_dir,
                status=ProjectStatus.GENERATING,
            )
            await _generate_project_scenes(session, project_id, project_dir)

            project = await session.get(Project, project_id)
            if project:
                project.status = ProjectStatus.COMPLETED
                project.error_message = None
                await session.commit()

    except Exception as e:
        logger.exception("Generation failed for project %d", project_id)
        async with async_session() as session:
            await _update_project_status(session, project_id, ProjectStatus.FAILED, str(e))
    finally:
        _running_tasks.pop(project_id, None)


async def run_scene_regeneration(project_id: int, scene_id: int) -> None:
    """重新生成單一分鏡，以及自動拆分後的後續分鏡。"""
    project_dir = settings.media_dir / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_session() as session:
            await _prepare_project_for_media_refresh(
                session,
                project_id,
                project_dir,
                status=ProjectStatus.GENERATING,
            )
            await _regenerate_scene_slice(session, project_id, scene_id, project_dir)
            await _finalize_project_after_partial_generation(session, project_id)

    except Exception as e:
        logger.exception("Scene regeneration failed for project %d scene %d", project_id, scene_id)
        async with async_session() as session:
            await _update_project_status(session, project_id, ProjectStatus.FAILED, str(e))
    finally:
        _running_tasks.pop(project_id, None)


async def run_stitching(project_id: int) -> None:
    """將最新生成的分鏡片段串接成最終影片。"""
    project_dir = settings.media_dir / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_session() as session:
            scenes = await _load_project_scenes(session, project_id)
            if not scenes:
                raise ValueError("No scenes found for project")
            if any(scene.status != SceneStatus.COMPLETED for scene in scenes):
                raise ValueError("All scenes must finish generating before stitching")

            await _prepare_project_for_media_refresh(
                session,
                project_id,
                project_dir,
                status=ProjectStatus.STITCHING,
            )
            scenes = await _load_project_scenes(session, project_id)
            video_paths, audio_paths = _collect_scene_media_paths(scenes, project_dir)

            final_path = project_dir / "final_video.mp4"
            stitch_videos(video_paths, audio_paths, final_path)

            project = await session.get(Project, project_id)
            if project:
                project.final_video_path = str(final_path)
                project.status = ProjectStatus.COMPLETED
                project.error_message = None
                await session.commit()

    except Exception as e:
        logger.exception("Stitching failed for project %d", project_id)
        async with async_session() as session:
            await _update_project_status(session, project_id, ProjectStatus.FAILED, str(e))
    finally:
        _running_tasks.pop(project_id, None)


def start_pipeline(
    project_id: int,
    *,
    video_prompt_language: str = DEFAULT_VIDEO_PROMPT_LANGUAGE,
) -> asyncio.Task:
    """以背景任務方式啟動前置流程。"""
    task = asyncio.create_task(
        run_pipeline(
            project_id,
            video_prompt_language=video_prompt_language,
        )
    )
    _running_tasks[project_id] = task
    return task


def start_generation(project_id: int) -> asyncio.Task:
    """以背景任務方式啟動影片生成。"""
    task = asyncio.create_task(run_generation(project_id))
    _running_tasks[project_id] = task
    return task


def start_scene_regeneration(project_id: int, scene_id: int) -> asyncio.Task:
    """以背景任務方式啟動單一分鏡重生。"""
    task = asyncio.create_task(run_scene_regeneration(project_id, scene_id))
    _running_tasks[project_id] = task
    return task


def start_stitching(project_id: int) -> asyncio.Task:
    """以背景任務方式啟動手動影片串接。"""
    task = asyncio.create_task(run_stitching(project_id))
    _running_tasks[project_id] = task
    return task
