// The nav's grouping and icons, shared between the shell that DRAWS the rail
// and the pages that point at each other. One copy, because the sidebar, the
// breadcrumb and the "next page" button must all agree on what the order of a
// section is -- three private copies of this list is three ways to disagree.
import {
  LayoutDashboard, BarChart3, Film,
  ScrollText, Boxes,
  Wrench, ScanEye, TableProperties,
  Shapes, Camera, Images, Plug, Workflow as WorkflowIcon, FileClock,
  Database as DatabaseIcon, Cable,
} from "lucide-react";

// Icons keyed by nav page id (core/services/nav.py).
export const PAGE_ICONS = {
  main: LayoutDashboard,
  detection: ScanEye, zones: Shapes, workflow: WorkflowIcon,
  analytics: BarChart3, events: TableProperties,
  database: DatabaseIcon,
  footage: Film, dataset: Camera, dataset_library: Images,
  audit: ScrollText, logs: FileClock,
  builder: Wrench, models: Boxes, blynk: Plug, modbus: Cable,
};

// Which section each page belongs to. A page id not listed here falls into a
// trailing "More" group so a new capability page still appears rather than
// vanishing. Order within a section follows this array.
//
// The order is the setup order: pick a model, aim a camera, draw the zones,
// wire the workflow -- then train on what you captured, then read the results.
// `key` is the section's stable id: it is what an account's per-system
// visibility grant is stored under (core/services/nav.py SECTION_PAGES -
// the two lists are pinned equal by tests/smoke_sections.py), so keys must
// survive a label rewording.
export const SECTIONS = [
  // Analytics sits directly under the Dashboard: they are the two pages an
  // operator READS (now, and over time), so they belong together rather than
  // one at the top and one three sections down.
  // Footage belongs here too: reviewing what was captured is part of reading
  // the system, not part of managing its database.
  { key: "overview", label: "Overview",
    pages: ["main", "analytics", "builder", "footage"] },
  // Zones are a TAB of Camera Setup now -- drawn while looking at that
  // camera's feed, on the same page that aims it.
  { key: "ai_config", label: "AI Configuration",
    pages: ["models", "detection", "workflow"] },
  // Data is the system's OWN tables -- their shape and their rows, in one
  // tool. The event log is not one of those: it is what you read when you
  // are working out why something happened, so it sits in Diagnostics.
  { key: "data", label: "Data", pages: ["database"] },
  { key: "ai_train", label: "AI Preparation", pages: ["dataset", "dataset_library"] },
  { key: "integration", label: "Integration", pages: ["blynk", "modbus"] },
  // Last, because these are the pages you open when something else went wrong.
  { key: "diagnostics", label: "Diagnostics", pages: ["events", "logs"] },
];
