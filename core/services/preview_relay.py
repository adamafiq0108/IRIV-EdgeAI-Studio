# -*- coding: utf-8 -*-
"""preview_relay.py -- the master's end of one MJPEG preview, and how it ends.

/api/detector/preview copies the detection app's multipart stream
(127.0.0.1:<stream_port>/video_feed) into the browser's response, one
blocking read at a time on a threadpool thread. The copying is trivial; what
this module is for is STOPPING. Two things have to happen the moment the
browser goes away, and neither did reliably:

  * the upstream socket to the app has to close, or the app keeps a viewer
    thread encoding frames for nobody (eight of them were found open on the
    app's stream port while a page held six). Closing the HTTPConnection is
    not enough while a read is parked on it: the response's file object
    keeps the descriptor alive, and a quiet upstream (a camera reopening)
    parks that read for up to the 30 s read timeout.
  * the generator has to finish, so the response's thread is returned.

close() therefore shuts the socket down at the kernel (shutdown(), which
wakes a blocked read with EOF or an error at once) before closing the
Python objects, and may be called from ANY thread while chunks() is blocked.
The route hands it to the response as its background task, so it runs when
Starlette notices the disconnect as well as when the stream ends on its own.

Framework-free on purpose (docs/conventions.md sec 2): the route owns the
HTTP status handling; this owns a connection and its two ways of ending.
"""

import http.client
import socket
import threading


class Relay:
    """One open upstream MJPEG response, iterated by chunks(), ended by
    close(). `conn` is the http.client.HTTPConnection, `upstream` the
    HTTPResponse already read to its headers."""

    #: Bytes per upstream read, at most. One JPEG frame is 20-200 KB, so this
    #: is a few dozen reads per frame -- small enough that a close() is
    #: noticed within one chunk, large enough not to be the bottleneck.
    #:
    #: read1(), not read(): HTTPResponse.read(n) on a body with no length is
    #: a BufferedReader read, which BLOCKS until n bytes are in -- so the
    #: tail of every frame (the last < 4 KB) sat in the buffer until the
    #: NEXT frame's bytes pushed it out, a frame of latency on every viewer,
    #: and a quiet upstream parked the relay with a frame half-delivered.
    #: read1() returns whatever has arrived.
    CHUNK = 4096

    def __init__(self, conn, upstream, read_timeout=30):
        self.conn = conn
        self.upstream = upstream
        self.closed = False
        self._lock = threading.Lock()
        # Connected: a stall this long means the app is gone, not busy. (A
        # read pause of seconds is normal while the app reopens a camera.)
        sock = getattr(conn, "sock", None)
        if sock is not None and read_timeout:
            try:
                sock.settimeout(read_timeout)
            except OSError:
                pass

    def chunks(self):
        """Yield the upstream body until it ends, errors, or close() is
        called. Always closes on the way out, whichever way that is."""
        try:
            while not self.closed:
                try:
                    chunk = self.upstream.read1(self.CHUNK)
                except (OSError, ValueError, AttributeError,
                        http.client.HTTPException):
                    # OSError: the app died or close() shut the socket.
                    # ValueError: read on a file close() already closed.
                    # HTTPException: a chunked body cut off mid-chunk
                    # (IncompleteRead) when the app restarts.
                    # AttributeError: close() on another thread raced this
                    # read inside http.client (its fp set to None under us).
                    # Every one of them is "this upstream ended".
                    break
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def close(self):
        """End the relay from any thread. Idempotent."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
        # The kernel first: a thread parked in read() returns immediately.
        sock = getattr(self.conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for obj in (self.upstream, self.conn):
            try:
                obj.close()
            except Exception:                               # noqa: BLE001
                pass
