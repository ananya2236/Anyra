// ----------------------------
// session handling (from URL or generated)
// ----------------------------
let urlParams = new URLSearchParams(window.location.search);
let sessionId = urlParams.get("session_id");
let isRecording = false;
let audioWS = null;


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


function toggleRecording() {
  if (!isRecording) {
    // start only if not recording
    isRecording = true;
    startRecording();
    document.getElementById('recordBtn').textContent = "⏹️ Stop";
  } else {
    // stop only if currently recording
    isRecording = false;
    stopRecording();
    document.getElementById('recordBtn').textContent = "🎙️ Start";
  }
}




async function startRecording(manual = true) {
  manualRecording = manual; // keep your flag, though we won't use upload/LLM here

  try {
    // 1) Open WebSocket to the new audio endpoint
    audioWS = new WebSocket(`ws://127.0.0.1:8000/ws-audio?session_id=${sessionId}`);
    audioWS.binaryType = "arraybuffer";

    audioWS.onopen = async () => {
      console.log("[WS] connected for audio streaming");

      // 2) Get mic & start MediaRecorder
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // NOTE: default is usually audio/webm; keep container/extension consistent on server
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });

      mediaRecorder.ondataavailable = async (event) => {
        if (event.data && event.data.size > 0 && audioWS && audioWS.readyState === WebSocket.OPEN) {
          const buf = await event.data.arrayBuffer();
          audioWS.send(buf); // 3) send binary chunk to server
        }
      };

      // timeslice (ms) → emit chunk every 250ms
      mediaRecorder.start(250);
      document.getElementById('uploadStatus').textContent = "🔴 Streaming…";
      console.log("🎙️ Streaming started...");
    };

    audioWS.onmessage = (evt) => {
      // Server replies "SAVED:/streams/..." on DONE
      if (typeof evt.data === "string" && evt.data.startsWith("SAVED:")) {
        const path = evt.data.substring("SAVED:".length);
        document.getElementById('uploadStatus').textContent = `✅ Saved: ${path}`;
        console.log("Saved file at:", path);
      }
    };

    audioWS.onclose = () => {
      console.log("[WS] closed");
    };

    audioWS.onerror = (e) => {
      console.error("[WS] error", e);
      document.getElementById('uploadStatus').textContent = "WebSocket error";
    };

  } catch (err) {
    console.error("Microphone access denied or error:", err);
    alert("Microphone access is required.");
  }
}


function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
    console.log("⏹️ Recording stopped.");
  }
  // tell server to finalize file
  if (audioWS && audioWS.readyState === WebSocket.OPEN) {
    audioWS.send("DONE");
    audioWS.close();
  }
  document.getElementById('uploadStatus').textContent = "⏹️ Stopped (finalizing…)";
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


function appendMessage(sender, text) {
    const chatContainer = document.getElementById('chatContainer');
    const messageDiv = document.createElement('div');

    if (sender === 'user') {
        messageDiv.className = "flex justify-end";
        messageDiv.innerHTML = `<div class="bg-cyan-900 text-white rounded-lg p-5 max-w-xs border border-white">${text}</div>`;
    } else {
        messageDiv.className = "flex";
        messageDiv.innerHTML = `<div class="text-white rounded-lg p-3 max-w-xs border border-white">${text}</div>`;
    }

    chatContainer.appendChild(messageDiv);
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
