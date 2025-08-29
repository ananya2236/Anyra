# --- imports ---
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, WebSocket, Header, Query
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import google.generativeai as genai
from tempfile import NamedTemporaryFile
# from dotenv import load_dotenv
from starlette.websockets import WebSocketDisconnect
from pathlib import Path
import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions, StreamingEvents,
    StreamingParameters, BeginEvent, TerminationEvent, TurnEvent, StreamingError
)
import requests, time, os, asyncio, threading, queue, json, websockets, base64, uuid, re
from datetime import datetime

# Disable local .env fallback for production
USE_LOCAL_KEYS = False


# --- load once at startup (for local dev only) ---
# load_dotenv()
# Optional local defaults — endpoints will require frontend keys
MURF_API_KEY_LOCAL = os.getenv("MURF_API_KEY")
ASSEMBLYAI_API_KEY_LOCAL = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY_LOCAL = os.getenv("GEMINI_API_KEY")

# configure genai if we have a local key (keeps safe fallback)
if GEMINI_API_KEY_LOCAL:
    try:
        genai.configure(api_key=GEMINI_API_KEY_LOCAL)
    except Exception as e:
        print("Warning: failed to configure genai with local key:", e)

# app + dirs
app = FastAPI()
STREAMS_DIR = Path("streams")
STREAMS_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Static + templates
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/streams", StaticFiles(directory="streams"), name="streams")
templates = Jinja2Templates(directory="templates")

# CORS for local frontend (adjust in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Globals ---
conversation_history: dict[str, list[dict]] = {}

# --- Persona & skills ---
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
        "style": "Expressive",
        "rate": 1,
        "pitch": 1,
        "variation": 2
    }
}

SYMPTOM_PATTERNS = {
    "fever": r"\bfever(ish)?|running (a )?temperature|running hot\b",
    "tired": r"\btired|exhaust(ed|ion)|fatigue(d)?\b",
    "headache": r"\bheadache|migraine\b",
    "cough": r"\bcough|cold|sore throat|throat pain|runny nose\b",
    "stress": r"\bstress(ed)?|anxious|anxiety|overwhelmed|burnt out|burned out\b",
}

def detect_symptom(text: str):
    t = (text or "").lower()
    for k, pat in SYMPTOM_PATTERNS.items():
        if re.search(pat, t):
            return k
    return None

def _bits_from_weather(w):
    if not isinstance(w, dict) or w.get("error"):
        return None
    parts = []
    if w.get("condition"): parts.append(w["condition"].lower())
    if w.get("temperature"): parts.append(f"{w['temperature']}")
    if w.get("humidity"): parts.append(f"humidity {w['humidity']}")
    try:
        rv = float(str(w.get("rain", "0")).split()[0])
        if rv > 0: parts.append("rain expected")
    except Exception:
        pass
    return "; ".join(parts) if parts else None

def build_health_advice(symptom: str, weather: dict | None):
    wbits = _bits_from_weather(weather) if weather else None
    wx = f" Today: {wbits}." if wbits else ""
    if symptom == "fever":
        msg = ("You’re feeling feverish." + wx +
               " Rest, sip warm fluids, and you may take paracetamol if you’re not allergic—follow the label."
               " If high fever or symptoms persist, see a clinician.")
    elif symptom == "tired":
        msg = ("You’re feeling drained." + wx +
               " Hydrate, do a 5-minute stretch, and try a short power nap."
               " If it lasts for days, review sleep and meals.")
    elif symptom == "headache":
        msg = ("Headache noted." + wx +
               " Drink water, dim the screen, and rest; paracetamol is okay if suitable for you."
               " Seek care if it’s severe or unusual.")
    elif symptom == "cough":
        msg = ("Sounds like a cold or cough." + wx +
               " Try steam inhalation, warm water or ginger tea, and lozenges."
               " If breathing is hard or fever persists, see a doctor.")
    elif symptom == "stress":
        msg = ("You’re stressed." + wx +
               " Let’s do 3 deep breaths together… inhale… exhale… and consider a short walk or calming music.")
    else:
        msg = "I’m here for you. Tell me more about how you feel."
    return msg + " This is general guidance—not medical advice."

