console.log("Loading script.js...");

let wsVoice;
let voiceCtx, voiceSrc, voiceProc;
let isRecording = false;
let sessionId = crypto.randomUUID();
console.log("Session ID:", sessionId);

let audioElement;
let mediaSource;
let sourceBuffer;
let queue = [];
let isAppending = false;
let audioChunksB64 = [];
let aiStreamBubbleEl;

// --- Streaming Audio Setup ---
function setupStreamingAudio() {
  audioElement = document.getElementById("audioPlayer");
  if (!audioElement) {
    console.error("❌ audioPlayer element not found");
    return;
  }
  mediaSource = new MediaSource();
  queue = [];
  isAppending = false;

  audioElement.src = URL.createObjectURL(mediaSource);

  mediaSource.addEventListener("sourceopen", () => {
    try {
      sourceBuffer = mediaSource.addSourceBuffer("audio/mpeg");
      sourceBuffer.mode = "sequence";

      sourceBuffer.addEventListener("updateend", () => {
        isAppending = false;
        if (queue.length > 0) appendNextChunk();
      });

      audioElement
        .play()
        .then(() => console.log("Playback started"))
        .catch((err) => console.error("Autoplay blocked:", err));
    } catch (err) {
      console.error("Error creating SourceBuffer:", err);
    }
  });

  audioElement.style.display = "block";
}

function appendNextChunk() {
  if (!sourceBuffer || isAppending || queue.length === 0) return;
  isAppending = true;
  const chunk = queue.shift();
  try {
    sourceBuffer.appendBuffer(chunk);
  } catch (err) {
    console.error("appendBuffer failed:", err);
    isAppending = false;
    // fallback: play full blob
    if (queue.length > 0) {
      const blob = new Blob(queue, { type: "audio/mpeg" });
      const url = URL.createObjectURL(blob);
      audioElement.src = url;
      audioElement.play().catch(() => {});
      console.warn("Fallback single-chunk playback started due to append error");
    }
  }
}

// --- Chunk Playback ---
function playStreamingChunk(chunk) {
  let uint8;

  // If it's a base64 string
  if (typeof chunk === "string") {
    const maybeData = chunk.includes(",") ? chunk.split(",")[1] : chunk;
    const cleaned = maybeData.replace(/\s+/g, "");

    if (!/^[A-Za-z0-9+/=]*$/.test(cleaned)) {
      console.warn("Skipping invalid base64 chunk:", chunk);
      return;
    }
    try {
      const binary = atob(cleaned);
      uint8 = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) uint8[i] = binary.charCodeAt(i);
    } catch (e) {
      console.error("Invalid base64 chunk:", e);
      return;
    }
  } else if (chunk instanceof ArrayBuffer) {
    uint8 = new Uint8Array(chunk);
  } else if (chunk instanceof Uint8Array) {
    uint8 = chunk;
  } else {
    console.error("Unsupported chunk type:", typeof chunk, chunk);
    return;
  }

  queue.push(uint8);
  if (!isAppending) appendNextChunk();
}

