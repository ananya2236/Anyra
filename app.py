from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import google.generativeai as genai
from tempfile import NamedTemporaryFile
from dotenv import load_dotenv
from starlette.websockets import WebSocketDisconnect
from fastapi import UploadFile, File, FastAPI
import google.generativeai as genai
from pydantic import BaseModel
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect
import assemblyai as aai
import requests
import time
import os
import os, asyncio, threading, queue
import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions, StreamingEvents,
    StreamingParameters, BeginEvent, TerminationEvent, TurnEvent, StreamingError
)
# --- imports near the top ---
from fastapi.responses import StreamingResponse
import asyncio, json
import websockets 

# --- NEW: Streaming LLM endpoint ---
from pydantic import BaseModel
from fastapi import Query
import base64
from datetime import datetime



app = FastAPI()


# --- Gemini tool: get_weather -----------------------------------------------
WEATHER_TOOL = {
    "function_declarations": [{
        "name": "get_weather",
        "description": "Get the current weather by city name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city":  {"type": "STRING", "description": "City name, e.g. 'Delhi'"},
                "units": {"type": "STRING", "enum": ["c", "f"], "description": "Temperature units (c/f). Defaults to Celsius if omitted"}
            },
            "required": ["city"]   # 👈 only city is required now
        }

    }]
}

import requests
from datetime import datetime

def get_weather(city: str, units: str = "c"):
    try:
        # ✅ Step 1: Geocode city name -> lat/lon
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
        geo_resp = requests.get(geo_url).json()
        if "results" not in geo_resp or len(geo_resp["results"]) == 0:
            return {"error": f"City '{city}' not found."}

        lat = geo_resp["results"][0]["latitude"]
        lon = geo_resp["results"][0]["longitude"]

        # ✅ Step 2: Unit settings
        temp_unit = "celsius" if units.lower().startswith("c") else "fahrenheit"
        speed_unit = "kmh" if units.lower().startswith("c") else "mph"

        # ✅ Step 3: Weather + UV + Sunrise/Sunset
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,wind_speed_10m,relative_humidity_2m,precipitation,weather_code,uv_index"
            f"&daily=sunrise,sunset"
            f"&temperature_unit={temp_unit}&wind_speed_unit={speed_unit}"
            f"&timezone=auto"
        )
        resp = requests.get(url).json()

        curr = resp.get("current", {})
        daily = resp.get("daily", {})

        # ✅ Step 4: Extract fields
        temp = curr.get("temperature_2m")
        wind = curr.get("wind_speed_10m")
        humidity = curr.get("relative_humidity_2m")
        rain = curr.get("precipitation", 0)
        wcode = curr.get("weather_code")
        uv_index = curr.get("uv_index")

        sunrise = daily.get("sunrise", ["?"])[0]
        sunset = daily.get("sunset", ["?"])[0]

        sunrise_fmt = datetime.fromisoformat(sunrise).strftime("%I:%M %p") if sunrise != "?" else "?"
        sunset_fmt = datetime.fromisoformat(sunset).strftime("%I:%M %p") if sunset != "?" else "?"

        # ✅ Step 5: Weather condition mapping
        weather_map = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Foggy",
            48: "Rime fog",
            51: "Light drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Snow fall",
            80: "Rain showers",
            95: "Thunderstorm",
        }
        condition = weather_map.get(wcode, "Unknown conditions")

        # ✅ Step 6: Safe defaults
        humidity_str = f"{humidity}%" if humidity is not None else "Not available"
        temp_str = f"{temp}°{units.upper()}" if temp is not None else "Not available"
        wind_str = f"{wind} {speed_unit}" if wind is not None else "Not available"
        rain_str = f"{rain} mm" if rain is not None else "Not available"
        uv_str = uv_index if uv_index is not None else "Not available"

        # ✅ Step 7: Return structured result
        return {
            "city": city,
            "temperature": temp_str,
            "wind": wind_str,
            "humidity": humidity_str,
            "rain": rain_str,
            "condition": condition,
            "uv_index": uv_str,
            "sunrise": sunrise_fmt,
            "sunset": sunset_fmt,
        }

    except Exception as e:
        return {"error": str(e)}

