"""CLAW Link v0.1: test-only BLE server. No sensors or motors."""
import asyncio
import logging
from bless import BlessServer, GATTCharacteristicProperties as Props, GATTAttributePermissions as Perms

SERVICE = "802b1000-6a92-4a6f-9b2b-769c88951a01"
COMMAND = "802b1001-6a92-4a6f-9b2b-769c88951a01"
STATUS = "802b1002-6a92-4a6f-9b2b-769c88951a01"

async def main():
    loop = asyncio.get_running_loop()
    server = BlessServer(name="CLAW-Pi", loop=loop)
    queue = asyncio.Queue(maxsize=32)

    def write(characteristic, value, **kwargs):
        if characteristic.uuid.lower() == COMMAND:
            def enqueue():
                if not queue.full():
                    queue.put_nowait(bytes(value)[:20])
            loop.call_soon_threadsafe(enqueue)

    server.read_request_func = lambda characteristic, **kwargs: characteristic.value
    server.write_request_func = write
    await server.add_new_service(SERVICE)
    await server.add_new_characteristic(SERVICE, COMMAND, Props.write, bytearray(), Perms.writeable)
    await server.add_new_characteristic(SERVICE, STATUS, Props.read | Props.notify,
                                        bytearray(b"READY"), Perms.readable)

    def notify(message):
        server.get_characteristic(STATUS).value = bytearray(message)
        server.update_value(SERVICE, STATUS)

    await server.start()
    logging.info("CLAW-Pi advertising. Open the HTTPS website on your phone.")
    counter = 0
    try:
        while True:
            try:
                value = await asyncio.wait_for(queue.get(), timeout=1)
                notify(b"PONG" if value == b"PING" else b"ERR:COMMAND")
                logging.info("Received %r", value)
            except asyncio.TimeoutError:
                counter = (counter + 1) % 1_000_000_000
                notify(f"T:{counter}".encode())
    finally:
        await server.stop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
