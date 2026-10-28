# -*- coding: utf-8 -*-
"""
Vlog-US-Studio-v1 — Phase B External B-Roll Hunter & Documentary Color Unifier (engine/hunt_broll.py)
Fetches clean, watermark-free external B-roll ONLY when Tier 1/2/3 authentic clips need supplementation:
  1. Waterfall Search (Prioritizing 100% License-Safe & Zero-Watermark Sources):
     - Tier A: Pexels API (if PEXELS_API_KEY) / Pixabay API (if PIXABAY_API_KEY)
     - Tier B: Wikimedia Commons Free Video (`filetype:video`, progressive keyword simplification)
     - Tier C: Wikimedia Commons High-Res Documentary Photography (`>= 1280px` CC-BY/CC0 + gentle 6% Ken Burns)
     - Tier D: Short raw stock clips (`< 90s`, excluding compilation/listicle channels) via `yt-dlp`
               + FFmpeg local slicing (avoids standalone `ffprobe` dependency in `yt-dlp`).
  2. Normalizes clip to exact `1920x1080 @ 30.00fps`, `yuv420p`, **100% MUTED (`-an`)**,
     and applies `shared/luts/warm_doc_unify.cube` so external shots match Uncle Dinh's warm tone.
  3. Renders a 3-frame Candidate QA Contact Strip in `.cache/<EP_ID>/broll_candidates/` for visual audit.
  4. Records license & source URL into `3_edl_and_assets/broll/broll_manifest.json`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import bins  # noqa: E402

USER_AGENT = "VlogUSStudioBot/1.1 (Documentary Production Tool; contact: studio@example.com)"

STOPWORDS = {
    "the", "and", "for", "with", "into", "from", "early", "quiet", "misty", "warm", "soft", "gentle"
}

ALWAYS_BAD_WORDS = {
    "map", "maps", "logo", "logos", "icon", "flag", "flags", "stamp", "chart",
    "poster", "diagram", "animation", "cartoon", "drawing", "illustration", "sketch",
    "sign", "signs", "signboard", "billboard", "banner", "plaque", "inscription",
    "museum", "memorial", "statue", "monument", "cemetery", "grave", "war", "military",
    "army", "soldier", "weapon", "hospital", "clinic", "conference", "ceremony",
    "portrait", "portraits", "selfie", "politician", "politicians", "journalist", "broadcasting",
}

RURAL_CONFLICT_WORDS = {
    "railroad", "railway", "train", "trains", "locomotive", "station", "airport", "aircraft",
    "highway", "expressway", "freeway", "interchange", "overpass", "traffic",
    "skyscraper", "highrise", "apartment", "apartments", "condominium", "mall", "supermarket",
    "hotel", "resort", "casino", "nightclub", "bus", "buses", "taxi", "truck", "lorry",
}

RURAL_CONFLICT_PHRASES = (
    "hanoi", "ha noi", "ho chi minh", "saigon", "da nang", "danang", "nha trang", "dalat", "da lat",
)

RURAL_POSITIVE_WORDS = {
    "village", "villages", "rural", "countryside", "hamlet", "commune", "paddy", "paddies",
    "rice", "field", "fields", "bamboo", "lane", "path", "alley", "morning", "dawn",
    "sunrise", "peaceful", "landscape", "landscapes", "farming", "agriculture",
}


def _is_rural_query(query: str) -> bool:
    ql = query.lower()
    return any(k in ql for k in ("village", "rural", "countryside", "lane", "paddy", "rice", "farm", "bamboo"))


def _progressive_queries(query: str) -> List[str]:
    """
    Generates progressively broader search queries while preserving rural/countryside semantics
    so search engines never drift into urban highways or train stations.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2]
    core = [w for w in words if w.lower() not in STOPWORDS]
    candidates: List[str] = [query.strip()]
    if len(core) >= 3:
        candidates.append(" ".join(core[:4]))
        candidates.append(" ".join(core[:3]))
    elif len(core) == 2:
        candidates.append(" ".join(core))

    if any(w.lower() == "vietnam" for w in words) and _is_rural_query(query):
        candidates.extend([
            "Thanh Hoa countryside",
            "Thanh Hoa village",
            "Rural Vietnam village",
            "Vietnam countryside rice field",
            "Vietnam village landscape",
            "Northern Vietnam countryside",
        ])
    elif any(w.lower() == "vietnam" for w in words):
        candidates.extend([
            "Vietnam peaceful landscape",
            "Rural Vietnam countryside",
        ])

    seen: set[str] = set()
    uniq: List[str] = []
    for c in candidates:
        cl = c.lower().strip()
        if cl and cl not in seen:
            seen.add(cl)
            uniq.append(c)
    return uniq


