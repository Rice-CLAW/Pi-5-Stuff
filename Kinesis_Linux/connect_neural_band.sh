
#!/usr/bin/env bash
# Reconnect an already-bonded Meta Neural Band through BlueZ.
set -u
export LC_ALL=C

MAC=${1:-A4:0E:2B:92:6C:59}
ATTEMPTS=3
SCAN_SECONDS=45
CONNECT_SECONDS=25

if (( $# > 1 )) || [[ ! $MAC =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    printf 'Usage: bash %s [Bluetooth MAC]\n' "$0" >&2
    exit 2
fi

for command in bluetoothctl stdbuf awk grep; do
    if ! command -v "$command" >/dev/null; then
        printf 'Required command is missing: %s\n' "$command" >&2
        exit 2
    fi
done

device_info() {
    bluetoothctl --timeout 5 info "$MAC" 2>&1
}

connected() {
    grep -qE '^[[:space:]]*Connected:[[:space:]]+yes' <<<"$1"
}

# Check multiple snapshots because a link can appear briefly during setup.
connection_held() {
    local info
    for check in 1 2 3; do
        info=$(device_info) || return 1
        connected "$info" || return 1
        if (( check < 3 )); then
            sleep 0.7
        fi
    done
    return 0
}

initial=$(device_info)
if ! grep -qE '^[[:space:]]*Bonded:[[:space:]]+yes' <<<"$initial"; then
    printf 'BlueZ did not report a saved bond for %s. Check bluetoothctl info and show.\n' "$MAC" >&2
    exit 2
fi
if connected "$initial" && connection_held; then
    printf 'Meta Neural Band is already connected at %s.\n' "$MAC"
    exit 0
fi

tmp=$(mktemp -d) || exit 2
scan_pid=
stop_scan() {
    if [[ -n $scan_pid ]]; then
        kill -TERM "$scan_pid" 2>/dev/null || true
        wait "$scan_pid" 2>/dev/null || true
        scan_pid=
    fi
}
cleanup() {
    stop_scan
    rm -rf -- "$tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    printf 'Attempt %s/%s. Scanning for %s. Wake the band if needed.\n' "$attempt" "$ATTEMPTS" "$MAC"
    # Discovery stays running until the current connect attempt finishes.
    stdbuf -oL bluetoothctl --timeout "$((SCAN_SECONDS + CONNECT_SECONDS + 4))" scan le \
        >"$tmp/scan" 2>&1 &
    scan_pid=$!
    deadline=$((SECONDS + SCAN_SECONDS))
    advertisement=
    while (( SECONDS < deadline )); do
        advertisement=$(grep -iF -- "$MAC" "$tmp/scan" |
            awk '/RSSI:|ManufacturerData[.]Key:|AdvertisingFlags:/ {print; exit}')
        [[ -z $advertisement ]] || break
        kill -0 "$scan_pid" 2>/dev/null || break
        sleep 0.2
    done

    if [[ -z $advertisement ]]; then
        printf 'No fresh advertisement for %s. Wake the band and run this again.\n' "$MAC" >&2
        grep -iE 'Failed to start discovery|No default controller|NotReady|Meta Band 00FM' \
            "$tmp/scan" | tail -n 5 >&2
        exit 1
    fi

    printf 'Band heard. Connecting…\n'
    stdbuf -oL bluetoothctl --timeout "$CONNECT_SECONDS" connect "$MAC" 2>&1 |
        awk '/Connection successful|Failed to connect|not available|No default controller/ {print; fflush()}'
    stop_scan

    if connection_held; then
        printf 'Meta Neural Band connected at %s. Ready to start Kinesis.\n' "$MAC"
        exit 0
    fi
    if (( attempt < ATTEMPTS )); then
        printf 'Connection did not stay up. Retrying after a fresh advertisement.\n'
        sleep 1
    fi
done

printf 'Could not hold a connection after %s attempts. Run check_neural_band.sh for controller details.\n' \
    "$ATTEMPTS" >&2
exit 1


