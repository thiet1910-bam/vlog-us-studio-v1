# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase A Reverse Source Conformer (engine/match_rough_to_raw.py)
Maps scenes in `video_so_bo.mp4` back to original camera files in `1_input/raw_clips/`
across 3 automatic modes:
  1. CAPCUT_DRAFT_JSON   : Instant 0.05s conform when `rough_draft_content.json` is provided.
  2. RAW_CONFORM_DHASH_FFT: Visual 64-bit dHash + Audio FFT Cross-Correlation + 30fps fine L1 lock
                           (<= 0.033s accuracy) when `raw_clips/` has .MP4/.MOV files.
  3. ROUGH_ONLY          : Clean fallback mapping directly to `video_so_bo.mp4` when `raw_clips/` is empty.
Also indexes `authentic_unused_pool` (unused raw clips or unused tails >= 4.5s).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402
import capcut_bridge  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".webm"}


def detect_scene_cuts(
    video_path: Path,
    threshold: float = 0.28,
    min_shot_sec: float = 1.5,
) -> List[Tuple[float, float]]:
    """
    Fast downscaled scene cut detector on `video_path` returning list of (in_sec, out_sec) shots.
    Downscales to 320x180 grayscale inside FFmpeg before `select='gt(scene,thr)'` for 6x speed on 10-bit HEVC.
    """
    meta = bins.probe_media(video_path)
    total_dur = float(meta["duration"])
    if total_dur <= 0:
        raise ValueError(f"Invalid video duration for {video_path}")

    ffmpeg_exe = bins.resolve_ffmpeg()
    vf = f"scale=320:180,format=gray,select='gt(scene,{threshold})',showinfo"
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostats",
        "-i", str(video_path),
        "-vf", vf,
        "-an",
        "-f", "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    pts_times: List[float] = [0.0]
    for m in re.finditer(r"pts_time:\s*(\d+(?:\.\d+)?)", proc.stderr):
        t = round(float(m.group(1)), 4)
        if t - pts_times[-1] >= min_shot_sec and (total_dur - t) >= 1.0:
            pts_times.append(t)
    pts_times.append(round(total_dur, 4))

    shots: List[Tuple[float, float]] = []
    for i in range(len(pts_times) - 1):
        s_in, s_out = pts_times[i], pts_times[i + 1]
        if s_out > s_in:
            shots.append((round(s_in, 4), round(s_out, 4)))
    return shots


def _extract_dhash_sequence(video_path: Path, sample_fps: float = 2.0) -> np.ndarray:
    """
    Streams 9x8 grayscale raw frames at `sample_fps` and computes 64-bit dHash array (N, 64) uint8 {0,1}.
    """
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps={sample_fps},scale=9:8:flags=area,format=gray",
        "-an",
        "-f", "rawvideo",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    raw = proc.stdout
    frame_bytes = 9 * 8
    n_frames = len(raw) // frame_bytes
    if n_frames == 0:
        return np.zeros((0, 64), dtype=np.uint8)
    frames = np.frombuffer(raw[: n_frames * frame_bytes], dtype=np.uint8).reshape(n_frames, 8, 9)
    bits = (frames[:, :, 1:] > frames[:, :, :-1]).astype(np.uint8).reshape(n_frames, 64)
    return bits


def _extract_single_dhash(video_path: Path, timestamp_sec: float) -> Optional[np.ndarray]:
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp_sec):.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=9:8:flags=area,format=gray",
        "-an",
        "-f", "rawvideo",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if len(proc.stdout) < 72:
        return None
    frame = np.frombuffer(proc.stdout[:72], dtype=np.uint8).reshape(8, 9)
    return (frame[:, 1:] > frame[:, :-1]).astype(np.uint8).reshape(64)


