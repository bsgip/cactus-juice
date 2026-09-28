from __future__ import annotations

import json
import types
from typing import Any, Self

import aiohttp

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
    Station,
    StructurePair,
    set_charging_profile_parameters,
)


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
    def _params(limit: int | None, skip: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if skip is not None:
            params["skip"] = skip
        return params

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict | list | None:
        session = self._ensure_session()
        async with session.get(f"{self._base_url}{path}", params=params) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrocaApiError(resp.status, "GET", path, body)
            return await resp.json(content_type=None) if body else None

    async def _post(self, path: str, payload: list[dict[str, Any]] | dict[str, Any]) -> Any:  # noqa: ANN401
        """POSTs payload as JSON - returns the parsed JSON response body (or None if it's empty / not JSON, eg
        Troca's plain text "Success")."""
        session = self._ensure_session()
        async with session.post(f"{self._base_url}{path}", json=payload) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrocaApiError(resp.status, "POST", path, body)
            try:
                return json.loads(body) if body else None
            except ValueError:
                return None

    # -- Config -----------------------------------------------

    async def get_connectors(self, *, limit: int | None = None, skip: int | None = None) -> list[Connector]:
        data = await self._get("/config/connectors", self._params(limit, skip))
        return [Connector.from_dict(item) for item in data or []]

    # -- Structure -------------------------------------------------------

    async def get_pools(self, *, limit: int | None = None, skip: int | None = None) -> list[Pool]:
        data = await self._get("/structure/pools", self._params(limit, skip))
        return [Pool.from_dict(item) for item in data or []]

    async def get_stations(self, *, limit: int | None = None, skip: int | None = None) -> list[Station]:
        data = await self._get("/structure/stations", self._params(limit, skip))
        return [Station.from_dict(item) for item in data or []]

    async def get_evses(self, *, limit: int | None = None, skip: int | None = None) -> list[Evse]:
        """EVSE identities only - electrical ratings have to be fetched from the station via get_variables."""
        data = await self._get("/structure/evses", self._params(limit, skip))
        return [Evse.from_dict(item) for item in data or []]

    async def get_structure_pairs(self, *, limit: int | None = None, skip: int | None = None) -> list[StructurePair]:
        data = await self._get("/structure/pairs", self._params(limit, skip))
        return [StructurePair.from_dict(item) for item in data or []]

    # -- Sessions ----------------------------------------------------------

    async def get_sessions(self, *, limit: int | None = None, skip: int | None = None) -> list[SessionData]:
        data = await self._get("/sessions", self._params(limit, skip))
        return [SessionData.from_dict(item) for item in data or []]

    async def get_session_statuses(
        self, *, limit: int | None = None, skip: int | None = None
    ) -> list[SessionStatusEntry]:
        """The full status history of every session (see SessionStatusEntry)."""
        data = await self._get("/sessions/status", self._params(limit, skip))
        return [SessionStatusEntry.from_dict(item) for item in data or []]

    async def get_session_operational_data(
        self, *, limit: int | None = None, skip: int | None = None
    ) -> list[SessionOperationalData]:
        data = await self._get("/sessions/operational-data", self._params(limit, skip))
        return [SessionOperationalData.from_dict(item) for item in data or []]

    # -- Power usage readings -------------------------------------------

    async def get_metering_data(self, *, limit: int | None = None, skip: int | None = None) -> list[MeteringReading]:
        data = await self._get("/observation-points/metering-data", self._params(limit, skip))
        return [MeteringReading.from_dict(item) for item in data or []]

    # -- Session commands (charging schedule read/write) --------------------

    async def get_session_commands(self, *, limit: int | None = None, skip: int | None = None) -> list[SessionCommand]:
        """Every recorded session command - both ours and Troca's own internal ones (see SessionCommand)."""
        data = await self._get("/sessions/commands", self._params(limit, skip))
        return [SessionCommand.from_dict(item) for item in data or []]

    async def get_session_command_statuses(
        self, *, limit: int | None = None, skip: int | None = None
    ) -> list[SessionCommandStatusEntry]:
        data = await self._get("/sessions/commands/status", self._params(limit, skip))
        return [SessionCommandStatusEntry.from_dict(item) for item in data or []]

    async def get_session_command_locations(
        self, *, limit: int | None = None, skip: int | None = None
    ) -> list[SessionCommandLocation]:
        data = await self._get("/sessions/commands/locations", self._params(limit, skip))
        return [SessionCommandLocation.from_dict(item) for item in data or []]

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

    async def get_variables(self, target: OcppTarget, requests: list[GetVariableData]) -> list[GetVariableResult]:
        """OCPP 2.x GetVariables via the passthrough."""
        response = await self.send_ocpp_command(
            target, "GetVariables", {"getVariableData": [r.to_dict() for r in requests]}
        )
        return [GetVariableResult.from_dict(r) for r in (response or {}).get("getVariableResult", [])]

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
