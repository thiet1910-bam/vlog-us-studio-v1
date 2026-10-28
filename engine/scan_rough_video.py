# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase A Rough Video Visual & Audio Scanner (engine/scan_rough_video.py)
1. Loads (or generates) `_he_thong/scan/source_conform_map.json`.
2. Extracts timestamped 4x5 Contact Sheets (`_he_thong/scan/contact_sheets/sheet_XX.jpg`)
   across every shot + every 8s inside long takes so the AI Director sees 100% of ground-truth reality.
3. Runs `faster-whisper` (`language="vi"`, `vad_filter=True`) + anti-hallucination filter + 48kHz RMS/peak analysis
   to classify location audio into `VN_SPEECH_LEAK`, `CLIPPING_NOISE`, and `CLEAN_ASMR`.
4. Extracts authentic `_he_thong/scan/clean_room_tone.wav` (48kHz Stereo) for seamless leak patching.
5. Writes `_he_thong/scan/rough_video_telemetry.json`.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402
import match_rough_to_raw  # noqa: E402

WHISPER_HALLUCINATION_PHRASES = (
    "cảm ơn các bạn đã xem",
    "hãy đăng ký kênh",
    "đăng ký kênh",
    "subscribe",
    "phụ đề được thực hiện",
    "amara.org",
    "hẹn gặp lại các bạn",
    "like và share",
    "nhấn chuông",
    "ghiền mì gõ",
    "la la la",
)


def fmt_mmss(sec: float) -> str:
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:04.1f}"


def _grab_frame_rgb(video_path: Path, timestamp_sec: float, width: int = 480, height: int = 270) -> Image.Image:
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp_sec):.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height}",
        "-f", "image2pipe",
        "-vcodec", "png",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.stdout:
        try:
            return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
        except Exception:
            pass
    return Image.new("RGB", (width, height), (24, 24, 24))


