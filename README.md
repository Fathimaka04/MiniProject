# GazeAssist

**Zero-learning, hardware-free assistive communication system** for non-verbal and motor-impaired users (ALS, locked-in syndrome, intubated ICU patients, non-verbal autistic children, post-stroke patients).

Runs entirely on a standard laptop webcam — no infrared hardware, no GPU.

## Features

| Module | Description |
|---|---|
| **Shared Perception** | Single MediaPipe Face Mesh (478 landmarks, 30+ FPS on CPU) |
| **Gaze Control** | MLP zone prediction with 9-point calibration, coarse-to-fine selection |
| **Blink Classifier** | LSTM + rule-based fallback — detects natural, short deliberate, long deliberate blinks |
| **AAC Phrase Board** | Paged grid with dwell/blink selection, repeat-to-confirm, frequency-adaptive reordering |
| **Pain Scale** | 1-10 visual scale (green→red), same confirm mechanism |
| **Emergency SOS** | 3 long blinks → speaker alarm + WhatsApp/SMS + dashboard alert |
| **Caregiver Dashboard** | Flask web app with live session log, pain timeline, phrase analytics |
| **Multilingual TTS** | Hindi, Malayalam, Tamil, Telugu via edge-tts (with pyttsx3 fallback) |

## Quick Start

```bash
# 1. Install dependencies
cd gazeassist
pip install -r requirements.txt

# 2. Run the app
python main.py
```

On first launch, the setup wizard will:
1. Ask for language preference (Hindi / Malayalam / Tamil / Telugu)
2. Collect emergency contact number (optional)
3. Run 9-point gaze calibration (~90 seconds)
4. Then drop into Navigate mode with the phrase board

## Architecture

```
One Camera → One MediaPipe Face Mesh → All 6 Modules

Thread 1: Perception (camera + MediaPipe)
Thread 2: Main/UI (Tkinter event loop)
Thread 3: SOS Monitor (continuous background)
Thread 4: Flask Dashboard (daemon)
```

**Key design constraint:** Only ONE `cv2.VideoCapture` and ONE MediaPipe instance exist in the entire app. All modules read landmarks from the shared perception layer.

## How It Works

### Selection Mechanism
1. **Gaze at a tile** for 800ms (dwell) → tile becomes **armed** (highlighted)
2. **Long blink** while tile is armed → tile is **confirmed** → phrase is spoken
3. If you look away or wait >3s → armed state **clears** (prevents accidental selection)

### SOS Emergency
- 3 long blinks within 10 seconds → triggers **all** alert channels:
  - 🔊 Loud alarm through speakers
  - 📱 WhatsApp/SMS to emergency contact
  - 🖥️ Red alert on caregiver dashboard
- SOS monitoring runs **always**, independent of current UI mode

### Caregiver Dashboard
Open `http://<laptop-ip>:5000` from any device on the same WiFi:
- Live communication log
- Pain level timeline (Chart.js)
- Most-used phrases
- SOS event log
- Session statistics

## Configuration

### Twilio (WhatsApp/SMS alerts)
Set environment variables:
```bash
set TWILIO_ACCOUNT_SID=your_sid
set TWILIO_AUTH_TOKEN=your_token
set TWILIO_FROM_NUMBER=+1234567890
```
If not configured, SOS alerts are logged but not sent (no crash).

### TTS Backends
The system auto-selects the best available TTS:
1. **edge-tts** — Neural voices for all 4 languages (requires internet)
2. **pyttsx3** — Offline system voices (fallback)
3. **Mock** — Logs output (for testing)

## Project Structure

```
gazeassist/
├── main.py                    # Application entry point
├── requirements.txt
├── perception/
│   └── face_mesh.py           # Shared MediaPipe perception layer
├── gaze/
│   ├── features.py            # Geometric feature extraction
│   ├── calibration.py         # 9-point calibration screen
│   └── model.py               # Gaze MLP + Ridge fallback
├── blink/
│   ├── ear.py                 # Eye Aspect Ratio computation
│   ├── classifier.py          # LSTM + rule-based blink classifier
│   └── enrollment.py          # Per-user blink enrollment
├── phrase_board/
│   ├── tiles.py               # Tile data + translations
│   ├── ui.py                  # Tkinter phrase board
│   ├── confirm.py             # Arm/confirm state machine
│   └── frequency.py           # Cross-session tile reordering
├── pain_scale/
│   └── ui.py                  # 1-10 pain scale UI
├── sos/
│   ├── monitor.py             # Background blink-pattern watcher
│   └── alerts.py              # Multi-channel alert system
├── state_machine/
│   └── modes.py               # Navigate / Pain mode state machine
├── tts/
│   └── indic_tts.py           # TTS engine (edge-tts / pyttsx3 / mock)
├── dashboard/
│   ├── app.py                 # Flask backend
│   ├── templates/index.html   # Dashboard UI
│   └── static/style.css
├── calibration_screens/
│   └── setup_wizard.py        # First-run setup wizard
├── analytics/
│   └── reports.py             # Matplotlib PDF reports
└── db/
    ├── schema.sql             # SQLite schema
    └── models.py              # Database access layer
```

## Hardware Requirements

- Any laptop/desktop with 8GB+ RAM
- Built-in or external 720p/30FPS webcam
- Speakers (for TTS and SOS alarm)
- No GPU required

## Supported Languages

| Language | Code | TTS Voice |
|---|---|---|
| Hindi | hi | hi-IN-SwaraNeural |
| Malayalam | ml | ml-IN-SobhanaNeural |
| Tamil | ta | ta-IN-PallaviNeural |
| Telugu | te | te-IN-ShrutiNeural |

## Non-Goals (Explicitly Excluded)

- Morse-code text entry / free-text typing
- Smart-home / environmental control
- Lip-reading module
- Passive emotion/distress detection
- Mobile (Android/iOS) deployment

## License

For research and accessibility use.
