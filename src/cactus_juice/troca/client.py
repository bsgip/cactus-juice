from __future__ import annotations

import json
import logging
import types
from datetime import datetime
from typing import Any, Self

import aiohttp

from .mapping import to_troca_timestamp
from .models import (
    ActivePowerSchedule,
    CommandParameter,
    Connector,
    Evse,
    GetVariableData,
    GetVariableResult,
    LocationId,
    MeteringReading,
    OcppChargingProfile,
    OcppTarget,
    Pool,
    SessionCommand,
    SessionCommandLocation,
    SessionCommandStatusEntry,
    SessionCommandType,
    SessionData,
    SessionOperationalData,
    SessionStatusEntry,
    SetVariableData,
    SetVariableResult,
    Station,
    StructurePair,
    set_charging_profile_parameters,
)

logger = logging.getLogger(__name__)

# Troca pages every collection GET - a request without a limit returns at most this many items
TROCA_PAGE_SIZE = 500


class TrocaApiError(Exception):
    """Raised when the Troca API returns a non-2xx response."""

    def __init__(self, status: int, method: str, path: str, body: str) -> None:
        super().__init__(f"{method} {path} -> {status}: {body}")
        self.status = status
        self.method = method
        self.path = path
        self.body = body


class TrocaClient:
    """Thin async wrapper around the Troca HTTP API."""

    _session: aiohttp.ClientSession | None
    _auth: aiohttp.BasicAuth
    _base_url: str

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(username, password)
        self._session = None

    async def __aenter__(self) -> Self:
        self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(auth=self._auth)
        return self._session

    @staticmethod
    def _params(
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Builds Troca's query parameters. filters are ANDed together - each key is either a field name (equality)
        or field[op] where op is one of gt, gte, lt, lte, ne or in (comma separated values). A datetime value is
        encoded in Troca's own timestamp format. orderby is a field name, prefixed with "-" for descending.

        Unknown fields aren't rejected - they just silently match nothing (eg "skip", which isn't Troca's offset)."""
        params: dict[str, Any] = {}
        for key, value in (filters or {}).items():
            if value is not None:
                params[key] = to_troca_timestamp(value) if isinstance(value, datetime) else value
        if orderby is not None:
            params["orderby"] = orderby
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return params

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: Any = None,  # noqa: ANN401
    ) -> str:
        """Sends a request (payload is JSON encoded if not None), returning the raw response body text. Every request
        is logged at INFO (with the bodies logged at DEBUG). Raises TrocaApiError on a non 2xx response."""
        session = self._ensure_session()
        request_body = json.dumps(payload).encode() if payload is not None else b""
        headers = {"Content-Type": "application/json"} if payload is not None else None
        async with session.request(
            method, f"{self._base_url}{path}", params=params, data=request_body or None, headers=headers
        ) as resp:
            response_bytes = await resp.read()
            body = await resp.text()
            logger.info(
                f"Troca {method} {path} params={params or {}} -> {resp.status} "
                f"(request {len(request_body)} bytes, response {len(response_bytes)} bytes)"
            )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Troca {method} {path} request body: {request_body.decode()}")
                logger.debug(f"Troca {method} {path} response body: {body}")
            if resp.status >= 400:
                raise TrocaApiError(resp.status, method, path, body)
            return body

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict | list | None:
        body = await self._request("GET", path, params=params)
        return json.loads(body) if body else None

    async def _get_list(
        self,
        path: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """GETs a Troca collection. If limit is set, that single page is returned - otherwise every page (from
        offset onwards) is fetched until Troca returns a short page."""
        if limit is not None:
            return await self._get(path, self._params(limit, offset, orderby, filters)) or []  # ty: ignore[invalid-return-type]

        items: list[dict[str, Any]] = []
        page_offset = offset or 0
        while True:
            page = await self._get(path, self._params(TROCA_PAGE_SIZE, page_offset, orderby, filters)) or []
            items.extend(page)
            if len(page) < TROCA_PAGE_SIZE:
                return items
            page_offset += TROCA_PAGE_SIZE

    async def _post(self, path: str, payload: list[dict[str, Any]] | dict[str, Any]) -> Any:  # noqa: ANN401
        """POSTs payload as JSON - returns the parsed JSON response body (or None if it's empty / not JSON, eg
        Troca's plain text "Success")."""
        body = await self._request("POST", path, payload=payload)
        try:
            return json.loads(body) if body else None
        except ValueError:
            return None

    # -- Config -----------------------------------------------

    async def get_connectors(self, *, limit: int | None = None, offset: int | None = None) -> list[Connector]:
        data = await self._get_list("/config/connectors", limit=limit, offset=offset)
        return [Connector.from_dict(item) for item in data]

    # -- Structure -------------------------------------------------------

    async def get_pools(self, *, limit: int | None = None, offset: int | None = None) -> list[Pool]:
        data = await self._get_list("/structure/pools", limit=limit, offset=offset)
        return [Pool.from_dict(item) for item in data]

    async def get_stations(self, *, limit: int | None = None, offset: int | None = None) -> list[Station]:
        data = await self._get_list("/structure/stations", limit=limit, offset=offset)
        return [Station.from_dict(item) for item in data]

    async def get_evses(self, *, limit: int | None = None, offset: int | None = None) -> list[Evse]:
        """EVSE identities only - electrical ratings have to be fetched from the station via get_variables."""
        data = await self._get_list("/structure/evses", limit=limit, offset=offset)
        return [Evse.from_dict(item) for item in data]

    async def get_structure_pairs(self, *, limit: int | None = None, offset: int | None = None) -> list[StructurePair]:
        data = await self._get_list("/structure/pairs", limit=limit, offset=offset)
        return [StructurePair.from_dict(item) for item in data]

    # -- Sessions ----------------------------------------------------------

    async def get_sessions(
        self,
        *,
        updated_since: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
    ) -> list[SessionData]:
        """updated_since restricts this to sessions created/changed at or after that moment (by lastUpdated)."""
        filters = {"lastUpdated[gte]": updated_since}
        data = await self._get_list("/sessions", limit=limit, offset=offset, orderby=orderby, filters=filters)
        return [SessionData.from_dict(item) for item in data]

    async def get_session_statuses(
        self,
        *,
        session_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
    ) -> list[SessionStatusEntry]:
        """The status history of session_id (or every session if None) - see SessionStatusEntry."""
        filters = {"sessionId": session_id}
        data = await self._get_list("/sessions/status", limit=limit, offset=offset, orderby=orderby, filters=filters)
        return [SessionStatusEntry.from_dict(item) for item in data]

    async def get_session_operational_data(
        self,
        *,
        session_id: str | None = None,
        after: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
    ) -> list[SessionOperationalData]:
        """Operational data for session_id (or every session if None), optionally only that timestamped strictly
        after the specified moment."""
        filters = {"sessionId": session_id, "timestamp[gt]": after}
        data = await self._get_list(
            "/sessions/operational-data", limit=limit, offset=offset, orderby=orderby, filters=filters
        )
        return [SessionOperationalData.from_dict(item) for item in data]

    # -- Power usage readings -------------------------------------------

    async def get_metering_data(
        self,
        *,
        after: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
    ) -> list[MeteringReading]:
        """Metering readings, optionally only those timestamped strictly after the specified moment."""
        filters = {"timestamp[gt]": after}
        data = await self._get_list(
            "/observation-points/metering-data", limit=limit, offset=offset, orderby=orderby, filters=filters
        )
        return [MeteringReading.from_dict(item) for item in data]

    # -- Session commands (charging schedule read/write) --------------------

    async def get_session_commands(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[SessionCommand]:
        """Every recorded session command - both ours and Troca's own internal ones (see SessionCommand)."""
        data = await self._get_list("/sessions/commands", limit=limit, offset=offset)
        return [SessionCommand.from_dict(item) for item in data]

    async def get_session_command_statuses(
        self,
        *,
        command_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        orderby: str | None = None,
    ) -> list[SessionCommandStatusEntry]:
        """The status history of command_id (or every command if None)."""
        filters = {"commandId": command_id}
        data = await self._get_list(
            "/sessions/commands/status", limit=limit, offset=offset, orderby=orderby, filters=filters
        )
        return [SessionCommandStatusEntry.from_dict(item) for item in data]

    async def get_session_command_locations(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[SessionCommandLocation]:
        data = await self._get_list("/sessions/commands/locations", limit=limit, offset=offset)
        return [SessionCommandLocation.from_dict(item) for item in data]

    async def send_session_commands(self, commands: list[SessionCommand]) -> None:
        await self._post("/sessions/commands", [c.to_dict() for c in commands])

    async def set_session_command_locations(self, locations: list[SessionCommandLocation]) -> None:
        """Setting a command's location is what triggers Troca to validate and dispatch it to the station."""
        await self._post("/sessions/commands/locations", [loc.to_dict() for loc in locations])

    async def set_active_power_schedule(
        self, *, command_id: str, timestamp: str, schedule: ActivePowerSchedule, evse_location: LocationId
    ) -> None:
        """Replaces the charging schedule for whatever session is active on the specified EVSE. This is fire and
        forget - the outcome is later reported via get_session_command_statuses (against command_id)."""
        command = SessionCommand(
            command_id=command_id,
            type=SessionCommandType.SET_CHARGING_PROFILE,
            input_parameters=CommandParameter(
                timestamp=timestamp, parameters=set_charging_profile_parameters(schedule)
            ),
        )
        await self.send_session_commands([command])
        await self.set_session_command_locations([SessionCommandLocation(command_id, evse_location)])

    # -- OCPP passthrough ----------------------------------------------------

    async def send_ocpp_command(self, target: OcppTarget, message_type: str, payload: dict[str, Any]) -> Any:  # noqa: ANN401
        """Sends an arbitrary OCPP request (payload) directly to a charging station via one of Troca's OCPP
        connectors, returning the station's raw OCPP response payload. The request must be wrapped in a "payload"
        key or Troca rejects it with "No input for request" - this isn't in the spec.

        Only the station's direct response comes back - anything the station sends as a follow up message of its
        own (eg ReportChargingProfiles in response to GetChargingProfiles, MeterValues) goes to Troca, not us."""
        path = f"/{target.connector_name}/ocpp/{target.ocpp_version}/command/{message_type}/{target.station_name}"
        return await self._post(path, {"payload": payload})

    async def get_base_report(self, target: OcppTarget, request_id: int, report_base: str) -> str:
        """OCPP 2.x GetBaseReport via the passthrough - returns the station's response status (eg Accepted). The
        report itself is sent by the station to Troca (not us) - it's what populates Troca's structure data."""
        response = await self.send_ocpp_command(
            target, "GetBaseReport", {"requestId": request_id, "reportBase": report_base}
        )
        return (response or {}).get("status", "")

    async def get_variables(self, target: OcppTarget, requests: list[GetVariableData]) -> list[GetVariableResult]:
        """OCPP 2.x GetVariables via the passthrough."""
        response = await self.send_ocpp_command(
            target, "GetVariables", {"getVariableData": [r.to_dict() for r in requests]}
        )
        return [GetVariableResult.from_dict(r) for r in (response or {}).get("getVariableResult", [])]

    async def set_variables(self, target: OcppTarget, requests: list[SetVariableData]) -> list[SetVariableResult]:
        """OCPP 2.x SetVariables via the passthrough."""
        response = await self.send_ocpp_command(
            target, "SetVariables", {"setVariableData": [r.to_dict() for r in requests]}
        )
        return [SetVariableResult.from_dict(r) for r in (response or {}).get("setVariableResult", [])]

    async def set_charging_profile(self, target: OcppTarget, evse_id: int, profile: OcppChargingProfile) -> str:
        """OCPP 2.x SetChargingProfile via the passthrough - returns the station's response status (eg Accepted).
        A profile with the same id as an existing one replaces it."""
        response = await self.send_ocpp_command(
            target, "SetChargingProfile", {"evseId": evse_id, "chargingProfile": profile.to_dict()}
        )
        return (response or {}).get("status", "")

    async def clear_charging_profile(self, target: OcppTarget, charging_profile_id: int) -> str:
        """OCPP 2.x ClearChargingProfile via the passthrough - returns the station's response status. Note that
        "Unknown" is returned if there was no such profile to clear."""
        response = await self.send_ocpp_command(
            target, "ClearChargingProfile", {"chargingProfileId": charging_profile_id}
        )
        return (response or {}).get("status", "")

    async def notify_allowed_energy_transfer(
        self, target: OcppTarget, transaction_id: str, allowed_energy_transfer: list[str]
    ) -> str:
        """OCPP 2.1 NotifyAllowedEnergyTransfer via the passthrough - tells the station which energy transfer modes
        (eg DC, DC_BPT) are allowed for transaction_id. Returns the station's response status (eg Accepted)."""
        response = await self.send_ocpp_command(
            target,
            "NotifyAllowedEnergyTransfer",
            {"transactionId": transaction_id, "allowedEnergyTransfer": allowed_energy_transfer},
        )
        return (response or {}).get("status", "")
