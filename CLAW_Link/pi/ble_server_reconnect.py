"""CLAW BLE test server with reconnect handling and automatic pairing."""

import argparse
import asyncio
import logging
from importlib.metadata import version

from bless import (
    BlessServer,
    GATTCharacteristicProperties as Props,
    GATTAttributePermissions as Perms,
)
from dbus_next import DBusError
from dbus_next.service import ServiceInterface, method
from bless.backends.bluezdbus.dbus.characteristic import (
    BlueZGattCharacteristic,
)
from bless.backends.bluezdbus.dbus import service as service_module


SERVICE = "802b1000-6a92-4a6f-9b2b-769c88951a01"
COMMAND = "802b1001-6a92-4a6f-9b2b-769c88951a01"
STATUS = "802b1002-6a92-4a6f-9b2b-769c88951a01"

AGENT_PATH = "/org/claw/AutoPairAgent"


class AutoPairAgent(ServiceInterface):
    """Automatically approve Pi-side pairing confirmations."""

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        logging.info(
            "Automatically approving pairing code %06d for %s",
            passkey,
            device,
        )

    @method()
    def RequestAuthorization(self, device: "o"):
        logging.info(
            "Automatically approving pairing for %s", device
        )

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        logging.info(
            "Automatically approving service %s for %s",
            uuid,
            device,
        )

    @method()
    def DisplayPasskey(
        self, device: "o", passkey: "u", entered: "q"
    ):
        logging.info(
            "Pairing code for %s: %06d (%d digits entered)",
            device,
            passkey,
            entered,
        )

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        logging.info(
            "Pairing PIN for %s: %s", device, pincode
        )

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        raise DBusError(
            "org.bluez.Error.Rejected",
            "PIN entry is not supported by this automatic agent",
        )

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        raise DBusError(
            "org.bluez.Error.Rejected",
            "Passkey entry is not supported by this automatic agent",
        )

    @method()
    def Cancel(self):
        logging.info("Pairing request cancelled")

    @method()
    def Release(self):
        logging.info("Automatic pairing agent released")


class ReconnectCharacteristic(BlueZGattCharacteristic):
    """Maintain notification state across start/stop cycles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._notifying = False

    @method()
    def StartNotify(self):
        if not any(
            flag in self._flags for flag in ("notify", "indicate")
        ):
            raise DBusError(
                "org.bluez.Error.NotSupported",
                "Characteristic does not support notifications",
            )

        if self._notifying:
            logging.info("StartNotify repeated: %s", self._uuid)
            return

        callback = self._service.app.StartNotify
        if callback is not None:
            callback(None)

        subscribed = self._service.app.subscribed_characteristics
        if self._uuid not in subscribed:
            subscribed.append(self._uuid)

        self._notifying = True
        self.emit_properties_changed({"Notifying": True})

        logging.info(
            "StartNotify: notifications ON for %s", self._uuid
        )

    @method()
    def StopNotify(self):
        if not self._notifying:
            logging.info("StopNotify repeated: %s", self._uuid)
            return

        callback = self._service.app.StopNotify
        if callback is not None:
            callback(None)

        subscribed = self._service.app.subscribed_characteristics
        while self._uuid in subscribed:
            subscribed.remove(self._uuid)

        self._notifying = False
        self.emit_properties_changed({"Notifying": False})

        logging.info(
            "StopNotify: notifications OFF for %s", self._uuid
        )


def install_workaround():
    if (
        version("bless") != "0.2.6"
        or version("bleak") != "0.22.3"
    ):
        raise RuntimeError(
            "Requires bless==0.2.6 and bleak==0.22.3"
        )

    # This changes only the current process, not installed packages.
    service_module.BlueZGattCharacteristic = ReconnectCharacteristic


async def main():
    install_workaround()

    loop = asyncio.get_running_loop()
    server = BlessServer(name="CLAW-Pi", loop=loop)
    queue = asyncio.Queue(maxsize=32)

    tasks = []
    agent = None
    agent_manager = None
    agent_registered = False
    server_started = False

    def write(characteristic, value, **kwargs):
        if characteristic.uuid.lower() != COMMAND:
            return

        payload = bytes(value)

        def enqueue():
            if len(payload) > 20:
                logging.warning("Ignoring command over 20 bytes")
            elif queue.full():
                logging.warning("Command queue full")
            else:
                queue.put_nowait(payload)

        loop.call_soon_threadsafe(enqueue)

    server.read_request_func = (
        lambda characteristic, **kwargs: characteristic.value
    )
    server.write_request_func = write

    try:
        await server.add_new_service(SERVICE)

        await server.add_new_characteristic(
            SERVICE,
            COMMAND,
            Props.write,
            bytearray(),
            Perms.writeable,
        )

        await server.add_new_characteristic(
            SERVICE,
            STATUS,
            Props.read | Props.notify,
            bytearray(b"READY"),
            Perms.readable,
        )

        status = server.get_characteristic(STATUS)

        # Register the automatic pairing agent on the server's D-Bus
        # connection so it is released when this process exits.
        agent = AutoPairAgent()
        server.bus.export(AGENT_PATH, agent)

        introspection = await server.bus.introspect(
            "org.bluez", "/org/bluez"
        )
        bluez = server.bus.get_proxy_object(
            "org.bluez", "/org/bluez", introspection
        )
        agent_manager = bluez.get_interface(
            "org.bluez.AgentManager1"
        )

        await agent_manager.call_register_agent(
            AGENT_PATH, "DisplayYesNo"
        )
        agent_registered = True

        await agent_manager.call_request_default_agent(AGENT_PATH)

        logging.info(
            "Automatic Pi-side pairing approval enabled"
        )

        def publish(message):
            # Keep the value readable without a subscription.
            status.value = bytearray(message)

            if not status.gatt.Notifying:
                logging.debug(
                    "Stored %r; no active notification session",
                    message,
                )
                return

            if not server.update_value(SERVICE, STATUS):
                raise RuntimeError(
                    "Status characteristic could not be updated"
                )

            # Local submission does not prove phone receipt.
            logging.debug(
                "Notification submitted: %r", message
            )

        async def counter_loop():
            counter = 0

            while True:
                await asyncio.sleep(1)
                counter = (counter + 1) % 1_000_000_000
                publish(f"T:{counter}".encode())

        async def command_loop():
            while True:
                value = await queue.get()
                logging.info("Command received: %r", value)

                if value == b"PING":
                    publish(b"PONG")
                else:
                    publish(b"ERR:COMMAND")

        await server.start()
        server_started = True

        logging.info(
            "CLAW-Pi advertising; notification-state workaround active"
        )

        tasks = [
            asyncio.create_task(counter_loop()),
            asyncio.create_task(command_loop()),
        ]

        await asyncio.gather(*tasks)

    finally:
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks, return_exceptions=True
            )

        if agent_registered and agent_manager is not None:
            try:
                await agent_manager.call_unregister_agent(
                    AGENT_PATH
                )
            except Exception as error:
                logging.warning(
                    "Could not unregister pairing agent: %s",
                    error,
                )

        if server_started:
            try:
                await server.stop()
            except Exception as error:
                logging.warning(
                    "Could not stop BLE server cleanly: %s",
                    error,
                )

        bus = getattr(server, "bus", None)
        if bus is not None:
            if agent is not None:
                bus.unexport(AGENT_PATH, agent)
            bus.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
