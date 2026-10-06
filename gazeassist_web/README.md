# GazeAssist – Caregiver Web Dashboard

Django dashboard for caregivers of people who communicate with the **GazeAssist**
eye-gaze app (Tkinter + MediaPipe). The gaze app talks to this dashboard only
through a REST API. It never opens the database.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Setup, models, admin, login/register, dashboard home | ✅ |
| 2 | Patient page tabs: overview, reports, activity, analytics | ✅ |
| 3 | REST API + online status + live polling | ✅ |
| 4 | SOS banner, acknowledge, messages to patient | ✅ |
| 5 | Chart.js analytics | ✅ |
| 6 | API client for the Tkinter app | ✅ |

## Requirements

- Windows 10/11, Python 3.11
- PowerShell

## Setup (first time)

```powershell
cd C:\Users\USER\Documents\miniproject\gazeassist_web

# 1. Virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run this once and retry:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Dependencies
pip install -r requirements.txt

# 3. Environment file
Copy-Item .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# Paste the printed key into .env as SECRET_KEY=...

# 4. Database
python manage.py migrate

# 5. Admin account and demo data
python manage.py createsuperuser
python manage.py seed_demo

# 6. Run
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in as **demo_caregiver / Demo@12345**.
The admin panel is at http://127.0.0.1:8000/admin/.

## Everyday commands

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver           # start the site
python manage.py test board          # run the tests
python manage.py seed_demo           # reset demo data
python manage.py makemigrations board; python manage.py migrate   # after model changes
```

## Opening the board on a phone (same Wi-Fi)

1. Find your PC's IP address: `ipconfig` (e.g. `192.168.1.20`).
2. In `.env`, set `ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.20` and
   `CSRF_TRUSTED_ORIGINS=http://192.168.1.20:8000`.
3. Run `python manage.py runserver 0.0.0.0:8000` and open `http://192.168.1.20:8000` on the phone.

## Caregiver approval

In **Admin → Site settings**, turn on *Require caregiver approval*. New
self-registered caregivers then cannot sign in until an admin approves them
(**Admin → Caregivers →** select → *Approve selected caregivers*, or tick
*Is approved* in the list).

A new caregiver sees no patients until an admin links them in
**Admin → Patients → (patient) → Patient–caregiver links**.

## Gaze app API

The Tkinter gaze app talks to the dashboard over a small JSON API at `/api/`.
Every call needs the patient's token in the header:

```
Authorization: Token <token>
```

### Getting a token

Each patient has their own gaze-app account and token. The token decides
which patient the calls are for. You can get it three ways:

```powershell
python manage.py gaze_token --list        # patient IDs
python manage.py gaze_token 1             # show (or create) the token for patient 1
python manage.py gaze_token 1 --reset     # revoke it and issue a new one
```

- **Admin → Patients:** select patients, then run the *Show gaze app API token* action.
- **`seed_demo`:** prints tokens for the two demo patients.

A gaze-app account has no password, so it cannot sign in to the website.
Caregiver accounts cannot use the API, even with a token.

### Endpoints

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/heartbeat/` | none | `{ok, patient{id,name}, server_time, pending_messages, online_window_seconds}`. Marks the patient online. |
| `POST /api/requests/` | `{"phrase": "I am thirsty", "is_emergency": false, "pain_level": null}` | `201` with the request: `{id, phrase, is_emergency, pain_level, timestamp, acknowledged, acknowledged_by, acknowledged_at, message}` |
| `GET /api/requests/<id>/status/` | none | Same shape as above. When acknowledged, `message` is `"Help is on the way"`. |
| `GET /api/messages/pending/` | none | `{"messages": [{id, text, sender, created_at}]}`. Each message is returned once, then marked delivered. |

Errors: `401` means the token is missing or wrong. `403` means the token is
not a patient token. `404` means the request belongs to another patient.
`400` means the data is invalid (empty phrase, pain level outside 0–10,
phrase longer than 255 characters). `429` means too many calls (default
limit is 240 per minute).

### Trying it from PowerShell

```powershell
$h = @{ Authorization = "Token PASTE_TOKEN_HERE" }
$base = "http://127.0.0.1:8000/api"

Invoke-RestMethod -Method Post -Uri "$base/heartbeat/" -Headers $h

$r = Invoke-RestMethod -Method Post -Uri "$base/requests/" -Headers $h `
     -ContentType "application/json" -Body '{"phrase": "SOS - I need help", "is_emergency": true}'
$r.id

Invoke-RestMethod -Uri "$base/requests/$($r.id)/status/" -Headers $h
Invoke-RestMethod -Uri "$base/messages/pending/" -Headers $h
```

