---
name: vlog-us-studio
description: Hệ thống sản xuất video tài liệu đời thực 2 Pha (Đạo diễn Kịch bản Pha A -> Editor Hậu kỳ Pha B) cho kênh YouTube Mỹ "Uncle Dinh - Simple Life at 50". Kích hoạt khi người dùng yêu cầu lên kịch bản, rà soát video sơ bộ, xuất lời dẫn ElevenLabs, hoặc dựng hậu kỳ video hoàn chỉnh kèm dự án CapCut cho một tập phim (`EP001`, `EP002`...).
---

# Vlog-US-Studio (Quy Trình Sản Xuất 2 Pha Tinh Gọn)

Hệ thống tách bạch tuyệt đối công việc thành **2 Pha** tương ứng với **3 ngăn thư mục sạch cho con người** và **1 ngăn ẩn `_he_thong/` cho máy** bên trong `episodes/<EP_ID>/`:

```text
episodes/<EP_ID>/
├── 1_input/                # Người dùng bỏ vào: kich_ban_so_bo.md, video_so_bo.mp4, (tùy chọn raw_clips/), và voice.mp3 (ở đầu Pha B)
├── 2_pha_A_kich_ban/       # Đầu ra Pha A: 1_kich_ban_hoan_chinh.md -> [DUYỆT] -> 2_loi_dan_elevenlabs.txt (+ 3_broll_bo_sung/ NẾU duyệt)
├── 3_pha_B_video/          # Đầu ra Pha B: Video_Hoan_Chinh_<EP>.mp4, Phu_De_YouTube_<EP>.srt, CapCut_Draft/, Anh_Kiem_Duyet_QA.png, Bao_Cao_Kiem_Duyet.md
└── _he_thong/              # Ngăn máy ẩn: toàn bộ file .json, ảnh quét nháp, câu cắt lẻ vo_slices/, cụm 3 phút clusters_temp/
```

> **QUY TẮC BẤT DI BẤT DỊCH (3 CẤM):**
> 1. **CẤM bày file kỹ thuật (`.json`, `.ass`, `.wav` lẻ, `cum_*.mp4`)** ra ngoài 3 ngăn của người dùng. Luôn lấy đường dẫn qua `engine/bins.py` (`get_episode_paths`).
> 2. **CẤM xuất `2_loi_dan_elevenlabs.txt` hoặc tự tải B-roll ngoài** trước khi người dùng đọc duyệt trực tiếp `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md`.
> 3. **CẤM bịa chi tiết cảnh quay lệch với hình thật:** Bắt buộc xem trực tiếp toàn bộ ảnh chụp `contact_sheets/sheet_*.jpg` từ `video_so_bo.mp4` để hiệu đính kịch bản sơ bộ khớp 100% với vật dụng, bối cảnh và hành động thật trên màn hình.

---

## 🎬 PHA A — ĐẠO DIỄN & KỊCH BẢN (Chia làm 2 bước qua Cổng Duyệt)

Đọc chi tiết tiêu chuẩn viết lời dẫn Mỹ tại `references/phase_a_director_guide.md` và hồ sơ nhân vật tại `references/us_audience_persona.md`.

### Bước A1: Quét hình ảnh thật & Lên kịch bản đề xuất (`check-draft`)
1. Kiểm tra môi trường & bố cục thư mục:
   `python engine/preflight.py --episode <EP_ID>`
2. Đối chiếu nguồn gốc clip (nếu có `raw_clips/` hoặc `rough_draft_content.json`):
   `python engine/match_rough_to_raw.py --episode <EP_ID>`
3. Quét toàn bộ cảnh quay & âm thanh hiện trường từ `1_input/video_so_bo.mp4`:
   `python engine/scan_rough_video.py --episode <EP_ID>`
