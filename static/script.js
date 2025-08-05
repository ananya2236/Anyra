// Voice Response with Audio Playback
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
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        text: text,
        voiceId: "en-AU-joyce" // Optional: Make dynamic later
      })
    });

    const result = await response.json();

    if (response.ok) {
      const audioUrl = result.audioFile;
      if (audioUrl) {
        audioPlayer.src = audioUrl;
        audioPlayer.style.display = "block";
        audioPlayer.play();
      } else {
        alert("No audio returned from API.");
      }
    } else {
      alert("Error: " + (result.detail || "Something went wrong"));
    }

  } catch (error) {
    console.error("Error:", error);
    alert("Failed to connect to backend.");
  }
}

// Eye Tracking
const eyeLeft = document.getElementById("eye-left");
const eyeRight = document.getElementById("eye-right");
const aiContainer = document.getElementById("ai-container");

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

// Reset eye position
document.addEventListener("mouseleave", () => {
  eyeLeft.style.transform = `translate(0, 0)`;
  eyeRight.style.transform = `translate(0, 0)`;
});

// Fade in "Let's Get Started!" after 2 seconds
setTimeout(() => {
  document.getElementById("getStartedText").classList.add("opacity-100");
}, 2000);

// Echo Bot Recording Setup
let mediaRecorder;
let recordedChunks = [];

const startRecording = async () => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(stream);
  recordedChunks = [];

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) {
      recordedChunks.push(event.data);
    }
  };

  mediaRecorder.onstop = () => {
  const blob = new Blob(recordedChunks, { type: 'audio/webm' });
  const audioURL = URL.createObjectURL(blob);
  const audio = document.getElementById('echoAudio');
  audio.src = audioURL;
  audio.classList.remove('hidden');
  audio.play();
};


  mediaRecorder.start();
};

const stopRecording = () => {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
};