# --- Weather tool declaration for Gemini ---
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
            "required": ["city"]
        }
    }]
}

def get_weather(city: str, units: str = "c"):
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
        geo_resp = requests.get(geo_url, timeout=6).json()
        if "results" not in geo_resp or not geo_resp["results"]:
            return {"error": f"City '{city}' not found."}
        lat = geo_resp["results"][0]["latitude"]
        lon = geo_resp["results"][0]["longitude"]
        temp_unit = "celsius" if units.lower().startswith("c") else "fahrenheit"
        speed_unit = "kmh" if units.lower().startswith("c") else "mph"
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,wind_speed_10m,relative_humidity_2m,precipitation,weather_code,uv_index"
            f"&daily=sunrise,sunset"
            f"&temperature_unit={temp_unit}&wind_speed_unit={speed_unit}"
            f"&timezone=auto"
        )
        resp = requests.get(url, timeout=6).json()
        curr = resp.get("current", {})
        daily = resp.get("daily", {})
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
        weather_map = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Rime fog", 51: "Light drizzle", 61: "Slight rain",
            63: "Moderate rain", 65: "Heavy rain", 71: "Snow fall", 80: "Rain showers", 95: "Thunderstorm",
        }
        condition = weather_map.get(wcode, "Unknown conditions")
        humidity_str = f"{humidity}%" if humidity is not None else "Not available"
        temp_str = f"{temp}°{units.upper()}" if temp is not None else "Not available"
        wind_str = f"{wind} {speed_unit}" if wind is not None else "Not available"
        rain_str = f"{rain} mm" if rain is not None else "Not available"
        uv_str = uv_index if uv_index is not None else "Not available"
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

# --- Helper: Murf TTS (safe) ---
def generate_murf_audio_safe(text: str, x_murf_key: str, voice: str = "en-IN-alia"):
    if not x_murf_key:
        return None
    try:
        murf_url = "https://api.murf.ai/v1/speech/generate"
        headers = {"Content-Type": "application/json", "api-key": x_murf_key}
        data = {
            "voiceId": voice, "text": text, "style": "Expressive",
            "rate": 1.1, "pitch": 1.1, "variation": 2, "format": "mp3"
        }
        resp = requests.post(murf_url, headers=headers, json=data, timeout=15)
        resp.raise_for_status()
        return resp.json().get("audioFile")
    except Exception as e:
        print("Murf TTS error:", e)
        return None

# --- Routes (clean) ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

class TTSRequest(BaseModel):
    text: str
    voiceId: str

