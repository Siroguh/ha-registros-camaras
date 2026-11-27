"""Exercise real HTTP transport, credential boundaries and command payloads."""

import json

from aiohttp import web
import pytest

from custom_components.registros_camaras.api import (
    AuthenticationError,
    Conflict,
    IncompatibleApi,
    RegistrosClient,
    Rejected,
    decode_snapshot,
    normalize_url,
)
from custom_components.registros_camaras.const import DEFAULT_CENTRE


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com",
        "file:///etc/passwd",
        "https://example.com/api",
        "https://example.com?key=secret",
        "https://example.com/#token",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_invalid_snapshot(raw):
    raw["chambers"][0]["occupants"][0]["quantity"] = "unknown"
    with pytest.raises(IncompatibleApi):
        decode_snapshot(raw, DEFAULT_CENTRE)


def test_storage_locations_keep_dashboard_entities(raw):
    raw["locations"].extend(
        [
            {
                "id": "storage-108",
                "centreId": DEFAULT_CENTRE,
                "name": "Producto terminado",
                "kind": "storage",
                "archived": False,
            },
            {
                "id": "finalizado",
                "centreId": DEFAULT_CENTRE,
                "name": "Finalizado",
                "kind": "terminal",
                "archived": False,
            },
        ]
    )
    snapshot = decode_snapshot(raw, DEFAULT_CENTRE)
    assert "storage-108" in snapshot.locations
    assert "finalizado" not in snapshot.locations


async def test_http_contract_and_exact_retries(aiohttp_client, socket_enabled, raw):
    requests = []

    async def route(request):
        assert request.headers["Authorization"] == "Bearer test-native-key"
        if request.method == "GET":
            return web.json_response(raw)
        requests.append((request.path, await request.json()))
        return web.json_response({"version": 8})

    app = web.Application()
    app.router.add_route("*", "/api/{tail:.*}", route)
    http = await aiohttp_client(app)
    client = RegistrosClient(
        http.session, str(http.make_url("/")).rstrip("/"), "test-native-key", DEFAULT_CENTRE
    )
    snapshot = await client.snapshot()
    assert snapshot.ready and snapshot.revision == 7
    command = {
        "command_id": "00000000-0000-4000-8000-000000000123",
        "expected_revision": 7,
        "location_id": "cam-1",
        "proposal_id": "proposal:7",
    }
    for _ in range(2):
        await client.command("confirm_proposal", command)
    assert requests[0] == requests[1]
    assert requests[0][1] == {
        "commandId": command["command_id"],
        "expectedRevision": 7,
        "action": {"kind": "confirm", "locationId": "cam-1", "proposalId": "proposal:7"},
    }
    await client.command(
        "complete_task",
        {
            **command,
            "task_id": "task:1/a",
            "task_revision": 3,
            "result": {"kind": "curing_review", "outcome": "ready", "notes": ""},
        },
    )
    assert requests[-1][1]["expectedTaskRevision"] == 3
    await client.command(
        "create_lot", {**command, "code": "A", "quantity": 10, "started_on": "2026-11-27"}
    )
    assert requests[-1][1]["startedOn"] == "2026-11-27"
    await client.command(
        "create_movement",
        {
            **command,
            "lot_id": "lot-1",
            "source_location_id": "cam-1",
            "occurred_on": "2026-11-27",
            "destinations": [{"locationId": "cam-2", "quantity": 5}],
        },
    )
    assert requests[-1][1]["destinations"][0]["quantity"] == 5


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (409, Conflict),
        (404, IncompatibleApi),
        (302, Rejected),
    ],
)
async def test_error_and_redirect_boundaries(aiohttp_client, socket_enabled, status, error):
    async def route(request):
        return web.Response(
            status=status,
            headers={"Location": "https://other.example"},
            text="private server details",
        )

    app = web.Application()
    app.router.add_get("/api/integration/" + DEFAULT_CENTRE, route)
    http = await aiohttp_client(app)
    client = RegistrosClient(
        http.session, str(http.make_url("/")).rstrip("/"), "test-key", DEFAULT_CENTRE
    )
    with pytest.raises(error) as caught:
        await client.snapshot()
    assert "private server details" not in str(caught.value)


def test_translation_keys_and_service_descriptions():
    from pathlib import Path

    import yaml

    from custom_components.registros_camaras.services import SCHEMAS

    root = Path(__file__).parents[1] / "custom_components/registros_camaras"
    assert json.loads((root / "strings.json").read_text()) == json.loads(
        (root / "translations/en.json").read_text()
    )
    descriptions = yaml.safe_load((root / "services.yaml").read_text())
    assert descriptions.keys() == SCHEMAS.keys()


async def test_business_rejection_is_not_a_stale_revision(aiohttp_client, socket_enabled):
    async def route(request):
        return web.json_response(
            {"_tag": "MovementRejected", "code": "before_entry", "message": "private detail"},
            status=409,
        )

    app = web.Application()
    app.router.add_get("/api/options", route)
    http = await aiohttp_client(app)
    client = RegistrosClient(
        http.session, str(http.make_url("/")).rstrip("/"), "test-key", DEFAULT_CENTRE
    )
    with pytest.raises(Rejected, match="fecha precede") as caught:
        await client.request("GET", "options")
    assert "private detail" not in str(caught.value)