Keep the dashboard open while you do this. Within 3 seconds, the patient
turns **Online** and the SOS appears under *Open alerts*. If no heartbeat
arrives for 15 seconds, the patient shows **Offline** again.

### Live updates in the browser

Pages update themselves by calling `fetch()` every 3 seconds (`POLL_INTERVAL_MS`).
The top bar shows **Live**, or **Reconnecting…** while the server can't be
reached; retries slow down to at most one every 30 seconds.

- **Dashboard** polls `/live/dashboard/` for the summary cards, the online dots and the open alerts.
- **Patient pages** poll `/live/patients/<id>/feed/`, which returns new requests and requests acknowledged since the last poll.

These endpoints use the normal signed-in session and apply the same
caregiver–patient checks as the pages.

## SOS flow

1. The gaze app sends `POST /api/requests/` with `"is_emergency": true`.
2. Within about 3 seconds, **every page** of every caregiver linked to that
   patient shows a red banner under the top bar:
   - It pulses slowly. There is no pulse if the device asks for reduced motion.
   - An alarm sounds, and repeats every 20 seconds while any SOS is open.
   - The tab title flashes "🔴 SOS (n)", and phones vibrate.
3. The caregiver presses **Acknowledge**, in the banner, the activity feed or
   the dashboard's Open alerts. The first caregiver to press it is recorded.
   Anyone pressing after that is told "Already acknowledged by …".
4. The gaze app's `GET /api/requests/<id>/status/` now returns
   `"acknowledged": true` and `"message": "Help is on the way"`.

**Sound.** Browsers block sound until you have clicked or pressed a key on
the page. If an SOS arrives first, the banner shows **Tap to turn on alarm
sound**. After any click, sound works until the page is closed. **Sound on / off**
mutes the alarm on that device (the choice is remembered in the browser).
Keep a dashboard tab open and click it once at the start of a shift.

Normal requests can be marked **seen** the same way. The gaze app then gets
"Your caregiver has seen your request".

## Messages to the patient

The **Overview** tab has a *Message* card, which the **Message** button in the
patient header jumps to. It has quick replies ("I'm on my way.", …) and up
to 280 characters of text.

The gaze app picks messages up from `GET /api/messages/pending/`. The card
then shows "Shown on screen" with the time.

## Analytics

The **Analytics** tab covers the last 7, 30 or 90 days, counted in whole
local days with today as the last. Choose the period at the top; it applies
to every number and chart on the page.

- **Key numbers:** requests (and per day), emergencies (and on how many
  days), average time to acknowledge an SOS, and average pain level.
- **Requests per day:** columns with every day shown, including days
  with no requests.
- **Emergencies over time:** SOS alerts per day. Only days that had an SOS
  get a dot.
- **Pain level trend:** the daily average on the 0–10 scale. Days without a
  pain report are gaps, not zeros.
- **Most-used phrases:** the top 8, with the count at the end of each bar.
- **Time of day:** morning, afternoon, evening and night.

Hover (or tap) a chart for exact values. Every value is also under
**Show as table**, so nothing depends on reading the chart.

