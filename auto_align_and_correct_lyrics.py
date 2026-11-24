import os
import sys
import re
import time
import json
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
import requests
import urllib3
import mutagen
import syncedlyrics

urllib3.disable_warnings()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Directories
MUSIC_DIR = os.path.dirname(os.path.abspath(__file__))
LYRICS_DIR = os.path.join(MUSIC_DIR, "lyrics")
LRC_DIR = os.path.join(LYRICS_DIR, ".lrc")
TXT_DIR = os.path.join(LYRICS_DIR, ".txt")
BACKUP_DIR = os.path.join(MUSIC_DIR, "lyrics_backup_whisper")
LOG_FILE = os.path.join(LYRICS_DIR, "auto_alignment.log")
REPORT_FILE = os.path.join(MUSIC_DIR, "lyrics_sync_report.json")
FINAL_REPORT = os.path.join(LYRICS_DIR, "final_report.json")

SUPPORTED_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac")
NUM_WORKERS = 4

# Zing API
ZING_VERSION = "1.6.34"
ZING_URL = "https://zingmp3.vn"
ZING_SECRET = "2aa2d1c561e809b267f3638c4a307aab"
ZING_API_KEY = "88265e23d4284f25963e6eedac8fbfa3"

# Exempt list: Already perfected manually or by reference
EXEMPT_STEMS = {
    "Chịu Cách Mình Nói Thua",
    "RHYDER - CHỊU CÁCH MÌNH NÓI THUA ｜ ft. BAN x COOLKID ｜ OFFICIAL MUSIC VIDEO",
    "Say Yes - OgeNus và PiaLinh ｜ Chọn gì？ Chọn ĐI! ｜ OFFICIAL MUSIC VIDEO",
    "QUÂN A.P x DOÃN HIẾU ｜ CÓ AI HẸN HÒ CÙNG EM CHƯA ｜ OFFICIAL MV",
    "Nụ Cười E Là Nắng - Lofi",
    "Vết Mưa",
    "Simple Love",
    "scratch_intro"
}

print_lock = threading.Lock()
thread_local = threading.local()

def log(msg):
    with print_lock:
        print(msg, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

def clean_word(w):
    return re.sub(r'[^\w\s]', '', w).lower().strip()

def format_lrc_ts(seconds):
    if seconds < 0: seconds = 0.0
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:05.2f}"

def parse_time_token(token):
    token = token.rstrip('s').strip()
    if ':' in token:
        parts = token.split(':')
        return int(parts[0]) * 60 + float(parts[1])
    return float(token)

def parse_txt_timed(path):
    timed = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for l in f:
                m = re.match(r'\[(.*?)\s*->\s*(.*?)\]\s*(.*)', l.strip())
                if m:
                    try:
                        s = parse_time_token(m.group(1))
                        e = parse_time_token(m.group(2))
                        timed.append((s, e, m.group(3).strip()))
                    except Exception:
                        pass
    return timed

def parse_lrc(path):
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for l in f:
                m = re.match(r'\[(\d{1,2}:\d{2}\.?\d*)\]\s*(.*)', l.strip())
                if m:
                    try:
                        sec = parse_time_token(m.group(1))
                        txt = m.group(2).strip()
                        lines.append((sec, txt))
                    except Exception:
                        pass
    return lines

def get_zing_session():
    if not hasattr(thread_local, "zing_session"):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://zingmp3.vn/"
        })
        try:
            s.get(ZING_URL, timeout=4)
        except Exception:
            pass
        thread_local.zing_session = s
    return thread_local.zing_session

def get_hash256(s):
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def get_hmac512(s, key):
    import hmac, hashlib
    return hmac.new(key.encode("utf-8"), s.encode("utf-8"), hashlib.sha512).hexdigest()

def get_zing_sig(path, song_id, ctime):
    param_str = f"ctime={ctime}id={song_id}version={ZING_VERSION}"
    return get_hmac512(path + get_hash256(param_str), ZING_SECRET)