// --- Chat UI helpers ---
function appendMessage(sender, text, pending = false) {
  const c = document.getElementById("chatContainer");
  if (!c) return;
  const div = document.createElement("div");
  div.className =
    sender === "user"
      ? "bg-blue-600 p-3 rounded-lg text-sm"
      : "bg-gray-700 p-3 rounded-lg text-sm";
  div.textContent = text;
  if (pending) div.classList.add("italic");
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function appendAIStreamBubbleStart() {
  const c = document.getElementById("chatContainer");
  if (!c) return;
  aiStreamBubbleEl = document.createElement("div");
  aiStreamBubbleEl.className = "bg-gray-700 p-3 rounded-lg text-sm";
  aiStreamBubbleEl.textContent = "AI is speaking…";
  c.appendChild(aiStreamBubbleEl);
  c.scrollTop = c.scrollHeight;
}
function updateAIStreamBubble(n) {
  if (aiStreamBubbleEl)
    aiStreamBubbleEl.textContent = `AI speaking… chunks: ${n}`;
}
function appendAIStreamBubbleFinalize() {
  if (aiStreamBubbleEl) aiStreamBubbleEl.textContent += " ✓";
}
function handleReceivedChunk(b64, seq) {
  audioChunksB64.push(b64);
  updateAIStreamBubble(audioChunksB64.length);
}
function reconstructAndPlayFromB64(chunksB64) {
  const byteArrays = chunksB64.map((b64) =>
    Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  );
  const blob = new Blob(byteArrays, { type: "audio/mpeg" });
  audioElement.src = URL.createObjectURL(blob);
  audioElement.play().catch(() => {});
  console.log("Full reconstructed playback started");
}

// --- Recording and WebSocket Flow ---
function startFullFlow() {
  if (wsVoice && wsVoice.readyState === WebSocket.OPEN) {
    console.log("Already running");
    return;
  }

  const rightFrame = document.getElementById("rightFrame");
  if (rightFrame && rightFrame.classList.contains("hidden")) toggleChat();

  const url = new URL("ws://127.0.0.1:8000/ws-voice");
  url.searchParams.set("session_id", sessionId);

  wsVoice = new WebSocket(url);
  wsVoice.binaryType = "arraybuffer";

  wsVoice.onopen = async () => {
    console.log("✅ /ws-voice connected");
    document.getElementById("recordBtn").textContent = "Stop Assistant";
    document.getElementById("uploadStatus").textContent = "🎙️ Listening...";

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    voiceCtx = new AudioContext({ sampleRate: 16000 });
    voiceSrc = voiceCtx.createMediaStreamSource(stream);
    voiceProc = voiceCtx.createScriptProcessor(4096, 1, 1);

    voiceProc.onaudioprocess = (e) => {
      if (!wsVoice || wsVoice.readyState !== WebSocket.OPEN) return;
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = downsampleTo16k(input, voiceCtx.sampleRate);
      wsVoice.send(pcm16);
    };

    voiceSrc.connect(voiceProc);
    voiceProc.connect(voiceCtx.destination);
    isRecording = true;
  };

  wsVoice.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);

      if (data.type === "turn") {
        const { text, eot, formatted } = data;

        if (!eot) {
          appendMessage("user", text || "", true);
          return;
        }
        if (!formatted) return;

        appendMessage("user", text || "", false);

        appendAIStreamBubbleStart();
        setupStreamingAudio();
        audioChunksB64 = [];
        return;
      }

      if (data.event === "audio_chunk") {
        handleReceivedChunk(data.data, data.seq);
        playStreamingChunk(data.data);
        return;
      }

      if (data.event === "end_of_audio") {
        appendAIStreamBubbleFinalize();
        try {
          reconstructAndPlayFromB64(audioChunksB64);
        } catch (err) {
          console.warn("Stream playback failed, using fallback:", err);
          if (audioChunksB64.length > 0) {
            const byteArrays = audioChunksB64.map((b64) => {
              const binary = atob(b64);
              const len = binary.length;
              const buffer = new Uint8Array(len);
              for (let i = 0; i < len; i++) buffer[i] = binary.charCodeAt(i);
              return buffer;
            });
            const blob = new Blob(byteArrays, { type: "audio/mpeg" });
            const url = URL.createObjectURL(blob);
            audioElement.src = url;
            audioElement.play().catch((err) =>
              console.error("Fallback autoplay blocked:", err)
            );
            console.log("Fallback full reply playback started");
          }
        }
        return;
      }

      if (data.event === "error") {
        appendMessage("ai", "Audio stream error: " + (data.message || ""));
        return;
      }
    } catch (e) {
      console.warn("Non-JSON message:", event.data);
    }
  };

  wsVoice.onclose = () => {
    cleanupVoice();
    document.getElementById("uploadStatus").textContent = "Socket closed";
  };
  wsVoice.onerror = (err) => {
    console.error("ws-voice error", err);
    appendMessage("ai", "Connection error.");
  };
}

function stopRecording() {
  if (voiceProc) try { voiceProc.disconnect(); } catch (_) {}
  if (voiceSrc) try { voiceSrc.disconnect(); } catch (_) {}
  if (voiceCtx && voiceCtx.state !== "closed") {
    voiceCtx.close().catch(() => {});
  }

  if (wsVoice && wsVoice.readyState === WebSocket.OPEN) {
    try { wsVoice.send("DONE"); } catch (_) {}
    wsVoice.close();
  }
  isRecording = false;
  document.getElementById("recordBtn").textContent = "Start Assistant";
  document.getElementById("uploadStatus").textContent = "Stopped";
}

function cleanupVoice() {
  try { voiceProc && voiceProc.disconnect(); } catch (_) {}
  try { voiceSrc && voiceSrc.disconnect(); } catch (_) {}
  if (voiceCtx && voiceCtx.state !== "closed") {
    voiceCtx.close().catch(() => {});
  }
  voiceProc = voiceSrc = voiceCtx = null;
  wsVoice = null;
}

// --- Downsampling helper ---
function downsampleTo16k(float32Array, inputSampleRate) {
  if (inputSampleRate === 16000) return float32ToPCM16(float32Array);

  const sampleRateRatio = inputSampleRate / 16000;
  const newLength = Math.round(float32Array.length / sampleRateRatio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    let accum = 0,
      count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < float32Array.length; i++) {
      accum += float32Array[i];
      count++;
    }
    result[offsetResult] = accum / count;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return float32ToPCM16(result);
}
function float32ToPCM16(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

// --- Record button listener ---
document.getElementById("recordBtn").addEventListener("click", () => {
  if (!isRecording) {
    startFullFlow();
  } else {
    stopRecording();
  }
});
