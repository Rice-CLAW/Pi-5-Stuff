"""CLI: scan, login, enroll, listen. Events on stdout; diagnostics on stderr."""
import argparse
import asyncio
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from . import auth
from .controls import Dial, Keyboard, Router
from .session import Session, identity_from_record


def state_dir():
    path = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "kinesis-linux"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def save_private(path, value):
    fd, temp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_private(path):
    if path.stat().st_mode & 0o077:
        raise ValueError(f"private file permissions are too open; run chmod 600 {path}")
    return json.loads(path.read_text())


async def run(args):
    from .transport import Transport, find_connected_band, scan
    if args.command == "scan":
        found = await scan(args.adapter, args.scan_seconds)
        for device, adv in found:
            print(json.dumps({"address": device.address, "name": adv.local_name or device.name, "rssi": adv.rssi}))
        return
    selected = await find_connected_band(args.address, args.adapter)
    if selected is not None:
        print(f"Using existing BlueZ connection to {selected.address}.", file=sys.stderr, flush=True)
    else:
        found = await scan(args.adapter, args.scan_seconds)
        selected = next((d for d, _ in found if d.address.upper() == args.address.upper()), None)
    if selected is None:
        raise ValueError("band not found in scan; keep it nearby/in pairing mode and run scan again")
    directory = state_dir()
    key_path = directory / (args.address.replace(":", "").lower() + ".identity.json")
    identity = None
    if args.identity:
        identity = identity_from_record(read_private(args.identity))
    elif key_path.exists() and args.command != "enroll":
        identity = identity_from_record(read_private(key_path))
    session_token = None
    if args.command == "enroll":
        session_token = read_private(directory / "meta-session.json")
    router = Router(json.loads(args.mapping.read_text()) if args.mapping else None)
    keyboard = Keyboard(router.mapping) if args.controls else None
    http = deque()
    dial = Dial()
    def emit(event, **values):
        if event in ("pair_request", "pair"):
            http.append((event, values))
            return
        if event == "identity_created":
            save_private(key_path, values["record"])
            print("Saved the band's owner identity.", file=sys.stderr)
            return
        dial_action = dial.feed(event, values)
        if dial_action:
            print(json.dumps({"event": "dial", "action": dial_action}), flush=True)
            if keyboard and dial_action in router.mapping:
                keyboard.press(dial_action)
        if event == "gesture":
            name = router.gesture(values)
            if name:
                values["mapping"] = name
                if keyboard:
                    keyboard.press(name)
        print(json.dumps(dict(event=event, received_at=time.monotonic(), **values)), flush=True)
    session = Session(emit, identity=identity, enroll=args.command == "enroll", raw=args.raw_emg)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    task = None
    try:
        async with Transport(selected, args.adapter) as transport:
            await transport.send(session.request())
            started = last_poll = time.monotonic()
            task = asyncio.create_task(transport.receive())
            try:
                while not stop.is_set():
                    dial.tick()
                    done, _ = await asyncio.wait({task}, timeout=.05)
                    if task in done:
                        await transport.send(session.feed(task.result()))
                        task = asyncio.create_task(transport.receive())
                    while http:
                        event, values = http.popleft()
                        signature, receipt, key = await asyncio.to_thread(auth.claim, event, values, session_token)
                        await transport.send(session.receipt(signature, receipt, key))
                    now = time.monotonic()
                    if args.command == "enroll" and session.streaming:
                        print("Enrollment complete. Run listen to reconnect with the saved identity.", file=sys.stderr)
                        break
                    if not session.streaming and now - started > (180 if args.command == "enroll" else 35):
                        raise TimeoutError(f"band startup timed out at {session.stage}")
                    if session.streaming and now - session.last_authenticated > 10:
                        raise TimeoutError("band stopped replying to authenticated queries")
                    if session.streaming and now - session.last_authenticated > 2 and now - last_poll > 2:
                        await transport.send(session.stream(5))
                        last_poll = now
            finally:
                # Disable on the original subscription, then await an authenticated ack.
                outgoing = session.stop()
                if outgoing:
                    await transport.send(outgoing)
                    deadline = time.monotonic() + 3
                    while not session.stop_ack and time.monotonic() < deadline:
                        done, _ = await asyncio.wait({task}, timeout=max(0, deadline - time.monotonic()))
                        if not done:
                            break
                        await transport.send(session.feed(task.result()))
                        task = asyncio.create_task(transport.receive())
                    if not session.stop_ack:
                        print("Stream-disable acknowledgement was not received.", file=sys.stderr)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
        if keyboard:
            keyboard.close()


def main():
    parser = argparse.ArgumentParser(description="Experimental Meta Neural Band Linux client")
    parser.add_argument("command", choices=("scan", "login", "enroll", "listen"))
    parser.add_argument("--address", help="band's Linux Bluetooth MAC (from scan)")
    parser.add_argument("--adapter", default="hci0")
    parser.add_argument("--scan-seconds", type=float, default=10)
    parser.add_argument("--identity", type=Path, help="existing owner identity record (chmod 600)")
    parser.add_argument("--raw-emg", action="store_true")
    parser.add_argument("--controls", action="store_true", help="enable virtual keyboard; off by default")
    parser.add_argument("--mapping", type=Path, help="JSON gesture name -> Linux key names")
    args = parser.parse_args()
    if not re.fullmatch(r"hci\d+", args.adapter):
        parser.error("adapter must be hciN")
    if args.command in ("enroll", "listen") and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.address or ""):
        parser.error("use --address AA:BB:CC:DD:EE:FF from scan")
    try:
        with (state_dir() / "band.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("another Kinesis Linux process is active") from None
            if args.command == "login":
                save_private(state_dir() / "meta-session.json", auth.login())
                print("Meta session saved. Put the band in pairing mode, then run enroll.")
            else:
                asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # HTTP libraries can include query secrets in exception repr; don't print them.
        if isinstance(error, (ValueError, TimeoutError, FileNotFoundError, PermissionError)):
            print(f"Kinesis: {error}", file=sys.stderr)
        else:
            print(f"Kinesis failed ({type(error).__name__}). Check Bluetooth pairing, adapter, and identity. See README troubleshooting.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
