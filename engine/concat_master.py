# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase B Lossless Master Concatenator & YouTube SRT Merger
(engine/concat_master.py)
Joins `_he_thong/clusters_temp/cum_01..cum_N.mp4` via fast lossless stream copy (`-c copy`) into:
  - `3_pha_B_video/Video_Hoan_Chinh_<EP_ID>.mp4`
  - `3_pha_B_video/Phu_De_YouTube_<EP_ID>.srt`
Optionally cleans temporary intermediate cluster files when `--cleanup-temp` is passed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402


def _fmt_srt_time(sec: float) -> str:
    s = max(0.0, float(sec))
    ms_total = int(round(s * 1000.0))
    hh, rem = divmod(ms_total, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def concat_episode_master(episode_id: str, cleanup_temp: bool = False) -> Dict[str, Any]:
    ep_paths = bins.init_episode_dirs(episode_id)
    plan_path = ep_paths["cluster_plan_json"]
    if not plan_path.exists():
        raise FileNotFoundError(f"Missing cluster_plan.json: {plan_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    temp_dir = ep_paths["clusters_temp_dir"]
    cluster_mp4s: List[Path] = []
    srt_entries: List[str] = []
    cue_idx = 0

    for idx, cl in enumerate(plan.get("clusters", []), start=1):
        mp4_p = temp_dir / f"cum_{idx:02d}.mp4"
        if not mp4_p.exists():
            raise FileNotFoundError(f"Missing rendered cluster video: {mp4_p}")
        cluster_mp4s.append(mp4_p)

        edl = json.loads(Path(cl["edl_path"]).read_text(encoding="utf-8"))
        ep_off = float(edl.get("episode_offset_sec", 0.0))
        for beat in edl.get("beats", []):
            for cue in beat.get("subtitles", []):
                s0 = ep_off + float(cue["start_in_cluster_sec"])
                s1 = ep_off + float(cue["end_in_cluster_sec"])
                if s1 <= s0 + 0.05:
                    continue
                cue_idx += 1
                txt_ass = str(cue.get("text_ass") or cue.get("text_plain", ""))
                txt_srt = txt_ass.replace("\\N", "\n").strip()
                srt_entries.append(f"{cue_idx}\n{_fmt_srt_time(s0)} --> {_fmt_srt_time(s1)}\n{txt_srt}\n")

    final_mp4 = ep_paths["final_video_mp4"]
    final_srt = ep_paths["final_srt"]
    final_mp4.parent.mkdir(parents=True, exist_ok=True)

    if len(cluster_mp4s) == 1:
        shutil.copy2(cluster_mp4s[0], final_mp4)
    else:
        concat_txt = temp_dir / "concat_list.txt"
        lines = [f"file '{str(p.resolve()).replace(chr(92), '/')}'" for p in cluster_mp4s]
        concat_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ffmpeg_exe = bins.resolve_ffmpeg()
        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final_mp4),
        ]
        subprocess.run(cmd, check=True)

    final_srt.write_text("\n".join(srt_entries), encoding="utf-8")
    probe = bins.probe_media(final_mp4)

    cleaned_bytes = 0
    if cleanup_temp and temp_dir.exists():
        for p in temp_dir.glob("cum_*.mp4"):
            cleaned_bytes += p.stat().st_size
            p.unlink(missing_ok=True)
        for p in temp_dir.glob("mix_*.wav"):
            cleaned_bytes += p.stat().st_size
            p.unlink(missing_ok=True)

    return {
        "episode_id": episode_id,
        "final_video_mp4": str(final_mp4),
        "final_srt": str(final_srt),
        "clusters_joined": len(cluster_mp4s),
        "subtitles_merged": cue_idx,
        "duration_sec": probe["duration"],
        "planned_duration_sec": float(plan.get("total_episode_duration_sec", 0.0)),
        "size_mb": round(probe["size_bytes"] / (1024 * 1024), 2),
        "pix_fmt": probe["pix_fmt"],
        "cleaned_temp_mb": round(cleaned_bytes / (1024 * 1024), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Join Cluster Videos into Final Master MP4 & YouTube SRT")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--cleanup-temp", action="store_true", help="Remove temporary cluster MP4/WAV files after join")
    args = parser.parse_args()

    res = concat_episode_master(args.episode, cleanup_temp=args.cleanup_temp)
    print("=" * 72)
    print(f"CONCAT_MASTER COMPLETE | EPISODE={args.episode}")
    print(f"  Final Video : {res['final_video_mp4']} ({res['size_mb']} MB, dur={res['duration_sec']}s, pix_fmt={res['pix_fmt']})")
    print(f"  YouTube SRT : {res['final_srt']} ({res['subtitles_merged']} cues)")
    if args.cleanup_temp:
        print(f"  Cleaned Temp: {res['cleaned_temp_mb']} MB freed")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