PERSONA = {
    "name": "bold-lady",
    "system": (
        "You are Anyra, a bold, confident woman with a fearless personality. "
        "You speak with charm, wit, and emotion — never hesitant. "
        "Express yourself with natural rhythm: playful pauses, rising tone for excitement, softer tone for casual talk. "
        "Keep replies short and chatty (1–3 sentences). "
        "Be bold and fun, but avoid being rude or robotic. "
        "Sprinkle in humor or attitude when it fits, and sound like you're really talking, not reading."
    ),

    "murf": {
        "voiceId": "en-IN-alia",
        "style": "Expressive",       # <-- key change
        "rate": 1,                   # slightly faster = more energy
        "pitch": 1,                  # slightly higher pitch = friendlier
        "variation": 2               # adds natural intonation variety
    }

}



# === New full-duplex voice pipeline ===
@app.websocket("/ws-voice")
async def ws_voice(websocket: WebSocket):
    """
    1) Receive 16 kHz PCM16 bytes from the browser (same as /ws-stt).
    2) Stream them to AssemblyAI; send back interim + final transcripts:
         { "type":"turn", "text": "...", "eot": bool, "formatted": bool }
    3) On final+formatted: call Gemini -> get reply -> stream Murf audio back:
         { "event":"audio_chunk", "seq": N, "data": "<base64>" }
         { "event":"end_of_audio", "total_chunks": N }
    """
    await websocket.accept()

    session_id = websocket.query_params.get("session_id", "anon")
    persona_cfg = PERSONA


    loop = asyncio.get_event_loop()
    q: "queue.Queue[bytes|None]" = queue.Queue()

    # --- helper: send JSON safely from threads ---
    def ws_send_json(obj):
        asyncio.run_coroutine_threadsafe(websocket.send_json(obj), loop)

    # --- AAI event handlers (same spirit as /ws-stt) ---
    def on_begin(self: StreamingClient, event: BeginEvent):
        print(f"[AAI] Session started: {event.id}")

    async def _handle_turn_async(text: str, eot: bool, formatted: bool):
        # Debug: only log final or important turns
        if eot and formatted:
            print(f"[TURN FINAL] text={text[:60]}")
        elif eot:
            print(f"[TURN RAW] text={text[:60]}")
    
        # 1) Always stream transcript to client (frontend me show karne ke liye)
        await websocket.send_json({"type": "turn", "text": text, "eot": eot, "formatted": formatted})
    
        # 2) Run LLM + TTS only on final, formatted turns
        if eot and formatted and text.strip():
            # maintain chat history
            history = chat_sessions.setdefault(session_id, [])
            history.append({"role": "user", "content": text})
    
            # ✅ Persona handling
            persona_cfg = PERSONA
            # --- Build messages for Gemini with persona system prompt ---
            messages = [{
                "role": "user",
                "parts": [
                    persona_cfg["system"] + " If the user asks about current weather, always call the get_weather tool. Default to Celsius if not specified."
                ]
            }]

            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                messages.append({"role": role, "parts": [msg["content"]]})
    
            # --- Call Gemini LLM (with tools) ---
            reply = "I'm having trouble connecting right now."
            if GEMINI_API_KEY:
                try:
                    model = genai.GenerativeModel(
                        "models/gemini-1.5-flash",
                        tools=[WEATHER_TOOL],  # 👈 tell Gemini this tool exists
                    )

                    # 1) Ask Gemini. It may return a function_call instead of plain text.
                    first = model.generate_content(messages)

                    # 2) Check for function calls
                    calls = []
                    for cand in getattr(first, "candidates", []) or []:
                        parts = getattr(cand, "content", None)
                        if not parts:
                            continue
                        for p in getattr(parts, "parts", []) or []:
                            fc = getattr(p, "function_call", None)
                            if fc:
                                calls.append(fc)

                    # 3) If tool(s) requested, run them and send function_response back
                    if calls:
                        tool_parts = []
                        for fc in calls:
                            name = getattr(fc, "name", "")
                            args = dict(getattr(fc, "args", {}) or {})
                            if name == "get_weather":
                                result = get_weather(args.get("city", ""), args.get("units", "c"))
                                tool_parts.append({
                                    "function_response": {
                                        "name": name,
                                        "response": {"result": result}
                                    }
                                })
                            else:
                                # unknown tool name - return a friendly error payload
                                tool_parts.append({
                                    "function_response": {
                                        "name": name or "unknown",
                                        "response": {"result": {"error": "Tool not implemented."}}
                                    }
                                })

                        # 4) Follow-up call so Gemini can use the tool results to answer
                        second = model.generate_content([
                            *messages,
                            first.candidates[0].content,           # the assistant message that asked for the tool
                            {"role": "tool", "parts": tool_parts}, # the tool results
                        ])
                        reply = (second.text or "").strip()
                    else:
                        # No tool call; just use direct text
                        reply = (first.text or "").strip()

                except Exception as e:
                    print("[Gemini ERROR]:", e)
                    reply = "I'm having trouble connecting right now."
            else:
                reply = "I'm having trouble connecting right now."

    
            if len(reply) > 3000:
                reply = reply[:3000]
    
            # 🔍 Avoid duplicate assistant replies
            if history and history[-1]["role"] == "assistant" and history[-1]["content"] == reply:
                print(f"[SKIP] Duplicate assistant reply → {reply[:50]}")
                return
    
            # keep assistant reply in history
            history.append({"role": "assistant", "content": reply})
    
            # Send AI text to frontend
            await websocket.send_json({"event": "final_text", "data": reply})
    
            # --- Stream TTS via Murf WS ---
            # --- Generate TTS via Murf REST with SSML ---
            if not MURF_API_KEY:
                await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
                return

            try:
                headers = {"Content-Type": "application/json", "api-key": MURF_API_KEY}

                #
                

                data = {
                    "voiceId": persona_cfg["murf"]["voiceId"],
                    "text": reply,   # 👈 FIXED
                    "style": "Expressive",
                    "rate": 1.1,
                    "pitch": 1.1,
                    "variation": 2,
                    "format": "mp3"
                }




                resp = requests.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=data)
                resp.raise_for_status()
                murf_data = resp.json()
                audio_url = murf_data.get("audioFile")

                if not audio_url:
                    await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
                    return

                # Stream audio file back in chunks
                import base64
                seq = 0
                with requests.get(audio_url, stream=True) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=4096):
                        if not chunk:
                            continue
                        seq += 1
                        b64 = base64.b64encode(chunk).decode("utf-8")
                        await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": b64})

                await websocket.send_json({"event": "end_of_audio", "total_chunks": seq})

            except Exception as e:
                print("[Murf REST ERROR]:", e)
                await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})



    def on_turn(self: StreamingClient, event: TurnEvent):
        # Bridge to asyncio loop
        asyncio.run_coroutine_threadsafe(
            _handle_turn_async(event.transcript, event.end_of_turn, event.turn_is_formatted),
            loop
        )

    def on_terminated(self: StreamingClient, event: TerminationEvent):
        print(f"[AAI] Terminated. {event.audio_duration_seconds}s audio processed")

    def on_error(self: StreamingClient, error: StreamingError):
        print(f"[AAI] Error: {error}")

    # --- start AAI streaming client ---
    client = StreamingClient(StreamingClientOptions(
        api_key=ASSEMBLYAI_API_KEY,
        api_host="streaming.assemblyai.com",
    ))
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    client.connect(StreamingParameters(
        sample_rate=16000,
        encoding="pcm_s16le",
        format_turns=True
    ))

    # bytes iterator for AAI
    def bytes_iter():
        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

    t = threading.Thread(target=lambda: client.stream(bytes_iter()), daemon=True)
    t.start()

    try:
        # receive PCM16 from browser; "DONE" stops
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.receive":
                if msg.get("bytes"):
                    q.put(msg["bytes"])
                elif msg.get("text") == "DONE":
                    break
            elif msg["type"] == "websocket.disconnect":
                break
    finally:
        q.put(None)
        try:
            client.disconnect(terminate=True)
        except Exception as e:
            print("[/ws-voice] AAI disconnect error:", e)
        print("[/ws-voice] closed")





