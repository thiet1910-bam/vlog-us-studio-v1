# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase A Script & Director Blueprint Auditor (engine/validate_script_gate.py)
Audits the Phase A Director deliverables with a strict 2-step Human Gate:
  - Step A1 (`--mode check-draft`): Validates `_he_thong/contracts/director_blueprint.json`
    and renders `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` for human review.
    Does NOT export ElevenLabs text or download external B-roll.
  - Step A2 (`--mode approve-export`): Runs ONLY AFTER human approval. Marks `human_approved=True`,
    exports crystal-clean `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt` (0 stage tags `[...]`),
    updates `_he_thong/contracts/story_memory.json`, and fetches ONLY approved B-roll (if any).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402


def count_words(text: str) -> int:
    cleaned = re.sub(r"\[[^\]]*\]", " ", text)
    words = re.findall(r"[A-Za-z0-9'$]+(?:-[A-Za-z0-9]+)?", cleaned)
    return len(words)


def clean_for_elevenlabs(paragraphs: List[str]) -> str:
    """
    Removes any accidental `[...]` tags, markdown formatting, or extra whitespace,
    preserving natural em-dashes (`—`) and ellipses (`...`) for ElevenLabs pacing.
    """
    cleaned_blocks: List[str] = []
    for raw in paragraphs:
        txt = re.sub(r"\[[^\]]*\]", "", raw)
        txt = txt.replace("**", "").replace("*", "")
        txt = re.sub(r"\s+", " ", txt).strip()
        # Strip surrounding straight/smart quotes if paragraph was quoted
        if len(txt) >= 2 and txt[0] in {'"', "“"} and txt[-1] in {'"', "”"}:
            txt = txt[1:-1].strip()
        if txt:
            cleaned_blocks.append(txt)
    return "\n\n".join(cleaned_blocks) + "\n"


def _fmt_mmss(sec: float) -> str:
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:04.1f}"


