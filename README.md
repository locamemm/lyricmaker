# Music & Lyrics Management Toolkit

Bộ công cụ tự động hóa nhận diện, phiên âm lời bài hát bằng AI (Faster-Whisper), căn chỉnh mốc thời gian (LRC / TXT) và đồng bộ lời chính thức từ các nguồn phát hành trực tuyến.

---

## 🌟 Tính năng chính

1. **Phiên âm ca từ bằng Whisper AI (`whisper.py`):**
   - Sử dụng mô hình `large-v3-turbo` (thư viện `faster-whisper`) tối ưu hóa tốc độ và độ chính xác ca từ tiếng Việt và tiếng Anh.
   - **Chống nuốt lời & mất điệp khúc lặp lại:** Cấu hình chuẩn `no_speech_threshold = 0.6` và `compression_ratio_threshold = 2.6`.
   - **Tự động quét bù lỗ hổng (Gap Detection & Auto-Recovery Pass):** Tự động phát hiện các khoảng lặng bất thường giữa 2 câu (`gap > 22s`) hoặc câu cuối dừng quá sớm so với bài hát để quét bù chuyên sâu (`clip_timestamps`), đảm bảo không sót verse 2 hay điệp khúc cao trào.
   - **Triệt tiêu ảo giác YouTube:** Loại bỏ hoàn toàn các chuỗi phụ đề rác (*"Hãy like, subscribe kênh..."*) bằng bộ lọc từ khóa và cơ chế cô lập ngữ cảnh (`condition_on_previous_text = False`).

2. **Bảo toàn mốc thời gian gốc (Preserve Timestamps):**
   - Đảm bảo 100% giữ nguyên mốc thời gian gốc (`[mm:ss.xx]` trong file `.lrc` và `[start.xxs -> end.xxs]` trong file `.txt`) theo đúng phân đoạn âm thanh thực tế của ca sĩ.
   - Chỉ thay thế và chuẩn hóa phần chữ ngữ nghĩa (sửa từ nghe nhầm, lỗi phiên âm AI, chính tả).

3. **Đồng bộ lời bài hát từ web (`sync_lyrics_from_web.py` & `auto_align_and_correct_lyrics.py`):**
   - Tìm kiếm và đối chiếu lời bài hát chính thức từ Zing MP3, LRCLIB, Spotify.
   - Thuật toán căn chỉnh thông minh (fuzzy alignment / dynamic time warping) ghép ca từ chuẩn xác vào từng mốc thời gian của Whisper.

4. **Quản lý & làm sạch thư viện nhạc:**
   - `find_duplicates.py`: Quét và phát hiện các bài hát bị trùng lặp trong thư viện.
   - `fix_truncated_lyrics.py`: Kiểm tra và sửa các file lyric bị cắt ngắn hoặc thiếu câu kết.
   - `apply_rule_and_restore_timestamps.py`: Kiểm định toàn vẹn cấu trúc file theo quy định trong `AGENTS.md`.

---

## 📂 Cấu trúc thư mục

```
Music/
├── AGENTS.md                              # Quy chuẩn quản lý và nghiệm thu lời bài hát
├── whisper.py                             # Script chính phiên âm audio sang LRC/TXT
├── sync_lyrics_from_web.py                # Tìm và đồng bộ lyric chính thức từ web
├── auto_align_and_correct_lyrics.py       # Căn chỉnh lời bài hát theo mốc thời gian
├── apply_rule_and_restore_timestamps.py   # Phục hồi mốc thời gian gốc từ backup
├── fix_truncated_lyrics.py                # Xử lý các file lyric bị cụt câu
├── find_duplicates.py                     # Quét file âm thanh trùng lặp
├── requirements.txt                       # Danh sách thư viện phụ thuộc
├── .gitignore                             # Loại trừ audio, lyric và cache khỏi Git
└── .agents/
    └── rules/
        └── lyrics_editing_rules.md        # Bộ quy tắc chỉnh sửa lyric
```

*Lưu ý: Tất cả các file âm thanh (`.mp3`, `.wav`, ...) và các file lyric (`.lrc`, `.txt`) được loại trừ khỏi kho mã nguồn thông qua `.gitignore`.*

---

## 🚀 Cài đặt & Sử dụng

### 1. Cài đặt môi trường
Yêu cầu Python 3.10 trở lên và [FFmpeg](https://ffmpeg.org/download.html) đã được cài đặt vào PATH hệ thống:

```bash
pip install -r requirements.txt
```

### 2. Phiên âm lời bài hát hàng loạt
Đặt các file âm thanh vào thư mục và chạy:

```bash
python whisper.py
```
Các file lyric sau khi xử lý sẽ tự động được lưu vào:
- `lyrics/.lrc/`: File lời bài hát đồng bộ chuẩn LRC.
- `lyrics/.txt/`: File văn bản gồm lời thuần túy và lời kèm mốc thời gian.
- `lyrics_backup_whisper/`: Bản sao lưu ban đầu từ mô hình Whisper.

### 3. Đồng bộ và chuẩn hóa từ ngữ từ web
```bash
python sync_lyrics_from_web.py
```

---

## 📜 Quy tắc nghiệm thu (AGENTS.md)
- **Độ phủ thời lượng:** Câu hát cuối cùng trong file lyric phải đạt tối thiểu `>= 70%` tổng thời lượng audio.
- **Kiểm tra khoảng trống:** Không tồn tại khoảng trống không lời `> 25 giây` ở giữa bài (ngoại trừ đoạn độc tấu nhạc cụ).
- **Điệp khúc lặp lại:** Bắt buộc có đầy đủ các đoạn ca sĩ hát lại điệp khúc ở nửa sau bài hát.
