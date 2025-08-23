// ----------------------------
// session handling (from URL or generated)
// ----------------------------
let urlParams = new URLSearchParams(window.location.search);
let sessionId = urlParams.get("session_id");
let isRecording = false;


if (!sessionId) {
  sessionId = crypto.randomUUID();
  urlParams.set("session_id", sessionId);
  history.replaceState({}, "", `?${urlParams.toString()}`);
}
console.log("Session ID:", sessionId);

// ----------------------------
// Voice Response with Audio Playback (Text → Murf) (unchanged)
// ----------------------------
async function sendMessage() {
  const input = document.getElementById("message");
  const text = input.value.trim();
  const audioPlayer = document.getElementById("audioPlayer");

  if (!text) {
    alert("Please type a message.");
    return;
  }

  try {
    const response = await fetch("http://127.0.0.1:8000/generate-voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, voiceId: "en-AU-joyce" })
    });

    const result = await response.json();

    if (response.ok && result.audioFile) {
      audioPlayer.src = result.audioFile;
      audioPlayer.style.display = "block";
      audioPlayer.play().catch(() => alert("Click play to hear the response."));
    } else {
      alert("Error: " + (result.detail || "Something went wrong"));
    }
  } catch (error) {
    console.error("Error:", error);
    alert("Failed to connect to backend.");
  }
}

// ----------------------------
// Eye tracking + UI small bits (unchanged)
// ----------------------------
const eyeLeft = document.getElementById("eye-left");
const eyeRight = document.getElementById("eye-right");
const aiContainer = document.getElementById("ai-container");
if (eyeLeft && eyeRight && aiContainer) {
  document.addEventListener("mousemove", (event) => {
    const rect = aiContainer.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const dx = event.clientX - centerX;
    const dy = event.clientY - centerY;
    const maxDistance = 27;
    const angle = Math.atan2(dy, dx);
    const offsetX = Math.cos(angle) * maxDistance;
    const offsetY = Math.sin(angle) * maxDistance;
    eyeLeft.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
    eyeRight.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
  });
  document.addEventListener("mouseleave", () => {
    eyeLeft.style.transform = `translate(0, 0)`;
    eyeRight.style.transform = `translate(0, 0)`;
  });
}
setTimeout(() => {
  const textEl = document.getElementById("getStartedText");
  if (textEl) textEl.classList.add("opacity-100");
}, 2000);

// ----------------------------
// Echo Bot Recording Setup
// ----------------------------
let mediaRecorder;
let recordedChunks = [];
let manualRecording = false; // differentiate manual vs auto
const AUTO_RECORD_DURATION_MS = 5000; // auto-record length (tweakable)
const echoAudio = document.getElementById("echoAudio");


let ws;
let audioCtx;
let source;
let processor;
// let isRecording = false;

async function toggleRecording() {
  if (!isRecording) {
    startRecording();
  } else {
    stopRecording();
  }
}

async function startRecording() {
  ws = new WebSocket("ws://127.0.0.1:8000/ws-stt");

  ws.onopen = async () => {
    console.log("✅ WebSocket connected, starting audio stream...");

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioCtx = new AudioContext({ sampleRate: 16000 });
    console.log("Browser sampleRate:", audioCtx.sampleRate);

    source = audioCtx.createMediaStreamSource(stream);
    processor = audioCtx.createScriptProcessor(4096, 1, 1);

    source.connect(processor);
    processor.connect(audioCtx.destination);

    processor.onaudioprocess = (e) => {
      if (!isRecording || ws.readyState !== WebSocket.OPEN) return;

      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = downsampleTo16k(input, audioCtx.sampleRate);
      console.log("PCM length:", pcm16.byteLength, "first 5 samples:", new Int16Array(pcm16).slice(0,5));
      ws.send(pcm16);

      console.log("Chunk sent:", pcm16.byteLength, "bytes");
    };

    isRecording = true;
    document.getElementById("recordBtn").textContent = "Stop";
    document.getElementById("uploadStatus").textContent = "🎙️ Listening...";
  };

  ws.onmessage = (event) => {
  try {
    const data = JSON.parse(event.data);
    if (data.type !== "turn") return;

    if (!data.eot) {
      // live interim
      appendMessage('user', data.text || "", true);
      return;
    }

    // At end-of-turn, ignore the early unformatted final.
    if (!data.formatted) return;

    // Final, punctuated text -> upgrade/create a single bubble.
    appendMessage('user', data.text || "", false);
    // ---- Day 19: start LLM streaming once we have the final transcript ----
try {
  const userFinal = data.text || "";
  const url = `http://127.0.0.1:8000/llm/stream?text=${encodeURIComponent(userFinal)}`;

  // EventSource = dead-simple SSE client (GET only, perfect for this use case)
  const es = new EventSource(url);

  let full = "";
  es.onmessage = (ev) => {
    // prints partial chunks as they arrive
    console.log("[LLM][chunk]", ev.data);
    full += ev.data;
  };

  es.addEventListener("done", (ev) => {
    // prints the final accumulated response once
    console.log("[LLM][final]", ev.data || full);
    es.close();
  });

  es.onerror = (err) => {
    console.error("[LLM][error]", err);
    es.close();
  };
} catch (e) {
  console.error("Failed to start LLM stream", e);
}

  } catch (e) {
    console.error("Non-JSON WS msg:", event.data);
  }
};



}


