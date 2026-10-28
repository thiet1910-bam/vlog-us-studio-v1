# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Binary & Media Probe Resolver (engine/bins.py)
Independent 100% within Vlog-US-Studio-v1.
Handles Windows UTF-8 stdout, FFmpeg/FFprobe discovery, FFmpeg-fallback media probing
(when standalone ffprobe.exe is absent in imageio_ffmpeg), and Windows filter path escaping.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, Optional

ENGINE_DIR = Path(__file__).resolve().parent
STUDIO_ROOT = ENGINE_DIR.parent
SHARED_DIR = STUDIO_ROOT / "shared"
EPISODES_DIR = STUDIO_ROOT / "episodes"
SKILL_DIR = STUDIO_ROOT / ".agent" / "skills" / "vlog-us-studio"
BRAND_DNA_PATH = SKILL_DIR / "config" / "brand_dna_us.json"


def get_episode_paths(episode_id: str) -> Dict[str, Path]:
    """
    Standardized V2 3-Folder (User-Facing) + 1-Hidden-System (Machine-Only) layout:
      1. 1_input/          : User inputs (kich_ban_so_bo.md, video_so_bo.mp4, raw_clips/, voice.mp3)
      2. 2_pha_A_kich_ban/ : Phase A deliverables (1_kich_ban_hoan_chinh.md, 2_loi_dan_elevenlabs.txt, optional 3_broll_bo_sung/)
      3. 3_pha_B_video/    : Phase B deliverables (Video_Hoan_Chinh_<EP>.mp4, Phu_De_YouTube_<EP>.srt, CapCut_Draft/, QA files)
      4. _he_thong/        : Internal machine telemetry, contracts, VO slices, and temporary 3-min clusters
    """
    ep_root = EPISODES_DIR / episode_id
    inp = ep_root / "1_input"
    pha_a = ep_root / "2_pha_A_kich_ban"
    pha_b = ep_root / "3_pha_B_video"
    sys_dir = ep_root / "_he_thong"
    return {
        "episode_root": ep_root,
        # [1] User Input Folder
        "input_dir": inp,
        "rough_script": inp / "kich_ban_so_bo.md",
        "rough_video": inp / "video_so_bo.mp4",
        "raw_clips_dir": inp / "raw_clips",
        "rough_draft_json": inp / "rough_draft_content.json",
        "voice_mp3": inp / "voice.mp3",
        # [2] Phase A User-Facing Output Folder
        "phase_a_dir": pha_a,
        "script_human_md": pha_a / "1_kich_ban_hoan_chinh.md",
        "elevenlabs_txt": pha_a / "2_loi_dan_elevenlabs.txt",
        "broll_approved_dir": pha_a / "3_broll_bo_sung",
        # [3] Phase B User-Facing Output Folder
        "phase_b_dir": pha_b,
        "final_video_mp4": pha_b / f"Video_Hoan_Chinh_{episode_id}.mp4",
        "final_srt": pha_b / f"Phu_De_YouTube_{episode_id}.srt",
        "qa_sheet_png": pha_b / "Anh_Kiem_Duyet_QA.png",
        "qa_report_md": pha_b / "Bao_Cao_Kiem_Duyet.md",
        "capcut_draft_dir": pha_b / "CapCut_Draft",
        # [_] Hidden Machine System Folder
        "sys_dir": sys_dir,
        "scan_dir": sys_dir / "scan",
        "contact_sheets_dir": sys_dir / "scan" / "contact_sheets",
        "conform_map_json": sys_dir / "scan" / "source_conform_map.json",
        "telemetry_json": sys_dir / "scan" / "rough_video_telemetry.json",
        "room_tone_wav": sys_dir / "scan" / "clean_room_tone.wav",
        "contracts_dir": sys_dir / "contracts",
        "director_blueprint_json": sys_dir / "contracts" / "director_blueprint.json",
        "story_memory_json": sys_dir / "contracts" / "story_memory.json",
        "broll_manifest_json": sys_dir / "contracts" / "broll_manifest.json",
        "cluster_plan_json": sys_dir / "contracts" / "cluster_plan.json",
        "timelines_dir": sys_dir / "contracts" / "timelines",
        "vo_slices_dir": sys_dir / "vo_slices",
        "clusters_temp_dir": sys_dir / "clusters_temp",
        "broll_candidates_dir": sys_dir / "broll_candidates",
    }


