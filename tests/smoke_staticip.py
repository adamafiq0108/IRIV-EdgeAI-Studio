# A fixed address can be set, and the form asks for it the way every other
# device on the bench asks for it.
#
# TWO FAULTS, ONE FORM.
#
# The Apply button never worked. core/routes/network.py called
# netconf.set_ipv4(...) and no such function was ever written, so every press
# raised AttributeError and came back as a 500 with a support code on it. A
# board could be given a fixed address only over SSH with nmcli.
#
# And the form asked for a "Prefix" of 24. Nobody hands an installer a sheet
# saying 24; it says 255.255.255.0, which is what the camera, the PLC and the
# switch beside the board all ask for. The three fields are now IP Address,
# Subnet Mask and Default Gateway, the mask is translated on both sides, and
# the address can be tested for a clash before it is taken.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netconf                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the function the route has always called exists -----------------------
check("netconf has the set_ipv4 the route calls",
      callable(getattr(netconf, "set_ipv4", None)),
      "core/routes/network.py has called this since the page was written; "
      "without it every Apply was a 500")
route = read("core", "routes", "network.py")
check("...and the route still calls it",       # in place, or on a thread (L5)
      "netconf.set_ipv4(" in route or "netconf.set_ipv4, " in route)

# -- 2. a mask is the same thing as a prefix ----------------------------------
for mask, prefix in [("255.255.255.0", 24), ("255.255.0.0", 16),
                     ("255.255.240.0", 20), ("255.255.255.252", 30),
                     ("255.0.0.0", 8), ("0.0.0.0", 0), ("255.255.255.255", 32)]:
    check(f"{mask} is /{prefix}", netconf.mask_to_prefix(mask) == prefix,
          netconf.mask_to_prefix(mask))
    check(f"...and /{prefix} is {mask}", netconf.prefix_to_mask(prefix) == mask,
          netconf.prefix_to_mask(prefix))
check("a prefix length is taken as itself, so an older caller still works",
      netconf.mask_to_prefix("24") == 24 and netconf.mask_to_prefix(16) == 16)

# A mask is ones and then zeros. Anything else is a typo, and a typo that
# reached NetworkManager would take the interface down.
for wrong in ("255.0.255.0", "255.255.255.1", "0.0.0.255", "1.2.3.4"):
    try:
        netconf.mask_to_prefix(wrong)
        check(f"{wrong} is refused", False, "it was accepted as a mask")
    except netconf.NetError:
        check(f"{wrong} is refused as a subnet mask", True)
for wrong in ("", "hello", "33", "-1"):
    try:
        netconf.mask_to_prefix(wrong)
        check(f"{wrong!r} is refused", False, "it was accepted")
    except netconf.NetError:
        check(f"{wrong!r} is refused", True)

# -- 3. what set_ipv4 refuses, before it touches the box ----------------------
# Every one of these has to fail on the ARGUMENT, not on nmcli: the operator
# is often reaching this page through the interface being changed, and a
# half-applied profile is a board nobody can reach.
calls = []
netconf._nmcli = lambda *a, **k: calls.append(a) or ""
netconf.devices = lambda: [{"device": "eth0", "connection": "Wired connection 1",
                            "ip": "192.168.1.116",
                            # What the lease handed this interface. On the
                            # network these boards live on that is a pair of
                            # public resolvers, NOT the router.
                            "dns": ["8.8.8.8", "8.8.4.4"]},
                           {"device": "eth9", "connection": "--"}]
netconf.status = lambda: {"ok": True}

for label, kwargs in [
        ("no interface", {"device": ""}),
        ("an interface this box does not have", {"device": "eth7"}),
        ("an interface with no profile", {"device": "eth9"}),
        ("an address that is not one", {"device": "eth0", "dhcp": False,
                                        "ip": "192.168.1.300"}),
        ("no address at all", {"device": "eth0", "dhcp": False, "ip": ""}),
        ("a mask that is not one", {"device": "eth0", "dhcp": False,
                                    "ip": "192.168.1.65", "prefix": "255.0.255.0"}),
        ("a gateway that is not one", {"device": "eth0", "dhcp": False,
                                       "ip": "192.168.1.65", "gateway": "no"}),
        ("a DNS server that is not one", {"device": "eth0", "dhcp": False,
                                          "ip": "192.168.1.65", "dns": "1.1.1.1, nope"})]:
    calls.clear()
    try:
        netconf.set_ipv4(**kwargs)
        check(f"{label} is refused", False, "it was applied")
    except netconf.NetError:
        check(f"{label} is refused, and nothing was written", not calls, calls)

# -- 4. what it writes when the answer is good --------------------------------
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.65",
                 prefix="255.255.255.0", gateway="192.168.1.1")
written = [a for a in calls if a and "modify" in a[0]]
check("a fixed address is written to the PROFILE, not the live device",
      written and "connection" in written[0][0] and "modify" in written[0][0], calls)
args = list(written[0][0])
check("...with the mask translated to a prefix",
      "192.168.1.65/24" in args, args)
check("...and the gateway as given", "192.168.1.1" in args, args)
# The form has no DNS field and a manual profile without one resolves
# nothing, so the resolvers the interface ALREADY had are carried over. They
# came from the lease and work on this network. Pinning an address must not
# change what answers DNS -- guessing the gateway instead broke a board on a
# network whose router does not serve DNS at all.
check("...and the resolvers the interface was already using carried over",
      args[args.index("ipv4.dns") + 1] == "8.8.8.8,8.8.4.4", args)
check("...method manual", args[args.index("ipv4.method") + 1] == "manual", args)
check("...and the profile is brought up so the change takes hold",
      any("up" in a[0] for a in calls), calls)

