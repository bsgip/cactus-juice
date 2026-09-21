#!/usr/bin/env python3
"""CLI for polling a SATEC EM133 / EM133-XM or EM235 / PM335 PRO meter over Modbus.

See satec/meter.py for the device protocol details."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from typing import TextIO

from cactus_juice.satec.meter import PROFILES, SatecMeter, build_client


class TextWriter:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def write(self, reading) -> None:  # noqa: ANN001
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

    def write(self, reading) -> None:  # noqa: ANN001
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

    def write(self, reading) -> None:  # noqa: ANN001
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

    client = build_client(
        host=args.host, port=args.port, port_tcp=args.port_tcp, baud=args.baud, parity=args.parity, timeout=args.timeout
    )
    meter = SatecMeter(PROFILES[args.model], client, unit=args.unit, float_mode=args.float_mode)
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
