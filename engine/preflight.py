# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — System Preflight & Shared Asset Bootstrapper (engine/preflight.py)
Checks Python packages, FFmpeg/probe_media, pycapcut, disk space, and ensures shared/
contains default 48kHz audio beds, fonts, and warm documentary 3D LUT.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import shutil
import sys
import wave
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402


def _write_stereo_wav_48k(path: Path, samples_stereo: np.ndarray) -> None:
    """Write float32 [-1.0, 1.0] stereo array (N, 2) as 48000 Hz 16-bit PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples_stereo, -0.99, 0.99)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(pcm16.tobytes())


def ensure_shared_assets() -> List[str]:
    """Ensure default room tone, subtle paper tick SFX, warm acoustic outro BGM, and 3D LUT exist."""
    created: List[str] = []
    sr = 48000
    rng = np.random.default_rng(50)

    # 1. room_tone_default.wav (12s gentle rural morning room air at ~ -50 dBFS, brown/warm filtered noise)
    room_path = bins.SHARED_DIR / "audio" / "room_tone_default.wav"
    if not room_path.exists():
        n = sr * 12
        white_l = rng.normal(0.0, 1.0, n).astype(np.float32)
        white_r = rng.normal(0.0, 1.0, n).astype(np.float32)
        # Warm low-pass moving average for natural quiet room air (no harsh hiss)
        kernel = np.ones(32, dtype=np.float32) / 32.0
        warm_l = np.convolve(white_l, kernel, mode="same")
        warm_r = np.convolve(white_r, kernel, mode="same")
        gain = 10.0 ** (-48.0 / 20.0) / max(float(np.std(warm_l)), 1e-6)
        stereo = np.stack([warm_l * gain, warm_r * gain], axis=1)
        _write_stereo_wav_48k(room_path, stereo)
        created.append(str(room_path))

    # 2. sfx_subtle_paper_tick.wav (0.18s subtle warm wooden/paper tick for USD callouts)
    tick_path = bins.SHARED_DIR / "audio" / "sfx_subtle_paper_tick.wav"
    if not tick_path.exists():
        n = int(sr * 0.18)
        t = np.linspace(0.0, 0.18, n, endpoint=False, dtype=np.float32)
        env = np.exp(-t * 58.0) * np.sin(np.pi * np.minimum(t / 0.004, 1.0) * 0.5)
        body = (
            0.65 * np.sin(2.0 * math.pi * 620.0 * t)
            + 0.35 * np.sin(2.0 * math.pi * 1240.0 * t)
            + 0.18 * rng.normal(0.0, 1.0, n).astype(np.float32)
        ) * env
        peak = max(float(np.max(np.abs(body))), 1e-6)
        amp = (10.0 ** (-18.0 / 20.0)) / peak
        mono = body * amp
        stereo = np.stack([mono, mono], axis=1)
        _write_stereo_wav_48k(tick_path, stereo)
        created.append(str(tick_path))

    # 3. bgm_warm_acoustic_outro.wav (48s gentle fingerpicked acoustic harmonic progression C -> G/B -> Am7 -> Fmaj7)
    bgm_path = bins.SHARED_DIR / "audio" / "bgm_warm_acoustic_outro.wav"
    if not bgm_path.exists():
        dur = 48.0
        n = int(sr * dur)
        left = np.zeros(n, dtype=np.float32)
        right = np.zeros(n, dtype=np.float32)
        # Warm fingerpicked notes (Hz) across 4 chords, 3 seconds per chord x 4 cycles = 48s
        chords = [
            [130.81, 196.00, 261.63, 329.63, 392.00, 329.63],  # C major warm
            [123.47, 196.00, 246.94, 293.66, 392.00, 293.66],  # G/B
            [110.00, 164.81, 220.00, 261.63, 329.63, 261.63],  # Am7
            [87.31, 174.61, 220.00, 261.63, 329.63, 220.00],   # Fmaj7
        ]
        beat_sec = 0.5
        total_notes = int(dur / beat_sec)
        for idx in range(total_notes):
            chord = chords[(idx // 6) % len(chords)]
            freq = chord[idx % 6]
            start_i = int(idx * beat_sec * sr)
            note_dur = min(2.4, dur - idx * beat_sec)
            ln = int(note_dur * sr)
            if ln <= 0:
                break
            tn = np.linspace(0.0, note_dur, ln, endpoint=False, dtype=np.float32)
            # Warm plucked string envelope & soft harmonics
            attack = np.minimum(tn / 0.012, 1.0)
            decay_f = np.exp(-tn * 1.45)
            decay_h2 = np.exp(-tn * 2.4)
            decay_h3 = np.exp(-tn * 3.8)
            wave_n = attack * (
                0.72 * decay_f * np.sin(2.0 * math.pi * freq * tn)
                + 0.21 * decay_h2 * np.sin(2.0 * math.pi * (freq * 2.0) * tn)
                + 0.07 * decay_h3 * np.sin(2.0 * math.pi * (freq * 3.0) * tn)
            )
            pan = 0.5 + 0.14 * math.sin(idx * 0.9)
            left[start_i:start_i + ln] += wave_n * math.sqrt(1.0 - pan)
            right[start_i:start_i + ln] += wave_n * math.sqrt(pan)

        # Fade in 2s, fade out 3s, normalize peak to -12 dBFS (mixed down to -26 dB in outro)
        fade_in = int(2.0 * sr)
        fade_out = int(3.0 * sr)
        left[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
        right[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
        left[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
        right[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
        peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-6)
        scale = (10.0 ** (-12.0 / 20.0)) / peak
        stereo = np.stack([left * scale, right * scale], axis=1)
        _write_stereo_wav_48k(bgm_path, stereo)
        created.append(str(bgm_path))

    # 4. warm_doc_unify.cube (17x17x17 3D LUT warming highlights slightly & softening harsh blue digital cast)
    lut_path = bins.SHARED_DIR / "luts" / "warm_doc_unify.cube"
    if not lut_path.exists():
        lut_path.parent.mkdir(parents=True, exist_ok=True)
        size = 17
        lines = [
            "TITLE \"Uncle_Dinh_Warm_Documentary_Unify\"",
            f"LUT_3D_SIZE {size}",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0",
        ]
        for bi in range(size):
            b = bi / (size - 1)
            for gi in range(size):
                g = gi / (size - 1)
                for ri in range(size):
                    r = ri / (size - 1)
                    # Gentle natural documentary warmth: +2.5% red warmth in midtones, -2.5% harsh blue, soft toe
                    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
                    mid_weight = 4.0 * luma * (1.0 - luma)
                    ro = min(1.0, max(0.0, r + 0.022 * mid_weight))
                    go = min(1.0, max(0.0, g + 0.008 * mid_weight))
                    bo = min(1.0, max(0.0, b - 0.024 * mid_weight))
                    # Gentle filmic rolloff near 1.0
                    ro = 0.012 + 0.976 * ro
                    go = 0.010 + 0.978 * go
                    bo = 0.010 + 0.972 * bo
                    lines.append(f"{ro:.6f} {go:.6f} {bo:.6f}")
        lut_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        created.append(str(lut_path))

    return created


def run_preflight(episode_id: str | None = None) -> Tuple[bool, Dict[str, object]]:
    created_shared = ensure_shared_assets()
    checks: List[Dict[str, object]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    # 1. Studio structure & isolation check
    studio_str = str(bins.STUDIO_ROOT)
    isolated = (bins.ENGINE_DIR.is_dir()) and (bins.SHARED_DIR.is_dir()) and (bins.SKILL_DIR.is_dir())
    record("studio_isolation", isolated, f"STUDIO_ROOT={studio_str}")

    # 2. FFmpeg & FFprobe / probe_media
    try:
        ff_exe = bins.resolve_ffmpeg()
        fp_exe = bins.resolve_ffprobe()
        mode_note = f"ffprobe={fp_exe}" if fp_exe else "ffprobe=Fallback(ffmpeg -i stderr + wave)"
        record("ffmpeg_binary", True, f"ffmpeg={ff_exe} | {mode_note}")
    except Exception as exc:
        record("ffmpeg_binary", False, str(exc))

    # 3. Core Python packages
    for pkg in ("numpy", "PIL", "faster_whisper", "imageio_ffmpeg", "yt_dlp"):
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "__version__", "installed")
            record(f"pkg_{pkg}", True, f"version={ver}")
        except Exception as exc:
            record(f"pkg_{pkg}", False, str(exc))

    # 4. pycapcut local engine
    try:
        import pycapcut as cc  # noqa: F401
        record("engine_pycapcut", True, f"Loaded from {Path(cc.__file__).parent}")
    except Exception as exc:
        record("engine_pycapcut", False, str(exc))

    # 5. Brand DNA & Shared assets
    dna = bins.load_brand_dna()
    record(
        "brand_dna_us",
        bool(dna and dna.get("channel_identity")),
        f"channel={dna.get('channel_identity', {}).get('channel_name', 'MISSING')}",
    )

    required_shared = [
        bins.SHARED_DIR / "fonts" / "segoeuib.ttf",
        bins.SHARED_DIR / "audio" / "room_tone_default.wav",
        bins.SHARED_DIR / "audio" / "sfx_subtle_paper_tick.wav",
        bins.SHARED_DIR / "audio" / "bgm_warm_acoustic_outro.wav",
        bins.SHARED_DIR / "luts" / "warm_doc_unify.cube",
    ]
    missing_shared = [str(p) for p in required_shared if not p.exists()]
    record("shared_assets", len(missing_shared) == 0, "All 5 core shared assets ready" if not missing_shared else f"Missing: {missing_shared}")

    # 6. Disk free space on Studio drive (>= 3 GB)
    usage = shutil.disk_usage(bins.STUDIO_ROOT)
    free_gb = usage.free / (1024 ** 3)
    record("disk_space", free_gb >= 3.0, f"Free={free_gb:.2f} GB")

    # 7. Optional episode input & clean V2 3-folder layout check
    ep_probe = None
    if episode_id:
        ep_paths = bins.init_episode_dirs(episode_id)
        vid_p = ep_paths["rough_video"]
        scr_p = ep_paths["rough_script"]
        if vid_p.exists() and scr_p.exists():
            ep_probe = bins.probe_media(vid_p)
            record(
                f"episode_{episode_id}_input",
                ep_probe["has_video"] and ep_probe["duration"] > 1.0,
                f"video={ep_probe['width']}x{ep_probe['height']}@{ep_probe['fps']}fps, dur={ep_probe['duration']}s, codec={ep_probe['video_codec']} ({ep_probe['video_profile']}), pix_fmt={ep_probe['pix_fmt']}",
            )
        else:
            record(f"episode_{episode_id}_input", False, f"Missing video_so_bo.mp4 or kich_ban_so_bo.md in {ep_paths['input_dir']}")

        ep_root = ep_paths["episode_root"]
        allowed_top = {"1_input", "2_pha_A_kich_ban", "3_pha_B_video", "_he_thong"}
        actual_top = {p.name for p in ep_root.iterdir()} if ep_root.exists() else set()
        extra_clutter = sorted(actual_top - allowed_top)
        root_cache_exists = (bins.STUDIO_ROOT / ".cache").exists()
        layout_ok = (allowed_top.issubset(actual_top)) and (len(extra_clutter) == 0) and (not root_cache_exists)
        detail_msg = (
            "Clean V2 Layout: 1_input | 2_pha_A_kich_ban | 3_pha_B_video | _he_thong"
            if layout_ok
            else f"Clutter detected: extra_ep_dirs={extra_clutter}, root_.cache={root_cache_exists}"
        )
        record(f"episode_{episode_id}_v2_layout", layout_ok, detail_msg)

    all_ok = all(bool(c["ok"]) for c in checks)
    summary = {
        "pass": all_ok,
        "studio_root": studio_str,
        "bootstrapped_shared_files": created_shared,
        "checks": checks,
        "episode_probe": ep_probe,
    }
    return all_ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Vlog-US-Studio-v1 System Preflight")
    parser.add_argument("--episode", type=str, default=None, help="Optional Episode ID (e.g., EP001) to inspect")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args()

    ok, summary = run_preflight(args.episode)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print(f"VLOG-US-STUDIO-V1 PREFLIGHT | STATUS: {'PASS 100%' if ok else 'FAILED'}")
        print("=" * 72)
        for item in summary["checks"]:
            badge = "[PASS]" if item["ok"] else "[FAIL]"
            print(f"{badge:7s} {item['check']:24s} -> {item['detail']}")
        print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
