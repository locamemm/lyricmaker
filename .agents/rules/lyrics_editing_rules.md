# Quy định sửa đổi file Lyric (Lyrics Editing Rules)

Khi thực hiện kiểm tra, chỉnh sửa hoặc chuẩn hóa file lời bài hát (`.lrc` và `.txt`) trong thư viện:

1. **TUYỆT ĐỐI KHÔNG ĐƯỢC THAY ĐỔI THÔNG TIN THỜI GIAN (TIMESTAMP):**
   - Giữ nguyên 100% mốc thời gian gốc ở đầu mỗi dòng:
     - Trong file `.lrc`: phần `[mm:ss.xx]`.
     - Trong file `.txt`: phần `[start.xxs -> end.xxs]`.
   - Các mốc thời gian này được tạo ra đồng bộ theo file âm thanh cục bộ (dựa trên phân đoạn âm thanh thực tế của file nhạc bằng Whisper).

2. **CHỈ ĐƯỢC PHÉP CHỈNH SỬA PHẦN NỘI DUNG CHỮ (TEXT):**
   - Sửa các từ ngữ bị nhận diện sai chính tả, hallucination (ảo giác AI), lệch từ vựng hoặc thiếu dấu tiếng Việt sao cho khớp chính xác với lời bài hát gốc chính thức từ ca sĩ/nhạc sĩ phát hành.
   - Không được tự ý cắt ngắn hoặc chèn thêm các mốc thời gian mới từ các trang mạng (như Zing MP3, Spotify, NetEase) làm lệch mốc thời gian của file nhạc người dùng.

3. **CẤU TRÚC FILE PHẢI ĐƯỢC BẢO TOÀN:**
   - File `.lrc`: Phải bắt đầu bằng `[ti:Tên Bài Hát]` và theo sau là các dòng `[mm:ss.xx]Lời bài hát`.
   - File `.txt`: Phải giữ đầy đủ 2 phần:
     - `--- LỜI THUẦN TÚY ---`
     - `--- LỜI KÈM MỐC THỜI GIAN ---`
