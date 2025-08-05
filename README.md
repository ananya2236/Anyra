# 🎙️ Anyra – AI Voice Assistant (In Development)

**Anyra** is a web-based AI voice assistant powered by Flask and enhanced with Tailwind CSS. It supports browser-based text-to-speech and microphone audio recording. Designed for learning, experimenting, and scaling – it's currently in active development.

> ⚠️ This is a personal project in its early stages. Expect lots of updates, refactors, and improvements!

---

## 📌 Current Features

- ✅ **Text-to-Speech (TTS)** using Web Speech API
- 🎤 **Echo Bot** section:
  - Records audio using the browser's **MediaRecorder API**
  - Plays back the recorded audio
- ✨ Animated glowing element (to give a fun personality!)
- 🌐 UI built with **Tailwind CSS**
- 🐍 **Flask** server to serve the frontend

---

## 🔧 Tech Stack

- Python 3.11+ (Flask)
- HTML5  
- Tailwind CSS  
- JavaScript  
- Web Speech API  
- MediaRecorder API  

---

## 📂 Project Structure

```bash
voice_assistant/
├── static/
│   ├── output.css        # Tailwind CSS output
│   └── script.js         # Frontend logic (TTS & recording)
├── templates/
│   └── index.html        # Main UI
├── app.py                # Flask app
├── .env                  # Environment variables (if any)
├── input.css             # Tailwind input config
├── package.json          # Node dependencies for Tailwind
├── requirements.txt      # Python dependencies
├── tailwind.config.js    # Tailwind customization
└── .venv/                # Python virtual environment
