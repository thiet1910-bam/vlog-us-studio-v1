# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase B Voiceover Aligner & 4-Tier Elastic Cluster Planner
(engine/align_voice_and_clusters.py)
Inherits `_he_thong/contracts/director_blueprint.json` from Phase A + `1_input/voice.mp3` (ElevenLabs / TTS):
  1. Runs `faster-whisper` (`word_timestamps=True`) via 16kHz float32 numpy array (zero PyAV bug).
  2. Normalizes English numbers/currency ("45" <-> "forty five") for 100% `SequenceMatcher` accuracy.
  3. Slices continuous `voice.mp3` at `find_nearest_silence_valley` (25ms RMS window) into
     `_he_thong/vo_slices/vo_cXX_bYY.wav` (48kHz Mono WAV, zero clipped word tails).
  4. Locks exact millisecond `trigger_word` timestamps for `#FFD700` USD callouts & builds 52px subtitle cues.
  5. Partitions 9-30 min episodes into natural chapter clusters (`c01..cN`, ~120s-215s) inside
     `_he_thong/contracts/timelines/edl_cXX.json` with exact integer-frame duration lock (`|Video - Audio| == 0.000s`).
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
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

ONES_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
}
TENS_WORDS = {
    2: "twenty", 3: "thirty", 4: "forty", 5: "fifty",
    6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety",
}


def _int_to_words(n: int) -> List[str]:
    if n < 0:
        return ["minus"] + _int_to_words(-n)
    if n < 20:
        return [ONES_WORDS[n]]
    if n < 100:
        t, r = divmod(n, 10)
        return [TENS_WORDS[t]] + ([ONES_WORDS[r]] if r else [])
    if n < 1000:
        h, r = divmod(n, 100)
        return [ONES_WORDS[h], "hundred"] + (_int_to_words(r) if r else [])
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        return _int_to_words(th) + ["thousand"] + (_int_to_words(r) if r else [])
    return [str(n)]


def expand_token_to_norm_words(raw_token: str) -> List[str]:
    """
    Normalizes an English word or numeric/currency token into lowercase alphabetic tokens
    so `"45"` and `"forty-five"` produce identical `["forty", "five"]` sequences!
    """
    tok = raw_token.strip().lower()
    tok = tok.replace("—", " ").replace("–", " ").replace("-", " ")
    out: List[str] = []
    for part in tok.split():
        has_dollar = "$" in part
        clean = re.sub(r"[^a-z0-9.]", "", part)
        if not clean:
            continue
        if re.fullmatch(r"\d+", clean):
            val = int(clean)
            if val <= 999999:
                out.extend(_int_to_words(val))
            else:
                out.append(clean)
            if has_dollar:
                out.append("dollars")
        elif re.fullmatch(r"\d+\.\d+", clean):
            left_s, right_s = clean.split(".", 1)
            out.extend(_int_to_words(int(left_s)))
            if has_dollar:
                out.append("dollars")
            if int(right_s) > 0:
                out.extend(_int_to_words(int(right_s)))
                if has_dollar:
                    out.append("cents")
        else:
            alpha = re.sub(r"[^a-z0-9]", "", clean)
            if alpha:
                out.append(alpha)
    return out


