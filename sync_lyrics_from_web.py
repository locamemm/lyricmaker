import os
import re
import sys
import time
import shutil
import hashlib
import hmac
import subprocess
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3
import mutagen
import syncedlyrics

urllib3.disable_warnings()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# --- CONFIGURATION ---
MUSIC_DIR = os.path.dirname(os.path.abspath(__file__))
LYRICS_DIR = os.path.join(MUSIC_DIR, "lyrics")
LRC_DIR = os.path.join(LYRICS_DIR, ".lrc")
TXT_DIR = os.path.join(LYRICS_DIR, ".txt")
BACKUP_DIR = os.path.join(MUSIC_DIR, "lyrics_backup_whisper")
REPORT_FILE = os.path.join(MUSIC_DIR, "lyrics_sync_report.json")
LOG_FILE = os.path.join(LYRICS_DIR, "sync_process.log")

SUPPORTED_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac")
NUM_WORKERS = 4

# Zing MP3 API Credentials & Parameters
ZING_VERSION = "1.6.34"
ZING_URL = "https://zingmp3.vn"
ZING_SECRET = "2aa2d1c561e809b267f3638c4a307aab"
ZING_API_KEY = "88265e23d4284f25963e6eedac8fbfa3"

print_lock = threading.Lock()

def log_msg(msg):
    with print_lock:
        print(msg, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

thread_local = threading.local()

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
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def get_hmac512(s, key):
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

    # 1. Mutagen ID3 tags
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

    # 2. Check for quoted title: e.g. 'SÀI GÒN ĐAU LÒNG QUÁ' or 'một đời'
    quoted = re.findall(r'[\'\"＂”‟“]([^\'\"＂”‟“”]{3,50})[\'\"＂”‟“]', stem)
    for q in quoted:
        cq = unicodedata.normalize("NFC", clean_noise(q)).strip()
        if len(cq) >= 3 and not any(bad in cq.lower() for bad in ['album', 'số không', '99%', 'now i don']):
            queries.append(cq)

    # 3. Filename parsing
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
                if not words:
                    continue
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

def fetch_yt_subtitles(query):
    try:
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-sub",
            "--sub-lang", "vi,en",
            "--convert-subs", "lrc",
            "-o", os.path.join(MUSIC_DIR, "_tmp_yt.%(ext)s"),
            f"ytsearch1:{query}"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, text=True)
        # Check if _tmp_yt.vi.lrc or _tmp_yt.en.lrc exists
        for ext in ['.vi.lrc', '.en.lrc', '.lrc']:
            p = os.path.join(MUSIC_DIR, f"_tmp_yt{ext}")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                try:
                    os.remove(p)
                except Exception:
                    pass
                if len(content) > 50 and '[' in content:
                    return content
    except Exception:
        pass
    return None

