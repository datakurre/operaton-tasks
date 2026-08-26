from aiohttp import ClientResponse, web
from aiohttp import client_reqrep
from aiohttp.test_utils import TestClient

from typing import Any
from typing import Dict
from typing import List


class FakeRequestResponse(ClientResponse):
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return b""


class ReadTrackingResponse:
    def __init__(self, response: ClientResponse) -> None:
        self._response = response
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return await self._response.read()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


class ReadTrackingSession:
    def __init__(self, session: TestClient) -> None:
        self._session = session
        self.responses: List[ReadTrackingResponse] = []

    async def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        **kwargs: Any,
    ) -> ClientResponse:
        original_read = client_reqrep.ClientResponse.read

        async def tracked_read(response: ClientResponse) -> bytes:
            for tracked in self.responses:
                if tracked._response is response:
                    tracked.read_calls += 1
                    break
            else:
                tracked = ReadTrackingResponse(response)
                tracked.read_calls = 1
                self.responses.append(tracked)
            return await original_read(response)

        client_reqrep.ClientResponse.read = tracked_read  # type: ignore[assignment]
        try:
            response = await self._session.request(method, url, headers=headers, **kwargs)
            return response
        finally:
            client_reqrep.ClientResponse.read = original_read  # type: ignore[method-assign]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def with_read_tracking(session: TestClient) -> ReadTrackingSession:
    return ReadTrackingSession(session)


class FakeServer:
    def __init__(self, statuses: List[int]) -> None:
        self._statuses = statuses
        self.calls: List[web.Request] = []
        self.app = web.Application()
        self.app.router.add_route("*", "/{tail:.*}", self.handle_request)
        self.responses: List[web.Response] = []
        self.bodies: List[bytes] = []

    async def handle_request(self, request: web.Request) -> web.Response:
        self.calls.append(request)
        self.bodies.append(await request.read())
        status = self._statuses.pop(0)
        response = web.Response(status=status)
        self.responses.append(response)
        return response


class FakeSession(TestClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.responses: List[ReadTrackingResponse] = []

    async def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        **kwargs: Any,
    ) -> ClientResponse:
        response = await super().request(method, url, headers=headers, **kwargs)
        return response
