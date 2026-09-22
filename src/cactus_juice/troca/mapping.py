from datetime import UTC, datetime

from cactus_juice.model import OCPPReading
from cactus_juice.troca.models import MeteringReading


def reading_to_db(mr: MeteringReading) -> OCPPReading:
    """Maps a reading from troca into an equivalent DB model"""

    reading_start = datetime.fromisoformat(mr.timestamp)
    if reading_start.tzinfo is None:
        reading_start = reading_start.replace(tzinfo=UTC)

    import_watts: float = 0
    export_watts: float = 0
    if mr.instantaneous_active_power is not None:
        if mr.instantaneous_active_power >= 0:
            import_watts = mr.instantaneous_active_power
        else:
            export_watts = -mr.instantaneous_active_power

    import_var: float | None = None
    export_var: float | None = None
    if mr.instantaneous_reactive_power is not None:
        if mr.instantaneous_reactive_power >= 0:
            import_var = mr.instantaneous_reactive_power
            export_var = 0.0
        else:
            import_var = 0.0
            export_var = -mr.instantaneous_reactive_power

    return OCPPReading(
        reading_start=reading_start,
        import_active_power_watts=import_watts,
        export_active_power_watts=export_watts,
        import_reactive_power_var=import_var,
        export_reactive_power_var=export_var,
    )
