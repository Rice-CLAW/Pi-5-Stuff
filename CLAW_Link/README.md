# CLAW Link

CLAW Link connects a phone to the Raspberry Pi 5 over Bluetooth Low Energy. The offline calibration demo walks through six gestures, trains a random forest on prerecorded EMG data, and displays eight simulated signal channels after training.

## Run the calibration demo

On the Pi, run the following from `CLAW_Link/pi/`. Create the virtual environment once if `.venv` does not already exist.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r calibration/requirements.txt
.venv/bin/python calibration/server.py --check-data
.venv/bin/python calibration/server.py
```

The server uses the converted `gestures_csv` dataset, including its `manifest.json`. If the data check cannot find it, pass `--data-dir /path/to/gestures_csv` to both server commands.

1. Save `web/claw-calibration.html` on your phone and open it in a browser app that supports Bluetooth from local HTML files. For Android most browser's should be able to do this, for IOS I've only been able to find this that works: https://apps.apple.com/us/app/bluetooth-browser/id6455128847
2. Tap **Connect to Pi**, select **CLAW-Pi**, and tap **Calibrate**.
3. Start each gesture when prompted. After the sixth step, the Pi trains the model and switches to inference mode. The eight simulated signal graphs then start updating.

The HTML runs offline once saved to your phone. Press **Ctrl+C** on the Pi to stop the server.

## Test the Bluetooth connection

For a basic connection check, run `pi/ble_server_reconnect.py` with the dependencies in `pi/requirements.txt`, then open `web/claw-local-test.html` on your phone. Run one Pi server at a time.
