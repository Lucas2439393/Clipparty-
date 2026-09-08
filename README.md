# 🎬 ClipParty

> Open-source AI clipping platform for turning long-form video into high-quality short-form content.

ClipParty is an open-source platform that automatically analyzes long-form video, identifies strong moments, creates short-form clips and prepares them for platforms such as TikTok, Instagram Reels and YouTube Shorts.

The goal is simple:

**Upload → Analyze → Select → Edit → Review → Export**

---

## ✨ Features

- 🎥 Video upload and processing
- 🤖 AI-assisted clip selection
- 📝 Automatic transcription
- 🎯 Campaign-aware clip selection
- ✂️ Automated clip rendering
- 📱 Vertical 9:16 output
- 💬 Captions and subtitles
- 🧠 Hook and payoff detection
- 👤 Face visibility analysis
- ✅ Compliance checking
- 🔍 Export quality control
- 📊 Job progress tracking
- 📁 Campaign management
- 🌐 Web-based interface
- 🔌 API architecture
- ⚙️ Worker-based video processing
- 🐳 Self-hosting support

---

# 🏗️ Architecture

ClipParty is designed as a modular platform.

```text
                    ClipParty
                        │
             ┌──────────┴──────────┐
             │                     │
          Frontend               Backend
             │                     │
             │                  REST API
             │                     │
             └──────────┬──────────┘
                        │
                    Job Queue
                        │
                   Worker(s)
                        │
              ┌─────────┴─────────┐
              │                   │
          AI Analysis         Video Engine
              │                   │
       ┌──────┴──────┐       FFmpeg
       │             │
 Transcription   Selection
       │             │
       └──────┬──────┘
              │
          Quality Control
              │
           Storage