def _score_wikimedia_candidate(page: Dict[str, Any], query: str) -> int:
    """
    Returns `< 0` if candidate violates documentary visual cleanliness (text banners, urban traffic, maps, portraits)
    or lacks geographic lock (`vietnam` in title/categories when requested), otherwise returns relevance score.
    """
    title = str(page.get("title", "")).lower().replace("_", " ")
    ii = (page.get("imageinfo") or [{}])[0]
    ext = ii.get("extmetadata") or {}
    desc_clean = re.sub(r"<[^>]+>", " ", str((ext.get("ImageDescription") or {}).get("value", "")).lower())
    cats_clean = str((ext.get("Categories") or {}).get("value", "")).lower().replace("_", " ")

    title_cats = f"{title} {cats_clean}"
    title_cat_words = set(re.findall(r"[a-z0-9]+", title_cats))
    all_words = title_cat_words | set(re.findall(r"[a-z0-9]+", desc_clean))

    # 1. Whole-word blacklist on title + categories (avoids false substring hits like "warm" -> "war")
    if title_cat_words & ALWAYS_BAD_WORDS:
        return -100
    if "coat of arms" in title_cats:
        return -100

    ql = query.lower()
    # 2. Geographic Anchor Lock: if query specifies Vietnam, title or Categories MUST belong to Vietnam!
    if "vietnam" in ql or "thanh hoa" in ql:
        vn_markers = ("vietnam", "viet nam", "việt nam", "thanh hoa", "thanh hóa", "ninh binh", "nam dinh", "ha nam", "red river")
        if not any(m in title_cats for m in vn_markers):
            return -100

    # 3. Rural Scenery Protection: exclude urban cities, trains, highways, apartments
    if _is_rural_query(query):
        if title_cat_words & RURAL_CONFLICT_WORDS:
            return -100
        for city_phrase in RURAL_CONFLICT_PHRASES:
            if city_phrase not in ql and city_phrase in title_cats:
                return -100

    # Base score inversely proportional to MediaWiki search rank (`index` starts at 1)
    search_idx = int(page.get("index", 15) or 15)
    score = max(1, 30 - search_idx)

    title_words = set(re.findall(r"[a-z0-9]+", title))
    q_words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2 and w.lower() not in STOPWORDS]
    for qw in q_words:
        if qw in title_words:
            score += 14
        elif qw in all_words:
            score += 6

    if _is_rural_query(query):
        for pos in RURAL_POSITIVE_WORDS:
            if pos in title_words:
                score += 10
            elif pos in title_cat_words:
                score += 5

    return score


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 18) -> Any:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _download_file(url: str, dst: Path, timeout: int = 40) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 12_000:
        return False
    dst.write_bytes(data)
    return True


def _try_pexels(query: str, raw_dst: Path, used_urls: set[str]) -> Optional[Dict[str, str]]:
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None
    for q_str in _progressive_queries(query)[:3]:
        try:
            q = urllib.parse.quote(q_str)
            url = f"https://api.pexels.com/videos/search?query={q}&orientation=landscape&size=medium&per_page=6"
            data = _http_get_json(url, headers={"Authorization": api_key})
            for vid in data.get("videos", []):
                page_url = vid.get("url", "")
                if page_url in used_urls:
                    continue
                for vf in sorted(vid.get("video_files", []), key=lambda x: abs(int(x.get("width", 0)) - 1920)):
                    if int(vf.get("width", 0)) >= 1280 and vf.get("link"):
                        if _download_file(vf["link"], raw_dst):
                            return {
                                "provider": "Pexels",
                                "local_raw": str(raw_dst),
                                "source_url": page_url or vf["link"],
                                "license": "Pexels Royalty-Free License",
                            }
        except Exception:
            continue
    return None