Charts use [Chart.js 4](https://www.chartjs.org/), loaded from a CDN with an
integrity hash. All chart data is prepared in `PatientAnalyticsView`
(`board/views.py`); the drawing code is in `static/js/analytics.js`.

## Connecting the Tkinter gaze app

The gaze app (`..\gazeassist`) talks to this dashboard through
`gazeassist\caregiver_link.py`. It needs no new packages, because the app
already uses `requests` and `python-dotenv`.

### First-time setup (no terminal, no files to edit)

1. **Install the desktop icon (once).** Whoever installs GazeAssist runs this
   from the `miniproject` folder:
   `python setup_shortcuts.py`. Add `--autostart` to also start GazeAssist
   at Windows login.
2. **Add the patient.** The caregiver signs in to the dashboard and clicks
   **Add patient**. They become that patient's primary caregiver.
3. **Get a connection code.** On the patient's page, click
   **Connect gaze app → Create connection code**. A 6-digit code is shown,
   valid for 10 minutes.
4. **Type the code on the patient's computer.** Double-click **GazeAssist**
   on the desktop. The first time, the gaze app shows *Connect to caregiver
   dashboard*. Type the code and press **Connect**. Already set up before?
   Press **Ctrl+Shift+P** in the gaze app.
5. **Done.** The dashboard page turns green. From then on, the gaze app
   starts and connects by itself; the patient only uses their eyes.

How it is protected:
- The code works once, only for that patient, and only for 10 minutes. A
  new code cancels the previous one.
- Only a hash of the code is stored, and code guesses are limited to 10 per
  minute per address.
- The gaze app receives its API token directly and saves it encrypted with
  Windows DPAPI in `gazeassist\data\dashboard_link.json`. Only that Windows
  user on that computer can read it.
- Connecting a new computer disconnects the old one. **Disconnect** on the
  patient's page revokes access immediately.
- Only primary caregivers can connect or disconnect the gaze app.

If the gaze app runs on another computer, start the dashboard with
`python run_all.py --host 0.0.0.0` (or `runserver 0.0.0.0:8000`). Add the
dashboard computer's IP address to `ALLOWED_HOSTS` in this project's `.env`,
and type `http://<that IP>:8000` as the dashboard address in the gaze app.

The older manual setup (`manage.py gaze_token` plus `GAZEASSIST_API_TOKEN` in
`gazeassist\.env`) still works. A saved pairing takes priority over it.

Check the link without a camera with `python verify_caregiver_link.py` (in `gazeassist`).

What the gaze app then does, all on one background thread so the UI never
freezes:

| In the gaze app | On the dashboard |
|---|---|
| App running (heartbeat every 5 s) | Patient shows **Online** |
| Patient selects a phrase | Appears in the live activity feed |
| Patient selects a pain level | Pain-level chart; the request carries `pain_level` |
| Patient selects an emergency tile | Red SOS banner and alarm for every linked caregiver |
| Caregiver presses **Acknowledge** | The gaze app stops its local alarm, shows **Help is on the way** and speaks it in the patient's language |
| Caregiver sends a message | The gaze app shows it in large text and speaks it |

If the dashboard can't be reached, requests are kept and sent when it comes
back. An SOS always goes first, and an SOS is never dropped even when the
queue is full. A small badge in the bottom-left corner of the gaze app shows
whether the dashboard is connected. If `GAZEASSIST_API_TOKEN` is empty, the
gaze app works exactly as before.

## Security notes

- Every patient page is fetched through `patients_for(caregiver)` (`board/access.py`).
  A patient who is not linked to you returns **404**, even if you type the URL yourself.
- Passwords are hashed by Django (PBKDF2). All forms use CSRF tokens.
- Secrets live in `.env`, which git ignores. `DEBUG` is set in `.env`.
- Uploaded reports go to `private_media/`, outside `static/`, under random
  file names. They are checked for extension, content type, file signature
  and size (5 MB max). They are never served directly: `/reports/<id>/view/`
  and `/reports/<id>/download/` check the caregiver-patient link and then stream the file.
- Any linked caregiver can upload and view reports. Only the caregiver who
  uploaded a report, or a primary caregiver, can delete it.
- Acknowledge and message forms are POST-only with CSRF tokens. They
  return 404 for requests or patients that aren't linked to you.
- The API only accepts tokens, never browser sessions, and each token
  can only reach its own patient. Calls are rate-limited per token.
- With `DEBUG=False`, `runserver` does not serve CSS/JS. For a quick local
  test, use `python manage.py runserver --insecure`.

## Project layout

```
gazeassist_web/
├── manage.py
├── requirements.txt
├── .env / .env.example
├── gazeassist_web/          # settings, root urls, wsgi/asgi
├── board/                   # main app
│   ├── models.py            # Patient, Caregiver, PatientCaregiver, PatientReport, Request, CaregiverMessage, SiteSettings
│   ├── access.py            # CaregiverRequiredMixin + patients_for()
│   ├── forms.py             # login + registration forms
│   ├── views.py             # auth, dashboard, patient tabs, reports
│   ├── live.py              # JSON endpoints polled by the pages
│   ├── api/                 # gaze app REST API (DRF): views, serializers, permissions, tokens
│   ├── admin.py             # search, filters, approval actions
│   ├── validators.py        # report upload validation
│   ├── signals.py           # delete files with their report
│   ├── context_processors.py
│   ├── templatetags/board_extras.py
│   ├── management/commands/  # seed_demo, gaze_token
│   ├── tests.py             # pages, access control, reports
│   ├── test_api.py          # API and live endpoints
│   ├── test_sos.py          # SOS banner, acknowledge, messages
│   └── test_analytics.py    # chart data
├── templates/               # base, auth, partials, board pages
├── static/css/gazeassist.css
├── static/js/gazeassist.js   # live updates, SOS banner, forms
├── static/js/analytics.js    # Chart.js charts for the Analytics tab
└── private_media/           # uploaded reports (created automatically)
```
