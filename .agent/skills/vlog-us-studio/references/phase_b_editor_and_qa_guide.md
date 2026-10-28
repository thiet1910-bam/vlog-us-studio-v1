# Hướng Dẫn Hậu Kỳ Pha B & Kiểm Duyệt QA (`phase_b_editor_and_qa_guide.md`)

## 1. Kiến Trúc Chia Cụm 3 Phút (`Cluster Architecture`) Cho Tập Dài 10–30 Phút
- `engine/align_voice_and_clusters.py` tự động chia tập phim dài `10–30 phút` thành các cụm chương tự nhiên dài `~2.5 – 4.5 phút` (`c01`, `c02`, `c03`... lưu trong `_he_thong/clusters_temp/`).
- Lợi ích:
  1. Không bao giờ tràn RAM hay đứt gãy tiến trình FFmpeg khi dựng tập phim dài 25–30 phút.
  2. Nếu cần sửa lại 1 phân đoạn, chỉ cần kết xuất lại đúng cụm chứa phân đoạn đó (`--cluster c02`) rồi chạy `concat_master.py` ghép lại trong 2 giây (`-c copy`).
- **Bảo toàn góc máy cao trào (`preserve_rough_cut: true`):** Khi cảnh gốc `video_so_bo.mp4` dài hơn câu đọc của `voice.mp3` (trong ngưỡng nghỉ ASMR tự nhiên `<= 60s`), hệ thống giữ trọn vẹn 100% thời lượng cảnh gốc để không cắt cụt các góc máy kết cảnh (như tay vặn kìm, lắc thử cổng chắn, nhìn ra mái ngói đỏ).

## 2. Tiêu Chuẩn Trộn Âm Thanh 4 Lớp (`engine/mix_4layer_audio.py`)
1. **Lớp 1 — Giọng kể tiếng Anh (`A1_Voiceover_US`):** Chuẩn hóa độ lớn phần lời nói về `-14.0 LUFS`, vuốt mượt `15ms` hai đầu mỗi câu cắt tại thung lũng im lặng.
2. **Lớp 2 — Âm thanh hiện trường & ASMR (`A2_Location_ASMR`):** Lọc cắt trầm `80Hz` khử ù gió, vá tự động mọi đoạn lọt tiếng nói tiếng Việt (`VN_SPEECH_LEAK`) bằng tiếng phòng sạch (`clean_room_tone.wav`), và điều tiết âm lượng mượt `0.4s`: hạ xuống `-18.0 dB` khi có giọng kể, nâng lên `-6.0 dB` trong khoảng nghỉ để khán giả nghe rõ tiếng chổi quét, tiếng đồ chơi, tiếng kìm sắt.
3. **Lớp 3 — Tiếng gõ nhãn giá USD (`sfx_subtle_paper_tick.wav`):** Phát ở mức tinh tế `-24.0 dBFS` đúng khoảnh khắc thẻ giá USD hiện lên.
4. **Lớp 4 — Nhạc guitar mộc đoạn kết (`bgm_warm_acoustic_outro.wav`):** Chỉ bật ở phân đoạn kết (`"outro_bgm": true`) ở mức nền ấm `-26.0 dB` kèm fade-in/fade-out.
5. **Giới hạn đỉnh tổng (`Master Ceiling`):** Khóa mềm tại `-1.2 dBFS` (đảm bảo luôn `<= -1.0 dBFS`, không bao giờ vỡ tiếng trên loa Tivi).

## 3. Tiêu Chuẩn Hình Ảnh, Phụ Đề & Dự Án CapCut (`engine/render_cluster_and_capcut.py`)
- **Chuyển hệ màu an toàn:** Chuyển từ `10-bit HEVC (yuv420p10le)` của điện thoại sang `8-bit H.264 High@4.1 (yuv420p, bt709, tv range)` để màu da ấm tự nhiên, không bị trắng bệch trên mọi trình phát.
- **Phụ đề chính (`SubEN`):** Font `Segoe UI Bold`, cỡ `52px`, màu trắng `#FFFFFF`, viền đen `3.4px`, tối đa 2 dòng cân đối ở đáy màn hình — thiết kế riêng cho khán giả Mỹ 50–75+ xem trên Tivi phòng khách.
- **Thẻ giá USD (`USDTag`):** Font `Segoe UI Bold`, cỡ `44px`, màu vàng ấm `#FFD700` trên nền hộp xám đen mờ `#1A1A1A` góc trên bên trái (`88, 88`).
- **Dự án `3_pha_B_video/CapCut_Draft/`:** Xuất đồng bộ `draft_content.json` và `draft_meta_info.json` tách sẵn 6–7 đường độc lập (`V1`, `V2`, `T1`, `T2`, `A1`, `A2` kèm keyframe âm lượng, và `A3` nhạc kết) để biên tập viên mở thẳng trên CapCut PC bất cứ lúc nào.

## 4. Cổng Kiểm Duyệt Tự Động (`engine/build_qa_sheet.py`)
Kiểm tra 9 chỉ số bắt buộc: `1920x1080@30fps`, `yuv420p 8-bit`, `48kHz stereo`, độ lệch hình/tiếng `<= 80ms`, đỉnh âm lượng `<= -1.0 dBFS`, `0` lỗi chớp đen (có đối chiếu độ sáng gốc cho các cảnh quay đêm/rạng đông), `0` khoảng câm tiếng số, `.srt` hợp lệ và `CapCut_Draft` đầy đủ.