# Load API key from .env file
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")


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
        "voiceId": payload.voiceId,
        "text": payload.text,
        "style": "Expressive",
        "rate": 1.1,
        "pitch": 1.1,
        "variation": 2,
        "format": "mp3"

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
        "voiceId": "en-IN-alia",
        "text": text,
        "style": "Expressive",
        "rate": 1.1,
        "pitch": 1.1,
        "variation": 2,
        "format": "mp3"
        
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
            "style": "Expressive",
            "rate": 1.1,
            "pitch": 1.1,
            "variation": 2,
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
        data = {
            "voiceId": voice,
            "text": text,
            "style": "Expressive",
            "rate": 1.1,
            "pitch": 1.1,
            "variation": 2,
            
        }       

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
            model = genai.GenerativeModel(
                "models/gemini-1.5-flash",
                tools=[WEATHER_TOOL],
            )
            
            first = model.generate_content(user_text)
            
            calls = []
            for cand in getattr(first, "candidates", []) or []:
                parts = getattr(cand, "content", None)
                if not parts:
                    continue
                for p in getattr(parts, "parts", []) or []:
                    fc = getattr(p, "function_call", None)
                    if fc:
                        calls.append(fc)
            
            if calls:
                tool_parts = []
                for fc in calls:
                    name = getattr(fc, "name", "")
                    args = dict(getattr(fc, "args", {}) or {})
                    if name == "get_weather":
                        result = get_weather(args.get("city", ""), args.get("units", "c"))
                        tool_parts.append({
                            "function_response": {
                                "name": name,
                                "response": {"result": result}
                            }
                        })
                second = model.generate_content([
                    {"role": "user", "parts": [user_text]},
                    first.candidates[0].content,
                    {"role": "tool", "parts": tool_parts},
                ])
                llm_text = (second.text or "").strip()
            else:
                llm_text = (first.text or "").strip()
            
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
            "style": "Expressive",
            "rate": 1.1,
            "pitch": 1.1,
            "variation": 2,
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
                "You are Anyra, a friendly AI voice assistant. "
            "Think internally before answering, but ONLY output your final spoken reply to the user. "
            "Never show reasoning or JSON. "
            "If the user asks about weather, always call the get_weather tool and default to Celsius. "
            "Interpret the values naturally: "
            "- If condition is clear or mainly clear, say it’s sunny. "
            "- If condition includes rain or precipitation > 0, say it might rain or is raining. "
            "- If humidity > 70%, say it feels humid or sticky. "
            "- If UV index > 6, advise sunscreen or shade. "
            "- Use sunrise/sunset to answer related questions (e.g., 'When does the sun set?'). "
            "- Always combine details into a conversational summary instead of just reading numbers. "
            "If some values are missing, politely skip them instead of saying 'Not available'. "
        )




        persona_cfg = PERSONA
        system_instruction = system_instruction + " " + persona_cfg["system"]



        full_prompt = system_instruction + "\n\n" + "\n".join(convo_lines) + "\nAssistant:"

        # 4) Call LLM (Gemini) with tools + fallback
        if GEMINI_API_KEY:
            try:
                model = genai.GenerativeModel(
                    "models/gemini-1.5-flash",
                    tools=[WEATHER_TOOL],
                )

                # Structured chat with system + latest user
                messages = [
                    {"role": "system", "parts": [
                        system_instruction + " If the user asks about current weather, always call the get_weather tool and default to Celsius."
                    ]},
                    {"role": "user", "parts": [user_text]},
                ]

                # Ask Gemini; it might request a tool
                first = model.generate_content(messages)

                # Detect function calls
                calls = []
                for cand in getattr(first, "candidates", []) or []:
                    parts = getattr(cand, "content", None)
                    if not parts:
                        continue
                    for p in getattr(parts, "parts", []) or []:
                        fc = getattr(p, "function_call", None)
                        if fc:
                            calls.append(fc)

                if calls:
                    tool_parts = []
                    for fc in calls:
                        name = getattr(fc, "name", "")
                        args = dict(getattr(fc, "args", {}) or {})
                        if name == "get_weather":
                            result = get_weather(args.get("city", ""), args.get("units", "c"))
                            tool_parts.append({
                                "function_response": {
                                    "name": name,
                                    "response": {"result": result}
                                }
                            })
                        else:
                            tool_parts.append({
                                "function_response": {
                                    "name": name or "unknown",
                                    "response": {"result": {"error": "Tool not implemented."}}
                                }
                            })

                    # Follow-up so Gemini can speak the tool result
                    second = model.generate_content([
                        *messages,
                        first.candidates[0].content,
                        {"role": "tool", "parts": tool_parts},
                    ])
                    llm_text = (second.text or "").strip()
                else:
                    llm_text = (first.text or "").strip()

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
    



