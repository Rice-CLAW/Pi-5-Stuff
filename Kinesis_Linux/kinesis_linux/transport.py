"""BlueZ GATT discovery plus a kernel LE credit-based L2CAP socket."""
import asyncio
import ctypes
from dataclasses import dataclass
import errno
import socket
import struct
import sys
import os
from bleak import BleakClient, BleakScanner
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType

PSM_UUID = "2d41da7c-82b6-42aa-b34e-e2e01df8cc1a"


@dataclass(frozen=True)
class ConnectedBand:
    address: str
    address_type: str
    local_address: str
    characteristic_path: str


async def find_connected_band(address, adapter):
    """Resolve a live, bonded connection by address, independent of its object path."""
    adapter_path = f"/org/bluez/{adapter}"
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination="org.bluez", path="/",
            interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
        ))
        if reply.message_type == MessageType.ERROR:
            raise ValueError(f"cannot list BlueZ devices: {reply.error_name}")
        objects = reply.body[0]
        for path, interfaces in objects.items():
            props = interfaces.get("org.bluez.Device1")
            if not props or not path.startswith(adapter_path + "/dev_"):
                continue
            if props["Address"].value.upper() != address.upper():
                continue
            if not all(props.get(key) and props[key].value
                       for key in ("Connected", "Bonded", "ServicesResolved")):
                continue
            for char_path, char_interfaces in objects.items():
                char = char_interfaces.get("org.bluez.GattCharacteristic1")
                if (char and char_path.startswith(path + "/")
                        and char["UUID"].value.lower() == PSM_UUID):
                    local = objects[adapter_path]["org.bluez.Adapter1"]["Address"].value
                    return ConnectedBand(props["Address"].value, props["AddressType"].value,
                                         local, char_path)
        return None
    finally:
        bus.disconnect()


async def read_connected_psm(path):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await asyncio.wait_for(bus.call(Message(
            destination="org.bluez", path=path,
            interface="org.bluez.GattCharacteristic1", member="ReadValue",
            signature="a{sv}", body=[{}],
        )), timeout=15)
        if reply.message_type == MessageType.ERROR:
            detail = reply.body[0] if reply.body else ""
            raise ValueError(f"BlueZ channel read failed: {reply.error_name} {detail}".rstrip())
        return bytes(reply.body[0])
    except TimeoutError:
        raise TimeoutError("BlueZ channel read timed out") from None
    except EOFError:
        raise ValueError("BlueZ closed the D-Bus connection during the channel read") from None
    finally:
        bus.disconnect()


async def scan(adapter="hci0", timeout=10):
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True, adapter=adapter)
    return [(d, a) for d, a in discovered.values() if (a.local_name or d.name or "").lower().startswith("meta band")]


async def bluez_properties(device):
    # Device1.Address may be an identity address after bonding; always ask BlueZ.
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        path = device.details["path"]
        reply = await bus.call(Message(destination="org.bluez", path=path, interface="org.freedesktop.DBus.Properties", member="GetAll", signature="s", body=["org.bluez.Device1"]))
        if reply.message_type == MessageType.ERROR:
            raise ValueError("cannot read BlueZ device address type")
        properties = {k: v.value for k, v in reply.body[0].items()}
        adapter_reply = await bus.call(Message(destination="org.bluez", path=path.rsplit("/dev_", 1)[0], interface="org.freedesktop.DBus.Properties", member="GetAll", signature="s", body=["org.bluez.Adapter1"]))
        if adapter_reply.message_type == MessageType.ERROR:
            raise ValueError("cannot read the selected BlueZ adapter")
        properties["LocalAddress"] = adapter_reply.body[0]["Address"].value
        return properties
    finally:
        bus.disconnect()


def make_socket(local_address, remote_address, address_type, psm):
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise ValueError("this Python build has no Linux Bluetooth socket support")
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        # SOL_BLUETOOTH=274, BT_SECURITY=4; level 2 requires link encryption.
        sock.setsockopt(274, 4, struct.pack("BB", 2, 0))
        # BDADDR_LE_PUBLIC=1 / BDADDR_LE_RANDOM=2. Binding selects the LE bearer.
        native_bind(sock, local_address)
        # BT_MODE=15; LE credit-based flow control=3, NOT extended mode=4.
        sock.setsockopt(274, 15, bytes([3]))
        sock.setblocking(False)
        return sock, (remote_address, psm, address_type)
    except BaseException:
        sock.close()
        raise