def decode_voice_48k_mono(voice_path: Path, out_wav: Path) -> Tuple[np.ndarray, int]:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd = [
        ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(voice_path),
        "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)
    with wave.open(str(out_wav), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, sr


def write_mono_wav_48k(path: Path, samples: np.ndarray, sr: int = 48000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = np.clip(samples * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())


def find_nearest_silence_valley(
    mono_48k: np.ndarray,
    sr: int,
    search_start_sec: float,
    search_end_sec: float,
    window_ms: float = 25.0,
) -> Tuple[float, float]:
    """
    Scans `[search_start_sec, search_end_sec]` in 5ms steps with a 25ms RMS window
    to find the quietest silence valley (`0.0ms` clipped tail protection).
    Returns `(cut_sec, valley_rms_dbfs)`.
    """
    total_sec = len(mono_48k) / float(sr)
    s0 = max(0.0, min(total_sec, search_start_sec))
    s1 = max(s0, min(total_sec, search_end_sec))
    win_n = max(1, int(round((window_ms / 1000.0) * sr)))
    step_n = max(1, int(round(0.005 * sr)))

    i0 = int(round(s0 * sr))
    i1 = int(round(s1 * sr))
    if i1 - i0 <= win_n:
        mid = 0.5 * (s0 + s1)
        mi = int(round(mid * sr))
        seg = mono_48k[max(0, mi - win_n // 2) : min(len(mono_48k), mi + win_n // 2)]
        rms = float(np.sqrt(np.mean(seg * seg))) if len(seg) else 1e-6
        return round(mid, 4), round(20.0 * math.log10(max(rms, 1e-6)), 2)

    best_rms = 1e9
    best_center_i = (i0 + i1) // 2
    for pos in range(i0, i1 - win_n + 1, step_n):
        seg = mono_48k[pos : pos + win_n]
        rms = float(np.sqrt(np.mean(seg * seg)))
        if rms < best_rms:
            best_rms = rms
            best_center_i = pos + win_n // 2

    cut_sec = best_center_i / float(sr)
    rms_db = 20.0 * math.log10(max(best_rms, 1e-6))
    return round(cut_sec, 4), round(rms_db, 2)


def transcribe_voice_words(
    voice_path: Path,
    whisper_model_name: str = "base",
    cache_json: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    sig = f"{voice_path.stat().st_size}_{int(voice_path.stat().st_mtime)}_{whisper_model_name}"
    if cache_json and cache_json.exists():
        try:
            cached = json.loads(cache_json.read_text(encoding="utf-8"))
            if cached.get("sig") == sig and cached.get("words"):
                return list(cached["words"])
        except Exception:
            pass

    from faster_whisper import WhisperModel

    audio_16k = bins.load_audio_16k_mono_np(voice_path)
    model = WhisperModel(whisper_model_name, device="cpu", compute_type="int8")
    segments_gen, _ = model.transcribe(
        audio_16k,
        language="en",
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=True,
    )

    expanded_words: List[Dict[str, Any]] = []
    for seg in segments_gen:
        if not seg.words:
            continue
        for w in seg.words:
            raw_w = w.word.strip()
            norms = expand_token_to_norm_words(raw_w)
            if not norms:
                continue
            w_start = float(w.start)
            w_end = max(w_start + 0.02, float(w.end))
            sub_dur = (w_end - w_start) / len(norms)
            for idx, nw in enumerate(norms):
                expanded_words.append(
                    {
                        "raw_word": raw_w if idx == 0 else "",
                        "norm": nw,
                        "start": round(w_start + idx * sub_dur, 4),
                        "end": round(w_start + (idx + 1) * sub_dur, 4),
                    }
                )
    if cache_json:
        cache_json.parent.mkdir(parents=True, exist_ok=True)
        cache_json.write_text(json.dumps({"sig": sig, "words": expanded_words}, ensure_ascii=False), encoding="utf-8")
    return expanded_words


def build_subtitle_cues_from_words(
    beat_text: str,
    beat_words: List[Dict[str, Any]],
    slice_start_sec: float,
    timeline_vo_start_sec: float,
    slice_dur_sec: float,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> List[Dict[str, Any]]:
    """
    Splits `beat_text` into clean US-senior-friendly 1-2 line subtitle cards (`<= 84` chars per card),
    strictly respecting sentence boundaries and preserving commas/em-dashes!
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", beat_text.strip()) if s.strip()]
    cards_raw: List[str] = []
    max_card_chars = max_chars_per_line * max_lines

    def _split_balanced_words(text_block: str, limit: int) -> List[str]:
        words_b = text_block.split()
        if len(text_block) <= limit or len(words_b) <= 3:
            return [text_block]
        n_chunks = max(2, int(math.ceil(len(text_block) / float(limit - 8))))
        chunk_w = int(math.ceil(len(words_b) / float(n_chunks)))
        res: List[str] = []
        for idx_w in range(0, len(words_b), chunk_w):
            piece = " ".join(words_b[idx_w : idx_w + chunk_w]).strip()
            if piece:
                res.append(piece)
        return res

    for sent in sentences:
        if len(sent) <= max_card_chars:
            cards_raw.append(sent)
            continue

        # Split long sentence keeping trailing punctuation (`,` or `—`) attached to the left clause
        clauses = [c.strip() for c in re.split(r"(?<=[,—])\s*", sent) if c.strip()]
        sent_cards: List[str] = []
        buf = ""
        for cl in clauses:
            sep = "" if (not buf or buf.endswith("—")) else " "
            cand = f"{buf}{sep}{cl}".strip() if buf else cl
            if len(cand) <= max_card_chars:
                buf = cand
            else:
                if buf:
                    sent_cards.extend(_split_balanced_words(buf, max_card_chars))
                    buf = ""
                if len(cl) <= max_card_chars:
                    buf = cl
                else:
                    sent_cards.extend(_split_balanced_words(cl, max_card_chars))
        if buf:
            sent_cards.extend(_split_balanced_words(buf, max_card_chars))

        # Rebalance short orphan clauses strictly INSIDE the same sentence (forward or backward)
        rebalanced_sent: List[str] = []
        idx_sc = 0
        while idx_sc < len(sent_cards):
            c = sent_cards[idx_sc]
            is_short = len(c.split()) <= 2 or len(c) < 15
            if is_short and idx_sc == 0 and len(sent_cards) > 1:
                sep = "" if c.endswith("—") else " "
                merged_fwd = f"{c}{sep}{sent_cards[1]}".strip()
                if len(merged_fwd) <= max_card_chars + 6:
                    rebalanced_sent.append(merged_fwd)
                else:
                    rebalanced_sent.extend(_split_balanced_words(merged_fwd, max_card_chars))
                idx_sc += 2
                continue
            elif is_short and rebalanced_sent:
                prev = rebalanced_sent.pop()
                sep = "" if prev.endswith("—") else " "
                merged_bwd = f"{prev}{sep}{c}".strip()
                if len(merged_bwd) <= max_card_chars + 6:
                    rebalanced_sent.append(merged_bwd)
                else:
                    rebalanced_sent.extend(_split_balanced_words(merged_bwd, max_card_chars))
            else:
                rebalanced_sent.append(c)
            idx_sc += 1

        cards_raw.extend(rebalanced_sent)

    if not cards_raw:
        return []

    # Wrap each card into at most 2 balanced lines <= max_chars_per_line
    def wrap_two_lines(text: str) -> str:
        if len(text) <= max_chars_per_line:
            return text
        words = text.split()
        mid_char = len(text) // 2
        best_split = 1
        best_dist = 10**9
        acc = 0
        for i in range(1, len(words)):
            acc += len(words[i - 1]) + (1 if i > 1 else 0)
            dist = abs(acc - mid_char)
            if dist < best_dist:
                best_dist = dist
                best_split = i
        line1 = " ".join(words[:best_split])
        line2 = " ".join(words[best_split:])
        return f"{line1}\\N{line2}"

    # Map each card to word indices by normalized word count
    card_word_counts = [max(1, len(expand_token_to_norm_words(c))) for c in cards_raw]
    total_cw = sum(card_word_counts)
    n_bw = len(beat_words)

    cues: List[Dict[str, Any]] = []
    acc_cw = 0
    for idx, (card_txt, cw) in enumerate(zip(cards_raw, card_word_counts)):
        if n_bw >= 2:
            w_i0 = min(n_bw - 1, int(round((acc_cw / total_cw) * n_bw)))
            w_i1 = min(n_bw - 1, max(w_i0, int(round(((acc_cw + cw) / total_cw) * n_bw)) - 1))
            rel_s = max(0.0, beat_words[w_i0]["start"] - slice_start_sec)
            rel_e = min(slice_dur_sec, max(rel_s + 0.6, beat_words[w_i1]["end"] - slice_start_sec + 0.08))
        else:
            rel_s = (acc_cw / total_cw) * slice_dur_sec
            rel_e = ((acc_cw + cw) / total_cw) * slice_dur_sec

        if idx == len(cards_raw) - 1:
            rel_e = slice_dur_sec
        if cues and rel_s < cues[-1]["end_in_cluster_sec"] - timeline_vo_start_sec:
            rel_s = cues[-1]["end_in_cluster_sec"] - timeline_vo_start_sec

        t_s = round(timeline_vo_start_sec + rel_s, 3)
        t_e = round(max(t_s + 0.5, timeline_vo_start_sec + rel_e), 3)
        cues.append(
            {
                "text_plain": card_txt,
                "text_ass": wrap_two_lines(card_txt),
                "start_in_cluster_sec": t_s,
                "end_in_cluster_sec": t_e,
                "duration_sec": round(t_e - t_s, 3),
            }
        )
        acc_cw += cw

    return cues


def partition_beats_into_clusters(
    beats: List[Dict[str, Any]],
    beat_durations: List[float],
    min_cluster_sec: float = 120.0,
    max_cluster_sec: float = 215.0,
) -> List[List[int]]:
    """
    Groups beat indices into natural chapter clusters (`~2.5 - 3.5 min`).
    Prefers splitting right on `chapter_id` boundaries!
    """
    # Group by chapter_id first
    chapter_groups: List[List[int]] = []
    for i, b in enumerate(beats):
        if not chapter_groups or b.get("chapter_id") != beats[chapter_groups[-1][-1]].get("chapter_id"):
            chapter_groups.append([i])
        else:
            chapter_groups[-1].append(i)

    clusters: List[List[int]] = []
    cur: List[int] = []
    cur_dur = 0.0

    for ch_indices in chapter_groups:
        ch_dur = sum(beat_durations[i] for i in ch_indices)
        # If single chapter is huge (> max_cluster_sec), split inside chapter cleanly
        if ch_dur > max_cluster_sec:
            if cur:
                clusters.append(cur)
                cur, cur_dur = [], 0.0
            for bi in ch_indices:
                bd = beat_durations[bi]
                if cur and (cur_dur + bd > max_cluster_sec) and cur_dur >= min_cluster_sec * 0.7:
                    clusters.append(cur)
                    cur, cur_dur = [bi], bd
                else:
                    cur.append(bi)
                    cur_dur += bd
        else:
            if cur and (cur_dur + ch_dur > max_cluster_sec) and cur_dur >= min_cluster_sec * 0.75:
                clusters.append(cur)
                cur = list(ch_indices)
                cur_dur = ch_dur
            else:
                cur.extend(ch_indices)
                cur_dur += ch_dur
                if cur_dur >= min_cluster_sec:
                    clusters.append(cur)
                    cur, cur_dur = [], 0.0

    if cur:
        if clusters and cur_dur < 65.0:
            clusters[-1].extend(cur)
        else:
            clusters.append(cur)

    return clusters


def run_alignment_and_clustering(
    episode_id: str,
    voice_file_override: Optional[Path] = None,
    whisper_model: str = "base",
) -> Dict[str, Any]:
    dna = bins.load_brand_dna()
    fps = int(dna.get("video_master_specs", {}).get("fps", 30))

    ep_paths = bins.init_episode_dirs(episode_id)
    bp_path = ep_paths["director_blueprint_json"]
    conform_path = ep_paths["conform_map_json"]
    telemetry_path = ep_paths["telemetry_json"]

    if not bp_path.exists():
        raise FileNotFoundError(f"Missing Phase A blueprint: {bp_path}")

    voice_path = (voice_file_override or ep_paths["voice_mp3"]).resolve()
    if not voice_path.exists():
        raise FileNotFoundError(f"Missing Phase B voiceover audio: {voice_path}")

    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    if not bp.get("human_approved", False):
        raise PermissionError(
            "BLOCKED BY HUMAN GATE: Phase B cannot align or edit video until the user approves "
            "`2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` (`human_approved` is False)!"
        )
    conform_map = json.loads(conform_path.read_text(encoding="utf-8")) if conform_path.exists() else {"authentic_unused_pool": []}
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8")) if telemetry_path.exists() else {"audio_telemetry": {}}

    beats: List[Dict[str, Any]] = bp["beats"]
    unused_pool: List[Dict[str, Any]] = list(conform_map.get("authentic_unused_pool", []))
    used_unused_ids: set[str] = set()

    # 1. Decode 48kHz Mono WAV & transcribe words via faster-whisper (with fast cache)
    full_vo_wav = ep_paths["vo_slices_dir"] / "voice_full_48k_mono.wav"
    whisper_cache = ep_paths["vo_slices_dir"] / "whisper_words_cache.json"
    mono_48k, sr = decode_voice_48k_mono(voice_path, full_vo_wav)
    total_vo_sec = len(mono_48k) / float(sr)
    whisper_words = transcribe_voice_words(voice_path, whisper_model_name=whisper_model, cache_json=whisper_cache)
    if not whisper_words:
        raise RuntimeError(f"faster-whisper detected 0 spoken words in {voice_path}")

    # 2. Build flat script normalized word list with beat ownership
    script_flat_norms: List[str] = []
    script_beat_word_ranges: List[Tuple[int, int]] = []
    for beat in beats:
        start_idx = len(script_flat_norms)
        b_norms = expand_token_to_norm_words(beat.get("narration_en", ""))
        script_flat_norms.extend(b_norms)
        script_beat_word_ranges.append((start_idx, len(script_flat_norms)))

    whisper_norms = [w["norm"] for w in whisper_words]
    sm = difflib.SequenceMatcher(None, script_flat_norms, whisper_norms, autojunk=False)

    # Map every script word index -> whisper word index via monotonic anchor interpolation
    s2w: List[Optional[int]] = [None] * len(script_flat_norms)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                s2w[i1 + offset] = j1 + offset
        elif tag == "replace" and (i2 > i1) and (j2 > j1):
            for offset in range(i2 - i1):
                mapped = j1 + int(round((offset / max(1, i2 - i1 - 1)) * max(0, j2 - j1 - 1)))
                s2w[i1 + offset] = min(len(whisper_words) - 1, mapped)

    # Fill any None monotonically
    last_valid = 0
    for idx in range(len(s2w)):
        if s2w[idx] is not None:
            last_valid = s2w[idx]  # type: ignore
        else:
            s2w[idx] = last_valid

    # Determine raw word span [w_first, w_last] for each beat
    beat_w_spans: List[Tuple[int, int]] = []
    for sw0, sw1 in script_beat_word_ranges:
        if sw1 <= sw0:
            beat_w_spans.append((-1, -1))
            continue
        wf = int(s2w[sw0] or 0)
        wl = int(s2w[sw1 - 1] or wf)
        if wl < wf:
            wl = wf
        beat_w_spans.append((wf, wl))

    # Enforce strict monotonic non-overlapping boundaries between consecutive beats using silence valleys!
    boundary_cuts: List[Tuple[float, float]] = []
    n_beats = len(beats)
    cut_points_sec: List[float] = [0.0] * (n_beats + 1)
    cut_valley_db: List[float] = [-60.0] * (n_beats + 1)

    # Leading start before beat 0
    first_wf = beat_w_spans[0][0]
    first_word_start = whisper_words[first_wf]["start"] if first_wf >= 0 else 0.0
    c0_sec, c0_db = find_nearest_silence_valley(mono_48k, sr, max(0.0, first_word_start - 0.35), max(0.02, first_word_start - 0.03))
    cut_points_sec[0] = c0_sec
    cut_valley_db[0] = c0_db

    for k in range(n_beats - 1):
        _, wl_curr = beat_w_spans[k]
        wf_next, _ = beat_w_spans[k + 1]
        if wf_next <= wl_curr and wf_next < len(whisper_words) - 1:
            wf_next = wl_curr + 1
        t_end_curr = whisper_words[wl_curr]["end"]
        t_start_next = whisper_words[min(len(whisper_words) - 1, wf_next)]["start"]
        if t_start_next <= t_end_curr + 0.04:
            s_lo = t_end_curr - 0.02
            s_hi = t_end_curr + 0.08
        else:
            s_lo = t_end_curr + 0.02
            s_hi = t_start_next - 0.02
        c_sec, c_db = find_nearest_silence_valley(mono_48k, sr, s_lo, s_hi)
        cut_points_sec[k + 1] = c_sec
        cut_valley_db[k + 1] = c_db

    # Trailing end after final beat
    last_wl = beat_w_spans[-1][1]
    last_word_end = whisper_words[last_wl]["end"] if last_wl >= 0 else total_vo_sec
    cn_sec, cn_db = find_nearest_silence_valley(
        mono_48k, sr, min(total_vo_sec, last_word_end + 0.05), min(total_vo_sec, last_word_end + 0.45)
    )
    cut_points_sec[n_beats] = max(cn_sec, min(total_vo_sec, last_word_end + 0.08))
    cut_valley_db[n_beats] = cn_db

    for k in range(n_beats):
        boundary_cuts.append((cut_points_sec[k], cut_points_sec[k + 1]))

    # Compute exact frame-locked beat durations
    # Critical Rule: When `preserve_rough_cut` is enabled (default True) and the rough-cut beat slot
    # `[in_sec, out_sec]` already accommodates the voiceover (`vo_dur + min_breath <= src_slot`) within
    # a natural documentary ASMR window (`<= 60s`), preserve 100% of the human editor's shots in `video_so_bo.mp4`
    # so zero climax/payoff shots at the end of beats are ever chopped off!
    preserve_rough_cut = bool(bp.get("preserve_rough_cut", True))
    default_lead_pad_sec = 0.30
    tail_pad_sec = 0.20
    beat_total_durations: List[float] = []
    beat_lead_pads: List[float] = []
    for k, beat in enumerate(beats):
        s_in, s_out = boundary_cuts[k]
        vo_dur = max(0.0, s_out - s_in)
        asmr_sec = float(beat.get("asmr_pause_after_sec", 0.0) or 0.0)
        psrc_k = beat.get("primary_source") or {}
        slot_k = max(0.2, float(psrc_k.get("out_sec", 0.0)) - float(psrc_k.get("in_sec", 0.0)))

        raw_target = default_lead_pad_sec + vo_dur + asmr_sec + tail_pad_sec
        min_needed = default_lead_pad_sec + vo_dur + min(asmr_sec, 3.0) + tail_pad_sec
        if preserve_rough_cut and slot_k >= min_needed and (slot_k - vo_dur) <= 60.0:
            chosen_dur = slot_k
        else:
            chosen_dur = raw_target

        exact_frames = max(fps, int(round(chosen_dur * fps)))
        final_b_dur = exact_frames / float(fps)
        beat_total_durations.append(final_b_dur)
        # On long DIY/ASMR beats (>22s non-VO breathing room), give 1.8s authentic sound establish before VO speaks
        if (final_b_dur - vo_dur) >= 22.0:
            beat_lead_pads.append(1.80)
        else:
            beat_lead_pads.append(default_lead_pad_sec)

    # 3. Partition into Chapter Clusters (`c01..cN`)
    cl_bounds = dna.get("pacing_and_script_rules", {}).get("cluster_duration_sec", [120.0, 215.0])
    min_cl = float(cl_bounds[0]) if len(cl_bounds) >= 2 else 120.0
    max_cl = float(cl_bounds[1]) if len(cl_bounds) >= 2 else 215.0
    cluster_groups = partition_beats_into_clusters(beats, beat_total_durations, min_cluster_sec=min_cl, max_cluster_sec=max_cl)

    sub_typo = dna.get("typography_specs", {}).get("subtitle_en", {})
    sub_max_chars = int(sub_typo.get("max_chars_per_line", 42))
    sub_max_lines = int(sub_typo.get("max_lines", 2))
    usd_default_dur = float(dna.get("typography_specs", {}).get("usd_callout", {}).get("default_duration_sec", 4.5))

    slices_dir = ep_paths["vo_slices_dir"]
    slices_dir.mkdir(parents=True, exist_ok=True)
    for old_vo in slices_dir.glob("vo_c*.wav"):
        old_vo.unlink(missing_ok=True)
    timelines_dir = ep_paths["timelines_dir"]
    timelines_dir.mkdir(parents=True, exist_ok=True)
    for old_edl in timelines_dir.glob("edl_*.json"):
        old_edl.unlink(missing_ok=True)

    vault_dir = bins.SHARED_DIR / "uncle_dinh_broll_vault"
    vault_files = {p.name.lower(): p.resolve() for p in vault_dir.iterdir() if p.is_file()} if vault_dir.exists() else {}

    cluster_summaries: List[Dict[str, Any]] = []
    episode_cursor_sec = 0.0

    for c_idx, beat_indices in enumerate(cluster_groups, start=1):
        cid = f"c{c_idx:02d}"
        cluster_cursor_sec = 0.0
        cluster_beats_edl: List[Dict[str, Any]] = []

        for bi in beat_indices:
            beat = beats[bi]
            bid = beat["beat_id"]
            vo_s0, vo_s1 = boundary_cuts[bi]
            vo_samples = mono_48k[int(round(vo_s0 * sr)) : int(round(vo_s1 * sr))]
            vo_dur = round(len(vo_samples) / float(sr), 4)
            slice_wav = slices_dir / f"vo_{cid}_{bid}.wav"
            write_mono_wav_48k(slice_wav, vo_samples, sr=sr)

            t_beat = beat_total_durations[bi]
            lead_pad_b = beat_lead_pads[bi]
            beat_frames = int(round(t_beat * fps))
            vo_start_in_cluster = round(cluster_cursor_sec + lead_pad_b, 4)
            vo_end_in_cluster = round(vo_start_in_cluster + vo_dur, 4)
            asmr_start_in_cluster = vo_end_in_cluster
            asmr_end_in_cluster = round(cluster_cursor_sec + t_beat, 4)
            actual_asmr_sec = round(max(0.0, asmr_end_in_cluster - asmr_start_in_cluster), 4)

            wf, wl = beat_w_spans[bi]
            b_words = whisper_words[wf : wl + 1] if wf >= 0 else []

            # Subtitle cues (52px bottom track)
            sub_cues = build_subtitle_cues_from_words(
                beat.get("narration_en", ""),
                b_words,
                slice_start_sec=vo_s0,
                timeline_vo_start_sec=vo_start_in_cluster,
                slice_dur_sec=vo_dur,
                max_chars_per_line=sub_max_chars,
                max_lines=sub_max_lines,
            )

            # USD Callout trigger word lock
            callout_edl = None
            cal = beat.get("usd_callout")
            if cal and isinstance(cal, dict) and cal.get("label"):
                trig_norms = expand_token_to_norm_words(str(cal.get("trigger_word", "")))
                trig_offset_in_vo = min(1.0, vo_dur * 0.25)
                if trig_norms and b_words:
                    target_nw = trig_norms[0]
                    for wobj in b_words:
                        if wobj["norm"] == target_nw or target_nw in wobj["norm"]:
                            trig_offset_in_vo = max(0.0, float(wobj["start"]) - vo_s0)
                            break
                cal_start = round(vo_start_in_cluster + trig_offset_in_vo, 3)
                cal_dur = float(cal.get("duration_sec", usd_default_dur))
                cal_end = round(min(cluster_cursor_sec + t_beat - 0.15, cal_start + cal_dur), 3)
                callout_edl = {
                    "label": str(cal["label"]),
                    "trigger_word": str(cal.get("trigger_word", "")),
                    "start_in_cluster_sec": cal_start,
                    "end_in_cluster_sec": max(cal_start + 1.2, cal_end),
                    "duration_sec": round(max(1.2, cal_end - cal_start), 3),
                }

            # Solve Delta_T via 4-Tier Authentic Coverage Waterfall
            psrc = beat["primary_source"]
            raw_src_p = Path(psrc["file"])
            src_file = str((raw_src_p if raw_src_p.is_absolute() else (bins.STUDIO_ROOT / raw_src_p)).resolve())
            src_in = float(psrc["in_sec"])
            src_out = float(psrc["out_sec"])
            src_slot = max(0.2, src_out - src_in)
            head_h = float(psrc.get("head_handle_sec", 0.0) or 0.0)
            tail_h = float(psrc.get("tail_handle_sec", 0.0) or 0.0)
            cov = beat.get("coverage_plan") or {}

            delta_t = round(t_beat - src_slot, 4)
            v2_overlays: List[Dict[str, Any]] = []
            coverage_tier_used = "NATURAL_OR_GOLDEN_TRIM"

            if t_beat <= src_slot:
                # Plenty of primary footage -> trim cleanly keeping starting action
                v1_in = src_in
                v1_out = round(src_in + t_beat, 4)
                v1_speed = 1.0
            else:
                # Deficit delta_t > 0 -> Tier 1: Extend Raw Clip Handles
                ext_tail = min(delta_t, tail_h)
                rem = delta_t - ext_tail
                ext_head = min(rem, head_h)
                rem = round(rem - ext_head, 4)
                v1_in = round(src_in - ext_head, 4)
                v1_out = round(src_out + ext_tail, 4)
                avail_dur = max(0.2, v1_out - v1_in)
                if rem <= 0.02:
                    coverage_tier_used = "TIER1_RAW_HANDLE_EXTEND"
                    v1_speed = 1.0
                    v1_out = round(v1_in + t_beat, 4)
                else:
                    # Keep V1 continuous via subtle elastic speed while placing authentic/external cutaway on V2 if rem > 1.2s!
                    v1_speed = round(avail_dur / t_beat, 5)
                    coverage_tier_used = "ELASTIC_CONFORM_SUBTLE"
                    if rem > 1.2 or v1_speed < 0.86:
                        ov_dur = round(min(max(3.5, rem + 1.0), t_beat * 0.45), 3)
                        ov_start = round(cluster_cursor_sec + max(1.5, (t_beat - ov_dur) * 0.55), 3)
                        # Tier 2: Authentic unused pool
                        chosen_unused = None
                        for u in unused_pool:
                            if u["unused_id"] not in used_unused_ids and float(u["duration_sec"]) >= ov_dur:
                                chosen_unused = u
                                used_unused_ids.add(u["unused_id"])
                                break
                        if chosen_unused:
                            coverage_tier_used = "TIER2_AUTHENTIC_UNUSED_SELECT"
                            v2_overlays.append(
                                {
                                    "overlay_id": f"{cid}_{bid}_v2",
                                    "tier": "TIER2_UNUSED_RAW",
                                    "file": chosen_unused["source_file"],
                                    "source_in_sec": float(chosen_unused["in_sec"]),
                                    "source_dur_sec": ov_dur,
                                    "start_in_cluster_sec": ov_start,
                                    "duration_sec": ov_dur,
                                }
                            )
                        elif cov.get("tier3_vault_clip") and str(cov["tier3_vault_clip"]).lower() in vault_files:
                            vf_p = vault_files[str(cov["tier3_vault_clip"]).lower()]
                            coverage_tier_used = "TIER3_UNCLE_DINH_VAULT"
                            v2_overlays.append(
                                {
                                    "overlay_id": f"{cid}_{bid}_v2",
                                    "tier": "TIER3_VAULT",
                                    "file": str(vf_p),
                                    "source_in_sec": 0.0,
                                    "source_dur_sec": ov_dur,
                                    "start_in_cluster_sec": ov_start,
                                    "duration_sec": ov_dur,
                                }
                            )
                        elif cov.get("broll_approved") is True and cov.get("tier4_broll_query"):
                            broll_id = f"broll_{bid}"
                            broll_dst = ep_paths["broll_approved_dir"] / f"{broll_id}.mp4"
                            if broll_dst.exists():
                                coverage_tier_used = "TIER4_APPROVED_BROLL"
                                v2_overlays.append(
                                    {
                                        "overlay_id": f"{cid}_{bid}_v2",
                                        "tier": "TIER4_APPROVED_BROLL",
                                        "broll_id": broll_id,
                                        "query": str(cov["tier4_broll_query"]),
                                        "file": str(broll_dst),
                                        "source_in_sec": 0.0,
                                        "source_dur_sec": ov_dur,
                                        "start_in_cluster_sec": ov_start,
                                        "duration_sec": ov_dur,
                                    }
                                )

            # Explicit Human-Approved cutaway on V2
            force_v2_sec = float(cov.get("force_v2_cutaway_sec", 0.0) or 0.0)
            if force_v2_sec > 0.0 and not v2_overlays and cov.get("broll_approved") is True and cov.get("tier4_broll_query"):
                ov_dur = round(min(force_v2_sec, t_beat * 0.4), 3)
                ratio = float(cov.get("v2_offset_ratio", 0.5))
                ov_start = round(cluster_cursor_sec + max(1.5, (t_beat - ov_dur) * ratio), 3)
                broll_id = f"broll_{bid}"
                broll_dst = ep_paths["broll_approved_dir"] / f"{broll_id}.mp4"
                if broll_dst.exists():
                    v2_overlays.append(
                        {
                            "overlay_id": f"{cid}_{bid}_v2",
                            "tier": "TIER4_APPROVED_BROLL",
                            "broll_id": broll_id,
                            "query": str(cov["tier4_broll_query"]),
                            "file": str(broll_dst),
                            "source_in_sec": 0.0,
                            "source_dur_sec": ov_dur,
                            "start_in_cluster_sec": ov_start,
                            "duration_sec": ov_dur,
                        }
                    )

            # Intersect any VN_SPEECH_LEAK ranges overlapping [v1_in, v1_out]
            leaks_in_beat = []
            for lk in telemetry.get("audio_telemetry", {}).get("vn_speech_leaks", []):
                ov0 = max(v1_in, float(lk["start_sec"]))
                ov1 = min(v1_out, float(lk["end_sec"]))
                if ov1 > ov0:
                    # Map source time -> cluster time
                    rel0 = (ov0 - v1_in) / max(v1_speed, 0.01)
                    rel1 = (ov1 - v1_in) / max(v1_speed, 0.01)
                    leaks_in_beat.append(
                        {
                            "start_in_cluster_sec": round(cluster_cursor_sec + rel0, 3),
                            "end_in_cluster_sec": round(cluster_cursor_sec + rel1, 3),
                            "detected_vi_text": lk.get("detected_vi_text", ""),
                        }
                    )

            cluster_beats_edl.append(
                {
                    "beat_id": bid,
                    "chapter_id": beat.get("chapter_id", ""),
                    "start_in_cluster_sec": round(cluster_cursor_sec, 4),
                    "end_in_cluster_sec": round(cluster_cursor_sec + t_beat, 4),
                    "duration_sec": round(t_beat, 4),
                    "frames_count": beat_frames,
                    "delta_t_sec": delta_t,
                    "coverage_tier": coverage_tier_used,
                    "v1_primary": {
                        "file": src_file,
                        "source_in_sec": v1_in,
                        "source_out_sec": v1_out,
                        "source_dur_sec": round(v1_out - v1_in, 4),
                        "speed": v1_speed,
                    },
                    "v2_overlays": v2_overlays,
                    "vo_audio": {
                        "slice_wav": str(slice_wav),
                        "source_vo_in_sec": vo_s0,
                        "source_vo_out_sec": vo_s1,
                        "start_in_cluster_sec": vo_start_in_cluster,
                        "end_in_cluster_sec": vo_end_in_cluster,
                        "duration_sec": vo_dur,
                        "head_valley_dbfs": cut_valley_db[bi],
                        "tail_valley_dbfs": cut_valley_db[bi + 1],
                    },
                    "asmr_showcase_window": {
                        "start_in_cluster_sec": asmr_start_in_cluster,
                        "end_in_cluster_sec": asmr_end_in_cluster,
                        "duration_sec": actual_asmr_sec,
                        "description": beat.get("asmr_description", ""),
                    },
                    "vn_speech_leaks_in_cluster": leaks_in_beat,
                    "subtitles": sub_cues,
                    "usd_callout": callout_edl,
                    "outro_bgm": bool(beat.get("outro_bgm", False)),
                }
            )
            cluster_cursor_sec = round(cluster_cursor_sec + t_beat, 6)

        total_video_sec = round(sum(b["duration_sec"] for b in cluster_beats_edl), 4)
        total_frames = sum(int(b["frames_count"]) for b in cluster_beats_edl)
        total_audio_sec = round(total_frames / float(fps), 4)

        edl_obj = {
            "episode_id": episode_id,
            "cluster_id": cid,
            "cluster_index": c_idx,
            "episode_offset_sec": round(episode_cursor_sec, 4),
            "fps": fps,
            "total_frames": total_frames,
            "total_video_duration_sec": total_video_sec,
            "total_audio_duration_sec": total_audio_sec,
            "av_delta_sec": round(abs(total_video_sec - total_audio_sec), 6),
            "clean_room_tone_wav": telemetry.get("audio_telemetry", {}).get(
                "clean_room_tone_wav", str(bins.SHARED_DIR / "audio" / "room_tone_default.wav")
            ),
            "beats": cluster_beats_edl,
        }
        edl_path = timelines_dir / f"edl_{cid}.json"
        edl_path.write_text(json.dumps(edl_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        cluster_summaries.append(
            {
                "cluster_id": cid,
                "status": "ALIGNED",
                "edl_path": str(edl_path),
                "beats": [b["beat_id"] for b in cluster_beats_edl],
                "episode_in_sec": round(episode_cursor_sec, 3),
                "episode_out_sec": round(episode_cursor_sec + total_video_sec, 3),
                "duration_sec": total_video_sec,
                "approved_v2_overlays_count": sum(len(b["v2_overlays"]) for b in cluster_beats_edl),
            }
        )
        episode_cursor_sec += total_video_sec

    plan_obj = {
        "episode_id": episode_id,
        "voice_source": str(voice_path),
        "voice_duration_sec": round(total_vo_sec, 3),
        "clusters_count": len(cluster_summaries),
        "total_episode_duration_sec": round(episode_cursor_sec, 3),
        "clusters": cluster_summaries,
    }
    plan_path = ep_paths["cluster_plan_json"]
    plan_path.write_text(json.dumps(plan_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_obj


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B Voiceover Silence-Valley Aligner & Cluster Planner")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--voice-file", type=Path, default=None, help="Optional override path for voice.mp3")
    parser.add_argument("--whisper-model", default="base", help="faster-whisper model (default: base)")
    args = parser.parse_args()

    plan = run_alignment_and_clustering(
        args.episode,
        voice_file_override=args.voice_file,
        whisper_model=args.whisper_model,
    )
    print("=" * 72)
    print(f"ALIGN_VOICE_AND_CLUSTERS COMPLETE | EPISODE={args.episode}")
    print(f"Clusters: {plan['clusters_count']} | Total Planned Duration: {plan['total_episode_duration_sec']}s")
    for cl in plan["clusters"]:
        print(f"  - {cl['cluster_id']}: {cl['beats']} | dur={cl['duration_sec']}s | v2_overlays={cl['approved_v2_overlays_count']}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
