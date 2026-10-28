# 🎬 Vlog-US-Studio-v1

**2-Phase Authentic Documentary Production Engine** (AI Director Phase A $\rightarrow$ Human Approval Gate $\rightarrow$ Automated 4-Layer Audio, Burned Typography & 6-Track CapCut Editor Phase B) built for US-targeted lifestyle & family documentary channels (`10–30+ min` episodes).

---

## ✨ Kiến Trúc Tinh Gọn 2 Pha & 3 Ngăn Thư Mục

Mỗi tập phim (`episodes/<EP_ID>/`) chỉ hiển thị **3 ngăn thư mục sạch cho con người** (mọi dữ liệu kỹ thuật trung gian được giấu kín trong `_he_thong/`):

```text
Vlog-US-Studio-v1/
├── .agent/skills/vlog-us-studio/   # Bộ điều phối AI (SKILL.md, brand_dna_us.json, references/, templates/)
├── engine/                         # 10 công cụ xử lý hình ảnh, âm thanh 4 lớp, phụ đề ASS & CapCut Bridge
├── shared/                         # Font chữ, LUT chuẩn màu, âm thanh phòng, tiếng gõ nhãn USD & guitar mộc
├── episodes/                       # Thư mục chứa từng tập phim (được .gitignore để giữ sạch kho mã nguồn)
│   └── <EP_ID>/
│       ├── 1_input/                # kich_ban_so_bo.md, video_so_bo.mp4, (raw_clips/), voice.mp3
│       ├── 2_pha_A_kich_ban/       # 1_kich_ban_hoan_chinh.md -> [DUYỆT] -> 2_loi_dan_elevenlabs.txt
│       └── 3_pha_B_video/          # Video_Hoan_Chinh.mp4, Phu_De_YouTube.srt, CapCut_Draft/, QA files
├── GOP_VIDEO_VA_DON_RAC.bat        # Tiện ích giải phóng file cắt lẻ tạm thời sau khi duyệt xong video
├── HUONG_DAN_SU_DUNG.md            # Sổ tay hướng dẫn 4 bước bằng tiếng Việt dễ hiểu
└── requirements.txt                # Các thư viện Python cần thiết
```

---

## 🚀 Cài Đặt Nhanh

```powershell
pip install -r requirements.txt
python engine/preflight.py
```

Xem hướng dẫn vận hành chi tiết từng bước bằng tiếng Việt tại **[HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md)**.
