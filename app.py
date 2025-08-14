from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import google.generativeai as genai
from tempfile import NamedTemporaryFile
from dotenv import load_dotenv
from fastapi import UploadFile, File, FastAPI
import google.generativeai as genai
from pydantic import BaseModel
from pathlib import Path
import assemblyai as aai
import requests
import os


# Load API key from .env file
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")



# Allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup template rendering
templates = Jinja2Templates(directory="templates")

# Root route
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# Optional base model if using JSON body in some endpoints
class TTSRequest(BaseModel):
    text: str
    voiceId: str


@app.get("/voices")
def get_voices():
    """Fetch available voices from Murf API"""
    url = "https://api.murf.ai/v1/speech/voices"
    headers = {
        "api-key": MURF_API_KEY
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch voices: {str(e)}")


@app.post("/generate-voice")
def generate_voice(payload: TTSRequest):
    """Generate voice using provided text and voiceId (for testing from Postman or Swagger)"""
    data = {
        "text": payload.text,
        "voiceId": payload.voiceId
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": MURF_API_KEY
    }

    url = "https://api.murf.ai/v1/speech/generate"

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tts")
async def generate_tts(request: Request):
    """Frontend-accessible endpoint to generate TTS and return audio URL"""
    body = await request.json()
    text = body.get("text")
    voice_id = body.get("voiceId", "en-IN-alia")  # Default voice

    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    headers = {
        "Content-Type": "application/json",
        "api-key": MURF_API_KEY
    }

    data = {
        "text": text,
        "voiceId": "en-IN-alia"
    }

    try:
        response = requests.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=data)
        response.raise_for_status()

        murf_data = response.json()
        audio_url = murf_data.get("audioFile")

        if not audio_url:
            raise HTTPException(status_code=500, detail="No audio URL returned by Murf")

        return JSONResponse(content={"url": audio_url})

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error calling Murf API: {str(e)}")


UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    file_path = Path(UPLOAD_DIR) / file.filename
    with open(file_path, "wb") as f:
        f.write(await file.read())

    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file_path.stat().st_size
    }

# Serve uploads folder
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Load AssemblyAI API key
aai.settings.api_key = ASSEMBLYAI_API_KEY

from tempfile import NamedTemporaryFile

@app.post("/transcribe/file")
async def transcribe_file(file: UploadFile = File(...)):
    try:
        audio_data = await file.read()
        transcriber = aai.Transcriber()

        # Save uploaded audio to a temp file and pass its path to AssemblyAI
        with NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
            tmp.write(audio_data)
            tmp.flush()
            transcript = transcriber.transcribe(tmp.name)

        return {"transcription": transcript.text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/tts/echo")
async def echo_bot(file: UploadFile = File(...)):
    try:
        audio_data = await file.read()

        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_data)
        text = transcript.text
        print(f"📝 Transcribed text: {text}")  

        murf_url = "https://api.murf.ai/v1/speech/generate"
        headers = {
            "Content-Type": "application/json",
            "api-key": MURF_API_KEY  
        }

        data = {
            "voiceId": "en-IN-alia",  
            "text": text,
            "format": "mp3"           
        }


        murf_response = requests.post(murf_url, headers=headers, json=data)
        print("📩 Murf API response:", murf_response.text)

        murf_response.raise_for_status()


        print("Murf API responded:", murf_response.json())  

        audio_file = murf_response.json().get("audioFile")
        if not audio_file:
            raise HTTPException(status_code=500, detail="No audio file returned from Murf.")

        return {"murf_audio_url": audio_file}

    except Exception as e:
        print(" Error in /tts/echo:", str(e))  
        raise HTTPException(status_code=500, detail=str(e))
    


# --- resilient config (replace the strict failure) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Warning: failed to configure genai:", e)
        GEMINI_API_KEY = None
else:
    print("Warning: GEMINI_API_KEY not set. LLM calls will use fallback text.")

class LLMRequest(BaseModel):
    text: str

def generate_murf_audio_safe(text, voice="en-IN-alia"):
    """Return audio URL on success or None on failure (no exception)."""
    if not MURF_API_KEY:
        print("Murf key missing - skipping Murf TTS.")
        return None
    try:
        murf_url = "https://api.murf.ai/v1/speech/generate"
        headers = {"Content-Type": "application/json", "api-key": MURF_API_KEY}
        data = {"voiceId": voice, "text": text, "format": "mp3"}
        resp = requests.post(murf_url, headers=headers, json=data, timeout=15)
        resp.raise_for_status()
        return resp.json().get("audioFile")
    except Exception as e:
        print("Murf TTS error:", e)
        return None




