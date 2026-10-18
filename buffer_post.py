"""Queue freshly rendered Motive8 videos on TikTok through Buffer, without dropping any.

Reads the same compact job file as render2.py. Field 14 = out_name, field 16 = base64 YouTube
description (used to build the TikTok caption). Buffer allows 10 scheduled posts per channel, so:
  - every new render is first saved to a waiting line (small m8-N.json files in the "backlog" release);
  - then the free queue spots are filled from the waiting line, oldest video first;
  - a video leaves the waiting line only after Buffer accepted it.
So when the queue is full, a render simply waits for the next free spot instead of being skipped.
Never fails the workflow.
"""
import base64, json, os, subprocess, sys, urllib.request

ORG = "5f3f4ddd70aa7f1d5d2e84b5"
TIKTOK = "6811d9d33dcfffe9e75efaa5"
REPO_RELEASE = "https://github.com/kyousif8/motive8-render/releases/download/renders/"
QUEUE_LIMIT = 10


def gql(key, query, variables):
    req = urllib.request.Request(
        "https://api.buffer.com",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def tiktok_caption(desc):
    c = desc.replace(" Tell me in the comments.", "\n\n👇 Comment below.")
    c = c.replace("🔔 Subscribe to Motive8 for a new mindset Short every day.",
                  "🔖 Save this for the day you need it.\n\n🚀 Follow @motive8ers for daily motivation and mindset.")
    c = c.replace("#Shorts", "#fyp #Motive8")
    return c.replace("*", "")


BACKLOG = "backlog"
BACKLOG_URL = "https://github.com/kyousif8/motive8-render/releases/download/backlog/"


def gh(*args, check=True):
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def num(name):
    try:
        return int(name.split("-")[1].split(".")[0])
    except Exception:
        return 10 ** 9


def save_to_backlog(out_name, caption):
    side = out_name.rsplit(".", 1)[0] + ".json"
    json.dump({"file": out_name, "caption": caption}, open(side, "w", encoding="utf-8"), ensure_ascii=False)
    if gh("release", "view", BACKLOG, check=False).returncode != 0:
        gh("release", "create", BACKLOG, "--title", BACKLOG, "--notes", "Renders waiting for a free TikTok queue spot")
    gh("release", "upload", BACKLOG, side, "--clobber")
    print(f"{out_name} added to the waiting line.")


def backlog_items():
    assets = json.loads(gh("release", "view", BACKLOG, "--json", "assets").stdout)["assets"]
    names = sorted((a["name"] for a in assets if a["name"].endswith(".json")), key=num)
    items = []
    for n in names:
        req = urllib.request.Request(BACKLOG_URL + n, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            items.append((n, json.load(r)))
    return items


def queue_count(key):
    q = """query($org: OrganizationId!, $ch: [ChannelId!]) {
      posts(first: 50, input: {organizationId: $org, filter: {channelIds: $ch, status: [scheduled]}}) { edges { node { id } } } }"""
    res = gql(key, q, {"org": ORG, "ch": [TIKTOK]})
    if "errors" in res:
        raise RuntimeError(f"Queue check failed: {res['errors']}")
    return len(res["data"]["posts"]["edges"] or [])


def post(key, out_name, caption):
    m = """mutation($input: CreatePostInput!) { createPost(input: $input) {
      __typename ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
    inp = {"channelId": TIKTOK, "mode": "addToQueue", "schedulingType": "automatic", "text": caption,
           "assets": [{"video": {"url": REPO_RELEASE + out_name, "metadata": {"title": out_name}}}]}
    res = gql(key, m, {"input": inp})
    print(f"Buffer response for {out_name}:", json.dumps(res)[:300])
    return res.get("data", {}).get("createPost", {}).get("__typename") == "PostActionSuccess"


def main(path):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        print("No BUFFER_API_KEY secret; skipping TikTok queue.")
        return
    p = open(path, encoding="utf-8").read().strip().split("~")
    if len(p) >= 17 and p[16]:
        save_to_backlog(p[14], tiktok_caption(base64.b64decode(p[16]).decode("utf-8")))
    else:
        print("Job has no caption field; nothing new to add.")

    room = QUEUE_LIMIT - queue_count(key)
    waiting = backlog_items()
    print(f"TikTok queue has {room} free spot(s); {len(waiting)} video(s) waiting.")
    for name, item in waiting[:max(room, 0)]:
        if post(key, item["file"], item["caption"]):
            gh("release", "delete-asset", BACKLOG, name, "-y", check=False)
        else:
            print(f"{item['file']} stays in the waiting line.")
            break


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "job.txt")
    except Exception as e:
        print("TikTok queue step error (render unaffected):", e)
