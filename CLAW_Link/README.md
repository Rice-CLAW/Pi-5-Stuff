# CLAW Link — Bluetooth + offline website starter

Version 0.1. Connection test only. No motor commands, sensor collection, or model training are implemented. No phone app compilation, Mac, paid domain, or cloud backend is needed.

## What you need

- Raspberry Pi 5 with Raspberry Pi OS, working Bluetooth, and terminal access.
- Android with Chrome, or iPhone with Bluefy installed.
- Internet on the Pi for package installation and on the phone for first website load.
- A hosting account. GitHub Pages, Cloudflare Pages, or Netlify can serve web/ over HTTPS.
- This extracted folder.

Only the web/ folder goes onto the web host. The pi/ folder runs on your Pi.
Python server uses the open-source Bless library; Linux Bluetooth is provided by BlueZ.
BLE packets stay between phone and Pi. The host serves interface files only.

## 1. Host the interface — choose ONE option

### Option A: GitHub Pages (good for version control)

1. Sign into GitHub and create a new repository, for example claw-link.
   A public repository is the straightforward GitHub Free Pages option.
   Do not upload private recordings, credentials, or participant information.
2. Upload the CONTENTS of web/ to the repository root, so index.html is at the root.
3. In repository Settings → Pages, select Deploy from a branch, main, /(root), and save.
4. Wait for GitHub to show the live site URL. Open that exact HTTPS address.
5. Enable Enforce HTTPS if it is not already enforced.

Relative asset paths support a project URL such as https://YOURNAME.github.io/claw-link/.
You do not need to buy YOURNAME.com.

### Option B: Netlify (simple manual upload)

Sign into Netlify, use its manual deploy/drop option, and upload the web/ folder.
The uploaded site's top level must contain index.html.
Use the supplied HTTPS URL. To update, upload a revised web/ folder to the SAME site.

### Option C: Cloudflare Pages

Create a Pages project using Direct Upload; upload web/ with index.html at its top level.
Use the supplied HTTPS pages.dev address.
Direct Upload project setup differs from Git integration; choose the workflow you want initially.

Do not just open index.html from Downloads. Use the hosted HTTPS URL for the supported workflow.
Do not embed this page in another website: open its URL directly.

## 2. Copy the Python server to the Pi

Copy this extracted CLAW_Link folder to your Pi's home directory.
Example from Windows PowerShell, after changing to the folder containing CLAW_Link:

    scp -r .\CLAW_Link wristpower@YOUR_PI_ADDRESS:~/

Replace YOUR_PI_ADDRESS with the same hostname or IP you use for SSH.
If your username is not wristpower, replace it too. An existing USB/file-transfer method also works.

The examples below assume ~/CLAW_Link exists on the Pi.

## 3. Install and run on the Pi

Run these in the Pi terminal. These steps do not modify your EMG project.

    sudo apt update
    sudo apt install -y bluetooth bluez python3-venv
    sudo systemctl enable --now bluetooth
    sudo rfkill unblock bluetooth
    bluetoothctl power on
    cd ~/CLAW_Link/pi
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python ble_server.py

The terminal should report that CLAW-Pi is advertising. Leave it open.
Use Ctrl+C to stop. The counter resets when the server restarts.

If BlueZ reports a permission/access-denied error, a one-time diagnostic is:

    sudo .venv/bin/python ble_server.py

Use this only for diagnosing this test server. For long-term operation, configure narrow
BlueZ permissions and a dedicated service rather than running the entire EMG stack as root.

If installation or advertising fails, save the exact terminal error and record:

    cat /etc/os-release
    bluetoothctl show
    .venv/bin/python -m pip show bless

Do not replace your OS or change your EMG scripts to fix this prototype.
Bless is pinned to 0.2.6 for reproducibility; compatibility with your Pi OS needs hardware verification.

## 4. Connect on the phone