@app.get("/voices")
def get_voices(x_murf_key: str = Header(None)):
    if not x_murf_key:
        raise HTTPException(status_code=400, detail="Murf API key required from frontend (x-murf-key header)")
    try:
        resp = requests.get("https://api.murf.ai/v1/speech/voices", headers={"api-key": x_murf_key}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch voices: {e}")

@app.post("/generate-voice")
def generate_voice(payload: TTSRequest, x_murf_key: str = Header(None)):
    if not x_murf_key:
        raise HTTPException(status_code=400, detail="Murf API key required")
    try:
        resp = requests.post(
            "https://api.murf.ai/v1/speech/generate",
            headers={"Content-Type": "application/json", "api-key": x_murf_key},
            json={
                "voiceId": payload.voiceId,
                "text": payload.text,
                "style": "Expressive",
                "rate": 1.1, "pitch": 1.1, "variation": 2, "format": "mp3"
            },
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tts")
async def generate_tts(request: Request, x_murf_key: str = Header(None)):
    if not x_murf_key:
        raise HTTPException(status_code=400, detail="Murf API key required (x-murf-key)")
    body = await request.json()
    text = body.get("text")
    voice_id = body.get("voiceId", "en-IN-alia")
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    audio_url = generate_murf_audio_safe(text, x_murf_key, voice=voice_id)
    if not audio_url:
        raise HTTPException(status_code=500, detail="Murf TTS failed")
    return JSONResponse(content={"url": audio_url})

@app.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    filepath = UPLOAD_DIR / file.filename
    with open(filepath, "wb") as f:
        f.write(await file.read())
    return {"filename": file.filename, "content_type": file.content_type, "size": filepath.stat().st_size}

# AssemblyAI simple transcribe (file)
@app.post("/transcribe/file")
async def transcribe_file(file: UploadFile = File(...), x_aai_key: str = Header(None)):
    if not x_aai_key and not ASSEMBLYAI_API_KEY_LOCAL:
        raise HTTPException(status_code=400, detail="AssemblyAI key required (x-aai-key header)")
    key = x_aai_key or ASSEMBLYAI_API_KEY_LOCAL
    try:
        aai.settings.api_key = key
        audio_data = await file.read()
        with NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
            tmp.write(audio_data); tmp.flush()
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(tmp.name)
        return {"transcription": transcript.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# echo endpoint: transcribe -> murf
@app.post("/tts/echo")
async def echo_bot(
    file: UploadFile = File(...),
    x_murf_key: str = Header(None),
    x_aai_key: str = Header(None)
):
    if not x_murf_key or not x_aai_key:
        raise HTTPException(status_code=400, detail="Murf + AssemblyAI API keys required from frontend")
    try:
        aai.settings.api_key = x_aai_key
        audio_data = await file.read()
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_data)
        text = transcript.text or ""
        audio_file = generate_murf_audio_safe(text, x_murf_key)
        if not audio_file:
            raise HTTPException(status_code=500, detail="Murf TTS failed")
        return {"murf_audio_url": audio_file}
    except Exception as e:
        print("❌ Error in /tts/echo:", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/llm/query")
async def llm_query(
    file: UploadFile = File(...),
    x_murf_key: str = Header(None),
    x_aai_key: str = Header(None),
    x_gemini_key: str = Header(None),
):
    if not x_murf_key or not x_aai_key or not x_gemini_key:
        raise HTTPException(status_code=400, detail="Murf, AssemblyAI, and Gemini API keys required")
    try:
        aai.settings.api_key = x_aai_key
        audio_data = await file.read()
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_data)
        user_text = transcript.text if transcript and transcript.text else ""
        if not user_text.strip():
            return JSONResponse(status_code=200, content={
                "transcription": "", "llm_text": "I couldn't hear you clearly, could you please repeat?",
                "murf_audio_url": None, "fallback": True, "fallback_text": "I couldn't hear you clearly, could you please repeat?"
            })
        # Gemini call
        genai.configure(api_key=x_gemini_key)
        model = genai.GenerativeModel("models/gemini-1.5-flash", tools=[WEATHER_TOOL])
        # bake persona system into messages
        messages = [
            {"role": "user", "parts": [PERSONA["system"]]},
            {"role": "user", "parts": [user_text]},
        ]
        first = model.generate_content(messages)
        # detect function calls
        calls = []
        for cand in getattr(first, "candidates", []) or []:
            parts = getattr(cand, "content", None)
            if not parts:
                continue
            for p in getattr(parts, "parts", []) or []:
                fc = getattr(p, "function_call", None)
                if fc: calls.append(fc)
        if calls:
            tool_parts = []
            for fc in calls:
                name = getattr(fc, "name", "")
                args = dict(getattr(fc, "args", {}) or {})
                if name == "get_weather":
                    result = get_weather(args.get("city", ""), args.get("units", "c"))
                    tool_parts.append({"function_response": {"name": name, "response": {"result": result}}})
            second = model.generate_content([
                *messages, first.candidates[0].content, {"role": "tool", "parts": tool_parts}
            ])
            llm_text = (second.text or "").strip()
        else:
            llm_text = (first.text or "").strip()
        if len(llm_text) > 3000:
            llm_text = llm_text[:3000]
        murf_audio_url = generate_murf_audio_safe(llm_text, x_murf_key)
        return {"transcription": user_text, "llm_text": llm_text, "murf_audio_url": murf_audio_url}
    except HTTPException:
        raise
    except Exception as e:
        print("❌ General error in /llm/query:", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-text")
async def generate_text(prompt: str, x_gemini_key: str = Header(None)):
    if not x_gemini_key:
        raise HTTPException(status_code=400, detail="Gemini API key required")
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={x_gemini_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=8)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

# Agent chat: audio -> STT -> LLM(with history & persona & tool) -> TTS
@app.post("/agent/chat/{session_id}")
async def agent_chat(
    session_id: str,
    file: UploadFile = File(...),
    x_murf_key: str = Header(None),
    x_aai_key: str = Header(None),
    x_gemini_key: str = Header(None)
):
    if not x_murf_key or not x_aai_key or not x_gemini_key:
        raise HTTPException(status_code=400, detail="Murf, AssemblyAI, and Gemini API keys are required")
    try:
        audio_data = await file.read()
        aai.settings.api_key = x_aai_key
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_data)
        user_text = transcript.text if transcript and transcript.text else ""
        if not user_text.strip():
            return JSONResponse(status_code=200, content={
                "transcription": "", "llm_text": "I couldn't hear you clearly, could you please repeat?",
                "murf_audio_url": None, "fallback": True, "fallback_text": "I couldn't hear you clearly, could you please repeat?"
            })
        # history
        history = conversation_history.setdefault(session_id, [])
        history.append({"role": "user", "content": user_text})
        # prepare messages for Gemini
        system_instruction = (
            "You are Anyra, a friendly AI voice assistant. Think internally before answering, but ONLY output the final spoken reply."
        ) + " " + PERSONA["system"]
        messages = [
            {"role": "user", "parts": [system_instruction]},
            {"role": "user", "parts": [user_text]},
        ]
        genai.configure(api_key=x_gemini_key)
        model = genai.GenerativeModel("models/gemini-1.5-flash", tools=[WEATHER_TOOL])
        first = model.generate_content(messages)
        calls = []
        for cand in getattr(first, "candidates", []) or []:
            parts = getattr(cand, "content", None)
            if not parts:
                continue
            for p in getattr(parts, "parts", []) or []:
                fc = getattr(p, "function_call", None)
                if fc: calls.append(fc)
        if calls:
            tool_parts = []
            for fc in calls:
                name = getattr(fc, "name", "")
                args = dict(getattr(fc, "args", {}) or {})
                if name == "get_weather":
                    result = get_weather(args.get("city", ""), args.get("units", "c"))
                    tool_parts.append({"function_response": {"name": name, "response": {"result": result}}})
            second = model.generate_content([*messages, first.candidates[0].content, {"role": "tool", "parts": tool_parts}])
            llm_text = (second.text or "").strip()
        else:
            llm_text = (first.text or "").strip()
        if len(llm_text) > 3000:
            llm_text = llm_text[:3000]
        history.append({"role": "assistant", "content": llm_text})
        murf_audio_url = generate_murf_audio_safe(llm_text, x_murf_key)
        return {"transcription": user_text, "llm_text": llm_text, "murf_audio_url": murf_audio_url}
    except Exception as e:
        print("[agent_chat] Error:", e)
        return JSONResponse(status_code=200, content={
            "transcription": "", "llm_text": "I'm having trouble connecting right now.",
            "murf_audio_url": None, "fallback": True, "fallback_text": "I'm having trouble connecting right now."
        })

# --- WebSocket: /ws-stt (AssemblyAI streaming receive) ---
@app.websocket("/ws-stt")
async def ws_stt(websocket: WebSocket, aai_key: str | None = Query(None)):
    await websocket.accept()
    key = aai_key or ASSEMBLYAI_API_KEY_LOCAL
    if not key:
        await websocket.send_json({"event": "error", "message": "AssemblyAI key required as query param `aai_key`"})
        await websocket.close(); return
    aai.settings.api_key = key
    q: "queue.Queue[bytes|None]" = queue.Queue()
    loop = asyncio.get_event_loop()
    def on_begin(self: StreamingClient, event: BeginEvent):
        print(f"[AAI] Session started: {event.id}")
    def on_turn(self: StreamingClient, event: TurnEvent):
        asyncio.run_coroutine_threadsafe(websocket.send_json({
            "type": "turn", "text": event.transcript, "eot": event.end_of_turn, "formatted": event.turn_is_formatted
        }), loop)
        if event.end_of_turn:
            asyncio.run_coroutine_threadsafe(websocket.send_json({"type": "turn_end", "text": event.transcript, "formatted": event.turn_is_formatted}), loop)
    def on_terminated(self: StreamingClient, event: TerminationEvent):
        print(f"[AAI] Terminated. {event.audio_duration_seconds}s processed")
    def on_error(self: StreamingClient, error: StreamingError):
        print("[AAI] Error:", error)
    client = StreamingClient(StreamingClientOptions(api_key=key, api_host="streaming.assemblyai.com"))
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)
    client.connect(StreamingParameters(sample_rate=16000, encoding="pcm_s16le", format_turns=True))
    def bytes_iter():
        while True:
            chunk = q.get()
            if chunk is None: break
            yield chunk
    t = threading.Thread(target=lambda: client.stream(bytes_iter()), daemon=True)
    t.start()
    try:
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
        try: client.disconnect(terminate=True)
        except Exception as e: print("[WS-STT] disconnect error", e)
        print("[WS-STT] closed")

# --- WebSocket: /ws-voice (full duplex) ---
@app.websocket("/ws-voice")
async def ws_voice(websocket: WebSocket):
    """
    Query params expected:
      ?murf_key=...&aai_key=...&gemini_key=...&session_id=...&city=...
    (we accept query params for WS because browser WS cannot easily set custom headers)
    """
    await websocket.accept()
    params = websocket.query_params
    murf_key = params.get("murf_key")
    aai_key = params.get("aai_key")
    gemini_key = params.get("gemini_key")
    session_id = params.get("session_id") or str(uuid.uuid4())
    user_city = params.get("city")
    if not (murf_key and aai_key and gemini_key):
        await websocket.send_json({"event": "error", "message": "murf_key, aai_key, gemini_key are required in query params"})
        await websocket.close(); return
    # configure per-connection
    aai.settings.api_key = aai_key
    try:
        genai.configure(api_key=gemini_key)
    except Exception:
        print("⚠️ Gemini configure failed for ws connection")
    conversation_history.setdefault(session_id, [])
    loop = asyncio.get_event_loop()
    q: "queue.Queue[bytes|None]" = queue.Queue()
    def ws_send_json(obj):
        asyncio.run_coroutine_threadsafe(websocket.send_json(obj), loop)
    # AAI handlers
    def on_begin(self: StreamingClient, event: BeginEvent):
        print(f"[AAI] Session started: {event.id}")
    async def _handle_turn_async(text: str, eot: bool, formatted: bool):
        await websocket.send_json({"type": "turn", "text": text, "eot": eot, "formatted": formatted})
        if not (eot and formatted and text and text.strip()):
            return
        # save user message
        conversation_history.setdefault(session_id, []).append({"role": "user", "content": text})
        # keep history short
        MAX_MESSAGES = 20
        if len(conversation_history[session_id]) > MAX_MESSAGES:
            conversation_history[session_id] = conversation_history[session_id][-MAX_MESSAGES:]
        # quick health skill
        sym = detect_symptom(text or "")
        if sym:
            wx = get_weather(user_city, "c") if user_city else None
            reply = build_health_advice(sym, wx)
            conversation_history[session_id].append({"role": "assistant", "content": reply})
            await websocket.send_json({"event": "final_text", "data": reply})
            # TTS via Murf (REST)
            audio_url = generate_murf_audio_safe(reply, murf_key)
            if not audio_url:
                await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
                return
            seq = 0
            try:
                with requests.get(audio_url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=4096):
                        if not chunk: continue
                        seq += 1
                        await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": base64.b64encode(chunk).decode()})
                await websocket.send_json({"event": "end_of_audio", "total_chunks": seq})
            except Exception as e:
                print("[MURF REST ERROR health]:", e)
                await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
            return
        # Normal LLM flow using conversation history + persona
        ai_reply = "I'm having trouble connecting right now."
        try:
            model = genai.GenerativeModel("models/gemini-1.5-flash", tools=[WEATHER_TOOL])
            messages = []
            if conversation_history[session_id]:
                messages.append({"role": "user", "parts": [PERSONA["system"]]})
            for m in conversation_history[session_id]:
                if m["role"] == "assistant":
                    messages.append({"role": "model", "parts": [m["content"]]})
                else:
                    messages.append({"role": "user", "parts": [m["content"]]})
            first = model.generate_content(messages)
            # detect function calls
            calls = []
            for cand in getattr(first, "candidates", []) or []:
                parts = getattr(cand, "content", None)
                if not parts: continue
                for p in getattr(parts, "parts", []) or []:
                    fc = getattr(p, "function_call", None)
                    if fc: calls.append(fc)
            if calls:
                tool_parts = []
                for fc in calls:
                    name = getattr(fc, "name", "")
                    args = dict(getattr(fc, "args", {}) or {})
                    if name == "get_weather":
                        result = get_weather(args.get("city", ""), args.get("units", "c"))
                        tool_parts.append({"function_response": {"name": name, "response": {"result": result}}})
                    else:
                        tool_parts.append({"function_response": {"name": name or "unknown", "response": {"result": {"error": "Tool not implemented."}}}})
                second = model.generate_content([*messages, first.candidates[0].content, {"role": "tool", "parts": tool_parts}])
                ai_reply = (second.text or "").strip()
            else:
                ai_reply = (first.text or "").strip()
        except Exception as e:
            print("[Gemini ERROR]:", e)
            ai_reply = "I'm having trouble connecting right now."
        conversation_history[session_id].append({"role": "assistant", "content": ai_reply})
        await websocket.send_json({"event": "final_text", "data": ai_reply})
        # TTS -> stream audio back
        audio_url = generate_murf_audio_safe(ai_reply, murf_key)
        if not audio_url:
            await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
            return
        seq = 0
        try:
            with requests.get(audio_url, stream=True, timeout=30) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=4096):
                    if not chunk: continue
                    seq += 1
                    await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": base64.b64encode(chunk).decode()})
            await websocket.send_json({"event": "end_of_audio", "total_chunks": seq})
        except Exception as e:
            print("[Murf REST ERROR]:", e)
            await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
    def on_turn(self: StreamingClient, event: TurnEvent):
        asyncio.run_coroutine_threadsafe(_handle_turn_async(event.transcript, event.end_of_turn, event.turn_is_formatted), loop)
    def on_terminated(self: StreamingClient, event: TerminationEvent):
        print(f"[AAI] Terminated. {event.audio_duration_seconds}s audio processed")
    def on_error(self: StreamingClient, error: StreamingError):
        print(f"[AAI] Error: {error}")
    client = StreamingClient(StreamingClientOptions(api_key=aai_key, api_host="streaming.assemblyai.com"))
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)
    client.connect(StreamingParameters(sample_rate=16000, encoding="pcm_s16le", format_turns=True))
    def bytes_iter():
        while True:
            chunk = q.get()
            if chunk is None: break
            yield chunk
    t = threading.Thread(target=lambda: client.stream(bytes_iter()), daemon=True)
    t.start()
    try:
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
        try: client.disconnect(terminate=True)
        except Exception as e: print("[/ws-voice] AAI disconnect error:", e)
        print("[/ws-voice] closed")

