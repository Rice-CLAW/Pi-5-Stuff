import argparse
import socket
import struct
import time

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["send", "receive"])
parser.add_argument("host", help="Pi Tailscale IPv4 address")
args = parser.parse_args()

port = 6677
frame = struct.Struct("!I8f")  # Sequence number and eight EMG values

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    if args.mode == "receive":
        sock.bind((args.host, port))
        count = 0
        deadline = time.monotonic() + 1

        while True:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                packet, _ = sock.recvfrom(1200)
                if packet and len(packet) % frame.size == 0:
                    count += len(packet) // frame.size
            except socket.timeout:
                pass

            if time.monotonic() >= deadline:
                print(f"{count} samples per channel", flush=True)
                count = 0
                deadline = time.monotonic() + 1
    else:
        from mindrove.board_shim import BoardIds, BoardShim, MindRoveInputParams

        board_id = BoardIds.SYNTHETIC_BOARD.value
        board = BoardShim(board_id, MindRoveInputParams())
        channels = BoardShim.get_emg_channels(board_id)[:8]
        board.prepare_session()

        sequence = 0
        try:
            board.start_stream()
            while True:
                time.sleep(0.04)
                samples = board.get_board_data()[channels].T
                for start in range(0, len(samples), 30):
                    packet = bytearray()
                    for sample in samples[start : start + 30]:
                        packet.extend(frame.pack(sequence, *sample))
                        sequence = (sequence + 1) % (2**32)
                    sock.sendto(packet, (args.host, port))
        finally:
            board.release_session()
