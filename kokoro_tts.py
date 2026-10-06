"""Motive8 voice: generate the voiceover with the free Kokoro voice (am_michael).

Rewrites field 13 of the compact job (audio URL) to the local Kokoro wav.
If anything fails, the job is left unchanged and the Deepgram audio from Make is used.
"""
import os, sys, numpy as np, soundfile as sf
sys.path.insert(0, os.getcwd())
import render2
from kokoro import KPipeline

VOICE = os.environ.get("M8_VOICE", "am_michael")


def main(job_path):
    job = render2.load_job(job_path)
    text = " ".join(c.strip() for c in job["captions"])
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    parts = []
    for r in pipe(text, voice=VOICE, speed=1.05):
        a = r.audio if hasattr(r, "audio") else r[2]
        a = a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)
        parts.append(a)
    wav = os.path.abspath("voice_kokoro.wav")
    sf.write(wav, np.concatenate(parts), 24000)
    raw = open(job_path, encoding="utf-8").read().strip().split("~")
    raw[13] = "file://" + wav
    open(job_path, "w", encoding="utf-8").write("~".join(raw))
    print("kokoro voice ready:", VOICE, wav)


if __name__ == "__main__":
    main(sys.argv[1])
