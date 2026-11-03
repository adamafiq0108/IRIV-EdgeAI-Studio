# -*- coding: utf-8 -*-
"""
ssd1306.py -- the IRIV board's 128x64 OLED, drawn from Python.

Written against /dev/i2c-1 directly rather than on top of luma.oled or
Adafruit-CircuitPython, and that is a deliberate trade. The panel needs six
primitives -- clear, pixel, text, line, filled rectangle, flush -- and the
device is a documented register interface; a pip dependency on an appliance
that ships offline, updates over OTA and must build a wheelhouse for every
release is a real cost, paid forever, for about four hundred bytes of font.
Everything here is stdlib.

The framebuffer is PAGE-MAJOR because the controller is: one byte is eight
vertically-stacked pixels, a page is eight rows, and the whole 1KB buffer
goes out in a single write. That makes `flush()` cheap enough to redraw the
whole screen per update instead of tracking dirty regions.

`buffer()` hands the same bytes to the UI, so the browser preview is not a
mock-up of what the panel shows -- it is the panel's own framebuffer.
"""

import os
import threading

try:                                    # Linux only, and only Display needs it
    import fcntl
except ImportError:                     # a Windows dev box still draws Canvas
    fcntl = None

#: Linux i2c-dev: set the slave address for subsequent read/write.
_I2C_SLAVE = 0x0703

#: Control byte prefixes. The SSD1306 reads the first byte of every I2C write
#: as "what follows is": 0x00 commands, 0x40 display data.
_CMD = 0x00
_DATA = 0x40

DEFAULT_BUS = 1
DEFAULT_ADDRESS = 0x3C
WIDTH = 128
HEIGHT = 64

#: 5x7 glyphs for ASCII 32..126, column-major, LSB = top pixel. The classic
#: public-domain 5x7 table -- the same shapes the board's factory display
#: uses, so a screen re-created here looks like the one it replaces rather
#: than merely saying the same words.
_FONT = (
    b"\x00\x00\x00\x00\x00"  # (space)
    b"\x00\x00\x5f\x00\x00"  # !
    b"\x00\x07\x00\x07\x00"  # "
    b"\x14\x7f\x14\x7f\x14"  # #
    b"\x24\x2a\x7f\x2a\x12"  # $
    b"\x23\x13\x08\x64\x62"  # %
    b"\x36\x49\x55\x22\x50"  # &
    b"\x00\x05\x03\x00\x00"  # '
    b"\x00\x1c\x22\x41\x00"  # (
    b"\x00\x41\x22\x1c\x00"  # )
    b"\x14\x08\x3e\x08\x14"  # *
    b"\x08\x08\x3e\x08\x08"  # +
    b"\x00\x50\x30\x00\x00"  # ,
    b"\x08\x08\x08\x08\x08"  # -
    b"\x00\x60\x60\x00\x00"  # .
    b"\x20\x10\x08\x04\x02"  # /
    b"\x3e\x51\x49\x45\x3e"  # 0
    b"\x00\x42\x7f\x40\x00"  # 1
    b"\x42\x61\x51\x49\x46"  # 2
    b"\x21\x41\x45\x4b\x31"  # 3
    b"\x18\x14\x12\x7f\x10"  # 4
    b"\x27\x45\x45\x45\x39"  # 5
    b"\x3c\x4a\x49\x49\x30"  # 6
    b"\x01\x71\x09\x05\x03"  # 7
    b"\x36\x49\x49\x49\x36"  # 8
    b"\x06\x49\x49\x29\x1e"  # 9
    b"\x00\x36\x36\x00\x00"  # :
    b"\x00\x56\x36\x00\x00"  # ;
    b"\x08\x14\x22\x41\x00"  # <
    b"\x14\x14\x14\x14\x14"  # =
    b"\x00\x41\x22\x14\x08"  # >
    b"\x02\x01\x51\x09\x06"  # ?
    b"\x32\x49\x79\x41\x3e"  # @
    b"\x7e\x11\x11\x11\x7e"  # A
    b"\x7f\x49\x49\x49\x36"  # B
    b"\x3e\x41\x41\x41\x22"  # C
    b"\x7f\x41\x41\x22\x1c"  # D
    b"\x7f\x49\x49\x49\x41"  # E
    b"\x7f\x09\x09\x09\x01"  # F
    b"\x3e\x41\x49\x49\x7a"  # G
    b"\x7f\x08\x08\x08\x7f"  # H
    b"\x00\x41\x7f\x41\x00"  # I
    b"\x20\x40\x41\x3f\x01"  # J
    b"\x7f\x08\x14\x22\x41"  # K
    b"\x7f\x40\x40\x40\x40"  # L
    b"\x7f\x02\x0c\x02\x7f"  # M
    b"\x7f\x04\x08\x10\x7f"  # N
    b"\x3e\x41\x41\x41\x3e"  # O
    b"\x7f\x09\x09\x09\x06"  # P
    b"\x3e\x41\x51\x21\x5e"  # Q
    b"\x7f\x09\x19\x29\x46"  # R
    b"\x46\x49\x49\x49\x31"  # S
    b"\x01\x01\x7f\x01\x01"  # T
    b"\x3f\x40\x40\x40\x3f"  # U
    b"\x1f\x20\x40\x20\x1f"  # V
    b"\x3f\x40\x38\x40\x3f"  # W
    b"\x63\x14\x08\x14\x63"  # X
    b"\x07\x08\x70\x08\x07"  # Y
    b"\x61\x51\x49\x45\x43"  # Z
    b"\x00\x7f\x41\x41\x00"  # [
    b"\x02\x04\x08\x10\x20"  # backslash
    b"\x00\x41\x41\x7f\x00"  # ]
    b"\x04\x02\x01\x02\x04"  # ^
    b"\x40\x40\x40\x40\x40"  # _
    b"\x00\x01\x02\x04\x00"  # `
    b"\x20\x54\x54\x54\x78"  # a
    b"\x7f\x48\x44\x44\x38"  # b
    b"\x38\x44\x44\x44\x20"  # c
    b"\x38\x44\x44\x48\x7f"  # d
    b"\x38\x54\x54\x54\x18"  # e
    b"\x08\x7e\x09\x01\x02"  # f
    b"\x0c\x52\x52\x52\x3e"  # g
    b"\x7f\x08\x04\x04\x78"  # h
    b"\x00\x44\x7d\x40\x00"  # i
    b"\x20\x40\x44\x3d\x00"  # j
    b"\x7f\x10\x28\x44\x00"  # k
    b"\x00\x41\x7f\x40\x00"  # l
    b"\x7c\x04\x18\x04\x78"  # m
    b"\x7c\x08\x04\x04\x78"  # n
    b"\x38\x44\x44\x44\x38"  # o
    b"\x7c\x14\x14\x14\x08"  # p
    b"\x08\x14\x14\x18\x7c"  # q
    b"\x7c\x08\x04\x04\x08"  # r
    b"\x48\x54\x54\x54\x20"  # s
    b"\x04\x3f\x44\x40\x20"  # t
    b"\x3c\x40\x40\x20\x7c"  # u
    b"\x1c\x20\x40\x20\x1c"  # v
    b"\x3c\x40\x30\x40\x3c"  # w
    b"\x44\x28\x10\x28\x44"  # x
    b"\x0c\x50\x50\x50\x3c"  # y
    b"\x44\x64\x54\x4c\x44"  # z
    b"\x00\x08\x36\x41\x00"  # {
    b"\x00\x00\x7f\x00\x00"  # |
    b"\x00\x41\x36\x08\x00"  # }
    b"\x08\x08\x2a\x1c\x08"  # ~
)