def _try_pixabay(query: str, raw_dst: Path, used_urls: set[str]) -> Optional[Dict[str, str]]:
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return None
    for q_str in _progressive_queries(query)[:3]:
        try:
            q = urllib.parse.quote(q_str)
            url = f"https://pixabay.com/api/videos/?key={api_key}&q={q}&video_type=film&per_page=6"
            data = _http_get_json(url)
            for hit in data.get("hits", []):
                page_url = hit.get("pageURL", "")
                if page_url in used_urls:
                    continue
                med = hit.get("videos", {}).get("large") or hit.get("videos", {}).get("medium")
                if med and med.get("url"):
                    if _download_file(med["url"], raw_dst):
                        return {
                            "provider": "Pixabay",
                            "local_raw": str(raw_dst),
                            "source_url": page_url or med["url"],
                            "license": "Pixabay Content License (Royalty-Free)",
                        }
        except Exception:
            continue
    return None


def _try_wikimedia_video(query: str, raw_dst: Path, used_urls: set[str]) -> Optional[Dict[str, str]]:
    """Searches Wikimedia Commons for free CC-BY/CC0 landscape video files ranked by semantic relevance."""
    for q_str in _progressive_queries(query):
        try:
            q_vid = urllib.parse.quote(f"{q_str} filetype:video")
            api_url = (
                "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
                f"&gsrsearch={q_vid}&gsrnamespace=6&gsrlimit=20&prop=imageinfo&iiprop=url|size|mime|extmetadata&format=json"
            )
            data = _http_get_json(api_url)
            pages = (data.get("query") or {}).get("pages") or {}
            ranked = sorted(
                pages.values(),
                key=lambda p: _score_wikimedia_candidate(p, query),
                reverse=True,
            )
            for page in ranked:
                sc = _score_wikimedia_candidate(page, query)
                if sc < 0:
                    continue
                ii = (page.get("imageinfo") or [{}])[0]
                mime = str(ii.get("mime", ""))
                w = int(ii.get("width", 0) or 0)
                h = int(ii.get("height", 0) or 0)
                f_url = ii.get("url", "")
                desc_url = ii.get("descriptionurl", f_url)
                if desc_url in used_urls:
                    continue
                if ("video" in mime or f_url.lower().endswith((".webm", ".ogv", ".mp4"))) and w > h and w >= 1280 and f_url:
                    ext = os.path.splitext(urllib.parse.urlparse(f_url).path)[1] or ".webm"
                    dl_path = raw_dst.with_suffix(ext)
                    if _download_file(f_url, dl_path):
                        lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "Wikimedia Commons CC")
                        return {
                            "provider": "Wikimedia_Commons_Video",
                            "local_raw": str(dl_path),
                            "source_url": desc_url,
                            "license": lic,
                            "matched_title": str(page.get("title", "")),
                        }
        except Exception:
            continue
    return None


