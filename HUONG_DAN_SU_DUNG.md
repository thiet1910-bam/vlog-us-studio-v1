# 📘 HƯỚNG DẪN SỬ DỤNG NHANH — VLOG US STUDIO V1
*(Dành cho kênh **"Uncle Dinh - Simple Life at 50"** — Áp dụng giống hệt nhau cho cả tập **10 phút** lẫn **30 phút**)*

---

## 1. Cấu trúc 3 ngăn cực gọn của mỗi tập phim

Mỗi khi làm một tập mới (ví dụ `EP002`, `EP003`...), bên trong `episodes/EP002/` bạn **chỉ làm việc với đúng 3 ngăn thư mục**:

```text
episodes/EP002/
├── 1_input/                👈 Nơi bạn bỏ nguyên liệu đầu vào
├── 2_pha_A_kich_ban/       👈 Nơi bạn đọc duyệt kịch bản & lấy chữ thu âm ElevenLabs
└── 3_pha_B_video/          👈 Nơi bạn nhận Video hoàn chỉnh, Phụ đề YouTube & Dự án CapCut
```
*(Ngăn `_he_thong/` là ngăn kỹ thuật ngầm của máy — bạn không cần mở hay chạm vào).*

---

## 2. Quy trình 4 bước sản xuất 1 tập phim mới

### 🔹 BƯỚC 1: Bỏ nguyên liệu đầu vào (`1_input/`) & Khởi động Pha A
1. Tạo thư mục `episodes/EP002/1_input/` (có thể copy mẫu nháp từ `.agent/skills/vlog-us-studio/templates/kich_ban_so_bo_mau.md`).
2. Bỏ vào `1_input/` **2 file**:
   - `kich_ban_so_bo.md` *(ý tưởng hoặc lời dẫn sơ bộ ban đầu)*
   - `video_so_bo.mp4` *(bản video hình thô bạn đã cắt sơ bộ)*
   - *(Tùy chọn)* Nếu muốn AI tự nối dài cảnh khi lời dẫn dài hơn hình, bỏ thêm các file quay gốc vào `1_input/raw_clips/`.
3. Nhắn câu lệnh cho AI:
   > **"Chạy Pha A cho tập EP002"**

---

### 🔹 BƯỚC 2: Bạn đọc duyệt Kịch bản hoàn chỉnh (`2_pha_A_kich_ban/`)
1. AI sẽ xem toàn bộ hình ảnh thật trên `video_so_bo.mp4`, chỉnh lại mọi chi tiết lệch thực tế, gắn giá quy đổi USD & tâm sự văn hóa Việt – Mỹ rồi xuất ra **duy nhất 1 file**:
   - 📄 `2_pha_A_kich_ban/1_kich_ban_hoan_chinh.md` *(có cả lời dẫn Tiếng Anh lẫn dịch nghĩa Tiếng Việt từng đoạn)*.
2. **AI sẽ dừng lại đợi bạn đọc duyệt:**
   - Nếu muốn sửa câu chữ nào hoặc muốn bật thêm cảnh phụ B-roll, bạn nhắn trực tiếp cho AI.
   - Khi đã ưng ý, bạn nhắn: **"Duyệt"**.
3. Ngay khi bạn duyệt, AI mới xuất file chữ sạch:
   - 📄 `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt` *(đã lọc sạch 100% ký hiệu kỹ thuật, sẵn sàng thu âm)*.

---

### 🔹 BƯỚC 3: Tạo giọng đọc `voice.mp3` & Khởi động Pha B
1. Mở `2_pha_A_kich_ban/2_loi_dan_elevenlabs.txt`, copy toàn bộ nội dung dán vào **ElevenLabs** để xuất file thu âm.
2. Lưu file thu âm tên là **`voice.mp3`** vào đúng ngăn đầu vào:
   - 🎙️ `episodes/EP002/1_input/voice.mp3`
3. Nhắn câu lệnh cho AI:
   > **"Tiếp tục Pha B cho tập EP002"**

---

### 🔹 BƯỚC 4: Nhận bộ thành phẩm đăng YouTube hoặc mở CapCut (`3_pha_B_video/`)
Hệ thống tự động chia cụm an toàn (dù tập phim dài 10 phút hay 30 phút đều không bị nghẽn máy), trộn âm thanh 4 lớp và trả đủ **5 thành phẩm sạch** vào `episodes/EP002/3_pha_B_video/`:

1. **`Video_Hoan_Chinh_EP002.mp4`** — Video thành phẩm `1080p30` chuẩn màu Tivi/Điện thoại, đã trộn sẵn giọng đọc + tiếng động đời thực của nhà chú Định + phụ đề chữ lớn `52px` + thẻ giá USD màu vàng ấm `#FFD700` + nhạc guitar mộc đoạn kết.
2. **`Phu_De_YouTube_EP002.srt`** — File phụ đề rời để tải kèm lên mục *Subtitles (CC)* của YouTube Mỹ.
3. **`Anh_Kiem_Duyet_QA.png`** & **`Bao_Cao_Kiem_Duyet.md`** — Ảnh chụp 12 khung hình kiểm tra nhanh & bảng đo 9 chỉ số kỹ thuật.
4. **`CapCut_Draft/`** — Dự án CapCut PC đã tách rời các lớp (Hình gốc, Phụ đề, Thẻ giá USD, Giọng đọc, Tiếng hiện trường có keyframe to/nhỏ, Nhạc kết).
   - *Cách mở trên CapCut PC:* Copy thư mục `CapCut_Draft` (có thể đổi tên thành `EP002_UncleDinh`) dán vào thư mục chứa dự án của CapCut trên máy bạn (`...\CapCut Drafts\`), mở CapCut PC lên là thấy ngay dự án để tinh chỉnh tùy ý!

---

## 🧹 Mẹo giải phóng ổ cứng sau khi đã duyệt xong Video
Sau khi bạn đã kiểm tra ưng ý `Video_Hoan_Chinh_EP002.mp4`, bạn có thể bấm đúp vào file **`GOP_VIDEO_VA_DON_RAC.bat`** ở thư mục gốc rồi gõ `EP002`:
- Công cụ sẽ xóa các file video/âm thanh cắt lẻ tạm thời trong `_he_thong/` để nhẹ ổ cứng, **nhưng vẫn giữ nguyên 100% Video hoàn chỉnh, Phụ đề `.srt` và khả năng mở dự án `CapCut_Draft/`**.
