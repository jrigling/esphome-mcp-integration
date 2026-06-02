"""Shared test fixtures.

Unit tests exercise the client against a real in-process aiohttp server rather
than a mocking library, so they can't drift out of sync with aiohttp itself.
"""
from __future__ import annotations

import socket
from collections.abc import Awaitable, Callable

import pytest
from aiohttp import web


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def serve() -> Callable[[web.Application], Awaitable[str]]:
    """Factory that serves an aiohttp app on a free port and returns its URL."""
    runners: list[web.AppRunner] = []

    async def _serve(app: web.Application) -> str:
        runner = web.AppRunner(app)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}"

    yield _serve

    for runner in runners:
        await runner.cleanup()


def make_app(routes: list[tuple[str, str, object]]) -> web.Application:
    """Build an app from (method, path, handler) tuples."""
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    return app


def json_route(data: object):
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(data)

    return handler


def text_route(body: str):
    async def handler(_request: web.Request) -> web.Response:
        return web.Response(text=body)

    return handler


def status_route(code: int):
    async def handler(_request: web.Request) -> web.Response:
        return web.Response(status=code)

    return handler