calls.clear()
netconf.set_ipv4("eth0", dhcp=True)
args = list(next(a for a in calls if a and "modify" in a[0])[0])
check("going back to DHCP clears the address, gateway and DNS with the method",
      args[args.index("ipv4.method") + 1] == "auto"
      and args[args.index("ipv4.addresses") + 1] == ""
      and args[args.index("ipv4.gateway") + 1] == "", args)

# an explicit DNS list still wins over the gateway
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.65", prefix="24",
                 gateway="192.168.1.1", dns="1.1.1.1, 8.8.8.8")
args = list(next(a for a in calls if a and "modify" in a[0])[0])
check("an explicit DNS list is kept as given",
      args[args.index("ipv4.dns") + 1] == "1.1.1.1,8.8.8.8", args)

# ...and the gateway is the LAST resort, for an interface that had no
# resolvers of its own to carry over
netconf.devices = lambda: [{"device": "eth0", "connection": "Wired connection 1",
                            "ip": "192.168.1.116", "dns": []}]
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.65", prefix="24",
                 gateway="192.168.1.1")
args = list(next(a for a in calls if a and "modify" in a[0])[0])
check("an interface with no resolvers of its own falls back to the gateway",
      args[args.index("ipv4.dns") + 1] == "192.168.1.1", args)

# a static address with no gateway is a real answer: a camera VLAN with no
# route off it is a normal thing to wire a board into
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="10.0.0.5", prefix="255.255.255.0")
args = list(next(a for a in calls if a and "modify" in a[0])[0])
check("a fixed address with no gateway and no resolvers leaves DNS empty",
      args[args.index("ipv4.gateway") + 1] == ""
      and args[args.index("ipv4.dns") + 1] == "", args)

# -- 5. the Test button ---------------------------------------------------------
check("the route offers the test the button calls",
      '"/api/hub/network/ipv4/test"' in route and "address_in_use" in route)
try:
    netconf.address_in_use("192.168.1.999")
    check("a test of something that is not an address is refused", False)
except netconf.NetError:
    check("a test of something that is not an address is refused", True)
answer = netconf.address_in_use("192.168.1.116")
check("this box's own address is not reported as a clash",
      answer["in_use"] is False and "already this box's address" in answer["detail"], answer)

# -- 6. the form asks the three questions, in the bench's words -----------------
page = read("frontend", "src", "components", "NetworkPanel.jsx")
# Sentence case since the 2026-10-14 card pass (Tok Aba's screenshot).
for label in ("IP address", "Subnet mask", "Default gateway"):
    check(f"the form asks for {label!r}", f">{label}</" in page or f">{label} (optional)</" in page)
check("...and no longer asks for an IPv4 address or a bare Prefix field",
      ">Prefix</div>" not in page and "IPv4 address</div>" not in page)
# Some sites hand out a mask, some hand out /24. Either can be typed, and each
# box writes the other as soon as what was typed makes sense -- half of
# "255.255." must not rewrite the prefix to something nobody chose.
check("the mask and the prefix are both editable and kept in step",
      "function typeMask(text)" in page and "function typePrefix(text)" in page
      and "onChange={(e) => typeMask(e.target.value)}" in page
      and "onChange={(e) => typePrefix(e.target.value)}" in page)
check("...and neither rewrites the other until what was typed makes sense",
      page.count('if (derived !== "")') == 2)
check("...with the prefix box named for a screen reader, since / is not a label",
      'aria-label="Prefix length"' in page)
# .input carries min-width: min(240px, 100%), so a plain width on the narrow
# box loses and the prefix swallowed the row the mask needed.
check("the mask gets the room and the prefix stays narrow, against .input's own floor",
      'flex: "0 0 62px", width: 62, minWidth: 62' in page
      and page.count('flex: "1 1 auto", minWidth: 128') == 1)
check("the address and the mask are marked required",
      "<Req>IP address</Req>" in page and "<Req>Subnet mask</Req>" in page)
# The gateway is always optional (2026-10-09): blank is a machine-only
# network, and the box decides which port is the uplink (netuplink.auto_role).
check("...and the gateway is optional, with no uplink tick beside it",
      page.count("<Req>") == 2
      and "Default gateway (optional)" in page
      and "This port is the internet uplink" not in page)
check("the mask is sent as a mask and translated on both sides",
      "mask, gateway, dns" in page and "maskToPrefix" in page
      and "def mask_to_prefix" in read("core", "services", "netaddr.py"))
check("the server takes a mask or a prefix, so an older caller still works",
      'body.get("mask") or body.get("prefix")' in route)
check("Apply stays disabled until the address and the mask are both good",
      "const staticReady = ipOk && gwOk && maskOk && !dnsBad && !conflict;" in page)
# Switching DHCP off starts from what the interface is using now: the address
# an operator wants to pin is almost always the one it already has, and
# copying the lease out of the line above by hand is where a typo comes from.
check("switching DHCP off fills the fields from what the interface has",
      "function takeStatic(next)" in page
      and "onCheckedChange={takeStatic}" in page
      and "device.static_ip || device.ip" in page
      and "device.static_gateway || device.gateway" in page)
check("...only the blanks, so anything already typed or saved is kept",
      page.count("setIp((cur) => cur ||") == 1
      and page.count("setGateway((cur) => cur ||") == 1)

check("the Test button asks the box, and its answer is shown under the row",
      '"/api/hub/network/ipv4/test"' in page and "tested.detail" in page)
check("...and editing the address clears the last verdict",
      "setTested(null)" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
