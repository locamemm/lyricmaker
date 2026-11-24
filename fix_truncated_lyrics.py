import os
import sys
import re
import json
import time
import unicodedata
import mutagen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

MUSIC_DIR = r"g:\Users\ACER\Music"
LYRICS_DIR = os.path.join(MUSIC_DIR, "lyrics")
LRC_DIR = os.path.join(LYRICS_DIR, ".lrc")
TXT_DIR = os.path.join(LYRICS_DIR, ".txt")
BACKUP_DIR = os.path.join(MUSIC_DIR, "lyrics_backup_whisper")

sys.path.append(MUSIC_DIR)
from sync_lyrics_from_web import (
    extract_search_queries, fetch_zing_lrc, fetch_netease_lrc, clean_noise
)

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

def parse_lrc(path):
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for l in f:
                m = re.match(r'\[(\d{1,2}:\d{2}\.?\d*)\]\s*(.*)', l.strip())
                if m:
                    sec = parse_time_token(m.group(1))
                    text = m.group(2).strip()
                    if text:
                        lines.append((sec, text))
    return lines

def parse_lrc_string(content):
    lines = []
    for l in content.splitlines():
        m = re.match(r'\[(\d{1,2}:\d{2}\.?\d*)\]\s*(.*)', l.strip())
        if m:
            sec = parse_time_token(m.group(1))
            text = m.group(2).strip()
            if text:
                lines.append((sec, text))
    return lines

def get_audio_vocal_start(stem):
    b_lrc = os.path.join(BACKUP_DIR, f"{stem}.lrc")
    b_lines = parse_lrc(b_lrc)
    if b_lines:
        return b_lines[0][0]
    c_lrc = os.path.join(LRC_DIR, f"{stem}.lrc")
    c_lines = parse_lrc(c_lrc)
    if c_lines:
        return c_lines[0][0]
    return 0.0

def write_lyric_files(stem, timed_lines):
    lrc_path = os.path.join(LRC_DIR, f"{stem}.lrc")
    txt_path = os.path.join(TXT_DIR, f"{stem}.txt")
    
    # 1. Write LRC
    lrc_content = [f"[ti:{stem}]"]
    for s, _, text in timed_lines:
        lrc_content.append(f"[{format_lrc_ts(s)}]{text}")
    with open(lrc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lrc_content) + "\n")
        
    # 2. Write TXT
    txt_content = [f"=== LỜI BÀI HÁT: {stem} ===", "", "--- LỜI THUẦN TÚY ---"]
    for _, _, text in timed_lines:
        txt_content.append(text)
    txt_content.append("")
    txt_content.append("--- LỜI KÈM MỐC THỜI GIAN ---")
    for s, e, text in timed_lines:
        txt_content.append(f"[{s:.2f}s -> {e:.2f}s] {text}")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_content) + "\n")

def process_single_song(item):
    stem = item["stem"]
    audio_filename = item["audio"]
    audio_path = os.path.join(MUSIC_DIR, audio_filename)
    dur = item["dur"]
    
    if stem == "Simple Love":
        return stem, "SKIPPED_PERFECTED", 0
        
    # Special duplicate handling
    if "BIGCITYBOI" in stem and "TOULIVER x BINZ" in stem:
        source_lrc = os.path.join(LRC_DIR, "Bigcityboi (feat. Touliver).lrc")
        if os.path.exists(source_lrc):
            lines = parse_lrc(source_lrc)
            if lines:
                timed = []
                for i in range(len(lines)):
                    s, t = lines[i]
                    e = lines[i+1][0] if i + 1 < len(lines) else s + 3.5
                    timed.append((s, e, t))
                write_lyric_files(stem, timed)
                return stem, f"COPIED_FROM_FEAT ({len(timed)} lines)", len(timed)

    queries = extract_search_queries(audio_path)
    # Add tailored query variations
    clean_stem = re.sub(r'[\'\"＂”‟“”\[\]\(\)]', '', stem).strip()
    if clean_stem not in queries:
        queries.append(clean_stem)
    parts = clean_stem.split('｜')
    if len(parts) > 1 and parts[0].strip() not in queries:
        queries.append(parts[0].strip())
        
    # Try fetching online LRC
    online_lrc_str = None
    used_source = None
    
    for q in queries[:6]:
        content = fetch_zing_lrc(q)
        if content:
            parsed = parse_lrc_string(content)
            if len(parsed) >= 15:
                online_lrc_str = content
                used_source = f"Zing({q})"
                break
                
    if not online_lrc_str:
        for q in queries[:4]:
            content = fetch_netease_lrc(q)
            if content:
                parsed = parse_lrc_string(content)
                if len(parsed) >= 15:
                    online_lrc_str = content
                    used_source = f"NetEase({q})"
                    break
                    
    if not online_lrc_str:
        return stem, "NOT_FOUND_ONLINE", 0
        
    parsed = parse_lrc_string(online_lrc_str)
    if not parsed:
        return stem, "PARSE_FAILED", 0
        
    audio_vocal_start = get_audio_vocal_start(stem)
    online_vocal_start = parsed[0][0]
    
    # Calculate offset
    # If the MV duration is much longer than song (e.g. MV audio has talking at start),
    # audio_vocal_start reflects the user's audio file!
    offset = audio_vocal_start - online_vocal_start
    
    # If audio_vocal_start is 0 or very small (< 2s) while online is normal (> 5s),
    # trust online intro if backup was truncated before vocals or use audio intro
    if audio_vocal_start <= 1.0 and online_vocal_start > 5.0:
        offset = 0.0

    timed_lines = []
    for i in range(len(parsed)):
        s, text = parsed[i]
        new_s = max(0.0, round(s + offset, 2))
        next_s = parsed[i+1][0] if i + 1 < len(parsed) else s + 4.0
        new_e = max(new_s + 0.5, round(next_s + offset, 2))
        timed_lines.append((new_s, new_e, text))
        
    write_lyric_files(stem, timed_lines)
    return stem, f"SUCCESS ({used_source}, offset={offset:+.2f}s, {len(timed_lines)} lines, last={timed_lines[-1][0]:.1f}s)", len(timed_lines)

if __name__ == "__main__":
    with open(os.path.join(MUSIC_DIR, "currently_truncated_in_lyrics.json"), encoding="utf-8") as f:
        songs = json.load(f)
        
    print(f"Starting fix for {len(songs)} truncated songs...")
    success_count = 0
    failed = []
    
    for idx, item in enumerate(songs, 1):
        stem, msg, cnt = process_single_song(item)
        if "SUCCESS" in msg or "COPIED" in msg:
            success_count += 1
            print(f"[{idx:2d}/{len(songs)}] OK: {stem[:40]:<40} -> {msg}")
        elif "SKIPPED" in msg:
            print(f"[{idx:2d}/{len(songs)}] SKIP: {stem[:40]:<40} -> {msg}")
        else:
            failed.append((item, msg))
            print(f"[{idx:2d}/{len(songs)}] FAIL: {stem[:40]:<40} -> {msg}")
            
    print(f"\nCompleted: {success_count} fixed, {len(failed)} remaining.")
    with open(os.path.join(MUSIC_DIR, "remaining_to_whisper.json"), "w", encoding="utf-8") as f:
        json.dump([item for item, _ in failed], f, ensure_ascii=False, indent=2)
