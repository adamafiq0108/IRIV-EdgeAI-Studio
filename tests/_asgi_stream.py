# -*- coding: utf-8 -*-
"""
_asgi_stream.py -- consume a StreamingResponse the way a server does.

Why this exists: smoke_overlayroute / smoke_hubwalloverlay used to read
`resp.body_iterator` with a plain `for` and call `resp.background.fn()`. Both
are Starlette internals: since Starlette 1.x the body iterator is async-only
and BackgroundTask has no `.fn`, so the tests broke on a dependency bump while
the product was fine. This helper uses only the public contract -- the ASGI
call `await resp(scope, receive, send)` plus `await resp.background()` -- so
it behaves the same on every Starlette, and on the stub the dev box (no
FastAPI) installs, which implements the same two entry points.

Not a test (no smoke_/check_ prefix, run_all never runs it).
"""
import asyncio
import threading


class PageStream:
    """One open page: runs the response on its own event-loop thread, collects
    the body as it is sent, and can 'close the tab' the way a browser does."""

    def __init__(self, resp):
        self.resp = resp
        self.buf = b""
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._gone = asyncio.run_coroutine_threadsafe(self._mk_event(), self._loop).result()
        self._task = asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    async def _mk_event(self):
        return asyncio.Event()

    async def _run(self):
        async def receive():
            await self._gone.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg.get("type") == "http.response.body":
                with self._lock:
                    self.buf += msg.get("body", b"")

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "GET", "path": "/", "headers": []}
        try:
            await self.resp(scope, receive, send)
        except BaseException:            # a hang-up mid-stream may surface as an error
            pass
        finally:
            self.finished.set()

    def body(self):
        with self._lock:
            return self.buf

    def read_until(self, pred, secs=10.0):
        """Poll the collected bytes until pred(bytes) or the deadline."""
        end = _now() + secs
        while _now() < end and not self.finished.is_set():
            if pred(self.body()):
                break
            self.finished.wait(0.02)
        return self.body()

    def drain(self, secs=15.0):
        """Wait for the response to end by itself; return the whole body."""
        self.finished.wait(secs)
        return self.body()

    def background(self):
        """Run the response's background task now (what Starlette runs when
        the response is done), through its public async call."""
        bg = self.resp.background
        if bg is not None:
            asyncio.run_coroutine_threadsafe(bg(), self._loop).result(15)

    def close_page(self, secs=10.0):
        """Leave the page: the cleanup runs (it also unblocks a reader thread
        parked on the upstream), the client 'disconnects', the response ends."""
        self.background()
        self._loop.call_soon_threadsafe(self._gone.set)
        self.finished.wait(secs)


def _now():
    import time
    return time.monotonic()


def stub_streaming_classes(types):
    """For the no-FastAPI stub: StreamingResponse + BackgroundTask offering the
    same two public entry points as Starlette's (ASGI call, awaitable task)."""

    class BackgroundTask:
        def __init__(self, func, *a, **k):
            self.func, self.args, self.kwargs = func, a, k

        async def __call__(self):
            self.func(*self.args, **self.kwargs)

    class StreamingResponse:
        def __init__(self, content, media_type=None, headers=None,
                     background=None, **_k):
            self.body_iterator, self.media_type = content, media_type
            self.headers, self.background = headers or {}, background

        async def __call__(self, scope, receive, send):
            loop = asyncio.get_running_loop()
            it = iter(self.body_iterator)
            end = object()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            while True:
                chunk = await loop.run_in_executor(None, next, it, end)
                if chunk is end:
                    break
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": True})
            await send({"type": "http.response.body", "body": b""})
            if self.background is not None:
                await self.background()
    return StreamingResponse, BackgroundTask