1. Turn Bluetooth on. Grant browser Bluetooth/nearby-device permissions when requested.
2. Android: open the HTTPS site in Chrome.
   iPhone: open the HTTPS site INSIDE Bluefy.
3. With the Python server running, tap Connect to Pi and select CLAW-Pi.
4. Look for Connected, then a counter updating roughly once a second.
5. Tap Send ping. You should see PONG received within 5 seconds.
6. Tap Disconnect and reconnect to test recovery.

The Pi advertises a custom service UUID, and the chooser filters by that service.
BLE connection from the page is the main flow; system Settings pairing is not normally required.
Test with one phone at a time.

## 5. Verify offline readiness

1. While online, tap Save for offline.
2. Confirm Offline files verified locally.
3. Tap Protect storage. Read whether protection was actually granted.
4. Close and reopen the page once while still online, allowing the service worker to control it.
5. Disable Wi-Fi AND cellular data on the phone, but leave Bluetooth ON.
6. Close and reopen the SAME URL in the SAME browser.
7. Connect to the Pi again and test counter + PING/PONG.
8. Repeat after restarting the phone.

On Android, you may install/add the PWA using Chrome's menu. Test the installed version too.
On iPhone, continue using Bluefy itself initially. A Home Screen web app is a different
execution environment and must not be assumed to retain Bluefy's Bluetooth bridge.

A cache check establishes that this version's assets are present, not that they last forever.
Persistence may be unavailable or denied in Bluefy. It does not protect against manual deletion.
The network badge reflects the browser's connectivity hint, not a guaranteed internet test.

## Troubleshooting

- Bluetooth API unavailable: verify the browser and HTTPS address; Safari/iPhone Chrome are
  not the intended clients. Bluefy compatibility must be tested.
- Pi not listed: keep server running, confirm bluetoothctl show reports Powered: yes,
  disconnect other test clients, move closer, and restart server.
- Connected but counter stops: the page marks updates stale after about 3.5 seconds.
  Browser suspension can interrupt the connection. Reconnect with the page foregrounded.
- Offline setup remains incomplete: keep internet on; check hosting paths and sw.js loading.
  Run the site at its own URL, not in a preview iframe.
- Offline reopen fails in Bluefy: report it as a failed milestone. Do not claim offline iPhone
  support based only on Android results or an already-open tab.
- Version updates: change CACHE in BOTH app.js and sw.js together (for example claw-link-v2).
  Deploy all assets together. Close every tab of the old app and reopen online to let the new
  worker activate. Do not clear storage while offline.

## Protocol

Service: 802b1000-6a92-4a6f-9b2b-769c88951a01
Command (write with response): 802b1001-6a92-4a6f-9b2b-769c88951a01
Status (read + notify): 802b1002-6a92-4a6f-9b2b-769c88951a01

Phone sends ASCII PING.
Pi sends ASCII PONG or ERR:COMMAND.
Pi periodically sends T:<counter>.
Every implemented message is at most 20 bytes; no fragmentation is needed for this version.
No pairing/authentication requirement is implemented: nearby clients can send test commands.
Do not add actuation to this protocol before designing authorization and device-side control behavior.

## Next milestones

First prove connection, two-way messages, and full offline reopening on both phone platforms.
Then integrate your existing EMG code: signal preview, labeled recording, training status, inference.
Keep raw data and models on the Pi. Define disconnect and movement behavior on the Pi/controller.
Phone browser background execution is not a dependable continuous motor-control channel.

## Validation performed

JavaScript and Python syntax checks; static asset/manifest consistency checks.
No claim of actual Pi BLE, phone-browser, or offline lifecycle testing.
No automatic boot service until manual operation has been verified.

## References

- https://github.com/kevincar/bless
- https://developer.chrome.com/docs/capabilities/bluetooth
- https://webkit.org/blog/14403/updates-to-storage-policy/
- https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site
- https://developers.cloudflare.com/pages/get-started/direct-upload/
- https://docs.netlify.com/start/quickstarts/netlify-drop-quickstart/