def render_human_script_md(episode_id: str, extra_notes_md: str = "") -> Path:
    """
    Renders a clean, human-readable bilingual review script at `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md`
    directly from `_he_thong/contracts/director_blueprint.json`.
    """
    ep_paths = bins.init_episode_dirs(episode_id)
    bp = json.loads(ep_paths["director_blueprint_json"].read_text(encoding="utf-8"))
    tel_path = ep_paths["telemetry_json"]
    leak_cnt = 0
    if tel_path.exists():
        tel = json.loads(tel_path.read_text(encoding="utf-8"))
        leak_cnt = int(tel.get("audio_telemetry", {}).get("vn_speech_leak_count", 0))

    beats = bp.get("beats", [])
    total_words = sum(count_words(str(b.get("narration_en", ""))) for b in beats)
    total_asmr = sum(float(b.get("asmr_pause_after_sec", 0.0) or 0.0) for b in beats)
    target_dur = float(bp.get("target_duration_sec", 0.0) or 0.0)
    approved_broll_cnt = sum(1 for b in beats if (b.get("coverage_plan") or {}).get("broll_approved") is True)
    status_badge = "✅ ĐÃ DUYỆT" if bp.get("human_approved") else "⏸️ ĐANG ĐỢI BẠN ĐỌC DUYỆT (Chưa xuất chữ ElevenLabs — Chưa tải B-roll)"

    lines: List[str] = [
        f"# 🎬 KỊCH BẢN HOÀN CHỈNH PHA A — TẬP {episode_id}",
        "",
        f"> **Trạng thái:** **{status_badge}**",
        f"> - **Tiêu đề Tiếng Anh:** `\"{bp.get('episode_title_en', '')}\"`",
        f"> - **Độ dài `video_so_bo.mp4` thực tế:** `{_fmt_mmss(target_dur)}` (`{target_dur:.2f}s`) | **Đoạn lọt tiếng nói tiếng Việt:** `{leak_cnt} đoạn`",
        f"> - **Nhịp điệu toàn tập:** `{len(beats)} phân đoạn` — Tổng `{total_words} từ tiếng Anh` đọc trầm ấm xen kẽ `{total_asmr:.1f} giây` nghỉ nghe âm thanh đời thực",
        f"> - **Số clip B-Roll ngoài (Internet/YouTube) đang bật:** **`{approved_broll_cnt} clip`** *(Mặc định ưu tiên dùng 100% hình quay thật của nhà chú Định)*",
        "",
    ]
    if extra_notes_md.strip():
        lines.append("---")
        lines.append("")
        lines.append(extra_notes_md.strip())
        lines.append("")

    section_heading = (
        f"## 📜 NỘI DUNG CHI TIẾT {len(beats)} PHÂN ĐOẠN (ĐÃ DUYỆT CHỐT)"
        if bp.get("human_approved")
        else f"## 📜 NỘI DUNG CHI TIẾT {len(beats)} PHÂN ĐOẠN (ĐỂ BẠN ĐỌC DUYỆT)"
    )
    lines.extend([
        "---",
        "",
        section_heading,
        "",
    ])

    ch_map = {c["chapter_id"]: c.get("title_vi", c["chapter_id"]) for c in bp.get("chapters", [])}
    cur_ch = None
    for b in beats:
        ch_id = b.get("chapter_id", "")
        if ch_id and ch_id != cur_ch:
            cur_ch = ch_id
            lines.append(f"### {ch_map.get(cur_ch, cur_ch)}")
            lines.append("")

        r_in = float(b.get("rough_in_sec", 0.0))
        r_out = float(b.get("rough_out_sec", 0.0))
        slot_dur = max(0.0, r_out - r_in)
        nar_en = str(b.get("narration_en", "")).strip()
        nar_vi = str(b.get("narration_vi", "")).strip()
        w_cnt = count_words(nar_en)

        lines.append(f"#### Phân đoạn `{b.get('beat_id', '')}` — Mốc video gốc: `{_fmt_mmss(r_in)} – {_fmt_mmss(r_out)}` *(Cảnh gốc dài {slot_dur:.1f}s)*")
        if b.get("visual_ground_truth_vi"):
            lines.append(f"- **👁️ Cảnh thật trên màn hình:** {b['visual_ground_truth_vi']}")
        lines.append(f"- **🎙️ Lời dẫn Tiếng Anh ({w_cnt} từ):**")
        lines.append(f'  > "{nar_en}"')
        if nar_vi:
            lines.append("- **🇻🇳 Dịch nghĩa Tiếng Việt:**")
            lines.append(f"  *{nar_vi}*")
        lines.append(
            f"- **🍃 Khoảng nghỉ nghe âm thanh thật (không đọc lời dẫn):** `{b.get('asmr_pause_after_sec', 0)} giây` — *{b.get('asmr_description', '')}*"
        )
        if b.get("usd_callout"):
            u = b["usd_callout"]
            lines.append(f"- **🏷️ Nhãn giá USD hiện trên màn hình:** `{u.get('label', '')}` *(hiện khi đọc tới chữ `{u.get('trigger_word', '')}`)*")
        if b.get("vn_us_bridge_topic"):
            lines.append(f"- **🌉 Điểm chạm tâm sự Việt – Mỹ:** {b['vn_us_bridge_topic']}")
        cov_note = (b.get("coverage_plan") or {}).get("broll_proposal_note_vi", "Dùng 100% hình gốc nhà chú Định.")
        lines.append(f"- **🎞️ Nguồn hình sử dụng:** {cov_note}")
        lines.append("")

    if bp.get("human_approved"):
        lines.extend([
            "---",
            "",
            "## ✅ BÀN GIAO CUỐI PHA A (ĐÃ CHỐT DUYỆT)",
            "- **Bản chữ sạch thu âm ElevenLabs:** `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt` *(đã lọc sạch 100% ghi chú kỹ thuật, sẵn sàng copy tạo `1_input/voice.mp3`)*.",
            f"- **Hình ảnh bổ sung (B-Roll ngoài):** `{approved_broll_cnt} clip` *(Giữ nguyên 100% hình quay thật nhà chú Định)*." if approved_broll_cnt == 0 else f"- **Hình ảnh bổ sung (B-Roll ngoài):** Đã tải `{approved_broll_cnt} clip` đã duyệt vào `2_pha_A_kich_ban/3_broll_bo_sung/`.",
            "",
        ])
    else:
        lines.extend([
            "---",
            "",
            "## ✅ CÂU HỎI ĐỂ BẠN CHỐT DUYỆT KỊCH BẢN PHA A",
            "1. **Về lời dẫn & câu chuyện:** Bạn đã ưng ý với các đoạn lời dẫn, các con số quy đổi USD và những câu tâm sự Việt – Mỹ ở trên chưa, hay muốn chỉnh sửa câu nào?",
            "2. **Về hình ảnh B-Roll ngoài:** Bạn đồng ý phương án **Dùng 100% hình thật nhà chú Định (`0 B-roll ngoài`)**, hay muốn bật thêm cảnh phụ nào không?",
            "",
        ])

    out_md = ep_paths["script_human_md"]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def validate_episode_phase_a(episode_id: str, mode: str = "check-draft", render_md: bool = False) -> Tuple[bool, Dict[str, Any]]:
    """
    `mode`:
      - `"check-draft"`    : Step A1 validation BEFORE human approval. Does NOT export `2_loi_dan_elevenlabs.txt`
                             and does NOT download external B-roll.
      - `"approve-export"` : Step A2 finalization AFTER human approves `1_kich_ban_hoan_chinh.md`. Marks
                             `human_approved = True`, exports `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt`,
                             and downloads ONLY `broll_approved == True` items (if any).
    """
    dna = bins.load_brand_dna()
    rules = dna.get("pacing_and_script_rules", {})
    ep_paths = bins.init_episode_dirs(episode_id)
    script_md_path = ep_paths["script_human_md"]
    blueprint_path = ep_paths["director_blueprint_json"]
    clean_txt_path = ep_paths["elevenlabs_txt"]
    memory_path = ep_paths["story_memory_json"]

    errors: List[str] = []
    warnings: List[str] = []

    if not blueprint_path.exists():
        errors.append(f"Missing Phase A -> Phase B contract: {blueprint_path}")
        return False, {"pass": False, "errors": errors, "warnings": warnings}

    bp_preview = json.loads(blueprint_path.read_text(encoding="utf-8"))
    if render_md:
        render_human_script_md(episode_id, extra_notes_md=str(bp_preview.get("visual_corrections_md", "")))

    if not script_md_path.exists():
        errors.append(f"Missing human-review script: {script_md_path}")
        return False, {"pass": False, "errors": errors, "warnings": warnings}

    bp = json.loads(blueprint_path.read_text(encoding="utf-8"))
    beats: List[Dict[str, Any]] = bp.get("beats", [])
    if not beats:
        errors.append("`director_blueprint.json` contains 0 beats!")
        return False, {"pass": False, "errors": errors, "warnings": warnings}

    target_dur = float(bp.get("target_duration_sec", 0.0) or 0.0)
    wps = float(rules.get("words_per_second", 2.1))
    banned_words = [w.lower() for w in rules.get("banned_ai_slop_words", [])]
    banned_pity = [w.lower() for w in rules.get("banned_pity_phrases", [])]

    spoken_paragraphs: List[str] = []
    total_words = 0
    total_asmr_sec = 0.0
    asmr_pause_count = 0
    usd_callouts: List[Dict[str, Any]] = []
    vn_us_bridges: List[str] = []
    used_unused_selects: List[str] = []
    broll_queries: List[str] = []

    for idx, beat in enumerate(beats, start=1):
        bid = beat.get("beat_id", f"b{idx:02d}")
        nar_en = str(beat.get("narration_en", "")).strip()

        # Forbid stage tags inside narration_en
        if "[" in nar_en or "]" in nar_en:
            errors.append(f"Beat {bid}: `narration_en` contains bracketed stage tag `[...]`. Strip all stage tags!")

        if nar_en:
            spoken_paragraphs.append(nar_en)
            w_cnt = count_words(nar_en)
            total_words += w_cnt
            low = nar_en.lower()
            for bw in banned_words:
                if re.search(rf"\b{re.escape(bw)}\b", low):
                    errors.append(f"Beat {bid}: Contains banned AI slop word/phrase '{bw}'")
            for bp_phrase in banned_pity:
                if bp_phrase in low:
                    errors.append(f"Beat {bid}: Contains banned pity/begging phrase '{bp_phrase}'")

        pause_sec = float(beat.get("asmr_pause_after_sec", 0.0) or 0.0)
        if pause_sec > 0:
            total_asmr_sec += pause_sec
            asmr_pause_count += 1

        # Check primary source file (supports both absolute paths and STUDIO_ROOT-relative paths)
        psrc = beat.get("primary_source") or {}
        src_file = psrc.get("file", "")
        src_p = Path(src_file) if Path(src_file).is_absolute() else (bins.STUDIO_ROOT / src_file)
        if not src_file or not src_p.exists():
            errors.append(f"Beat {bid}: `primary_source.file` missing on disk: {src_file}")

        # Check coverage plan if estimated beat duration exceeds rough slot + handles by > 2.0s
        r_in = float(beat.get("rough_in_sec", 0.0))
        r_out = float(beat.get("rough_out_sec", 0.0))
        rough_slot = max(0.0, r_out - r_in)
        tail_h = float(psrc.get("tail_handle_sec", 0.0) or 0.0)
        est_vo_sec = (count_words(nar_en) / wps) if nar_en else 0.0
        est_target_sec = est_vo_sec + pause_sec + 0.35
        cov = beat.get("coverage_plan") or {}
        if est_target_sec > (rough_slot + tail_h + 2.0):
            has_fallback = bool(
                cov.get("tier2_unused_select")
                or cov.get("tier3_vault_clip")
                or cov.get("tier4_broll_query")
            )
            if not has_fallback:
                warnings.append(
                    f"Beat {bid}: Est. audio {est_target_sec:.1f}s > rough+handle {rough_slot + tail_h:.1f}s without Tier 2/3/4 coverage query."
                )

        if cov.get("tier2_unused_select"):
            used_unused_selects.append(str(cov["tier2_unused_select"]))
        if cov.get("tier4_broll_query"):
            broll_queries.append(str(cov["tier4_broll_query"]))

        callout = beat.get("usd_callout")
        if callout and isinstance(callout, dict) and callout.get("label"):
            if "$" not in str(callout["label"]):
                errors.append(f"Beat {bid}: `usd_callout.label` must include `$` USD value: {callout['label']}")
            if not callout.get("trigger_word"):
                errors.append(f"Beat {bid}: `usd_callout` missing `trigger_word`")
            usd_callouts.append({"beat_id": bid, **callout})

        bridge = beat.get("vn_us_bridge_topic")
        if bridge:
            vn_us_bridges.append(str(bridge))

    est_spoken_sec = round(total_words / wps, 2)
    est_total_audio_sec = round(est_spoken_sec + total_asmr_sec + len(spoken_paragraphs) * 0.35, 2)
    spoken_ratio = round(est_spoken_sec / max(est_total_audio_sec, 1.0), 3)
    asmr_ratio = round(total_asmr_sec / max(est_total_audio_sec, 1.0), 3)

    # Check USD presence in script or callouts
    full_en_text = " ".join(spoken_paragraphs)
    if rules.get("require_usd_conversion", True):
        if ("$" not in full_en_text and "dollar" not in full_en_text.lower() and "cent" not in full_en_text.lower()) and len(usd_callouts) == 0:
            errors.append("US Cultural Rule Violation: Zero USD (`$` / dollars / cents) conversions found in script or callouts!")
        if len(usd_callouts) < 2:
            warnings.append(f"Only {len(usd_callouts)} USD visual callouts defined (recommended >= 2 per episode).")

    if asmr_pause_count < max(3, int(target_dur // 95)):
        errors.append(f"Insufficient ASMR breathing pauses: {asmr_pause_count} found for {target_dur:.1f}s video.")

    if len(vn_us_bridges) < 2:
        warnings.append(f"Found {len(vn_us_bridges)} VN-US cultural bridge reflections (recommended >= 2).")

    if target_dur > 0:
        drift_ratio = abs(est_total_audio_sec - target_dur) / target_dur
        if drift_ratio > 0.28:
            warnings.append(
                f"Estimated total timeline {est_total_audio_sec:.1f}s differs by >28% from rough duration {target_dur:.1f}s (Phase B elastic conform will stretch/trim)."
            )

    exported_txt: str | None = None
    approved_broll_items: List[Dict[str, Any]] = []

    if not errors:
        clean_narration = clean_for_elevenlabs(spoken_paragraphs)
        if "[" in clean_narration or "]" in clean_narration:
            errors.append("Exported narration still contains `[` or `]`!")
        elif mode == "approve-export":
            # Step A2 (ONLY AFTER Human Approval): mark approved, re-render badge in MD, export 2_loi_dan_elevenlabs.txt, and fetch approved B-roll (if any)
            bp["human_approved"] = True
            blueprint_path.write_text(json.dumps(bp, ensure_ascii=False, indent=2), encoding="utf-8")
            render_human_script_md(episode_id, extra_notes_md=str(bp.get("visual_corrections_md", "")))

            clean_txt_path.parent.mkdir(parents=True, exist_ok=True)
            clean_txt_path.write_text(clean_narration, encoding="utf-8")
            exported_txt = str(clean_txt_path)

            ledger = {
                "episode_id": episode_id,
                "human_approved": True,
                "vn_us_bridges_told": vn_us_bridges,
                "usd_callouts_shown": usd_callouts,
                "used_unused_raw_selects": used_unused_selects,
                "planned_broll_queries": broll_queries,
            }
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

            import hunt_broll
            approved_broll_items = hunt_broll.run_hunt_for_episode(episode_id)

    report = {
        "pass": len(errors) == 0,
        "mode": mode,
        "episode_id": episode_id,
        "beats_count": len(beats),
        "spoken_beats_count": len(spoken_paragraphs),
        "total_english_words": total_words,
        "target_wpm": 126,
        "estimated_spoken_sec": est_spoken_sec,
        "total_asmr_pause_sec": round(total_asmr_sec, 2),
        "asmr_pause_count": asmr_pause_count,
        "estimated_total_timeline_sec": est_total_audio_sec,
        "rough_video_duration_sec": target_dur,
        "spoken_ratio": spoken_ratio,
        "asmr_ratio": asmr_ratio,
        "usd_callouts_count": len(usd_callouts),
        "vn_us_bridges_count": len(vn_us_bridges),
        "elevenlabs_clean_txt": exported_txt,
        "approved_broll_downloaded": len(approved_broll_items),
        "errors": errors,
        "warnings": warnings,
    }
    return len(errors) == 0, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase A Script & Enforce Human Approval Gate")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument(
        "--mode",
        choices=("check-draft", "approve-export"),
        default="check-draft",
        help="check-draft (Step A1 before human approval) | approve-export (Step A2 after human approval)",
    )
    parser.add_argument("--render-md", action="store_true", help="Render 1_kich_ban_hoan_chinh.md from director_blueprint.json")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    ok, rep = validate_episode_phase_a(args.episode, mode=args.mode, render_md=args.render_md)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print(f"VALIDATE_SCRIPT_GATE (PHASE A - {args.mode.upper()}) | STATUS: {'PASS' if ok else 'BLOCKED'}")
        print("=" * 72)
        print(f"Beats: {rep.get('beats_count', 0)} | Words: {rep.get('total_english_words', 0)} (@126 WPM -> {rep.get('estimated_spoken_sec', 0)}s)")
        print(f"ASMR Pauses: {rep.get('asmr_pause_count', 0)} ({rep.get('total_asmr_pause_sec', 0)}s | ratio={rep.get('asmr_ratio', 0)*100:.1f}%)")
        print(f"Est. Total Timeline: {rep.get('estimated_total_timeline_sec', 0)}s vs Rough Video: {rep.get('rough_video_duration_sec', 0)}s")
        print(f"USD Callouts: {rep.get('usd_callouts_count', 0)} | VN-US Bridges: {rep.get('vn_us_bridges_count', 0)}")
        if args.mode == "check-draft" and ok:
            print("[HUMAN GATE] Draft validated! STOP now and present `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` for user approval.")
        elif args.mode == "approve-export" and ok:
            print(f"ElevenLabs Clean File : {rep['elevenlabs_clean_txt']}")
            print(f"Approved B-Roll Ready : {rep['approved_broll_downloaded']} clip(s)")
        for w in rep.get("warnings", []):
            print(f"[WARN] {w}")
        for e in rep.get("errors", []):
            print(f"[ERR ] {e}")
        print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
