# -*- coding: utf-8 -*-
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, Literal, Dict, Any

try:
    import pymediainfo  # type: ignore
except Exception:
    pymediainfo = None  # type: ignore

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402


def _coerce_numeric(value: object, *, field_name: str) -> float:
    if value is None:
        raise ValueError(f"Media field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"Media field '{field_name}' is empty")
        return float(stripped)
    raise TypeError(f"Unsupported type for '{field_name}': {type(value)}")


def _coerce_int(value: object, *, field_name: str) -> int:
    return int(round(_coerce_numeric(value, field_name=field_name)))


def _pick_first_numeric(*candidates: object, field_name: str) -> float:
    last_error: Optional[Exception] = None
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return _coerce_numeric(candidate, field_name=field_name)
        except (TypeError, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError(f"Media field '{field_name}' missing")


class CropSettings:
    upper_left_x: float
    upper_left_y: float
    upper_right_x: float
    upper_right_y: float
    lower_left_x: float
    lower_left_y: float
    lower_right_x: float
    lower_right_y: float

    def __init__(
        self,
        *,
        upper_left_x: float = 0.0,
        upper_left_y: float = 0.0,
        upper_right_x: float = 1.0,
        upper_right_y: float = 0.0,
        lower_left_x: float = 0.0,
        lower_left_y: float = 1.0,
        lower_right_x: float = 1.0,
        lower_right_y: float = 1.0,
    ):
        self.upper_left_x = upper_left_x
        self.upper_left_y = upper_left_y
        self.upper_right_x = upper_right_x
        self.upper_right_y = upper_right_y
        self.lower_left_x = lower_left_x
        self.lower_left_y = lower_left_y
        self.lower_right_x = lower_right_x
        self.lower_right_y = lower_right_y

    def export_json(self) -> Dict[str, Any]:
        return {
            "upper_left_x": self.upper_left_x,
            "upper_left_y": self.upper_left_y,
            "upper_right_x": self.upper_right_x,
            "upper_right_y": self.upper_right_y,
            "lower_left_x": self.lower_left_x,
            "lower_left_y": self.lower_left_y,
            "lower_right_x": self.lower_right_x,
            "lower_right_y": self.lower_right_y,
        }


class VideoMaterial:
    """Local video or photo material for CapCut PC timeline (with automatic FFmpeg/PIL fallback)."""

    material_id: str
    local_material_id: str
    material_name: str
    path: str
    duration: int
    height: int
    width: int
    crop_settings: CropSettings
    material_type: Literal["video", "photo"]

    def __init__(
        self,
        path: str,
        material_name: Optional[str] = None,
        crop_settings: CropSettings = CropSettings(),
    ):
        path = os.path.abspath(path)
        postfix = os.path.splitext(path)[1].lower()
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        self.material_name = material_name if material_name else os.path.basename(path)
        self.material_id = uuid.uuid4().hex
        self.path = path
        self.crop_settings = crop_settings
        self.local_material_id = ""

        # Try pymediainfo first if available, otherwise use bins.probe_media + PIL
        if pymediainfo is not None and pymediainfo.MediaInfo.can_parse():
            info = pymediainfo.MediaInfo.parse(path, mediainfo_options={"File_TestContinuousFileNames": "0"})
            if len(info.video_tracks):
                video_track = info.video_tracks[0]
                general_track = info.general_tracks[0] if len(info.general_tracks) else None
                self.material_type = "video"
                self.duration = int(
                    round(
                        _pick_first_numeric(
                            getattr(video_track, "duration", None),
                            getattr(general_track, "duration", None) if general_track is not None else None,
                            field_name="video duration",
                        )
                        * 1e3
                    )
                )
                self.width = _coerce_int(getattr(video_track, "width", None), field_name="video width")
                self.height = _coerce_int(getattr(video_track, "height", None), field_name="video height")
                return
            elif len(info.image_tracks):
                self.material_type = "photo"
                self.duration = 10800000000
                self.width = _coerce_int(getattr(info.image_tracks[0], "width", None), field_name="image width")
                self.height = _coerce_int(getattr(info.image_tracks[0], "height", None), field_name="image height")
                return

        # Self-sufficient fallback using PIL (for images) and bins.probe_media (for videos)
        if postfix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
            self.material_type = "photo"
            self.duration = 10800000000
            self.width = int(w)
            self.height = int(h)
            return

        meta = bins.probe_media(path)
        if not meta["has_video"] or meta["duration"] <= 0:
            raise ValueError(f"Input file {path} has no valid video track (meta={meta})")
        self.material_type = "video"
        self.duration = int(round(float(meta["duration"]) * 1_000_000))
        self.width = int(meta["width"])
        self.height = int(meta["height"])

    def export_json(self) -> Dict[str, Any]:
        return {
            "audio_fade": None,
            "category_id": "",
            "category_name": "local",
            "check_flag": 63487,
            "crop": self.crop_settings.export_json(),
            "crop_ratio": "free",
            "crop_scale": 1.0,
            "duration": self.duration,
            "height": self.height,
            "id": self.material_id,
            "local_material_id": self.local_material_id,
            "material_id": self.material_id,
            "material_name": self.material_name,
            "media_path": "",
            "path": self.path,
            "type": self.material_type,
            "width": self.width,
        }


class AudioMaterial:
    """Local audio material for CapCut PC timeline (with automatic FFmpeg/wave fallback)."""

    material_id: str
    material_name: str
    path: str
    duration: int

    def __init__(self, path: str, material_name: Optional[str] = None):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Audio file not found: {path}")

        self.material_name = material_name if material_name else os.path.basename(path)
        self.material_id = uuid.uuid4().hex
        self.path = path

        if pymediainfo is not None and pymediainfo.MediaInfo.can_parse():
            info = pymediainfo.MediaInfo.parse(path)
            if len(info.video_tracks):
                raise ValueError(f"AudioMaterial cannot contain a video stream: {path}")
            if len(info.audio_tracks):
                audio_track = info.audio_tracks[0]
                general_track = info.general_tracks[0] if len(info.general_tracks) else None
                self.duration = int(
                    round(
                        _pick_first_numeric(
                            getattr(audio_track, "duration", None),
                            getattr(general_track, "duration", None) if general_track is not None else None,
                            field_name="audio duration",
                        )
                        * 1e3
                    )
                )
                return

        meta = bins.probe_media(path)
        if meta["has_video"]:
            raise ValueError(f"AudioMaterial cannot contain a video stream: {path}")
        if not meta["has_audio"] or meta["duration"] <= 0:
            raise ValueError(f"Audio file {path} has no valid audio track (meta={meta})")
        self.duration = int(round(float(meta["duration"]) * 1_000_000))

    def export_json(self) -> Dict[str, Any]:
        return {
            "app_id": 0,
            "category_id": "",
            "category_name": "local",
            "check_flag": 3,
            "copyright_limit_type": "none",
            "duration": self.duration,
            "effect_id": "",
            "formula_id": "",
            "id": self.material_id,
            "local_material_id": self.material_id,
            "music_id": self.material_id,
            "name": self.material_name,
            "path": self.path,
            "source_platform": 0,
            "type": "extract_music",
            "wave_points": [],
        }