# --- SSE LLM stream endpoint (example) ---
@app.get("/llm/stream")
async def llm_stream(text: str = Query(..., min_length=1), x_gemini_key: str = Header(None)):
    if not x_gemini_key and not GEMINI_API_KEY_LOCAL:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY missing (header x-gemini-key or local env)")
    genai.configure(api_key=x_gemini_key or GEMINI_API_KEY_LOCAL)
    def sse():
        try:
            prompt = (
                "You are a concise, friendly voice agent. Reply as if you were speaking, no bullet points.\n\n"
                f"User: {text}\nAssistant:"
            )
            model = genai.GenerativeModel("models/gemini-1.5-flash")
            stream = model.generate_content(prompt, stream=True)
            acc = []
            for chunk in stream:
                piece = getattr(chunk, "text", None) or ""
                if not piece: continue
                acc.append(piece)
                yield f"data: {piece}\n\n"
            full = "".join(acc)
            yield f"event: done\ndata: {full}\n\n"
        except Exception as e:
            err = f"LLM stream error: {e}"
            yield f"event: error\ndata: {err}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream")

# --- day20: stream LLM tokens into Murf WebSocket (prints base64 audio in server console) ---
WS_URL = "wss://api.murf.ai/v1/speech/stream-input"
MURF_SAMPLE_RATE = 44100
MURF_CHANNEL = "MONO"
MURF_FORMAT = "MP3"

