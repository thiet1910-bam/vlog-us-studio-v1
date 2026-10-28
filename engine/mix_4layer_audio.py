# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase B 4-Layer Documentary Audio Mixer (engine/mix_4layer_audio.py)
Renders frame-exact 48kHz Stereo WAV (`_he_thong/clusters_temp/mix_cXX.wav`) per cluster EDL:
  - Layer 1 (VO): Sliced US narration normalized to -14 LUFS active speech level + 15ms micro-fades
  - Layer 2 (Location ASMR): Authentic 80Hz high-passed location sound, VN speech-leak room-tone patching,
    0.3s seam smoothing, and smooth 0.4s convolution ducking (-18 dB under VO <-> -6 dB in ASMR pauses)
  - Layer 3 (USD Callout SFX): Subtle paper tick (`sfx_subtle_paper_tick.wav`) at -24 dBFS on USD triggers
  - Layer 4 (Warm Outro BGM): Optional acoustic guitar (`bgm_warm_acoustic_outro.wav`) at -26 dB on outro beats
  - Master Bus: Soft-knee ceiling limiter locked at -1.2 dBFS (zero clipping, exact sample lock `1600 * frames`).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402


def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def lin_to_db(val: float) -> float:
    return float(20.0 * math.log10(max(float(val), 1e-8)))


def read_wav_float32_stereo(wav_path: Path, target_sr: int = 48000) -> np.ndarray:
    """Reads any WAV (or transcodes via FFmpeg if rate/ch differs) into shape `(N, 2)` float32 `[-1, 1]`."""
    if wav_path.exists() and wav_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(wav_path), "rb") as wf:
                ch = wf.getnchannels()
                sr = wf.getframerate()
                sw = wf.getsampwidth()
                n = wf.getnframes()
                if sr == target_sr and sw == 2 and ch in (1, 2):
                    raw = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
                    if ch == 1:
                        return np.stack([raw, raw], axis=1)
                    return raw.reshape(-1, 2)
        except Exception:
            pass

    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(target_sr),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) % 2 == 1:
        pcm = pcm[:-1]
    return pcm.reshape(-1, 2)


def extract_location_segment_stereo(
    src_file: Path,
    src_in_sec: float,
    src_dur_sec: float,
    target_samples: int,
    speed: float = 1.0,
    highpass_hz: int = 80,
    sr: int = 48000,
) -> np.ndarray:
    """
    Extracts stereo 48kHz location audio from `src_file` `[src_in_sec, src_in_sec + src_dur_sec]`,
    applies `highpass=f=80` and optional `atempo` if `speed != 1.0`, and pads/trims to exact `target_samples`.
    """
    ffmpeg_exe = bins.resolve_ffmpeg()
    af_chain: List[str] = [f"highpass=f={int(highpass_hz)}"]
    spd = float(speed or 1.0)
    if abs(spd - 1.0) > 0.003:
        # Chain atempo within [0.5, 2.0] bounds
        rem_spd = spd
        while rem_spd < 0.5:
            af_chain.append("atempo=0.5")
            rem_spd /= 0.5
        while rem_spd > 2.0:
            af_chain.append("atempo=2.0")
            rem_spd /= 2.0
        af_chain.append(f"atempo={rem_spd:.5f}")

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, src_in_sec):.4f}",
        "-t",
        f"{max(0.05, src_dur_sec):.4f}",
        "-i",
        str(src_file),
        "-vn",
        "-af",
        ",".join(af_chain),
        "-ac",
        "2",
        "-ar",
        str(sr),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or len(proc.stdout) == 0:
        return np.zeros((target_samples, 2), dtype=np.float32)

    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) % 2 == 1:
        pcm = pcm[:-1]
    stereo = pcm.reshape(-1, 2)

    if len(stereo) >= target_samples:
        return stereo[:target_samples].copy()
    out = np.zeros((target_samples, 2), dtype=np.float32)
    out[: len(stereo)] = stereo
    if len(stereo) > 0:
        # Loop tail room ambience gently rather than hard digital zero
        tail_len = min(len(stereo), sr // 2)
        tail_tile = stereo[-tail_len:]
        pos = len(stereo)
        while pos < target_samples:
            take = min(tail_len, target_samples - pos)
            out[pos : pos + take] = tail_tile[:take] * 0.5
            pos += take
    return out


def apply_edge_micro_fades(arr: np.ndarray, fade_samples: int) -> np.ndarray:
    if len(arr) <= 2 or fade_samples <= 1:
        return arr
    f = min(fade_samples, len(arr) // 3)
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, f, dtype=np.float32)))
    if arr.ndim == 2:
        ramp = ramp[:, None]
    out = arr.copy()
    out[:f] *= ramp
    out[-f:] *= ramp[::-1]
    return out


