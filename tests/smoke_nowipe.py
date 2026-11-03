# Regression: saving a workflow must not delete zone-action functions that
# have no node on the canvas yet.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

calls = {}


class FakeFns:
    """Stand-in for functions_service: records what apply_artifacts writes."""

    def get_functions(self, systems_dir, slug):
        return [
            {"id": "z_zone_a_a1_count", "type": "zone_count", "name": "count",
             "enabled": True, "params": {"zone": "zone_a"}},
            {"id": "hand_made", "type": "expr", "name": "mine",
             "enabled": True, "params": {"expr": "1"}},
        ]

    def set_functions(self, systems_dir, slug, functions):
        calls["written"] = [f["id"] for f in functions]
        return functions


class FakeDC:
    def get_outputs(self, systems_dir, slug):
        return []

    def set_outputs(self, systems_dir, slug, outs):
        pass

    # Telegram commands are written beside the outputs. Recorded rather than
    # ignored so this test can say the artifact write reached them at all.
    def set_commands(self, systems_dir, slug, commands):
        self.commands = commands

    def set_watches(self, systems_dir, slug, watches):
        self.watches = watches

    # Which rules run with detection off (workflow.logic_artifacts).
    def set_logic(self, systems_dir, slug, frames_free):
        self.logic = frames_free

    # The per-camera picture chain, written beside them.
    def set_preprocess(self, systems_dir, slug, entries):
        self.preprocess = entries

    # Asked before that write, to resolve "the system's first camera".
    def cameras(self, systems_dir, slug):
        return []


class FakeBuilder:
    MAIN_PAGE_ID = "main"
    MAX_WIDGETS = 24

    def load(self, config_dir, slug):
        return {"pages": [{"id": "main", "widgets": []},
                          {"id": "overview", "widgets": []}]}

    def save(self, config_dir, slug, layout):
        pass


wf.functions_service = FakeFns()
wf.dc = FakeDC()
wf.builder_service = FakeBuilder()

compiled = {"functions": [{"id": "wf_occupancy_x1", "type": "zone_count",
                           "name": "n", "enabled": True,
                           "params": {"zone": "zone_a"}}],
            "outputs": [], "pages": []}

wf.apply_artifacts("systems", None, "smoketest2", compiled)
print("written:", calls["written"])
assert "wf_occupancy_x1" in calls["written"], "compiled function missing"
assert "z_zone_a_a1_count" in calls["written"], \
    "REGRESSION: the zone action was wiped by a workflow save"
print("OK: a workflow save keeps zone-action functions")

# And when the canvas DOES own that id (mirrored), it must not be written twice.
compiled2 = {"functions": [{"id": "z_zone_a_a1_count", "type": "zone_count",
                            "name": "n", "enabled": True,
                            "params": {"zone": "zone_a"}}],
             "outputs": [], "pages": []}
wf.apply_artifacts("systems", None, "smoketest2", compiled2)
print("written:", calls["written"])
assert calls["written"].count("z_zone_a_a1_count") == 1, "duplicate id written"
print("OK: a mirrored action is written exactly once")