def init_episode_dirs(episode_id: str) -> Dict[str, Path]:
    """Ensures the clean 3-folder + `_he_thong` directory skeleton exists (never pre-creating `3_broll_bo_sung`)."""
    p = get_episode_paths(episode_id)
    for key in (
        "input_dir",
        "phase_a_dir",
        "phase_b_dir",
        "scan_dir",
        "contracts_dir",
        "vo_slices_dir",
        "clusters_temp_dir",
    ):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def ensure_utf8_stdio() -> None:
    """Prevent UnicodeEncodeError ('charmap') on Windows PowerShell/CMD."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


ensure_utf8_stdio()


def load_brand_dna() -> Dict[str, Any]:
    if BRAND_DNA_PATH.exists():
        return json.loads(BRAND_DNA_PATH.read_text(encoding="utf-8"))
    return {}


_FFMPEG_CACHE: Optional[str] = None
_FFPROBE_CACHE: Optional[str] = None


def resolve_ffmpeg() -> str:
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE and os.path.exists(_FFMPEG_CACHE):
        return _FFMPEG_CACHE

    # 1. Explicit env var
    env_ff = os.environ.get("FFMPEG_BINARY")
    if env_ff and os.path.exists(env_ff):
        _FFMPEG_CACHE = env_ff
        return env_ff

    # 2. System PATH
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_CACHE = found
        return found

    # 3. imageio_ffmpeg bundled binary (standard on this host)
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            _FFMPEG_CACHE = exe
            return exe
    except Exception:
        pass

    # 4. Common Windows WinGet / Chocolatey / Downloads locations
    local_app = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")
    search_roots = [
        Path(local_app) / "Microsoft" / "WinGet" / "Packages",
        Path("C:/ffmpeg/bin"),
        Path(user_profile) / "Downloads",
    ]
    for root in search_roots:
        if root.exists():
            for match in root.rglob("ffmpeg.exe"):
                _FFMPEG_CACHE = str(match)
                return _FFMPEG_CACHE

    raise FileNotFoundError(
        "FFmpeg executable not found! Install `imageio-ffmpeg` (`pip install imageio-ffmpeg`) or add ffmpeg to PATH."
    )


def resolve_ffprobe() -> Optional[str]:
    global _FFPROBE_CACHE
    if _FFPROBE_CACHE and os.path.exists(_FFPROBE_CACHE):
        return _FFPROBE_CACHE

    found = shutil.which("ffprobe")
    if found:
        _FFPROBE_CACHE = found
        return found

    # Check next to ffmpeg
    try:
        ff_dir = Path(resolve_ffmpeg()).parent
        candidate = ff_dir / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.exists():
            _FFPROBE_CACHE = str(candidate)
            return _FFPROBE_CACHE
    except Exception:
        pass

    local_app = os.environ.get("LOCALAPPDATA", "")
    winget_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        for match in winget_root.rglob("ffprobe.exe"):
            _FFPROBE_CACHE = str(match)
            return _FFPROBE_CACHE

    return None


def ffmpeg_escape_filter_path(path: Path | str) -> str:
    """
    Escape Windows absolute path for FFmpeg `-vf subtitles=...` or `ass=...`.
    Example: `D:\\Studio\\sub.ass` -> `D\\:/Studio/sub.ass`
    """
    raw = str(Path(path).resolve()).replace("\\", "/")
    # Escape single quotes, colons, brackets, commas for libavfilter syntax
    raw = raw.replace("'", r"\'")
    raw = raw.replace(":", r"\:")
    raw = raw.replace("[", r"\[")
    raw = raw.replace("]", r"\]")
    return raw


def _parse_ffmpeg_info_stderr(stderr_text: str, file_path: Path) -> Dict[str, Any]:
    """
    Fallback media parser using `ffmpeg -hide_banner -i <file>` stderr output.
    Works 100% reliably when standalone `ffprobe.exe` is not installed.
    """
    info: Dict[str, Any] = {
        "path": str(file_path),
        "duration": 0.0,
        "has_video": False,
        "has_audio": False,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "video_codec": "",
        "video_profile": "",
        "pix_fmt": "",
        "is_10bit": False,
        "audio_codec": "",
        "sample_rate": 0,
        "channels": 0,
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
    }

    # Duration: 00:09:22.32, start: 0.000000, bitrate: ...
    dur_m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text)
    if dur_m:
        hh, mm, ss = int(dur_m.group(1)), int(dur_m.group(2)), float(dur_m.group(3))
        info["duration"] = round(hh * 3600 + mm * 60 + ss, 4)

    # Stream #0:0...: Video: hevc (Main 10) (hvc1 / ...), yuv420p10le(tv, bt709), 1920x1080 ..., 30 fps
    for line in stderr_text.splitlines():
        if "Stream #" in line and "Video:" in line and not info["has_video"]:
            info["has_video"] = True
            v_part = line.split("Video:", 1)[1].strip()
            codec_m = re.match(r"([a-zA-Z0-9_]+)(?:\s*\(([^)]+)\))?", v_part)
            if codec_m:
                info["video_codec"] = codec_m.group(1).lower()
                info["video_profile"] = (codec_m.group(2) or "").strip()

            res_m = re.search(r"(?<![0-9a-zA-Z])(\d{2,5})x(\d{2,5})(?![0-9a-zA-Z])", v_part)
            if res_m:
                info["width"] = int(res_m.group(1))
                info["height"] = int(res_m.group(2))

            fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", v_part)
            if not fps_m:
                fps_m = re.search(r"(\d+(?:\.\d+)?)\s*tbr", v_part)
            if fps_m:
                info["fps"] = round(float(fps_m.group(1)), 3)

            pix_m = re.search(r",\s*(yuv[a-z0-9]+|rgb[a-z0-9]+|bgr[a-z0-9]+|nv\d+|p010[a-z]*)", v_part)
            if pix_m:
                info["pix_fmt"] = pix_m.group(1)
                info["is_10bit"] = ("10" in info["pix_fmt"]) or ("10" in info["video_profile"])

        elif "Stream #" in line and "Audio:" in line and not info["has_audio"]:
            info["has_audio"] = True
            a_part = line.split("Audio:", 1)[1].strip()
            ac_m = re.match(r"([a-zA-Z0-9_]+)", a_part)
            if ac_m:
                info["audio_codec"] = ac_m.group(1).lower()
            sr_m = re.search(r"(\d+)\s*Hz", a_part)
            if sr_m:
                info["sample_rate"] = int(sr_m.group(1))
            if "stereo" in a_part.lower():
                info["channels"] = 2
            elif "mono" in a_part.lower():
                info["channels"] = 1
            else:
                ch_m = re.search(r"(\d+)\s*channels", a_part)
                if ch_m:
                    info["channels"] = int(ch_m.group(1))

    # Exact duration fallback for uncompressed .wav via Python stdlib `wave`
    if file_path.suffix.lower() == ".wav" and file_path.exists():
        try:
            with wave.open(str(file_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    info["duration"] = round(frames / float(rate), 4)
                    info["sample_rate"] = rate
                    info["channels"] = wf.getnchannels()
                    info["has_audio"] = True
        except Exception:
            pass

    return info


def probe_media(path: Path | str) -> Dict[str, Any]:
    """
    Universal media inspector returning normalized dictionary:
    {
      path, duration, has_video, has_audio, width, height, fps,
      video_codec, video_profile, pix_fmt, is_10bit,
      audio_codec, sample_rate, channels, size_bytes
    }
    """
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Media file does not exist: {file_path}")

    ffprobe_exe = resolve_ffprobe()
    if ffprobe_exe:
        cmd = [
            ffprobe_exe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                data = json.loads(proc.stdout)
                fmt = data.get("format", {})
                info: Dict[str, Any] = {
                    "path": str(file_path),
                    "duration": round(float(fmt.get("duration", 0.0) or 0.0), 4),
                    "has_video": False,
                    "has_audio": False,
                    "width": 0,
                    "height": 0,
                    "fps": 0.0,
                    "video_codec": "",
                    "video_profile": "",
                    "pix_fmt": "",
                    "is_10bit": False,
                    "audio_codec": "",
                    "sample_rate": 0,
                    "channels": 0,
                    "size_bytes": int(fmt.get("size", file_path.stat().st_size)),
                }
                for st in data.get("streams", []):
                    ctype = st.get("codec_type")
                    if ctype == "video" and not info["has_video"]:
                        info["has_video"] = True
                        info["width"] = int(st.get("width", 0) or 0)
                        info["height"] = int(st.get("height", 0) or 0)
                        info["video_codec"] = (st.get("codec_name") or "").lower()
                        info["video_profile"] = st.get("profile") or ""
                        info["pix_fmt"] = st.get("pix_fmt") or ""
                        info["is_10bit"] = ("10" in info["pix_fmt"]) or ("10" in info["video_profile"])
                        r_fps = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
                        if "/" in r_fps:
                            num, den = r_fps.split("/", 1)
                            info["fps"] = round(float(num) / float(den), 3) if float(den) != 0 else 0.0
                        else:
                            info["fps"] = round(float(r_fps), 3)
                        if info["duration"] <= 0 and st.get("duration"):
                            info["duration"] = round(float(st["duration"]), 4)
                    elif ctype == "audio" and not info["has_audio"]:
                        info["has_audio"] = True
                        info["audio_codec"] = (st.get("codec_name") or "").lower()
                        info["sample_rate"] = int(st.get("sample_rate", 0) or 0)
                        info["channels"] = int(st.get("channels", 0) or 0)
                        if info["duration"] <= 0 and st.get("duration"):
                            info["duration"] = round(float(st["duration"]), 4)
                return info
            except Exception:
                pass

    # Fallback: parse `ffmpeg -hide_banner -i <file_path>`
    ffmpeg_exe = resolve_ffmpeg()
    proc = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", str(file_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return _parse_ffmpeg_info_stderr(proc.stderr, file_path)


def load_audio_16k_mono_np(path: Path | str) -> "Any":
    """
    Decodes any audio/video file via FFmpeg into a 1D `np.float32` mono array at `16000 Hz`
    for `faster_whisper.WhisperModel.transcribe(audio_np, ...)`.
    Bypasses `PyAV` (`av.open(..., metadata_errors=...)`) version incompatibilities on Windows!
    """
    import numpy as np

    ffmpeg_exe = resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(Path(path).resolve()),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0

