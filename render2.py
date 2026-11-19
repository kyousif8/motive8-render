"""Motive8 renderer: replaces Creatomate.

Input: job.json with
  captions      list of 6 strings
  durations     list of 6 numbers (seconds, used as weights)
  broll_urls    list of 6 direct video URLs (Pexels)
  audio_url     direct URL of the voiceover (mp3/wav)
  out_name      output file name, e.g. "Comfort is the enemy of growth.mp4"
Output: out/<out_name>, 1080x1920, 30fps, H.264 + AAC.
"""
import base64, json, os, subprocess, sys, textwrap, time, urllib.parse, urllib.request

import modesty

W, H, FPS = 1080, 1920, 30
FONT = os.environ.get("M8_FONT", "fonts/Poppins-Bold.ttf")
HANDLE = "@motive8ers"
MIN_LEN = 61.5   # seconds; no Motive8 video under 61 s
SHOT_LEN = 2.0   # target seconds per shot (retention)


def note(msg):
    """Show a failure in the run's annotations (the API can read these, the raw logs are not always reachable)."""
    msg = " ".join(str(msg).split())[-900:]
    print("::error::" + msg.replace("%", "%25"))


def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr or "")
        note("exit %d: %s | %s" % (p.returncode, " ".join(cmd)[:120], (p.stderr or "")[-600:]))
        raise subprocess.CalledProcessError(p.returncode, cmd, p.stdout, p.stderr)


def fetch(url, path, tries=3):
    """Download with retries; raises if the URL is empty or keeps failing."""
    if not url or not url.startswith(("http://", "https://", "file://")):
        raise ValueError("empty or invalid url: %r" % url)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
                f.write(r.read())
            return
        except Exception as e:
            last = e
            print("download failed (attempt %d): %s" % (attempt + 1, e))
            time.sleep(3)
    raise last


def pexels_candidates(query, per_page=15):
    """Search Pexels for portrait videos; yield (page_url, file_link) best-first."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        raise ValueError("no PEXELS_API_KEY to search for %r" % query)
    q = urllib.parse.urlencode({"query": query, "orientation": "portrait", "per_page": per_page})
    req = urllib.request.Request("https://api.pexels.com/videos/search?" + q,
                                 headers={"Authorization": key, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        videos = json.load(r).get("videos") or []
    for v in videos:
        files = [f for f in v.get("video_files", []) if f.get("link") and (f.get("width") or 0) >= 540]
        if not files:
            continue
        files.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
        tags = " ".join(t if isinstance(t, str) else str(t.get("name", "")) for t in (v.get("tags") or []))
        yield (v.get("url") or "") + " " + tags, files[0]["link"]


T_START = time.time()
USED = set()  # clip links already used in this video, so the same shot is not repeated


def clean_clip(entry, path):
    """Download a modest clip for this entry into path (see modesty.py). Raises if none passes."""
    entry = (entry or "").strip()
    if not entry:
        raise ValueError("empty b-roll entry")
    if modesty.detector() is None:
        queries = list(modesty.SAFE_SCENERY)  # cannot check people, so use scenery only
    elif entry.startswith(("http://", "https://", "file://")):
        bad = modesty.blocked_text(entry)
        if bad:
            raise ValueError("blocked word %r in %s" % (bad, entry))
        fetch(entry, path)
        why = modesty.check_clip(path, entry)
        if why:
            raise ValueError("clip rejected (%s): %s" % (why, entry))
        return
    else:
        bad = modesty.blocked_text(entry)
        if bad:
            print("search phrase %r has blocked word %r; using scenery" % (entry, bad))
            queries = []
        elif modesty.couple_scene(entry):
            print("search phrase %r is a couple scene; using symbolic shots instead" % entry)
            queries = list(modesty.COUPLE_SUBSTITUTES)
        else:
            queries = [entry]
        queries += modesty.SAFE_SCENERY
    for query in queries:
        try:
            cands = list(pexels_candidates(query))
        except Exception as e:
            print("search failed for %r: %s" % (query, e))
            continue
        for page, link in cands:
            if link in USED:
                continue
            bad = modesty.blocked_text(page) or (modesty.romantic(page) and "romance")
            if bad:
                print("  skip %s (word %r)" % (page.split()[0] if page else link, bad))
                continue
            try:
                fetch(link, path)
            except Exception as e:
                print("  download failed:", e)
                continue
            why = modesty.check_clip(path, query + " " + page)
            if why:
                print("  reject %s (%s)" % (page.split()[0] if page else link, why))
                continue
            USED.add(link)
            print("  ok %r -> %s" % (query, page.split()[0] if page else link))
            return
    raise ValueError("no modest clip found for %r" % entry)


def get_broll(urls, i, path):
    """Use clip i; if no modest clip is found for it, fall back to the other entries in order.

    Each entry is either a direct video URL or a search phrase looked up on Pexels."""
    for j in [i] + [k for k in range(len(urls)) if k != i]:
        try:
            clean_clip(urls[j], path)
            if j != i:
                print("b-roll %d unavailable, used entry %d" % (i, j))
            return
        except Exception as e:
            print("b-roll %d skipped: %s" % (j, e))
    raise SystemExit("no usable b-roll at all")


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip())


DEFAULT_MUSIC = "https://cdn.creatomate.com/demo/music3.mp3"


def load_job(path):
    raw = open(path, encoding="utf-8").read().strip()
    if raw.startswith("{"):
        return json.loads(raw)
    # Compact format sent by Make (no JSON escaping needed):
    # 6 base64 captions ~ 6 comma-separated durations ~ 6 b-roll URLs ~ audio URL ~ out_name [~ music URL]
    p = raw.split("~")
    job = {
        "captions": [base64.b64decode(x).decode("utf-8") for x in p[0:6]],
        "durations": [float(x or 4) for x in p[6].split(",")],
        "broll_urls": p[7:13],
        "audio_url": p[13],
        "out_name": p[14],
    }
    if len(p) > 15 and p[15]:
        job["music_url"] = p[15]
    if len(p) > 17 and p[17]:
        job["title"] = base64.b64decode(p[17]).decode("utf-8")
    return job


def chunks(text, n=3):
    words = text.split()
    return [" ".join(words[k:k + n]) for k in range(0, len(words), n)] or [""]


def text_width(text, size):
    """Pixel width of text in FONT at this size (Pillow if available, else a safe estimate)."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(FONT, size).getlength(text)
    except Exception:
        return len(text) * size * 0.62


