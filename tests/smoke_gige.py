# Finding a GigE camera means asking the NETWORK, not asking Aravis.
#
# The picker listed whatever `arv-tool` reported, which is only ever the
# cameras Aravis can already open. So the two failures an installer actually
# hits -- the Aravis plugin is not installed, and the camera is on a different
# subnet from this box -- both looked identical to "no camera found", with
# nothing on screen to act on.
#
# GigE Vision has its own discovery: eight bytes of UDP to port 3956, and every
# camera on the wire answers with its address, MAC, vendor, model and serial.
# That answer means the camera is plugged in and powered whatever Aravis
# thinks, and its address is the fact that tells the two failures apart.
import os
import socket
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the command is the protocol's own ----------------------------------------
# magic 0x42, flags 0x11 (answer required + broadcast answer allowed),
# command 0x0002 = DISCOVERY_CMD, length 0, request id 1.
check("the discovery command is eight bytes of GVCP",
      dc._GVCP_DISCOVERY == b"\x42\x11\x00\x02\x00\x00\x00\x01",
      repr(dc._GVCP_DISCOVERY))
check("...sent to the GigE Vision control port", dc.GVCP_PORT == 3956)


def ack(ip="192.168.3.40", mac=b"\x00\x30\x53\x12\x34\x56", vendor=b"Basler",
        model=b"acA1300-30gm", serial=b"21734321", user=b""):
    """A DISCOVERY_ACK exactly as a camera sends one: 8-byte header, then the
    248-byte payload the standard lays out."""
    body = bytearray(248)
    body[dc._GVCP_MAC:dc._GVCP_MAC + 6] = mac
    body[dc._GVCP_IP:dc._GVCP_IP + 4] = socket.inet_aton(ip)
    body[dc._GVCP_VENDOR:dc._GVCP_VENDOR + len(vendor)] = vendor
    body[dc._GVCP_MODEL:dc._GVCP_MODEL + len(model)] = model
    body[dc._GVCP_SERIAL:dc._GVCP_SERIAL + len(serial)] = serial
    body[dc._GVCP_USER_NAME:dc._GVCP_USER_NAME + len(user)] = user
    # header: status 0x0000, command 0x0003 (ACK), length, ack id
    return b"\x00\x00\x00\x03" + struct.pack("!HH", 248, 1) + bytes(body)


# -- a real answer is read the way the standard writes it ----------------------
payload = ack()[8:]
check("the camera's address is read from the answer",
      socket.inet_ntoa(payload[dc._GVCP_IP:dc._GVCP_IP + 4]) == "192.168.3.40")
check("...and its vendor, model and serial",
      (dc._gvcp_text(payload, dc._GVCP_VENDOR, 32),
       dc._gvcp_text(payload, dc._GVCP_MODEL, 32),
       dc._gvcp_text(payload, dc._GVCP_SERIAL, 16))
      == ("Basler", "acA1300-30gm", "21734321"))
# NUL padding is padding, not part of the name.
check("a padded field is trimmed",
      dc._gvcp_text(b"Basler" + b"\x00" * 26, 0, 32) == "Basler")

# -- the whole scan, against a camera that only exists in this test ------------
# A UDP socket on loopback answering the discovery command IS a GigE camera as
# far as this code is concerned, which is the point: no hardware, no Aravis,
# and the parsing is exercised end to end.
import threading                                              # noqa: E402

srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
srv.settimeout(3.0)


def answer():
    try:
        _data, who = srv.recvfrom(2048)
        srv.sendto(ack(ip="127.0.0.9"), who)
    except OSError:
        pass


threading.Thread(target=answer, daemon=True).start()
_real_port, _real_ips = dc.GVCP_PORT, dc._local_ipv4s
dc.GVCP_PORT = port
dc._local_ipv4s = lambda: [("127.0.0.1", "127.0.0.1")]
try:
    cams = dc.scan_gige(timeout=1.5)
finally:
    dc.GVCP_PORT, dc._local_ipv4s = _real_port, _real_ips
    srv.close()

check("the scan finds a camera that answers", len(cams) == 1, str(cams))
if cams:
    cam = cams[0]
    check("...at its address", cam["ip"] == "127.0.0.9", str(cam))
    # Aravis names a device <vendor>-<model>-<serial>: checked against
    # arv-tool on a real camera ("Hikrobot-MV-CS050-60GC-DB0176142"). That is
    # the string aravissrc opens, so a camera found only by this scan can
    # still be selected and will still open.
    check("...with the name Aravis would use for it",
          cam["name"] == "Basler-acA1300-30gm-21734321", str(cam))
    check("...and the local address that heard it, so 'wrong subnet' can be "
          "said rather than guessed", cam["via"] == "127.0.0.1", str(cam))

# -- which of the two failures it is -------------------------------------------
check("a camera on this box's network is reachable",
      dc._same_subnet("192.168.1.10", "192.168.1.40"))
check("...and one on another network is not",
      not dc._same_subnet("192.168.1.10", "192.168.3.40"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
