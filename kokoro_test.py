"""Voice test: re-render a Make job with the free Kokoro voice instead of Deepgram (nothing is posted)."""
import os, sys, numpy as np, soundfile as sf
sys.path.insert(0, os.getcwd())
import render2
from kokoro import KPipeline


def main(job_path, voices):
    job = render2.load_job(job_path)
    text = " ".join(c.strip() for c in job["captions"])
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    for v in voices:
        parts = []
        for r in pipe(text, voice=v, speed=1.05):
            a = r.audio if hasattr(r, "audio") else r[2]
            a = a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)
            parts.append(a)
        wav = os.path.abspath(f"voice_{v}.wav")
        sf.write(wav, np.concatenate(parts), 24000)
        raw = open(job_path, encoding="utf-8").read().strip().split("~")
        raw[13] = "file://" + wav
        raw[14] = f"voice-test-kokoro-{v}.mp4"
        open("job_k.txt", "w", encoding="utf-8").write("~".join(raw))
        render2.main("job_k.txt")


if __name__ == "__main__":
    main(sys.argv[1], [v.strip() for v in sys.argv[2].split(",") if v.strip()])