def fit_size(text, size, max_w=W * 0.88, min_size=54):
    """Largest font size <= size so the caption fits inside the frame (never cut off at the edges)."""
    while size > min_size and text_width(text, size) > max_w:
        size -= 2
    return size


def hook_line(caption, limit=70):
    """The first sentence of Caption_1 (the twist hook), short enough to read in about two seconds.

    Retention: viewers decide in the first 1-2 s, so the on-screen hook is one short sentence, not two.
    Falls back to the first ~9 words when the first sentence is too long; "" if there is no caption."""
    import re
    text = (caption or "").strip()
    if not text:
        return ""
    first = re.findall(r"[^.!?]+[.!?]+[\"')\u201d]*|[^.!?]+$", text)[0].strip()
    if len(first) <= limit:
        return first
    words = first.split()
    hook = ""
    for w in words:
        cand = (hook + " " + w).strip()
        if len(cand) > limit - 3:
            break
        hook = cand
    return hook.rstrip(",;:") + "..."


def wfile(path, text):
    open(path, "w", encoding="utf-8").write(text)
    return path


def text_pass(filters):
    """Burn the text onto work/visual.mp4 -> work/texted.mp4, surviving a bad ffmpeg build or a bad visual track.

    Attempt 1: as written. Attempt 2: re-encode the visual track first (fixes odd concat-copy output).
    Attempt 3: simpler drawtext options (no text_align / box / shadow). Every failure prints ffmpeg's real error."""
    def run(src, script):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_script:v", script,
               "-c:v", "libx264", "-preset", "medium", "-crf", "27", "-maxrate", "1100k", "-bufsize", "2200k",
               "-pix_fmt", "yuv420p", "work/texted.mp4"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            print("text pass failed (exit %d) on %s with %s\n%s" % (p.returncode, src, script, (p.stderr or "")[-1500:]))
            v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[:1]
            print("ffmpeg:", v)
            try:
                has_dt = " drawtext " in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
                fsz = os.path.getsize(FONT) if os.path.exists(FONT) else -1
                fhead = open(FONT, "rb").read(4).hex() if fsz > 0 else ""
                which = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True).stdout.strip()
            except Exception as e:  # diagnostics must never hide the real error
                has_dt, fsz, fhead, which = "?", "?", "?", "?"
            note("text pass failed (exit %d) src=%s script=%s | ffmpeg=%s %s drawtext_listed=%s font=%s size=%s head=%s | %s"
                 % (p.returncode, src, script, which, v, has_dt, FONT, fsz, fhead, (p.stderr or "")[-500:]))
        return p.returncode == 0

    if run("work/visual.mp4", "work/vf.txt"):
        return
    sh(["ffmpeg", "-y", "-v", "error", "-i", "work/visual.mp4", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "work/visual_fix.mp4"])
    if run("work/visual_fix.mp4", "work/vf.txt"):
        return
    import re
    simple = []
    for f in filters:
        f = re.sub(r":(text_align|box|boxcolor|boxborderw|shadowx|shadowy|shadowcolor|line_spacing)=[^:]+", "", f)
        simple.append(f)
    wfile("work/vf_simple.txt", ",".join(simple))
    if run("work/visual_fix.mp4", "work/vf_simple.txt"):
        return
    raise SystemExit("ffmpeg text pass failed three ways; see errors above")


