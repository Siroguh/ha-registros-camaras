"""Run the actual Home Assistant integration lifecycle."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.registros_camaras.api import decode_snapshot
from custom_components.registros_camaras.const import DEFAULT_CENTRE, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

LIMITS = {"temperature": {"min": 2, "max": 6}, "humidity": {"min": 50, "max": 70}}
RAW = {
    "apiVersion": 1,
    "centreId": DEFAULT_CENTRE,
    "revision": 7,
    "planRevision": 7,
    "sampledAt": "2026-11-27T12:00:00Z",
    "status": "ready",
    "locations": [
        {
            "id": "cam-1",
            "centreId": DEFAULT_CENTRE,
            "name": "Cámara 1",
            "kind": "chamber",
            "archived": False,
            "description": None,
        }
    ],
    "chambers": [
        {
            "locationId": "cam-1",
            "status": "compatible",
            "ideal": LIMITS,
            "automatic": LIMITS,
            "effective": LIMITS,
            "manual": None,
            "proposal": {
                "id": "proposal:7",
                "contextKey": "context:7",
                "limits": LIMITS,
                "createdAt": 1795777200000,
                "dueAt": 1795777500000,
                "overdue": True,
            },
            "occupants": [
                {
                    "lotId": "lot-1",
                    "quantity": 45,
                    "age": 3,
                    "baseVersionId": None,
                    "privateRevisionId": None,
                    "stepKey": None,
                    "limits": LIMITS,
                }
            ],
        }
    ],
    "tasks": [
        {
            "id": "task:1",
            "lotId": "lot-1",
            "revision": 3,
            "dueAt": 1795777200000,
            "status": "due",
            "step": {
                "kind": "curing_review",
                "key": "review",
                "title": "Revisión",
                "instructions": "Revisar",
                "day": 1,
            },
        }
    ],
}


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def raw():
    return deepcopy(RAW)


@pytest.fixture
def client(raw):
    with patch(
        "custom_components.registros_camaras.api.RegistrosClient.snapshot",
        new=AsyncMock(return_value=decode_snapshot(raw, DEFAULT_CENTRE)),
    ) as snapshot:
        yield snapshot


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="Registros Test",
        unique_id="server-id",
        data={
            "url": "https://registros.example",
            "api_key": "test-key",
            "centre_id": DEFAULT_CENTRE,
        },
    )
    entry.add_to_hass(hass)
    return entry