4. **Đọc trực tiếp bằng mắt** tất cả ảnh `_he_thong/scan/contact_sheets/sheet_*.jpg` và `_he_thong/scan/rough_video_telemetry.json`. Soạn hợp đồng `_he_thong/contracts/director_blueprint.json` theo mẫu `templates/director_blueprint_template.json` (`"human_approved": false`, mặc định `"broll_approved": false` ưu tiên giữ 100% cảnh thật).
5. Kiểm tra & xuất bản đọc duyệt cho người dùng:
   `python engine/validate_script_gate.py --episode <EP_ID> --mode check-draft --render-md`
6. **🛑 DỪNG BẮT BUỘC (CỔNG CON NGƯỜI DUYỆT KỊCH BẢN):**
   Trình bày ngắn gọn bằng tiếng Việt dễ hiểu, gửi link `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` để người dùng:
   - Chốt lời dẫn tiếng Anh / nghĩa tiếng Việt & các nhãn giá USD.
   - Chốt giữ 100% cảnh thật (`0 B-roll ngoài`) hay bật tải cảnh phụ B-roll ở phân đoạn nào.

### Bước A2: Xuất bản chữ sạch ElevenLabs sau khi người dùng duyệt (`approve-export`)
1. Cập nhật mọi yêu cầu chỉnh sửa câu chữ hoặc bật `"broll_approved": true` (nếu có) vào `_he_thong/contracts/director_blueprint.json`.
2. Chạy lệnh chốt Pha A:
   `python engine/validate_script_gate.py --episode <EP_ID> --mode approve-export`
   *(Lệnh tự động đóng dấu `✅ ĐÃ DUYỆT`, xuất `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt` sạch 100% ký hiệu kỹ thuật, và chỉ tải `3_broll_bo_sung/` nếu người dùng duyệt B-roll).*
3. **🛑 DỪNG BÀN GIAO PHA A:** Mời người dùng lấy `2_loi_dan_elevenlabs.txt` tạo file thu âm bỏ vào `1_input/voice.mp3` trước khi sang Pha B.

---

## 🎞️ PHA B — HẬU KỲ, DỰNG VIDEO, DỰ ÁN CAPCUT & KIỂM DUYỆT QA

Chỉ chạy khi Pha A đã duyệt (`human_approved: true`) và đã có `1_input/voice.mp3`. Xem thông số kỹ thuật chi tiết tại `references/phase_b_editor_and_qa_guide.md`.

1. **Căn khớp giọng đọc vào thung lũng im lặng & Chia cụm 3 phút (an toàn cho cả tập 10–30 phút):**
   `python engine/align_voice_and_clusters.py --episode <EP_ID>`
2. **Trộn âm thanh 4 lớp chuẩn tài liệu Mỹ (`-14 LUFS` VO, ASMR `-18 dB <-> -6 dB`, trần `-1.2 dBFS`):**
   `python engine/mix_4layer_audio.py --episode <EP_ID>`
3. **Kết xuất các cụm `1080p30 yuv420p bt709`, gắn phụ đề `52px` + thẻ USD `#FFD700` & Xuất `CapCut_Draft/`:**
   `python engine/render_cluster_and_capcut.py --episode <EP_ID> --preset fast`
4. **Ghép siêu tốc Video hoàn chỉnh & Phụ đề YouTube `.srt`:**
   `python engine/concat_master.py --episode <EP_ID>`
5. **Chạy kiểm duyệt tự động 9 chỉ số & Xuất bảng ảnh QA:**
   `python engine/build_qa_sheet.py --episode <EP_ID>`
6. **Kiểm tra bằng mắt bắt buộc:** Mở xem trực tiếp `3_pha_B_video/Anh_Kiem_Duyet_QA.png` và `3_pha_B_video/Bao_Cao_Kiem_Duyet.md`. Chỉ bàn giao khi đạt `PASS 100%` (không lỗi đen hình, không mất tiếng, phụ đề và thẻ USD rõ nét, không tràn lề).
