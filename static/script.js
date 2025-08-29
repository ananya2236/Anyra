document.addEventListener("mousemove", (e) => {
  const container = document.getElementById("ai-container");
  const eyeLeft = document.getElementById("eye-left");
  const eyeRight = document.getElementById("eye-right");

  const rect = container.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;

  // Cursor offset relative to AI container
  const offsetX = e.clientX - centerX;
  const offsetY = e.clientY - centerY;

  // Limit max eye movement
  const maxOffset = 30; 
  const moveX = Math.max(Math.min(offsetX / 10, maxOffset), -maxOffset);
  const moveY = Math.max(Math.min(offsetY / 10, maxOffset), -maxOffset);

  // Apply transform to each eye
  eyeLeft.style.transform = `translate(${moveX}px, ${moveY}px)`;
  eyeRight.style.transform = `translate(${moveX}px, ${moveY}px)`;
});




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
let persona = "futuristic-ai";
const personaSel = document.getElementById("personaSelect");
const greetingEl = document.getElementById("ai-greeting");
const aiBall = document.getElementById("ai-container");

if (personaSel) {
  personaSel.addEventListener("change", () => {
    persona = personaSel.value;
    // UI touch: greeting update
    const labels = {
      "futuristic-ai": "Hello! I'm Anyra — Your Futuristic AI ✨",
      "pirate": "Ahoy! I'm Anyra the Pirate ☠️",
      "cowboy": "Howdy! I'm Anyra the Cowboy 🤠",
      "robot": "Greetings. Anyra Robot online. 🤖",
      "professor": "Good day. Professor Anyra here. 👩‍🏫",
      "desi-mentor": "Namaste! Main hoon Anyra, aapki Desi Mentor. 🙏"
    };
    if (greetingEl) greetingEl.textContent = labels[persona] || "Hello! I'm Anyra";
  });
}


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

let isStreaming = false;