@app.post("/day20/llm-to-murf")
async def day20_llm_to_murf(
    q: str = Query(..., min_length=1),
    x_gemini_key: str = Header(None),
    x_murf_key: str = Header(None)
):
    if not x_gemini_key or not x_murf_key:
        raise HTTPException(status_code=400, detail="Murf and Gemini API keys are required from frontend")
    CONTEXT_ID = "anyra-day20"
    ws_query = (
        f"{WS_URL}?api-key={x_murf_key}"
        f"&sample_rate={MURF_SAMPLE_RATE}"
        f"&channel_type={MURF_CHANNEL}"
        f"&format={MURF_FORMAT}"
    )
    async with websockets.connect(ws_query) as ws:
        voice_config_msg = {"voice_config": {"voiceId": "en-IN-alia", "style": "Expressive", "rate": 1.1, "pitch": 1.1, "variation": 2}}
        await ws.send(json.dumps(voice_config_msg))
        async def murf_receiver():
            try:
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if "audio" in data:
                        print(f"[MURF AUDIO BASE64] {data['audio']}")
                    if data.get("final"):
                        print("[MURF ✔] final=true")
                        break
            except Exception as e:
                print("[MURF ❌ receiver error]", e)
        recv_task = asyncio.create_task(murf_receiver())
        genai.configure(api_key=x_gemini_key)
        system = "You are a concise, friendly Indian-English voice agent. Reply naturally in 1–3 sentences, no lists."
        prompt = f"{system}\n\nUser: {q}\nAssistant:"
        model = genai.GenerativeModel("models/gemini-1.5-flash")
        stream = model.generate_content(prompt, stream=True)
        buffer = ""
        async def send_text_chunk(txt: str):
            payload = {"context_id": CONTEXT_ID, "text": txt, "style": "Expressive", "rate": 1.1, "pitch": 1.1, "variation": 2}
            await ws.send(json.dumps(payload))
        def pop_sentence(buf: str):
            for p in [".", "?", "!"]:
                idx = buf.find(p)
                if idx != -1:
                    return buf[:idx+1].strip(), buf[idx+1:].lstrip()
            return None, buf
        for chunk in stream:
            token = getattr(chunk, "text", "") or ""
            if not token: continue
            buffer += token
            while True:
                sent, buffer = pop_sentence(buffer)
                if not sent: break
                await send_text_chunk(sent)
        if buffer.strip(): await send_text_chunk(buffer.strip())
        await ws.send(json.dumps({"context_id": CONTEXT_ID, "end": True}))
        await recv_task
    return {"ok": True, "note": "Check server console for base64 audio chunks."}
