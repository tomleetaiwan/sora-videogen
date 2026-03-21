"""相容匯出層：保留既有匯入路徑，實作已搬至 media_backend。"""

from app.services.media_backend import stitch_videos

__all__ = ["stitch_videos"]
