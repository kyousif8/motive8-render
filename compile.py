"""Weekly long-form YouTube compilation of Motive8 story videos.

Picks the story videos saved in the last 7 days (sidecars in the "stories" release), or a manifest
passed in by hand, turns each vertical story into a 16:9 1080p clip (sharp video centred on a
blurred copy of itself), joins them with a short end card, writes YouTube chapters, publishes the
file to the "compilations" release and queues it on the YouTube channel through Buffer.
"""
import datetime as dt, json, os, subprocess, sys, urllib.request

REPO = "kyousif8/motive8-render"
RENDERS = f"https://github.com/{REPO}/releases/download/renders/"
STORIES = f"https://github.com/{REPO}/releases/download/stories/"
COMPS = f"https://github.com/{REPO}/releases/download/compilations/"
ORG = "5f3f4ddd70aa7f1d5d2e84b5"
YOUTUBE = "68af8a783d2fbc20d49c276b"
FONT = "fonts/Poppins-Bold.ttf"
MIN_STORIES = 3


def sh(cmd, **kw):
    print("+", cmd if isinstance(cmd, str) else " ".join(cmd)[:200])
    return subprocess.run(cmd, check=True, **kw)


def out(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, shell=isinstance(cmd, str)).stdout


def fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        f.write(r.read())


def duration(p):
    return float(out(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())


def pick_stories(manifest):
    if manifest:
        items = json.loads(manifest)
        return [{"file": f"m8-{i['n']}.mp4", "title": i["title"]} for i in items]
    assets = json.loads(out("gh release view stories --json assets"))["assets"]
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    picked = []
    for a in assets:
        if not a["name"].endswith(".json"):
            continue
        created = dt.datetime.fromisoformat(a["createdAt"].replace("Z", "+00:00"))
        if created >= since:
            fetch(STORIES + a["name"], "side.json")
            picked.append(json.load(open("side.json", encoding="utf-8")))
    picked.sort(key=lambda s: int(s["file"].split("-")[1].split(".")[0]))
    return picked


def ts(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def gql(key, query, variables):
    req = urllib.request.Request("https://api.buffer.com",
                                 data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    manifest = os.environ.get("MANIFEST", "").strip()
    post = os.environ.get("POST_TO_YOUTUBE", "true").strip().lower() == "true"
    stories = pick_stories(manifest)
    print(f"{len(stories)} stories:", [s["title"] for s in stories])
    if len(stories) < MIN_STORIES:
        print(f"Fewer than {MIN_STORIES} stories this week; no compilation.")
        return

    os.makedirs("work", exist_ok=True)
    os.makedirs("out", exist_ok=True)
    vf = ("[0:v]split[a][b];"
          "[a]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=28:2,eq=brightness=-0.12[bg];"
          "[b]scale=-2:1080[fg];[bg][fg]overlay=(W-w)/2:0,fps=30,format=yuv420p[v]")
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "1400k", "-bufsize", "2800k",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
    parts, chapters, t = [], [], 0.0
    for i, s in enumerate(stories):
        src = f"work/src{i}.mp4"
        fetch(RENDERS + s["file"], src)
        dst = f"work/part{i}.mp4"
        sh(["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex", vf, "-map", "[v]", "-map", "0:a",
            *enc, dst])
        chapters.append((t, s["title"]))
        t += duration(dst)
        parts.append(dst)

    # End card: invite viewers to subscribe.
    open("work/end1.txt", "w").write("A new story with a moral")
    open("work/end2.txt", "w").write("every day on Motive8. Subscribe.")
    end_vf = (f"drawtext=fontfile={FONT}:textfile=work/end1.txt:fontsize=72:fontcolor=white:x=(w-text_w)/2:y=h*0.40,"
              f"drawtext=fontfile={FONT}:textfile=work/end2.txt:fontsize=72:fontcolor=white:x=(w-text_w)/2:y=h*0.52,"
              "format=yuv420p")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x111111:s=1920x1080:r=30:d=6",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "6", "-vf", end_vf, *enc, "work/end.mp4"])
    parts.append("work/end.mp4")

    with open("work/list.txt", "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    today = dt.date.today().isoformat()
    name = f"motive8-stories-{today}.mp4"
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "work/list.txt", "-c", "copy",
        "-movflags", "+faststart", f"out/{name}"])
    size = os.path.getsize(f"out/{name}") / 1e6
    total = duration(f"out/{name}")
    print(f"Compilation: {name}, {total / 60:.1f} min, {size:.0f} MB")

    sh("gh release view compilations >/dev/null 2>&1 || gh release create compilations "
       "--title compilations --notes 'Weekly long-form story compilations'", shell=True)
    vol = len([a for a in json.loads(out("gh release view compilations --json assets"))["assets"]
               if a["name"].endswith(".mp4")]) + 1
    sh(["gh", "release", "upload", "compilations", f"out/{name}", "--clobber"])

    n = len(stories)
    title = f"{n} Short Stories With a Moral That Will Change How You See Life | Vol. {vol}"
    lines = [f"{n} short stories with a powerful lesson at the end of each one. Put it on, slow down, and listen.", "",
             "Chapters:"] + [f"{ts(c[0])} {c[1]}" for c in chapters] + [
             f"{ts(t)} One last thing", "",
             "💭 Which story hit you the hardest? Tell me in the comments.", "",
             "🔔 Subscribe to Motive8 for a new story with a moral every day.", "",
             "#MoralStories #Motivation #LifeLessons #Mindset #Motive8"]
    desc = "\n".join(lines)
    print(title); print(desc)
    open(f"out/{name}.txt", "w", encoding="utf-8").write(title + "\n\n" + desc)

    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not post or not key:
        print("Not posting to YouTube (POST_TO_YOUTUBE=false or no BUFFER_API_KEY).")
        return
    m = """mutation($input: CreatePostInput!) { createPost(input: $input) {
      __typename ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
    inp = {"channelId": YOUTUBE, "mode": "addToQueue", "schedulingType": "automatic", "text": desc,
           "assets": [{"video": {"url": COMPS + name, "metadata": {"title": title}}}],
           "metadata": {"youtube": {"title": title[:100], "categoryId": "22", "privacy": "public",
                                    "madeForKids": False, "notifySubscribers": True}}}
    res = gql(key, m, {"input": inp})
    print("Buffer response:", json.dumps(res)[:800])
    if "errors" in res or res.get("data", {}).get("createPost", {}).get("__typename") != "PostActionSuccess":
        sys.exit("Buffer did not accept the compilation post.")


if __name__ == "__main__":
    main()
