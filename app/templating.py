from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.prompt_generator import CHARS_PER_SECOND
from app.video_timing import (
	estimate_max_narration_chars,
	get_max_scene_duration_seconds,
	resolve_video_duration_seconds,
)


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["resolve_video_duration_seconds"] = resolve_video_duration_seconds
templates.env.globals["narration_chars_per_second"] = CHARS_PER_SECOND
templates.env.globals["max_narration_chars"] = estimate_max_narration_chars(CHARS_PER_SECOND)
templates.env.globals["max_scene_duration_seconds"] = get_max_scene_duration_seconds()
templates.env.globals["max_scenes_per_project"] = settings.max_scenes_per_project
templates.env.globals["static_asset_version"] = "20260323-2"