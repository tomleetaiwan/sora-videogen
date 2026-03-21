import logging
from pathlib import Path

import ffmpeg

logger = logging.getLogger(__name__)


def stitch_videos(
    video_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    """使用 ffmpeg 將影片與對應音軌串接起來。

    每支影片都會配上相對應的旁白音軌，最後輸出為單一連續影片檔。

    參數：
        video_paths：依順序排列的影片分段檔案清單。
        audio_paths：依相同順序排列的旁白音訊檔案清單。
        output_path：最終串接影片的儲存位置。

    回傳最終影片的路徑。
    """
    if len(video_paths) != len(audio_paths):
        raise ValueError(
            f"Mismatched counts: {len(video_paths)} videos, {len(audio_paths)} audio files"
        )

    if not video_paths:
        raise ValueError("No videos to stitch")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 作法：先把每段音訊疊到對應影片上，再串接所有分段
    segments = []
    for i, (vpath, apath) in enumerate(zip(video_paths, audio_paths)):
        video_input = ffmpeg.input(str(vpath))
        audio_input = ffmpeg.input(str(apath))
        # 將影片與旁白音軌合併
        segment = ffmpeg.output(
            video_input.video,
            audio_input.audio,
            str(output_path.parent / f"_segment_{i}.mp4"),
            vcodec="copy",
            acodec="aac",
            shortest=None,
        ).overwrite_output()
        segment.run(quiet=True)
        segments.append(output_path.parent / f"_segment_{i}.mp4")

    # 寫出 concat 清單檔
    concat_list_path = output_path.parent / "_concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{seg.resolve()}'\n")

    # 串接所有分段
    (
        ffmpeg.input(str(concat_list_path), format="concat", safe=0)
        .output(str(output_path), c="copy")
        .overwrite_output()
        .run(quiet=True)
    )

    # 清理暫存檔案
    for seg in segments:
        seg.unlink(missing_ok=True)
    concat_list_path.unlink(missing_ok=True)

    logger.info("Stitched %d segments into %s", len(segments), output_path)
    return output_path
