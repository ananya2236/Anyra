from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv
import requests
import os

# Load API key from .env file
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")

app = FastAPI()

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
    voice_id = body.get("voiceId", "en-AU-joyce")  # Default voice

    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    headers = {
        "Content-Type": "application/json",
        "api-key": MURF_API_KEY
    }

    data = {
        "text": text,
        "voiceId": "ta-IN-iniya"
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
