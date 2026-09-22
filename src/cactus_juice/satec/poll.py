#!/usr/bin/env python3
"""CLI for polling a SATEC EM133 / EM133-XM or EM235 / PM335 PRO meter over Modbus.

See satec/meter.py for the device protocol details."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
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


# --------------------------------------------------------------------------
# Installation sanity checks
#
# These catch wiring and scaling faults that produce believable-looking
# numbers rather than errors -- the kind nothing else notices. The motivating
# case: a CT with its sense leads reversed on L2 made a 3.77 kW export read
# as 1.21 kW, with no register out of range and no exception raised.
#
# The tell was the neutral current. For three balanced currents 120 degrees
# apart the phasor sum is zero, so In should sit near zero. Reverse one
# phase and that phase's contribution rotates 180 degrees, giving
# In = 2 x I_phase exactly -- which is what the meter showed (11.2 A against
# a 5.5 A mean). That ratio is the signature, and it is why the balance test
# has to pass before the neutral test means anything: a genuinely unbalanced
# or single-phase load produces a large In for honest reasons.
# --------------------------------------------------------------------------

# Below this current (A) there is nothing to judge -- no CTs, or no load.
CHECK_MIN_CURRENT = 0.1
# Below this power (kW) the phase sign is noise, not a reading.
CHECK_MIN_POWER = 0.05
# Phase currents within this spread are treated as balanced.
CHECK_BALANCE_TOL = 0.15
# Neutral above this fraction of mean phase current, while balanced, is a
# reversed CT. A correct install sits near 0; one reversed phase gives 2.0.
CHECK_NEUTRAL_TOL = 0.5
# Tolerance on V x I against the meter's own kVA, as a fraction.
CHECK_VA_TOL = 0.10


@dataclass
class Finding:
    level: str  # "error" | "warn"
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():5} {self.code}: {self.message}"


def _check_balance_and_neutral(
    currents: list[float | None], powers: list[float | None], neutral: float | None
) -> tuple[list[Finding], bool]:
    """Returns findings plus whether the three phase currents are balanced --
    the neutral-current test only means anything once balance is established."""
    live = [c for c in currents if c is not None and c > CHECK_MIN_CURRENT]
    if len(live) != 3:
        return [], False

    mean_i = sum(live) / 3
    spread = (max(live) - min(live)) / mean_i if mean_i else 0.0
    balanced = spread <= CHECK_BALANCE_TOL
    if not balanced or neutral is None:
        return [], balanced

    ratio = neutral / mean_i
    if ratio <= CHECK_NEUTRAL_TOL:
        return [], balanced

    # Name the odd phase out if the powers disagree on sign.
    culprit = _odd_phase_out(powers)
    where = f" -- check L{culprit}" if culprit else ""
    finding = Finding(
        "error",
        "neutral-current",
        f"In = {neutral:.2f} A against a balanced "
        f"{mean_i:.2f} A mean (ratio {ratio:.2f}, expected "
        f"near 0). A reversed CT gives exactly 2.0{where}",
    )
    return [finding], balanced


def _check_phase_sign(powers: list[float | None], balanced: bool) -> list[Finding]:
    culprit = _odd_phase_out(powers)
    if not culprit:
        return []
    level = "error" if balanced else "warn"
    signs = ", ".join(f"L{n}={powers[n - 1]:+.3f}" for n in (1, 2, 3))
    return [
        Finding(
            level,
            "phase-sign",
            f"L{culprit} has the opposite power sign to the other two "
            f"({signs} kW) -- reversed CT, or a genuinely mixed "
            f"import/export installation",
        )
    ]


def _check_va_mismatch(
    volts: list[float | None], currents: list[float | None], vas: list[float | None]
) -> list[Finding]:
    out = []
    for n in (1, 2, 3):
        v, i, va = volts[n - 1], currents[n - 1], vas[n - 1]
        if v is None or i is None or va is None or i <= CHECK_MIN_CURRENT or va <= 0:
            continue
        computed = v * i / 1000.0
        err = abs(computed - va) / va
        if err > CHECK_VA_TOL:
            out.append(
                Finding(
                    "error",
                    "va-mismatch",
                    f"L{n}: {v:.1f} V x {i:.2f} A = {computed:.3f} kVA but the "
                    f"meter reports {va:.3f} kVA ({err * 100:.1f}% out) -- "
                    f"voltage, current or power scaling is wrong",
                )
            )
    return out


def _check_kw_over_kva(powers: list[float | None], vas: list[float | None]) -> list[Finding]:
    out = []
    for n in (1, 2, 3):
        kw, va = powers[n - 1], vas[n - 1]
        if kw is None or va is None or va <= 0:
            continue
        if abs(kw) > va * 1.02:
            out.append(
                Finding(
                    "error", "kw-over-kva", f"L{n}: |kW| {abs(kw):.3f} exceeds kVA {va:.3f} -- power scaling is wrong"
                )
            )
    return out


def _check_pf_range(values: dict) -> list[Finding]:
    out = []
    for name in ("total_pf", "pf_l1", "pf_l2", "pf_l3"):
        pf = values.get(name)
        if pf is not None and abs(pf) > 1.001:
            out.append(Finding("error", "pf-range", f"{name} = {pf:.3f}, outside -1..1 -- scaling is wrong"))
    return out


def _check_frequency(values: dict) -> list[Finding]:
    freq = values.get("frequency")
    if freq is not None and freq > 0 and not 45.0 <= freq <= 65.0:
        return [
            Finding("error", "frequency", f"frequency = {freq:.2f} Hz, outside 45-65 -- decoding or scaling is wrong")
        ]
    return []


def _check_voltage(volts: list[float | None]) -> list[Finding]:
    out = []
    live_v = [v for v in volts if v is not None and v > 1.0]
    if not live_v:
        return out

    if len(live_v) < 3:
        out.append(
            Finding(
                "warn",
                "missing-phase",
                f"only {len(live_v)} of 3 phase voltages present "
                f"-- expected on a bench with one phase connected, a "
                f"fault anywhere else",
            )
        )
    for n, v in enumerate(volts, 1):
        if v is not None and v > 1.0 and not 50.0 <= v <= 500.0:
            out.append(
                Finding(
                    "error",
                    "voltage-range",
                    f"v{n} = {v:.1f} V, outside 50-500 -- check the high/low resolution scale for this model",
                )
            )
    return out


def sanity_check(values: dict) -> list[Finding]:
    """Look for wiring and scaling faults in one reading.

    Takes the merged measurement dict from Reading.values (per-phase fields
    must be present -- run the sample with phases=True). Returns a list of
    findings, empty when everything checks out. Checks whose inputs are
    missing or below threshold are skipped rather than guessed at.
    """

    def get(name: str) -> float | None:
        v = values.get(name)
        return None if v is None else float(v)

    currents = [get(f"i{n}") for n in (1, 2, 3)]
    powers = [get(f"kw_l{n}") for n in (1, 2, 3)]
    volts = [get(f"v{n}") for n in (1, 2, 3)]
    vas = [get(f"kva_l{n}") for n in (1, 2, 3)]
    neutral = get("i_neutral")

    neutral_findings, balanced = _check_balance_and_neutral(currents, powers, neutral)

    out: list[Finding] = []
    out.extend(neutral_findings)
    out.extend(_check_phase_sign(powers, balanced))
    out.extend(_check_va_mismatch(volts, currents, vas))
    out.extend(_check_kw_over_kva(powers, vas))
    out.extend(_check_pf_range(values))
    out.extend(_check_frequency(values))
    out.extend(_check_voltage(volts))
    return out


def _odd_phase_out(powers: list) -> int | None:
    """Return the 1-based phase whose power sign differs from the other two,
    or None when they agree or the readings are too small to judge."""
    usable = [(n, p) for n, p in enumerate(powers, 1) if p is not None and abs(p) > CHECK_MIN_POWER]
    if len(usable) != 3:
        return None
    positives = [n for n, p in usable if p > 0]
    if len(positives) == 1:
        return positives[0]
    if len(positives) == 2:
        return next(n for n, p in usable if p < 0)
    return None


def _report_checks(reading, count: int | None) -> bool:  # noqa: ANN001
    """Runs sanity_check on a reading, prints findings to stderr, and returns
    whether any of them was an error (the caller should then fail the run)."""
    findings = sanity_check(reading.values)
    for f in findings:
        print(f, file=sys.stderr, flush=True)
    if any(f.level == "error" for f in findings):
        return True
    if not findings and count == 1:
        print("checks passed", file=sys.stderr, flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Poll a SATEC meter over Modbus",
        epilog="exit codes: 0 ok, 2 Modbus/connection failure, 3 a --check sanity check failed",
    )
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
        "--check",
        action="store_true",
        help="run installation sanity checks on each reading (implies --phases); exits 3 if any check fails",
    )
    ap.add_argument(
        "--float", dest="float_mode", action="store_true", help="meter is already configured for float32 (EM133)"
    )
    ap.add_argument("--set-float", type=int, metavar="PASSWORD", help="switch an EM133 to float32 registers first")
    args = ap.parse_args()

    if args.once:
        args.count = 1
        args.quiet = True
    if args.check:
        # Every check needs the per-phase block.
        args.phases = True

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
        failed = False
        while args.count is None or n < args.count:
            reading = meter.sample(phases=args.phases, energy=args.energy)
            writer.write(reading)
            if args.check and _report_checks(reading, args.count):
                failed = True
            n += 1
            if args.count is None or n < args.count:
                time.sleep(args.interval)
        writer.close()
        if failed:
            return 3
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
