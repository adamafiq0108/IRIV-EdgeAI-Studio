// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The main guideline of the Workflow, one flow per template a system can be created
// from. Each flow is the template's own workflow told as a slideshow: what each part
// does in plain words, where to find the node, and where its numbers end up on the
// Dashboard (and Analytics). GuideFlow.jsx draws whichever flow the system's template
// names, and the Blank Canvas flow for a system that has none of its own.
//
// A flow is {label, welcome, zones, tables, nodes, wires, modules, steps, cards,
// cardTitle, chartCards, chartTitle}. `nodes` and `wires` are named once and the steps
// pick the ones they show, so a step draws only the part of the workflow it is about.

const SHOW = (nodes, wires, mark) => ({ nodes, wires, mark });

// ---- The guide: one workflow for every template -----------------------------------
// A restricted-area flow with a log and a chart, split into THREE MODULES that each end
// with something visible: 1 Basic AI System (see and count), 2 Condition & Trigger
// (test the number, raise the violation, keep a state), 3 Database (log it, chart
// it). A module opens with an overview of the nodes it adds, then one slide per node,
// with that node's properties beside the picture (GuideFlow.jsx). Node numbers `n` run
// 1-15 across the whole workflow, so a number means the same node in every module.
// `steps` is the modules' slides in order, each tagged with its module.
const NODES = {
  det:   { id: "det", n: 1, type: "source.detection", x: 0, y: 40 },
  flt:   { id: "flt", n: 2, type: "filter.class", x: 330, y: 0, data: { name: "Class Filter", classes: ["person"] } },
  zone:  { id: "zone", n: 3, type: "zone.ref", x: 330, y: 190, data: { zone: "zone_1" } },
  cnt:   { id: "cnt", n: 4, type: "analytics.counter", x: 660, y: 20, data: { name: "Zone 1 violations" } },
  cardc: { id: "cardc", n: 5, type: "dashboard.card", x: 990, y: -190, data: { name: "People in Zone 1", kind: "tile" } },
  ifx:   { id: "ifx", n: 6, type: "branch.cases", x: 990, y: 20,
           data: { name: "If / Else if", rules: [{ input: "in1", op: ">", value: 0 }] } },
  vara:  { id: "vara", n: 9, type: "variable.ref", x: 990, y: 330, data: { name: "variable", key: "fn_status_value" } },
  setv:  { id: "setv", n: 10, type: "math.setvar", x: 1320, y: 250 },
  varb:  { id: "varb", n: 11, type: "variable.ref", x: 1650, y: 380, data: { name: "status_out", key: "fn_status_out_value" } },
  vio:   { id: "vio", n: 7, type: "output.trigger", x: 1320, y: 20,
           data: { name: "Violate", alert: true, severity: "warn", message: "alert1", draw: true, zone_color: "red" } },
  cardv: { id: "cardv", n: 8, type: "dashboard.card", x: 1650, y: -20, data: { name: "Violation", kind: "tile" } },
  tbl:   { id: "tbl", n: 12, type: "output.dataset", x: 1650, y: 180,
           data: { name: "violation", dataset: "violation", mode: "alert" } },
  logc:  { id: "logc", n: 13, type: "dashboard.card", x: 1980, y: -20, data: { name: "Violation Log", kind: "tile" } },
  chart: { id: "chart", n: 14, type: "dashboard.graph", x: 1980, y: 150, data: { name: "total violation", kind: "area" } },
  totc:  { id: "totc", n: 15, type: "dashboard.card", x: 1980, y: 350, data: { name: "Total Violation", kind: "tile" } },
};

const WIRES = {
  det_flt: { source: "det", target: "flt" },
  flt_cnt: { source: "flt", target: "cnt", targetHandle: "in" },
  zone_cnt: { source: "zone", target: "cnt", targetHandle: "zone" },
  cnt_cardc: { source: "cnt", target: "cardc", sourceHandle: "out" },
  cnt_ifx: { source: "cnt", target: "ifx", sourceHandle: "out", targetHandle: "in1" },
  zone_vio: { source: "zone", target: "vio", targetHandle: "zone" },
  ifx_vio: { source: "ifx", target: "vio", sourceHandle: "out0" },
  vio_cardv: { source: "vio", target: "cardv" },
  vara_setv: { source: "vara", target: "setv", targetHandle: "a" },
  ifx_setv: { source: "ifx", target: "setv", sourceHandle: "out0", targetHandle: "when" },
  setv_varb: { source: "setv", target: "varb" },
  varb_tbl: { source: "varb", target: "tbl", targetHandle: "c_status" },
  cnt_tbl: { source: "cnt", target: "tbl", targetHandle: "c_person_id" },
  tbl_logc: { source: "tbl", target: "logc" },
  tbl_chart: { source: "tbl", target: "chart", sourceHandle: "o_status" },
  tbl_totc: { source: "tbl", target: "totc", sourceHandle: "o_status" },
};