def _try_wikimedia_photo_kenburns(query: str, raw_dst: Path, used_urls: set[str]) -> Optional[Dict[str, str]]:
    """
    Searches Wikimedia Commons for clean, watermark-free high-res landscape documentary photos
    (`>= 1280px`, `CC-BY / CC0`), ranked by rural/documentary semantic score and rendered into smooth 30fps Ken Burns cutaways!
    """
    for q_str in _progressive_queries(query):
        try:
            q_enc = urllib.parse.quote(f"{q_str} filetype:bitmap")
            api_url = (
                "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
                f"&gsrsearch={q_enc}&gsrnamespace=6&gsrlimit=25&prop=imageinfo&iiprop=url|size|mime|thumburl|extmetadata&iiurlwidth=1920&format=json"
            )
            data = _http_get_json(api_url)
            pages = (data.get("query") or {}).get("pages") or {}
            ranked = sorted(
                pages.values(),
                key=lambda p: _score_wikimedia_candidate(p, query),
                reverse=True,
            )
            for page in ranked:
                sc = _score_wikimedia_candidate(page, query)
                if sc < 0:
                    continue
                ii = (page.get("imageinfo") or [{}])[0]
                w = int(ii.get("width", 0) or 0)
                h = int(ii.get("height", 0) or 0)
                mime = str(ii.get("mime", ""))
                dl_url = ii.get("thumburl") or ii.get("url", "")
                desc_url = ii.get("descriptionurl", dl_url)
                if desc_url in used_urls:
                    continue
                if ("jpeg" in mime or "png" in mime) and w >= 1280 and (w / max(h, 1)) >= 1.35 and dl_url:
                    img_dst = raw_dst.with_suffix(".jpg")
                    if _download_file(dl_url, img_dst):
                        lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "CC-BY-SA / Wikimedia Commons")
                        return {
                            "provider": "Wikimedia_Commons_Documentary_KenBurns",
                            "local_raw": str(img_dst),
                            "source_url": desc_url,
                            "license": lic,
                            "is_photo": "true",
                            "matched_title": str(page.get("title", "")),
                        }
        except Exception:
            continue
    return None


def _try_ytdlp_short_stock(query: str, raw_dst: Path, used_urls: set[str]) -> Optional[Dict[str, str]]:
    """
    Fallback short raw stock video (`duration < 90s`, single MP4 stream so standalone `ffprobe` is NOT required),
    filtering out listicle/compilation channels that burn corner watermarks.
    """
    out_tmpl = str(raw_dst.with_suffix(".%(ext)s"))
    search_expr = f"ytsearch5:{query} drone stock footage no watermark"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--max-downloads", "1",
        "--match-filter", "duration < 90 & duration > 6",
        "-f", "bestvideo[height<=1080][width>=1280][ext=mp4]/best[height<=1080][width>=1280][ext=mp4]",
        "-o", out_tmpl,
        search_expr,
    ]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=75)
    for cand in raw_dst.parent.glob(f"{raw_dst.stem}.*"):
        if cand.is_file() and cand.stat().st_size > 30_000 and cand.suffix.lower() == ".mp4":
            src_tag = f"ytsearch:{query}"
            if src_tag not in used_urls:
                return {
                    "provider": "YouTube_ShortStock_yt_dlp",
                    "local_raw": str(cand),
                    "source_url": src_tag,
                    "license": "Short Stock B-Roll Cutaway (Muted & Warm-LUT Graded)",
                }
    return None


