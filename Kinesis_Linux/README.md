# Meta Neural Band on Raspberry Pi

Kinesis Linux connects a Meta Neural Band to a Raspberry Pi, streams raw EMG, and lets you train and run a personal classifier for **rest**, **wrist flexion**, and **wrist extension**. The Bluetooth client is an experimental Python port of [callbacked/kinesis](https://github.com/callbacked/kinesis). The Pi setup, enrollment, and EMG workflow have been exercised with a band. Compatibility can vary with the Linux Bluetooth stack, band firmware, and Meta sign-in.

- **First time with this Pi and band:** Follow steps 1–5. Step 5 confirms that the Pi can read decoded EMG.
- **Band paired and Meta session saved, with enrollment still pending:** Skip to [step 4: Connect and enroll the band](#4-connect-and-enroll-the-band).
- **Already enrolled on this Pi:** Use [Reconnect after a reboot](#reconnect-after-a-reboot), then go to [training](#6-train-a-wrist-gesture-model) or [live prediction](#7-run-live-prediction).
- **Already trained:** Reuse your saved model directory in step 7.

## 1. Install on the Pi

Use a Raspberry Pi 5 with Raspberry Pi OS, working Bluetooth, Python 3.11 or later, and internet access for installation and enrollment. The Meta sign-in window needs a graphical session. You can use SSH for the other steps. Run the application as the same regular Pi user throughout so it finds the credentials and model files you saved.

On a fresh Pi, install the system packages and clone the repo:

```bash
sudo apt update
sudo apt install -y git bluez python3-venv python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
sudo systemctl enable --now bluetooth

git clone https://github.com/Rice-CLAW/Pi-5-Stuff.git ~/Pi-5-Stuff
cd ~/Pi-5-Stuff/Kinesis_Linux
```

If you already cloned the repo, start with `cd ~/Pi-5-Stuff/Kinesis_Linux`. Run the rest of this guide from that folder:

```bash
python3 --version
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install . numpy scikit-learn joblib
.venv/bin/kinesis-linux --help
.venv/bin/python -c 'import kinesis_linux.transport, numpy, sklearn, joblib; print("Dependencies OK")'
```

The virtual environment exposes the Pi's system GTK and WebKit libraries through `--system-site-packages`. The examples call executables inside `.venv` directly, so you can paste them into a new terminal without activating the environment.

## 2. Pair the band with the Pi

Disconnect any phone, glasses, or computer currently using the band. Keep the band close to the Pi and put it in Bluetooth pairing mode. Start `bluetoothctl`:

```bash
bluetoothctl
```

At the `bluetoothctl` prompt, enter these commands one at a time:

```text
power on
agent on
default-agent
scan le
```

Look for a device named `Meta Band` followed by its suffix. Substitute the address you see for `AA:BB:CC:DD:EE:FF` in the following commands. Accept a pairing prompt for your band if one appears.

```text
pair AA:BB:CC:DD:EE:FF
devices Bonded
```

Pairing can cause BlueZ to show a different address for the same band. Use the address next to your band's name in `devices Bonded` in the commands below. If `pair` reports that the band is already paired, check the saved entry with `devices Bonded`.

```text
trust AA:BB:CC:DD:EE:FF
info AA:BB:CC:DD:EE:FF
scan off
quit
```

Check for `Paired: yes` and `Bonded: yes`. `Connected: no` means the saved bond exists and the Bluetooth link is currently down. Back in Bash, set the bonded MAC address for this terminal:

```bash
BAND_MAC=AA:BB:CC:DD:EE:FF
```

Set `BAND_MAC` again when you open a new terminal. Both shell scripts have a built-in address for the development band, so pass your own address each time.

## 3. Sign in to Meta once

Open a terminal in the Pi's graphical desktop and run:

```bash
cd ~/Pi-5-Stuff/Kinesis_Linux
.venv/bin/kinesis-linux login
```

Complete sign-in in the Meta window using the account that owns the band. Wait for **`Meta session saved`** in the terminal. Login stores a session token for enrollment. Once enrolled, streaming, training, and prediction use the saved owner identity and can run headless without a Meta sign-in or internet access.

<details>
<summary>Headless Pi with a separate Linux desktop</summary>

Install Kinesis Linux and the GTK/WebKit packages on a Linux computer with a working graphical session. Run `login` there, then copy only the session file to the Pi over SSH. Replace `PI_USER@PI_HOST` with your Pi login and hostname or IP address. These examples use the default state directory on the Pi.

On the Pi, as the user who will run Kinesis:

```bash
install -d -m 700 ~/.local/state/kinesis-linux
```

On the Linux desktop where login succeeded:

```bash
scp "${XDG_STATE_HOME:-$HOME/.local/state}/kinesis-linux/meta-session.json" \
  PI_USER@PI_HOST:.local/state/kinesis-linux/meta-session.json
```

Back on the Pi:

```bash
chmod 600 ~/.local/state/kinesis-linux/meta-session.json
```

</details>

## 4. Connect and enroll the band

Wake the band and run these commands on the Pi. Set `BAND_MAC` first if you switched terminals.

```bash
bash connect_neural_band.sh "$BAND_MAC" &&
  .venv/bin/kinesis-linux enroll --address "$BAND_MAC"
```

Wait for **`Saved the band's owner identity.`** and **`Enrollment complete.`**. Enrollment uses the Meta session to register a Linux owner identity with the band. It can replace the application identity used by an existing phone or Mac setup. A working Pi enrollment is reusable across reboots.

If you already have your own compatible owner identity JSON, you can test it with `listen --identity /path/to/band-identity-record.json` and `--address "$BAND_MAC"` instead of enrolling. Set the JSON permissions to `600` first. The training and prediction scripts launch `listen` without an identity override and expect the matching identity in Kinesis's default state path for that MAC.

By default, private state is under `~/.local/state/kinesis-linux/`, or `$XDG_STATE_HOME/kinesis-linux/` if set. Keep `meta-session.json` and the MAC-named `*.identity.json` files private and out of Git.

## 5. Verify raw EMG before training

```bash
bash connect_neural_band.sh "$BAND_MAC" &&
  .venv/bin/kinesis-linux listen --address "$BAND_MAC" --raw-emg
```

Look for a JSON event with `"event": "connected"`, then repeated `"event": "emg_batch"` lines. The decoded configuration used by the trainer has 16 samples per batch across 8 channels at 2,048 samples per second per channel. Press **Ctrl+C** once you see batches. Leave only one Kinesis client running at a time. The trainer and predictor each start their own stream.

## 6. Train a wrist gesture model

Wear the band in the position you plan to use for live prediction. Keep that position throughout the run.

```bash
bash connect_neural_band.sh "$BAND_MAC" &&
  .venv/bin/python train_wrist_rf.py --address "$BAND_MAC"
```

For each prompt, start with your wrist neutral, press **Enter**, move into the requested position when prompted, then hold it until the recording finishes. For **rest**, keep the wrist neutral and relaxed. For **flexion**, bend toward the palm side. For **extension**, bend toward the back of the hand. Return to neutral between recordings.

The current trainer collects **four repetitions per gesture**, for **12 recordings**. The first three repetitions of each gesture train the model and the fourth is held out for testing. `--seconds` changes recording length (default 3 seconds) and `--settle` changes the time to get into position (default 1.5 seconds). To change the repetition count, edit `TOTAL_REPS` and `TRAIN_REPS` in [train_wrist_rf.py](train_wrist_rf.py), keeping `1 <= TRAIN_REPS < TOTAL_REPS`.

Copy the exact directory printed after `Model and report saved in`. By default, each run creates a new directory under `~/wrist_emg_runs/`.

| Output | Contents |
| --- | --- |
| `wrist_rf.joblib` | Trained model and feature settings |
| `report.json` | Held-out accuracy and confusion matrices |
| `*.npz` | Raw samples and metadata for each completed recording |

With one held-out repetition per gesture, the report evaluates three held-out trials. Use the repetition-level results to see which gestures the model confused.

## 7. Run live prediction

Replace `wrist-rf-REPLACE_ME` with the directory name printed by training. You can also pass the full path to `wrist_rf.joblib`. Load models you created or obtained from a trusted source.

```bash
MODEL_DIR="$HOME/wrist_emg_runs/wrist-rf-REPLACE_ME"
bash connect_neural_band.sh "$BAND_MAC" &&
  .venv/bin/python predict_wrist_live.py --address "$BAND_MAC" --model "$MODEL_DIR"
```

The terminal displays the predicted gesture, its random forest score, and the number of EMG batches in the current window. Try rest, flexion, and extension with the band in the same position used for training. Press **Ctrl+C** to stop. The predictor displays labels and does not send motor commands.

## Reconnect after a reboot

```bash
cd ~/Pi-5-Stuff/Kinesis_Linux
BAND_MAC=AA:BB:CC:DD:EE:FF
bash connect_neural_band.sh "$BAND_MAC"
```

Wake the band if needed. The script checks for a saved BlueZ bond and waits up to 45 seconds for a fresh advertisement before attempting a connection. If a connection attempt fails or drops immediately, the script can retry up to three attempts total. Once it reports `Ready to start Kinesis` or `already connected`, run the stream, trainer, or predictor you want.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `.venv/bin/kinesis-linux` is missing or `No module named 'bleak'` | Return to the `Kinesis_Linux` directory and repeat step 1. Use `.venv/bin/python` for both installation and running the scripts. |
| Meta's Continue button stalls or WebKit logs EGL errors | From a graphical terminal, try `KINESIS_LOGIN_TRACE=1 LIBGL_ALWAYS_SOFTWARE=true GALLIUM_DRIVER=llvmpipe .venv/bin/kinesis-linux login`. Check for `Meta session saved` before starting enrollment. |
| No fresh advertisement | Wake the band, bring it close, and disconnect other hosts using it. Check the saved address with `bluetoothctl devices Bonded`. `.venv/bin/kinesis-linux scan --scan-seconds 20` lists nearby Meta Band advertisements. |
| `InProgress` or `le-connection-abort-by-local` | Let the pending attempt finish, then run one reconnect script. If the error repeats, run `bash check_neural_band.sh "$BAND_MAC"` for a controller summary. It uses `sudo` for `btmon`. |
| `EOFError`, channel read failure, or Bluetooth timeout | Check `bluetoothctl info "$BAND_MAC"` for `Connected: yes`. Run the reconnect script and retry with the bonded address. |
| `0x03001043` after Bluetooth connects | Kinesis needs the matching owner identity. Check the Pi user, bonded MAC, and state directory from enrollment. Recover the matching identity if available. If the band is yours and you need to replace the application identity, repeat steps 3–4 with the owning Meta account. |
| `0x03001042` during enrollment | Check that you signed in with the owning Meta account. |
| Warning about no stored band public key | Kinesis can continue after the band accepts the owner identity, but this session lacks independent verification of the band's long-term identity proof. Check whether the stream reaches the `connected` event. |
| Training keeps retrying a repetition | Verify decoded `emg_batch` events with step 5. The trainer retries if no 250 ms window received at least 24 of its expected 32 batches. Reconnect and check that the stream stays active if this repeats. |
| Model metadata does not match | Use the trainer and predictor from the same checkout. If the model was created with different feature settings, use its matching scripts or train a new model. |

## Raw capture, desktop controls, and development

To save the raw event stream as JSON lines, connect the band and run:

```bash
.venv/bin/kinesis-linux listen --address "$BAND_MAC" --raw-emg > band-events.jsonl
```

JSON goes to the file and diagnostics stay in the terminal. Press **Ctrl+C** to finish. `missing_before` reports sequence gaps. The ADC voltage scale and physical electrode order have not been established.

Optional desktop keyboard controls require `evdev` and read/write access to `/dev/uinput` for your regular user. The defaults include thumb swipe shortcuts, media taps, and index pinch-and-turn volume. See [mapping.example.json](mapping.example.json) for the full map.

```bash
.venv/bin/python -m pip install evdev
sudo modprobe uinput
.venv/bin/kinesis-linux listen --address "$BAND_MAC" --controls --mapping mapping.example.json
```

Kinesis defaults to adapter `hci0`. With multiple Bluetooth controllers, use `bluetoothctl list` to identify them and `--adapter hci1` (or the appropriate adapter) on direct Kinesis commands. The trainer and predictor use the CLI's default adapter, and the diagnostic script traces `hci0`.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

See [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md) for the protocol and Linux transport details. The protocol tests exercise the implementation without requiring a physical band or a Meta account.
