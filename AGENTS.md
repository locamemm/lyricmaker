# Workspace Rules: Music & Lyrics Management

## Cấu trúc thư mục Lời bài hát (Lyrics Directory Structure)
- **Thư mục file `.lrc`:** `lyrics/.lrc/`
- **Thư mục file `.txt`:** `lyrics/.txt/`
- **Thư mục sao lưu gốc của Whisper:** `lyrics_backup_whisper/`

## Quy tắc chỉnh sửa lời bài hát (Lyrics Editing Rule)
- **Bảo toàn mốc thời gian gốc (Preserve Original Timestamps):** Khi sửa lời bài hát trong `lyrics/.lrc/` và `lyrics/.txt/`, **TUYỆT ĐỐI KHÔNG ĐƯỢC sửa đổi phần thông tin thời gian ở trước** (timestamp dạng `[mm:ss.xx]` trong `.lrc` hoặc `[start.xxs -> end.xxs]` trong `.txt`).
- **Chỉ sửa phần nội dung chữ:** Chỉ thay thế các từ ngữ nghe nhầm, lỗi phiên âm hoặc ảo giác của Whisper bằng lời bài hát gốc chính xác trên mạng, giữ nguyên vị trí và mốc thời gian của từng dòng theo file âm thanh gốc.
- **Tham chiếu thư mục sao lưu:** File gốc ban đầu được lưu trữ trong `lyrics_backup_whisper`.

## Quy tắc kiểm tra toàn vẹn độ phủ lời bài hát (Coverage & Gap Integrity Rule)
- **Kiểm tra đoạn điệp khúc lặp lại (Repeated Chorus / Verse 2):** Tuyệt đối không được bỏ sót các đoạn ca sĩ hát lại điệp khúc, verse 2 hoặc điệp khúc cao trào ở nửa sau bài hát (đặc biệt là các bản Live Performance, Acoustic hoặc Extended).
- **Kiểm tra độ phủ thời lượng (Coverage Check):** Mốc thời gian câu hát cuối cùng trong file lyric phải tương xứng với phần kết bài hát (`end_timestamp >= 70%` tổng thời lượng audio). Nếu audio dài 5 - 6 phút mà lyric dừng ở phút 2 - 3 thì chắc chắn đã bị sót lời nửa sau.
- **Kiểm tra khoảng trống bất thường (Gap Check):** Trong suốt bài hát, không được để tồn tại khoảng trống không lời `> 25 giây` ở giữa bài (trừ khi là đoạn độc tấu solo nhạc cụ thực sự).
- **Cấu hình Whisper chống nuốt lời:**
  - `no_speech_threshold` phải đặt tối thiểu `0.6` (tuyệt đối không đặt `<= 0.3` vì sẽ nhầm tiếng hát live/đè nhạc thành im lặng).
  - `compression_ratio_threshold` đặt `>= 2.6` để tránh drop các câu điệp khúc lặp lại nhiều lần.
  - Luôn kích hoạt cơ chế tự động bù khoảng trống (`Gap Recovery Pass`) trên các đoạn nghi vấn bị ảo giác hoặc che lấp.