function stopRecording() {
  if (processor) processor.disconnect();
  if (source) source.disconnect();
  if (audioCtx) audioCtx.close();

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send("DONE");
    ws.close();
  }

  isRecording = false;
  document.getElementById("recordBtn").textContent = "Start";
  document.getElementById("uploadStatus").textContent = "Stopped";
}

function floatTo16BitPCM(float32Array) {
  let buffer = new ArrayBuffer(float32Array.length * 2);
  let view = new DataView(buffer);
  for (let i = 0; i < float32Array.length; i++) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}







function playFallbackVoice(text) {
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'en-IN'; // You can change the language if needed
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
    } else {
        alert(text);
    }
}


function appendMessage(sender, text, interim = false) {
  const chatContainer = document.getElementById('chatContainer');

  // If interim bubble exists, update it
  if (interim) {
    let lastBubble = chatContainer.querySelector('.message.interim');
    if (!lastBubble) {
      // create interim bubble
      lastBubble = document.createElement('div');
      lastBubble.className = `message interim flex justify-end`;
      lastBubble.innerHTML = `
        <div class="bg-cyan-600/70 italic text-white rounded-2xl rounded-br-none px-4 py-2 max-w-xs border border-cyan-400 text-sm mb-1">
          ${text}
        </div>`;
      chatContainer.appendChild(lastBubble);
    } else {
      // update text of interim bubble
      lastBubble.querySelector("div").textContent = text;
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return;
  }

  // ✅ Finalize interim instead of creating a new one
  let interimBubble = chatContainer.querySelector('.message.interim');
  if (interimBubble) {
    interimBubble.classList.remove("interim");
    const bubble = interimBubble.querySelector("div");
    bubble.classList.remove("italic", "bg-cyan-600/70", "border", "border-cyan-400");
    bubble.classList.add("bg-cyan-600", "shadow");
    bubble.innerHTML = `
      <span class="font-medium">${text}</span>
      <div class="text-xs text-gray-200 mt-1 text-right">${new Date().toLocaleTimeString()}</div>
    `;
  } else {
    // fallback: if no interim, create a final bubble
    const messageDiv = document.createElement('div');
    messageDiv.className = "message flex justify-end";
    messageDiv.innerHTML = `
      <div class="bg-cyan-600 text-white rounded-2xl rounded-br-none px-4 py-2 max-w-xs shadow text-sm mb-1">
        <span class="font-medium">${text}</span>
        <div class="text-xs text-gray-200 mt-1 text-right">${new Date().toLocaleTimeString()}</div>
      </div>`;
    chatContainer.appendChild(messageDiv);
  }

  chatContainer.scrollTop = chatContainer.scrollHeight;
}




async function sendToLLM(blob) {
    const formData = new FormData();
    formData.append("file", blob, "recording.webm");

    try {
        const response = await fetch(`http://127.0.0.1:8000/agent/chat/${sessionId}`, {
            method: "POST",
            body: formData
        });

        const result = await response.json();

        if (response.ok) {
            // Show user's spoken text on the right
            if (result.transcription) {
                appendMessage('user', result.transcription);
            }

            // Show AI's reply on the left
            if (result.llm_text) {
                appendMessage('ai', result.llm_text);
            }

            // Handle voice playback
            if (result.murf_audio_url) {
                echoAudio.src = result.murf_audio_url;

                // Add speaking effect
                document.getElementById('ai-container').classList.add('speaking');

                await echoAudio.play();
                document.getElementById('uploadStatus').textContent = "Audio played successfully.";

                // Remove speaking effect when audio ends
                echoAudio.onended = () => {
                    document.getElementById('ai-container').classList.remove('speaking');
                    setTimeout(() => startRecording(false), 300);
                };

            } else if (result.fallback_text) {
                // Fallback to browser voice
                playFallbackVoice(result.fallback_text);
                document.getElementById('uploadStatus').textContent = "Fallback voice played.";
            }

        } else {
            const errorMsg = result.detail || "Error occurred.";
            appendMessage('ai', errorMsg);
        }

    } catch (err) {
        appendMessage('ai', "Connection error. Please try again.");
    }
}


let stt = {
  ws: null,
  ctx: null,
  node: null,
  media: null,
  started: false,
};

async function startSTT() {
  if (stt.started) return;
  stt.started = true;

  // Connect WebSocket to our FastAPI endpoint
  // stt.ws = new WebSocket(`ws://${location.hostname}:8000/ws-stt`);
  // stt.ws.onmessage = (evt) => {
  //   try {
  //     const data = JSON.parse(evt.data);
  //     if (data?.type === "transcript") {
  //       const el = document.getElementById("live-transcript");
  //       if (el) el.textContent += (data.text || "") + (data.eot ? "\n" : " ");
  //     }
  //   } catch (_) {}
  // };

  // 16 kHz audio context
  stt.ctx = new (window.AudioContext || window.webkitAudioContext)({
    sampleRate: 16000,
  });

  // Create a worklet on the fly (no extra file needed)
  const workletCode = `
  class PCM16Processor extends AudioWorkletProcessor {
    constructor() {
      super();
      this._buf = [];
      this._samplesPerPacket = 0 | (0.05 * 16000); // ~50 ms
    }
    process(inputs) {
      const input = inputs[0];
      if (!input || !input[0]) return true;
      const ch0 = input[0]; // Float32Array
      const n = ch0.length;

      // Convert Float32 [-1,1] -> Int16LE bytes
      // Accumulate until ~50ms worth, then post to main thread
      const bytes = new Uint8Array(n * 2);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < n; i++) {
        let s = Math.max(-1, Math.min(1, ch0[i]));
        const v = s < 0 ? s * 0x8000 : s * 0x7FFF;
        view.setInt16(i * 2, v, true); // little-endian
      }
      this._buf.push(bytes);

      // How many samples are buffered?
      let samples = 0;
      for (const b of this._buf) samples += (b.byteLength >> 1);
      if (samples >= this._samplesPerPacket) {
        let total = 0;
        for (const b of this._buf) total += b.byteLength;
        const out = new Uint8Array(total);
        let off = 0;
        for (const b of this._buf) { out.set(b, off); off += b.byteLength; }
        this._buf = [];
        this.port.postMessage(out);
      }
      return true;
    }
  }
  registerProcessor('pcm16-processor', PCM16Processor);
  `;
  const blob = new Blob([workletCode], { type: "application/javascript" });
  const url = URL.createObjectURL(blob);
  await stt.ctx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);

  // Mic
  stt.media = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const src = stt.ctx.createMediaStreamSource(stt.media);
  stt.node = new AudioWorkletNode(stt.ctx, "pcm16-processor");
  stt.node.port.onmessage = (e) => {
    if (stt.ws && stt.ws.readyState === 1) {
      stt.ws.send(e.data); // binary Uint8Array
    }
  };

  // Do NOT connect to destination to avoid echo
  src.connect(stt.node);
  // (no node.connect(ctx.destination))

  document.getElementById("sttStatus")?.classList.remove("hidden");
}