def parse_lrc_content(lrc_text, song_title):
    raw_lines = lrc_text.splitlines()
    entries = []
    time_regex = re.compile(r'\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]')
    
    for line in raw_lines:
        line_s = line.strip()
        if not line_s or re.match(r'\[(re|ve|ar|al|by|offset|length):', line_s, flags=re.IGNORECASE):
            continue
        matches = list(time_regex.finditer(line_s))
        if not matches:
            continue
        text = time_regex.sub('', line_s).strip()
        if not text or re.match(r'^(Bài hát|Ca sĩ|Nhạc sĩ|Lời|Sáng tác|Trình bày|Producer|Beat|Mix)\s*[:：]', text, flags=re.IGNORECASE):
            continue
            
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            millis = m.group(3)
            if millis:
                if len(millis) == 1:
                    ms = int(millis) * 100
                elif len(millis) == 2:
                    ms = int(millis) * 10
                else:
                    ms = int(millis[:3])
            else:
                ms = 0
            total_sec = round(mins * 60 + secs + ms / 1000.0, 2)
            entries.append((total_sec, text))
            
    entries.sort(key=lambda x: x[0])
    if len(entries) < 4:
        return None, None
        
    lrc_out = [f"[ti:{song_title}]"]
    txt_plain = []
    txt_timed = []
    
    for i, (t, text) in enumerate(entries):
        mins = int(t // 60)
        secs = t % 60
        lrc_timestamp = f"{mins:02d}:{secs:05.2f}"
        lrc_out.append(f"[{lrc_timestamp}]{text}")
        txt_plain.append(text)
        
        if i + 1 < len(entries):
            next_t = entries[i + 1][0]
            end_t = next_t if next_t > t else round(t + 2.5, 2)
        else:
            end_t = round(t + 4.0, 2)
            
        m_start = int(t // 60)
        s_start = t % 60
        m_end = int(end_t // 60)
        s_end = end_t % 60
        time_range = f"{m_start:02d}:{s_start:05.2f}s -> {m_end:02d}:{s_end:05.2f}s"
        txt_timed.append(f"[{time_range}] {text}")
        
    txt_out = (
        f"=== LỜI BÀI HÁT: {song_title} ===\n\n"
        f"--- LỜI THUẦN TÚY ---\n"
        + "\n".join(txt_plain)
        + "\n\n--- LỜI KÈM MỐC THỜI GIAN ---\n"
        + "\n".join(txt_timed)
        + "\n"
    )
    return "\n".join(lrc_out) + "\n", txt_out

def process_song(audio_file):
    stem = os.path.splitext(audio_file)[0]
    audio_path = os.path.join(MUSIC_DIR, audio_file)
    lrc_path = os.path.join(LRC_DIR, f"{stem}.lrc")
    txt_path = os.path.join(TXT_DIR, f"{stem}.txt")
    
    queries = extract_search_queries(audio_path)
    found_lrc = None
    found_source = None
    used_query = None

    # Step 1: Zing MP3 API (official Vietnamese lyrics)
    for q in queries[:4]:
        raw = fetch_zing_lrc(q)
        if raw:
            found_lrc = raw
            found_source = "ZingMP3"
            used_query = q
            break

    # Step 2: NetEase provider (fast, high coverage)
    if not found_lrc:
        for q in queries[:3]:
            raw = fetch_netease_lrc(q)
            if raw:
                found_lrc = raw
                found_source = "NetEase"
                used_query = q
                break

    # Step 3: YouTube Subtitles via yt-dlp (for tracks with official subs)
    if not found_lrc and queries:
        yt_raw = fetch_yt_subtitles(queries[0])
        if yt_raw:
            found_lrc = yt_raw
            found_source = "YouTubeSub"
            used_query = queries[0]

    if found_lrc:
        clean_lrc, clean_txt = parse_lrc_content(found_lrc, stem)
        if clean_lrc and clean_txt:
            # Backup original if exists
            if os.path.exists(lrc_path):
                bak_lrc = os.path.join(BACKUP_DIR, f"{stem}.lrc")
                if not os.path.exists(bak_lrc):
                    try:
                        shutil.copy2(lrc_path, bak_lrc)
                    except Exception:
                        pass
            if os.path.exists(txt_path):
                bak_txt = os.path.join(BACKUP_DIR, f"{stem}.txt")
                if not os.path.exists(bak_txt):
                    try:
                        shutil.copy2(txt_path, bak_txt)
                    except Exception:
                        pass

            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(clean_lrc)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(clean_txt)
                
            line_count = len([l for l in clean_lrc.splitlines() if l.strip() and not l.startswith('[ti:')])
            return {
                "file": audio_file,
                "status": "SUCCESS",
                "source": found_source,
                "lines": line_count,
                "query": used_query
            }

    # If online search couldn't find, keep existing files if present
    has_existing = os.path.exists(lrc_path) and os.path.getsize(lrc_path) > 100
    return {
        "file": audio_file,
        "status": "KEEP_EXISTING" if has_existing else "NOT_FOUND",
        "source": "Existing Whisper" if has_existing else "None",
        "lines": 0,
        "query": queries[0] if queries else stem
    }

def normalize_comp(name):
    n = unicodedata.normalize("NFC", name)
    n = re.sub(r'\.(mp3|m4a|wav|flac|ogg)$', '', n, flags=re.IGNORECASE)
    n = re.sub(r'\[.*?\]', '', n)
    n = re.sub(r'\(.*?\)', '', n)
    n = re.sub(r'[\|｜⧸/\\_\-\'\"]', ' ', n)
    for w in ['official', 'music', 'video', 'mv', 'audio', 'lyrics', 'lyric', 'cover', 'remix', 'lofi', 'prod', 'by', 'the album']:
        n = re.sub(rf'\b{w}\b', '', n, flags=re.IGNORECASE)
    return re.sub(r'\s+', '', n).lower()

def main():
    os.makedirs(LYRICS_DIR, exist_ok=True)
    os.makedirs(LRC_DIR, exist_ok=True)
    os.makedirs(TXT_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    audio_files = [
        f for f in os.listdir(MUSIC_DIR)
        if os.path.isfile(os.path.join(MUSIC_DIR, f)) and f.lower().endswith(SUPPORTED_EXTS)
    ]
    audio_files.sort()
    
    total = len(audio_files)
    log_msg("=" * 65)
    log_msg(f"  TIẾN TRÌNH ĐỒNG BỘ LỜI BÀI HÁT CHUẨN TỪ INTERNET (BẢN NÂNG CẤP)")
    log_msg(f"  Tổng số bài hát: {total}")
    log_msg(f"  Số luồng xử lý: {NUM_WORKERS}")
    log_msg(f"  Thư mục sao lưu: {BACKUP_DIR}")
    log_msg("=" * 65)

    start_time = time.time()
    results = {}
    
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_to_song = {executor.submit(process_song, af): af for af in audio_files}
        
        for idx, future in enumerate(as_completed(future_to_song), 1):
            res = future.result()
            results[res["file"]] = res
            
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 1
            rem_songs = total - idx
            rem_sec = rem_songs / rate
            m, s = divmod(int(rem_sec), 60)
            eta_str = f"{m:02d}m{s:02d}s"
            
            status = res["status"]
            if status == "SUCCESS":
                log_msg(f"[{idx:03d}/{total}][ETA: {eta_str}] OK ({res['lines']:2d} lines) [{res['source']:<10}] -> {res['file'][:42]}")
            elif status == "KEEP_EXISTING":
                log_msg(f"[{idx:03d}/{total}][ETA: {eta_str}] GIỮ BẢN CŨ -> {res['file'][:42]}")
            else:
                log_msg(f"[{idx:03d}/{total}][ETA: {eta_str}] CHƯA CÓ LỜI -> {res['file'][:42]}")

    # Phase 2: Duplicate / Variant matching
    log_msg("\n>>> Tiến hành liên kết và đồng bộ các bài hát trùng lặp / biến thể...")
    propagated_count = 0
    success_files = [f for f, r in results.items() if r["status"] == "SUCCESS"]
    
    for af, r in results.items():
        if r["status"] != "SUCCESS":
            k_norm = normalize_comp(af)
            matched_success = None
            for sf in success_files:
                s_norm = normalize_comp(sf)
                if len(k_norm) >= 5 and (k_norm in s_norm or s_norm in k_norm):
                    matched_success = sf
                    break
            if matched_success:
                src_stem = os.path.splitext(matched_success)[0]
                dst_stem = os.path.splitext(af)[0]
                src_lrc = os.path.join(LRC_DIR, f"{src_stem}.lrc")
                src_txt = os.path.join(TXT_DIR, f"{src_stem}.txt")
                dst_lrc = os.path.join(LRC_DIR, f"{dst_stem}.lrc")
                dst_txt = os.path.join(TXT_DIR, f"{dst_stem}.txt")
                if os.path.exists(src_lrc):
                    # update [ti:dst_stem]
                    with open(src_lrc, "r", encoding="utf-8") as f_in:
                        c_lrc = f_in.read()
                    c_lrc = re.sub(r'^\[ti:[^\]]*\]', f'[ti:{dst_stem}]', c_lrc)
                    with open(dst_lrc, "w", encoding="utf-8") as f_out:
                        f_out.write(c_lrc)
                    with open(src_txt, "r", encoding="utf-8") as f_in:
                        c_txt = f_in.read()
                    c_txt = re.sub(r'^=== LỜI BÀI HÁT: [^\n]* ===', f'=== LỜI BÀI HÁT: {dst_stem} ===', c_txt)
                    with open(dst_txt, "w", encoding="utf-8") as f_out:
                        f_out.write(c_txt)
                    results[af]["status"] = "SUCCESS"
                    results[af]["source"] = f"Linked({matched_success[:20]})"
                    results[af]["lines"] = results[matched_success]["lines"]
                    propagated_count += 1
                    log_msg(f"  [ĐỒNG BỘ TRÙNG] {af[:35]} <== {matched_success[:35]}")

    total_time = time.time() - start_time
    m, s = divmod(int(total_time), 60)
    
    success_count = sum(1 for r in results.values() if r["status"] == "SUCCESS")
    kept_count = sum(1 for r in results.values() if r["status"] == "KEEP_EXISTING")
    not_found_count = sum(1 for r in results.values() if r["status"] == "NOT_FOUND")

    report_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "updated_from_web": success_count,
        "propagated_duplicates": propagated_count,
        "kept_existing": kept_count,
        "not_found": not_found_count,
        "elapsed_seconds": round(total_time, 2),
        "details": list(results.values())
    }
    import json
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    log_msg("\n" + "=" * 65)
    log_msg("  HOÀN THÀNH TIẾN TRÌNH ĐỒNG BỘ!")
    log_msg(f"  - Cập nhật chuẩn từ mạng (ZingMP3/NetEase/YouTube): {success_count} bài ({success_count/total*100:.1f}%)")
    log_msg(f"  - Giữ lại bản cũ (không có trên mạng):               {kept_count} bài")
    log_msg(f"  - Chưa có lyric:                                    {not_found_count} bài")
    log_msg(f"  - Tổng thời gian:                                   {m:02d}m {s:02d}s")
    log_msg(f"  - Chi tiết lưu tại: {REPORT_FILE}")
    log_msg("=" * 65)

if __name__ == "__main__":
    main()