def normalize_vo_active_level(mono_samples: np.ndarray, sr: int = 48000, target_lufs: float = -14.0) -> np.ndarray:
    """
    Normalizes active speech frames (above -42 dBFS gate) to match target integrated loudness
    (~ -15.5 dBFS gated stereo-summed mono RMS -> -14.0 LUFS stereo dual-mono).
    """
    if len(mono_samples) == 0:
        return mono_samples
    win = max(1, int(0.05 * sr))
    n_wins = len(mono_samples) // win
    if n_wins < 1:
        rms = float(np.sqrt(np.mean(mono_samples * mono_samples)) + 1e-8)
    else:
        trimmed = mono_samples[: n_wins * win].reshape(n_wins, win)
        win_rms = np.sqrt(np.mean(trimmed * trimmed, axis=1))
        gated = win_rms[win_rms > db_to_lin(-42.0)]
        rms = float(np.sqrt(np.mean(gated * gated))) if len(gated) > 0 else float(np.mean(win_rms) + 1e-8)

    # Dual-mono stereo LUFS ≈ 20*log10(mono_rms) + 3.01 - 0.691 -> mono_rms_target_db = target_lufs - 2.32
    target_mono_rms_db = target_lufs - 2.32
    curr_rms_db = lin_to_db(rms)
    gain_db = max(-10.0, min(14.0, target_mono_rms_db - curr_rms_db))
    scaled = mono_samples * db_to_lin(gain_db)
    # Prevent single-slice harsh peak before master bus
    peak = float(np.max(np.abs(scaled))) if len(scaled) else 0.0
    if peak > db_to_lin(-2.0):
        scaled *= db_to_lin(-2.0) / peak
    return scaled


def normalize_location_bed(stereo: np.ndarray, sr: int = 48000) -> np.ndarray:
    """
    Brings quiet camera/phone location audio to a consistent documentary pre-fader bed (~ -14 dBFS p90 RMS)
    so that `-6 dB` showcase pauses land cleanly around `-20 dBFS` and `-18 dB` under-VO sits around `-32 dBFS`,
    with a max boost cap of `+15 dB` so quiet room air never turns into white noise hiss.
    """
    if len(stereo) == 0:
        return stereo
    mono = 0.5 * (stereo[:, 0] + stereo[:, 1])
    win = max(1, int(0.10 * sr))
    n_wins = len(mono) // win
    if n_wins < 2:
        return stereo
    rms_w = np.sqrt(np.mean(mono[: n_wins * win].reshape(n_wins, win) ** 2, axis=1))
    p85 = float(np.percentile(rms_w, 85))
    if p85 < 1e-6:
        return stereo
    curr_db = lin_to_db(p85)
    target_prefader_db = -14.0
    gain_db = max(-12.0, min(14.0, target_prefader_db - curr_db))
    out = stereo * db_to_lin(gain_db)
    peak = float(np.max(np.abs(out)))
    if peak > db_to_lin(-1.5):
        out *= db_to_lin(-1.5) / peak
    return out


