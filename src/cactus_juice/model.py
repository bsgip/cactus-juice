from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BIGINT,
    DECIMAL,
    DOUBLE_PRECISION,
    INTEGER,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OCPPReading(Base):
    """Represents a reading via OCPP - these will be instantaneous samplings"""

    __tablename__ = "ocpp_reading"

    ocpp_reading_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)

    reading_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    frequency_hz: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    import_active_power_watts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    export_active_power_watts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    import_reactive_power_var: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    export_reactive_power_var: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    soc_percent: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    voltage_volts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)


class OCPPMetadata(Base):
    """Represents a snapshot of OCPP device metadata retrieved at a moment in time"""

    __tablename__ = "ocpp_metadata"

    ocpp_metadata_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    max_voltage_volts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    min_voltage_volts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    max_power_watts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    max_charge_rate_watts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    max_discharge_rate_watts: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    set_grad_w: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)


class Meter(Base):
    """Represents some form of third party power meter."""

    __tablename__ = "meter"

    meter_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)  # Short human readable descriptor

    # TODO: Meter connection details

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

    meter_reading_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meter.id"))  # Which meter is this reading for

    reading_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # When was the reading valid for?
    active_power_watts: Mapped[int] = mapped_column(INTEGER)  # The observed power (in whole active power watts)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    meter: Mapped["Meter"] = relationship(lazy="raise", back_populates="meter_readings")


class CSIPAusDefault(Base):
    """Represents the combination of CSIP-AUS DefaultDERControls that were active for a period of time.
    There may have been multiple contributing DERControls that generated this composite default."""

    __tablename__ = "csipaus_default"
    __table_args__ = (
        # A rolling history of defaults must never have two rows active at the same instant - an
        # overlapping active_range is a data integrity violation. The gist index this constraint
        # maintains also serves the fetch_active_default lookup.
        ExcludeConstraint(
            ("active_range", "&&"),
            name="excl_csipaus_default_active_range_overlap",
            using="gist",
        ),
    )

    csipaus_default_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # When was this set of defaults active from?
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )  # When was this set of defaults removed - set to max date if it's currently active

    # Generated/computed column - Postgres maintains this automatically from active_from/active_to.
    # No Mapped[...] annotation needed - it's derived, not something you set directly.
    active_range = mapped_column(
        TSTZRANGE,
        Computed("tstzrange(started_at, finished_at, '[)')", persisted=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # These are the various default options we want to track
    ramp_percent_max_second_hundredths: Mapped[int | None] = mapped_column(Integer, nullable=True)  # setGradW
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

    csipaus_control_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)

    primacy: Mapped[int] = mapped_column(INTEGER)  # Primacy of parent DERProgram
    mrid: Mapped[str] = mapped_column(String, unique=True)

    duration_seconds: Mapped[int] = mapped_column(INTEGER)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        Computed(
            # `timestamptz + interval` and bare `extract(epoch from timestamptz)` are only
            # STABLE (they depend on the session TimeZone), so Postgres rejects them in a
            # generated column. Pinning the zone with `AT TIME ZONE 'UTC'` on both sides
            # makes every step IMMUTABLE while preserving the instant.
            "(started_at AT TIME ZONE 'UTC' + duration_seconds * interval '1 second') AT TIME ZONE 'UTC'",
            persisted=True,
        ),
        index=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String, nullable=True)  # If set - send Responses to this URI location

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

    csipaus_control_response_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)
    csipaus_control_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("csipaus_control.id"), index=True)

    response_status: Mapped[int] = mapped_column(INTEGER)
    end_device_lfdi: Mapped[str] = mapped_column(String)
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
        UniqueConstraint(
            "csipaus_control_id",
            "end_device_lfdi",
            "response_status",
            name="uc_csipaus_control_response_control_device_status",
        ),
    )


class CSIPAusConfig(Base):
    """Represents the current configuration for accessing a CSIP-AUS server - the active config is the entry with the
    most recent created_at"""

    __tablename__ = "csipaus_config"

    csipaus_config_id: Mapped[int] = mapped_column(name="id", primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Client config
    is_aggregator: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE"
    )  # True if this is an aggregator client, False if Device client
    certificate_pem: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )  # PEM encoded X509 client cert for mTLS to server
    key_pem: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )  # PEM encoded X509 client key for mTLS to server
    nmi: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # What NMI should be registered as a connection point ID?
    client_pen: Mapped[int | None] = mapped_column(
        INTEGER, nullable=True
    )  # Private Enterprise Number used to encode mrids

    # Server config
    dcap_uri: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # DeviceCapability URI - This *could* just be the host and the v1.3 endpoint could be found via .well-known
    serca_pem: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )  # PEM encoded X509 server cert for mTLS to check
    verify_hostname: Mapped[bool] = mapped_column(Boolean, server_default="TRUE")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, server_default="TRUE")


class CSIPAusDynamicPrice(Base):
    """Represents a CSIP-AUS dynamic price (basically a flattened TimeTariffInterval + ConsumptionBlock)

    It will NOT represent periodical prices - nor will it map any price beyond the first consumption block"""

    __tablename__ = "csipaus_dynamic_price"

    csipaus_dynamic_price_id: Mapped[int] = mapped_column(BIGINT, name="id", primary_key=True, autoincrement=True)

    primacy: Mapped[int] = mapped_column(INTEGER)  # Primacy of parent TariffProfile
    mrid: Mapped[str] = mapped_column(String, unique=True)

    duration_seconds: Mapped[int] = mapped_column(INTEGER)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        Computed(
            # `timestamptz + interval` and bare `extract(epoch from timestamptz)` are only
            # STABLE (they depend on the session TimeZone), so Postgres rejects them in a
            # generated column. Pinning the zone with `AT TIME ZONE 'UTC'` on both sides
            # makes every step IMMUTABLE while preserving the instant.
            "(started_at AT TIME ZONE 'UTC' + duration_seconds * interval '1 second') AT TIME ZONE 'UTC'",
            persisted=True,
        ),
        index=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String, nullable=True)  # If set - send Responses to this URI location

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    price_kwh: Mapped[Decimal | None] = mapped_column(
        DECIMAL(10, 4), nullable=True
    )  # dollars / kwh - flattened from parent RateComponent

    responses: Mapped[list["CSIPAusDynamicPriceResponse"]] = relationship(
        lazy="raise", back_populates="dynamic_price", cascade="all, delete-orphan"
    )


class CSIPAusDynamicPriceResponse(Base):
    """Log of what CSIPAusDynamicPrice responses are required to be sent"""

    __tablename__ = "csipaus_dynamic_price_response"

    csipaus_dynamic_price_response_id: Mapped[int] = mapped_column(
        BIGINT, name="id", primary_key=True, autoincrement=True
    )
    csipaus_dynamic_price_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("csipaus_dynamic_price.id"), index=True)

    response_status: Mapped[int] = mapped_column(INTEGER)
    end_device_lfdi: Mapped[str] = mapped_column(String)
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )  # Don't send this response before this time - allows "enqueing" otherwise just set it to now
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dynamic_price: Mapped["CSIPAusDynamicPrice"] = relationship(lazy="raise", back_populates="responses")

    __table_args__ = (
        Index(
            "idx_unsent_dynamic_price_responses",
            "not_before",
            postgresql_where=text("sent_at IS NULL"),
        ),
        UniqueConstraint(
            "csipaus_dynamic_price_id",
            "end_device_lfdi",
            "response_status",
            name="uc_csipaus_dynamic_price_response_device_status",
        ),
    )