def main(job_path):
    global T_START
    T_START = time.time()
    job = load_job(job_path)
    os.makedirs("work", exist_ok=True)
    os.makedirs("out", exist_ok=True)
    caps = job["captions"]
    n = len(caps)

    fetch(job["audio_url"], "work/voice")
    audio_len = duration("work/voice")
    weights = [float(d) for d in job["durations"]]
    speech = audio_len + 0.4
    segs = [w / sum(weights) * speech for w in weights]
    # Every Motive8 video must run over 61 s (TikTok Creator Rewards only pays for videos over 1 minute).
    # Scripts are written long enough; if one still comes out short, an end card fills the gap.
    total = max(speech, MIN_LEN)
    tail = total - speech
    if tail > 0:
        print("speech is %.1f s; adding a %.1f s end card to reach %.1f s" % (speech, tail, total))

    # 1) Visual track. Retention: a new shot about every 2 s (the first one after ~1.2 s), alternating between
    #    two different clips per caption segment, with a slow pan or push-in that changes direction each shot.
    parts, shot = [], 0

    def cut(src, start, d, n):
        clip_len = 0
        try:
            clip_len = duration(src)
        except Exception:
            pass
        if clip_len and start + d > clip_len - 0.1:
            start = max(clip_len - d - 0.1, 0)
        if n % 3 == 2:  # push-in zoom
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"scale=w='2*trunc({W}*(1+0.12*t/{d:.3f})/2)':h=-2:eval=frame,"
                  f"crop={W}:{H}:x='(iw-ow)/2':y='(ih-oh)/2',fps={FPS},setsar=1")
        else:
            pan = "(iw-ow)*t/%.3f" % d if n % 2 == 0 else "(iw-ow)*(1-t/%.3f)" % d
            vf = (f"scale={int(W*1.12)}:{int(H*1.12)}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H}:x='{pan}':y='(ih-oh)/2',fps={FPS},setsar=1")
        part = f"work/part{n:03d}.mp4"
        try:
            sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", src,
                "-t", f"{d:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", part])
        except subprocess.CalledProcessError:  # e.g. an odd clip size for the zoom: plain centre crop
            sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", src,
                "-t", f"{d:.3f}", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"fps={FPS},setsar=1", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", part])
        return part

    for i in range(n):
        srcs = [f"work/broll{i}"]
        get_broll(job["broll_urls"], i, srcs[0])
        try:  # a second, different clip for the same idea, so a long segment is not one shot repeated
            if time.time() - T_START > 420:
                raise RuntimeError("time budget used, keeping one clip")
            clean_clip(job["broll_urls"][i], f"work/broll{i}b")
            srcs.append(f"work/broll{i}b")
        except Exception as e:
            print("second clip for segment %d skipped: %s" % (i, e))
        seg = segs[i] + (tail if i == n - 1 else 0)
        k_shots = max(2, round(seg / SHOT_LEN))
        lens = [seg / k_shots] * k_shots
        if i == 0 and k_shots >= 2:  # quick first cut: something changes on screen within ~1.2 s
            first = min(1.2, lens[0])
            lens = [first] + [(seg - first) / (k_shots - 1)] * (k_shots - 1)
        offs = {}
        for j, d in enumerate(lens):
            src = srcs[j % len(srcs)]
            start = offs.get(src, 0.0)
            parts.append(cut(src, start, d, shot))
            offs[src] = start + d + 0.3
            shot += 1
    with open("work/list.txt", "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt", "-c", "copy", "work/visual.mp4"])

    # 2) Text: the hook (opening line of Caption_1) big at the top while it is spoken - or the title card for
    #    2.2 s when there is no hook - then captions in 3-word chunks timed across each segment, then the handle.
    filters, t0, k = [], 0.0, 0
    hook = hook_line(caps[0]) if caps else ""
    hook_end = 0.0
    top = hook or (job.get("title") or "").strip()
    if top:
        if hook:
            share = len(hook) / max(len(caps[0].strip()), 1)
            hook_end = min(max(segs[0] * share + 0.3, 2.2), 6.0)
            show_until = hook_end
        else:
            show_until = 2.2
        tlines = textwrap.wrap(top, 18)
        tfs = min(fit_size(line, 84) for line in tlines)
        tf = wfile("work/title.txt", "\n".join(tlines))
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={tf}:fontsize={tfs}:fontcolor=white:line_spacing=12:text_align=center:"
            f"x=(w-text_w)/2:y=h*0.20:box=1:boxcolor=black@0.6:boxborderw=28:enable='lt(t,{show_until:.3f})'")
    for i in range(n):
        cs = chunks(caps[i].strip())
        weights_c = [max(len(c), 4) for c in cs]
        tt = t0
        for c, wc in zip(cs, weights_c):
            d = segs[i] * wc / sum(weights_c)
            if tt + d <= hook_end:
                tt += d
                continue
            fs = fit_size(c, 86)
            if text_width(c, fs) > W * 0.88:  # still too wide at the smallest size: split onto two lines
                c = "\n".join(textwrap.wrap(c, max(len(c) // 2 + 1, 8)))
            cf = wfile(f"work/c{k:03d}.txt", c)
            filters.append(
                f"drawtext=fontfile={FONT}:textfile={cf}:fontsize={fs}:fontcolor=white:borderw=7:bordercolor=black@0.9:"
                f"line_spacing=10:text_align=center:"
                f"shadowx=0:shadowy=5:shadowcolor=black@0.5:x=(w-text_w)/2:y=h*0.64:enable='between(t,{tt:.3f},{tt + d:.3f})'")
            tt += d
            k += 1
        t0 += segs[i]
    if tail > 0.5:  # end card for the padded seconds (only when the script ran short)
        ef = wfile("work/endcard.txt", "Follow " + HANDLE + "\nfor tomorrow's story")
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={ef}:fontsize=72:fontcolor=white:line_spacing=14:text_align=center:"
            f"x=(w-text_w)/2:y=h*0.42:box=1:boxcolor=black@0.6:boxborderw=30:enable='gte(t,{speech:.3f})'")
    filters.append(f"drawtext=fontfile={FONT}:text='{HANDLE}':fontsize=34:fontcolor=white@0.55:x=(w-text_w)/2:y=h*0.90")
    wfile("work/vf.txt", ",".join(filters))
    text_pass(filters)

    # 3) Audio: voice from frame one, music underneath.
    out = os.path.join("out", job["out_name"])
    music = job.get("music_url", DEFAULT_MUSIC)
    have_music = False
    if music:
        try:
            fetch(music, "work/music")
            have_music = True
        except Exception as e:
            print("music skipped:", e)
    if have_music:
        af = (f"[2:a]volume=0.12,afade=t=out:st={max(total - 2, 0):.2f}:d=2[m];"
              f"[1:a]apad=whole_dur={total:.3f}[v];[v][m]amix=inputs=2:duration=first:normalize=0[a]")
        sh(["ffmpeg", "-y", "-v", "error", "-i", "work/texted.mp4", "-i", "work/voice", "-stream_loop", "-1",
            "-i", "work/music", "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-t", f"{total:.3f}", "-movflags", "+faststart", out])
    else:
        sh(["ffmpeg", "-y", "-v", "error", "-i", "work/texted.mp4", "-i", "work/voice", "-map", "0:v", "-map", "1:a",
            "-af", f"apad=whole_dur={total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", out])
    print("rendered", out, round(duration(out), 2), "s")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "job.json")
    except subprocess.CalledProcessError:
        raise  # already annotated by sh()
    except BaseException as e:
        if not isinstance(e, SystemExit) or e.code not in (0, None):
            note("%s: %s" % (type(e).__name__, e))
        raise
