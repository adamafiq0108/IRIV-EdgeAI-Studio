// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The main guideline of the Workflow, one flow per template a system can be created
// from. Each flow is the template's own workflow told as a slideshow: what each part
// does in plain words, where to find the node, and where its numbers end up on the
// Dashboard (and Analytics). GuideFlow.jsx draws whichever flow the system's template
// names, and the Blank Canvas flow for a system that has none of its own.
//
// A flow is {label, welcome, zones, tables, nodes, wires, steps, cards, cardTitle,
// chartCards, chartTitle}. `nodes` and `wires` are named once and the steps pick the
// ones they show, so a step draws only the part of the workflow it is about.

const SHOW = (nodes, wires, mark) => ({ nodes, wires, mark });

// ---- The guide: one workflow for every template -----------------------------------
// A restricted-area flow with a log and a chart. Every node has a number `n`; the picture of a
// step rings the nodes it is about with that number, and the step names the nodes to use
// (`use`) and how each is set up (`setup`).
const FLOW = {
  label: "How a workflow is built: from the camera to the cards and chart",
  welcome: {
    heading: "Welcome to the Workflow",
    lead: "Turn what the camera sees into alerts, cards and charts. The numbers on the picture "
      + "match the numbers in each step.",
    learn: ["Pick what to detect and where", "Count it and test it", "Raise a violation",
            "Remember the state in a variable", "Log it in a table", "Show it as cards and a chart"],
  },
  zones: [{ id: "zone_1", name: "Zone 1", kind: "area" }],
  tables: [{ key: "violation", name: "violation",
             columns: [{ name: "status", type: "text" }, { name: "person_id", type: "int" }] }],
  nodes: {
    det:   { id: "det", n: 1, type: "source.detection", x: 0, y: 40 },
    flt:   { id: "flt", n: 2, type: "filter.class", x: 330, y: 0, data: { name: "Class Filter", classes: ["person"] } },
    zone:  { id: "zone", n: 3, type: "zone.ref", x: 330, y: 190, data: { zone: "zone_1" } },
    cnt:   { id: "cnt", n: 4, type: "analytics.counter", x: 660, y: 20, data: { name: "Zone 1 violations" } },
    ifx:   { id: "ifx", n: 5, type: "branch.cases", x: 990, y: 20,
             data: { name: "If / Else if", rules: [{ input: "in1", op: ">", value: 0 }] } },
    vio:   { id: "vio", n: 6, type: "output.trigger", x: 1320, y: 20,
           data: { name: "Violate", alert: true, severity: "warn", message: "alert1", draw: true, zone_color: "red" } },
    cardv: { id: "cardv", n: 7, type: "dashboard.card", x: 1650, y: -20, data: { name: "Violation", kind: "tile" } },
    vara:  { id: "vara", n: 8, type: "variable.ref", x: 990, y: 330, data: { name: "variable", key: "fn_status_value" } },
    setv:  { id: "setv", n: 9, type: "math.setvar", x: 1320, y: 250 },
    varb:  { id: "varb", n: 10, type: "variable.ref", x: 1650, y: 380, data: { name: "status_out", key: "fn_status_out_value" } },
    tbl:   { id: "tbl", n: 11, type: "output.dataset", x: 1650, y: 180,
             data: { name: "violation", dataset: "violation", mode: "alert" } },
    logc:  { id: "logc", n: 12, type: "dashboard.card", x: 1980, y: -20, data: { name: "Violation Log", kind: "tile" } },
    chart: { id: "chart", n: 13, type: "dashboard.graph", x: 1980, y: 150, data: { name: "total violation", kind: "area" } },
    totc:  { id: "totc", n: 14, type: "dashboard.card", x: 1980, y: 350, data: { name: "Total Violation", kind: "tile" } },
  },
  wires: {
    det_flt: { source: "det", target: "flt" },
    flt_cnt: { source: "flt", target: "cnt", targetHandle: "in" },
    zone_cnt: { source: "zone", target: "cnt", targetHandle: "zone" },
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
  },
  cards: [
    { origin: "card", kind: "tile", title: "Violation", node: "guide_violation", key: "" },
    { origin: "card", kind: "tile", title: "Violation Log", node: "guide_violog", key: "" },
    { origin: "card", kind: "tile", title: "Total Violation", node: "guide_violtotal", key: "" },
  ],
  chartCards: [{ origin: "card", kind: "area", title: "total violation", node: "guide_chart", key: "" }],
  cardTitle: "Violation",
  chartTitle: "total violation",
  steps: [
    { title: "Welcome", where: "Workflow page",
      body: "A workflow tells your system what to do with what the camera sees. This guide builds "
        + "one from the camera to the cards and chart nodes. Every node has a number: "
        + "find the same number on the picture.",
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
    { title: "The whole workflow", where: "Workflow page, the canvas",
      body: "This is the finished workflow, left to right: detect, narrow down, count, test, "
        + "raise a violation, remember it, log it, show it. The next steps build it one number at a time.",
      use: ["1 Detection Stream", "2 Class Filter", "3 Zone Filter", "4 Counter", "5 If / Else if",
            "6 Violate", "7 Card", "8 Variable", "9 Change Variable", "10 Variable", "11 Data Table",
            "12 Card", "13 Chart", "14 Card"],
      setup: "Nothing to set yet. Set up AI Models and Camera Setup first.",
      tall: 340,
      show: SHOW(["det", "flt", "zone", "cnt", "ifx", "vio", "cardv", "vara", "setv", "varb", "tbl",
                  "logc", "chart", "totc"],
                 ["det_flt", "flt_cnt", "zone_cnt", "cnt_ifx", "zone_vio", "ifx_vio", "vio_cardv",
                  "vara_setv", "ifx_setv", "setv_varb", "varb_tbl", "cnt_tbl", "tbl_logc", "tbl_chart", "tbl_totc"],
                 ["det", "flt", "zone", "cnt", "ifx", "vio", "cardv", "vara", "setv", "varb", "tbl",
                  "logc", "chart", "totc"]) },
    { title: "1  What the camera sees", where: "Node list: Input, Source, Detection Stream",
      body: "Every workflow begins with the Detection Stream. It hands on everything the AI finds in "
        + "the camera picture: each person, car or box, and where it is.",
      use: ["1 Detection Stream"],
      setup: "Drag it onto the canvas. No settings: it uses the model and camera from Camera Setup.",
      show: SHOW(["det"], [], ["det"]) },
    { title: "2  Pick the kind of object", where: "Node list: Input, Filters, Class Filter",
      body: "A Class Filter keeps only the kinds of object you choose, so everything wired after it "
        + "ignores cars, boxes and anything else.",
      use: ["2 Class Filter"],
      setup: "Wire the Detection Stream into it. In the properties, tick person.",
      tip: "The guide keeps people, but you can tick other classes (car, box, anything the model knows) "
        + "or more than one. Change it to suit your use case.",
      show: SHOW(["det", "flt"], ["det_flt"], ["flt"]) },
    { title: "3  Pick the place to watch",
      where: "Camera Setup draws it, then: Input, Filters, Zone Filter",
      body: "First draw the area on the Camera Setup page. Then add a Zone Filter and choose the "
        + "area. It waits on its own: the Counter and Violate use it.",
      use: ["3 Zone Filter"],
      setup: "In the properties, choose Zone 1 (the area you drew). It needs no input wire.",
      show: SHOW(["det", "flt", "zone"], ["det_flt"], ["zone"]) },
    { title: "4  Count what is inside", where: "Node list: Processing, AI Processing, Counter",
      body: "A Counter counts. Wire the Class Filter into its In point and the Zone Filter into its "
        + "Zone point, and it counts the people inside the area.",
      use: ["4 Counter"],
      setup: "Name it, for example Zone 1 violations. Class Filter into In, Zone Filter into Zone.",
      tip: "The Counter is an AI Processing node. Its settings (what it counts, how, and for how long) "
        + "can be changed in the properties to match your own use case.",
      show: SHOW(["det", "flt", "zone", "cnt"], ["det_flt", "flt_cnt", "zone_cnt"], ["cnt"]) },
    { title: "5  Test the number", where: "Node list: Logic, Branch, If / Else if",
      body: "An If / Else if checks a number and opens one way out when the test holds. Here it "
        + "asks: is the count greater than 0?",
      use: ["5 If / Else if"],
      setup: "Wire the Counter into input1. In the properties set the rule: input1 > 0.",
      tip: "Use any test and number you need, for example input1 > 3, or add more rules.",
      show: SHOW(["flt", "zone", "cnt", "ifx"], ["flt_cnt", "zone_cnt", "cnt_ifx"], ["ifx"]) },
    { title: "6  Raise the violation", where: "Node list: Logic, Branch, Trigger",
      body: "Violate is a Trigger: when the test holds it raises the alert, paints the boxes and paints "
        + "the zone. Wire the If / Else if's first way out into it.",
      use: ["6 Violate"],
      setup: "Name it Violate. Tick Alert and set the message. Tick paint boxes and paint zone. If / Else if way out into it, Zone Filter into Zone.",
      show: SHOW(["flt", "zone", "cnt", "ifx", "vio"],
                 ["flt_cnt", "zone_cnt", "cnt_ifx", "zone_vio", "ifx_vio"], ["vio"]) },
    { title: "7  Show it on a card", where: "Node list: Visualization, Cards, Card",
      body: "A Card turns the result into something you can see. Wire Violate into it and name it: "
        + "that name is what shows on your Dashboard.",
      use: ["7 Card"],
      setup: "Name it Violation. In the properties set Show as to a number tile.",
      show: SHOW(["flt", "zone", "vio", "cardv"], ["zone_vio", "vio_cardv"], ["cardv"]) },
    { title: "8  Keep a status in a variable", where: "Node list: Variable, Variable",
      body: "A Variable holds a value that other nodes can read and change, here the status text. "
        + "Add one for the value to change, and one more for the answer to be stored in.",
      use: ["8 Variable", "10 Variable"],
      setup: "Create a text Variable named status, and a second one named status_out.",
      show: SHOW(["vara", "varb"], [], ["vara", "varb"]) },
    { title: "9  Change it when the test holds", where: "Node list: Logic, State, Change Variable",
      body: "Change Variable writes a new value into a variable. Wire the If / Else if into it, "
        + "the status Variable into its Variable point, and the result into the status_out Variable.",
      use: ["9 Change Variable"],
      setup: "Variable point: status. In the properties choose the value to set, for example the text violation.",
      show: SHOW(["ifx", "vara", "setv", "varb"], ["vara_setv", "ifx_setv", "setv_varb"], ["setv"]) },
    { title: "10  Log it in a table",
      where: "Database Setup first, then Node list: Storage, Database, your table",
      body: "A chart needs history, so write each violation into a table. First make the table on "
        + "the Database page (the button below walks you through it), then drag it onto the canvas.",
      use: ["11 Data Table"],
      setup: "Table violation with columns status (text) and person_id (number). Write a row: when something alerts. "
        + "status_out into status, Counter OID into person_id.",
      show: SHOW(["cnt", "varb", "tbl"], ["varb_tbl", "cnt_tbl"], ["tbl"]),
      dbSetup: true },
    { title: "Find your table in the node list", where: "Node list: Storage, Database, Data tables",
      body: "Open Storage, then Database. Every table you made on the Database page is listed under "
        + "Data tables. Drag yours onto the canvas.",
      use: ["11 Data Table"], setup: "Pick the table violation.",
      view: "dbpalette" },
    { title: "11  Show the log, chart and total", where: "Node list: Visualization, Cards and Charts",
      body: "The table feeds three more nodes: a Card for the log, a Chart that draws the rows over "
        + "time, and a Card for the total. Name each: the names show on the Dashboard and Analytics.",
      use: ["12 Card", "13 Chart", "14 Card"],
      setup: "Wire a table column into each. Name them Violation Log, total violation (Chart, kind area) "
        + "and Total Violation.",
      show: SHOW(["tbl", "logc", "chart", "totc"], ["tbl_logc", "tbl_chart", "tbl_totc"], ["logc", "chart", "totc"]) },
    { title: "Workflow done", where: "Workflow page",
      body: "The workflow is built. Putting its cards and chart on your pages happens on the "
        + "Dashboard and Analytics pages, and each has its own guide: open the page and press "
        + "Guide at the top.",
      use: ["1 to 14 All nodes"],
      setup: "Check that every wire is connected, then go to the Dashboard page.",
      show: SHOW(["det", "flt", "zone", "cnt", "ifx", "vio", "cardv", "vara", "setv", "varb", "tbl",
                  "logc", "chart", "totc"],
                 ["det_flt", "flt_cnt", "zone_cnt", "cnt_ifx", "zone_vio", "ifx_vio", "vio_cardv",
                  "vara_setv", "ifx_setv", "setv_varb", "varb_tbl", "cnt_tbl", "tbl_logc", "tbl_chart", "tbl_totc"],
                 []),
      tall: 340 },
  ],
};

export const FLOWS = { blank_canvas: FLOW };

/** One guide for every template: the same workflow, told step by step. */
export const flowFor = (key) => FLOWS[String(key || "")] || FLOW;