class SockAddrL2(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("psm", ctypes.c_ushort),
                ("address", ctypes.c_ubyte * 6), ("cid", ctypes.c_ushort),
                ("address_type", ctypes.c_ubyte)]


def sockaddr(address, psm, address_type):
    value = SockAddrL2()
    value.family = socket.AF_BLUETOOTH
    value.psm = int.from_bytes(psm.to_bytes(2, "little"), sys.byteorder)
    value.address[:] = bytes.fromhex(address.replace(":", ""))[::-1]
    value.address_type = address_type
    return value


def syscall(name, sock, address):
    # CPython before 3.14 only accepts two-element L2CAP tuples, losing LE
    # address type. Pass the Linux sockaddr_l2 ABI directly, with no helper binary.
    libc = ctypes.CDLL(None, use_errno=True)
    call = getattr(libc, name)
    call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(sock.fileno(), ctypes.byref(address), ctypes.sizeof(address)) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def native_bind(sock, address):
    syscall("bind", sock, sockaddr(address, 0, 1))


async def native_connect(sock, address, psm, address_type):
    try:
        syscall("connect", sock, sockaddr(address, psm, address_type))
        return
    except OSError as error:
        if error.errno not in (errno.EINPROGRESS, errno.EWOULDBLOCK):
            raise
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    fd = sock.fileno()
    def writable():
        if not ready.done():
            ready.set_result(None)
    loop.add_writer(fd, writable)
    try:
        await ready
        error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if error:
            raise OSError(error, os.strerror(error))
    finally:
        loop.remove_writer(fd)


class Transport:
    def __init__(self, device, adapter="hci0"):
        self.device, self.adapter = device, adapter
        self.client = self.sock = None

    async def __aenter__(self):
        try:
            if isinstance(self.device, ConnectedBand):
                band = self.device
                psm_bytes = await read_connected_psm(band.characteristic_path)
                local, address, address_kind = band.local_address, band.address, band.address_type
            else:
                # Bleak holds the ACL connection while the L2CAP fd is alive.
                self.client = BleakClient(self.device, adapter=self.adapter, pair=True, timeout=45)
                await self.client.connect()
                psm_bytes = bytes(await self.client.read_gatt_char(PSM_UUID))
                props = await bluez_properties(self.device)
                local = props["LocalAddress"]
                address = props.get("Address", self.device.address)
                address_kind = props.get("AddressType")
            if psm_bytes != b"\xff\x00":
                raise ValueError("unsupported input PSM; upstream verifies only 255")
            address_type = {"public": 1, "random": 2}.get(address_kind)
            if address_type is None:
                raise ValueError("BlueZ did not report public/random address type")
            try:
                self.sock, remote = make_socket(local, address, address_type, 255)
                await asyncio.wait_for(native_connect(self.sock, *remote), 15)
            except TimeoutError:
                raise TimeoutError("band data channel (L2CAP PSM 255) timed out") from None
            except OSError as error:
                raise ValueError(f"band data channel (L2CAP PSM 255): {error}") from error
            # Respect the peer's negotiated outgoing SDU MTU.
            self.send_mtu = min(128, int.from_bytes(self.sock.getsockopt(274, 12, 2), byteorder=sys.byteorder))
            if self.send_mtu < 1:
                raise ValueError("invalid negotiated L2CAP MTU")
            return self
        except BaseException:
            try:
                await self.__aexit__(None, None, None)
            except Exception:
                # A disconnect failure must not hide the original setup error.
                pass
            raise

    async def send(self, data):
        if not data:
            return
        # Preserve SOCK_SEQPACKET SDU boundaries; do not retry partial sends.
        # Each AirShield record can span SDUs, as with upstream's stream transport.
        for offset in range(0, len(data), self.send_mtu):
            part = data[offset:offset + self.send_mtu]
            while True:
                try:
                    count = self.sock.send(part)
                    break
                except BlockingIOError:
                    loop = asyncio.get_running_loop()
                    ready = loop.create_future()
                    fd = self.sock.fileno()
                    def writable():
                        if not ready.done():
                            ready.set_result(None)
                    loop.add_writer(fd, writable)
                    try:
                        await ready
                    finally:
                        loop.remove_writer(fd)
            if count != len(part):
                raise OSError("partial L2CAP SDU write")

    async def receive(self):
        data = await asyncio.get_running_loop().sock_recv(self.sock, 65536)
        if not data:
            raise ConnectionError("band closed the input channel")
        return data

    async def __aexit__(self, *_):
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.client:
            await self.client.disconnect()
            self.client = None
