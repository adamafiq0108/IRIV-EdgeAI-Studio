# spawn_master() must reap its own child rather than leaking a zombie: a
# fire-and-forget Popen with the handle discarded never gets .wait()'d, so
# every master bounce (power route, counters-reset bounce, boot) grows the
# box's process table by one permanent zombie -- found live on the Pi, one
# per restarted master, sitting since their bounce.
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.services import provision                            # noqa: E402

src = inspect.getsource(provision.spawn_master)
assert "proc = subprocess.Popen" in src, \
    "spawn_master must keep the Popen handle to reap it"
assert "proc.wait" in src, \
    "spawn_master must reap its child (background .wait()), not leak a zombie"
assert "threading.Thread" in src and "daemon=True" in src, \
    "the reaper must not block spawn_master's caller nor the process exit"

# remove_system must stop the detection APP too, before the master: the app
# is its own detached process, and deleting a system without stopping it left
# an orphan holding the camera, a Hailo slot and the stream port -- which the
# NEXT created system inherits, so its app died on Errno 98 at every start
# (found live: a deleted system's app running 40+ minutes after deletion).
rsrc = inspect.getsource(provision.remove_system)
assert "dc.stop_app(systems_dir, slug)" in rsrc, \
    "remove_system must stop the whole app (dc.stop only switches the AI off)"
assert rsrc.index("dc.stop_app") < rsrc.index("stop_master"), \
    "the app stops before the master -- the master is what can reach it"

print("smoke_masterreap OK")