async function stopSTT() {
  if (!stt.started) return;
  stt.started = false;

  try { stt.ws?.send("DONE"); } catch(_){}
  try { stt.ws?.close(); } catch(_){}
  stt.ws = null;

  try { stt.node?.disconnect(); } catch(_){}
  stt.node = null;

  try { stt.media?.getTracks().forEach(t => t.stop()); } catch(_){}
  stt.media = null;

  try { await stt.ctx?.close(); } catch(_){}
  stt.ctx = null;

  document.getElementById("sttStatus")?.classList.add("hidden");
}

// Expose to buttons if you like:
window.startSTT = startSTT;
window.stopSTT = stopSTT;


function downsampleTo16k(float32Array, inputSampleRate) {
  if (inputSampleRate === 16000) {
    return floatTo16BitPCM(float32Array);
  }

  const ratio = inputSampleRate / 16000;
  const newLength = Math.round(float32Array.length / ratio);
  const result = new Float32Array(newLength);

  let offset = 0;
  for (let i = 0; i < newLength; i++) {
    result[i] = float32Array[Math.floor(i * ratio)];
  }

  return floatTo16BitPCM(result);
}



// --- Quick mic test with MediaRecorder ---
async function testMicRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream);
    const chunks = [];

    recorder.ondataavailable = (e) => chunks.push(e.data);

    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: "audio/webm" });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.controls = true;
      document.body.appendChild(audio);
      audio.play();
      console.log("✅ Mic test recording ready, length:", blob.size, "bytes");
    };

    recorder.start();
    console.log("🎙️ Recording for 3 seconds...");
    setTimeout(() => recorder.stop(), 3000);
  } catch (err) {
    console.error("Mic test failed:", err);
  }
}




// ----------------------------
// Day 21: Receive server->client base64 audio via WS
// ----------------------------
let wsAudioOut = null;
let audioChunksB64 = [];  // collected base64 chunks

