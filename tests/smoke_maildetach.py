# Deleting a mailbox lets go of everything that pointed at it.
#
# WHAT IT USED TO DO. delete_account removed the account and left every
# pointer behind, on purpose: resolve() refuses to fall back to another
# mailbox, so a node naming a dead id reports a missing mailbox rather than
# quietly sending from somebody else's address. That half is right and stays.
#
# The other half was not. A repeating footage export went on firing at a
# mailbox that no longer existed, and every page went on showing its name as
# though it were a choice, so the only way to find out was that the export
# stopped arriving.
#
# WHAT IS PINNED HERE. That the pointer is CLEARED and never repointed -- no
# node is silently re-addressed to a surviving mailbox; that an export which
# also posts to Telegram keeps running with only its email switched off; and
# that a Telegram node, whose `account` names a BOT rather than a mailbox, is
# left completely alone.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import footage_schedule as sched          # noqa: E402
from core.services import mail_accounts as mail              # noqa: E402
from core.services import workflow                           # noqa: E402

CONFIG = tempfile.mkdtemp()
GONE, KEPT = "acc_gone", "acc_kept"

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- a box with two systems, and a workflow store held in memory ----------------
STORES = {}


def graph(nodes):
    return {"version": 3, "workflows": [{"id": "main", "name": "Workflow",
                                         "graph": {"nodes": nodes, "edges": []}}]}


STORES["line"] = graph([
    {"id": "n1", "type": "output.email",
     "data": {"to": "a@b.c", "subject": "s", "account": GONE}},
    {"id": "n2", "type": "output.email",
     "data": {"to": "d@e.f", "subject": "s", "account": KEPT}},
    # A Telegram node's `account` names one of the box's BOTS. It happens to
    # hold the same id here precisely because that is the collision worth
    # proving safe.
    {"id": "n3", "type": "output.telegram",
     "data": {"chat_id": "1", "account": GONE}},
])
STORES["park"] = graph([
    {"id": "n9", "type": "output.email", "data": {"to": "z@z.z", "account": GONE}},
])

mail._slugs = lambda config_dir=None, systems_dir=None: ["line", "park", "empty"]
workflow.load_store = lambda slug: STORES.get(slug)
workflow.save_store = lambda slug, store: STORES.__setitem__(slug, store)

# -- two schedules: one email-only, one that also posts to Telegram -------------
sched.save({"enabled": True, "account": GONE, "send_email": True,
            "send_telegram": False, "to": "a@b.c"}, "line", CONFIG)
sched.save({"enabled": True, "account": GONE, "send_email": True,
            "send_telegram": True, "chat_id": "42", "to": "a@b.c"}, "park", CONFIG)
sched.save({"enabled": True, "account": KEPT, "send_email": True,
            "send_telegram": False, "to": "a@b.c"}, "empty", CONFIG)

out = mail.detach(GONE, CONFIG, None)

# -- the schedules --------------------------------------------------------------
line = sched.load("line", CONFIG)
check("an export naming the deleted mailbox stops emailing",
      line["send_email"] is False and not line["account"], line)
check("...and stops altogether, since email was all it did",
      line["enabled"] is False, line)
check("...but keeps its recipient and period, which nobody asked to lose",
      line["to"] == "a@b.c" and line["every"] == sched.DEFAULTS["every"], line)

park = sched.load("park", CONFIG)
check("an export that ALSO posts to Telegram keeps running",
      park["enabled"] is True and park["send_telegram"] is True, park)
check("...with only the half that cannot work switched off",
      park["send_email"] is False and not park["account"], park)

other = sched.load("empty", CONFIG)
check("an export on a DIFFERENT mailbox is untouched",
      other["account"] == KEPT and other["send_email"] is True, other)

# -- the nodes ------------------------------------------------------------------
nodes = {n["id"]: n for w in STORES["line"]["workflows"]
         for n in w["graph"]["nodes"]}
check("an email node naming it is left with no mailbox",
      nodes["n1"]["data"]["account"] == "", nodes["n1"])
check("...and is NOT repointed at a surviving one, which would send evidence "
      "from the wrong address",
      nodes["n1"]["data"]["account"] != KEPT)
check("...keeping everything else it was configured with",
      nodes["n1"]["data"]["to"] == "a@b.c" and nodes["n1"]["data"]["subject"] == "s")
check("an email node on another mailbox is untouched",
      nodes["n2"]["data"]["account"] == KEPT)
check("a TELEGRAM node holding the same id is left alone -- its account is a "
      "bot, and silencing a chat because a mailbox went is unfindable",
      nodes["n3"]["data"]["account"] == GONE, nodes["n3"])
check("every system is swept, not just the first",
      STORES["park"]["workflows"][0]["graph"]["nodes"][0]["data"]["account"] == "")

# -- and it reports what it did -------------------------------------------------
check("the caller is told which exports were changed",
      sorted(out["schedules"]) == ["line", "park"], out)
check("...and which nodes", sorted(out["nodes"]) == ["line:n1", "park:n9"], out)

# -- a system that cannot be read must not strand the others --------------------
def boom(slug):
    if slug == "line":
        raise OSError("unreadable")
    return STORES.get(slug)


STORES["park"]["workflows"][0]["graph"]["nodes"][0]["data"]["account"] = GONE
workflow.load_store = boom
out2 = mail.detach(GONE, CONFIG, None)
check("one unreadable system does not stop the sweep",
      "park" in [s.split(":")[0] for s in out2["nodes"]], out2)

# -- and an empty id changes nothing at all -------------------------------------
check("deleting nothing detaches nothing",
      mail.detach("", CONFIG, None) == {"schedules": [], "nodes": []})

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
