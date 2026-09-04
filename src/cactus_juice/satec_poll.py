#!/usr/bin/env python3
"""
Poll a SATEC EM133 / EM133-XM or EM235 / PM335 PRO meter over Modbus.

Both families share the same Modbus architecture -- native register numbering
0-65535, 32-bit values in two adjacent registers LOW WORD FIRST with the low
word on an even address, and the same base addresses for the real-time
measurement blocks. Develop against the EM235 and the EM133 swap is a profile
change, not a rewrite.

Two addressing quirks, both confirmed against hardware:

  * The measurement tables in section 2 of the reference guide are documented
    with a Modicon "4XXXX" overlay (native + 40001). The device setup tables
    in section 3.7 are NOT -- 46080 and 46112 are native addresses that only
    look like Modicon numbers. In hex they are 0xB400 and 0xB420, matching the
    round block bases used everywhere else.

  * Numeric fields are big-endian within each register, but CHAR16 strings are
    byte-swapped. Read the model name with '<8H', everything else with '>'.

Requires: pip install 'pymodbus[serial]==3.15.0'
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Any, TextIO

from pymodbus.client import ModbusBaseSyncClient, ModbusSerialClient, ModbusTcpClient

# --------------------------------------------------------------------------
# Device identification / factory settings (section 3.7, native addresses)
# --------------------------------------------------------------------------

REG_IDENT = 46080  # 0xB400, 32 registers
REG_FACTORY = 46112  # 0xB420, 67 registers

# --------------------------------------------------------------------------
# Fields shared by both families (identical order and offsets)
# --------------------------------------------------------------------------

TOTALS_FIELDS = [
    "total_kw",
    "total_kvar",
    "total_kva",
    "total_pf",
    "total_pf_lag",
    "total_pf_lead",
    "kw_import",
    "kw_export",
    "kvar_import",
    "kvar_export",
    "v_avg_ln",
    "v_avg_ll",
    "i_avg",
]

PHASE_FIELDS = [
    "v1",
    "v2",
    "v3",
    "i1",
    "i2",
    "i3",
    "kw_l1",
    "kw_l2",
    "kw_l3",
    "kvar_l1",
    "kvar_l2",
    "kvar_l3",
    "kva_l1",
    "kva_l2",
    "kva_l3",
    "pf_l1",
    "pf_l2",
    "pf_l3",
]

# Integer-mode fractional pre-multipliers, keyed by field name.
#
# The voltage scale below is confirmed for a high-resolution unit (0.1 V):
# v1 read 2428 against 242.8 V mains. A low-resolution unit reports 1 V and
# these entries must be removed. CURRENT AND POWER SCALES ARE NOT YET
# CONFIRMED -- no CTs were connected during bench testing. Verify against the
# front panel before trusting kW.
SCALE = {
    "total_pf": 0.001,
    "total_pf_lag": 0.001,
    "total_pf_lead": 0.001,
    "pf_l1": 0.001,
    "pf_l2": 0.001,
    "pf_l3": 0.001,
    "frequency": 0.01,
    "frequency_mhz": 0.001,
    "v_unbalance": 0.1,
    "i_unbalance": 0.1,
    "internal_temp": 0.1,
    "vbatt": 0.001,
    "v1": 0.1,
    "v2": 0.1,
    "v3": 0.1,
    "v_avg_ln": 0.1,
    "v_avg_ll": 0.1,
}


@dataclass
class Profile:
    name: str
    totals: tuple
    phase: tuple
    aux: tuple
    energy: tuple
    float_capable: bool
    reg_32bit_type: int | None = None
    reg_authorization: int | None = None


PROFILES = {
    "em133": Profile(
        name="EM133 / EM133-XM",
        totals=(14336, TOTALS_FIELDS),
        phase=(13952, PHASE_FIELDS),
        # Index 0 is unused on a plain EM133. On an XM (4 current inputs) it
        # is most likely I4 -- unconfirmed, reads zero without CTs.
        aux=(
            14464,
            [
                "_aux0",
                "i_neutral",
                "frequency",
                "v_unbalance",
                "i_unbalance",
                "_r5",
                "_r6",
                "_r7",
                "_r8",
                "_r9",
                "frequency_mhz",
            ],
        ),
        energy=(
            14720,
            [
                "kwh_import",
                "kwh_export",
                "_r2",
                "_r3",
                "kvarh_import",
                "kvarh_export",
                "_r6",
                "_r7",
                "kvah_total",
                "_r9",
                "_r10",
                "kvah_import",
                "kvah_export",
                "kvarh_q1",
                "kvarh_q2",
                "kvarh_q3",
                "kvarh_q4",
            ],
        ),
        float_capable=True,
        reg_32bit_type=246,
        reg_authorization=2575,
    ),
    "em235": Profile(
        name="EM235 / PM335 PRO",
        totals=(14336, TOTALS_FIELDS),
        phase=(13952, PHASE_FIELDS),
        aux=(
            14464,
            [
                "i4",
                "i_neutral",
                "frequency",
                "v_unbalance",
                "i_unbalance",
                "_r5",
                "_r6",
                "_r7",
                "_r8",
                "internal_temp",
                "frequency_mhz",
                "vbatt",
                "internal_temp_2",
                "frequency_100u",
                "i_leakage",
                "v3xi4_kw",
            ],
        ),
        energy=(
            14720,
            [
                "kwh_import",
                "kwh_export",
                "kwh_net",
                "kwh_total",
                "kvarh_import",
                "kvarh_export",
                "kvarh_net",
                "kvarh_total",
                "kvah_total",
                "vh_total",
                "ah_total",
                "kvah_import",
                "kvah_export",
                "_r13",
                "_r14",
                "_r15",
                "_r16",
                "_r17",
                "kvarh_q1",
                "kvarh_q2",
                "kvarh_q3",
                "kvarh_q4",
            ],
        ),
        float_capable=False,
    ),
}


@dataclass
class DeviceInfo:
    serial: int
    model_id: int
    model_name: str
    options: int
    firmware: str
    bootloader: str

    def __str__(self) -> str:
        return (
            f"{self.model_name} serial {self.serial} "
            f"(model id {self.model_id}, fw {self.firmware}, "
            f"boot {self.bootloader})"
        )


@dataclass
class Reading:
    timestamp: float
    serial: int
    values: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {k: v for k, v in self.values.items() if not k.startswith("_")}

    def __str__(self) -> str:
        return "  ".join(f"{k}={v:.3f}" for k, v in self.public().items())


class SatecMeter:
    def __init__(self, profile: Profile, client: ModbusBaseSyncClient, unit: int = 1, float_mode: bool = False) -> None:
        self.profile = profile
        self.client = client
        self.unit = unit
        self.float_mode = float_mode and profile.float_capable
        self._info: DeviceInfo | None = None

    def connect(self) -> bool:
        return self.client.connect()

    def close(self) -> None:
        self.client.close()

    # -- low level ---------------------------------------------------------

    def _read(self, address: int, count: int) -> list[int]:
        """Read holding registers. Handles the pymodbus 3.x rename of
        slave= to device_id=."""
        try:
            rr = self.client.read_holding_registers(address, count=count, device_id=self.unit)
        except TypeError:
            rr = self.client.read_holding_registers(address, count=count, slave=self.unit)  # ty:ignore[unknown-argument]
        if rr.isError():
            raise OSError(f"Modbus read failed at {address}: {rr}")
        return rr.registers

    def _write(self, address: int, value: int) -> None:
        try:
            rq = self.client.write_register(address, value, device_id=self.unit)
        except TypeError:
            rq = self.client.write_register(address, value, slave=self.unit)  # ty:ignore[unknown-argument]
        if rq.isError():
            raise OSError(f"Modbus write failed at {address}: {rq}")

    @staticmethod
    def _u32(regs: list[int], i: int) -> int:
        """Decode a 32-bit unsigned at offset i, low word first."""
        return struct.unpack(">I", struct.pack(">HH", regs[i + 1], regs[i]))[0]

    def _decode32(self, regs: list[int]) -> list[float]:
        out = []
        for lo, hi in zip(regs[0::2], regs[1::2], strict=True):
            raw = struct.pack(">HH", hi, lo)
            if self.float_mode:
                out.append(struct.unpack(">f", raw)[0])
            else:
                out.append(float(struct.unpack(">i", raw)[0]))
        return out

    def _read_block(self, block: tuple) -> dict:
        start, fields = block
        values = self._decode32(self._read(start, len(fields) * 2))
        out = {}
        for name, value in zip(fields, values, strict=True):
            if not self.float_mode and name in SCALE:
                value *= SCALE[name]
            out[name] = value
        return out

    # -- identification ----------------------------------------------------

    def identify(self, refresh: bool = False) -> DeviceInfo:
        """Read the device identification block. Cached after the first call
        -- none of it changes at runtime."""
        if self._info is not None and not refresh:
            return self._info
        r = self._read(REG_IDENT, 32)
        # CHAR16 strings are byte-swapped relative to the numeric fields.
        name = struct.pack("<8H", *r[4:12]).split(b"\0")[0].decode("ascii", errors="replace")
        self._info = DeviceInfo(
            serial=self._u32(r, 0),
            model_id=self._u32(r, 2),
            model_name=name,
            options=self._u32(r, 12),
            firmware=f"{r[20] // 100}.{r[20] % 100:02d} build {r[21]}",
            bootloader=f"{r[24] // 100}.{r[24] % 100:02d} build {r[25]}",
        )
        return self._info

    def factory_settings(self) -> dict:
        """V and I input ranges -- the ground truth for scaling."""
        r = self._read(REG_FACTORY, 8)
        return {
            "v_range": r[0],
            "v_overload_pct": r[1],
            "i_range": r[4],
            "i_overload_pct": r[5],
        }

    # -- measurements ------------------------------------------------------

    def totals(self) -> dict:
        return self._read_block(self.profile.totals)

    def phases(self) -> dict:
        return self._read_block(self.profile.phase)

    def auxiliary(self) -> dict:
        return self._read_block(self.profile.aux)

    def energies(self) -> dict:
        return self._read_block(self.profile.energy)

    def sample(self, phases: bool = False, energy: bool = False) -> Reading:
        values = {}
        values.update(self.totals())
        values.update(self.auxiliary())
        if phases:
            values.update(self.phases())
        if energy:
            values.update(self.energies())
        return Reading(timestamp=time.time(), serial=self.identify().serial, values=values)

    # -- configuration -----------------------------------------------------

    def get_32bit_type(self) -> int | None:
        if self.profile.reg_32bit_type is None:
            return None
        return self._read(self.profile.reg_32bit_type, 1)[0]

    def set_float_registers(self, password: int) -> None:
        """EM133 only. Bits 0-1 analog, 2-3 counters, 4-5 energy;
        0 = int32, 1 = float32, so 0b010101 = 21 selects float everywhere."""
        if not self.profile.float_capable:
            raise RuntimeError(f"{self.profile.name} has no float register mode")
        self._write(self.profile.reg_authorization or 0, password)
        self._write(self.profile.reg_32bit_type or 0, 21)
        self._write(self.profile.reg_authorization or 0, 0)
        self.float_mode = True


# --------------------------------------------------------------------------
# Output writers
# --------------------------------------------------------------------------


class TextWriter:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def write(self, reading: Reading) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(reading.timestamp))
        print(f"[{stamp}] {reading.serial}  {reading}", file=self.stream, flush=True)

    def close(self) -> None:
        pass


class CsvWriter:
    """One row per sample. The header is written from the first reading's
    keys, so the column set is fixed for the life of the file."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.writer = None
        self.fields = None

    def write(self, reading: Reading) -> None:
        row = {
            "timestamp": f"{reading.timestamp:.3f}",
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(reading.timestamp)),
            "serial": reading.serial,
        }
        row.update({k: f"{v:.4f}" for k, v in reading.public().items()})
        if self.writer is None:
            self.fields = list(row)
            self.writer = csv.DictWriter(self.stream, fieldnames=self.fields)
            self.writer.writeheader()
        self.writer.writerow({k: row.get(k, "") for k in (self.fields or [])})
        self.stream.flush()

    def close(self) -> None:
        pass