def clean_noise(text):
    patterns = [
        r'\[.*?\]',
        r'\(feat\.?.*?\)',
        r'\(ft\.?.*?\)',
        r'\(prod\.?.*?\)',
        r'\(official.*?\)',
        r'\(lyric.*?\)',
        r'\(audio.*?\)',
        r'\(video.*?\)',
        r'\(cover.*?\)',
        r'\(remix.*?\)',
        r'\(lofi.*?\)',
        r'\(live.*?\)',
        r'\(ost.*?\)',
        r'\(original.*?\)',
        r'\(album.*?\)',
        r'\(track\s*\d+.*?\)',
        r'【.*?】',
        r'「.*?」',
        r'#\S+',
        r'official\s*(mv|music\s*video|audio|lyrics?)?',
        r'lyrics?\s*(video)?',
        r'visualizer',
        r'acoustic\s*version',
        r'the\s*album',
        r'album\s*[\'\"＂”‟“”].*?[\'\"＂”‟“”]',
        r'ep\s*[\'\"＂”‟“”].*?[\'\"＂”‟“”]',
        r'\.{2,}.*$',
    ]
    t = text
    for p in patterns:
        t = re.sub(p, ' ', t, flags=re.IGNORECASE)
    return t

def extract_search_queries(audio_path):
    stem = unicodedata.normalize("NFC", os.path.splitext(os.path.basename(audio_path))[0])
    queries = []

    # 1. Mutagen metadata
    try:
        m = mutagen.File(audio_path)
        if m and hasattr(m, 'tags') and m.tags:
            tag_title = None
            tag_artist = None
            for k in ['TIT2', 'title', '\xa9nam']:
                if k in m.tags:
                    v = m.tags[k]
                    tag_title = str(v[0]) if isinstance(v, (list, tuple)) else str(v)
                    break
            for k in ['TPE1', 'artist', '\xa9ART']:
                if k in m.tags:
                    v = m.tags[k]
                    tag_artist = str(v[0]) if isinstance(v, (list, tuple)) else str(v)
                    break
            if tag_title:
                tag_title = unicodedata.normalize("NFC", tag_title)
                clean_tt = re.sub(r'[\'\"＂”‟“”\(\)]', ' ', clean_noise(tag_title)).strip()
                if tag_artist:
                    tag_artist = unicodedata.normalize("NFC", tag_artist)
                    clean_ta = re.sub(r'[\'\"＂”‟“”\(\)]', ' ', clean_noise(tag_artist)).replace('/', ' ').replace(';', ' ').strip()
                    queries.append(f"{clean_tt} {clean_ta}")
                queries.append(clean_tt)
    except Exception:
        pass

    # 2. Quoted song name
    quoted = re.findall(r'[\'\"＂”‟“]([^\'\"＂”‟“”]{3,50})[\'\"＂”‟“]', stem)
    for q in quoted:
        cq = unicodedata.normalize("NFC", clean_noise(q)).strip()
        if len(cq) >= 3 and not any(bad in cq.lower() for bad in ['album', 'số không', '99%', 'now i don']):
            queries.append(cq)

    # 3. Filename sections
    fn = stem
    fn = re.sub(r'^\d{1,2}\s*[\.\-]\s*', '', fn)
    fn = re.sub(r'^(AUDIO|LYRICS?|OFFICIAL\s*(MV)?)\s*[\|\-｜⧸]\s*', '', fn, flags=re.IGNORECASE)
    sections = [clean_noise(s).strip() for s in re.split(r'\s*[\|\｜\⧸]\s*', fn)]
    sections = [re.sub(r'[\'\"＂”‟“”\(\)]', ' ', s).strip() for s in sections if s]
    sections = [s for s in sections if s and not any(bad in s.lower() for bad in ['99%', 'the album', 'cover', 'live at', 'audio', 'video'])]

    if sections:
        main_sec = sections[0]
        if ' - ' in main_sec:
            parts = [p.strip() for p in main_sec.split(' - ') if p.strip()]
            if len(parts) >= 2:
                p1, p2 = parts[0], parts[1]
                if len(sections) > 1 and len(sections[1]) < 40:
                    queries.append(f"{p1} {sections[1]}")
                    queries.append(f"{p2} {sections[1]}")
                queries.append(f"{p1} {p2}")
                queries.append(f"{p2} {p1}")
                queries.append(p1)
                queries.append(p2)
        else:
            if len(sections) > 1:
                queries.append(f"{sections[0]} {sections[1]}")
            queries.append(sections[0])

    final_queries = []
    for q in queries:
        q_norm = unicodedata.normalize("NFC", q)
        q_simp = re.sub(r'\bRPT\s+MCK\b', 'MCK', q_norm, flags=re.IGNORECASE)
        q_simp = re.sub(r'\s+(ft|feat)\.?\s+.*$', '', q_simp, flags=re.IGNORECASE)
        for variant in [q_norm, q_simp]:
            vc = re.sub(r'[^\w\s\d]', ' ', variant)
            vc = re.sub(r'\s+', ' ', vc).strip()
            if len(vc) >= 2 and vc not in final_queries:
                final_queries.append(vc)

    return final_queries

