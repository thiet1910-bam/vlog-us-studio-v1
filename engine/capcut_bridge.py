# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Two-Way CapCut PC Timeline Bridge (engine/capcut_bridge.py)
1. Reverse Conform (`parse_rough_draft`): Parses CapCut `draft_content.json` in ~0.05s to map
   every rough cut back to `raw_clips/` with microsecond precision + head/tail handles.
2. Forward 6-Track Export (`export_6track_draft`): Generates non-destructive `CapCut_Draft/`
   with `draft_content.json` + synchronized `draft_meta_info.json` across 6 independent tracks:
   - V1_Authentic_Primary (Main documentary footage)
   - V2_Broll_Overlay     (Cutaways / unused raw selects / external B-roll, muted)
   - T1_Subtitle_52px     (US Senior-friendly bottom subtitles)
   - T2_USD_Callout       (Warm Gold #FFD700 upper-left USD cultural callouts)
   - A1_Voiceover_US      (Sliced 48kHz narration clips)
   - A2_Location_ASMR     (Location audio / room-tone patched track with volume ducking keyframes)
   (+ optional A3_Warm_Outro_BGM when outro music is used)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402
import pycapcut as cc  # noqa: E402
from pycapcut import assets as cc_assets  # noqa: E402


def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def sec_to_us(sec: float) -> int:
    return max(0, int(round(float(sec) * 1_000_000)))


def parse_rough_draft(
    draft_json_path: Path | str,
    raw_clips_dir: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    Reverse-maps a CapCut `draft_content.json` file to raw source clips and computes available handles.
    """
    draft_p = Path(draft_json_path).resolve()
    if not draft_p.exists():
        raise FileNotFoundError(f"CapCut draft JSON not found: {draft_p}")

    data = json.loads(draft_p.read_text(encoding="utf-8"))
    raw_lookup: Dict[str, Path] = {}
    if raw_clips_dir:
        rdir = Path(raw_clips_dir).resolve()
        if rdir.exists():
            for fp in rdir.iterdir():
                if fp.is_file():
                    raw_lookup[fp.name.lower()] = fp

    # Index video materials by ID
    mat_by_id: Dict[str, Dict[str, Any]] = {}
    for vmat in data.get("materials", {}).get("videos", []):
        mid = vmat.get("id") or vmat.get("material_id")
        if mid:
            mat_by_id[mid] = vmat

    speed_by_id: Dict[str, float] = {}
    for smat in data.get("materials", {}).get("speeds", []):
        sid = smat.get("id")
        if sid:
            speed_by_id[sid] = float(smat.get("speed", 1.0) or 1.0)

    # Pick main video track (first video track with segments)
    video_tracks = [
        tr for tr in data.get("tracks", [])
        if tr.get("type") == "video" and tr.get("segments")
    ]
    if not video_tracks:
        return []

    main_track = video_tracks[0]
    cuts: List[Dict[str, Any]] = []
    for idx, seg in enumerate(main_track.get("segments", []), start=1):
        mid = seg.get("material_id", "")
        vmat = mat_by_id.get(mid, {})
        orig_path_str = vmat.get("path", "")
        bname = os.path.basename(orig_path_str).lower()
        resolved_path = raw_lookup.get(bname)
        if resolved_path is None and orig_path_str and os.path.exists(orig_path_str):
            resolved_path = Path(orig_path_str).resolve()

        tgt = seg.get("target_timerange") or {}
        src = seg.get("source_timerange") or {}
        tl_in = float(tgt.get("start", 0)) / 1_000_000.0
        tl_dur = float(tgt.get("duration", 0)) / 1_000_000.0
        src_in = float(src.get("start", 0)) / 1_000_000.0
        src_dur = float(src.get("duration", tl_dur * 1_000_000.0)) / 1_000_000.0

        mat_dur_sec = float(vmat.get("duration", 0)) / 1_000_000.0
        if resolved_path and resolved_path.exists() and mat_dur_sec <= 0:
            mat_dur_sec = bins.probe_media(resolved_path)["duration"]

        spd = float(seg.get("speed", 1.0) or 1.0)
        for ref_id in seg.get("extra_material_refs", []):
            if ref_id in speed_by_id:
                spd = speed_by_id[ref_id]
                break

        head_handle = round(max(0.0, src_in), 4)
        tail_handle = round(max(0.0, mat_dur_sec - (src_in + src_dur)), 4)

        cuts.append(
            {
                "shot_id": f"shot_{idx:03d}",
                "rough_in_sec": round(tl_in, 4),
                "rough_out_sec": round(tl_in + tl_dur, 4),
                "rough_duration_sec": round(tl_dur, 4),
                "source_file": str(resolved_path) if resolved_path else orig_path_str,
                "source_exists": bool(resolved_path and resolved_path.exists()),
                "source_in_sec": round(src_in, 4),
                "source_out_sec": round(src_in + src_dur, 4),
                "source_total_duration_sec": round(mat_dur_sec, 4),
                "head_handle_sec": head_handle,
                "tail_handle_sec": tail_handle,
                "speed": round(spd, 4),
                "conform_method": "CAPCUT_DRAFT_JSON",
                "confidence": 1.0,
            }
        )
    return cuts


def export_6track_draft(
    output_draft_dir: Path | str,
    draft_name: str,
    timeline_spec: Dict[str, Any],
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> Dict[str, Any]:
    """
    Exports a non-destructive 6-Track CapCut PC project folder directly into `output_draft_dir`
    containing `draft_content.json` and synchronized `draft_meta_info.json`.

    `timeline_spec` schema:
    {
      "v1_segments": [{"path": str, "timeline_start": float, "timeline_dur": float, "source_start": float, "source_dur": float, "volume": float}],
      "v2_segments": [{"path": str, "timeline_start": float, "timeline_dur": float, "source_start": float, "source_dur": float}],
      "t1_subtitles": [{"text": str, "timeline_start": float, "timeline_dur": float}],
      "t2_callouts":  [{"text": str, "timeline_start": float, "timeline_dur": float}],
      "a1_voiceover": [{"path": str, "timeline_start": float, "timeline_dur": float, "source_start": float, "volume": float}],
      "a2_location":  [{"path": str, "timeline_start": float, "timeline_dur": float, "source_start": float, "volume": float,
                        "keyframes": [{"offset_sec": float, "volume_linear": float}]}],
      "a3_bgm":       [{"path": str, "timeline_start": float, "timeline_dur": float, "source_start": float, "volume": float}]
    }
    """
    out_dir = Path(output_draft_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sf = cc.ScriptFile(width, height, fps, maintrack_adsorb=False)

    # Create 6 canonical tracks (+ optional 7th BGM track)
    sf.add_track(cc.TrackType.video, "V1_Authentic_Primary", relative_index=0)
    sf.add_track(cc.TrackType.video, "V2_Broll_Overlay", relative_index=1, mute=True)
    sf.add_track(cc.TrackType.text, "T1_Subtitle_52px", relative_index=0)
    sf.add_track(cc.TrackType.text, "T2_USD_Callout", relative_index=1)
    sf.add_track(cc.TrackType.audio, "A1_Voiceover_US", relative_index=0)
    sf.add_track(cc.TrackType.audio, "A2_Location_ASMR", relative_index=1)
    if timeline_spec.get("a3_bgm"):
        sf.add_track(cc.TrackType.audio, "A3_Warm_Outro_BGM", relative_index=2)

    video_mat_cache: Dict[str, cc.VideoMaterial] = {}
    audio_mat_cache: Dict[str, cc.AudioMaterial] = {}

    def get_vmat(p: str) -> cc.VideoMaterial:
        ap = str(Path(p).resolve())
        if ap not in video_mat_cache:
            video_mat_cache[ap] = cc.VideoMaterial(ap)
        return video_mat_cache[ap]

    def get_amat(p: str) -> cc.AudioMaterial:
        ap = str(Path(p).resolve())
        if ap not in audio_mat_cache:
            audio_mat_cache[ap] = cc.AudioMaterial(ap)
        return audio_mat_cache[ap]

    # 1. Track V1 — Primary Documentary Footage
    v1_cursor_us = 0
    for item in timeline_spec.get("v1_segments", []):
        vmat = get_vmat(item["path"])
        t_start_us = max(v1_cursor_us, sec_to_us(item["timeline_start"]))
        t_dur_us = max(33333, sec_to_us(item["timeline_dur"]))
        s_start_us = min(sec_to_us(item.get("source_start", 0.0)), max(0, vmat.duration - 33333))
        s_dur_us = sec_to_us(item.get("source_dur", item["timeline_dur"]))
        if s_start_us + s_dur_us > vmat.duration:
            s_dur_us = max(33333, vmat.duration - s_start_us)
            if abs(s_dur_us - t_dur_us) < 60000:
                t_dur_us = s_dur_us

        seg = cc.VideoSegment(
            vmat,
            target_timerange=cc.Timerange(t_start_us, t_dur_us),
            source_timerange=cc.Timerange(s_start_us, s_dur_us),
            volume=float(item.get("volume", 0.0)),
        )
        sf.add_segment(seg, "V1_Authentic_Primary")
        v1_cursor_us = t_start_us + t_dur_us

    # 2. Track V2 — Overlay Cutaways / B-Roll (Muted 100%)
    v2_cursor_us = 0
    for item in sorted(timeline_spec.get("v2_segments", []), key=lambda x: x["timeline_start"]):
        vmat = get_vmat(item["path"])
        t_start_us = max(v2_cursor_us, sec_to_us(item["timeline_start"]))
        t_dur_us = max(33333, sec_to_us(item["timeline_dur"]))
        s_start_us = min(sec_to_us(item.get("source_start", 0.0)), max(0, vmat.duration - 33333))
        s_dur_us = min(sec_to_us(item.get("source_dur", item["timeline_dur"])), max(33333, vmat.duration - s_start_us))
        seg = cc.VideoSegment(
            vmat,
            target_timerange=cc.Timerange(t_start_us, t_dur_us),
            source_timerange=cc.Timerange(s_start_us, s_dur_us),
            volume=0.0,
        )
        sf.add_segment(seg, "V2_Broll_Overlay")
        v2_cursor_us = t_start_us + t_dur_us

    # 3. Track T1 — US Senior-Friendly Bottom Subtitles (52px equiv -> CapCut size ~9.5, y=-0.82)
    t1_cursor_us = 0
    for sub in sorted(timeline_spec.get("t1_subtitles", []), key=lambda x: x["timeline_start"]):
        txt = str(sub.get("text", "")).strip()
        if not txt:
            continue
        t_start_us = max(t1_cursor_us, sec_to_us(sub["timeline_start"]))
        t_dur_us = max(100000, sec_to_us(sub["timeline_dur"]))
        tseg = cc.TextSegment(
            txt,
            cc.Timerange(t_start_us, t_dur_us),
            style=cc.TextStyle(
                size=9.5,
                bold=True,
                color=(1.0, 1.0, 1.0),
                align=1,
                auto_wrapping=True,
                max_line_width=0.82,
            ),
            clip_settings=cc.ClipSettings(transform_y=-0.80),
            border=cc.TextBorder(alpha=1.0, color=(0.0, 0.0, 0.0), width=42.0),
            shadow=cc.TextShadow(alpha=0.85, color=(0.0, 0.0, 0.0), diffuse=18.0, distance=5.0),
        )
        sf.add_segment(tseg, "T1_Subtitle_52px")
        t1_cursor_us = t_start_us + t_dur_us

    # 4. Track T2 — Warm Gold #FFD700 USD Cultural Callouts (upper-left transform_x=-0.58, transform_y=0.76)
    t2_cursor_us = 0
    for cal in sorted(timeline_spec.get("t2_callouts", []), key=lambda x: x["timeline_start"]):
        txt = str(cal.get("text", "")).strip()
        if not txt:
            continue
        t_start_us = max(t2_cursor_us, sec_to_us(cal["timeline_start"]))
        t_dur_us = max(200000, sec_to_us(cal["timeline_dur"]))
        cseg = cc.TextSegment(
            txt,
            cc.Timerange(t_start_us, t_dur_us),
            style=cc.TextStyle(
                size=8.2,
                bold=True,
                color=(1.0, 0.843, 0.0),  # #FFD700
                align=0,
                auto_wrapping=False,
            ),
            clip_settings=cc.ClipSettings(transform_x=-0.56, transform_y=0.76),
            border=cc.TextBorder(alpha=1.0, color=(0.0, 0.0, 0.0), width=35.0),
            background=cc.TextBackground(color="#1A1A1A", style=1, alpha=0.78, round_radius=0.22, height=0.16, width=0.16),
        )
        sf.add_segment(cseg, "T2_USD_Callout")
        t2_cursor_us = t_start_us + t_dur_us

    # 5. Track A1 — US Voiceover Slices
    a1_cursor_us = 0
    for vo in sorted(timeline_spec.get("a1_voiceover", []), key=lambda x: x["timeline_start"]):
        amat = get_amat(vo["path"])
        t_start_us = max(a1_cursor_us, sec_to_us(vo["timeline_start"]))
        s_start_us = min(sec_to_us(vo.get("source_start", 0.0)), max(0, amat.duration - 20000))
        t_dur_us = min(sec_to_us(vo.get("timeline_dur", amat.duration / 1e6)), max(20000, amat.duration - s_start_us))
        aseg = cc.AudioSegment(
            amat,
            target_timerange=cc.Timerange(t_start_us, t_dur_us),
            source_timerange=cc.Timerange(s_start_us, t_dur_us),
            volume=float(vo.get("volume", 1.0)),
        )
        sf.add_segment(aseg, "A1_Voiceover_US")
        a1_cursor_us = t_start_us + t_dur_us

    # 6. Track A2 — Location Audio & ASMR Envelope Keyframes
    a2_cursor_us = 0
    for loc in sorted(timeline_spec.get("a2_location", []), key=lambda x: x["timeline_start"]):
        amat = get_amat(loc["path"])
        t_start_us = max(a2_cursor_us, sec_to_us(loc["timeline_start"]))
        s_start_us = min(sec_to_us(loc.get("source_start", 0.0)), max(0, amat.duration - 20000))
        t_dur_us = min(sec_to_us(loc.get("timeline_dur", amat.duration / 1e6)), max(20000, amat.duration - s_start_us))
        aseg = cc.AudioSegment(
            amat,
            target_timerange=cc.Timerange(t_start_us, t_dur_us),
            source_timerange=cc.Timerange(s_start_us, t_dur_us),
            volume=float(loc.get("volume", 1.0)),
        )
        for kf in loc.get("keyframes", []):
            off_us = max(0, min(t_dur_us, sec_to_us(kf["offset_sec"])))
            aseg.add_keyframe(off_us, float(kf["volume_linear"]))
        sf.add_segment(aseg, "A2_Location_ASMR")
        a2_cursor_us = t_start_us + t_dur_us

    # 7. Optional Track A3 — Warm Acoustic Outro BGM
    a3_cursor_us = 0
    for bgm in sorted(timeline_spec.get("a3_bgm", []), key=lambda x: x["timeline_start"]):
        amat = get_amat(bgm["path"])
        t_start_us = max(a3_cursor_us, sec_to_us(bgm["timeline_start"]))
        s_start_us = min(sec_to_us(bgm.get("source_start", 0.0)), max(0, amat.duration - 20000))
        t_dur_us = min(sec_to_us(bgm.get("timeline_dur", amat.duration / 1e6)), max(20000, amat.duration - s_start_us))
        bseg = cc.AudioSegment(
            amat,
            target_timerange=cc.Timerange(t_start_us, t_dur_us),
            source_timerange=cc.Timerange(s_start_us, t_dur_us),
            volume=float(bgm.get("volume", db_to_linear(-26.0))),
        )
        bseg.add_fade(sec_to_us(1.5), sec_to_us(2.5))
        sf.add_segment(bseg, "A3_Warm_Outro_BGM")
        a3_cursor_us = t_start_us + t_dur_us

    # Write draft_content.json
    content_path = out_dir / "draft_content.json"
    sf.dump(str(content_path))

    # Build synchronized draft_meta_info.json so CapCut PC lists & opens the project cleanly
    meta_template_path = cc_assets.get_asset_path("DRAFT_META_TEMPLATE")
    meta_obj = json.loads(meta_template_path.read_text(encoding="utf-8"))
    now_us = int(time.time() * 1_000_000)
    meta_obj["draft_id"] = str(uuid.uuid4()).upper()
    meta_obj["draft_name"] = draft_name
    meta_obj["draft_fold_path"] = str(out_dir).replace("\\", "/")
    meta_obj["draft_root_path"] = str(out_dir.parent).replace("\\", "/")
    meta_obj["tm_duration"] = int(sf.duration)
    meta_obj["tm_draft_create"] = now_us
    meta_obj["tm_draft_modified"] = now_us
    meta_obj["draft_timeline_materials_size_"] = content_path.stat().st_size

    # Populate type 0 (video/photo/audio local materials) in draft_materials
    mat_entries: List[Dict[str, Any]] = []
    for vm in video_mat_cache.values():
        mat_entries.append(
            {
                "create_time": int(time.time()),
                "duration": vm.duration,
                "extra_info": vm.material_name,
                "file_Path": vm.path.replace("\\", "/"),
                "height": vm.height,
                "id": vm.material_id,
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_source": 1,
                "md5": "",
                "metetype": "video" if vm.material_type == "video" else "photo",
                "roughcut_time_range": {"duration": vm.duration, "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": vm.width,
            }
        )
    for am in audio_mat_cache.values():
        mat_entries.append(
            {
                "create_time": int(time.time()),
                "duration": am.duration,
                "extra_info": am.material_name,
                "file_Path": am.path.replace("\\", "/"),
                "height": 0,
                "id": am.material_id,
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_source": 1,
                "md5": "",
                "metetype": "music",
                "roughcut_time_range": {"duration": am.duration, "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": 0,
            }
        )

    for bucket in meta_obj.get("draft_materials", []):
        if bucket.get("type") == 0:
            bucket["value"] = mat_entries
            break

    meta_path = out_dir / "draft_meta_info.json"
    meta_path.write_text(json.dumps(meta_obj, ensure_ascii=False, indent=4), encoding="utf-8")

    return {
        "draft_dir": str(out_dir),
        "draft_content_json": str(content_path),
        "draft_meta_info_json": str(meta_path),
        "duration_sec": round(sf.duration / 1_000_000.0, 3),
        "track_names": list(sf.tracks.keys()),
        "video_materials_count": len(video_mat_cache),
        "audio_materials_count": len(audio_mat_cache),
    }
