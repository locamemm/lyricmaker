import os
import sys
import time
import re

# Thiết lập encoding UTF-8 cho console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import torch
    has_cuda = torch.cuda.is_available()
except Exception:
    has_cuda = False

from faster_whisper import WhisperModel

OUTPUT_DIR = "lyrics"
TXT_DIR = os.path.join(OUTPUT_DIR, ".txt")
LRC_DIR = os.path.join(OUTPUT_DIR, ".lrc")
BACKUP_DIR = "lyrics_backup_whisper"
SUPPORTED_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac")
LOG_FILE = os.path.join(OUTPUT_DIR, "transcribe.log")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "completed_songs.txt")

def get_completed_songs():
    """Đọc danh sách các file bài hát đã được xử lý xong với cấu hình mới"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()

def mark_song_completed(audio_file):
    """Ghi nhận ngay lập tức một bài hát đã hoàn tất (flush & fsync) để chống mất mát nếu tắt máy đột ngột"""
    try:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{audio_file}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass

HALLUCINATION_KEYWORDS = [
    "subscribe", "đăng ký kênh", "ghiền mì gõ", "like and subscribe", 
    "hãy like", "cảm ơn các bạn đã theo dõi", "hẹn gặp lại",
    "chúc các bạn nghe nhạc", "đừng quên like", "chia sẻ", "nhấn chuông",
    "bấm chuông", "subscribers", "hãy đăng ký", "theo dõi kênh",
    "lời bài hát tiếng việt", "không bỏ sót chữ"
]

ENGLISH_ARTISTS = [
    "ed sheeran", "justin bieber", "cash cash", "pink sweat$", 
    "vanessa carlton", "westlife", "george benson", "photograph"
]

def log_print(msg, end="\n", to_file=True):
    print(msg, end=end, flush=True)
    if to_file:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(msg + ("\n" if end == "\n" else ""))
        except Exception:
            pass

def is_hallucination(text):
    t = text.lower().strip()
    if not t:
        return True
    if t == "lời bài hát tiếng việt chuẩn, không bỏ sót chữ.":
        return True
    return any(keyword in t for keyword in HALLUCINATION_KEYWORDS)

def format_lrc_timestamp(seconds):
    """Đổi số giây sang định dạng chuẩn LRC [mm:ss.xx]"""
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:05.2f}"

def format_duration(seconds):
    """Đổi số giây sang giờ:phút:giây dễ nhìn"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"

def is_valid_lyrics(lrc_path, txt_path):
    """Kiểm tra file lyric đã hoàn chỉnh hay chưa (ít nhất 4 dòng)"""
    if not (os.path.exists(lrc_path) and os.path.exists(txt_path)):
        # Fallback kiểm tra tại thư mục cha nếu đang chuyển đổi cấu trúc thư mục
        parent_lrc = os.path.join(os.path.dirname(os.path.dirname(lrc_path)), os.path.basename(lrc_path))
        parent_txt = os.path.join(os.path.dirname(os.path.dirname(txt_path)), os.path.basename(txt_path))
        if os.path.exists(parent_lrc) and os.path.exists(parent_txt):
            lrc_path, txt_path = parent_lrc, parent_txt
        else:
            return False
    try:
        with open(lrc_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("[ti:")]
        return len(lines) >= 4
    except Exception:
        return False

def consolidate_segments(raw_segments):
    """
    Gộp các phân đoạn bị cắt vụn do ca sĩ ngân dài hoặc lấy hơi (orphan words),
    đảm bảo các câu hát hoàn chỉnh, tự nhiên và đúng nhịp.
    """
    if not raw_segments:
        return []
        
    consolidated = []
    for s in raw_segments:
        text = s["text"].strip()
        if not text or is_hallucination(text):
            continue
            
        if not consolidated:
            consolidated.append(dict(s))
            continue
            
        prev = consolidated[-1]
        gap = s["start"] - prev["end"]
        words = text.split()
        prev_words = prev["text"].split()
        
        # 1. Phân đoạn trước quá ngắn (<= 2 từ như 'Khi', 'Em', 'Vỡ tan') và khoảng cách ngắn (< 1.8s)
        # 2. Phân đoạn hiện tại quá ngắn (1 từ như 'Buông', 'mình') và khoảng cách ngắn (< 1.2s)
        # 3. Khoảng nghỉ rất ngắn (< 0.6s) và câu trước chưa quá dài (< 12 từ)
        should_merge = (
            (len(prev_words) <= 2 and gap < 1.8) or
            (len(words) <= 2 and gap < 1.2) or
            (gap < 0.6 and len(prev_words) + len(words) <= 12)
        )
        
        if should_merge:
            prev["text"] = f"{prev['text']} {text}"
            prev["end"] = s["end"]
        else:
            consolidated.append(dict(s))
            
    return consolidated