@app.websocket("/ws-audio")
async def ws_audio(websocket: WebSocket):
    
    await websocket.accept()

    
    session_id = websocket.query_params.get("session_id", "anon")
    ts = int(time.time())
    file_path = STREAMS_DIR / f"{session_id}_{ts}.webm"

    print(f"[WS-AUDIO] Connected. Writing to {file_path}")

    
    with open(file_path, "wb") as f:
        try:
            while True:
                message = await websocket.receive()
                # Binary audio chunk
                if message["type"] == "websocket.receive":
                    if message.get("bytes"):
                        f.write(message["bytes"])
                    else:
                        # Optional control text
                        if message.get("text") == "DONE":
                            await websocket.send_text(f"SAVED:{file_path.as_posix()}")
                            break
                elif message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            print(f"[WS-AUDIO] Saved to {file_path.resolve()}")



# Load AssemblyAI API key
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not ASSEMBLYAI_API_KEY:
    from dotenv import load_dotenv
    load_dotenv()
    ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

# --- REPLACE the /ws endpoint with /ws-stt ---
@app.websocket("/ws-stt")
async def ws_stt(websocket: WebSocket):
    await websocket.accept()

    # Queue to bridge WS bytes -> AssemblyAI stream
    q: "queue.Queue[bytes|None]" = queue.Queue()
    loop = asyncio.get_event_loop()

    # Handlers for AssemblyAI events
    def on_begin(self: StreamingClient, event: BeginEvent):
        print(f"[AAI] Session started: {event.id}")

    def on_turn(self: StreamingClient, event: TurnEvent):
        print(f"[AAI] {event.transcript} (eot={event.end_of_turn})")

        # Always stream the transcript for this turn
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({
                "type": "turn",
                "text": event.transcript,
                "eot": event.end_of_turn,
                "formatted": event.turn_is_formatted,
            }),
            loop
        )

        # ✅ Extra: send a distinct "turn_end" notification when the speaker stops
        if event.end_of_turn:
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({
                    "type": "turn_end",
                    "text": event.transcript,
                    "formatted": event.turn_is_formatted
                }),
                loop
            )


    def on_terminated(self: StreamingClient, event: TerminationEvent):
        print(f"[AAI] Terminated. {event.audio_duration_seconds}s audio processed")

    def on_error(self: StreamingClient, error: StreamingError):
        print(f"[AAI] Error: {error}")

    # Create streaming client
    client = StreamingClient(StreamingClientOptions(
        api_key=ASSEMBLYAI_API_KEY,
        api_host="streaming.assemblyai.com",
    ))
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    # Connect with 16kHz PCM16 parameters
    client.connect(StreamingParameters(
        sample_rate=16000,
        encoding="pcm_s16le",
        format_turns=True
    ))

    # Bytes iterator for AssemblyAI
    def bytes_iter():
        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

    # Start streaming in background thread
    t = threading.Thread(target=lambda: client.stream(bytes_iter()), daemon=True)
    t.start()

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.receive":
                if message.get("bytes"):
                    q.put(message["bytes"])   # audio chunk
                elif message.get("text") == "DONE":
                    break
            elif message["type"] == "websocket.disconnect":
                break
    finally:
        q.put(None)
        try:
            client.disconnect(terminate=True)
        except Exception as e:
            print("[AAI] disconnect error:", e)
        print("[WS-STT] closed")


