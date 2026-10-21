import struct
from dataclasses import dataclass, field

import pytest
from pymodbus.exceptions import ModbusIOException

from cactus_juice.satec.meter import (
    ACUDC_ENERGY,
    ACUDC_MODEL,
    ACUDC_REALTIME,
    ACUDC_REG_SERIAL,
    ACUDC_VLOAD,
    PROFILES,
    AcuDCMeter,
    SatecMeter,
    build_meter,
)


@dataclass
class FakeResponse:
    registers: list[int]
    error: bool = False

    def isError(self) -> bool:  # noqa: N802
        return self.error


@dataclass
class FakeModbusClient:
    """Duck-types a pymodbus sync client - serves holding registers from a {address: register} map. Any read that
    touches an address missing from the map returns an error response (like a Modbus exception 2)."""

    registers: dict[int, int] = field(default_factory=dict)
    raise_on_read: Exception | None = None
    reads: list[tuple[int, int]] = field(default_factory=list)

    def read_holding_registers(self, address: int, count: int, device_id: int) -> FakeResponse:
        self.reads.append((address, count))
        if self.raise_on_read is not None:
            raise self.raise_on_read
        if any(a not in self.registers for a in range(address, address + count)):
            return FakeResponse([], error=True)
        return FakeResponse([self.registers[a] for a in range(address, address + count)])


def float32_regs(value: float) -> list[int]:
    """AcuDC float32 - high word first."""
    return list(struct.unpack(">2H", struct.pack(">f", value)))


def float64_regs(value: float) -> list[int]:
    """AcuDC float64 - high word first."""
    return list(struct.unpack(">4H", struct.pack(">d", value)))


def ascii_regs(text: str, count: int, swapped: bool = False) -> list[int]:
    raw = text.encode("ascii").ljust(count * 2, b"\0")
    return list(struct.unpack(f"{'<' if swapped else '>'}{count}H", raw))


def load_block(registers: dict[int, int], block: tuple, values: dict[str, float], width: int) -> None:
    start, fields = block
    encode = float32_regs if width == 2 else float64_regs
    for i, name in enumerate(fields):
        for j, reg in enumerate(encode(values.get(name, 0.0))):
            registers[start + i * width + j] = reg


def make_acudc_registers(serial: str = "MDA250500001", swapped_serial: bool = False) -> dict[int, int]:
    registers: dict[int, int] = {}
    for i, reg in enumerate(ascii_regs(serial, 16, swapped=swapped_serial)):
        registers[ACUDC_REG_SERIAL + i] = reg
    return registers


@pytest.mark.parametrize("swapped_serial", [False, True])
def test_acudc_identify_reads_serial_either_byte_order(swapped_serial: bool):
    client = FakeModbusClient(make_acudc_registers(swapped_serial=swapped_serial))
    meter = AcuDCMeter(client)  # ty: ignore[invalid-argument-type]

    info = meter.identify()

    # Only the serial is mandatory - every other identification block is missing so falls back
    assert info.serial == "MDA250500001"
    assert info.model == "AcuDC 260 series"
    assert info.firmware == "unknown"


def test_acudc_sample_decodes_high_word_first_and_reports_power_unscaled():
    registers = make_acudc_registers()
    load_block(registers, ACUDC_REALTIME, {"v_source": 119.98, "i_ch1": 10.5, "kw_ch1": 1.25979, "kw_sum": 1.25979}, 2)
    load_block(registers, ACUDC_VLOAD, {"v_load_ch1": 119.5}, 2)
    load_block(registers, ACUDC_ENERGY, {"kwh_import_sum": 123456.789}, 4)
    meter = AcuDCMeter(FakeModbusClient(registers))  # ty: ignore[invalid-argument-type]

    reading = meter.sample(energy=True)

    assert reading.serial == "MDA250500001"
    assert reading.values["v_source"] == pytest.approx(119.98, rel=1e-6)
    assert reading.values["i_ch1"] == pytest.approx(10.5)
    assert reading.values["kw_ch1"] == pytest.approx(1.25979, rel=1e-6)  # kW on the wire, no scaling
    assert reading.values["kw_sum"] == pytest.approx(1.25979, rel=1e-6)
    assert reading.values["v_load_ch1"] == pytest.approx(119.5)
    assert reading.values["kwh_import_sum"] == 123456.789


def test_acudc_sample_tolerates_missing_vload_block():
    registers = make_acudc_registers()
    load_block(registers, ACUDC_REALTIME, {"v_source": 48.0}, 2)
    client = FakeModbusClient(registers)
    meter = AcuDCMeter(client)  # ty: ignore[invalid-argument-type]

    first = meter.sample()
    second = meter.sample()

    assert "v_load_ch1" not in first.values
    assert first.values["v_source"] == pytest.approx(48.0)
    assert "v_load_ch1" not in second.values
    # Once the firmware has refused the V Load block it isn't asked again
    assert [r for r in client.reads if r[0] == ACUDC_VLOAD[0]] == [(ACUDC_VLOAD[0], 4)]


def test_acudc_sample_raises_when_realtime_block_fails():
    meter = AcuDCMeter(FakeModbusClient(make_acudc_registers()))  # ty: ignore[invalid-argument-type]
    with pytest.raises(OSError):
        meter.sample()


def test_modbus_exception_is_raised_as_oserror():
    client = FakeModbusClient(raise_on_read=ModbusIOException("no response"))
    meter = AcuDCMeter(client)  # ty: ignore[invalid-argument-type]
    with pytest.raises(OSError):
        meter.identify()


@pytest.mark.parametrize(
    "model, float_mode, expected_type, expected_float_mode",
    [
        ("em133", True, SatecMeter, True),
        ("em235", True, SatecMeter, False),  # em235 isn't float_capable
        (ACUDC_MODEL, True, AcuDCMeter, None),
    ],
)
def test_build_meter(model: str, float_mode: bool, expected_type: type, expected_float_mode: bool | None):
    meter = build_meter(model, FakeModbusClient(), unit=3, float_mode=float_mode)  # ty: ignore[invalid-argument-type]

    assert isinstance(meter, expected_type)
    assert meter.unit == 3
    if isinstance(meter, SatecMeter):
        assert meter.profile is PROFILES[model]
        assert meter.float_mode is expected_float_mode
