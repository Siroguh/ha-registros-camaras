"""Async client for the versioned Registros Cámaras 2 API. No HA dependencies."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp


class ApiError(Exception):
    """A safe error, never containing credentials or server response bodies."""


class AuthenticationError(ApiError):
    """The application rejected the API key."""


class CannotConnect(ApiError):
    """Unavailable server or unknown command outcome."""


class IncompatibleApi(ApiError):
    """The server does not implement our contract."""


class Conflict(ApiError):
    """Refresh and review the new revision before issuing a new command."""


class Rejected(ApiError):
    """The server rejected the operation."""


def normalize_url(value: str) -> str:
    """Accept a server root, never embedded credentials, queries or fragments."""
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        raise ValueError("Use the HTTP(S) server root")
    _ = parts.port
    return urlunsplit((parts.scheme, parts.netloc.lower(), "", "", ""))


def segment(value: str) -> str:
    return quote(value, safe="")


@dataclass(frozen=True)
class Snapshot:
    """A single authoritative centre revision; pending reads are not empty stock."""

    raw: dict[str, Any]

    @property
    def ready(self) -> bool:
        return self.raw["status"] == "ready"

    @property
    def revision(self) -> int:
        return self.raw["revision"]

    @property
    def locations(self) -> dict[str, dict[str, Any]]:
        return {v["id"]: v for v in self.raw["locations"] if v["kind"] != "terminal"}

    @property
    def chambers(self) -> dict[str, dict[str, Any]]:
        return {v["locationId"]: v for v in self.raw["chambers"]}

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return self.raw["tasks"]


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid contract value")


def _timestamp(value: Any) -> None:
    _require(type(value) in (int, float) and math.isfinite(value))
    datetime.fromtimestamp(value / 1000, UTC)


def decode_snapshot(value: Any, centre_id: str) -> Snapshot:
    """Fail closed on malformed snapshots rather than publish plausible zeroes."""
    try:
        _require(type(value["apiVersion"]) is int and value["apiVersion"] == 1)
        _require(value["centreId"] == centre_id)
        _require(value["status"] in {"ready", "pending"})
        for key in ("revision", "planRevision"):
            _require(type(value[key]) is int and value[key] >= 0)
        _require(isinstance(value["sampledAt"], str))
        for key in ("locations", "chambers", "tasks"):
            _require(isinstance(value[key], list))
        for loc in value["locations"]:
            _require(isinstance(loc["id"], str) and isinstance(loc["name"], str))
            _require(loc["centreId"] == centre_id)
            _require(loc["kind"] in {"chamber", "storage", "terminal"})
            _require(type(loc["archived"]) is bool)
        for chamber in value["chambers"]:
            _require(isinstance(chamber["locationId"], str))
            _require(
                chamber["status"] in {"empty", "no_plan", "no_phase", "compatible", "conflict"}
            )
            _require(isinstance(chamber.get("occupants", []), list))
            for occupant in chamber.get("occupants", []):
                _require(isinstance(occupant["lotId"], str))
                _require(type(occupant["quantity"]) is int and occupant["quantity"] >= 0)
            for key in ("effective", "ideal", "automatic"):
                _validate_limits(chamber[key])
            if chamber["manual"] is not None:
                _validate_limits(chamber["manual"]["limits"])
                _require(chamber["manual"]["limits"] is not None)
                _require(isinstance(chamber["manual"]["id"], str))
                _timestamp(chamber["manual"]["startsAt"])
                _timestamp(chamber["manual"]["endsAt"])
            if (proposal := chamber["proposal"]) is not None:
                _require(isinstance(proposal["id"], str))
                _require(type(proposal["overdue"]) is bool)
                _timestamp(proposal["dueAt"])
                _validate_limits(proposal["limits"])
        for task in value["tasks"]:
            _require(isinstance(task["id"], str) and isinstance(task["lotId"], str))
            _require(task["status"] in {"pending", "due"})
            _require(type(task["revision"]) is int)
            _timestamp(task["dueAt"])
            _require(task["step"]["kind"] in {"curing_review", "quality_check", "remove_product"})
        return Snapshot(value)
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as err:
        raise IncompatibleApi("Invalid integration snapshot") from err


def _validate_limits(limits: Any) -> None:
    if limits is None:
        return
    for metric in ("temperature", "humidity"):
        bounds = limits[metric]
        _require(type(bounds["min"]) in (int, float) and math.isfinite(bounds["min"]))
        _require(type(bounds["max"]) in (int, float) and math.isfinite(bounds["max"]))
        _require(bounds["min"] <= bounds["max"])


MOVEMENT_ERRORS = {
    "before_entry": "La fecha precede a la entrada inicial del lote.",
    "insufficient_stock": "No hay existencias suficientes en la fecha seleccionada.",
    "ambiguous_sources": "Selecciona las procedencias o activa la mezcla de origen.",
    "invalid_contributions": "Revisa las cantidades y procedencias del reparto.",
    "invalid_distribution": "Revisa las cantidades y destinos del reparto.",
    "immutable_entry": "La entrada inicial se corrige desde la ficha del lote.",
    "archived_destination": "El destino está archivado.",
}


class RegistrosClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, key: str, centre_id: str) -> None:
        self.session = session
        self.url = normalize_url(url)
        self._key = key.strip()
        if not re.fullmatch(r"[a-z0-9-]{1,100}", centre_id):
            raise ValueError("Invalid centre")
        self.centre_id = centre_id

    async def request(
        self, method: str, path: str, body: dict | None = None, params: dict | None = None
    ) -> dict:
        try:
            async with asyncio.timeout(30):
                async with self.session.request(
                    method,
                    f"{self.url}/api/{path}",
                    headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
                    json=body,
                    params=params,
                    allow_redirects=False,
                ) as response:
                    if response.status in {401, 403}:
                        raise AuthenticationError("API key rejected")
                    if response.status == 409:
                        try:
                            error = json.loads(await response.content.read(65536))
                        except ValueError, UnicodeError:
                            error = {}
                        if isinstance(error, dict) and error.get("_tag") == "MovementRejected":
                            raise Rejected(
                                MOVEMENT_ERRORS.get(
                                    error.get("code"), "Movimiento rechazado; revisa los datos."
                                )
                            )
                        raise Conflict("Revision changed; refresh and review")
                    if response.status in {404, 405}:
                        raise IncompatibleApi("Endpoint, centre or resource not found")
                    if response.status == 429 or response.status >= 500:
                        raise CannotConnect("Server unavailable; retry commands with the same ID")
                    if not 200 <= response.status < 300:
                        raise Rejected("Operation rejected")
                    payload = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        payload.extend(chunk)
                        if len(payload) > 8 * 1024 * 1024:
                            raise IncompatibleApi("Response too large")
                    result = json.loads(payload)
                    if not isinstance(result, dict):
                        raise IncompatibleApi("Expected an object")
                    return result
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect("Connection failed; retry commands with the same ID") from err
        except (ValueError, UnicodeError) as err:
            raise IncompatibleApi("Invalid JSON response") from err

    async def snapshot(self) -> Snapshot:
        return decode_snapshot(
            await self.request("GET", f"integration/{self.centre_id}"), self.centre_id
        )

    async def sensor_stream(self) -> dict:
        value = await self.request("GET", f"environment/streams/{self.centre_id}")
        try:
            _require(type(value["apiVersion"]) is int and value["apiVersion"] == 1)
            _require(value["maxBatchSize"] == 100 and value["maxBatchDelayMillis"] == 2000)
            _require(type(value["revision"]) is int and value["revision"] >= 0)
            _require(isinstance(value["assignments"], list))
            ids = set()
            for assignment in value["assignments"]:
                for key in ("id", "source", "entityId", "locationId"):
                    _require(isinstance(assignment[key], str) and bool(assignment[key]))
                _require(assignment["id"] not in ids)
                ids.add(assignment["id"])
                _require(assignment["entityId"].startswith("sensor."))
                for key in ("startsAt", "historyFrom"):
                    _timestamp(assignment[key])
                if assignment["endsAt"] is not None:
                    _timestamp(assignment["endsAt"])
            return value
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as err:
            raise IncompatibleApi("Invalid sensor stream manifest") from err

    async def push_updates(self, batch: dict) -> None:
        receipt = await self.request("POST", "environment/updates", batch)
        if (
            receipt.get("commandId") != batch["commandId"]
            or receipt.get("stream") != f"ReadingIngestion/{batch['commandId']}"
            or receipt.get("eventId") != f"ReadingIngestion/{batch['commandId']}:1"
            or receipt.get("version") != 1
        ):
            raise IncompatibleApi("Unexpected durable ingestion acknowledgement")

    async def command(self, action: str, data: dict) -> dict:
        """IDs and revisions come from the caller, so retries remain durable across HA restarts."""
        body = {"commandId": data["command_id"], "expectedRevision": data["expected_revision"]}
        plans_path = f"centres/{self.centre_id}/plans"
        lots_path = f"production/{self.centre_id}/lots"
        if action == "confirm_proposal":
            path = plans_path
            body["action"] = {
                "kind": "confirm",
                "locationId": data["location_id"],
                "proposalId": data["proposal_id"],
            }
        elif action == "complete_task":
            path = f"centres/{self.centre_id}/tasks/{segment(data['task_id'])}/completions"
            body.update(expectedTaskRevision=data["task_revision"], result=data["result"])
        elif action == "create_lot":
            path = lots_path
            body.update(code=data["code"], quantity=data["quantity"], startedOn=data["started_on"])
        elif action in {"schedule_manual", "replace_manual", "finish_manual"}:
            path = plans_path
            change = {"kind": action, "locationId": data["location_id"], "reason": data["reason"]}
            if action != "schedule_manual":
                change["manualId"] = data["manual_id"]
            if action != "finish_manual":
                change.update(
                    limits=data["limits"], startsAt=data["starts_at"], endsAt=data["ends_at"]
                )
            body["action"] = change
        elif action in {"create_movement", "correct_movement"}:
            path = f"{lots_path}/{segment(data['lot_id'])}/movements"
            body.update(
                occurredOn=data["occurred_on"],
                sourceLocationId=data["source_location_id"],
                destinations=data["destinations"],
                mixSources=data.get("mix_sources", False),
                notes=data.get("notes", ""),
            )
            if action == "correct_movement":
                path += f"/{segment(data['movement_id'])}/corrections"
                body["reason"] = data["reason"]
        else:
            raise Rejected("Unknown action")
        return await self.request("POST", path, body)