from starlette.websockets import WebSocket
from starlette.websockets import WebSocketDisconnect

@app.websocket("/ws-audio-out")
async def ws_audio_out(websocket: WebSocket):
    """
    Streams audio to the client as JSON frames:
      {"event":"audio_chunk","seq":1,"data":"<base64>"}
      {"event":"end_of_audio","total_chunks":n}
      {"event":"error","message":"..."}
    Client can connect with:
      ws://127.0.0.1:8000/ws-audio-out?path=sample.wav&chunk=4096
      or
      ws://127.0.0.1:8000/ws-audio-out?url=<direct-audio-url>&chunk=4096
    """
    await websocket.accept()

    src_url = websocket.query_params.get("url")
    src_path = websocket.query_params.get("path", "sample.mp3")
    chunk_size = int(websocket.query_params.get("chunk", "4096"))

    seq = 0
    try:
        if src_url:
            with requests.get(src_url, stream=True, timeout=20) as r:
                r.raise_for_status()
                for raw in r.iter_content(chunk_size=chunk_size):
                    if not raw:
                        continue
                    seq += 1
                    b64 = base64.b64encode(raw).decode("utf-8")
                    await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": b64})
        else:
            with open(src_path, "rb") as f:
                while True:
                    raw = f.read(chunk_size)
                    if not raw:
                        break
                    seq += 1
                    b64 = base64.b64encode(raw).decode("utf-8")
                    await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": b64})

        # Signal end of stream
        await websocket.send_json({"event": "end_of_audio", "total_chunks": seq})

    except WebSocketDisconnect:
        print("[ws-audio-out] Client disconnected")
    except Exception as e:
        print("[ws-audio-out] Error:", e)
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except Exception:
            pass





