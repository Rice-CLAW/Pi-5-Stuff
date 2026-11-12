import ctypes
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from dbus_fast import MessageType, Variant
from kinesis_linux.cli import save_private, read_private
from kinesis_linux.controls import Dial
from kinesis_linux.transport import PSM_UUID, Transport, find_connected_band, sockaddr, make_socket


class LinuxTests(unittest.TestCase):
    def test_address_abi_for_python_before_314(self):
        with patch.object(socket, "AF_BLUETOOTH", 31, create=True):
            address = sockaddr("AA:BB:CC:DD:EE:FF", 255, 2)
        self.assertEqual(ctypes.sizeof(address), 14)
        data = bytes(address)
        self.assertEqual(data[2:4], b"\xff\x00")
        self.assertEqual(data[4:10], bytes.fromhex("ffeeddccbbaa"))
        self.assertEqual(data[12], 2)

    def test_private_file_atomic_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            save_private(path, {"secret": "test"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_private(path), {"secret": "test"})
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                read_private(path)

    def test_dial_requires_fresh_pinch_and_confirmed_hand(self):
        now = [0.]
        dial = Dial(clock=lambda: now[0])
        dial.feed("handedness", {"hand": "right"})
        dial.feed("gyro", {"timestamp_us": 1000000, "values": [0, 0, 0]})
        dial.feed("gesture", {"finger": "index", "action": "press", "derived_action": "unknown"})
        output = []
        for i in range(1, 51):
            now[0] = i / 100
            value = dial.feed("gyro", {"timestamp_us": 1000000+i*10000, "values": [1000, 0, 0]})
            if value:
                output.append(value)
        self.assertTrue(output)
        self.assertEqual(set(output), {"dial_up"})
        now[0] += .4
        dial.tick()
        self.assertFalse(dial.engaged)
        self.assertIsNone(dial.pressed)

    def test_dial_left_hand_reverses(self):
        now = [0.]
        dial = Dial(clock=lambda: now[0])
        dial.feed("handedness", {"hand": "left"})
        dial.feed("gyro", {"timestamp_us": 1000000, "values": [0, 0, 0]})
        dial.feed("gesture", {"finger": "index", "action": "press", "derived_action": "unknown"})
        output = []
        for i in range(1, 51):
            now[0] = i / 100
            value = dial.feed("gyro", {"timestamp_us": 1000000+i*10000, "values": [1000, 0, 0]})
            if value:
                output.append(value)
        self.assertTrue(output)
        self.assertEqual(set(output), {"dial_down"})

    def test_transport_uses_three_part_native_address(self):
        from unittest.mock import Mock
        fake = Mock()
        with patch.object(socket, "AF_BLUETOOTH", 31, create=True), patch.object(socket, "BTPROTO_L2CAP", 0, create=True), patch("kinesis_linux.transport.socket.socket", return_value=fake), patch("kinesis_linux.transport.native_bind"):
            sock, remote = make_socket("00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF", 2, 255)
        self.assertEqual(remote, ("AA:BB:CC:DD:EE:FF", 255, 2))
        self.assertIs(sock, fake)


class ConnectedBandTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_live_gatt_object_with_previous_random_address_in_path(self):
        adapter = "/org/bluez/hci0"
        device = adapter + "/dev_74_F7_11_80_FD_73"
        characteristic = device + "/service0023/char0027"
        address = "A4:0E:2B:92:6C:59"
        local = "AA:BB:CC:DD:EE:FF"
        objects = {
            adapter: {"org.bluez.Adapter1": {"Address": Variant("s", local)}},
            device: {"org.bluez.Device1": {
                "Address": Variant("s", address),
                "AddressType": Variant("s", "public"),
                "Connected": Variant("b", True),
                "Bonded": Variant("b", True),
                "ServicesResolved": Variant("b", True),
            }},
            characteristic: {"org.bluez.GattCharacteristic1": {"UUID": Variant("s", PSM_UUID)}},
        }
        bus = Mock(call=AsyncMock(side_effect=[
            SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[objects]),
            SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[b"\xff\x00"]),
        ]))
        sock = Mock()
        sock.getsockopt.return_value = (128).to_bytes(2, "little")
        remote = (address, 255, 1)
        module = "kinesis_linux.transport"
        with (patch(f"{module}.MessageBus") as bus_type,
              patch(f"{module}.BleakClient") as client_type,
              patch(f"{module}.make_socket", return_value=(sock, remote)) as make,
              patch(f"{module}.native_connect", new_callable=AsyncMock) as connect):
            bus_type.return_value.connect = AsyncMock(return_value=bus)
            band = await find_connected_band(address, "hci0")
            async with Transport(band):
                make.assert_called_once_with(local, address, 1, 255)
                connect.assert_awaited_once_with(sock, *remote)
            client_type.assert_not_called()
        read_request = bus.call.await_args_list[1].args[0]
        self.assertEqual((read_request.path, read_request.member), (characteristic, "ReadValue"))
        sock.close.assert_called_once()
