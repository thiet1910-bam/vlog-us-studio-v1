# Hướng Dẫn Đạo Diễn Pha A (`phase_a_director_guide.md`)

## 1. Nguyên Tắc Cốt Lõi: "Hình Thật Quyết Định Lời Dẫn"
- Người dùng cung cấp `1_input/kich_ban_so_bo.md` (ý tưởng sơ bộ) và `1_input/video_so_bo.mp4` (bản dựng hình thật).
- **Bắt buộc** mở đọc toàn bộ ảnh chụp `_he_thong/scan/contact_sheets/sheet_*.jpg` sau khi chạy `scan_rough_video.py`.
- Bất kỳ chi tiết nào trong `kich_ban_so_bo.md` khác với hình trên `video_so_bo.mp4` (màu sắc đồ vật, dụng cụ cầm tay, trang phục, thời điểm sáng/tối, vật liệu bàn bếp, chỗ ngủ...) **phải được hiệu đính theo đúng 100% hình thật** và ghi rõ vào trường `visual_corrections_md` để báo cáo minh bạch cho người dùng trong `1_kich_ban_hoan_chinh.md`.

## 2. Nhịp Điệu Lời Dẫn & Khoảng Nghỉ Tiếng Động Đời Thực (ASMR)
- **Tốc độ ước tính khi lên kịch bản:** `~126 từ/phút` (`2.1 từ/giây`), chừa khoảng thở tự nhiên để dù người đọc thực tế đọc ở `125–160 WPM` thì toàn bộ câu thoại vẫn nằm gọn thoải mái trong cảnh quay mà không bị tràn sang cảnh sau.
- **Tỷ lệ vàng:** `52% – 65%` thời lượng có giọng kể ấm áp, xen kẽ `35% – 48%` khoảng nghỉ nghe âm thanh thật tại hiện trường (`asmr_pause_after_sec`: từ `5s` đến `32s` tùy độ hấp dẫn của thao tác tay trên màn hình).
- **Tuyệt đối không nhét ký hiệu sân khấu** kiểu `[pause]`, `[sighs]`, `[ASMR]` vào trường `narration_en` — mọi khoảng nghỉ đã được quản lý bằng con số giây chính xác ở `asmr_pause_after_sec`.

## 3. Cầu Nối Văn Hóa Việt – Mỹ & Quy Đổi USD
- **Quy đổi đơn vị quen thuộc cho khán giả Mỹ:**
  - Chiều dài / diện tích: quy đổi mét sang `feet` và `sq ft` (ví dụ: nhà ống `5x15m` = `16 feet wide, 50 feet deep, ~800 sq ft per floor`).
  - Tiền tệ: quy đổi VND sang `USD` / `cents` (tỷ giá tham chiếu `~25,500 VND = $1 USD`).
- **Tối thiểu 2–5 thẻ giá USD (`usd_callout`) mỗi tập 10 phút:**
  - Khai báo `label` (bắt buộc có ký hiệu `$`) và `trigger_word` (từ tiếng Anh trong câu thoại mà khi người đọc phát âm tới từ đó, thẻ giá màu vàng ấm `#FFD700` sẽ hiện lên kèm tiếng gõ giấy nhẹ).
- **Tối thiểu 2–4 điểm chạm tâm sự Việt – Mỹ (`vn_us_bridge_topic`):**
  - Liên hệ ấm áp, ngang hàng giữa thói quen đời sống ở Mỹ (tách cà phê sáng ngoài hiên nhà, tự tay sửa đồ DIY cuối tuần, chi phí sinh hoạt, không gian phòng riêng...) với nếp sống gia đình 3 thế hệ ở quê Việt Nam.
  - Kiểm tra `_he_thong/contracts/story_memory.json` của các tập trước (nếu có) để không lặp lại y nguyên một phép so sánh cũ.

## 4. Từ Vựng Bị Cấm Tuyệt Đối
- **Cấm văn mẫu AI sáo rỗng (`banned_ai_slop_words`):** `delve`, `tapestry`, `testament`, `symphony`, `nestled`, `beacon`, `embark`, `whispers of`, `dance of`, `in conclusion`, `moreover`, `furthermore`, `bustling metropolis`, `unwavering`, `poignant reminder`, `transcends`, `kaleidoscope`.
- **Cấm than nghèo kể khổ / xin xỏ (`banned_pity_phrases`):** Giọng kể của chú Định luôn đĩnh đạc, tự trọng, biết đủ và ấm áp; tuyệt đối không xin tiền hay gợi sự thương hại.

## 5. Quy Tắc Đề Xuất B-Roll Ngoài (Tôn Trọng Cảnh Thật 100%)
- Mặc định mọi phân đoạn đặt `"broll_approved": false` (ưu tiên tuyệt đối hình thật của gia đình chú Định).
- Chỉ ghi gợi ý nhẹ vào `broll_proposal_note_vi` nếu có một phân đoạn lời dẫn dài hơn cảnh gốc mà không có `raw_clips/` dự phòng, hoặc cần 4 giây cảnh làng quê chuyển đoạn.
- **Chỉ khi người dùng gõ xác nhận duyệt bật B-roll** thì mới chuyển `"broll_approved": true` trước khi chạy `--mode approve-export`.
