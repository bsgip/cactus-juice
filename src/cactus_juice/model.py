from datetime import datetime

from sqlalchemy import (
    INTEGER,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Meter(Base):
    """Represents some form of third party power meter."""

    __tablename__ = "meter"

    meter_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)  # Short human readable descriptor

    # TODO: Meter connection details

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    meter_readings: Mapped[list["MeterReading"]] = relationship(
        lazy="raise", back_populates="meter", cascade="all, delete-orphan"
    )


class MeterReading(Base):
    """Represents an instantaneous reading from a third party meter that may or may not be associated with
    an actual test."""

    __tablename__ = "meter_reading"
    __table_args__ = (
        Index(
            "meter_id_reading_start_idx",
            "meter_id",
            "reading_start",
        ),
    )

    meter_reading_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meter.id"))  # Which meter is this reading for

    reading_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # When was the reading valid for?
    active_power_watts: Mapped[int] = mapped_column(INTEGER)  # The observed power (in whole active power watts)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    meter: Mapped["Meter"] = relationship(lazy="raise", back_populates="meter_readings")


class CSIPAusDefault(Base):
    """Represents the combination of CSIP-AUS DefaultDERControls that were active for a period of time.
    There may have been multiple contributing DERControls that generated this composite default."""

    __tablename__ = "csipaus_default"
    __table_args__ = (Index("ix_csipaus_default_active_range", "active_range", postgresql_using="gist"),)

    csipaus_control_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)

    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # When was this set of defaults active from?
    active_to: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )  # When was this set of defaults removed - set to max date if it's currently active

    # Generated/computed column - Postgres maintains this automatically from active_from/active_to.
    # No Mapped[...] annotation needed - it's derived, not something you set directly.
    active_range = mapped_column(
        TSTZRANGE,
        Computed("tstzrange(active_from, active_to, '[)')", persisted=True),
        nullable=False,
    )

    # These are the various default options we want to track
    set_grad_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connect: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    energize: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    import_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    export_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_target_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CSIPAusControl(Base):
    """Represents a CSIP-AUS DERControl from a DERProgram"""

    __tablename__ = "csipaus_control"

    csipaus_control_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)

    primacy: Mapped[int] = mapped_column(INTEGER)  # Primacy of parent DERProgram
    mrid: Mapped[str] = mapped_column(String, unique=True)

    duration_seconds: Mapped[int] = mapped_column(INTEGER)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        Computed(
            "started_at + (duration_seconds * interval '1 second')",
            persisted=True,
        ),
        index=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # These are the actual control values that may/may not be set
    ramp_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connect: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    energize: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    import_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    export_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_limit_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_target_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)

    responses: Mapped[list["CSIPAusControlResponse"]] = relationship(
        lazy="raise", back_populates="control", cascade="all, delete-orphan"
    )


class CSIPAusControlResponse(Base):
    """Log of what CSIPAusControl responses are required to be sent"""

    __tablename__ = "csipaus_control_response"

    csipaus_control_response_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)
    csipaus_control_id: Mapped[int] = mapped_column(ForeignKey("csipaus_control.id"), index=True)

    response_status: Mapped[int] = mapped_column(INTEGER)
    end_device_mrid: Mapped[str] = mapped_column(String)
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )  # Don't send this response before this time - allows "enqueing" otherwise just set it to now
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    control: Mapped["CSIPAusControl"] = relationship(lazy="raise", back_populates="responses")

    __table_args__ = (
        Index(
            "idx_unsent_responses",
            "not_before",
            postgresql_where=text("sent_at IS NULL"),
        ),
    )
