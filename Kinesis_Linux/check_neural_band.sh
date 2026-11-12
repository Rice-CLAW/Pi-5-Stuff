#!/usr/bin/env bash
# Check advertisements and make one BLE connection attempt on a Raspberry Pi.
set -u -o pipefail
export LC_ALL=C

MAC=${1:-A4:0E:2B:92:6C:59}
SCAN_SECONDS=45
CONNECT_SECONDS=25
if [[ ! $MAC =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    printf 'Usage: bash %s [Bluetooth MAC]\n' "$0" >&2
    exit 2
fi
for cmd in bluetoothctl btmon sudo timeout stdbuf awk; do
    if ! command -v "$cmd" >/dev/null; then
        printf 'Required command is missing: %s\n' "$cmd" >&2
        exit 2
    fi
done

show_status() {
    bluetoothctl --timeout 5 info "$MAC" |
        awk '/^[[:space:]]+(Paired|Bonded|Connected):/ {print}'
}
if show_status 2>/dev/null | grep -q 'Connected: yes'; then
    printf '%s is already connected.\n' "$MAC"
    show_status
    exit 0
fi

# Keep captures private and discard raw controller output at exit.
tmp=$(mktemp -d) || exit 2
monitor_pid=
scan_pid=
stop_child() {
    if [[ -n $1 ]]; then
        kill -TERM "$1" 2>/dev/null || true
        wait "$1" 2>/dev/null || true
    fi
}
cleanup() {
    stop_child "$scan_pid"
    stop_child "$monitor_pid"
    rm -rf -- "$tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Authenticating sudo for the Bluetooth controller trace.\n'
sudo -v || exit 2
sudo -n timeout --kill-after=2s "$((SCAN_SECONDS + CONNECT_SECONDS + 12))s" stdbuf -oL btmon -i 0 \
    >"$tmp/hci" 2>"$tmp/monitor-error" &
monitor_pid=$!
sleep 0.25
if ! kill -0 "$monitor_pid" 2>/dev/null; then
    printf 'Could not start btmon:\n' >&2
    cat "$tmp/monitor-error" >&2
    exit 2
fi

printf 'Waiting up to %s seconds for an advertisement from %s. Wake the band if needed.\n' \
    "$SCAN_SECONDS" "$MAC"
# Leave discovery running through the connection attempt. The longer timeout
# lets the scanner stay open even if the advertisement arrives near the end.
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
    printf 'No fresh advertisement matched %s. No connection was attempted.\n' "$MAC"
    grep -iE -- "$MAC|Meta Band 00FM|Failed to start discovery|No default controller|NotReady" "$tmp/scan" | tail -n 8
    offset=-1
else
    printf 'Advertisement received:\n%s\n\n' "$advertisement"
    offset=$(wc -l <"$tmp/hci")
    printf 'Attempting one connection to %s…\n' "$MAC"
    stdbuf -oL bluetoothctl --timeout "$CONNECT_SECONDS" connect "$MAC" 2>&1 |
        awk -v mac="$MAC" '
            (index(toupper($0), toupper(mac)) && /Attempting to connect|Connected:|ServicesResolved:/) ||
            /Connection successful|Failed to connect|not available|No default controller/ {print; fflush()}
        ' || true
    sleep 1
fi

status=$(show_status 2>/dev/null || true)
stop_child "$scan_pid"
scan_pid=
stop_child "$monitor_pid"
monitor_pid=

printf '\nController summary (hci0)\n'
# Print only advertising type and connection status fields, not packet payloads.
awk -v mac="$MAC" -v cutoff="$offset" '
    function finish_ad() {
        if (advertising && matches && fields != "") {
            recent[count++ % 3] = fields
        }
        matches = 0
        fields = ""
    }
    /^[<>@=*]/ {
        finish_ad()
        advertising = 0
        connection = 0
        if (cutoff >= 0 && NR > cutoff && /Disconnect(ion)? Complete|Encryption Change|Authentication Complete|LE (Extended )?Create Connection Cancel/) {
            connection = 1
            print $0
            printed++
        }
    }
    /LE (Extended )?Advertising Report/ { advertising = 1 }
    advertising {
        if (/Event type:/) {
            if (fields != "") finish_ad()
            fields = $0
        }
        if (index(toupper($0), toupper(mac)) || /Name \(.*\): Meta Band 00FM/) matches = 1
        if (/Address:|RSSI:|^[[:space:]]+(Non-)?[Cc]onnectable[[:space:]]*$/) fields = fields "\n" $0
    }
    cutoff >= 0 && NR > cutoff && /LE (Enhanced )?Connection Complete/ {
        connection = 1
        print $0
        printed++
    }
    connection && /Status:|Reason:|Peer address:|Handle:/ {print}
    END {
        finish_ad()
        if (cutoff >= 0 && !printed) print "No connection, encryption, or disconnect event captured during the attempt."
        if (!count) print "No raw advertising report matched the saved MAC or band name. The radio address may be rotating."
        else {
            print "Recent matching advertising reports:"
            for (i = (count > 3 ? count - 3 : 0); i < count; i++) print recent[i % 3]
        }
    }
' "$tmp/hci"

printf '\nFinal BlueZ state\n%s\n' "${status:-No device status returned.}"
if [[ $status == *'Connected: yes'* ]]; then
    exit 0
fi
exit 1
