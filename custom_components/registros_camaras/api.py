from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL


class ApiError(Exception):
    pass


class AuthenticationError(ApiError):
    pass


class CannotConnect(ApiError):
    pass


class IncompatibleApi(ApiError):
    pass


class RequestError(ApiError):
    pass


def normalize_url(value: str) -> str:
    url = URL(value.strip())
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.user is not None
        or url.password is not None
        or url.query_string
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("Use the application origin without credentials or a path")
    return str(url).rstrip("/")


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    name: str
    account_id: str
    api_version: int
    capabilities: tuple[str, ...]


class RegistrosClient:
    def __init__(self, session: ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self.base_url = normalize_url(base_url)
        self._api_key = api_key.strip()

    async def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}/api/v1/{path}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
                timeout=ClientTimeout(total=10),
                allow_redirects=False,
                raise_for_status=False,
                json=payload,
            ) as response:
                if response.status in {401, 403}:
                    raise AuthenticationError("Invalid, revoked or unauthorized API key")
                if response.status == 404 and path.startswith("integration/"):
                    raise IncompatibleApi("Actualiza el servidor: falta el endpoint de integración")
                if response.status in {400, 404, 409, 422}:
                    data = await response.json()
                    error = data.get("error") if isinstance(data, dict) else None
                    message = error.get("message") if isinstance(error, dict) else None
                    message = message or "Operación rechazada por la API"
                    raise RequestError(str(message))
                if response.status not in {200, 201}:
                    raise CannotConnect(f"API returned HTTP {response.status}")
                return await response.json()
        except (ClientError, TimeoutError) as err:
            raise CannotConnect("Unable to reach the application API") from err
        except ValueError as err:
            raise IncompatibleApi("The application did not return valid JSON") from err

    async def async_get_status(self) -> IntegrationStatus:
        data = await self._request("GET", "integration/status")
        if (
            not isinstance(data, dict)
            or data.get("application") != "registros_camaras"
            or type(data.get("apiVersion")) is not int
            or data["apiVersion"] != 1
            or data.get("status") != "ok"
            or not isinstance(data.get("name"), str)
            or not data["name"]
            or not isinstance(data.get("accountId"), str)
            or not data["accountId"]
            or not isinstance(data.get("capabilities"), list)
            or not all(isinstance(item, str) for item in data["capabilities"])
            or "connection:read" not in data["capabilities"]
        ):
            raise IncompatibleApi("Unsupported application API contract")
        return IntegrationStatus(
            name=data["name"],
            account_id=data["accountId"],
            api_version=data["apiVersion"],
            capabilities=tuple(data["capabilities"]),
        )

    async def async_dashboard(self) -> dict:
        data = await self._request("GET", "integration/dashboard")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("camaras"), list)
            or not isinstance(data.get("ubicaciones"), list)
        ):
            raise IncompatibleApi("Actualiza el servidor: falta el contrato de controles")
        for camera in data["camaras"]:
            if not isinstance(camera, dict) or not all(
                key in camera
                for key in (
                    "ubicacionId",
                    "nombre",
                    "consigna",
                    "pendiente",
                    "origen",
                    "lotes",
                    "manualHasta",
                )
            ):
                raise IncompatibleApi("Respuesta de cámara incompatible")
        return data

    async def async_operation(self, operation: str, data: dict) -> dict:
        location = quote(str(data.get("location_id", "")), safe="")
        lot = quote(str(data.get("lot_id", "")), safe="")
        match operation:
            case "lots":
                query = urlencode(
                    {
                        "offset": data.get("offset", 0),
                        "limit": 100,
                        "buscar": data.get("search", ""),
                    }
                )
                return await self._request("GET", f"lotes?{query}")
            case "movement_options":
                query = urlencode({"fecha": data.get("date", "")})
                return await self._request("GET", f"lotes/{lot}/opciones-movimiento?{query}")
            case "create_lot":
                return await self._request("POST", "lotes", data.get("payload", {}))
            case "register_movement":
                return await self._request(
                    "POST", f"lotes/{lot}/movimientos", data.get("payload", {})
                )
            case "set_manual":
                return await self._request(
                    "POST", f"ubicaciones/{location}/consignas", data.get("payload", {})
                )
            case "confirm_change":
                return await self._request(
                    "POST",
                    f"ubicaciones/{location}/confirmacion",
                    {"propuestaId": data.get("proposal_id")},
                )
            case _:
                raise RequestError("Operación no disponible")
