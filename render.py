"""Motive8 renderer: replaces Creatomate.

Input: job.json with
  captions      list of 6 strings
  durations     list of 6 numbers (seconds, used as weights)
  broll_urls    list of 6 direct video URLs (Pexels)
  audio_url     direct URL of the voiceover (mp3/wav)
  out_name      output file name, e.g. "Comfort is the enemy of growth.mp4"
Output: out/<out_name>, 1080x1920, 30fps, H.264 + AAC.
"""
import json, os, subprocess, sys, textwrap, urllib.request

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


def main(job_path):
    job = json.load(open(job_path))
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
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt",
        "-i", "work/voice", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
        "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    print("rendered", out, round(duration(out), 2), "s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "job.json")
