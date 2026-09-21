from __future__ import annotations

import types
from typing import Any, Self

import aiohttp

from .models import (
    ChargingProfile,
    CommandStatus,
    Evse,
    MeteringReading,
    Pool,
    SessionCommand,
    SessionCommandRequest,
    SessionCommandType,
    SessionData,
    Station,
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

    async def _post(self, path: str, payload: list[dict[str, Any]]) -> None:
        session = self._ensure_session()
        async with session.post(f"{self._base_url}{path}", json=payload) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrocaApiError(resp.status, "POST", path, body)

    # -- Device metadata -----------------------------------------------

    async def get_pools(self, *, limit: int | None = None, skip: int | None = None) -> list[Pool]:
        data = await self._get("/structure/pools", self._params(limit, skip))
        return [Pool.from_dict(item) for item in data or []]

    async def get_stations(self, *, limit: int | None = None, skip: int | None = None) -> list[Station]:
        data = await self._get("/structure/stations", self._params(limit, skip))
        return [Station.from_dict(item) for item in data or []]

    async def get_evses(self, *, limit: int | None = None, skip: int | None = None) -> list[Evse]:
        """Metadata about connected charge points (vendor/model/capability
        flags). Does not include electrical ratings -- see ``models.Evse``.
        """
        data = await self._get("/structure/evses", self._params(limit, skip))
        return [Evse.from_dict(item) for item in data or []]

    # -- Sessions (incl. connected-EV charge/discharge-rate metadata) ---

    async def get_sessions(self, *, limit: int | None = None, skip: int | None = None) -> list[SessionData]:
        """Active/past charging sessions. ``SessionData.constraints`` is

        where the connected EV's own max/min charge and discharge rate,
        battery capacity, and V2G support are reported -- there is no
        equivalent static, pre-session EVSE capability endpoint (see
        ``get_evses``).
        """
        data = await self._get("/sessions", self._params(limit, skip))
        return [SessionData.from_dict(item) for item in data or []]

    # -- Power usage readings -------------------------------------------

    async def get_metering_data(self, *, limit: int | None = None, skip: int | None = None) -> list[MeteringReading]:
        data = await self._get("/observation-points/metering-data", self._params(limit, skip))
        return [MeteringReading.from_dict(item) for item in data or []]

    # -- Charging schedule read/write ------------------------------------

    async def get_session_commands(self, *, limit: int | None = None, skip: int | None = None) -> list[SessionCommand]:
        """All recorded session commands, including past
        ``set_charging_profile`` / ``clear_charging_profile`` commands and
        their ``status``. A polling service enforcing a schedule should use
        this to confirm the last profile it pushed was ``accepted``.
        """
        data = await self._get("/sessions/commands", self._params(limit, skip))
        return [SessionCommand.from_dict(item) for item in data or []]

    async def get_command_statuses(self, *, limit: int | None = None, skip: int | None = None) -> list[CommandStatus]:
        """Raw ``/sessions/commands/status``. Per the spec this returns a
        bare list of status enum values with no command id attached, so it
        can't tell you *which* command a status belongs to -- prefer
        ``get_session_commands`` and read ``.status`` off the record you
        care about.
        """
        data = await self._get("/sessions/commands/status", self._params(limit, skip))
        return [CommandStatus(item) for item in data or []]

    async def send_session_command(self, command: SessionCommandRequest) -> None:
        """Low-level escape hatch for any ``SessionCommandType``."""
        await self._post("/sessions/commands", [command.to_dict()])

    async def set_charging_profile(
        self,
        *,
        command_id: str,
        profile: ChargingProfile,
        pool_id: str | None = None,
        station_id: str | None = None,
        evse_id: int | None = None,
    ) -> None:
        """Push a time-bounded power limit onto a session's EVSE."""
        command = SessionCommandRequest(
            id=command_id,
            type=SessionCommandType.SET_CHARGING_PROFILE,
            pool_id=pool_id,
            station_id=station_id,
            evse_id=evse_id,
            input_parameters=profile.to_dict(),
        )
        await self.send_session_command(command)

    async def clear_charging_profile(
        self,
        *,
        command_id: str,
        pool_id: str | None = None,
        station_id: str | None = None,
        evse_id: int | None = None,
        charging_profile_id: int | None = None,
    ) -> None:
        """Remove a previously-set charging profile."""
        command = SessionCommandRequest(
            id=command_id,
            type=SessionCommandType.CLEAR_CHARGING_PROFILE,
            pool_id=pool_id,
            station_id=station_id,
            evse_id=evse_id,
            input_parameters=({"chargingProfileId": charging_profile_id} if charging_profile_id is not None else None),
        )
        await self.send_session_command(command)
