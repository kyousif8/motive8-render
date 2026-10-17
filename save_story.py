"""Remember long story videos for the weekly YouTube compilation.

Runs after a render. If the finished video is longer than 45 seconds (a story), uploads a small
sidecar file m8-N.json (title + file name) to the "stories" release. The video itself stays in the
"renders" release. Never fails the workflow.
"""
import base64, glob, json, os, subprocess, sys


def main(job_path):
    raw = open(job_path, encoding="utf-8").read().strip().split("~")
    out_name = raw[14]
    path = os.path.join("out", out_name)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip() or 0)
    if dur < 45:
        print(f"{out_name}: {dur:.1f}s, not a story; skipping.")
        return
    title = base64.b64decode(raw[17]).decode("utf-8") if len(raw) > 17 and raw[17] else out_name
    side = out_name.rsplit(".", 1)[0] + ".json"
    json.dump({"file": out_name, "title": title.strip(), "duration": round(dur, 2)},
              open(side, "w", encoding="utf-8"), ensure_ascii=False)
    subprocess.run("gh release view stories >/dev/null 2>&1 || gh release create stories "
                   "--title stories --notes 'Story videos queued for the weekly compilation'",
                   shell=True, check=True)
    subprocess.run(["gh", "release", "upload", "stories", side, "--clobber"], check=True)
    print(f"Saved {side} for the weekly compilation ({dur:.1f}s): {title}")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "job.txt")
    except Exception as e:
        print("save_story error (render unaffected):", e)