const ALL_NODES = Object.keys(NODES);
const ALL_WIRES = Object.keys(WIRES);

const MODULES = [
  {
    id: "basic", title: "Basic AI System",
    lead: "See the camera, keep the objects you want in the place you want, count them and show the count.",
    steps: [
      { title: "Welcome", where: "Workflow page",
        body: "A workflow tells your system what to do with what the camera sees. This guide is "
          + "split into three short modules. Each one adds a few nodes and ends with something you can "
          + "see. Every node has a number: find the same number on the picture.",
        view: "welcome" },
      { title: "Drag a node onto the canvas", where: "Node list on the left, then the canvas",
        body: "Grab a node in the list on the left, drag it into the canvas and let go. It stays where you drop it.",
        view: "mv-drag" },
      { title: "Open a node's properties", where: "On the canvas, then the right column",
        body: "Double-click a node on the canvas. Its properties open in the right column, where you set it up.",
        view: "mv-dblclick" },
      { title: "Move around the canvas", where: "On an empty part of the canvas",
        body: "Hold the right mouse button on an empty part of the canvas and drag. The whole canvas moves with you.",
        view: "mv-pan" },
      { title: "Zoom in and out", where: "Mouse wheel, or the + and - buttons at the bottom left of the canvas",
        body: "Scroll the mouse wheel over the canvas to zoom in and out, or click the + and - buttons at the bottom left.",
        view: "mv-zoom" },
      { title: "Right-click for more", where: "Right-click a node, a selection or an empty spot",
        body: "Right-click a node to open a menu: Cut, Copy, Duplicate, Disconnect wires, Delete and more. "
          + "Right-click an empty spot to Insert a node or Paste, and use Select all or Export selection from there.",
        view: "mv-menu" },
      { title: "Delete a node", where: "Select the node, then the keyboard or the right-click menu",
        body: "Click a node to select it, then press Delete on the keyboard. Or right-click the node and choose Delete. "
          + "If several nodes are selected, Delete removes all of them.",
        view: "mv-delete" },
      { title: "What this module builds", where: "Workflow page, the canvas",
        body: "The basic AI system: the camera's detections, narrowed to people inside Zone 1, counted, "
          + "and the count shown on a card. Set up AI Models and Camera Setup first.",
        use: ["1 Detection Stream", "2 Class Filter", "3 Zone Filter", "4 Counter", "5 Card"],
        setup: "Nothing to set yet. The next slides add one node at a time.",
        show: SHOW(["det", "flt", "zone", "cnt", "cardc"], ["det_flt", "flt_cnt", "zone_cnt", "cnt_cardc"],
                   ["det", "flt", "zone", "cnt", "cardc"]) },
      { title: "1  What the camera sees", where: "Node list: Input, Source, Detection Stream",
        body: "Every workflow begins with the Detection Stream. It hands on everything the AI finds in "
          + "the camera picture: each person, car or box, and where it is.",
        use: ["1 Detection Stream"],
        setup: "Drag it onto the canvas. No settings: it uses the model and camera from Camera Setup.",
        show: SHOW(["det"], [], ["det"]) },
      { title: "2  Pick the kind of object", where: "AI Models first, then Node list: Input, Filters, Class Filter",
        body: "A Class Filter keeps only the kinds of object you choose, so everything wired after it "
          + "ignores cars, boxes and anything else. Its properties show a tick box for every class: "
          + "but only the classes switched on in AI Models. So first go to the AI Models page, put a "
          + "model in use and, under Classes to keep, switch on the classes you need (for example "
          + "person). Then they appear here to tick.",
        use: ["2 Class Filter"],
        setup: "First, on the AI Models page, switch on the classes you need under Classes to keep. "
          + "Then wire the Detection Stream into the Class Filter, give it a name, and under Object "
          + "classes tick person. Type in the filter box to find a class quickly.",
        tip: "The guide keeps people, but you can tick other classes (car, box, anything the model knows) "
          + "or more than one. Change it to suit your use case.",
        show: SHOW(["det", "flt"], ["det_flt"], ["flt"]) },
      { title: "3  Pick the place to watch",
        where: "Camera Setup draws it, then: Input, Filters, Zone Filter",
        body: "First draw the area on the Camera Setup page. Then add a Zone Filter and choose the "
          + "area. It waits on its own: the Counter uses it.",
        use: ["3 Zone Filter"],
        setup: "In the properties, choose Zone 1 (the area you drew). It needs no input wire.",
        show: SHOW(["det", "flt", "zone"], ["det_flt"], ["zone"]) },
      { title: "4  Count what is inside", where: "Node list: Processing, AI Processing, Counter",
        body: "A Counter counts. Wire the Class Filter into its In point and the Zone Filter into its "
          + "Zone point, and it counts the people inside the area.",
        use: ["4 Counter"],
        setup: "Name it, for example Zone 1 violations. Class Filter into In, Zone Filter into Zone.",
        tip: "Its settings (what it counts, how, and for how long) can be changed in the properties to "
          + "match your own use case.",
        show: SHOW(["det", "flt", "zone", "cnt"], ["det_flt", "flt_cnt", "zone_cnt"], ["cnt"]) },
      { title: "5  Show the count on a card", where: "Node list: Visualization, Cards, Card Widget",
        body: "A Card Widget turns a number into something you can see. Wire the Counter into it and name "
          + "it: that name is what shows on your Dashboard.",
        use: ["5 Card"],
        setup: "Give it a Title, for example People in Zone 1. Set Show as to stat tile for the number, "
          + "and Metric to what it counts.",
        tip: "Want words instead of a number? Set Show as to status: it starts with named states "
          + "(0 NOT DETECTED, 1 DETECTED) that you can rename and recolour, for example 0 No Person "
          + "in grey and 1 Person in green.",
        shots: ["dashboard_card", "dashboard_card_status"],
        shotCaptions: ["Show as: stat tile (a number)", "Show as: status (named states)"],
        show: SHOW(["flt", "zone", "cnt", "cardc"], ["flt_cnt", "zone_cnt", "cnt_cardc"], ["cardc"]) },
    ],
  },
  {
    id: "condition", title: "Condition & Trigger",
    lead: "Test the count, raise the violation when the test holds, and keep a status.",
    steps: [
      { title: "What this module builds", where: "Workflow page, the canvas",
        body: "A condition on the count from module 1: is anybody inside? When the answer is yes, a "
          + "Trigger raises the violation (an alert, red boxes and a red zone), a card shows it, and a "
          + "status variable is changed, ready to be logged in module 3.",
        use: ["6 If / Else if", "7 Violate", "8 Card", "9 Variable", "10 Change Variable", "11 Variable"],
        setup: "Start from the module 1 workflow. The next slides add one node at a time.",
        show: SHOW(["cnt", "zone", "ifx", "vio", "cardv", "vara", "setv", "varb"],
                   ["cnt_ifx", "zone_vio", "ifx_vio", "vio_cardv", "vara_setv", "ifx_setv", "setv_varb"],
                   ["ifx", "vio", "cardv", "vara", "setv", "varb"]) },
      { title: "6  Test the number", where: "Node list: Logic, Branch, If / Else if",
        body: "An If / Else if checks a number and opens one way out when the test holds. Here it "
          + "asks: is the count greater than 0?",
        use: ["6 If / Else if"],
        setup: "Wire the Counter into input1. In the properties set the rule: input1 > 0.",
        tip: "Use any test and number you need, for example input1 > 3, or add more rules.",
        show: SHOW(["flt", "zone", "cnt", "ifx"], ["flt_cnt", "zone_cnt", "cnt_ifx"], ["ifx"]) },
      { title: "7  Raise the violation", where: "Node list: Logic, Branch, Trigger",
        body: "Violate is a Trigger: when the test holds it raises the alert, paints the boxes and paints "
          + "the zone. Wire the first way out of the If / Else if into it.",
        use: ["7 Violate"],
        setup: "Name it Violate. Tick Alert and set the message. Tick paint boxes and paint zone. "
          + "If / Else if way out into it, Zone Filter into Zone.",
        show: SHOW(["flt", "zone", "cnt", "ifx", "vio"],
                   ["flt_cnt", "zone_cnt", "cnt_ifx", "zone_vio", "ifx_vio"], ["vio"]) },
      { title: "8  Show it on a card", where: "Node list: Visualization, Cards, Card Widget",
        body: "A Card Widget shows the violation on your Dashboard. Wire Violate into it and name it. "
          + "Fed by a Trigger, its status reads normal or alert.",
        use: ["8 Card"],
        setup: "Give it the Title Violation. Set Show as to status: 0 shows the normal label, 1 the alert label.",
        show: SHOW(["zone", "vio", "cardv"], ["zone_vio", "vio_cardv"], ["cardv"]) },
      { title: "9  Keep a status in a variable", where: "Node list: Variable, Variable",
        body: "A Variable holds a value that other nodes can read and change, here the status text. "
          + "Add one for the value to change, and one more for the answer to be stored in.",
        use: ["9 Variable", "11 Variable"],
        setup: "Create a text Variable named status, and a second one named status_out.",
        show: SHOW(["vara", "varb"], [], ["vara", "varb"]) },
      { title: "10  Change it when the test holds", where: "Node list: Logic, State, Change Variable",
        body: "Change Variable writes a new value into a variable. Wire the If / Else if into it, "
          + "the status Variable into its Variable point, and the result into the status_out Variable.",
        use: ["10 Change Variable"],
        setup: "Variable point: status. In the properties choose the value to set, for example the text violation.",
        show: SHOW(["ifx", "vara", "setv", "varb"], ["vara_setv", "ifx_setv", "setv_varb"], ["setv"]) },
    ],
  },
  {
    id: "database", title: "Database",
    lead: "Log every violation in a table, then chart it and total it.",
    steps: [
      { title: "What this module builds", where: "Workflow page, the canvas",
        body: "A chart needs history, so each violation is written into a table. The table then feeds "
          + "a log card, a chart and a total.",
        use: ["12 Data Table", "13 Card", "14 Chart", "15 Card"],
        setup: "Start from the module 2 workflow. The next slides add one node at a time.",
        show: SHOW(["cnt", "varb", "tbl", "logc", "chart", "totc"],
                   ["varb_tbl", "cnt_tbl", "tbl_logc", "tbl_chart", "tbl_totc"],
                   ["tbl", "logc", "chart", "totc"]) },
      { title: "12  Log it in a table",
        where: "Database Setup first, then Node list: Storage, Database, your table",
        body: "First make the table on the Database page (the button below walks you through it), then "
          + "drag it onto the canvas.",
        use: ["12 Data Table"],
        setup: "Table violation with columns status (text) and person_id (number). Write a row: when "
          + "something alerts. status_out into status, Counter OID into person_id.",
        show: SHOW(["cnt", "varb", "tbl"], ["varb_tbl", "cnt_tbl"], ["tbl"]),
        dbSetup: true },
      { title: "Find your table in the node list", where: "Node list: Storage, Database, Data tables",
        body: "Open Storage, then Database. Every table you made on the Database page is listed under "
          + "Data tables. Drag yours onto the canvas.",
        use: ["12 Data Table"], setup: "Pick the table violation.",
        view: "dbpalette" },
      { title: "13 to 15  Show the log, chart and total", where: "Node list: Visualization, Cards and Charts",
        body: "The table feeds three more nodes: a Card Widget for the log, a Chart Widget that draws the "
          + "rows over time, and a Card Widget for the total. Name each: the names show on the Dashboard "
          + "and Analytics.",
        use: ["13 Card", "14 Chart", "15 Card"],
        setup: "Wire a table column into each. Name them Violation Log, total violation (Chart, kind area) "
          + "and Total Violation.",
        show: SHOW(["tbl", "logc", "chart", "totc"], ["tbl_logc", "tbl_chart", "tbl_totc"],
                   ["logc", "chart", "totc"]) },
      { title: "Workflow done", where: "Workflow page",
        body: "All three modules are built. Putting the cards and chart on your pages happens on the "
          + "Dashboard and Analytics pages, and each has its own guide: open the page and press "
          + "Guide at the top.",
        use: ["1 to 15 All nodes"],
        setup: "Check that every wire is connected, then go to the Dashboard page.",
        show: SHOW(ALL_NODES, ALL_WIRES, []),
        tall: 340 },
    ],
  },
];

