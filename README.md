# 🎤 Anyra – AI Voice Assistant  
🔊 Powered by [Murf AI](https://murf.ai/api/docs/introduction/overview)

![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google_Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)
![AssemblyAI](https://img.shields.io/badge/AssemblyAI-6D6DFF?style=for-the-badge)
![Murf AI](https://img.shields.io/badge/Murf_TTS-FF6F61?style=for-the-badge)
![Render](https://img.shields.io/badge/Render-20232A?style=for-the-badge&logo=render&logoColor=white)

**Anyra** is an **AI-powered conversational voice assistant** that combines **real-time speech recognition**, **large language models**, and **text-to-speech synthesis** to create **natural, human-like voice conversations**.  

With a bold personality, an animated avatar, and interactive chat UI, Anyra delivers an **engaging, lifelike AI experience**.  

🌐 **Live Demo:** [Anyra on Render](https://anyra.onrender.com)  

---

## ✨ Features  

- 🎙️ **Live Voice Capture** – Manual & automatic recording modes.  
- 🧠 **LLM Conversations** – Context-aware replies powered by **Google Gemini 1.5 Flash**.  
- 🗣️ **Natural Voice Output** – **Murf AI TTS** with browser SpeechSynthesis fallback.  
- 👁️ **Animated Avatar** – Glowing orb + eye-tracking visuals for lifelike feel.  
- 💬 **Dynamic Chat UI** – Switch between **avatar mode** and **chat history**.  
- ♻️ **Persistent Context** – Maintains session memory with `session_id`.  
- 📂 **Audio Uploads** – Save manual recordings locally for later use.  
- 🚦 **Error Handling** – Graceful fallbacks if APIs fail.  
- 🔧 **Custom Personas** – Multiple personalities (futuristic, pirate, professor, etc.).  
- ⚡ **WebSocket Streaming** – Smooth **real-time speech-to-text** and **AI voice playback**.  

---

## 🪄 Skills  

Anyra is more than just small talk — she’s got **smart skills**:  

1. 🌦️ **Weather Report** – Get live weather updates by city.  
2. 😂 **Jokes** – Quick humor to lighten the mood.  
3. 🩺 **Health Recommendations** – Symptom-based general wellness tips (⚠️ not medical advice).  
4. 🌍 **Web Search** – Real-time factual queries answered via knowledge search.  
5. 📰 **News Headlines** – Latest updates and trending stories summarized.  

---

## 🛠️ Tech Stack  

| Layer        | Technology |
|--------------|------------|
| **Frontend** | HTML, Tailwind CSS, JavaScript |
| **Backend**  | Python, FastAPI |
| **AI Model** | Google Gemini 1.5 Flash |
| **STT**      | AssemblyAI |
| **TTS**      | Murf AI |
| **Styling**  | Tailwind CSS + custom animations |
| **Hosting**  | Render (deployed) |

---

## 🪄 Screenshots

![Anyra Chat UI](img2.png)
![Anyra Avatar](img.png)



🎥 **Demo Video:** [Watch Anyra in action](https://www.linkedin.com/posts/singhananya22_30daysofaivoiceagentschallenge-aivoiceagents-activity-7367981165242109953-llKM?utm_source=share&utm_medium=member_desktop&rcm=ACoAAEY_JsoBu6JyCXNxaSO0__JKI-cn7MFq7bQ)




## 🚀 Installation & Setup

1️⃣ Clone the Repository

git clone https://github.com/your-username/anyra-ai-voice-assistant.git
cd anyra-ai-voice-assistant


2️⃣ Install Backend Dependencies

pip install -r requirements.txt


3️⃣ Set Environment Variables (.env)

MURF_API_KEY=your_murf_api_key  
ASSEMBLYAI_API_KEY=your_assemblyai_api_key  
GEMINI_API_KEY=your_gemini_api_key  


4️⃣ Run the Backend Server

uvicorn app:app --reload


➡️ Backend: http://127.0.0.1:8000

5️⃣ Run the Frontend

python -m http.server 5500


➡️ Frontend: http://127.0.0.1:5500/index.html



## 🌱 Future Enhancements

🌐 Multi-language support for STT & TTS.

📱 Mobile-optimized responsive UI.

💾 Database-backed conversation history.

🔄 Continuous conversations without manual restarts.

🎭 Expanded personalities & voice styles.




## 🙏 Acknowledgments

Google Gemini – Contextual AI responses.

Murf AI – Text-to-Speech synthesis.

AssemblyAI – Speech-to-Text streaming.

FastAPI – Backend framework.

Tailwind CSS – Modern UI styling.

## ⚙️ Architecture  

```mermaid
flowchart TD
    A[🎙️ User Speaks] --> B[🎧 Audio Capture in Browser]
    B --> C["📡 FastAPI Backend /agent/chat/{session_id}"]
    C --> D["📝 AssemblyAI Transcribes Speech → Text"]
    D --> E["🤖 Gemini LLM Generates Contextual Reply"]
    E --> F["🔊 Murf AI Converts Text → Speech"]
    F --> G[🎧 Browser Plays Audio + Updates UI]