def process_segments(segments, song_name):
    """Chuyển đổi generator segments thành danh sách câu sạch và format LRC/TXT sau khi gộp câu"""
    raw_list = []
    for segment in segments:
        text = segment.text.strip()
        if text and not is_hallucination(text):
            raw_list.append({
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": text
            })
            
    consolidated = consolidate_segments(raw_list)
    
    lrc_lines = [f"[ti:{song_name}]"]
    txt_lines = []
    clean_texts = []

    for item in consolidated:
        start = item["start"]
        end = item["end"]
        text = item["text"].strip()
        
        clean_texts.append(text)
        txt_lines.append(f"[{start:05.2f}s -> {end:05.2f}s] {text}")
        lrc_timestamp = format_lrc_timestamp(start)
        lrc_lines.append(f"[{lrc_timestamp}]{text}")

    return clean_texts, txt_lines, lrc_lines

def transcribe_file(model, audio_file, force=False):
    song_name = os.path.splitext(os.path.basename(audio_file))[0]
    txt_output_path = os.path.join(TXT_DIR, f"{song_name}.txt")
    lrc_output_path = os.path.join(LRC_DIR, f"{song_name}.lrc")
    
    # 1. Bỏ qua bài hát đã có lyric đầy đủ (>= 4 câu) nếu không bật force
    if not force and is_valid_lyrics(lrc_output_path, txt_output_path):
        return "skipped"

    # 2. Bỏ qua file bị hỏng / quá nhỏ (< 200 KB)
    if os.path.getsize(audio_file) < 200 * 1024:
        return "skipped (file hỏng < 200KB)"

    start_time = time.time()

    # Nhận diện ngôn ngữ và tạo initial_prompt giàu ngữ cảnh âm nhạc
    lower_name = audio_file.lower()
    is_english = any(artist in lower_name for artist in ENGLISH_ARTISTS)
    is_chinese = "热恋" in audio_file

    # Làm sạch tên bài hát để prompt không bị nhiễu bởi các tag MP3/LYRICS/YouTube
    clean_title = re.sub(r'^(?:\[.*?\]|\(.*?\)|_|\s|-)+', '', song_name).strip()
    clean_title = re.sub(r'^(?:AUDIO|LYRICS?|OFFICIAL\s*(?:MV)?)\s*[-_|⧸]\s*', '', clean_title, flags=re.IGNORECASE).strip()
    if not clean_title:
        clean_title = song_name

    if is_chinese:
        lang = "zh"
        prompt = None
    elif is_english:
        lang = "en"
        prompt = f"Lyrics for the song '{clean_title}'. Clear English singing without missing words."
    else:
        lang = "vi"
        prompt = f"Lời bài hát '{clean_title}'. Ca từ tiếng Việt rõ ràng, từng câu từng chữ không bỏ sót."

    # Cấu hình tối ưu cho âm nhạc:
    # - vad_filter=False: Không dùng Silero VAD ban đầu vì sẽ nuốt mất tiếng hát nhỏ/nhẹ ở intro trên nền nhạc cụ
    # - word_timestamps=True: Đo chính xác từng từ để căn nhịp và nối câu
    # - condition_on_previous_text=False: Chống lặp vòng lặp ảo giác qua các đoạn dạo nhạc (bridge/outro)
    # - no_speech_threshold=0.6: Chuẩn Whisper (tránh drop nhầm tiếng hát bị nhạc nền/live hò reo lấn át)
    # - compression_ratio_threshold=2.6: Cho phép điệp khúc lặp lại nhiều lần mà không bị coi là vòng lặp ảo giác
    # - repetition_penalty=1.1: Chống lặp các cụm từ ảo giác
    segments, info = model.transcribe(
        audio_file,
        language=lang,
        initial_prompt=prompt,
        beam_size=3,
        temperature=[0.0, 0.2, 0.4],
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.6,
        repetition_penalty=1.1,
        vad_filter=False,
        word_timestamps=True,
        condition_on_previous_text=False
    )
    
    # Gom danh sách phân đoạn thô ban đầu
    raw_list = []
    for segment in segments:
        text = segment.text.strip()
        if text and not is_hallucination(text):
            raw_list.append({
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": text
            })

    # === TỰ ĐỘNG PHÁT HIỆN & PHỤC HỒI KHOẢNG TRỐNG (GAP RECOVERY PASS) ===
    # Ngăn chặn hoàn toàn việc bỏ sót các đoạn hát lại điệp khúc, cao trào hoặc bị ảo giác che lấp
    total_dur = getattr(info, "duration", 0) or 0
    if raw_list and total_dur > 60:
        recovered = []
        # 1. Quét các khoảng trống bất thường giữa các câu liên tiếp (> 22 giây)
        for i in range(len(raw_list) - 1):
            g_start = raw_list[i]["end"]
            g_end = raw_list[i + 1]["start"]
            if (g_end - g_start) > 22.0:
                log_print(f"\n  [GAP DETECTED] Phát hiện khoảng trống {g_end - g_start:.1f}s ({g_start:.1f}s -> {g_end:.1f}s). Đang tự động quét bù...")
                try:
                    gap_segs, _ = model.transcribe(
                        audio_file,
                        language=lang,
                        initial_prompt=prompt,
                        clip_timestamps=[max(0.0, g_start - 1.0), g_end + 1.0],
                        beam_size=3,
                        temperature=0.0,
                        no_speech_threshold=0.8,
                        compression_ratio_threshold=2.8,
                        word_timestamps=True,
                        condition_on_previous_text=False
                    )
                    for gs in gap_segs:
                        gt = gs.text.strip()
                        if gt and not is_hallucination(gt):
                            recovered.append({
                                "start": round(gs.start, 2),
                                "end": round(gs.end, 2),
                                "text": gt
                            })
                except Exception as e_gap:
                    log_print(f"  [GAP RECOVERY ERROR] {e_gap}")

        # 2. Quét khoảng trống ở cuối bài (nếu ca sĩ hát điệp khúc cuối nhưng câu dừng quá sớm)
        last_end = raw_list[-1]["end"]
        if (total_dur - last_end) > 45.0:
            log_print(f"\n  [GAP DETECTED] Câu cuối dừng ở {last_end:.1f}s (audio dài {total_dur:.1f}s). Đang quét phân đoạn cuối...")
            try:
                gap_segs, _ = model.transcribe(
                    audio_file,
                    language=lang,
                    initial_prompt=prompt,
                    clip_timestamps=[max(0.0, last_end - 1.0), total_dur],
                    beam_size=3,
                    temperature=0.0,
                    no_speech_threshold=0.8,
                    compression_ratio_threshold=2.8,
                    word_timestamps=True,
                    condition_on_previous_text=False
                )
                for gs in gap_segs:
                    gt = gs.text.strip()
                    if gt and not is_hallucination(gt):
                        recovered.append({
                            "start": round(gs.start, 2),
                            "end": round(gs.end, 2),
                            "text": gt
                        })
            except Exception as e_gap:
                log_print(f"  [GAP RECOVERY ERROR] {e_gap}")

        if recovered:
            log_print(f"  [GAP RECOVERED] Phục hồi thành công {len(recovered)} phân đoạn bị sót!")
            raw_list.extend(recovered)
            raw_list.sort(key=lambda x: x["start"])

    consolidated = consolidate_segments(raw_list)
    
    lrc_lines = [f"[ti:{song_name}]"]
    txt_lines = []
    clean_texts = []

    for item in consolidated:
        start = item["start"]
        end = item["end"]
        text = item["text"].strip()
        
        clean_texts.append(text)
        txt_lines.append(f"[{start:05.2f}s -> {end:05.2f}s] {text}")
        lrc_timestamp = format_lrc_timestamp(start)
        lrc_lines.append(f"[{lrc_timestamp}]{text}")

    if not clean_texts:
        return "empty (không phát hiện lời)"

    # Lưu file .txt vào lyrics/
    with open(txt_output_path, "w", encoding="utf-8") as f:
        f.write(f"=== LỜI BÀI HÁT: {song_name} ===\n\n")
        f.write("--- LỜI THUẦN TÚY ---\n")
        for text in clean_texts:
            f.write(f"{text}\n")
        f.write("\n--- LỜI KÈM MỐC THỜI GIAN ---\n")
        f.write("\n".join(txt_lines) + "\n")

    # Lưu file .lrc vào lyrics/
    with open(lrc_output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lrc_lines) + "\n")

    # Đồng bộ lưu bản sao lưu gốc vào lyrics_backup_whisper
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_txt = os.path.join(BACKUP_DIR, f"{song_name}.txt")
        backup_lrc = os.path.join(BACKUP_DIR, f"{song_name}.lrc")
        with open(backup_txt, "w", encoding="utf-8") as f:
            f.write(f"=== LỜI BÀI HÁT: {song_name} ===\n\n--- LỜI THUẦN TÚY ---\n")
            for text in clean_texts:
                f.write(f"{text}\n")
            f.write("\n--- LỜI KÈM MỐC THỜI GIAN ---\n" + "\n".join(txt_lines) + "\n")
        with open(backup_lrc, "w", encoding="utf-8") as f:
            f.write("\n".join(lrc_lines) + "\n")
    except Exception as e:
        log_print(f"Lỗi khi lưu backup: {e}")

    elapsed = time.time() - start_time
    return f"done ({elapsed:.1f}s, {len(clean_texts)} câu)"

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TXT_DIR, exist_ok=True)
    os.makedirs(LRC_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    # Xác định danh sách bài hát và cờ ép buộc chạy lại (force)
    force = False
    args = [a for a in sys.argv[1:]]
    if "--reset-progress" in args:
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
            log_print(">>> Đã xóa file tiến trình cũ (completed_songs.txt).")
        args = [a for a in args if a != "--reset-progress"]

    if "-f" in args or "--force" in args:
        force = True
        args = [a for a in args if a not in ("-f", "--force")]

    is_explicit_files = bool(args and args[0] not in ("--all", "-a", "--rebuild"))
    if is_explicit_files:
        target_files = args
        force = True  # Nếu người dùng truyền đích danh file, luôn chạy bài đó
    else:
        target_files = [
            f for f in os.listdir(".")
            if os.path.isfile(f) and f.lower().endswith(SUPPORTED_EXTS)
        ]
        target_files.sort()

    total_files = len(target_files)
    if total_files == 0:
        log_print("Không tìm thấy file bài hát nào phù hợp!")
        return

    # Tải danh sách bài hát đã hoàn tất với cấu hình mới
    completed_set = get_completed_songs()
    remaining_count = sum(1 for f in target_files if f not in completed_set)

    device = "cuda" if has_cuda else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    log_print("=" * 60)
    log_print(f"  TỔNG SỐ BÀI HÁT: {total_files}")
    log_print(f"  Đã hoàn tất trước đó: {len(completed_set)} bài")
    log_print(f"  Cần xử lý tiếp: {remaining_count} bài")
    log_print(f"  Thiết bị: {device.upper()} | Kiểu dữ liệu: {compute_type}")
    log_print(f"  Model: large-v3-turbo (faster-whisper)")
    log_print(f"  File checkpoint: {PROGRESS_FILE}")
    log_print("=" * 60 + "\n")

    log_print(">>> Đang nạp model vào bộ nhớ...")
    model = WhisperModel("large-v3-turbo", device=device, compute_type=compute_type, cpu_threads=os.cpu_count() or 4)
    log_print(">>> Model đã sẵn sàng! Bắt đầu tiến trình...\n")

    batch_start = time.time()
    processed_count = 0
    skipped_count = 0
    failed_count = 0
    times_taken = []

    for idx, audio_file in enumerate(target_files, start=1):
        # 1. Bỏ qua nếu bài hát này đã được hoàn tất trước đó với settings mới
        if not is_explicit_files and audio_file in completed_set:
            log_print(f"[{idx}/{total_files}] {audio_file} ... ĐÃ HOÀN TẤT VỚI SETTINGS MỚI (Bỏ qua)")
            skipped_count += 1
            continue

        if times_taken:
            avg_time = sum(times_taken) / len(times_taken)
            remaining_songs = total_files - idx + 1
            eta_str = f" | ETA: {format_duration(avg_time * remaining_songs)}"
        else:
            eta_str = ""

        prefix_str = f"[{idx}/{total_files}]{eta_str} {audio_file} ... "
        log_print(prefix_str, end="", to_file=False)
        
        try:
            # Khi làm lại toàn bộ, truyền force=True để đảm bảo dùng cấu hình mới
            status = transcribe_file(model, audio_file, force=True)
            if "empty" in status:
                log_print(f"{prefix_str}KHÔNG LỜI ({status})")
                failed_count += 1
            elif "skipped" in status:
                log_print(f"{prefix_str}{status.upper()}")
                skipped_count += 1
            else:
                log_print(f"{prefix_str}XONG -> {status}")
                processed_count += 1
                # Ghi nhận ngay lập tức vào checkpoint file (flush & sync)
                mark_song_completed(audio_file)
                completed_set.add(audio_file)
                try:
                    s_sec = float(status.split("(")[1].split("s")[0])
                    times_taken.append(s_sec)
                except Exception:
                    pass
        except Exception as e:
            log_print(f"{prefix_str}LỖI: {e}")
            failed_count += 1
            with open(os.path.join(OUTPUT_DIR, "failed_log.txt"), "a", encoding="utf-8") as f_err:
                f_err.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {audio_file}: {e}\n")

    total_time = time.time() - batch_start
    summary_str = f"""
{"=" * 60}
  HOÀN TẤT TOÀN BỘ QUÁ TRÌNH!
  - Đã xử lý mới: {processed_count} bài
  - Đã xong từ trước (bỏ qua): {skipped_count} bài
  - Thất bại / Trống: {failed_count} bài
  - Tổng thời gian phiên này: {format_duration(total_time)}
{"=" * 60}
"""
    log_print(summary_str)

if __name__ == "__main__":
    main()