const FLOW = {
  label: "How a workflow is built, in three modules",
  welcome: {
    heading: "Welcome to the Workflow",
    lead: "Turn what the camera sees into alerts, cards and charts, in three short modules. The "
      + "numbers on the picture match the numbers in each step.",
    learn: MODULES.map((m, i) => `${i + 1}  ${m.title}`),
  },
  zones: [{ id: "zone_1", name: "Zone 1", kind: "area" }],
  tables: [{ key: "violation", name: "violation",
             columns: [{ name: "status", type: "text" }, { name: "person_id", type: "int" }] }],
  nodes: NODES,
  wires: WIRES,
  modules: MODULES.map(({ id, title, lead }) => ({ id, title, lead })),
  steps: MODULES.flatMap((m, mi) => m.steps.map((st) => ({ ...st, module: mi }))),
  cards: [
    { origin: "card", kind: "tile", title: "People in Zone 1", node: "guide_people", key: "" },
    { origin: "card", kind: "tile", title: "Violation", node: "guide_violation", key: "" },
    { origin: "card", kind: "tile", title: "Violation Log", node: "guide_violog", key: "" },
    { origin: "card", kind: "tile", title: "Total Violation", node: "guide_violtotal", key: "" },
  ],
  chartCards: [{ origin: "card", kind: "area", title: "total violation", node: "guide_chart", key: "" }],
  cardTitle: "Violation",
  chartTitle: "total violation",
};

export const FLOWS = { blank_canvas: FLOW };

/** One guide for every template: the same workflow, told step by step. */
export const flowFor = (key) => FLOWS[String(key || "")] || FLOW;