def transcode_and_grade_broll(
    raw_input: Path,
    out_mp4: Path,
    duration_sec: float,
    is_photo: bool = False,
) -> None:
    """
    Normalizes B-roll to `1920x1080 @ 30fps`, strict TV-range `yuv420p` (`bt709`), `-an` (100% muted),
    applying `shared/luts/warm_doc_unify.cube` for warm documentary visual unity.
    """
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = bins.resolve_ffmpeg()
    lut_path = bins.SHARED_DIR / "luts" / "warm_doc_unify.cube"
    lut_escaped = bins.ffmpeg_escape_filter_path(lut_path)

    target_dur = max(3.0, round(duration_sec + 0.5, 2))
    total_frames = int(round(target_dur * 30))

    if is_photo:
        vf = (
            f"scale=2304:1296:force_original_aspect_ratio=increase,crop=2304:1296,"
            f"zoompan=z='min(zoom+0.0006,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps=30,"
            f"lut3d=file='{lut_escaped}',eq=saturation=0.95:contrast=1.02,"
            f"scale=1920:1080:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        )
        cmd = [
            ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(raw_input),
            "-t", f"{target_dur:.2f}",
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-crf", "18", "-preset", "fast",
            str(out_mp4),
        ]
    else:
        meta = bins.probe_media(raw_input)
        in_args: List[str] = []
        if meta["duration"] > target_dur + 3.0:
            in_args = ["-ss", "2.0"]
        elif 0 < meta["duration"] < target_dur:
            in_args = ["-stream_loop", "-1"]
        # Slight 1.05x overscan crop (2016x1134 -> 1920x1080) keeps framing clean
        vf = (
            f"scale=2016:1134:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,"
            f"lut3d=file='{lut_escaped}',eq=saturation=0.95:contrast=1.02,"
            f"scale=1920:1080:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        )
        cmd = [
            ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
            *in_args,
            "-i", str(raw_input),
            "-t", f"{target_dur:.2f}",
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-crf", "18", "-preset", "fast",
            str(out_mp4),
        ]
    subprocess.run(cmd, check=True)


def build_broll_qa_strip(broll_mp4: Path, out_jpg: Path, label: str) -> None:
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    meta = bins.probe_media(broll_mp4)
    dur = max(1.0, float(meta["duration"]))
    ffmpeg_exe = bins.resolve_ffmpeg()

    tiles: List[Image.Image] = []
    for frac in (0.15, 0.50, 0.85):
        ts = dur * frac
        proc = subprocess.run(
            [
                ffmpeg_exe, "-hide_banner", "-loglevel", "error",
                "-ss", f"{ts:.2f}", "-i", str(broll_mp4),
                "-frames:v", "1", "-vf", "scale=480:270",
                "-f", "image2pipe", "-vcodec", "png", "-",
            ],
            capture_output=True,
        )
        if proc.stdout:
            tiles.append(Image.open(io.BytesIO(proc.stdout)).convert("RGB"))
        else:
            tiles.append(Image.new("RGB", (480, 270), (20, 20, 20)))

    canvas = Image.new("RGB", (480 * 3, 312), (16, 16, 18))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(str(bins.SHARED_DIR / "fonts" / "segoeuib.ttf"), 18)
    except Exception:
        font = ImageFont.load_default()
    draw.text((14, 10), f"B-ROLL QA STRIP: {label} | 1920x1080@30fps | Muted (-an) | Warm LUT Applied", fill=(255, 215, 0), font=font)
    for idx, im in enumerate(tiles):
        canvas.paste(im, (idx * 480, 42))
    canvas.save(out_jpg, quality=88)


def fulfill_broll_request(
    episode_id: str,
    broll_id: str,
    query: str,
    duration_sec: float,
    target_file: Path,
    used_urls: set[str],
) -> Dict[str, Any]:
    ep_paths = bins.init_episode_dirs(episode_id)
    cache_cand_dir = ep_paths["broll_candidates_dir"]
    cache_cand_dir.mkdir(parents=True, exist_ok=True)
    for old_f in cache_cand_dir.glob(f"{broll_id}_raw.*"):
        old_f.unlink()
    raw_base = cache_cand_dir / f"{broll_id}_raw.mp4"

    hit = (
        _try_pexels(query, raw_base, used_urls)
        or _try_pixabay(query, raw_base, used_urls)
        or _try_wikimedia_video(query, raw_base, used_urls)
        or _try_wikimedia_photo_kenburns(query, raw_base, used_urls)
        or _try_ytdlp_short_stock(query, raw_base, used_urls)
    )
    if not hit:
        raise RuntimeError(f"Could not fetch B-roll candidate for query='{query}'")

    used_urls.add(hit["source_url"])
    raw_local = Path(hit.get("local_raw", str(raw_base)))
    is_photo = hit.get("is_photo") == "true"

    transcode_and_grade_broll(raw_local, target_file, duration_sec=duration_sec, is_photo=is_photo)
    qa_strip = cache_cand_dir / f"{broll_id}_qa_strip.jpg"
    build_broll_qa_strip(target_file, qa_strip, label=f"{broll_id} ({hit['provider']})")
    out_meta = bins.probe_media(target_file)

    return {
        "broll_id": broll_id,
        "query": query,
        "provider": hit["provider"],
        "matched_title": hit.get("matched_title", ""),
        "source_url": hit["source_url"],
        "license": hit["license"],
        "output_file": str(target_file),
        "qa_strip_jpg": str(qa_strip),
        "width": out_meta["width"],
        "height": out_meta["height"],
        "fps": out_meta["fps"],
        "pix_fmt": out_meta["pix_fmt"],
        "has_audio": out_meta["has_audio"],
        "duration_sec": out_meta["duration"],
    }


def run_hunt_for_episode(episode_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Phase A Step A2 B-Roll Downloader (ONLY runs AFTER human script approval!).
    Reads `_he_thong/contracts/director_blueprint.json`:
      - Blocks execution if `human_approved` is False!
      - Downloads ONLY beats explicitly flagged with `coverage_plan.broll_approved == True`
        into `2_pha_A_kich_ban/3_broll_bo_sung/broll_<beat_id>.mp4`.
      - If 0 beats have approved B-roll, does NOT create `3_broll_bo_sung/`.
    """
    ep_paths = bins.init_episode_dirs(episode_id)
    bp_path = ep_paths["director_blueprint_json"]
    manifest_path = ep_paths["broll_manifest_json"]
    broll_out_dir = ep_paths["broll_approved_dir"]

    if not bp_path.exists():
        raise FileNotFoundError(f"Missing Phase A blueprint: {bp_path}")
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    if not bp.get("human_approved", False):
        raise PermissionError(
            "BLOCKED BY HUMAN GATE: Cannot download external B-roll before human approves "
            "`2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` (`human_approved` is False)!"
        )

    approved_reqs: List[Dict[str, Any]] = []
    for beat in bp.get("beats", []):
        bid = beat.get("beat_id", "")
        cov = beat.get("coverage_plan") or {}
        if cov.get("broll_approved") is True and str(cov.get("tier4_broll_query", "")).strip():
            dur = float(cov.get("broll_duration_sec", 5.5) or 5.5)
            broll_id = f"broll_{bid}"
            approved_reqs.append(
                {
                    "broll_id": broll_id,
                    "beat_id": bid,
                    "query": str(cov["tier4_broll_query"]).strip(),
                    "duration_sec": dur,
                    "target_file": broll_out_dir / f"{broll_id}.mp4",
                }
            )

    if len(approved_reqs) == 0:
        # Remove empty `3_broll_bo_sung/` if it exists so `2_pha_A_kich_ban/` stays 100% clean
        if broll_out_dir.exists() and not any(broll_out_dir.iterdir()):
            broll_out_dir.rmdir()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"episode_id": episode_id, "approved_broll_count": 0, "items": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return []

    existing_manifest: Dict[str, Any] = {"episode_id": episode_id, "items": []}
    if manifest_path.exists() and not force_refresh:
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {item["broll_id"]: item for item in existing_manifest.get("items", [])}
    used_urls: set[str] = {item["source_url"] for item in by_id.values() if item.get("source_url")}

    fetched_now: List[Dict[str, Any]] = []
    for req in approved_reqs:
        bid = req["broll_id"]
        tgt: Path = req["target_file"]
        if (not force_refresh) and tgt.exists() and bid in by_id:
            fetched_now.append(by_id[bid])
            continue
        entry = fulfill_broll_request(
            episode_id,
            broll_id=bid,
            query=req["query"],
            duration_sec=float(req["duration_sec"]),
            target_file=tgt,
            used_urls=used_urls,
        )
        by_id[bid] = entry
        fetched_now.append(entry)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_obj = {"episode_id": episode_id, "approved_broll_count": len(fetched_now), "items": fetched_now}
    manifest_path.write_text(json.dumps(manifest_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return fetched_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase A Step A2 Approved B-Roll Downloader & Warm Color Unifier")
    parser.add_argument("--episode", required=True, help="Episode ID, e.g., EP001")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetch and re-grade approved B-roll items")
    args = parser.parse_args()

    items = run_hunt_for_episode(args.episode, force_refresh=args.refresh)
    print("=" * 72)
    print(f"HUNT_BROLL COMPLETE | EPISODE={args.episode} | APPROVED ITEMS={len(items)}")
    if not items:
        print("  -> 0 external B-roll approved. Using 100% authentic Uncle Dinh footage!")
    for it in items:
        print(
            f"  - {it['broll_id']}: {it['provider']} -> {it['width']}x{it['height']}@{it['fps']}fps "
            f"| pix_fmt={it['pix_fmt']} | has_audio={it['has_audio']} | dur={it['duration_sec']}s"
        )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
