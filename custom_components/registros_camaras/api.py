from dataclasses import dataclass
from typing import Any

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

    async def _request(self, method: str, path: str) -> Any:
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
            ) as response:
                if response.status in {401, 403}:
                    raise AuthenticationError("Invalid, revoked or unauthorized API key")
                if response.status == 404:
                    raise IncompatibleApi("The integration endpoint is not available")
                if response.status != 200:
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