class JsonWriter:
    """Newline-delimited JSON -- one object per line, so the file stays valid
    and tailable even if the process is interrupted."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def write(self, reading: Reading) -> None:
        obj = {
            "timestamp": round(reading.timestamp, 3),
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(reading.timestamp)),
            "serial": reading.serial,
            "measurements": {k: round(v, 4) for k, v in reading.public().items()},
        }
        self.stream.write(json.dumps(obj) + "\n")
        self.stream.flush()

    def close(self) -> None:
        pass


WRITERS = {"text": TextWriter, "csv": CsvWriter, "json": JsonWriter}


def build_client(args: Any) -> ModbusBaseSyncClient:  # noqa: ANN401
    if args.host:
        return ModbusTcpClient(args.host, port=args.port_tcp, timeout=args.timeout)
    return ModbusSerialClient(
        port=args.port, baudrate=args.baud, bytesize=8, parity=args.parity, stopbits=1, timeout=args.timeout
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Poll a SATEC meter over Modbus")
    ap.add_argument("--model", default="em133", choices=sorted(PROFILES))
    ap.add_argument("--port", default="/dev/ttyUSB0", help="serial device")
    ap.add_argument("--host", help="use Modbus/TCP instead of RTU")
    ap.add_argument("--port-tcp", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1, help="Modbus address 1-247")
    ap.add_argument("--baud", type=int, default=19200)
    ap.add_argument("--parity", default="N", choices=["N", "E", "O"])
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--count", type=int, help="stop after N samples")
    ap.add_argument("--once", action="store_true", help="take a single reading and exit (implies --quiet)")
    ap.add_argument("--quiet", "-q", action="store_true", help="suppress the device identification banner")
    ap.add_argument("--phases", action="store_true", help="include per-phase measurements")
    ap.add_argument("--energy", action="store_true", help="include energy counters")
    ap.add_argument(
        "--format", default="text", choices=sorted(WRITERS), help="output format (json is newline-delimited)"
    )
    ap.add_argument("--output", "-o", help="write to a file instead of stdout")
    ap.add_argument("--info", action="store_true", help="print device identification and exit")
    ap.add_argument(
        "--float", dest="float_mode", action="store_true", help="meter is already configured for float32 (EM133)"
    )
    ap.add_argument("--set-float", type=int, metavar="PASSWORD", help="switch an EM133 to float32 registers first")
    args = ap.parse_args()

    if args.once:
        args.count = 1
        args.quiet = True

    meter = SatecMeter(PROFILES[args.model], build_client(args), unit=args.unit, float_mode=args.float_mode)
    if not meter.connect():
        raise SystemExit("could not open connection")

    # The header goes to stderr whenever stdout carries data, so it never
    # contaminates a piped csv/json stream.
    log = sys.stdout if (args.format == "text" and not args.output) else sys.stderr

    stream = None
    try:
        info = meter.identify()
        if not args.quiet or args.info:
            print(info, file=log, flush=True)

        if args.info:
            fs = meter.factory_settings()
            print(
                f"V range {fs['v_range']} V "
                f"(overload {fs['v_overload_pct']}%), "
                f"I range {fs['i_range']} A "
                f"(overload {fs['i_overload_pct']}%)",
                file=log,
                flush=True,
            )
            reg = meter.get_32bit_type()
            print(f"32-bit register type: {reg if reg is not None else 'int32 only'}", file=log, flush=True)
            return 0

        if args.set_float is not None:
            meter.set_float_registers(args.set_float)

        stream = open(args.output, "a", newline="") if args.output else sys.stdout
        writer = WRITERS[args.format](stream)

        n = 0
        while args.count is None or n < args.count:
            writer.write(meter.sample(phases=args.phases, energy=args.energy))
            n += 1
            if args.count is None or n < args.count:
                time.sleep(args.interval)
        writer.close()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        # A one-shot read is likely being called from a script or a
        # monitoring check, so fail with a usable exit code and a single
        # line on stderr rather than a traceback.
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if stream is not None and stream is not sys.stdout:
            stream.close()
        meter.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
