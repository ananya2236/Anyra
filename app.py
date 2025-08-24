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

app = FastAPI()



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
    loop = asyncio.get_event_loop()
    q: "queue.Queue[bytes|None]" = queue.Queue()

    # --- helper: send JSON safely from threads ---
    def ws_send_json(obj):
        asyncio.run_coroutine_threadsafe(websocket.send_json(obj), loop)

    # --- AAI event handlers (same spirit as /ws-stt) ---
    def on_begin(self: StreamingClient, event: BeginEvent):
        print(f"[AAI] Session started: {event.id}")

    async def _handle_turn_async(text: str, eot: bool, formatted: bool):
        # 1) Always stream transcript to client
        await websocket.send_json({"type": "turn", "text": text, "eot": eot, "formatted": formatted})

        # 2) If end-of-turn + formatted => run LLM -> Murf streaming
        if eot and formatted and text.strip():
            # maintain chat history
            history = chat_sessions.setdefault(session_id, [])
            history.append({"role": "user", "content": text})

            # Build structured messages for Gemini
            
            # Convert history into Gemini-compatible messages
            messages = [
                {"role": "user", "parts": ["You are a friendly AI voice assistant. "
                                           "Reply as if speaking, in 1–3 concise sentences. No lists."]}
            ]
            
            for msg in history:
                if msg["role"] == "user":
                    messages.append({"role": "user", "parts": [msg["content"]]})
                else:  # assistant
                    messages.append({"role": "model", "parts": [msg["content"]]})
            
            
            # LLM
            if GEMINI_API_KEY:
                try:
                    model = genai.GenerativeModel("models/gemini-1.5-flash")
                    llm_resp = model.generate_content(messages)
                    reply = (llm_resp.text or "").strip()
                except Exception as e:
                    print("[/ws-voice] Gemini error:", e)
                    reply = "I'm having trouble connecting right now."
            else:
                reply = "I'm having trouble connecting right now."

            if len(reply) > 3000:
                reply = reply[:3000]

            # keep assistant reply in history
            history.append({"role": "assistant", "content": reply})

            # --- Stream TTS via Murf WS and forward chunks to browser ---
            if not MURF_API_KEY:
                await websocket.send_json({"event": "end_of_audio", "total_chunks": 0})
                return

            WS = (
                f"{WS_URL}?api-key={MURF_API_KEY}"
                f"&sample_rate={MURF_SAMPLE_RATE}"
                f"&channel_type={MURF_CHANNEL}"
                f"&format={MURF_FORMAT}"
            )

            async def tts_stream():
                seq = 0
                try:
                    async with websockets.connect(WS) as ws_murf:
                        # 1) voice config
                        vc = {
                            "voice_config": {
                                "voiceId": "en-IN-alia",
                                "style": "Conversational",
                                "rate": 0,
                                "pitch": 0,
                                "variation": 1,
                            }
                        }
                        await ws_murf.send(json.dumps(vc))

                        # 2) send reply text in sentence chunks
                        def split_sents(s: str):
                            parts, buf = [], s.strip()
                            while True:
                                idxs = [buf.find("."), buf.find("?"), buf.find("!")]
                                idxs = [i for i in idxs if i != -1]
                                idx = min(idxs) if idxs else -1
                                if idx == -1:
                                    break
                                parts.append(buf[:idx + 1].strip())
                                buf = buf[idx + 1:].lstrip()
                            if buf:
                                parts.append(buf)
                            return parts

                        for sent in split_sents(reply):
                            await ws_murf.send(json.dumps({"context_id": session_id, "text": sent}))

                        await ws_murf.send(json.dumps({"context_id": session_id, "end": True}))

                        # 3) forward Murf audio to browser
                        while True:
                            msg = await ws_murf.recv()
                            data = json.loads(msg)
                            if "audio" in data:
                                seq += 1
                                await websocket.send_json({"event": "audio_chunk", "seq": seq, "data": data["audio"]})
                            if data.get("final"):
                                break

                except Exception as e:
                    print("[/ws-voice] Murf stream error:", e)
                    try:
                        await websocket.send_json({"event": "error", "message": str(e)})
                    except Exception:
                        pass
                finally:
                    try:
                        await websocket.send_json({"event": "end_of_audio", "total_chunks": seq})
                    except Exception:
                        pass

            asyncio.create_task(tts_stream())



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
                "style": "Conversational",
                "rate": 0,
                "pitch": 0,
                "variation": 1
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
            payload = {"context_id": CONTEXT_ID, "text": txt}
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