function startBase64Stream() {
  // IMPORTANT: MP3 file stream karo, WAV nahi (MSE compatibility)
  const wsUrl = new URL("ws://127.0.0.1:8000/ws-audio-out");
  wsUrl.searchParams.set("path", "sample.mp3"); // <-- yahan apni mp3 ka naam
  wsUrl.searchParams.set("chunk", "16384");      // bigger chunks -> smoother playback (tweakable)

  const player = createMSEPlayer('audio/mpeg');
  const audioEl = player.element;

  wsAudioOut = new WebSocket(wsUrl.toString());

  wsAudioOut.onopen = () => {
    console.log("🔗 [Day22] Connected to /ws-audio-out");
    // try autoplay; agar blocked ho to user click pe play ho jayega
    audioEl.play().catch(() => console.log('Autoplay blocked—click Play.'));
  };

  wsAudioOut.onmessage = (event) => {
    const data = event.data;

    if (data === "END") {
      console.log("🏁 [Day22] Stream complete");
      player.end();
      return;
    }
    if (typeof data === "string" && data.startsWith("ERROR:")) {
      console.error("[Day22] Server error:", data);
      return;
    }

    // Base64 -> bytes -> append
    try {
  const u8 = b64ToU8(data);
  console.log("Received chunk, length:", u8.length);
  player.appendChunk(u8);
} catch (e) {
  console.error("Chunk append failed:", e);
}

  };

  wsAudioOut.onerror = (err) => console.error("[Day22] WS error", err);
  wsAudioOut.onclose = () => console.log("[Day22] WS closed");
}


// (Optional) If you want to verify by actually hearing the audio,
// you can reconstruct a Blob and play it in your hidden <audio id="audioPlayer"> element.
function reconstructAndPlayFromB64(chunks) {
  try {
    // Convert joined base64 -> ArrayBuffer
    const b64 = chunks.join("");
    const byteString = atob(b64);
    const buf = new ArrayBuffer(byteString.length);
    const view = new Uint8Array(buf);
    for (let i = 0; i < byteString.length; i++) view[i] = byteString.charCodeAt(i);

    const blob = new Blob([buf], { type: "audio/wav" }); // or audio/mpeg if mp3
    const url = URL.createObjectURL(blob);
    const audioEl = document.getElementById("audioPlayer");
    audioEl.src = url;
    audioEl.style.display = "block";
    console.log("▶️ [Day21] Reconstructed audio Blob, ready to play");
    audioEl.play().catch(() => console.log("Click play to listen."));
  } catch (e) {
    console.error("Failed to reconstruct audio:", e);
  }
}



// --- Day 22: MediaSource-based streaming audio player ---
function createMSEPlayer(mime = 'audio/mpeg') {
  const audioEl = document.getElementById('audioPlayer');
  audioEl.style.display = 'block';

  const mediaSource = new MediaSource();
  const url = URL.createObjectURL(mediaSource);
  audioEl.src = url;

  let sourceBuffer;
  let queue = [];
  let endWhenEmpty = false;

  function appendChunk(u8) {
    if (!sourceBuffer || sourceBuffer.updating) {
      queue.push(u8);
      return;
    }
    try {
      sourceBuffer.appendBuffer(u8);
    } catch (e) {
      console.error('appendBuffer failed', e);
    }
  }

  mediaSource.addEventListener('sourceopen', () => {
    if (!MediaSource.isTypeSupported(mime)) {
      console.error('MIME not supported for MSE:', mime);
      return;
    }
    sourceBuffer = mediaSource.addSourceBuffer(mime);

    sourceBuffer.addEventListener('updateend', () => {
      if (queue.length > 0 && !sourceBuffer.updating) {
        const next = queue.shift();
        try { sourceBuffer.appendBuffer(next); } catch (e) { console.error(e); }
        console.log("Buffered:", sourceBuffer.buffered.length > 0 
  ? sourceBuffer.buffered.end(0).toFixed(2) + "s"
  : "no buffer");

      } else if (endWhenEmpty && !sourceBuffer.updating && queue.length === 0) {
        try { mediaSource.endOfStream(); } catch (_) {}
      }
    });
  });

  return {
    element: audioEl,
    appendChunk,
    end: () => { endWhenEmpty = true; },
  };
}

function b64ToU8(b64) {
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8;
}


async function startMicStream() {
  const ws = new WebSocket("ws://127.0.0.1:8000/ws-mic");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const audioContext = new AudioContext();
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);

  source.connect(processor);
  processor.connect(audioContext.destination);

  processor.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0); // Float32
    // convert to 16-bit PCM
    let buffer = new ArrayBuffer(inputData.length * 2);
    let view = new DataView(buffer);
    let offset = 0;
    for (let i = 0; i < inputData.length; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, inputData[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    ws.send(buffer);
  };

  ws.onopen = () => console.log("Mic stream started");
  ws.onclose = () => console.log("Mic stream closed");
}
