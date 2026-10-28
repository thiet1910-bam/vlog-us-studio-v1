# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase B Cluster Video Renderer & 6-Track CapCut Exporter
(engine/render_cluster_and_capcut.py)
1. Renders each chapter cluster (`c01..cN`) into `_he_thong/clusters_temp/cum_XX.mp4`:
   - Safe 10-bit HEVC (`yuv420p10le`) -> 8-bit H.264 High@4.1 (`yuv420p`, `bt709`, `tv` range)
   - Multi-segment V1 conform + optional V2 cutaway overlays (with LUT unification)
   - Burned ASS typography: 52px US-senior bottom subtitles + #FFD700 Warm Gold USD callout cards
   - Muxed with frame-exact 48kHz stereo `mix_cXX.wav` (`-frames:v total_frames`)
2. Exports cluster `.srt` files (`_he_thong/clusters_temp/sub_cXX.srt`).
3. Generates the full-episode non-destructive 6-Track CapCut project in `3_pha_B_video/CapCut_Draft/`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402
import capcut_bridge  # noqa: E402


def _fmt_ass_time(sec: float) -> str:
    s = max(0.0, float(sec))
    hh = int(s // 3600)
    s -= hh * 3600
    mm = int(s // 60)
    s -= mm * 60
    cs = int(round(s * 100.0))
    ss, cc_rem = divmod(cs, 100)
    if ss >= 60:
        mm += 1
        ss -= 60
    return f"{hh}:{mm:02d}:{ss:02d}.{cc_rem:02d}"


def _fmt_srt_time(sec: float) -> str:
    s = max(0.0, float(sec))
    ms_total = int(round(s * 1000.0))
    hh, rem = divmod(ms_total, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _escape_ass_text(txt: str) -> str:
    # Preserve intentional `\N` line breaks while escaping literal braces
    placeholder = "__ASS_NL__"
    safe = txt.replace("\\N", placeholder).replace("{", "(").replace("}", ")")
    return safe.replace(placeholder, "\\N")


def write_cluster_ass_and_srt(
    edl: Dict[str, Any],
    ass_path: Path,
    srt_path: Path,
    dna: Dict[str, Any],
) -> Dict[str, int]:
    typo = dna.get("typography_specs", {})
    sub_spec = typo.get("subtitle_en", {})
    usd_spec = typo.get("usd_callout", {})

    font_name = str(sub_spec.get("font_name", "Segoe UI"))
    sub_fs = int(sub_spec.get("font_size_px", 52))
    sub_margin_v = int(sub_spec.get("margin_v_px", 65))
    usd_fs = int(usd_spec.get("font_size_px", 44))
    pos_x = int(usd_spec.get("position_x_px", 88))
    pos_y = int(usd_spec.get("position_y_px", 88))

    # ASS color order is &HAABBGGRR (AA=00 opaque, AA=FF transparent)
    # White #FFFFFF -> &H00FFFFFF | Warm Gold #FFD700 (R=FF,G=D7,B=00) -> &H0000D7FF
    ass_lines: List[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: SubEN,{font_name},{sub_fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H88000000,"
        f"-1,0,0,0,100,100,0,0,1,3.4,1.8,2,90,90,{sub_margin_v},1",
        f"Style: USDTag,{font_name},{usd_fs},&H0000D7FF,&H0000D7FF,&H381A1A1A,&H80000000,"
        f"-1,0,0,0,100,100,0.4,0,3,14.0,0.0,7,40,40,40,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    srt_blocks: List[str] = []
    sub_count = 0
    usd_count = 0

    for beat in edl.get("beats", []):
        for cue in beat.get("subtitles", []):
            s0 = float(cue["start_in_cluster_sec"])
            s1 = float(cue["end_in_cluster_sec"])
            if s1 <= s0 + 0.05:
                continue
            sub_count += 1
            ass_txt = _escape_ass_text(str(cue.get("text_ass") or cue.get("text_plain", "")))
            ass_lines.append(
                f"Dialogue: 1,{_fmt_ass_time(s0)},{_fmt_ass_time(s1)},SubEN,,0,0,0,,{{\\fad(110,110)}}{ass_txt}"
            )
            srt_txt = ass_txt.replace("\\N", "\n")
            srt_blocks.append(f"{sub_count}\n{_fmt_srt_time(s0)} --> {_fmt_srt_time(s1)}\n{srt_txt}\n")

        cal = beat.get("usd_callout")
        if cal and cal.get("label"):
            c0 = float(cal["start_in_cluster_sec"])
            c1 = float(cal["end_in_cluster_sec"])
            if c1 > c0 + 0.2:
                usd_count += 1
                lbl = _escape_ass_text(f"  {cal['label']}  ")
                ass_lines.append(
                    f"Dialogue: 2,{_fmt_ass_time(c0)},{_fmt_ass_time(c1)},USDTag,,0,0,0,,"
                    f"{{\\pos({pos_x},{pos_y})\\fad(180,220)}}{lbl}"
                )

    ass_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")
    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")
    return {"subtitles_count": sub_count, "usd_callouts_count": usd_count}


def _coalesce_contiguous_v1_runs(beats: List[Dict[str, Any]], fps: int) -> List[Dict[str, Any]]:
    """
    Merges adjacent V1 primary segments that come from the exact same file at speed=1.0
    and contiguous timestamps (`|next.in - prev.out| <= 0.05s`) so FFmpeg decodes sequentially
    in a single fast pass instead of spawning redundant HEVC decoders!
    """
    runs: List[Dict[str, Any]] = []
    for beat in beats:
        v1 = beat["v1_primary"]
        f_path = str(Path(v1["file"]).resolve())
        s_in = float(v1["source_in_sec"])
        s_out = float(v1["source_out_sec"])
        spd = float(v1.get("speed", 1.0))
        frames = int(beat["frames_count"])

        if (
            runs
            and runs[-1]["file"] == f_path
            and abs(runs[-1]["speed"] - 1.0) <= 0.002
            and abs(spd - 1.0) <= 0.002
            and abs(s_in - runs[-1]["source_out_sec"]) <= 0.06
        ):
            runs[-1]["source_out_sec"] = s_out
            runs[-1]["frames_count"] += frames
        else:
            runs.append(
                {
                    "file": f_path,
                    "source_in_sec": s_in,
                    "source_out_sec": s_out,
                    "speed": spd,
                    "frames_count": frames,
                }
            )
    for r in runs:
        r["target_dur_sec"] = round(r["frames_count"] / float(fps), 4)
        r["source_dur_sec"] = round(max(0.1, r["source_out_sec"] - r["source_in_sec"]), 4)
    return runs


def render_single_cluster_video(
    episode_id: str,
    edl_path: Path,
    preset_override: Optional[str] = None,
) -> Dict[str, Any]:
    dna = bins.load_brand_dna()
    vspec = dna.get("video_master_specs", {})
    width = int(vspec.get("width", 1920))
    height = int(vspec.get("height", 1080))
    fps = int(vspec.get("fps", 30))
    crf = int(vspec.get("crf", 18))
    preset = preset_override or str(vspec.get("preset", "fast"))

    ep_paths = bins.init_episode_dirs(episode_id)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    cid = edl["cluster_id"]
    c_idx = int(edl.get("cluster_index", 1))
    total_frames = int(edl["total_frames"])

    temp_dir = ep_paths["clusters_temp_dir"]
    mix_wav = temp_dir / f"mix_{cid}.wav"
    if not mix_wav.exists():
        raise FileNotFoundError(f"Missing mixed cluster audio: {mix_wav}. Run mix_4layer_audio.py first.")

    ass_path = temp_dir / f"sub_{cid}.ass"
    srt_path = temp_dir / f"sub_{cid}.srt"
    out_mp4 = temp_dir / f"cum_{c_idx:02d}.mp4"

    sub_stats = write_cluster_ass_and_srt(edl, ass_path, srt_path, dna)
    v1_runs = _coalesce_contiguous_v1_runs(edl["beats"], fps=fps)

    all_v2: List[Dict[str, Any]] = []
    for beat in edl["beats"]:
        for ov in beat.get("v2_overlays", []):
            if Path(ov["file"]).exists():
                all_v2.append(ov)

    ffmpeg_exe = bins.resolve_ffmpeg()
    cmd: List[str] = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]

    # Add V1 inputs (input indices 0 .. len(v1_runs)-1)
    for r in v1_runs:
        # Read 0.25s extra tail safety so `trim=duration=...` never starves by 1 frame
        read_dur = max(0.2, r["source_dur_sec"] + 0.35)
        cmd.extend(["-ss", f"{max(0.0, r['source_in_sec']):.4f}", "-t", f"{read_dur:.4f}", "-i", r["file"]])

    # Add V2 overlay inputs (indices len(v1_runs) .. len(v1_runs)+len(all_v2)-1)
    v2_base_idx = len(v1_runs)
    for ov in all_v2:
        ov_s0 = max(0.0, float(ov.get("source_in_sec", 0.0)))
        ov_dur = max(0.2, float(ov["duration_sec"]) + 0.25)
        cmd.extend(["-ss", f"{ov_s0:.4f}", "-t", f"{ov_dur:.4f}", "-i", ov["file"]])

    # Add mixed stereo WAV audio input last
    audio_idx = v2_base_idx + len(all_v2)
    cmd.extend(["-i", str(mix_wav)])

    filter_parts: List[str] = []
    v1_labels: List[str] = []
    for i, r in enumerate(v1_runs):
        spd = float(r["speed"])
        pts_expr = "PTS-STARTPTS" if abs(spd - 1.0) <= 0.002 else f"(PTS-STARTPTS)/{spd:.5f}"
        lbl = f"v1_{i}"
        filter_parts.append(
            f"[{i}:v]setpts={pts_expr},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
            f"tpad=stop_mode=clone:stop_duration=0.25,"
            f"trim=start=0:end_frame={r['frames_count']},setpts=PTS-STARTPTS,format=yuv420p[{lbl}]"
        )
        v1_labels.append(f"[{lbl}]")

    if len(v1_labels) == 1:
        cur_v = v1_labels[0]
    else:
        filter_parts.append(f"{''.join(v1_labels)}concat=n={len(v1_labels)}:v=1:a=0[v1_cat]")
        cur_v = "[v1_cat]"

    lut_path = bins.SHARED_DIR / "luts" / "warm_doc_unify.cube"
    lut_esc = bins.ffmpeg_escape_filter_path(lut_path) if lut_path.exists() else ""

    for j, ov in enumerate(all_v2):
        inp_idx = v2_base_idx + j
        st = float(ov["start_in_cluster_sec"])
        en = st + float(ov["duration_sec"])
        lut_f = f",lut3d=file='{lut_esc}':interp=trilinear" if lut_esc else ""
        ov_lbl = f"ov_{j}"
        nxt_lbl = f"v_ov_{j}"
        filter_parts.append(
            f"[{inp_idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p{lut_f},"
            f"setpts=PTS-STARTPTS+{st:.4f}/TB[{ov_lbl}]"
        )
        filter_parts.append(
            f"{cur_v}[{ov_lbl}]overlay=0:0:eof_action=pass:enable='between(t,{st:.4f},{en:.4f})'[{nxt_lbl}]"
        )
        cur_v = f"[{nxt_lbl}]"

    ass_esc = bins.ffmpeg_escape_filter_path(ass_path)
    fonts_esc = bins.ffmpeg_escape_filter_path(bins.SHARED_DIR / "fonts")
    filter_parts.append(f"{cur_v}ass=filename='{ass_esc}':fontsdir='{fonts_esc}',format=yuv420p[v_out]")

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v_out]",
            "-map",
            f"{audio_idx}:a:0",
            "-frames:v",
            str(total_frames),
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-video_track_timescale",
            "30000",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
    )

    subprocess.run(cmd, check=True)
    probe = bins.probe_media(out_mp4)
    return {
        "cluster_id": cid,
        "cluster_mp4": str(out_mp4),
        "cluster_srt": str(srt_path),
        "duration_sec": probe["duration"],
        "target_duration_sec": round(total_frames / float(fps), 4),
        "pix_fmt": probe["pix_fmt"],
        "v1_runs_count": len(v1_runs),
        "v2_overlays_count": len(all_v2),
        **sub_stats,
    }


def build_episode_capcut_draft(episode_id: str) -> Dict[str, Any]:
    """
    Combines all cluster EDLs into a single non-destructive 6-Track CapCut PC project
    inside `episodes/<EP_ID>/3_pha_B_video/CapCut_Draft/`.
    """
    dna = bins.load_brand_dna()
    vspec = dna.get("video_master_specs", {})
    aspec = dna.get("audio_master_specs", {})
    under_lin = capcut_bridge.db_to_linear(float(aspec.get("asmr_under_vo_db", -18.0)))
    show_lin = capcut_bridge.db_to_linear(float(aspec.get("asmr_pause_showcase_db", -6.0)))
    bgm_lin = capcut_bridge.db_to_linear(float(aspec.get("bgm_outro_db", -26.0)))

    ep_paths = bins.init_episode_dirs(episode_id)
    plan = json.loads(ep_paths["cluster_plan_json"].read_text(encoding="utf-8"))

    timeline_spec: Dict[str, List[Dict[str, Any]]] = {
        "v1_segments": [],
        "v2_segments": [],
        "t1_subtitles": [],
        "t2_callouts": [],
        "a1_voiceover": [],
        "a2_location": [],
        "a3_bgm": [],
    }

    bgm_path = bins.SHARED_DIR / "audio" / "bgm_warm_acoustic_outro.wav"

    for cl_item in plan.get("clusters", []):
        edl = json.loads(Path(cl_item["edl_path"]).read_text(encoding="utf-8"))
        ep_off = float(edl.get("episode_offset_sec", 0.0))

        for beat in edl.get("beats", []):
            b_tl_start = ep_off + float(beat["start_in_cluster_sec"])
            b_dur = float(beat["duration_sec"])
            v1 = beat["v1_primary"]

            timeline_spec["v1_segments"].append(
                {
                    "path": v1["file"],
                    "timeline_start": round(b_tl_start, 4),
                    "timeline_dur": round(b_dur, 4),
                    "source_start": float(v1["source_in_sec"]),
                    "source_dur": float(v1["source_dur_sec"]),
                    "volume": 0.0,
                }
            )

            for ov in beat.get("v2_overlays", []):
                if Path(ov["file"]).exists():
                    timeline_spec["v2_segments"].append(
                        {
                            "path": ov["file"],
                            "timeline_start": round(ep_off + float(ov["start_in_cluster_sec"]), 4),
                            "timeline_dur": float(ov["duration_sec"]),
                            "source_start": float(ov.get("source_in_sec", 0.0)),
                            "source_dur": float(ov.get("source_dur_sec", ov["duration_sec"])),
                        }
                    )

            for cue in beat.get("subtitles", []):
                timeline_spec["t1_subtitles"].append(
                    {
                        "text": str(cue["text_plain"]),
                        "timeline_start": round(ep_off + float(cue["start_in_cluster_sec"]), 4),
                        "timeline_dur": float(cue["duration_sec"]),
                    }
                )

            cal = beat.get("usd_callout")
            if cal and cal.get("label"):
                timeline_spec["t2_callouts"].append(
                    {
                        "text": str(cal["label"]),
                        "timeline_start": round(ep_off + float(cal["start_in_cluster_sec"]), 4),
                        "timeline_dur": float(cal["duration_sec"]),
                    }
                )

            vo = beat.get("vo_audio") or {}
            if vo.get("slice_wav") and Path(vo["slice_wav"]).exists():
                timeline_spec["a1_voiceover"].append(
                    {
                        "path": vo["slice_wav"],
                        "timeline_start": round(ep_off + float(vo["start_in_cluster_sec"]), 4),
                        "timeline_dur": float(vo["duration_sec"]),
                        "source_start": 0.0,
                        "volume": 1.0,
                    }
                )

            # A2 Location segment with smooth ducking keyframes relative to segment start
            vo_rel_s = max(0.0, float(vo.get("start_in_cluster_sec", 0.0)) - float(beat["start_in_cluster_sec"]))
            vo_rel_e = min(b_dur, float(vo.get("end_in_cluster_sec", 0.0)) - float(beat["start_in_cluster_sec"]))
            kfs = [
                {"offset_sec": 0.0, "volume_linear": show_lin if vo_rel_s >= 0.8 else under_lin},
                {"offset_sec": max(0.0, vo_rel_s - 0.2), "volume_linear": under_lin},
                {"offset_sec": vo_rel_e, "volume_linear": under_lin},
                {"offset_sec": min(b_dur, vo_rel_e + 0.4), "volume_linear": show_lin},
                {"offset_sec": b_dur, "volume_linear": show_lin},
            ]
            loc_wav = ep_paths["scan_dir"] / "rough_location_48k.wav"
            loc_src = str(loc_wav) if loc_wav.exists() and Path(v1["file"]) == ep_paths["rough_video"] else v1["file"]
            timeline_spec["a2_location"].append(
                {
                    "path": loc_src,
                    "timeline_start": round(b_tl_start, 4),
                    "timeline_dur": round(b_dur, 4),
                    "source_start": float(v1["source_in_sec"]),
                    "volume": 1.0,
                    "keyframes": kfs,
                }
            )

            if beat.get("outro_bgm") and bgm_path.exists():
                timeline_spec["a3_bgm"].append(
                    {
                        "path": str(bgm_path),
                        "timeline_start": round(b_tl_start, 4),
                        "timeline_dur": round(b_dur, 4),
                        "source_start": 0.0,
                        "volume": bgm_lin,
                    }
                )

    return capcut_bridge.export_6track_draft(
        output_draft_dir=ep_paths["capcut_draft_dir"],
        draft_name=f"UncleDinh_US_{episode_id}",
        timeline_spec=timeline_spec,
        width=int(vspec.get("width", 1920)),
        height=int(vspec.get("height", 1080)),
        fps=int(vspec.get("fps", 30)),
    )


def render_episode_clusters_and_capcut(
    episode_id: str,
    target_cluster_id: Optional[str] = None,
    preset_override: Optional[str] = None,
    skip_capcut: bool = False,
) -> Dict[str, Any]:
    ep_paths = bins.init_episode_dirs(episode_id)
    plan = json.loads(ep_paths["cluster_plan_json"].read_text(encoding="utf-8"))
    rendered: List[Dict[str, Any]] = []
    for cl in plan.get("clusters", []):
        cid = cl["cluster_id"]
        if target_cluster_id and cid != target_cluster_id:
            continue
        info = render_single_cluster_video(episode_id, Path(cl["edl_path"]), preset_override=preset_override)
        rendered.append(info)

    cc_info = None
    if not skip_capcut and target_cluster_id is None:
        cc_info = build_episode_capcut_draft(episode_id)

    return {"episode_id": episode_id, "clusters_rendered": rendered, "capcut_draft": cc_info}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Phase B Clusters & 6-Track CapCut Draft")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--cluster", default=None, help="Optional single cluster ID (e.g., c01)")
    parser.add_argument("--preset", default=None, help="Optional x264 preset override (e.g., fast / medium)")
    parser.add_argument("--skip-capcut", action="store_true", help="Skip CapCut draft generation")
    args = parser.parse_args()

    out = render_episode_clusters_and_capcut(
        args.episode,
        target_cluster_id=args.cluster,
        preset_override=args.preset,
        skip_capcut=args.skip_capcut,
    )
    print("=" * 72)
    print(f"RENDER_CLUSTER_AND_CAPCUT COMPLETE | EPISODE={args.episode}")
    for r in out["clusters_rendered"]:
        print(
            f"  - {r['cluster_id']} -> {Path(r['cluster_mp4']).name} | dur={r['duration_sec']}s "
            f"(target={r['target_duration_sec']}s) | pix_fmt={r['pix_fmt']} | subs={r['subtitles_count']} | usd={r['usd_callouts_count']}"
        )
    if out["capcut_draft"]:
        cc_d = out["capcut_draft"]
        print(f"  - CapCut 6-Track Draft -> {cc_d['draft_dir']} (dur={cc_d['duration_sec']}s, tracks={cc_d['track_names']})")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
