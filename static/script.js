// ----------------------------
// session handling (from URL or generated)
// ----------------------------
let urlParams = new URLSearchParams(window.location.search);
let sessionId = urlParams.get("session_id");
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
    const btn = document.getElementById("recordBtn");
    if (mediaRecorder && mediaRecorder.state === "recording") {
        stopRecording();
        btn.textContent = "Start";
        btn.classList.remove("border-pink-500");
        btn.classList.add("border-cyan-500");
    } else {
        startRecording(true);
        btn.textContent = "Stop";
        btn.classList.remove("border-cyan-500");
        btn.classList.add("border-pink-500");
    }
}



async function startRecording(manual = true) {
  manualRecording = manual;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    recordedChunks = [];

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) recordedChunks.push(event.data);
    };

    mediaRecorder.onstop = () => {
      const blob = new Blob(recordedChunks, { type: 'audio/webm' });
      // Send to LLM pipeline (always)
      sendToLLM(blob);

      // If manual recording, ask to save/upload; auto recordings skip the prompt/upload
      if (manualRecording) {
        const status = document.getElementById('uploadStatus');
        status.textContent = "Processing... Please wait.";

        let defaultName = `recording_${Date.now()}`;
        let newName = prompt("✅ Audio recorded! Name the file?", defaultName);
        if (!newName) newName = defaultName;
        if (!newName.toLowerCase().endsWith(".webm")) newName += ".webm";

        const formData = new FormData();
        formData.append('file', blob, newName);

        fetch("http://127.0.0.1:8000/upload-audio", { method: "POST", body: formData })
          .then(res => res.json())
          .then(data => {
            console.log(`File "${data.filename}" uploaded successfully! Size: ${data.size} bytes`);
            document.getElementById('uploadStatus').textContent = `Uploaded: ${data.filename}`;
          })
          .catch(err => {
            console.error("❌ Upload failed.", err);
            document.getElementById('uploadStatus').textContent = `Upload failed`;
          });
      } else {
        // For auto recordings, optionally update UI but do not prompt
        document.getElementById('uploadStatus').textContent = "Auto message sent.";
      }
    };

    mediaRecorder.start();
    console.log("🎙️ Recording started...", manual ? "(manual)" : "(auto)");

    // For auto recordings, stop automatically after a short time
    if (!manual) {
      setTimeout(() => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
          mediaRecorder.stop();
        }
      }, AUTO_RECORD_DURATION_MS);
    }
  } catch (err) {
    console.error("Microphone access denied or error:", err);
    alert("Microphone access is required.");
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    console.log("⏹️ Recording stopped.");
  }
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