def _extract_audio_2k_mono(video_path: Path, start_sec: float = 0.0, dur_sec: Optional[float] = None) -> np.ndarray:
    """Extracts 2000 Hz mono float32 waveform for ultra-fast FFT cross-correlation."""
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [ffmpeg_exe, "-hide_banner", "-loglevel", "error"]
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.4f}"]
    cmd += ["-i", str(video_path)]
    if dur_sec is not None:
        cmd += ["-t", f"{dur_sec:.4f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


def _extract_tiny_frames_30fps(
    video_path: Path,
    start_sec: float,
    dur_sec: float,
    fps: float = 30.0,
) -> np.ndarray:
    """Extracts 32x18 grayscale frames at 30fps over a short window for frame-accurate visual locking."""
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{max(0.0, start_sec):.4f}",
        "-i", str(video_path),
        "-t", f"{max(0.1, dur_sec):.4f}",
        "-vf", f"fps={fps},scale=32:18:flags=area,format=gray",
        "-an",
        "-f", "rawvideo",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    fbytes = 32 * 18
    nf = len(proc.stdout) // fbytes
    if nf == 0:
        return np.zeros((0, fbytes), dtype=np.float32)
    return np.frombuffer(proc.stdout[: nf * fbytes], dtype=np.uint8).reshape(nf, fbytes).astype(np.float32)


def _fft_refine_offset(
    rough_audio: np.ndarray,
    raw_audio: np.ndarray,
    sr: int,
    coarse_offset_sec: float,
    search_radius_sec: float = 2.0,
) -> Tuple[Optional[float], float]:
    """
    Uses FFT Cross-Correlation around `coarse_offset_sec +/- search_radius_sec` to lock offset to <= 0.005s.
    Returns (refined_offset_sec, correlation_peak_snr).
    """
    if len(rough_audio) < sr // 2 or len(raw_audio) < sr // 2:
        return None, 0.0

    win_start = max(0.0, coarse_offset_sec - search_radius_sec)
    win_end = min(len(raw_audio) / float(sr), coarse_offset_sec + (len(rough_audio) / float(sr)) + search_radius_sec)
    i0, i1 = int(win_start * sr), int(win_end * sr)
    sub_raw = raw_audio[i0:i1]
    q = rough_audio[: min(len(rough_audio), int(8.0 * sr))]
    if len(sub_raw) <= len(q) or float(np.std(q)) < 1e-4 or float(np.std(sub_raw)) < 1e-4:
        return None, 0.0

    q_norm = q - float(np.mean(q))
    r_norm = sub_raw - float(np.mean(sub_raw))
    n_fft = 1 << (len(r_norm) + len(q_norm) - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(r_norm, n=n_fft) * np.conj(np.fft.rfft(q_norm, n=n_fft)), n=n_fft)
    valid_len = len(r_norm) - len(q_norm) + 1
    valid_corr = corr[:valid_len]
    best_idx = int(np.argmax(valid_corr))
    peak_val = float(valid_corr[best_idx])
    denom = float(np.linalg.norm(q_norm)) * float(np.linalg.norm(r_norm[best_idx : best_idx + len(q_norm)])) + 1e-8
    norm_corr = peak_val / denom
    refined_sec = win_start + (best_idx / float(sr))
    return round(refined_sec, 4), round(norm_corr, 4)


def _visual_30fps_refine_offset(
    rough_video: Path,
    rough_in_sec: float,
    raw_video: Path,
    raw_dur: float,
    coarse_offset_sec: float,
    fps: float = 30.0,
) -> Tuple[float, float]:
    """
    Frame-accurate 30fps L1 visual alignment around `coarse_offset_sec +/- 0.85s`.
    Works even if rough cut audio was muted or replaced!
    """
    probe_dur = 0.8
    q_frames = _extract_tiny_frames_30fps(rough_video, rough_in_sec + 0.05, probe_dur, fps=fps)
    if len(q_frames) < 4:
        return round(coarse_offset_sec, 4), 0.7

    search_in = max(0.0, coarse_offset_sec - 0.85)
    search_dur = min(raw_dur - search_in, probe_dur + 1.8)
    r_frames = _extract_tiny_frames_30fps(raw_video, search_in, search_dur, fps=fps)
    if len(r_frames) <= len(q_frames):
        return round(coarse_offset_sec, 4), 0.7

    best_sad = 1e18
    best_k = 0
    nq = len(q_frames)
    for k in range(len(r_frames) - nq + 1):
        sad = float(np.mean(np.abs(r_frames[k : k + nq] - q_frames)))
        if sad < best_sad:
            best_sad = sad
            best_k = k

    # Compensate for the +0.05s probe start offset
    refined = max(0.0, search_in + (best_k / fps) - 0.05)
    conf = max(0.0, min(1.0, 1.0 - (best_sad / 64.0)))
    return round(refined, 4), round(conf, 4)


def list_raw_clips(raw_dir: Path) -> List[Path]:
    if not raw_dir.exists():
        return []
    files = [
        p.resolve()
        for p in sorted(raw_dir.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    return files


def build_unused_pool(
    raw_files: List[Path],
    raw_meta_map: Dict[str, Dict[str, Any]],
    used_intervals_by_file: Dict[str, List[Tuple[float, float]]],
    min_unused_sec: float = 4.5,
) -> List[Dict[str, Any]]:
    """
    Identifies completely unused raw clips AND unused continuous head/tail windows >= `min_unused_sec`.
    """
    pool: List[Dict[str, Any]] = []
    idx = 1
    for rfile in raw_files:
        fstr = str(rfile)
        dur = float(raw_meta_map[fstr]["duration"])
        intervals = sorted(used_intervals_by_file.get(fstr, []))
        if not intervals:
            if dur >= 2.5:
                pool.append(
                    {
                        "unused_id": f"unused_{idx:03d}",
                        "source_file": fstr,
                        "file_name": rfile.name,
                        "in_sec": 0.0,
                        "out_sec": round(dur, 4),
                        "duration_sec": round(dur, 4),
                        "kind": "FULL_UNUSED_CLIP",
                    }
                )
                idx += 1
            continue

        # Merge overlapping used intervals and check gaps >= min_unused_sec
        merged: List[List[float]] = []
        for s, e in intervals:
            if not merged or s > merged[-1][1] + 0.1:
                merged.append([s, e])
            else:
                merged[-1][1] = max(merged[-1][1], e)

        cursor = 0.0
        for s, e in merged:
            if s - cursor >= min_unused_sec:
                pool.append(
                    {
                        "unused_id": f"unused_{idx:03d}",
                        "source_file": fstr,
                        "file_name": rfile.name,
                        "in_sec": round(cursor, 4),
                        "out_sec": round(s, 4),
                        "duration_sec": round(s - cursor, 4),
                        "kind": "UNUSED_SEGMENT_HANDLE",
                    }
                )
                idx += 1
            cursor = max(cursor, e)
        if dur - cursor >= min_unused_sec:
            pool.append(
                {
                    "unused_id": f"unused_{idx:03d}",
                    "source_file": fstr,
                    "file_name": rfile.name,
                    "in_sec": round(cursor, 4),
                    "out_sec": round(dur, 4),
                    "duration_sec": round(dur - cursor, 4),
                    "kind": "UNUSED_TAIL_SELECT",
                }
            )
            idx += 1

    return pool


def run_conform(
    episode_id: str,
    rough_video_override: Optional[Path] = None,
    raw_dir_override: Optional[Path] = None,
    draft_json_override: Optional[Path] = None,
    out_json_override: Optional[Path] = None,
    shots_override: Optional[List[Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    ep_paths = bins.init_episode_dirs(episode_id)
    rough_video = (rough_video_override or ep_paths["rough_video"]).resolve()
    raw_dir = (raw_dir_override or ep_paths["raw_clips_dir"]).resolve()
    draft_json = draft_json_override or ep_paths["rough_draft_json"]
    out_json = (out_json_override or ep_paths["conform_map_json"]).resolve()

    if not rough_video.exists():
        raise FileNotFoundError(f"Rough video not found: {rough_video}")

    rough_meta = bins.probe_media(rough_video)
    raw_files = list_raw_clips(raw_dir)
    raw_meta_map: Dict[str, Dict[str, Any]] = {str(p): bins.probe_media(p) for p in raw_files}

    # MODE 1: CapCut Rough Draft JSON present
    if draft_json and Path(draft_json).exists():
        cuts = capcut_bridge.parse_rough_draft(draft_json, raw_dir)
        used_by_file: Dict[str, List[Tuple[float, float]]] = {}
        for c in cuts:
            if c["source_exists"]:
                used_by_file.setdefault(c["source_file"], []).append((c["source_in_sec"], c["source_out_sec"]))
        unused_pool = build_unused_pool(raw_files, raw_meta_map, used_by_file)
        result = {
            "episode_id": episode_id,
            "mode": "CAPCUT_DRAFT_JSON",
            "rough_video": rough_meta,
            "raw_clips_count": len(raw_files),
            "shots_count": len(cuts),
            "shots": cuts,
            "authentic_unused_pool": unused_pool,
        }
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # Detect shots on rough_video (unless explicit shots_override provided)
    shots = shots_override if shots_override is not None else detect_scene_cuts(rough_video)

    # MODE 3: ROUGH_ONLY (no raw_clips provided yet)
    if len(raw_files) == 0:
        cuts = []
        for idx, (s_in, s_out) in enumerate(shots, start=1):
            dur = round(s_out - s_in, 4)
            cuts.append(
                {
                    "shot_id": f"shot_{idx:03d}",
                    "rough_in_sec": s_in,
                    "rough_out_sec": s_out,
                    "rough_duration_sec": dur,
                    "source_file": str(rough_video),
                    "source_exists": True,
                    "source_in_sec": s_in,
                    "source_out_sec": s_out,
                    "source_total_duration_sec": rough_meta["duration"],
                    "head_handle_sec": 0.0,
                    "tail_handle_sec": 0.0,
                    "speed": 1.0,
                    "conform_method": "ROUGH_ONLY",
                    "confidence": 1.0,
                }
            )
        result = {
            "episode_id": episode_id,
            "mode": "ROUGH_ONLY",
            "rough_video": rough_meta,
            "raw_clips_count": 0,
            "shots_count": len(cuts),
            "shots": cuts,
            "authentic_unused_pool": [],
        }
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # MODE 2: RAW_CONFORM_DHASH_FFT (64-bit dHash + 2kHz Audio FFT + 30fps L1 Fine Alignment)
    sample_fps = 2.0
    raw_dhashes: Dict[str, np.ndarray] = {}
    raw_audios: Dict[str, np.ndarray] = {}
    for rfile in raw_files:
        fstr = str(rfile)
        raw_dhashes[fstr] = _extract_dhash_sequence(rfile, sample_fps=sample_fps)
        raw_audios[fstr] = _extract_audio_2k_mono(rfile)

    cuts = []
    used_by_file = {}
    for idx, (s_in, s_out) in enumerate(shots, start=1):
        dur = round(s_out - s_in, 4)
        # Probe 3 anchor points inside the rough shot
        probe_offsets = [min(0.25, dur * 0.2), dur * 0.5, max(0.0, dur - min(0.25, dur * 0.2))]
        probe_hashes: List[Tuple[float, np.ndarray]] = []
        for off in probe_offsets:
            dh = _extract_single_dhash(rough_video, s_in + off)
            if dh is not None:
                probe_hashes.append((off, dh))

        best_file: Optional[str] = None
        best_coarse_in = 0.0
        best_hamming = 64.0

        for rfile in raw_files:
            fstr = str(rfile)
            seq = raw_dhashes[fstr]
            r_dur = float(raw_meta_map[fstr]["duration"])
            if len(seq) == 0 or r_dur < dur * 0.7:
                continue
            for k in range(len(seq)):
                cand_in = k / sample_fps
                if cand_in + dur > r_dur + 0.6:
                    break
                hams = []
                for off, ph in probe_hashes:
                    rk = min(len(seq) - 1, int(round((cand_in + off) * sample_fps)))
                    hams.append(int(np.count_nonzero(seq[rk] != ph)))
                avg_ham = float(np.mean(hams)) if hams else 64.0
                if avg_ham < best_hamming:
                    best_hamming = avg_ham
                    best_file = fstr
                    best_coarse_in = cand_in

        if best_file is not None and best_hamming <= 14.0:
            r_dur = float(raw_meta_map[best_file]["duration"])
            rough_aud = _extract_audio_2k_mono(rough_video, start_sec=s_in, dur_sec=min(dur, 8.0))
            fft_in, fft_snr = _fft_refine_offset(rough_aud, raw_audios[best_file], 2000, best_coarse_in)
            anchor_in = fft_in if (fft_in is not None and fft_snr >= 0.45) else best_coarse_in
            # Final 30fps visual frame lock eliminates ~21-45ms AAC encoder priming shift!
            vis_in, vis_conf = _visual_30fps_refine_offset(
                rough_video, s_in, Path(best_file), r_dur, anchor_in, fps=float(rough_meta["fps"] or 30.0)
            )
            if fft_in is not None and fft_snr >= 0.45 and abs(vis_in - fft_in) <= 0.15:
                refined_in = vis_in
                conf = round(min(0.99, max(fft_snr, vis_conf)), 4)
                method = "DHASH_FFT_VISUAL_LOCK"
            elif fft_in is not None and fft_snr >= 0.65:
                refined_in = fft_in
                conf = round(min(0.99, fft_snr), 4)
                method = "DHASH_PLUS_AUDIO_FFT"
            else:
                refined_in = vis_in
                conf = round(max(vis_conf, 1.0 - best_hamming / 64.0), 4)
                method = "DHASH_PLUS_VISUAL_30FPS"

            refined_in = max(0.0, min(refined_in, max(0.0, r_dur - dur)))
            refined_out = min(r_dur, refined_in + dur)
            head_h = round(refined_in, 4)
            tail_h = round(max(0.0, r_dur - refined_out), 4)
            used_by_file.setdefault(best_file, []).append((refined_in, refined_out))

            cuts.append(
                {
                    "shot_id": f"shot_{idx:03d}",
                    "rough_in_sec": s_in,
                    "rough_out_sec": s_out,
                    "rough_duration_sec": dur,
                    "source_file": best_file,
                    "source_exists": True,
                    "source_in_sec": round(refined_in, 4),
                    "source_out_sec": round(refined_out, 4),
                    "source_total_duration_sec": round(r_dur, 4),
                    "head_handle_sec": head_h,
                    "tail_handle_sec": tail_h,
                    "speed": 1.0,
                    "conform_method": method,
                    "dhash_hamming": round(best_hamming, 2),
                    "confidence": conf,
                }
            )
        else:
            # Unmatched shot (e.g., external insert in rough cut) -> fallback to rough_video slice
            cuts.append(
                {
                    "shot_id": f"shot_{idx:03d}",
                    "rough_in_sec": s_in,
                    "rough_out_sec": s_out,
                    "rough_duration_sec": dur,
                    "source_file": str(rough_video),
                    "source_exists": True,
                    "source_in_sec": s_in,
                    "source_out_sec": s_out,
                    "source_total_duration_sec": rough_meta["duration"],
                    "head_handle_sec": 0.0,
                    "tail_handle_sec": 0.0,
                    "speed": 1.0,
                    "conform_method": "FALLBACK_ROUGH_SLICE",
                    "dhash_hamming": round(best_hamming, 2),
                    "confidence": 0.5,
                }
            )

    unused_pool = build_unused_pool(raw_files, raw_meta_map, used_by_file)
    result = {
        "episode_id": episode_id,
        "mode": "RAW_CONFORM_DHASH_FFT",
        "rough_video": rough_meta,
        "raw_clips_count": len(raw_files),
        "shots_count": len(cuts),
        "shots": cuts,
        "authentic_unused_pool": unused_pool,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase A Reverse Source Conform (rough -> raw_clips)")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--rough-video", type=Path, default=None, help="Optional override rough video path")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Optional override raw_clips directory")
    parser.add_argument("--draft-json", type=Path, default=None, help="Optional override CapCut draft JSON")
    parser.add_argument("--out", type=Path, default=None, help="Optional override output JSON path")
    args = parser.parse_args()

    res = run_conform(
        args.episode,
        rough_video_override=args.rough_video,
        raw_dir_override=args.raw_dir,
        draft_json_override=args.draft_json,
        out_json_override=args.out,
    )
    print("=" * 72)
    print(f"MATCH_ROUGH_TO_RAW COMPLETE | EPISODE={args.episode} | MODE={res['mode']}")
    print(f"Shots Mapped: {res['shots_count']} | Raw Clips: {res['raw_clips_count']} | Unused Pool: {len(res['authentic_unused_pool'])}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
