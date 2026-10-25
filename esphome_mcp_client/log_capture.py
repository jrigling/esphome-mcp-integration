"""Bounded, filterable capture over any line stream.

Log streams never end on their own, so a capture stops at the first of: the
stream finishing, ``max_lines`` *kept* lines (after filtering, so a narrow
filter can wait for matches), or ``stop_event`` being set (a background capture
the caller ended early). Kept lines live here, not in the stream, so cancelling
the stream task on an early stop loses nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from .command import CommandResult
from .logtext import LogFilter

LineStream = Callable[[Callable[[str], None]], Awaitable[CommandResult | None]]


async def async_capture(
    stream: LineStream,
    *,
    line_filter: LogFilter | None = None,
    max_lines: int,
    on_line: Callable[[str], None] | None = None,
    stop_event: asyncio.Event | None = None,
) -> CommandResult:
    """Run ``stream(on_raw_line)`` and return the kept lines.

    ``stream`` must call the callback for every raw line and return when its
    own deadline passes (its ``CommandResult`` supplies ``exit_code`` and
    ``truncated``). ``on_line`` sees each kept line as it arrives.
    """
    flt = line_filter if line_filter is not None else LogFilter(include_config_dump=True)
    result = CommandResult()
    full = asyncio.Event()

    def _on_raw(raw: str) -> None:
        if full.is_set():
            return
        for line in flt.feed(raw):
            result.lines.append(line)
            if on_line is not None:
                on_line(line)
        if len(result.lines) >= max_lines:
            full.set()

    stream_task = asyncio.ensure_future(stream(_on_raw))
    waiters = [asyncio.ensure_future(full.wait())]
    if stop_event is not None:
        waiters.append(asyncio.ensure_future(stop_event.wait()))
    try:
        await asyncio.wait([stream_task, *waiters], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()

    if full.is_set():
        result.end_reason = "max_lines"
    elif stream_task.done():
        # Surfaces the stream's own error (connection failure etc.).
        underlying = stream_task.result()
        if underlying is not None:
            result.exit_code = underlying.exit_code
            result.truncated = underlying.truncated
        result.end_reason = "window" if result.truncated else "closed"
    else:
        result.end_reason = "stopped"
    if not stream_task.done():
        stream_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stream_task
    if result.end_reason in ("max_lines", "stopped"):
        result.truncated = True
    return result