def loop_stereo_to_length(loop_src: np.ndarray, length: int, crossfade_n: int = 2400) -> np.ndarray:
    if len(loop_src) == 0 or length <= 0:
        return np.zeros((max(0, length), 2), dtype=np.float32)
    if len(loop_src) >= length:
        return loop_src[:length].copy()
    out = np.zeros((length, 2), dtype=np.float32)
    cf = min(crossfade_n, len(loop_src) // 4)
    pos = 0
    first = True
    while pos < length:
        if first or cf <= 0:
            take = min(len(loop_src), length - pos)
            out[pos : pos + take] = loop_src[:take]
            pos += take
            first = False
        else:
            start_write = max(0, pos - cf)
            avail = length - start_write
            take = min(len(loop_src), avail)
            seg = loop_src[:take].copy()
            ov = min(cf, take)
            ramp = np.linspace(0.0, 1.0, ov, dtype=np.float32)[:, None]
            out[start_write : start_write + ov] = out[start_write : start_write + ov] * (1.0 - ramp) + seg[:ov] * ramp
            if take > ov:
                out[start_write + ov : start_write + take] = seg[ov:take]
            pos = start_write + take
    return out


def write_stereo_wav_48k(path: Path, stereo: np.ndarray, sr: int = 48000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = np.clip(np.round(stereo * 32767.0), -32767, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())


def mix_single_cluster(
    episode_id: str,
    edl_path: Path,
    out_wav_path: Optional[Path] = None,
) -> Dict[str, Any]:
    dna = bins.load_brand_dna()
    aspec = dna.get("audio_master_specs", {})
    sr = int(aspec.get("sample_rate", 48000))
    vo_target_lufs = float(aspec.get("vo_target_lufs", -14.0))
    under_vo_db = float(aspec.get("asmr_under_vo_db", -18.0))
    showcase_db = float(aspec.get("asmr_pause_showcase_db", -6.0))
    duck_sec = float(aspec.get("ducking_transition_sec", 0.4))
    hp_hz = int(aspec.get("location_highpass_hz", 80))
    xfade_sec = float(aspec.get("location_crossfade_sec", 0.3))
    bgm_db = float(aspec.get("bgm_outro_db", -26.0))
    tick_db = float(aspec.get("usd_tick_sfx_db", -24.0))
    ceiling_db = float(aspec.get("master_ceiling_dbfs", -1.2))

    ep_paths = bins.init_episode_dirs(episode_id)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    cid = edl["cluster_id"]
    fps = int(edl.get("fps", 30))
    total_frames = int(edl["total_frames"])
    samples_per_frame = sr // fps
    total_samples = total_frames * samples_per_frame

    if out_wav_path is None:
        out_wav_path = ep_paths["clusters_temp_dir"] / f"mix_{cid}.wav"

    vo_bus = np.zeros((total_samples, 2), dtype=np.float32)
    vo_active_mask = np.zeros(total_samples, dtype=np.float32)
    loc_bus = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_bus = np.zeros((total_samples, 2), dtype=np.float32)
    bgm_bus = np.zeros((total_samples, 2), dtype=np.float32)

    room_tone_path = Path(edl.get("clean_room_tone_wav", str(bins.SHARED_DIR / "audio" / "room_tone_default.wav")))
    if not room_tone_path.exists():
        room_tone_path = bins.SHARED_DIR / "audio" / "room_tone_default.wav"
    room_tone_stereo = read_wav_float32_stereo(room_tone_path, target_sr=sr)

    tick_path = bins.SHARED_DIR / "audio" / "sfx_subtle_paper_tick.wav"
    tick_stereo = read_wav_float32_stereo(tick_path, target_sr=sr) if tick_path.exists() else np.zeros((0, 2), dtype=np.float32)
    if len(tick_stereo) > 0:
        t_pk = float(np.max(np.abs(tick_stereo)))
        if t_pk > 1e-6:
            tick_stereo = tick_stereo * (db_to_lin(tick_db) / t_pk)

    bgm_path = bins.SHARED_DIR / "audio" / "bgm_warm_acoustic_outro.wav"
    bgm_src = read_wav_float32_stereo(bgm_path, target_sr=sr) if bgm_path.exists() else np.zeros((0, 2), dtype=np.float32)

    seam_fade_n = max(1, int(round(0.5 * xfade_sec * sr)))
    vo_fade_n = max(1, int(round(0.015 * sr)))
    patched_leaks_count = 0
    usd_ticks_count = 0
    outro_bgm_used = False

    for beat in edl.get("beats", []):
        b_start_s = float(beat["start_in_cluster_sec"])
        b_frames = int(beat["frames_count"])
        b_i0 = int(round(b_start_s * sr))
        b_len = b_frames * samples_per_frame
        b_i1 = min(total_samples, b_i0 + b_len)
        b_len = max(0, b_i1 - b_i0)
        if b_len <= 0:
            continue

        # --- Layer 2: Authentic Location Audio for this beat ---
        v1 = beat["v1_primary"]
        loc_seg = extract_location_segment_stereo(
            src_file=Path(v1["file"]),
            src_in_sec=float(v1["source_in_sec"]),
            src_dur_sec=float(v1["source_dur_sec"]),
            target_samples=b_len,
            speed=float(v1.get("speed", 1.0)),
            highpass_hz=hp_hz,
            sr=sr,
        )
        loc_seg = normalize_location_bed(loc_seg, sr=sr)

        # Patch any VN_SPEECH_LEAK windows inside this beat with clean room tone
        for lk in beat.get("vn_speech_leaks_in_cluster", []):
            lk0 = max(b_i0, int(round(float(lk["start_in_cluster_sec"]) * sr))) - b_i0
            lk1 = min(b_i1, int(round(float(lk["end_in_cluster_sec"]) * sr))) - b_i0
            if lk1 > lk0 and len(room_tone_stereo) > 0:
                pad = min(int(0.20 * sr), lk0, b_len - lk1)
                p0 = lk0 - pad
                p1 = lk1 + pad
                patch = loop_stereo_to_length(room_tone_stereo, p1 - p0)
                patch = normalize_location_bed(patch, sr=sr) * 0.75
                if pad > 16:
                    r_in = np.linspace(0.0, 1.0, pad, dtype=np.float32)[:, None]
                    loc_seg[p0 : p0 + pad] = loc_seg[p0 : p0 + pad] * (1.0 - r_in) + patch[:pad] * r_in
                    loc_seg[p0 + pad : p1 - pad] = patch[pad : len(patch) - pad]
                    loc_seg[p1 - pad : p1] = patch[len(patch) - pad :] * (1.0 - r_in) + loc_seg[p1 - pad : p1] * r_in
                else:
                    loc_seg[p0:p1] = patch
                patched_leaks_count += 1

        # Smooth 0.15s edge micro-fades + gentle room-tone bed across beat boundaries so zero cut clicks occur
        loc_seg = apply_edge_micro_fades(loc_seg, seam_fade_n)
        loc_bus[b_i0:b_i1] += loc_seg

        # --- Layer 1: Voiceover Slice ---
        vo_info = beat.get("vo_audio") or {}
        slice_p = Path(vo_info.get("slice_wav", ""))
        if slice_p.exists() and float(vo_info.get("duration_sec", 0.0)) > 0.02:
            vo_st = read_wav_float32_stereo(slice_p, target_sr=sr)
            vo_mono = 0.5 * (vo_st[:, 0] + vo_st[:, 1])
            vo_mono = normalize_vo_active_level(vo_mono, sr=sr, target_lufs=vo_target_lufs)
            vo_mono = apply_edge_micro_fades(vo_mono, vo_fade_n)
            v_i0 = max(0, int(round(float(vo_info["start_in_cluster_sec"]) * sr)))
            v_take = min(len(vo_mono), total_samples - v_i0)
            if v_take > 0:
                vo_bus[v_i0 : v_i0 + v_take, 0] += vo_mono[:v_take]
                vo_bus[v_i0 : v_i0 + v_take, 1] += vo_mono[:v_take]
                # Mark VO ducking mask with 0.12s pre-duck lead and 0.18s post-duck hold
                m0 = max(0, v_i0 - int(0.12 * sr))
                m1 = min(total_samples, v_i0 + v_take + int(0.18 * sr))
                vo_active_mask[m0:m1] = 1.0

        # --- Layer 3: USD Callout Paper Tick SFX ---
        cal = beat.get("usd_callout")
        if cal and len(tick_stereo) > 0:
            c_i0 = max(0, int(round(float(cal["start_in_cluster_sec"]) * sr)))
            c_take = min(len(tick_stereo), total_samples - c_i0)
            if c_take > 0:
                sfx_bus[c_i0 : c_i0 + c_take] += tick_stereo[:c_take]
                usd_ticks_count += 1

        # --- Layer 4: Warm Acoustic Outro BGM (only when outro_bgm == True) ---
        if beat.get("outro_bgm") and len(bgm_src) > 0:
            outro_bgm_used = True
            bgm_seg = loop_stereo_to_length(bgm_src, b_len)
            b_pk = float(np.max(np.abs(bgm_seg)))
            if b_pk > 1e-6:
                bgm_seg = bgm_seg * (db_to_lin(bgm_db + 3.0) / b_pk)
            # Gentle 1.8s fade-in and 2.2s fade-out
            fi = min(int(1.8 * sr), b_len // 3)
            fo = min(int(2.2 * sr), b_len // 3)
            if fi > 1:
                bgm_seg[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)[:, None]
            if fo > 1:
                bgm_seg[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)[:, None]
            bgm_bus[b_i0:b_i1] += bgm_seg

    # Add ultra-subtle continuous room-tone glue (-42 dBFS) across seam boundaries so zero digital silence holes exist
    if len(room_tone_stereo) > 0:
        glue = loop_stereo_to_length(room_tone_stereo, total_samples)
        g_pk = float(np.max(np.abs(glue)))
        if g_pk > 1e-6:
            glue = glue * (db_to_lin(-40.0) / g_pk)
        loc_bus += glue

    # Smooth 0.4s Convolution Gain Envelope for Location ASMR (-18 dB under VO <-> -6 dB in ASMR pauses)
    kern_n = max(3, int(round(duck_sec * sr)))
    if kern_n % 2 == 0:
        kern_n += 1
    hann = np.hanning(kern_n).astype(np.float32)
    hann /= float(np.sum(hann))
    smooth_vo_mask = np.convolve(vo_active_mask, hann, mode="same")
    gain_under = db_to_lin(under_vo_db)
    gain_show = db_to_lin(showcase_db)
    loc_gain_env = gain_show + (gain_under - gain_show) * np.clip(smooth_vo_mask, 0.0, 1.0)
    loc_mixed = loc_bus * loc_gain_env[:, None]

    # Subtle ducking on Outro BGM when VO speaks (-4 dB under VO, 0 dB swell in outro pause)
    if outro_bgm_used:
        bgm_env = 1.0 - 0.37 * np.clip(smooth_vo_mask, 0.0, 1.0)
        bgm_bus *= bgm_env[:, None]

    master = vo_bus + loc_mixed + sfx_bus + bgm_bus

    # Master Bus True-Peak Ceiling Limiter (-1.2 dBFS = 0.87096)
    ceiling_lin = db_to_lin(ceiling_db)
    knee_lin = ceiling_lin * 0.88
    abs_m = np.abs(master)
    over = abs_m > knee_lin
    if np.any(over):
        headroom = max(1e-6, ceiling_lin - knee_lin)
        sign_m = np.sign(master[over])
        excess = (abs_m[over] - knee_lin) / headroom
        master[over] = sign_m * (knee_lin + headroom * np.tanh(excess))
    master = np.clip(master, -ceiling_lin, ceiling_lin)

    write_stereo_wav_48k(out_wav_path, master, sr=sr)

    peak_lin = float(np.max(np.abs(master))) if len(master) else 0.0
    rms_lin = float(np.sqrt(np.mean(master * master))) if len(master) else 0.0
    stats = {
        "episode_id": episode_id,
        "cluster_id": cid,
        "wav_path": str(out_wav_path),
        "sample_rate": sr,
        "channels": 2,
        "total_frames": total_frames,
        "total_samples": total_samples,
        "exact_duration_sec": round(total_samples / float(sr), 4),
        "max_peak_dbfs": round(lin_to_db(peak_lin), 2),
        "overall_rms_dbfs": round(lin_to_db(rms_lin), 2),
        "patched_vn_leaks": patched_leaks_count,
        "usd_sfx_ticks": usd_ticks_count,
        "outro_bgm_used": outro_bgm_used,
    }
    stats_path = out_wav_path.with_name(f"mix_{cid}_stats.json")
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def mix_episode_clusters(episode_id: str, target_cluster_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ep_paths = bins.init_episode_dirs(episode_id)
    plan_path = ep_paths["cluster_plan_json"]
    if not plan_path.exists():
        raise FileNotFoundError(f"Missing cluster_plan.json: {plan_path}. Run align_voice_and_clusters.py first.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    results: List[Dict[str, Any]] = []
    for cl in plan.get("clusters", []):
        cid = cl["cluster_id"]
        if target_cluster_id and cid != target_cluster_id:
            continue
        edl_p = Path(cl["edl_path"])
        st = mix_single_cluster(episode_id, edl_p)
        results.append(st)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B 4-Layer Audio Mixer (-14 LUFS VO + Dynamic ASMR + Limiter)")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--cluster", default=None, help="Optional single cluster ID (e.g., c01)")
    args = parser.parse_args()

    res = mix_episode_clusters(args.episode, target_cluster_id=args.cluster)
    print("=" * 72)
    print(f"MIX_4LAYER_AUDIO COMPLETE | EPISODE={args.episode} | CLUSTERS={len(res)}")
    for r in res:
        print(
            f"  - {r['cluster_id']}: dur={r['exact_duration_sec']}s | peak={r['max_peak_dbfs']} dBFS | "
            f"rms={r['overall_rms_dbfs']} dBFS | ticks={r['usd_sfx_ticks']} | bgm={r['outro_bgm_used']}"
        )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
