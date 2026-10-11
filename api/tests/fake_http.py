"""An httpx client whose answers the test queues up, and which keeps every
request it was sent. For the worker's calls to Simple WMS and Platen."""
import json

import httpx


class FakeHttp:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list = []     # (status, body) or an exception; empty means 202 {}
        self.client = httpx.Client(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (202, {})
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]
