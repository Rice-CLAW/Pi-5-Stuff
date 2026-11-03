#!/usr/bin/env python3
"""Run one instance only. Offline BLE calibration prototype; no motor control."""
import argparse
import asyncio
import logging
from pathlib import Path
from core import Calibration, Dataset

SERVICE = '802b1000-6a92-4a6f-9b2b-769c88951a01'
COMMAND = '802b1001-6a92-4a6f-9b2b-769c88951a01'
STATUS = '802b1002-6a92-4a6f-9b2b-769c88951a01'
DATA = '802b1003-6a92-4a6f-9b2b-769c88951a01'

def default_data():
    repo = Path(__file__).resolve().parents[3] / 'ml_classification/EMG_data/gestures_csv'
    old = Path.home() / 'ml_classification/EMG_data/gestures_csv'
    return repo if (repo / 'manifest.json').is_file() else old

async def run(args, dataset):
    from bless import BlessServer, GATTCharacteristicProperties as P, GATTAttributePermissions as A
    from dbus_next.service import ServiceInterface, method
    from dbus_next import DBusError

    class Agent(ServiceInterface):
        def __init__(self):
            super().__init__('org.bluez.Agent1')
        @method()
        def Release(self): pass
        @method()
        def Cancel(self): logging.info('Pairing canceled')
        @method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'):
            logging.info('Automatically accepting pairing confirmation for %s', device)
        @method()
        def RequestAuthorization(self, device: 'o'): pass
        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'): pass
        @method()
        def DisplayPinCode(self, device: 'o', pincode: 's'): pass
        @method()
        def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'): pass
        @method()
        def RequestPinCode(self, device: 'o') -> 's':
            raise DBusError('org.bluez.Error.Rejected', 'Legacy PIN entry is not supported')
        @method()
        def RequestPasskey(self, device: 'o') -> 'u':
            raise DBusError('org.bluez.Error.Rejected', 'Passkey entry is not supported')

    state = Calibration(dataset, args.capture_seconds)
    server = BlessServer(name='CLAW-Pi', loop=asyncio.get_running_loop())
    manager = None
    agent_path = '/org/claw/calibration/agent'
    started = False
    try:
        await server.setup_task
        if not args.no_auto_pair:
            intro = await server.bus.introspect('org.bluez', '/org/bluez')
            manager = server.bus.get_proxy_object('org.bluez', '/org/bluez', intro).get_interface('org.bluez.AgentManager1')
            server.bus.export(agent_path, Agent())
            await manager.call_register_agent(agent_path, 'NoInputNoOutput')
            await manager.call_request_default_agent(agent_path)
            logging.info('Pi-side automatic pairing enabled; phone may still ask permission.')
        await server.add_new_service(SERVICE)
        await server.add_new_characteristic(SERVICE, COMMAND, P.write, bytearray(), A.writeable)
        await server.add_new_characteristic(SERVICE, STATUS, P.read, bytearray(state.snapshot()), A.readable)
        await server.add_new_characteristic(SERVICE, DATA, P.read, bytearray(20), A.readable)
        latest_data = bytes(20)
        def read(characteristic, **kwargs):
            if characteristic.uuid.lower() == STATUS:
                return bytearray(state.snapshot())
            if characteristic.uuid.lower() == DATA:
                return bytearray(latest_data)
            return characteristic.value
        def write(characteristic, value, **kwargs):
            if characteristic.uuid.lower() == COMMAND:
                state.command(bytes(value))
        server.read_request_func, server.write_request_func = read, write
        await server.start()
        started = True
        logging.info('CLAW-Pi calibration server ready. Open claw-calibration.html.')
        while True:
            latest_data = state.telemetry()
            await asyncio.sleep(.25)
    finally:
        if state.task and not state.task.done():
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
        if started:
            await server.stop()
        if manager:
            try: await manager.call_unregister_agent(agent_path)
            except Exception: logging.debug('Agent cleanup', exc_info=True)
        server.bus.disconnect()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=default_data())
    p.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'models')
    p.add_argument('--seed', type=int, help='Optional reproducible participant/recording selection')
    p.add_argument('--capture-seconds', type=float, default=3)
    p.add_argument('--no-auto-pair', action='store_true')
    p.add_argument('--check-data', action='store_true', help='Check one selected session and all six gesture windows without BLE')
    p.add_argument('--debug', action='store_true')
    args = p.parse_args()
    if not 0 < args.capture_seconds <= 60: p.error('capture-seconds must be in (0,60]')
    if args.seed is not None and not 0 <= args.seed < 2**32: p.error('seed must be 0..4294967295')
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    dataset = Dataset(args.data_dir, args.output_dir, args.seed)
    if args.check_data:
        dataset.select()
        for g in range(1,7): dataset.collect(g)
        from emg_common import windows
        for r in dataset.test: windows(r, 'gestures', 250, 125, 'hudgins', 1e-5)
        print('PASS: six gestures plus separate test recordings load correctly.')
        return
    try: asyncio.run(run(args, dataset))
    except KeyboardInterrupt: pass

if __name__ == '__main__': main()
