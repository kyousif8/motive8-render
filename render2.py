"""Motive8 renderer: replaces Creatomate.

Input: job.json with
  captions      list of 6 strings
  durations     list of 6 numbers (seconds, used as weights)
  broll_urls    list of 6 direct video URLs (Pexels)
  audio_url     direct URL of the voiceover (mp3/wav)
  out_name      output file name, e.g. "Comfort is the enemy of growth.mp4"
Output: out/<out_name>, 1080x1920, 30fps, H.264 + AAC.
"""
import base64, json, os, subprocess, sys, textwrap, time, urllib.request

W, H, FPS = 1080, 1920, 30
FONT = os.environ.get("M8_FONT", "fonts/Poppins-Bold.ttf")
HANDLE = "@motive8ers"


def sh(cmd):
    subprocess.run(cmd, check=True)


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


def get_broll(urls, i, path):
    """Use clip i; if it is missing or broken, fall back to the other clips in order."""
    for j in [i] + [k for k in range(len(urls)) if k != i]:
        try:
            fetch(urls[j], path)
            if j != i:
                print("b-roll %d unavailable, reused clip %d" % (i, j))
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


def wfile(path, text):
    open(path, "w", encoding="utf-8").write(text)
    return path


def main(job_path):
    job = load_job(job_path)
    os.makedirs("work", exist_ok=True)
    os.makedirs("out", exist_ok=True)
    caps = job["captions"]
    n = len(caps)

    fetch(job["audio_url"], "work/voice")
    audio_len = duration("work/voice")
    weights = [float(d) for d in job["durations"]]
    total = audio_len + 0.4
    segs = [w / sum(weights) * total for w in weights]

    # 1) Visual track: each caption segment = 2 shots (~1.5-2.5 s) with a slow pan, alternating direction.
    parts, shot = [], 0
    for i in range(n):
        src = f"work/broll{i}"
        get_broll(job["broll_urls"], i, src)
        try:
            clip_len = duration(src)
        except Exception:
            clip_len = 0
        halves = [segs[i] / 2, segs[i] - segs[i] / 2]
        for h, d in enumerate(halves):
            start = 0 if h == 0 or clip_len < d * 2 + 0.5 else min(clip_len / 2, max(clip_len - d - 0.1, 0))
            pan = "(iw-ow)*t/%.3f" % d if shot % 2 == 0 else "(iw-ow)*(1-t/%.3f)" % d
            vf = (f"scale={int(W*1.12)}:{int(H*1.12)}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H}:x='{pan}':y='(ih-oh)/2',fps={FPS},setsar=1")
            part = f"work/part{shot:02d}.mp4"
            sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", src,
                "-t", f"{d:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", part])
            parts.append(part)
            shot += 1
    with open("work/list.txt", "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt", "-c", "copy", "work/visual.mp4"])

    # 2) Text: hook title for the first 2.2 s, captions in 3-word chunks timed across each segment, handle.
    filters, t0, k = [], 0.0, 0
    title = (job.get("title") or "").strip()
    if title:
        tf = wfile("work/title.txt", "\n".join(textwrap.wrap(title, 18)))
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={tf}:fontsize=78:fontcolor=white:line_spacing=12:text_align=center:"
            f"x=(w-text_w)/2:y=h*0.20:box=1:boxcolor=black@0.55:boxborderw=28:enable='lt(t,2.2)'")
    for i in range(n):
        cs = chunks(caps[i].strip())
        weights_c = [max(len(c), 4) for c in cs]
        tt = t0
        for c, wc in zip(cs, weights_c):
            d = segs[i] * wc / sum(weights_c)
            cf = wfile(f"work/c{k:03d}.txt", c)
            filters.append(
                f"drawtext=fontfile={FONT}:textfile={cf}:fontsize=86:fontcolor=white:borderw=7:bordercolor=black@0.9:"
                f"shadowx=0:shadowy=5:shadowcolor=black@0.5:x=(w-text_w)/2:y=h*0.64:enable='between(t,{tt:.3f},{tt + d:.3f})'")
            tt += d
            k += 1
        t0 += segs[i]
    filters.append(f"drawtext=fontfile={FONT}:text='{HANDLE}':fontsize=34:fontcolor=white@0.55:x=(w-text_w)/2:y=h*0.90")
    wfile("work/vf.txt", ",".join(filters))
    sh(["ffmpeg", "-y", "-v", "error", "-i", "work/visual.mp4", "-filter_script:v", "work/vf.txt",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2500k", "-bufsize", "5000k",
        "-pix_fmt", "yuv420p", "work/texted.mp4"])

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
              f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        sh(["ffmpeg", "-y", "-v", "error", "-i", "work/texted.mp4", "-i", "work/voice", "-stream_loop", "-1",
            "-i", "work/music", "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", out])
    else:
        sh(["ffmpeg", "-y", "-v", "error", "-i", "work/texted.mp4", "-i", "work/voice", "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    print("rendered", out, round(duration(out), 2), "s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "job.json")
