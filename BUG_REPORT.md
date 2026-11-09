# Jarvis 1.6 (app.jarvis.phone): bug report and test results

**Tested:** `jarvis.1.6.apk`, version 1.6 (versionCode 7), minSdk 21, targetSdk 35.

The app is a native shell (`classes.dex`, 4 classes) around one web page (`assets/index.html`, about 1,600 lines). The web page
holds all of the assistant's logic. The native shell provides the Android abilities through two JavaScript interfaces:
`Jarvis` (storage, HTTP, Clock app, volume, flashlight, apps, browser panel) and `JarvisVoice` (speech).

## How it was tested

- **Web page:** loaded in headless Chromium with a mock of both Android interfaces and a fake OpenAI-compatible model endpoint.
  This exercises every feature end to end: instant commands, the tool-calling loop, settings, the browser panel, weather,
  STOP, the log and layout at three screen sizes. There are 27 tests in `tests/tests.js`. Each test asserts the *correct*
  behaviour, so a FAIL means a reproduced bug.
- **Native code:** the dex file was read with a small bytecode lister (`tests/dexdump.py`). It shows the strings, fields and
  calls used by each method. A full decompiler (jadx) was not allowed in this environment, so the native findings below come
  from call lists, not from source.

| | Passed | Failed |
|---|---|---|
| Original `index.html` | 14 | 13 |
| Fixed `app/index.html` | 27 | 0 |

The full output is in `tests/results/original.txt` and `tests/results/fixed.txt`. To rerun: `npm i && npm test`, or
`npm run test:original` for the original page.

## Bugs found in the web page (fixed in `app/index.html`)

Line numbers refer to `original/index.html`.

| # | Severity | Bug | Where | Reproduced |
|---|---|---|---|---|
| 1 | Medium | **The ⚙ Settings button is off-screen on 360 px-wide phones** (a very common width), and on 320 px the PC button is off-screen too. A rule in the narrow-screen CSS (`input{min-width:96px}`) pushes the buttons past the edge. Settings can still be reached from the ? panel. | L83 | `cut off: gear (viewport 360px)` and `pc, gear (viewport 320px)`; see `tests/results/small-phone-before.png` and `small-phone-after.png` |
| 2 | Medium | **"Open X and do Y" never reaches the brain.** Any message starting with "open" is treated as an app name. "Open the browser and search for cats" replies *"there is no app called browser and search for cats"*. "Open youtube.com" also looks for an app instead of opening the site. | L1311 | Both replies seen in the test run |
| 3 | Medium | **Tomorrow's weather is not possible.** "Weather tomorrow" is a button in the ? panel, but `get_weather` has no day setting and asks the weather service for 1 day only. The model gets today's numbers and presents them as tomorrow's. | L797, L1246 | The model got only "Today 1 to 7°C" and answered "Tomorrow it will be 1 to 7 degrees" |
| 4 | Medium | **The online model setting is sent to both services.** A Groq model name in "Online model" is also sent to Gemini when Groq fails (for example, at its free limit), so the fallback fails as well. It also breaks video watching through Gemini. | L858 | `Gemini was sent model "llama-3.3-70b-versatile"` |
| 5 | Low | **"Is the flashlight on?" turns the flashlight on.** The instant command matches the words "flashlight" and "on" even in a question. | L1300 | `torch(true)` called |
| 6 | Low | **The brain view (zoom, stop spinning) is never saved.** The view-saving `keep()` (L458) has the same name as the storage function `keep(key, value)` (L677), and the later one replaces it. Each tap or zoom instead writes a junk entry `undefined`. | L458 / L677 | Zoom 1.82 before a restart, 1.00 after |
| 7 | Low | **"null% chance of rain".** The weather service can return no rain chance, and the reply prints `null`. | L804 | Reproduced |
| 8 | Low | **An alarm "for 7am today", set in the afternoon, is reported as set for today.** The Clock app will ring tomorrow morning instead. | L777 | `Alarm set for 07:00` at 15:00 |
| 9 | Low | **Unneeded extra model calls.** The check for questions that need a tool matches "charge", so "Who is in charge of…" triggers a forced second model call. This costs time and free-tier quota. | L664 | 2 model calls instead of 1 |
| 10 | Low | **The chat box grows without limit.** Every line stays in the page for the whole session (303 lines after 150 questions). On long sessions this slowly uses memory on low-end phones. | L1422 | Reproduced |

These were tested and work correctly: instant commands (time, timers, volume, alarms, reminders, the to-do list, remember,
open app, YouTube, Google), the tool-calling loop, the "you said you did it but didn't call the tool" nudge, STOP discarding a
late answer, the clear error when no brain is configured, refusing to store PINs and passwords, hiding passwords in the
activity log, refusing notes that contain commands, keeping and removing a saved API key, name validation, and loading in a
plain browser.

## Findings in the native code (not fixed: they need a rebuild of the Java code)

| # | Severity | Finding |
|---|---|---|
| N1 | Medium | **`JarvisVoice` is not restricted to the app's own page.** Every `Jarvis` method checks `ok()` (only true while the main view shows `file:///android_asset/`). `JarvisVoice.listen/speak/stop/voices` have no such check, and neither do `Jarvis.battery` and `Jarvis.vibrate`. The PC button loads the laptop's page into the same view over plain HTTP. That page, or anyone who can tamper with it on the Wi-Fi, can start the microphone and receive the transcript through `window.onNativeHeard`. **Fix:** call `ok()` in those methods, or open the laptop page in a WebView without the interfaces. |
| N2 | Medium | **The laptop password and memories travel unencrypted.** `usesCleartextTraffic=true` applies to the whole app. `laptopBase()` always uses `http://`, even when the user types `https://`. `openLaptop` POSTs the password to `/login`, and "Sync memories" sends every saved memory over the same HTTP link. On a shared Wi-Fi, anyone listening can read them. |
| N3 | Low | **Some requests can hang forever.** `Bridge.http` sets a connect timeout and a per-read timeout, but no total limit. A server that sends data slowly keeps a request open indefinitely. On the JavaScript side, `http()` has no timeout of its own, and if `ok()` is false the callback never fires. Either way Jarvis stays in "Thinking" until STOP is pressed. |
| N4 | Low | **A thread-safety gap.** `onHome` is written on the UI thread and read on the JavaBridge thread, but it is not `volatile`. |
| N5 | Info | **API keys and the laptop password are stored as plain text** in the `jarvis` shared preferences. `allowBackup=false` limits the exposure. |

## Not covered

- Real device behaviour: text-to-speech, speech recognition, the Clock app intents, the floating pop-up, the foreground
  service and permissions.
- Real Groq, Gemini and Ollama responses (the tests use a fake endpoint with the same request format).
- The fixed page has **not** been put back into an APK. To ship it, copy `app/index.html` to `assets/index.html` in the
  Android project, rebuild, and sign with the release key.
