import os, re, json, glob, subprocess, tempfile, datetime
import httpx
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from google import genai
from google.genai import types

app = FastAPI()
ai = genai.Client()                                   # GEMINI_API_KEY
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
TMDB = os.getenv("TMDB_API_KEY")                      # optional posters
ACCESS_CODE = os.getenv("ACCESS_CODE", "")            # testers must know this
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "150"))    # total clips per day
used = {}

PROMPT = """You identify movies, TV shows and music videos from a short clip.
You get the clip's audio and frames from it. Use dialogue, music, lyrics, voices,
settings, on-screen text and language.
Reply with JSON only, no other text:
{"guesses":[{"title":"","year":"","type":"movie|show|music video","confidence":0-100,"reason":"one short sentence"}]}
For a music video, title is "Song - Artist". Give up to 3 guesses, best first.
If you are not sure, give a LOW confidence. Never invent a title."""

def run(*args):
    return subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True)

async def poster(title):
    if not TMDB: return None
    try:
        async with httpx.AsyncClient(timeout=8) as h:
            r = await h.get("https://api.themoviedb.org/3/search/multi",
                            params={"api_key": TMDB, "query": title})
        for x in r.json().get("results", []):
            if x.get("poster_path"):
                return "https://image.tmdb.org/t/p/w185" + x["poster_path"]
    except Exception:
        pass
    return None

@app.get("/")
def home():
    return FileResponse("index.html")

@app.post("/identify")
async def identify(video: UploadFile = File(...), code: str = Form("")):
    if ACCESS_CODE and code.strip() != ACCESS_CODE:
        return {"error": "Wrong access code. Ask Adama for the code."}
    today = datetime.date.today().isoformat()
    if used.get(today, 0) >= DAILY_LIMIT:
        return {"error": "Daily limit reached. Please try again tomorrow."}
    data = await video.read()
    if len(data) > 80_000_000:
        return {"error": "Video is too large. Trim it to about 30 seconds."}
    used[today] = used.get(today, 0) + 1
    with tempfile.TemporaryDirectory() as d:
        src = f"{d}/in"
        open(src, "wb").write(data)
        run("-i", src, "-t", "30", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", f"{d}/a.mp3")
        run("-i", src, "-t", "30", "-vf", "fps=1/5,scale=640:-1", f"{d}/f%02d.jpg")
        parts = [types.Part.from_bytes(data=open(p, "rb").read(), mime_type="image/jpeg")
                 for p in sorted(glob.glob(f"{d}/f*.jpg"))[:6]]
        if os.path.exists(f"{d}/a.mp3"):
            parts.append(types.Part.from_bytes(data=open(f"{d}/a.mp3", "rb").read(), mime_type="audio/mp3"))
        if not parts:
            return {"error": "Could not read this video. Try another clip."}
        parts.append(PROMPT)
        try:
            raw = ai.models.generate_content(model=MODEL, contents=parts).text
            guesses = json.loads(re.search(r"\{.*\}", raw, re.S).group())["guesses"]
        except Exception as e:
            return {"error": f"The AI step failed: {str(e)[:160]}"}
        for g in guesses:
            g["poster"] = await poster(g.get("title", ""))
        return {"guesses": guesses}

@app.post("/feedback")
async def feedback(wrong: str = Form(""), correct: str = Form("")):
    print("FEEDBACK", json.dumps({"wrong": wrong, "correct": correct}))   # visible in Space logs
    return {"ok": True}