#: Power-on sequence for a 128x64 panel with the usual 0x12 charge-pump
#: wiring. Horizontal addressing mode, so one flush walks the whole buffer.
_INIT = (
    0xAE,                    # display off
    0xD5, 0x80,              # clock divide / oscillator
    0xA8, HEIGHT - 1,        # multiplex ratio
    0xD3, 0x00,              # display offset
    0x40,                    # start line 0
    0x8D, 0x14,              # charge pump on
    0x20, 0x00,              # horizontal addressing mode
    0xA1,                    # segment remap (column 127 -> SEG0)
    0xC8,                    # COM scan descending
    0xDA, 0x12,              # COM pins: alternative, no remap
    0x81, 0xCF,              # contrast
    0xD9, 0xF1,              # pre-charge
    0xDB, 0x40,              # VCOMH deselect
    0xA4,                    # resume from RAM
    0xA6,                    # normal (not inverted)
    0xAF,                    # display on
)


def text_width(text, size=1):
    """Pixels a string occupies, spacing included. 6px per glyph per size."""
    return max(0, len(text) * 6 * max(1, int(size)) - max(1, int(size)))


class Canvas:
    """A 1-bit framebuffer with the handful of primitives the panel needs.

    Separate from the device on purpose: every page can be composed, measured
    and rendered with no hardware present, which is what lets the browser
    preview and the smoke tests draw the real screens on a dev box.
    """

    def __init__(self, width=WIDTH, height=HEIGHT):
        self.width = int(width)
        self.height = int(height)
        self.pages = self.height // 8
        self.buf = bytearray(self.width * self.pages)

    def clear(self, color=0):
        fill = 0xFF if color else 0x00
        for i in range(len(self.buf)):
            self.buf[i] = fill

    def pixel(self, x, y, color=1):
        x = int(x); y = int(y)
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        index = (y // 8) * self.width + x
        bit = 1 << (y % 8)
        if color:
            self.buf[index] |= bit
        else:
            self.buf[index] &= ~bit & 0xFF

    def fill_rect(self, x, y, w, h, color=1):
        for yy in range(int(y), int(y) + int(h)):
            for xx in range(int(x), int(x) + int(w)):
                self.pixel(xx, yy, color)

    def line(self, x0, y0, x1, y1, color=1):
        # Bresenham. Horizontal rules are the only lines the panel actually
        # draws, but a special case for them would be more code than this.
        x0 = int(x0); y0 = int(y0); x1 = int(x1); y1 = int(y1)
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            err2 = 2 * err
            if err2 >= dy:
                err += dy
                x0 += sx
            if err2 <= dx:
                err += dx
                y0 += sy

    def text(self, x, y, string, size=1, color=1):
        """Draw `string` with its TOP-LEFT at (x, y).

        Top-left, not baseline: the flows this replaces address the screen
        that way ("y": 15 is the row the glyph starts on), and keeping the
        same convention means a layout can be copied across unchanged.
        """
        size = max(1, int(size))
        cursor = int(x)
        for char in str(string):
            code = ord(char)
            if code < 32 or code > 126:
                code = ord("?")
            glyph = _FONT[(code - 32) * 5:(code - 32) * 5 + 5]
            for col, bits in enumerate(glyph):
                if not bits:
                    continue
                for row in range(7):
                    if not bits & (1 << row):
                        continue
                    if size == 1:
                        self.pixel(cursor + col, int(y) + row, color)
                    else:
                        self.fill_rect(cursor + col * size, int(y) + row * size,
                                       size, size, color)
            cursor += 6 * size
        return cursor

    def rows(self):
        """The buffer as one string of '0'/'1' per row, top row first.

        For the browser: a page-major byte array is the wrong shape for
        anything that draws in scanlines, and unpacking it here keeps that
        knowledge in the one file that already has it.
        """
        out = []
        for y in range(self.height):
            page = (y // 8) * self.width
            bit = 1 << (y % 8)
            out.append("".join("1" if self.buf[page + x] & bit else "0"
                               for x in range(self.width)))
        return out


class Display:
    """The physical panel. `available` is false when there is no device.

    Never raises on a missing screen: this runs on dev boxes, on bare Pis and
    on IRIV boards whose OLED is not fitted, and a front panel is the last
    thing that should be able to stop a hub from starting.
    """

    def __init__(self, bus=DEFAULT_BUS, address=DEFAULT_ADDRESS,
                 width=WIDTH, height=HEIGHT):
        self.bus = int(bus)
        self.address = int(address)
        self.canvas = Canvas(width, height)
        self.error = ""
        self._fd = None
        self._lock = threading.Lock()

    # -- device ---------------------------------------------------------------
    @property
    def available(self):
        return self._fd is not None

    def open(self):
        """Open the bus and initialise the controller. False if absent."""
        if self._fd is not None:
            return True
        if fcntl is None:
            self.error = "no i2c on this platform"
            return False
        path = "/dev/i2c-%d" % self.bus
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            self.error = "%s: %s" % (path, exc.strerror or exc)
            return False
        try:
            fcntl.ioctl(fd, _I2C_SLAVE, self.address)
            self._fd = fd
            self._command(*_INIT)
        except OSError as exc:
            os.close(fd)
            self._fd = None
            # Almost always "no device at 0x3c" (ENXIO/EREMOTEIO) on a board
            # with no panel fitted, or EBUSY where another program holds it.
            self.error = "0x%02x on %s: %s" % (self.address, path,
                                               exc.strerror or exc)
            return False
        self.error = ""
        return True

    def close(self):
        with self._lock:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None

    def _command(self, *bytes_):
        # One control byte per command byte. Slower than a burst, but the
        # only burst this driver ever needs is the framebuffer itself.
        for value in bytes_:
            os.write(self._fd, bytes((_CMD, value & 0xFF)))

    def flush(self):
        """Push the whole framebuffer. False if the write failed."""
        with self._lock:
            if self._fd is None:
                return False
            try:
                self._command(0x21, 0, self.canvas.width - 1,      # column range
                              0x22, 0, self.canvas.pages - 1)      # page range
                # In chunks: i2c-dev caps a single transfer, and 1KB in one
                # write is refused on some kernels with EINVAL.
                data = bytes(self.canvas.buf)
                for start in range(0, len(data), 512):
                    os.write(self._fd, bytes((_DATA,)) + data[start:start + 512])
            except OSError as exc:
                self.error = str(exc)
                # The panel is gone (unplugged, or the bus wedged). Drop the
                # handle so the next open() re-initialises rather than writing
                # into a dead fd forever.
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
                return False
            return True

    def power(self, on):
        """Blank the panel without forgetting what is on it."""
        with self._lock:
            if self._fd is None:
                return False
            try:
                self._command(0xAF if on else 0xAE)
            except OSError:
                return False
            return True
