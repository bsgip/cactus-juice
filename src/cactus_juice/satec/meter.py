"""SATEC EM133 / EM133-XM / EM235 / PM335 PRO Modbus device model.

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

import struct
import time
from dataclasses import dataclass, field

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

# --------------------------------------------------------------------------
# Integer-mode scale factors, keyed by field name.
#
# In integer mode the meter transmits fractional values pre-multiplied by a
# power of ten, and reports power in W/var/VA rather than kW/kvar/kVA. These
# tables convert back to engineering units. They are NOT applied in float
# mode, where the meter has already done the work.
#
# Scales differ between models -- and, for voltage and current, between the
# low- and high-resolution ordering options of the same model -- so each
# profile carries its own table. COMMON_SCALE holds the entries that are the
# same everywhere.
# --------------------------------------------------------------------------

COMMON_SCALE = {
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
}

# Power fields are transmitted in W / var / VA. Scale to k-units so the field
# names match the values.
POWER_FIELDS = (
    "total_kw",
    "total_kvar",
    "total_kva",
    "kw_import",
    "kw_export",
    "kvar_import",
    "kvar_export",
    "kw_l1",
    "kw_l2",
    "kw_l3",
    "kvar_l1",
    "kvar_l2",
    "kvar_l3",
    "kva_l1",
    "kva_l2",
    "kva_l3",
)

# EM133-XM, serial 40003692, firmware 12.10.
#
# Voltage confirmed against mains: v1 read 2428 for 242.8 V, so 0.1 V units
# (high-resolution ordering option -- a low-resolution unit reports whole
# volts and these entries must be dropped).
#
# CURRENT AND POWER ARE UNCONFIRMED ON THIS MODEL. No CTs were connected
# during bench testing, so every current and power register read zero. The
# values below are carried over from the EM235, where they are confirmed;
# verify against the front panel under load before trusting kW on an EM133.
EM133_SCALE = {
    **COMMON_SCALE,
    "v1": 0.1,
    "v2": 0.1,
    "v3": 0.1,
    "v_avg_ln": 0.1,
    "v_avg_ll": 0.1,
    "i1": 0.01,
    "i2": 0.01,
    "i3": 0.01,
    "i_avg": 0.01,
    "i_neutral": 0.01,
    **{f: 0.001 for f in POWER_FIELDS},
}

# EM235 / PM335 PRO, confirmed against a live 3-phase install at 230 V.
#
# Cross-check that fixes both current and power at once:
#   v1 226.7 V x i1 5.44 A = 1233 VA against kva_l1 = 1231  (0.2%)
# and the same holds on L2 and L3. Frequency agrees across three registers
# at three different scales (0.01, 0.001 and 0.0001 Hz), and internal_temp
# agrees with internal_temp_2, which confirms the decode independently.
#
# Caveat: on some SATEC models the power unit depends on the PT ratio (W at
# a ratio of 1, kW otherwise). The confirming install is direct-connected,
# so PT ratio is 1. Re-check on any site using VTs.
EM235_SCALE = {
    **COMMON_SCALE,
    "v1": 0.1,
    "v2": 0.1,
    "v3": 0.1,
    "v_avg_ln": 0.1,
    "v_avg_ll": 0.1,
    "i1": 0.01,
    "i2": 0.01,
    "i3": 0.01,
    "i4": 0.01,
    "i_avg": 0.01,
    "i_neutral": 0.01,
    "internal_temp": 0.1,
    "internal_temp_2": 0.1,
    "vbatt": 0.001,
    "frequency_100u": 0.0001,
    **{f: 0.001 for f in POWER_FIELDS},
    # i_leakage units are unconfirmed -- left unscaled (raw) for now.
}


@dataclass
class Profile:
    name: str
    totals: tuple
    phase: tuple
    aux: tuple
    energy: tuple
    float_capable: bool
    scale: dict
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
        scale=EM133_SCALE,
        reg_32bit_type=246,
        reg_authorization=2575,
    ),
    "em235": Profile(
        name="EM235 / PM335 PRO",
        totals=(14336, TOTALS_FIELDS),
        phase=(13952, PHASE_FIELDS),
        # 15 values, confirmed by length probe. The PM335 PRO guide documents
        # a 16th (v3xi4_kw) that this firmware does not serve -- asking for it
        # makes the whole read fail with exception 2.
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
        scale=EM235_SCALE,
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
        scale = self.profile.scale
        out = {}
        for name, value in zip(fields, values, strict=True):
            if not self.float_mode and name in scale:
                value *= scale[name]
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


def build_client(
    *,
    host: str | None,
    port: str | None,
    port_tcp: int,
    baud: int,
    parity: str,
    timeout: float,
) -> ModbusBaseSyncClient:
    """Builds the pymodbus client for a meter - Modbus/TCP if host is set, otherwise serial RTU via port."""
    if host:
        return ModbusTcpClient(host, port=port_tcp, timeout=timeout)
    if port is None:
        raise ValueError("Either host (Modbus/TCP) or port (serial device) must be specified")
    return ModbusSerialClient(port=port, baudrate=baud, bytesize=8, parity=parity, stopbits=1, timeout=timeout)
