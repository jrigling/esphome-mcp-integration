"""Edge cases of the shared bounded-capture helper."""
import asyncio

import pytest

from esphome_mcp_client import CommandResult, LogFilter
from esphome_mcp_client.log_capture import async_capture


def stream_of(lines, *, exit_code=None, truncated=False, delay=0.0, forever=False):
    async def stream(on_raw):
        for line in lines:
            await asyncio.sleep(delay)
            on_raw(line)
        while forever:
            await asyncio.sleep(0.01)
        return CommandResult(exit_code=exit_code, truncated=truncated)

    return stream


async def test_stream_result_supplies_exit_code_and_truncation():
    result = await async_capture(stream_of(["a"], exit_code=3), max_lines=10)
    assert result.lines == ["a"]
    assert result.exit_code == 3
    assert result.truncated is False


async def test_stream_errors_propagate():
    async def broken(on_raw):
        on_raw("partial")
        raise ConnectionError("socket died")

    with pytest.raises(ConnectionError, match="socket died"):
        await async_capture(broken, max_lines=10)


async def test_reaching_max_lines_cancels_the_stream():
    cancelled = asyncio.Event()

    async def endless(on_raw):
        try:
            i = 0
            while True:
                on_raw(f"[D][t:001]: {i}")
                i += 1
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    result = await async_capture(endless, max_lines=5)
    assert len(result.lines) == 5
    assert result.truncated is True
    assert cancelled.is_set()


async def test_lines_after_max_are_not_collected():
    # A burst delivered synchronously can't overshoot max_lines.
    async def burst(on_raw):
        for i in range(100):
            on_raw(f"[D][t:001]: {i}")
        return CommandResult()

    result = await async_capture(burst, max_lines=3)
    assert len(result.lines) == 3


async def test_preset_stop_event_returns_immediately():
    stop = asyncio.Event()
    stop.set()
    result = await async_capture(stream_of([], forever=True), max_lines=10, stop_event=stop)
    assert result.lines == []
    assert result.truncated is True


async def test_filter_is_applied_and_counts_tracked():
    flt = LogFilter(tags=["race"])
    result = await async_capture(
        stream_of(["[D][wifi:001]: a", "[D][race:001]: b"]), max_lines=10, line_filter=flt
    )
    assert result.lines == ["[D][race:001]: b"]
    assert (flt.lines_received, flt.lines_returned) == (2, 1)


async def test_end_reasons():
    async def short(on_raw):
        return CommandResult(truncated=True)

    async def closes(on_raw):
        return CommandResult()

    assert (await async_capture(short, max_lines=5)).end_reason == "window"
    closed = await async_capture(closes, max_lines=5)
    assert (closed.end_reason, closed.truncated) == ("closed", False)
    full = await async_capture(stream_of(["a", "b"], forever=True), max_lines=2)
    assert (full.end_reason, full.truncated) == ("max_lines", True)
    stop = asyncio.Event()
    stop.set()
    stopped = await async_capture(stream_of([], forever=True), max_lines=5, stop_event=stop)
    assert (stopped.end_reason, stopped.truncated) == ("stopped", True)
