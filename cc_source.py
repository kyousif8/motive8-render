"""Find and fetch Creative Commons long-form videos for Motive8 clipping.

Two modes (env MODE):
  scout    - QUERIES (one search per line). Searches YouTube with the Creative Commons filter, reads full
             metadata for each hit, keeps only CC-licensed videos of 15+ minutes, and publishes
             scout-<date>.json to the "sources" release. A scheduled Claude task scores these against
             the Channel Growth Playbook (Stage 1) and picks which ones to clip.
  download - URL (one YouTube URL). Re-checks the licence (refuses anything not Creative Commons),
             downloads up to 720p mp4, and publishes src-<id>.mp4 + src-<id>.json to the "sources"
             release, where Descript can import it by direct link.
"""
import datetime as dt, json, os, subprocess, sys, urllib.parse

REPO = "kyousif8/motive8-render"
SOURCES = f"https://github.com/{REPO}/releases/download/sources/"
CC_FILTER = "EgIwAQ%3D%3D"  # YouTube search filter: Creative Commons
MIN_MINUTES = 15
PER_QUERY = 12


def ytdlp(*args):
    r = subprocess.run(["yt-dlp", "--no-warnings", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
    return r


def meta(url):
    r = ytdlp("-J", "--skip-download", url)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def is_cc(m):
    return "creative commons" in (m.get("license") or "").lower()


def summary(m, query=""):
    return {
        "id": m["id"],
        "url": f"https://www.youtube.com/watch?v={m['id']}",
        "title": m.get("title"),
        "channel": m.get("channel") or m.get("uploader"),
        "channel_id": m.get("channel_id"),
        "license": m.get("license"),
        "minutes": round((m.get("duration") or 0) / 60, 1),
        "views": m.get("view_count"),
        "likes": m.get("like_count"),
        "comments": m.get("comment_count"),
        "upload_date": m.get("upload_date"),
        "description": (m.get("description") or "")[:400],
        "query": query,
    }


def publish(*paths):
    subprocess.run("gh release view sources >/dev/null 2>&1 || gh release create sources --title sources "
                   "--notes 'Creative Commons source videos for Motive8 clipping'", shell=True, check=True)
    subprocess.run(["gh", "release", "upload", "sources", *paths, "--clobber"], check=True)


def scout(queries):
    seen, found, errors = set(), [], []
    for q in queries:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}&sp={CC_FILTER}"
        r = ytdlp("--flat-playlist", "-J", "--playlist-end", str(PER_QUERY), url)
        if r.returncode != 0:
            errors.append({"query": q, "error": r.stderr[-300:]})
            continue
        for e in (json.loads(r.stdout).get("entries") or []):
            vid = e.get("id")
            if not vid or vid in seen or (e.get("duration") or 0) < MIN_MINUTES * 60:
                continue
            seen.add(vid)
            m = meta(f"https://www.youtube.com/watch?v={vid}")
            if m and is_cc(m):
                found.append(summary(m, q))
            elif not m:
                errors.append({"id": vid, "error": "metadata unavailable"})
    name = f"scout-{dt.date.today().isoformat()}.json"
    json.dump({"date": dt.date.today().isoformat(), "queries": queries, "videos": found, "errors": errors},
              open(name, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    publish(name)
    print(f"{len(found)} CC videos -> {SOURCES}{name}; {len(errors)} errors")
    if not found and errors:
        sys.exit("No videos found and searches failed (YouTube may be blocking this runner).")


def download(url):
    m = meta(url)
    if not m:
        sys.exit("Could not read video metadata (YouTube may be blocking this runner).")
    if not is_cc(m):
        sys.exit(f"Refused: licence is '{m.get('license')}', not Creative Commons.")
    out = f"src-{m['id']}.mp4"
    r = ytdlp("-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[height<=720]",
              "--merge-output-format", "mp4", "-o", out, url)
    if r.returncode != 0 or not os.path.exists(out):
        sys.exit("Download failed.")
    side = f"src-{m['id']}.json"
    json.dump(summary(m), open(side, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    publish(out, side)
    print(f"Ready for Descript: {SOURCES}{out}")


if __name__ == "__main__":
    mode = os.environ.get("MODE", "scout")
    if mode == "download":
        download(os.environ["URL"].strip())
    else:
        qs = [q.strip() for q in os.environ.get("QUERIES", "").replace(";", "\n").splitlines() if q.strip()]
        scout(qs or ["motivational speech", "les brown speech", "motivational interview podcast"])