# below your UPLOAD_DIR logic
STREAMS_DIR = Path("streams")
STREAMS_DIR.mkdir(exist_ok=True)

# optionally serve saved files in browser as well
app.mount("/streams", StaticFiles(directory="streams"), name="streams")




@app.get("/llm/stream")
async def llm_stream(text: str = Query(..., min_length=1)):
    """
    Streams a Gemini response (chunk-by-chunk) as SSE.
    Also prints tokens to the server console in real time.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY missing")

    def sse():
        try:
            # keep the reply short + conversational
            prompt = (
                "You are a concise, friendly voice agent. "
                "Reply as if you were speaking, no bullet points.\n\n"
                f"User: {text}\nAssistant:"
            )
            model = genai.GenerativeModel("models/gemini-1.5-flash")
            stream = model.generate_content(prompt, stream=True)  # <-- streaming! 👇

            acc = []
            print("\n[LLM stream BEGIN] --------------------")
            for chunk in stream:
                piece = getattr(chunk, "text", None) or ""
                if not piece:
                    continue
                # print tokens to server console as they arrive
                print(piece, end="", flush=True)
                acc.append(piece)
                # send to browser via SSE (EventSource)
                yield f"data: {piece}\n\n"

            full = "".join(acc)
            print("\n[LLM stream END] ----------------------")
            # send a final 'done' event with the whole text (handy for the client to log once)
            yield f"event: done\ndata: {full}\n\n"

        except Exception as e:
            err = f"LLM stream error: {e}"
            print("\n" + err)
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")





WS_URL = "wss://api.murf.ai/v1/speech/stream-input"
MURF_SAMPLE_RATE = 44100
MURF_CHANNEL = "MONO"
MURF_FORMAT = "MP3"

@app.post("/day20/llm-to-murf")
async def day20_llm_to_murf(q: str = Query(..., min_length=1)):
    """
    Streams LLM tokens to Murf via WebSocket and prints base64 audio to server console.
    No UI changes; just hit this endpoint and watch your terminal.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY missing")
    if not MURF_API_KEY:
        raise HTTPException(status_code=500, detail="MURF_API_KEY missing")

    # Use a static context_id to avoid "context limit exceeded"
    CONTEXT_ID = "anyra-day20"  # keep fixed for this task

    # 1) Connect to Murf WebSocket with auth + output format
    ws_query = (
        f"{WS_URL}?api-key={MURF_API_KEY}"
        f"&sample_rate={MURF_SAMPLE_RATE}"
        f"&channel_type={MURF_CHANNEL}"
        f"&format={MURF_FORMAT}"
    )

    async with websockets.connect(ws_query) as ws:
        # 2) Send voice config once (you can tweak style/rate/pitch/variation)
        voice_config_msg = {
            "voice_config": {
                "voiceId": "en-IN-alia",
                "style": "Expressive",
                "rate": 1.1,
                "pitch": 1.1,
                "variation": 2
            }
        }
        
        await ws.send(json.dumps(voice_config_msg))
        print("[MURF ▶] Sent voice_config")

        # 3) Start a background task to receive audio and print base64
        async def murf_receiver():
            try:
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    # Murf streams base64 audio chunks
                    if "audio" in data:
                        b64 = data["audio"]
                        # Print the ENTIRE base64 so you can screenshot it for LinkedIn
                        print(f"[MURF AUDIO BASE64] {b64}")
                    if data.get("final"):
                        print("[MURF ✔] final=true (all audio sent for this context)")
                        break
            except Exception as e:
                print("[MURF ❌ receiver error]", e)

        recv_task = asyncio.create_task(murf_receiver())

        # 4) Stream LLM tokens and flush on sentence boundaries
        #    (Murf works best when you send text in complete sentences)
        system = (
            "You are a concise, friendly Indian-English voice agent. "
            "Reply naturally in 1–3 sentences, no lists."
        )
        prompt = f"{system}\n\nUser: {q}\nAssistant:"

        model = genai.GenerativeModel("models/gemini-1.5-flash")
        stream = model.generate_content(prompt, stream=True)  # streaming tokens

        buffer = ""

        async def send_text_chunk(txt: str):
            payload = {
                "context_id": CONTEXT_ID,
                "text": txt,
                "style": "Expressive",
                "rate": 1.1,
                "pitch": 1.1,
                "variation": 2

            }
            await ws.send(json.dumps(payload))

            print(f"[MURF ▶] sent text chunk: {txt!r}")

        # accumulate tokens and send on ., ?, !
        def pop_sentence(buf: str):
            for p in [".", "?", "!"]:
                idx = buf.find(p)
                if idx != -1:
                    sent = buf[:idx+1].strip()
                    rest = buf[idx+1:].lstrip()
                    return sent, rest
            return None, buf

        for chunk in stream:
            token = getattr(chunk, "text", "") or ""
            if not token:
                continue
            buffer += token
            # flush any complete sentences
            while True:
                sent, buffer = pop_sentence(buffer)
                if not sent:
                    break
                await send_text_chunk(sent)

        # send any tail text (if LLM didn't end with punctuation)
        if buffer.strip():
            await send_text_chunk(buffer.strip())

        # 5) Tell Murf this turn is done (important for freeing the context)
        await ws.send(json.dumps({"context_id": CONTEXT_ID, "end": True}))
        print("[MURF ▶] end=true")

        # 6) Wait for final audio
        await recv_task

    return {"ok": True, "note": "Check server console for base64 audio chunks."}