def fetch_zing_lrc(query):
    try:
        session = get_zing_session()
        q_norm = unicodedata.normalize("NFC", query)
        ac_url = f"https://ac.mp3.zing.vn/complete?type=artist,song,key,hub&num=5&query={q_norm}"
        r_ac = session.get(ac_url, verify=False, timeout=3.5)
        data_ac = r_ac.json()
        song_id = None
        for sec in data_ac.get("data", []):
            for s in sec.get("song", []):
                song_id = s.get("id")
                break
            if song_id:
                break
        if not song_id:
            return None

        ctime = str(int(time.time()))
        path = "/api/v2/lyric/get/lyric"
        sig = get_zing_sig(path, song_id, ctime)
        params = {
            "id": song_id,
            "ctime": ctime,
            "version": ZING_VERSION,
            "apiKey": ZING_API_KEY,
            "sig": sig
        }
        r = session.get(f"{ZING_URL}{path}", params=params, timeout=3.5)
        d = r.json().get("data", {})
        lrc_url = d.get("file")
        if lrc_url:
            r_lrc = session.get(lrc_url, timeout=3.5)
            if r_lrc.status_code == 200 and len(r_lrc.text) > 50:
                return r_lrc.text

        sentences = d.get("sentences", [])
        if sentences:
            lines = []
            for s in sentences:
                words = s.get("words", [])
                if not words: continue
                start_ms = words[0].get("startTime", 0)
                text = " ".join(w.get("data", "") for w in words).strip()
                sec = start_ms / 1000.0
                m = int(sec // 60)
                sc = sec % 60
                lines.append(f"[{m:02d}:{sc:05.2f}]{text}")
            if len(lines) >= 4:
                return "\n".join(lines)
    except Exception:
        pass
    return None

def fetch_netease_lrc(query):
    try:
        q_norm = unicodedata.normalize("NFC", query)
        res = syncedlyrics.search(q_norm, providers=['NetEase'])
        if res and '[' in res and len(res) > 50:
            return res
    except Exception:
        pass
    return None

def parse_raw_lrc_lines(raw_text):
    clean_lines = []
    time_regex = re.compile(r'\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]')
    for line in raw_text.splitlines():
        line_s = line.strip()
        if not line_s or re.match(r'\[(re|ve|ar|al|by|offset|length):', line_s, flags=re.IGNORECASE):
            continue
        text = time_regex.sub('', line_s).strip()
        if not text or re.match(r'^(Bài hát|Ca sĩ|Nhạc sĩ|Lời|Sáng tác|Trình bày|Producer|Beat|Mix|制作人|作词|作曲)\s*[:：]', text, flags=re.IGNORECASE):
            continue
        clean_lines.append(text)
    return clean_lines

def align_lyrics(segs, off_clean):
    whisper_tokens = []
    seg_indices = []
    for s_idx, (start, end, text) in enumerate(segs):
        for w in text.split():
            whisper_tokens.append(clean_word(w))
            seg_indices.append(s_idx)

    off_tokens = []
    for line in off_clean:
        for w in line.split():
            cw = clean_word(w)
            if cw:
                off_tokens.append((cw, w))

    if not whisper_tokens or not off_tokens:
        return None, 0.0

    sm = SequenceMatcher(None, whisper_tokens, [w[0] for w in off_tokens])
    blocks = sm.get_matching_blocks()
    
    matched_words = sum(b.size for b in blocks)
    match_ratio = matched_words / max(len(whisper_tokens), 1)

    # Direct match mapping
    off_to_w = [None] * len(off_tokens)
    for b in blocks:
        for k in range(b.size):
            off_to_w[b.b + k] = b.a + k

    matched_indices = [i for i, w in enumerate(off_to_w) if w is not None]
    if not matched_indices:
        return None, 0.0

    first_m = matched_indices[0]
    last_m = matched_indices[-1]

    # Pre-gap guard (at most 3 words before first match)
    w_first = off_to_w[first_m]
    for i in range(max(0, first_m - 3), first_m):
        off_to_w[i] = w_first

    # Internal gaps proportional interpolation
    for idx in range(len(matched_indices) - 1):
        o_start = matched_indices[idx]
        o_end = matched_indices[idx + 1]
        w_start = off_to_w[o_start]
        w_end = off_to_w[o_end]
        gap_len = o_end - o_start
        for o_i in range(o_start + 1, o_end):
            progress = (o_i - o_start) / gap_len
            w_i = int(round(w_start + progress * (w_end - w_start)))
            off_to_w[o_i] = max(0, min(len(whisper_tokens) - 1, w_i))

    # Post-gap guard (at most 4 words after last match)
    w_last = off_to_w[last_m]
    for i in range(last_m + 1, min(len(off_tokens), last_m + 4)):
        off_to_w[i] = w_last

    # Group official tokens into exact Whisper segments
    seg_words = {i: [] for i in range(len(segs))}
    for o_idx, w_idx in enumerate(off_to_w):
        if w_idx is not None:
            s_idx = seg_indices[w_idx]
            seg_words[s_idx].append(off_tokens[o_idx][1])

    final_lines = []
    for i in range(len(segs)):
        s, e, orig_w = segs[i]
        words = seg_words[i]
        orig_len = len(orig_w.split())
        # Guard against bloated sentences
        if words and len(words) <= max(16, int(orig_len * 2.2)):
            new_text = " ".join(words).strip()
        else:
            new_text = orig_w
        final_lines.append((s, e, new_text))

    return final_lines, match_ratio

def process_song(audio_file):
    stem = os.path.splitext(audio_file)[0]
    
    if stem in EXEMPT_STEMS:
        return stem, "EXEMPT_PRESERVED", 1.0, "Manual"

    b_txt = os.path.join(BACKUP_DIR, f"{stem}.txt")
    b_lrc = os.path.join(BACKUP_DIR, f"{stem}.lrc")
    c_txt = os.path.join(TXT_DIR, f"{stem}.txt")
    c_lrc = os.path.join(LRC_DIR, f"{stem}.lrc")

    if not os.path.exists(b_txt) or not os.path.exists(b_lrc):
        return stem, "NO_BACKUP_EXISTS", 0.0, "None"

    segs = parse_txt_timed(b_txt)
    if not segs:
        lrc_timed = parse_lrc(b_lrc)
        for i in range(len(lrc_timed)):
            s, txt = lrc_timed[i]
            e = lrc_timed[i+1][0] if i + 1 < len(lrc_timed) else s + 4.0
            segs.append((s, e, txt))

    if not segs:
        return stem, "NO_VALID_SEGMENTS", 0.0, "None"

    # Search online
    audio_path = os.path.join(MUSIC_DIR, audio_file)
    queries = extract_search_queries(audio_path)
    
    best_candidate_lines = None
    best_source = None
    best_ratio = 0.0
    best_final_lines = None

    # Try Zing MP3
    for q in queries[:4]:
        raw = fetch_zing_lrc(q)
        if raw:
            off_lines = parse_raw_lrc_lines(raw)
            if len(off_lines) >= 4:
                final_lines, ratio = align_lyrics(segs, off_lines)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_candidate_lines = final_lines
                    best_source = "ZingMP3"
                    if ratio >= 0.65:
                        break

    # If match ratio < 0.50, try NetEase
    if best_ratio < 0.50:
        for q in queries[:3]:
            raw = fetch_netease_lrc(q)
            if raw:
                off_lines = parse_raw_lrc_lines(raw)
                if len(off_lines) >= 4:
                    final_lines, ratio = align_lyrics(segs, off_lines)
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_candidate_lines = final_lines
                        best_source = "NetEase"
                        if ratio >= 0.65:
                            break

    # Threshold check: Must achieve at least 35% match with Whisper to prevent wrong songs
    if best_candidate_lines and best_ratio >= 0.35:
        # Write files with 100% PRESERVED TIMESTAMPS
        lrc_content = [f"[ti:{stem}]"]
        txt_plain = []
        txt_timed = []

        for s, e, text in best_candidate_lines:
            ts = format_lrc_ts(s)
            lrc_content.append(f"[{ts}]{text}")
            txt_plain.append(text)
            txt_timed.append(f"[{s:05.2f}s -> {e:05.2f}s] {text}")

        txt_content = (
            f"=== LỜI BÀI HÁT: {stem} ===\n\n"
            f"--- LỜI THUẦN TÚY ---\n"
            + "\n".join(txt_plain)
            + "\n\n--- LỜI KÈM MỐC THỜI GIAN ---\n"
            + "\n".join(txt_timed)
            + "\n"
        )

        with open(c_lrc, "w", encoding="utf-8") as f:
            f.write("\n".join(lrc_content) + "\n")
        with open(c_txt, "w", encoding="utf-8") as f:
            f.write(txt_content)

        return stem, "ALIGNED_AND_CORRECTED", best_ratio, best_source

    else:
        # Keep original Whisper text safely
        return stem, "KEPT_WHISPER_ORIGINAL", best_ratio, best_source or "LowSimilarity/NotFound"

def main():
    audio_files = [
        f for f in os.listdir(MUSIC_DIR)
        if os.path.isfile(os.path.join(MUSIC_DIR, f)) and f.lower().endswith(SUPPORTED_EXTS)
    ]
    audio_files.sort()

    total = len(audio_files)
    log("=" * 70)
    log("  TIẾN TRÌNH TỰ ĐỘNG ĐỐI CHIẾU & CHUẨN HÓA LỜI BÀI HÁT TRỰC TUYẾN")
    log("  (TUYỆT ĐỐI BẢO TOÀN 100% TIMESTAMP GỐC CỦA WHISPER THEO AGENTS.MD)")
    log(f"  Tổng số file âm thanh: {total}")
    log(f"  Số luồng worker: {NUM_WORKERS}")
    log(f"  File log: {LOG_FILE}")
    log("=" * 70)

    start_time = time.time()
    stats = {
        "ALIGNED_AND_CORRECTED": 0,
        "KEPT_WHISPER_ORIGINAL": 0,
        "EXEMPT_PRESERVED": 0,
        "NO_BACKUP_EXISTS": 0,
        "NO_VALID_SEGMENTS": 0
    }

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(process_song, af): af for af in audio_files}
        for idx, future in enumerate(as_completed(futures), 1):
            stem, status, ratio, source = future.result()
            stats[status] = stats.get(status, 0) + 1

            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 1
            rem_songs = total - idx
            rem_sec = rem_songs / rate
            m, s = divmod(int(rem_sec), 60)
            eta_str = f"{m:02d}m{s:02d}s"

            if status == "ALIGNED_AND_CORRECTED":
                log(f"[{idx:03d}/{total}][ETA: {eta_str}] OK (match={ratio:4.1%}, {source:<8}) -> {stem[:45]}")
            elif status == "EXEMPT_PRESERVED":
                log(f"[{idx:03d}/{total}][ETA: {eta_str}] PRESERVED_MANUAL -> {stem[:45]}")
            else:
                log(f"[{idx:03d}/{total}][ETA: {eta_str}] KEPT_ORIGINAL (match={ratio:4.1%}) -> {stem[:45]}")

    total_time = time.time() - start_time
    m_tot, s_tot = divmod(int(total_time), 60)

    log("\n" + "=" * 70)
    log(f"  HOÀN TẤT ĐỐI CHIẾU TOÀN BỘ {total} BÀI HÁT (Tổng thời gian: {m_tot}m {s_tot}s)")
    log(f"  - Chuẩn hóa thành công từ nguồn trực tuyến: {stats['ALIGNED_AND_CORRECTED']} bài")
    log(f"  - Giữ nguyên lời chuẩn thủ công / mẫu:     {stats['EXEMPT_PRESERVED']} bài")
    log(f"  - Giữ nguyên lời Whisper an toàn (độ lệch cao / không tìm thấy): {stats['KEPT_WHISPER_ORIGINAL']} bài")
    log("=" * 70)

    # Update reports
    report_data = {
        "total_audio_files": total,
        "aligned_and_corrected_from_web": stats["ALIGNED_AND_CORRECTED"],
        "preserved_manual_perfection": stats["EXEMPT_PRESERVED"],
        "kept_whisper_original": stats["KEPT_WHISPER_ORIGINAL"],
        "rule_enforced": "100% Original Whisper Timestamps Preserved (AGENTS.md)",
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
