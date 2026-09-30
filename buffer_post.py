"""Queue a freshly rendered Motive8 video on TikTok through Buffer.

Reads the same compact job file as render2.py. Field 14 = out_name, field 16 = base64 YouTube
description (used to build the TikTok caption). Adds the video to the TikTok queue only if the
queue has room (Buffer plan limit: 10 scheduled posts). Never fails the workflow.
"""
import base64, json, os, sys, urllib.request

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


def main(path):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        print("No BUFFER_API_KEY secret; skipping TikTok queue.")
        return
    p = open(path, encoding="utf-8").read().strip().split("~")
    if len(p) < 17 or not p[16]:
        print("Job has no caption field; skipping TikTok queue.")
        return
    out_name, caption = p[14], tiktok_caption(base64.b64decode(p[16]).decode("utf-8"))

    q = """query($org: OrganizationId!, $ch: [ChannelId!]) {
      posts(first: 50, input: {organizationId: $org, filter: {channelIds: $ch, status: [scheduled]}}) { edges { node { id } } } }"""
    res = gql(key, q, {"org": ORG, "ch": [TIKTOK]})
    if "errors" in res:
        print("Queue check failed:", res["errors"]); return
    queued = len(res["data"]["posts"]["edges"] or [])
    if queued >= QUEUE_LIMIT:
        print(f"TikTok queue full ({queued}); {out_name} stays on GitHub as stock.")
        return

    m = """mutation($input: CreatePostInput!) { createPost(input: $input) {
      __typename ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
    inp = {"channelId": TIKTOK, "mode": "addToQueue", "schedulingType": "automatic", "text": caption,
           "assets": [{"video": {"url": REPO_RELEASE + out_name, "metadata": {"title": out_name}}}]}
    res = gql(key, m, {"input": inp})
    print("Buffer response:", json.dumps(res)[:500])


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "job.txt")
    except Exception as e:
        print("TikTok queue step error (render unaffected):", e)