@app.post("/llm/query")
async def llm_query(file: UploadFile = File(...)):
    try:
        
        audio_data = await file.read()
        print(f"Received audio file: {file.filename}, size: {len(audio_data)} bytes")

        
        try:
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(audio_data)
            user_text = transcript.text if transcript and transcript.text else ""
            print(f"Transcription: {user_text}")
        except Exception as e:
            print("AssemblyAI error:", str(e))
            raise HTTPException(status_code=500, detail=f"AssemblyAI Error: {str(e)}")

        if not user_text.strip():
            print("[agent_chat] No speech detected — sending fallback.")
            return JSONResponse(status_code=200, content={
                "transcription": "",
                "llm_text": "I couldn't hear you clearly, could you please repeat?",
                "murf_audio_url": None,
                "fallback": True,
                "fallback_text": "I couldn't hear you clearly, could you please repeat?"
            })


        
        try:
            model = genai.GenerativeModel("models/gemini-1.5-flash")
            llm_response = model.generate_content(user_text)
            llm_text = llm_response.text
            print(f" LLM Response: {llm_text}")
        except Exception as e:
            print("Gemini error:", str(e))
            raise HTTPException(status_code=500, detail=f"LLM Error: {str(e)}")

        
        if len(llm_text) > 3000:
            llm_text = llm_text[:3000]

        murf_url = "https://api.murf.ai/v1/speech/generate"
        headers = {
            "Content-Type": "application/json",
            "api-key": MURF_API_KEY
        }

        data = {
            "voiceId": "en-IN-alia",
            "text": llm_text,
            "format": "mp3"
        }

        murf_response = requests.post(murf_url, headers=headers, json=data)
        print("Murf API Raw Response:", murf_response.text)

        if murf_response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Murf API Error: {murf_response.text}")

        murf_audio_url = murf_response.json().get("audioFile")
        if not murf_audio_url:
            raise HTTPException(status_code=500, detail="No audio file returned from Murf.")

        
        return {
            "transcription": user_text,
            "llm_text": llm_text,
            "murf_audio_url": murf_audio_url
        }

    except HTTPException:
        raise
    except Exception as e:
        print("❌ General error:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-text")
async def generate_text(prompt: str):
    url = "https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key=" + GEMINI_API_KEY
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = requests.post(url, json=payload)
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


# add this near the top (after app = FastAPI())
chat_sessions = {}  # in-memory: session_id -> list of {"role": "user"/"assistant", "content": "..."}

# New endpoint: Audio -> STT -> LLM with history -> TTS -> return audio URL
@app.post("/agent/chat/{session_id}")
async def agent_chat(session_id: str, file: UploadFile = File(...)):
    try:
        audio_data = await file.read()
        print(f"[agent_chat] Received audio {file.filename}, bytes={len(audio_data)}")

        # 1) Transcribe (AssemblyAI)
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_data)
        user_text = transcript.text if transcript and getattr(transcript, "text", None) else ""
        print("[agent_chat] Transcription:", user_text)

        if not user_text.strip():
            print("[agent_chat] No speech detected — sending fallback.")
            return JSONResponse(status_code=200, content={
                "transcription": "",
                "llm_text": "I couldn't hear you clearly, could you please repeat?",
                "murf_audio_url": None,
                "fallback": True,
                "fallback_text": "I couldn't hear you clearly, could you please repeat?"
            })


        # 2) Get/create chat history and append user message
        history = chat_sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": user_text})

        # 3) Build conversation prompt from history
        convo_lines = []
        for msg in history:
            prefix = "User" if msg["role"] == "user" else "Assistant"
            convo_lines.append(f"{prefix}: {msg['content']}")

        # ✅ Add system instruction
        system_instruction = (
            "You are a friendly AI voice assistant. "
            "Think internally before answering, but ONLY output your final spoken reply to the user. "
            "Do not include your reasoning, bullet points, or multiple options — just a clear, natural answer."
        )

        full_prompt = system_instruction + "\n\n" + "\n".join(convo_lines) + "\nAssistant:"

        # 4) Call LLM (Gemini) with fallback
        if GEMINI_API_KEY:
            try:
                model = genai.GenerativeModel("models/gemini-1.5-flash")
                llm_response = model.generate_content(full_prompt)
                llm_text = llm_response.text.strip()
            except Exception as e:
                print("[agent_chat] Gemini error:", e)
                llm_text = "I'm having trouble connecting right now."
        else:
            llm_text = "I'm having trouble connecting right now."

        # truncate safely
        if len(llm_text) > 3000:
            llm_text = llm_text[:3000]

        # 5) Save assistant reply to session history
        history.append({"role": "assistant", "content": llm_text})

        # 6) Generate TTS using Murf (safe)
        murf_audio_url = generate_murf_audio_safe(llm_text)

        if murf_audio_url:
            return {
                "transcription": user_text,
                "llm_text": llm_text,
                "murf_audio_url": murf_audio_url
            }
        else:
            # return a structured fallback so UI can recover
            return JSONResponse(status_code=200, content={
                "transcription": user_text,
                "llm_text": llm_text,
                "murf_audio_url": None,
                "fallback": True,
                "fallback_text": "I'm having trouble connecting right now."
            })

    except HTTPException:
        raise
    except Exception as e:
        print("[agent_chat] Error:", str(e))
        return JSONResponse(status_code=200, content={
            "transcription": "",
            "llm_text": "I'm having trouble connecting right now.",
            "murf_audio_url": None,
            "fallback": True,
            "fallback_text": "I'm having trouble connecting right now."
        })