// --- Chunk Playback ---
function playStreamingChunk(chunk) {
  // console.log("🔊 Streaming playback triggered");
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
function appendMessage(sender, text, pending = false, replace = false) {
  const c = document.getElementById("chatContainer");
  if (!c) return;

  let div;
  if (replace && c.lastChild && c.lastChild.dataset.sender === sender) {
    div = c.lastChild;
    div.textContent = text;
  } else {
    div = document.createElement("div");
    div.dataset.sender = sender;

    // Bubble base style
    div.className = "px-4 py-2 text-sm max-w-[75%] break-words shadow-md";

    if (sender === "user") {
      div.classList.add(
        "bg-blue-600",
        "text-white",
        "self-end",
        "ml-auto",
        "rounded-2xl",
        "rounded-tr-md",
        "text-right"
      );
      div.textContent = text; // User text shows instantly
    } else {
      div.classList.add(
        "bg-gray-700",
        "text-white",
        "self-start",
        "mr-auto",
        "rounded-2xl",
        "rounded-tl-md",
        "text-left"
      );

      // AI typing effect
      let i = 0;
      function typeWriter() {
        if (i < text.length) {
          div.textContent += text.charAt(i);
          i++;
          setTimeout(typeWriter, 30); // speed (ms) per character
        }
      }
      typeWriter();
    }

    if (pending) div.classList.add("italic");
    c.appendChild(div);
  }

  c.scrollTop = c.scrollHeight;
}



// function appendAIStreamBubbleStart() {
//   const c = document.getElementById("chatContainer");
//   if (!c) return;
//   aiStreamBubbleEl = document.createElement("div");
//   aiStreamBubbleEl.className = "bg-gray-700 p-3 rounded-lg text-sm";
//   aiStreamBubbleEl.textContent = "AI is speaking…";
//   c.appendChild(aiStreamBubbleEl);
//   c.scrollTop = c.scrollHeight;
// }
// function updateAIStreamBubble(n) {
//   if (aiStreamBubbleEl)
//     aiStreamBubbleEl.textContent = `AI speaking… chunks: ${n}`;
// }
// function appendAIStreamBubbleFinalize() {
//   if (aiStreamBubbleEl) aiStreamBubbleEl.textContent += " ✓";
// }

function appendAIStreamBubbleStart() {
  // no need to display anything while streaming
  aiStreamBubbleEl = null;
}

function updateAIStreamBubble(n) {
  // do nothing
}

function appendAIStreamBubbleFinalize(replyText) {
  const c = document.getElementById("chatContainer");
  if (!c) return;
  const div = document.createElement("div");
  div.className = "bg-gray-700 p-3 rounded-lg text-sm";
  div.textContent = replyText;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function handleReceivedChunk(b64, seq) {
  audioChunksB64.push(b64);
  updateAIStreamBubble(audioChunksB64.length);
}
function reconstructAndPlayFromB64(chunksB64) {
  console.log("🎵 Full audio reconstruction triggered");
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

  const murfKey = localStorage.getItem("MURF_API_KEY");
  const aaiKey = localStorage.getItem("ASSEMBLYAI_API_KEY");
  const geminiKey = localStorage.getItem("GEMINI_API_KEY");

  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(`${wsProtocol}://${window.location.host}/ws-voice`);

  url.searchParams.set("session_id", sessionId);
  url.searchParams.set("persona", persona); 

  url.searchParams.set("murf_key", murfKey);
  url.searchParams.set("aai_key", aaiKey);
  url.searchParams.set("gemini_key", geminiKey);

  const cityEl = document.getElementById("cityInput");
  const city = cityEl && cityEl.value ? cityEl.value.trim() : "";
  if (city) {
    localStorage.setItem("anyra_city", city);
    url.searchParams.set("city", city);
  } else {
    const cached = localStorage.getItem("anyra_city");
    if (cached) url.searchParams.set("city", cached);
  } 

  


  wsVoice = new WebSocket(url);
  wsVoice.binaryType = "arraybuffer";

  wsVoice.onopen = async () => {
    console.log("✅ /ws-voice connected");
    document.getElementById("recordBtn").textContent = "Stop";
    document.getElementById("uploadStatus").textContent = " Listening...";

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
    // console.log("WS EVENT:", event.data);
    const data = JSON.parse(event.data);

    // 🎙️ Handle transcription turns
    if (data.type === "turn") {
      const { text, eot, formatted } = data;
    
      if (!formatted) {
        // 📝 update the same bubble while speaking
        appendMessage("user", text || "", true, true);  
        return;
      }
    
      if (formatted) {
        // ✅ replace live bubble with final version
        appendMessage("user", text || "", false, true); 
        showTypingIndicator();
        return;
      }
    }



    // 💬 Handle AI final text
    if (data.event === "final_text") {
      removeTypingIndicator();
      appendMessage("ai", data.data, false);  // show AI reply in chat
      wsVoice.lastReplyText = data.data;      // store for reference
      return;
    }

    // 🔊 Handle AI audio chunks (streaming playback only)
      
    if (data.event === "audio_chunk") {
      if (aiBall && !aiBall.classList.contains("speaking")) {
        aiBall.classList.add("speaking");
      }
    
      if (!mediaSource || !sourceBuffer) {
        setupStreamingAudio();
        console.log("🎧 Audio pipeline initialized for first reply");
      }
    
      handleReceivedChunk(data.data, data.seq);
      playStreamingChunk(data.data);
    
      // ✅ Log only once per sequence
      if (!isStreaming) {
        console.log("🔊 Streaming playback started...");
        isStreaming = true;
      }
      return;
    }
    
    if (data.event === "end_of_audio") {
      if (aiBall) aiBall.classList.remove("speaking");
      if (isStreaming) {
        console.log("✅ End of audio received, streaming complete.");
        isStreaming = false;
      }
      return;
    }
    
    


    // ❌ Handle errors
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

document.getElementById("recordBtn").addEventListener("click", () => {
  if (!isRecording) {
    const murfKey = localStorage.getItem("MURF_API_KEY");
    const aaiKey = localStorage.getItem("ASSEMBLYAI_API_KEY");
    const geminiKey = localStorage.getItem("GEMINI_API_KEY");

    if (!murfKey || !aaiKey || !geminiKey) {
      alert("⚠️ Please enter all API keys before starting!");
      document.getElementById("settingsModal").classList.remove("hidden"); 
      return;
    }

    startFullFlow();
  } else {
    stopRecording();
  }
});



function showTypingIndicator() {
  const c = document.getElementById("chatContainer");
  let div = document.createElement("div");
  div.id = "typing-indicator";
  div.dataset.sender = "ai";
  div.className = "bg-gray-700 text-white self-start mr-auto rounded-2xl rounded-tl-md px-4 py-2 text-sm max-w-[75%] flex gap-1";
  div.innerHTML = `<span class="dot w-2 h-2 bg-white rounded-full animate-bounce"></span>
                   <span class="dot w-2 h-2 bg-white rounded-full animate-bounce delay-150"></span>
                   <span class="dot w-2 h-2 bg-white rounded-full animate-bounce delay-300"></span>`;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function removeTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}


function typeWriterEffect(element, text, speed = 40) {
  let i = 0;
  element.textContent = ""; // clear first
  function typing() {
    if (i < text.length) {
      element.textContent += text.charAt(i);
      i++;
      setTimeout(typing, speed);
    }
  }
  typing();
}


window.addEventListener("DOMContentLoaded", () => {
  const cityEl = document.getElementById("cityInput");
  if (cityEl) cityEl.value = localStorage.getItem("anyra_city") || "";
});


// --- Settings Modal Logic ---
const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeModal = document.getElementById("closeModal");
const saveKeysBtn = document.getElementById("saveKeysBtn");

settingsBtn.addEventListener("click", () => settingsModal.classList.remove("hidden"));
closeModal.addEventListener("click", () => settingsModal.classList.add("hidden"));

saveKeysBtn.addEventListener("click", async () => {
  const murfKey = document.getElementById("murfKeyInput").value.trim();
  const aaiKey = document.getElementById("aaiKeyInput").value.trim();
  const geminiKey = document.getElementById("geminiKeyInput").value.trim();

    if (!murfKey || !aaiKey || !geminiKey) {
    alert("⚠️ Please enter all API keys before saving!");
    return; // stop until all fields are filled
  }


  // Save to localStorage
  if (murfKey) localStorage.setItem("MURF_API_KEY", murfKey);
  if (aaiKey) localStorage.setItem("ASSEMBLYAI_API_KEY", aaiKey);
  if (geminiKey) localStorage.setItem("GEMINI_API_KEY", geminiKey);

  try {
    // ✅ Validate Murf
    if (murfKey) {
      const res = await fetch("/voices", { headers: { "x-murf-key": murfKey } });
      if (!res.ok) throw new Error("Invalid Murf API Key");
    }

    // ✅ Validate AssemblyAI (simpler request)
    if (aaiKey) {
      const res = await fetch("https://api.assemblyai.com/v2/transcript", {
        method: "POST",
        headers: { "authorization": aaiKey }
      });
      if (res.status === 401) throw new Error("Invalid AssemblyAI API Key");
    }

    // ✅ Validate Gemini
    if (geminiKey) {
      const res = await fetch("https://generativelanguage.googleapis.com/v1/models", {
        headers: { "x-goog-api-key": geminiKey }
      });
      if (res.status === 403 || res.status === 401) throw new Error("Invalid Gemini API Key");
    }

    alert("✅ API keys saved & validated!");
    settingsModal.classList.add("hidden");

  } catch (err) {
    alert("❌ " + err.message);
  }
});


// --- API Helpers (fetch-based routes) ---

async function fetchVoices() {
  const murfKey = localStorage.getItem("MURF_API_KEY");
  if (!murfKey) throw new Error("⚠️ Murf API key missing. Please enter it in Settings.");
  const res = await fetch("/voices", {
    headers: { "x-murf-key": murfKey }
  });
  if (!res.ok) throw new Error("Failed to fetch voices");
  return res.json();
}


async function generateVoice(text, voiceId) {
  const murfKey = localStorage.getItem("MURF_API_KEY");
  if (!murfKey) {
    throw new Error("⚠️ Murf API key missing. Please enter it in Settings.");
  }

  const res = await fetch("/generate-voice", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-murf-key": murfKey
    },
    body: JSON.stringify({ text, voiceId })
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Voice generation failed: ${errText}`);
  }

  return res.json();
}


async function generateTTS(text, voiceId = "en-IN-alia") {
  const murfKey = localStorage.getItem("MURF_API_KEY");
  if (!murfKey) {
    throw new Error("⚠️ Murf API key missing. Please enter it in Settings.");
  }

  const res = await fetch("/tts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-murf-key": murfKey
    },
    body: JSON.stringify({ text, voiceId })
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`TTS request failed: ${errText}`);
  }

  return res.json();
}

async function queryLLM(audioFile) {
  const murfKey = localStorage.getItem("MURF_API_KEY");
  const aaiKey = localStorage.getItem("ASSEMBLYAI_API_KEY");
  const geminiKey = localStorage.getItem("GEMINI_API_KEY");

  // 🚨 Block if any key is missing
  if (!murfKey || !aaiKey || !geminiKey) {
    throw new Error("⚠️ Missing API keys. Please enter Murf, AssemblyAI, and Gemini keys in Settings.");
  }

  const formData = new FormData();
  formData.append("file", audioFile);

  const res = await fetch("/llm/query", {
    method: "POST",
    headers: {
      "x-murf-key": murfKey,
      "x-aai-key": aaiKey,
      "x-gemini-key": geminiKey
    },
    body: formData
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`LLM query failed: ${errText}`);
  }

  return res.json();
}
