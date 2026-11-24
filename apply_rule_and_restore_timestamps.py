import os
import sys
import re
import json
import time
from difflib import SequenceMatcher

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

MUSIC_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(MUSIC_DIR, "lyrics_backup_whisper")
LYRICS_DIR = os.path.join(MUSIC_DIR, "lyrics")
LRC_DIR = os.path.join(LYRICS_DIR, ".lrc")
TXT_DIR = os.path.join(LYRICS_DIR, ".txt")
REPORT_FILE = os.path.join(MUSIC_DIR, "lyrics_sync_report.json")
LOG_FILE = os.path.join(LYRICS_DIR, "timestamp_restore.log")

def log(msg):
    print(msg, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def clean_word(w):
    return re.sub(r'[^\w\s]', '', w).lower()

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
                m = re.match(r'\[(\d{2}:\d{2}\.\d{2})\]\s*(.*)', l.strip())
                if m:
                    sec = parse_time_token(m.group(1))
                    lines.append((sec, m.group(2).strip()))
    return lines

def parse_official_clean(path):
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            rec = False
            for l in f:
                s = l.strip()
                if '--- LỜI THUẦN TÚY ---' in s:
                    rec = True
                    continue
                elif '--- LỜI KÈM MỐC THỜI GIAN ---' in s:
                    break
                if rec and s:
                    lines.append(s)
    return lines

def process_song(stem):
    # Don't touch Simple Love since it's already manually perfected
    if stem == "Simple Love":
        return "PRESERVED_MANUAL_PERFECTION"

    b_txt = os.path.join(BACKUP_DIR, f"{stem}.txt")
    b_lrc = os.path.join(BACKUP_DIR, f"{stem}.lrc")
    c_txt = os.path.join(TXT_DIR, f"{stem}.txt")
    c_lrc = os.path.join(LRC_DIR, f"{stem}.lrc")
    
    if not os.path.exists(b_txt) and not os.path.exists(b_lrc):
        return "NO_BACKUP_AVAILABLE"
        
    segs = parse_txt_timed(b_txt)
    if not segs:
        lrc_timed = parse_lrc(b_lrc)
        for i in range(len(lrc_timed)):
            s, txt = lrc_timed[i]
            e = lrc_timed[i+1][0] if i + 1 < len(lrc_timed) else s + 4.0
            segs.append((s, e, txt))
            
    off_clean = parse_official_clean(c_txt)
    off_timed = parse_txt_timed(c_txt)
    if not off_timed:
        lrc_timed = parse_lrc(c_lrc)
        for i in range(len(lrc_timed)):
            s, txt = lrc_timed[i]
            e = lrc_timed[i+1][0] if i + 1 < len(lrc_timed) else s + 4.0
            off_timed.append((s, e, txt))

    if not off_clean:
        return "NO_OFFICIAL_CLEAN"

    final_timed = []
    mode_str = ""

    # CASE A: Adequate backup segments (>= 15 segments)
    if len(segs) >= 15:
        whisper_tokens = []
        seg_indices = []
        for s_idx, (start, end, text) in enumerate(segs):
            for w in text.split():
                whisper_tokens.append(clean_word(w))
                seg_indices.append(s_idx)
                
        off_tokens = []
        for line in off_clean:
            for w in line.split():
                off_tokens.append((clean_word(w), w))
                
        sm = SequenceMatcher(None, whisper_tokens, [w[0] for w in off_tokens])
        blocks = sm.get_matching_blocks()
        
        off_to_seg = [None] * len(off_tokens)
        for b in blocks:
            for k in range(b.size):
                w_idx = b.a + k
                o_idx = b.b + k
                if w_idx < len(seg_indices) and o_idx < len(off_to_seg):
                    off_to_seg[o_idx] = seg_indices[w_idx]
                    
        cur = 0
        for i in range(len(off_to_seg)):
            if off_to_seg[i] is not None:
                cur = off_to_seg[i]
            else:
                off_to_seg[i] = cur
                
        seg_words = {i: [] for i in range(len(segs))}
        for o_idx, s_idx in enumerate(off_to_seg):
            seg_words[s_idx].append(off_tokens[o_idx][1])
            
        for i in range(len(segs)):
            s, e, orig_w = segs[i]
            words = seg_words[i]
            text = " ".join(words).strip() if words else orig_w
            final_timed.append((s, e, text))
            
        mode_str = f"EXACT_BACKUP_TIMESTAMPS ({len(final_timed)} lines)"
        
    # CASE B: Truncated backup (< 15 segments) -> Calculate Offset
    else:
        if not off_timed:
            return "NO_OFFICIAL_TIMED"
            
        best_offset = 0.0
        best_score = 0.0
        
        for b_s, b_e, b_txt_str in segs:
            b_words = [clean_word(w) for w in b_txt_str.split() if len(clean_word(w)) > 2]
            if not b_words: continue
            for o_s, o_e, o_txt_str in off_timed:
                o_words = [clean_word(w) for w in o_txt_str.split() if len(clean_word(w)) > 2]
                overlap = sum(1 for w in b_words if w in o_words)
                score = overlap / max(len(b_words), len(o_words), 1)
                if score > best_score and score >= 0.3:
                    best_score = score
                    best_offset = b_s - o_s
                    
        if best_score < 0.3 and segs and off_timed:
            best_offset = segs[0][0] - off_timed[0][0]
            
        for s, e, text in off_timed:
            new_s = max(0.0, round(s + best_offset, 2))
            new_e = max(new_s + 0.5, round(e + best_offset, 2))
            final_timed.append((new_s, new_e, text))
            
        mode_str = f"OFFSET_SHIFT ({best_offset:+.2f}s, {len(final_timed)} lines)"

    # Write files
    if final_timed:
        # LRC
        lrc_lines = [f"[ti:{stem}]"]
        for s, e, txt in final_timed:
            ts_str = format_lrc_ts(s)
            lrc_lines.append(f"[{ts_str}]{txt}")
        lrc_content = "\n".join(lrc_lines) + "\n"
        
        # TXT
        txt_lines = [f"=== LỜI BÀI HÁT: {stem} ===", "", "--- LỜI THUẦN TÚY ---"]
        for _, _, txt in final_timed:
            txt_lines.append(txt)
        txt_lines.append("")
        txt_lines.append("--- LỜI KÈM MỐC THỜI GIAN ---")
        for s, e, txt in final_timed:
            txt_lines.append(f"[{s:.2f}s -> {e:.2f}s] {txt}")
        txt_lines.append("")
        txt_content = "\n".join(txt_lines)
        
        with open(c_lrc, "w", encoding="utf-8") as f:
            f.write(lrc_content)
        with open(c_txt, "w", encoding="utf-8") as f:
            f.write(txt_content)
            
        return mode_str

    return "NO_OUTPUT"

def main():
    audio_files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac'))]
    unique_stems = sorted(list({os.path.splitext(f)[0] for f in audio_files}))
    
    log(f"Starting timestamp restoration across {len(unique_stems)} songs...")
    stats = {
        "EXACT_BACKUP_TIMESTAMPS": 0,
        "OFFSET_SHIFT": 0,
        "PRESERVED_MANUAL_PERFECTION": 0,
        "NO_BACKUP_AVAILABLE": 0,
        "OTHER": 0
    }
    
    for idx, stem in enumerate(unique_stems, 1):
        res = process_song(stem)
        prefix = res.split()[0]
        if prefix in stats:
            stats[prefix] += 1
        else:
            stats["OTHER"] += 1
            
        if idx % 20 == 0 or idx == len(unique_stems):
            log(f"[{idx:3d}/{len(unique_stems)}] Processed -> {stem[:35]:<35} ({res})")

    log("\n=== COMPLETED TIMESTAMP RESTORATION ===")
    for k, v in stats.items():
        log(f"  {k}: {v}")
        
    report = {
        "total_audio_files": len(audio_files),
        "total_unique_songs": len(unique_stems),
        "exact_backup_timestamps_preserved": stats["EXACT_BACKUP_TIMESTAMPS"] + stats["PRESERVED_MANUAL_PERFECTION"],
        "offset_shifted_truncated_backups": stats["OFFSET_SHIFT"],
        "no_backup_online_only": stats["NO_BACKUP_AVAILABLE"],
        "rule_added": True,
        "rule_file": ".agents/rules/lyrics_editing_rules.md",
        "status": "COMPLETED_AND_RULE_ENFORCED"
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log(f"Report updated at {REPORT_FILE}")

if __name__ == "__main__":
    main()
