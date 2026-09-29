"""Motive8 renderer: replaces Creatomate.

Input: job.json with
  captions      list of 6 strings
  durations     list of 6 numbers (seconds, used as weights)
  broll_urls    list of 6 direct video URLs (Pexels)
  audio_url     direct URL of the voiceover (mp3/wav)
  out_name      output file name, e.g. "Comfort is the enemy of growth.mp4"
Output: out/<out_name>, 1080x1920, 30fps, H.264 + AAC.
"""
import base64, json, os, subprocess, sys, textwrap, urllib.request

W, H, FPS = 1080, 1920, 30
FONT = os.environ.get("M8_FONT", "fonts/Poppins-Bold.ttf")
HANDLE = "@motive8ers"


def sh(cmd):
    subprocess.run(cmd, check=True)


def fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())


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
    return job


def main(job_path):
    job = load_job(job_path)
    os.makedirs("work", exist_ok=True)
    os.makedirs("out", exist_ok=True)
    caps = job["captions"]
    n = len(caps)

    fetch(job["audio_url"], "work/voice")
    audio_len = duration("work/voice")

    # Stretch caption timings so the video ends with the voiceover (+0.4s tail).
    weights = [float(d) for d in job["durations"]]
    total = audio_len + 0.4
    segs = [w / sum(weights) * total for w in weights]

    parts = []
    for i in range(n):
        src = f"work/broll{i}"
        fetch(job["broll_urls"][i], src)
        txt = f"work/cap{i}.txt"
        open(txt, "w", encoding="utf-8").write("\n".join(textwrap.wrap(caps[i].strip(), 28)))
        vf = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
            f"drawtext=fontfile={FONT}:textfile={txt}:fontsize=66:fontcolor=white:line_spacing=14:"
            f"text_align=center:x=(w-text_w)/2:y=h*0.66:borderw=5:bordercolor=black@0.85:"
            f"shadowx=0:shadowy=4:shadowcolor=black@0.5,"
            f"drawtext=fontfile={FONT}:text='{HANDLE}':fontsize=34:fontcolor=white@0.55:"
            f"x=(w-text_w)/2:y=h*0.90,"
            f"fade=t=in:st=0:d=0.25"
        )
        part = f"work/part{i}.mp4"
        sh(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", src, "-t", f"{segs[i]:.3f}",
            "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", part])
        parts.append(part)

    with open("work/list.txt", "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")

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
        # Voice on top, music quietly underneath with a fade out at the end.
        af = (f"[2:a]volume=0.12,afade=t=out:st={max(total - 2, 0):.2f}:d=2[m];"
              f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt",
            "-i", "work/voice", "-stream_loop", "-1", "-i", "work/music", "-filter_complex", af,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", out])
    else:
        sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt",
            "-i", "work/voice", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    print("rendered", out, round(duration(out), 2), "s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "job.json")
