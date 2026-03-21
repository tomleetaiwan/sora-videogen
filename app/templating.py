from fastapi.templating import Jinja2Templates

from app.video_timing import resolve_video_duration_seconds


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["resolve_video_duration_seconds"] = resolve_video_duration_seconds