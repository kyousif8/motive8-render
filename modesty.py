"""Footage rule for Motive8: modest clips only.

Every b-roll clip is checked before it can go into a video:
  1. Words in the Pexels page address (its title) - swimwear, underwear, kissing, same-sex couples, etc.
  2. A nudity/body-exposure detector (NudeNet) run on frames across the clip: bras, bikinis, bare
     midriffs, cleavage, bare shoulders/armpits, short shorts and anything more are rejected.
  3. No romantic couple footage at all: a search phrase about a couple/marriage/wedding is swapped for
     symbolic shots (rings, two coffee cups, a home interior...), and any stock clip whose title mentions
     a couple, love, kissing, hugging or holding hands is skipped. Stock sites cannot be trusted to show
     only a man and a woman, so the safe rule is to show none. As an extra check, a clip that shows two
     people of one gender alone in an affection scene is rejected.
If the detector cannot load, only no-people scenery is allowed, so nothing unchecked gets through.
"""
import glob, os, re, shutil, subprocess, tempfile

BLOCK_WORDS = [
    # body exposure
    "bikini", "lingerie", "bra", "bralette", "underwear", "panties", "swimsuit", "swimwear", "swimming",
    "swimmer", "swim", "pool", "topless", "shirtless", "naked", "nude", "nudity", "sexy", "sensual",
    "seductive", "erotic", "sauna", "shower", "bathtub", "bath", "bathing", "twerk", "twerking",
    "pole", "belly-dance", "cleavage", "crop-top", "sports-bra", "beach-body", "body-positive",
    "massage", "spa", "tan", "tanning", "sunbathing", "sunbathe", "boxers", "briefs", "leggings",
    "yoga-pants", "miniskirt", "short-shorts", "hot-pants", "stripper", "club", "nightclub",
    "party-girl", "bed-sheets", "wet", "drunk", "alcohol", "wine", "beer", "cocktail", "champagne",
    "kiss", "kissing", "kisses", "making-out", "flirt", "flirting", "intimate", "intimacy", "sensuality",
    # same-sex couples / LGBT content
    "gay", "lesbian", "lesbians", "lgbt", "lgbtq", "lgbtqia", "queer", "pride", "same-sex",
    "homosexual", "bisexual", "transgender", "trans", "drag", "rainbow-flag", "nonbinary", "non-binary",
]
# Couple scenes: the clip must clearly show a man AND a woman, and never two of one gender alone.
COUPLE_WORDS = ["couple", "couples", "lovers", "romance", "romantic", "marriage", "married", "wedding", "bride",
                "groom", "husband", "wife", "spouse", "boyfriend", "girlfriend", "honeymoon", "anniversary",
                "valentine", "dating", "engaged", "proposal", "newlyweds", "fiance", "fiancee"]
# Affection scenes: never two people of one gender alone.
ROMANCE_WORDS = COUPLE_WORDS + ["love", "partner", "partners", "date", "hug", "hugging", "embrace", "cuddle",
                                "cuddling", "relationship", "holding hands"]
COUPLE_SUBSTITUTES = ["wedding rings close up", "two coffee cups on table", "cozy living room interior",
                      "handwritten letter on table", "family dinner table food", "sunset through window",
                      "house keys in hand", "flowers in vase"]
SAFE_SCENERY = ["sunrise over mountains", "ocean waves aerial", "forest path sunlight", "city skyline night",
                "clouds timelapse sky", "empty road landscape", "desert dunes", "waterfall nature",
                "rain on window", "mountain lake calm"]

# Detector class -> lowest score that rejects the clip (strict on purpose; a rejected clean clip just
# means the next search result is used).
LIMITS = {
    "FEMALE_BREAST_COVERED": 0.25, "FEMALE_BREAST_EXPOSED": 0.25, "FEMALE_GENITALIA_COVERED": 0.25,
    "FEMALE_GENITALIA_EXPOSED": 0.2, "MALE_GENITALIA_EXPOSED": 0.45, "BUTTOCKS_COVERED": 0.35,
    "BUTTOCKS_EXPOSED": 0.35, "ANUS_EXPOSED": 0.2, "ANUS_COVERED": 0.35, "BELLY_EXPOSED": 0.3,
    "MALE_BREAST_EXPOSED": 0.3, "ARMPITS_EXPOSED": 0.3,
}
FRAMES = 10

_detector = None
_detector_failed = False


def detector():
    global _detector, _detector_failed
    if _detector is None and not _detector_failed:
        try:
            from nudenet import NudeDetector
            _detector = NudeDetector()
        except Exception as e:
            print("modesty: detector unavailable, scenery-only mode:", e)
            _detector_failed = True
    return _detector


def words(text):
    text = (text or "").lower()
    toks = set(re.findall(r"[a-z]+", text))
    hy = re.sub(r"[^a-z]+", "-", text)
    return toks, "-" + hy + "-"


def blocked_text(text):
    toks, hy = words(text)
    for w in BLOCK_WORDS:
        if ("-" in w and "-" + w + "-" in hy) or w in toks:
            return w
    return None


def romantic(text):
    toks, hy = words(text)
    return any(w in toks or "-" + w.replace(" ", "-") + "-" in hy for w in ROMANCE_WORDS)


def couple_scene(text):
    toks, _ = words(text)
    return any(w in toks for w in COUPLE_WORDS)


def frames(video, n=FRAMES):
    tmp = tempfile.mkdtemp(prefix="m8chk")
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "csv=p=0", video], capture_output=True, text=True).stdout.strip() or 0)
    except ValueError:
        dur = 0
    if dur <= 0:
        return tmp, []
    for k in range(n):
        t = dur * (k + 0.5) / n
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", video, "-frames:v", "1",
                        "-vf", "scale=480:-2", os.path.join(tmp, "f%02d.jpg" % k)])
    return tmp, sorted(glob.glob(os.path.join(tmp, "*.jpg")))


def check_clip(video, context=""):
    """Return None if the clip is acceptable, else a short reason."""
    det = detector()
    if det is None:
        return "no detector"
    romance = romantic(context)
    couple = couple_scene(context)
    any_f = any_m = False
    tmp, imgs = frames(video)
    try:
        if not imgs:
            return "unreadable clip"
        for img in imgs:
            found = det.detect(img)
            for d in found:
                lim = LIMITS.get(d["class"])
                if lim is not None and d["score"] >= lim:
                    return "%s %.2f" % (d["class"], d["score"])
            fem = sum(1 for d in found if d["class"] == "FACE_FEMALE" and d["score"] >= 0.3)
            mal = sum(1 for d in found if d["class"] == "FACE_MALE" and d["score"] >= 0.3)
            any_f, any_m = any_f or fem > 0, any_m or mal > 0
            if romance and ((fem >= 2 and mal == 0) or (mal >= 2 and fem == 0)):
                return "same-gender pair in a romance scene"
        if couple and not (any_f and any_m):
            return "couple scene without a clearly visible man and woman"
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