def build_contact_sheets(
    video_path: Path,
    shots: List[Dict[str, Any]],
    out_dir: Path,
    max_interval_in_long_shot_sec: float = 8.0,
) -> List[Dict[str, Any]]:
    """
    Samples representative frames across all shots (plus every ~8s in long handheld takes)
    and composes crisp 4x5 (20 tiles/sheet) Contact Sheets with yellow timestamp badges.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("sheet_*.jpg"):
        old.unlink()

    sample_points: List[Tuple[float, str]] = []
    for sh in shots:
        s_in = float(sh["rough_in_sec"])
        s_out = float(sh["rough_out_sec"])
        dur = s_out - s_in
        sid = sh["shot_id"]
        if dur <= max_interval_in_long_shot_sec * 1.25:
            mid = s_in + dur * 0.45
            sample_points.append((round(mid, 2), sid))
        else:
            n_steps = max(2, int(math.ceil(dur / max_interval_in_long_shot_sec)))
            step = dur / n_steps
            for k in range(n_steps):
                t = s_in + (k + 0.5) * step
                sample_points.append((round(t, 2), f"{sid}.{k+1}"))

    font_path = bins.SHARED_DIR / "fonts" / "segoeuib.ttf"
    try:
        font = ImageFont.truetype(str(font_path), 19)
        header_font = ImageFont.truetype(str(font_path), 24)
    except Exception:
        font = ImageFont.load_default()
        header_font = font

    cols, rows = 4, 5
    per_sheet = cols * rows
    tile_w, tile_h = 480, 270
    header_h = 48
    sheets_meta: List[Dict[str, Any]] = []

    for s_idx in range(0, len(sample_points), per_sheet):
        chunk = sample_points[s_idx : s_idx + per_sheet]
        sheet_num = (s_idx // per_sheet) + 1
        canvas = Image.new("RGB", (cols * tile_w, header_h + rows * tile_h), (16, 16, 18))
        draw = ImageDraw.Draw(canvas)
        t_first, t_last = chunk[0][0], chunk[-1][0]
        draw.text(
            (18, 10),
            f"VLOG-US-STUDIO PHASE A CONTACT SHEET #{sheet_num:02d}  |  Range: {fmt_mmss(t_first)} ({t_first:.1f}s) -> {fmt_mmss(t_last)} ({t_last:.1f}s)",
            fill=(255, 215, 0),
            font=header_font,
        )

        for idx_in_sheet, (ts, label) in enumerate(chunk):
            r = idx_in_sheet // cols
            c = idx_in_sheet % cols
            x0 = c * tile_w
            y0 = header_h + r * tile_h
            frame_im = _grab_frame_rgb(video_path, ts, width=tile_w, height=tile_h)
            canvas.paste(frame_im, (x0, y0))

            # Draw dark pill + bright yellow timestamp
            badge_text = f" {fmt_mmss(ts)} ({ts:.1f}s) | {label} "
            draw.rectangle([x0 + 6, y0 + tile_h - 34, x0 + 275, y0 + tile_h - 6], fill=(12, 12, 12))
            draw.text((x0 + 10, y0 + tile_h - 32), badge_text, fill=(255, 215, 0), font=font)
            draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], outline=(55, 55, 60), width=1)

        sheet_path = out_dir / f"sheet_{sheet_num:02d}.jpg"
        canvas.save(sheet_path, quality=88)
        sheets_meta.append(
            {
                "sheet_index": sheet_num,
                "path": str(sheet_path),
                "range_sec": [t_first, t_last],
                "tiles_count": len(chunk),
            }
        )

    return sheets_meta


def _extract_wav(video_path: Path, out_wav: Path, sr: int = 48000, channels: int = 2) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-vn",
        "-ac", str(channels),
        "-ar", str(sr),
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)


def _is_genuine_vietnamese_speech(text: str, no_speech_prob: float, avg_logprob: float, compression_ratio: float) -> bool:
    cleaned = text.strip().lower()
    if len(cleaned) < 2:
        return False
    if no_speech_prob >= 0.55 or avg_logprob <= -1.05 or compression_ratio >= 2.4:
        return False
    for phrase in WHISPER_HALLUCINATION_PHRASES:
        if phrase in cleaned:
            return False
    # Require at least 2 alphabetic letters
    alpha_cnt = sum(1 for ch in cleaned if ch.isalpha())
    return alpha_cnt >= 2


def analyze_audio_and_extract_room_tone(
    video_path: Path,
    cache_ep_dir: Path,
    whisper_model_name: str = "base",
) -> Dict[str, Any]:
    """
    Analyzes 48kHz location audio per 1-second bin, runs faster-whisper Vietnamese speech leak detection,
    finds clean ambient patches, and saves `clean_room_tone.wav` (48kHz Stereo).
    """
    wav_48k = cache_ep_dir / "rough_location_48k.wav"
    _extract_wav(video_path, wav_48k, sr=48000, channels=2)

    with wave.open(str(wav_48k), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)
    stereo = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32).reshape(-1, 2) / 32768.0
    mono = np.mean(stereo, axis=1)
    total_sec = len(mono) / float(sr)

    # 1. Per-second RMS & Peak dBFS profile
    n_secs = int(math.ceil(total_sec))
    sec_rms_db: List[float] = []
    sec_peak_db: List[float] = []
    clipping_ranges: List[Dict[str, Any]] = []

    for s in range(n_secs):
        chunk = mono[s * sr : min(len(mono), (s + 1) * sr)]
        if len(chunk) == 0:
            rms_db, pk_db = -90.0, -90.0
        else:
            rms = float(np.sqrt(np.mean(chunk * chunk)))
            pk = float(np.max(np.abs(chunk)))
            rms_db = round(20.0 * math.log10(max(rms, 1e-5)), 2)
            pk_db = round(20.0 * math.log10(max(pk, 1e-5)), 2)
        sec_rms_db.append(rms_db)
        sec_peak_db.append(pk_db)
        if pk_db > -1.0:
            clipping_ranges.append({"start_sec": float(s), "end_sec": round(min(total_sec, s + 1.0), 3), "peak_dbfs": pk_db})

    # 2. Run faster-whisper (language="vi", vad_filter=True) for VN_SPEECH_LEAK detection
    # Feed 16kHz float32 numpy array directly (bypasses PyAV av.open metadata_errors bug)
    from faster_whisper import WhisperModel

    audio_16k = bins.load_audio_16k_mono_np(wav_48k)
    model = WhisperModel(whisper_model_name, device="cpu", compute_type="int8")
    segments_gen, _ = model.transcribe(
        audio_16k,
        language="vi",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        condition_on_previous_text=False,
    )

    speech_leaks: List[Dict[str, Any]] = []
    speech_mask = np.zeros(n_secs, dtype=bool)

    for seg in segments_gen:
        if _is_genuine_vietnamese_speech(
            seg.text,
            float(seg.no_speech_prob),
            float(seg.avg_logprob),
            float(seg.compression_ratio),
        ):
            s0 = round(max(0.0, float(seg.start) - 0.15), 3)
            s1 = round(min(total_sec, float(seg.end) + 0.15), 3)
            for idx_s in range(int(math.floor(s0)), min(n_secs, int(math.ceil(s1)))):
                speech_mask[idx_s] = True
            speech_leaks.append(
                {
                    "start_sec": s0,
                    "end_sec": s1,
                    "duration_sec": round(s1 - s0, 3),
                    "detected_vi_text": seg.text.strip(),
                    "no_speech_prob": round(float(seg.no_speech_prob), 3),
                    "avg_logprob": round(float(seg.avg_logprob), 3),
                }
            )

    # 3. Extract 6.0s authentic `clean_room_tone.wav` from the cleanest, steady ambient window (no speech, no clipping)
    best_win_start = 0
    best_score = 1e9
    win_len = min(6, max(2, n_secs // 4))
    for i in range(0, max(1, n_secs - win_len)):
        if np.any(speech_mask[i : i + win_len]):
            continue
        r_slice = sec_rms_db[i : i + win_len]
        p_slice = sec_peak_db[i : i + win_len]
        if max(p_slice) > -12.0 or min(r_slice) < -68.0:
            continue
        # Prefer steady gentle room tone around -42 dBFS with minimal variance
        mean_r = float(np.mean(r_slice))
        std_r = float(np.std(r_slice))
        score = abs(mean_r - (-42.0)) + std_r * 3.5
        if score < best_score:
            best_score = score
            best_win_start = i

    room_tone_path = cache_ep_dir / "clean_room_tone.wav"
    rt_samples = stereo[best_win_start * sr : min(len(stereo), (best_win_start + win_len) * sr)]
    if len(rt_samples) < sr * 2:
        shutil.copy(bins.SHARED_DIR / "audio" / "room_tone_default.wav", room_tone_path)
    else:
        pcm16 = np.clip(rt_samples * 32767.0, -32767, 32767).astype(np.int16)
        with wave.open(str(room_tone_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm16.tobytes())

    # Attach nearest clean 4s patch coordinates to each VN_SPEECH_LEAK
    for leak in speech_leaks:
        center = int(round(leak["start_sec"]))
        patch_start = float(best_win_start)
        for delta in range(2, 45):
            for cand in (center - delta - 4, center + delta):
                if 0 <= cand <= n_secs - 4 and not np.any(speech_mask[cand : cand + 4]):
                    if max(sec_peak_db[cand : cand + 4]) < -10.0:
                        patch_start = float(cand)
                        break
            else:
                continue
            break
        leak["same_clip_clean_patch"] = {
            "patch_in_sec": patch_start,
            "patch_out_sec": round(min(total_sec, patch_start + 4.0), 3),
        }

    # Summarize clean ASMR showcase windows (continuous >= 4s without speech leak and healthy ambient energy -48..-10 dB)
    clean_asmr_windows: List[Dict[str, Any]] = []
    run_start = None
    for s in range(n_secs):
        is_good = (not speech_mask[s]) and (-52.0 <= sec_rms_db[s] <= -8.0) and (sec_peak_db[s] <= -1.2)
        if is_good and run_start is None:
            run_start = s
        elif (not is_good) and run_start is not None:
            if s - run_start >= 4:
                clean_asmr_windows.append(
                    {
                        "start_sec": float(run_start),
                        "end_sec": float(s),
                        "duration_sec": float(s - run_start),
                        "mean_rms_dbfs": round(float(np.mean(sec_rms_db[run_start:s])), 2),
                    }
                )
            run_start = None
    if run_start is not None and (n_secs - run_start) >= 4:
        clean_asmr_windows.append(
            {
                "start_sec": float(run_start),
                "end_sec": round(total_sec, 3),
                "duration_sec": round(total_sec - run_start, 3),
                "mean_rms_dbfs": round(float(np.mean(sec_rms_db[run_start:])), 2),
            }
        )

    return {
        "duration_sec": round(total_sec, 3),
        "mean_rms_dbfs": round(float(np.mean(sec_rms_db)), 2),
        "max_peak_dbfs": round(float(np.max(sec_peak_db)), 2),
        "clean_room_tone_wav": str(room_tone_path),
        "clean_room_tone_source_window_sec": [float(best_win_start), float(best_win_start + win_len)],
        "vn_speech_leak_count": len(speech_leaks),
        "vn_speech_leaks": speech_leaks,
        "clipping_noise_ranges": clipping_ranges,
        "clean_asmr_windows_count": len(clean_asmr_windows),
        "clean_asmr_windows": clean_asmr_windows,
    }


def run_scan(episode_id: str, whisper_model: str = "base") -> Dict[str, Any]:
    ep_paths = bins.init_episode_dirs(episode_id)
    rough_video = ep_paths["rough_video"].resolve()
    conform_path = ep_paths["conform_map_json"]
    scan_dir = ep_paths["scan_dir"]

    if not conform_path.exists():
        conform_data = match_rough_to_raw.run_conform(episode_id)
    else:
        conform_data = json.loads(conform_path.read_text(encoding="utf-8"))

    sheets = build_contact_sheets(
        rough_video,
        conform_data["shots"],
        ep_paths["contact_sheets_dir"],
    )
    audio_telemetry = analyze_audio_and_extract_room_tone(
        rough_video,
        scan_dir,
        whisper_model_name=whisper_model,
    )

    telemetry = {
        "episode_id": episode_id,
        "conform_mode": conform_data["mode"],
        "video_summary": conform_data["rough_video"],
        "shots_count": conform_data["shots_count"],
        "contact_sheets_count": len(sheets),
        "contact_sheets": sheets,
        "audio_telemetry": audio_telemetry,
    }
    out_path = ep_paths["telemetry_json"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(telemetry, ensure_ascii=False, indent=2), encoding="utf-8")
    return telemetry


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase A Rough Video Visual & Audio Scanner")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--whisper-model", default="base", help="faster-whisper model (default: base)")
    args = parser.parse_args()

    tel = run_scan(args.episode, whisper_model=args.whisper_model)
    at = tel["audio_telemetry"]
    print("=" * 72)
    print(f"SCAN_ROUGH_VIDEO COMPLETE | EPISODE={args.episode} | SHOTS={tel['shots_count']}")
    print(f"Contact Sheets : {tel['contact_sheets_count']} saved in episodes/{args.episode}/_he_thong/scan/contact_sheets/")
    print(f"VN Speech Leaks: {at['vn_speech_leak_count']} flagged | Clean ASMR Windows: {at['clean_asmr_windows_count']}")
    print(f"Room Tone WAV  : {at['clean_room_tone_wav']} (window {at['clean_room_tone_source_window_sec']}s)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
