"""Run: python -m unittest discover -s calibration -p 'test_*.py' (from pi)."""
import asyncio
import struct
import tempfile
import unittest
from pathlib import Path
from core import *

class FakeData:
    def __init__(self): self.calls=[]
    def select(self): return 4
    def collect(self,g): self.calls.append(g)
    def fit(self): return .75

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_and_duplicate_commands(self):
        d=FakeData(); c=Calibration(d,.001)
        def command(op,g=0,req=1): return struct.pack('<BBIIB',1,op,c.boot,req,g)
        c.command(command(2,2)); self.assertEqual(c.phase,IDLE); self.assertEqual(c.error,2)
        c.command(command(1,req=2)); await c.task
        self.assertEqual(c.phase,READY)
        for g in range(1,7):
            packet=command(2,g,req=g+2)
            c.command(packet); c.command(packet)
            self.assertEqual(c.phase,COLLECTING)
            await c.task
        self.assertEqual(d.calls,list(range(1,7)))
        self.assertEqual(c.phase,INFERENCE)
        self.assertEqual(c.accuracy,7500)
        self.assertEqual(len(c.snapshot()),20)
        packet=c.telemetry(); self.assertEqual(len(packet),20)
        self.assertTrue(all(0<=x<=1000 for x in struct.unpack('<I8H',packet)[1:]))
        # Stale duplicate must not restart calibration.
        c.command(command(1,req=2)); self.assertEqual(c.phase,INFERENCE)

    async def test_failure_does_not_claim_success(self):
        class Broken(FakeData):
            def select(self): raise ValueError('missing manifest')
        c=Calibration(Broken()); c.command(struct.pack('<BBIIB',1,1,c.boot,1,0)); await c.task
        self.assertEqual(c.phase,ERROR); self.assertEqual(c.completed,0)
        self.assertEqual(struct.unpack('<I8H',c.telemetry())[0],0)

    async def test_stale_boot_and_out_of_order(self):
        c=Calibration(FakeData(),.001)
        c.command(struct.pack('<BBIIB',1,1,c.boot^1,1,0)); self.assertEqual(c.phase,IDLE)
        c.command(struct.pack('<BBIIB',1,1,c.boot,2,0)); await c.task
        c.command(struct.pack('<BBIIB',1,2,c.boot,3,3)); self.assertEqual(c.phase,READY)
        self.assertEqual(c.error,2); self.assertEqual(c.completed,0)

if __name__=='__main__': unittest.main()
