// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// What the Node Guideline page says, in plain words for someone who has never
// built a workflow. Every node has an entry in NODE_GUIDE (the test
// tests/check_nodeguideline.mjs refuses a node without one, and refuses jargon
// such as code-style names), and the connections worth explaining by hand are in
// LINKS. Whatever a link has no hand-written words for is built from the two
// nodes' own entries here, so it reads the same way.
//
// Keys are node types as the server names them (core/services/workflow.py
// REGISTRY). A link is "<from type>><to type>".
//
// An entry is:
//   inputs   false when nothing can be wired INTO the node (it starts a flow,
//            reads the outside world or names an area); left out otherwise
//   does     what the node is for, in one or two short sentences
//   example  one real situation where you would use it
//   takes    what goes into it, as a noun phrase ("the objects the AI found")
//   gives    what comes out of it, as a noun phrase
//   then     what it does once something is wired in, a verb phrase that
//            finishes "<node> now ..." ("counts them")

export const NODE_GUIDE = {
  "common.inject": {
    inputs: false,
    does: "A button you press to send one value into the workflow by hand. It can also fire by itself when the system starts, or again and again on a timer.",
    example: "Press it to take a test photo, or to try out an alert without waiting for something to happen.",
    takes: "nothing, it starts the flow itself",
    gives: "one value each time it fires (a number, some text, on or off, or the time)",
    then: "waits for you to press it.",
  },
  "source.detection": {
    does: "The starting point. It hands on everything the AI sees in the camera picture: each object it finds, what it is (person, car, panel) and where it is.",
    example: "A camera watches a staircase, and this node passes on every person the AI spots.",
    takes: "the camera picture, read by the AI",
    gives: "the list of objects the AI found, updated on every picture",
    then: "starts reading the camera.",
  },
  "filter.class": {
    does: "Keeps only the kinds of object you choose, such as person or car, and drops the rest.",
    example: "Choose person, and everything after it ignores cars, boxes and anything else.",
    takes: "the objects the AI found",
    gives: "only the kinds of object you chose",
    then: "sees only the kinds of object you chose.",
  },
  "filter.preprocess": {
    does: "Cleans up the camera picture before the AI looks at it, for example by making it black and white or brighter.",
    example: "A dark camera: raise the brightness so the AI can see better.",
    takes: "the camera picture",
    gives: "the improved picture",
    then: "works from the improved picture.",
  },
  "zone.ref": {
    inputs: false,
    does: "Points to an area you drew on the Camera Setup page. Wire it into a node and that node only looks at what is inside the area.",
    example: "Draw a box around a doorway, then count only the people inside that box.",
    takes: "nothing, it names an area you drew",
    gives: "the area to look at",
    then: "names the area to look at.",
  },
  "analytics.object": {
    does: "Tells you whether an object of the chosen kind is there. It gives 1 while something is there and 0 when nothing is.",
    example: "Is there a person in the restricted area? 1 means yes.",
    takes: "the objects the AI found, and optionally an area to watch",
    gives: "1 when something is there, 0 when nothing is",
    then: "checks whether any of them are there.",
  },
  "analytics.anomaly": {
    does: "Judges each photo as GOOD or NG (not good). It says NG when the photo contains any object of the kind you chose, such as a defect.",
    example: "A product photo: if a scratch is found, the photo is NG.",
    takes: "the objects found in photos taken by a Snapshot detection stream",
    gives: "GOOD or NG for the latest photo",
    then: "judges each photo as GOOD or NG.",
  },
  "analytics.counter": {
    does: "Counts objects. With a zone it counts what is inside it, with a line it counts what crosses the line, and with neither it counts everything in view.",
    example: "Count how many people are in the waiting room right now.",
    takes: "the objects the AI found, and optionally a zone or a line",
    gives: "a number: how many",
    then: "counts them.",
  },
  "analytics.dwell": {
    does: "Measures time. It can time how long each object stays in an area.",
    example: "Time how long a worker stays at a machine, and raise an alert after 20 seconds.",
    takes: "the objects the AI found, and optionally a zone",
    gives: "times in seconds, such as how long the longest-staying object has been there",
    then: "starts timing them.",
  },
  "analytics.travel": {
    does: "Measures how long an object takes to go from line A to line B, or how fast it moves between them.",
    example: "How many seconds a forklift takes to go from the gate to the loading dock.",
    takes: "the objects the AI found, and two lines",
    gives: "the trip time and the speed",
    then: "times trips between the two lines.",
  },
  "analytics.proximity": {
    does: "Watches the distance between two kinds of object and warns when they come too close.",
    example: "Warn when a person gets too close to a forklift.",
    takes: "the objects the AI found",
    gives: "how many pairs are too close, and how close they are",
    then: "measures the distance between them.",
  },
  "analytics.guard": {
    does: "Draws a safety bubble around each chosen object and counts it as an intrusion when something else comes inside the bubble.",
    example: "A bubble around each machine, and an alert when a person steps into it.",
    takes: "the objects the AI found",
    gives: "how many intrusions there were, and 1 while one is happening",
    then: "watches for anything entering the bubbles.",
  },
  "analytics.heatmap": {
    does: "Builds a picture that shows where objects spend the most time.",
    example: "See which part of a shop floor gets the most foot traffic.",
    takes: "the objects the AI found, and optionally an area",
    gives: "a map of the busy spots",
    then: "builds the map from them.",
  },
  "analytics.trails": {
    does: "Follows each object and draws a dotted trail of where it has moved on the live picture.",
    example: "See the path a person walked through the room.",
    takes: "the objects the AI found",
    gives: "how many objects are being followed, and how many are moving",
    then: "follows each of them.",
  },
  "analytics.total": {
    does: "Adds things up over time: how many times something happened, or how much a value has grown. It can start again each day.",
    example: "Total the number of alerts today.",
    takes: "a number or an event",
    gives: "a running total",
    then: "adds it to the running total.",
  },
  "analytics.expr": {
    does: "Calculates with your values using a formula, like adding, rounding or finding a percentage.",
    example: "Work out the time over the allowed limit: time minus 20.",
    takes: "one or more numbers",
    gives: "the answer, as a number",
    then: "calculates with it.",
  },
  "variable.schedule": {
    inputs: false,
    does: "Does something at set times: every few minutes, every hour, every day, every week or every month.",
    example: "Send a daily report by email at 8 in the morning.",
    takes: "nothing, the clock starts it",
    gives: "a signal at each set time",
    then: "waits for the set time.",
  },
  "variable.mqtt": {
    inputs: false,
    does: "Listens to an MQTT channel (a topic, which is how many devices share messages) and gives you the number it receives.",
    example: "Read a temperature sent by another device.",
    takes: "numbers published to an MQTT topic",
    gives: "the latest number received",
    then: "reads from the topic.",
  },
  "variable.board_input": {
    inputs: false,
    does: "Reads a switch or sensor wired to the board's input terminals (DI0 or DI1).",
    example: "A foot switch or a light curtain that tells the system a cycle has started.",
    takes: "a wire connected to the board's input terminal",
    gives: "on or off: whether the input is active",
    then: "reads the input.",
  },
  "output.zone_switch": {
    does: "Turns a zone on or off while the workflow is running.",
    example: "Turn a restricted zone off during a break.",
    takes: "a value where 1 means do the switch",
    gives: "nothing, it changes the zone",
    then: "switches the zone.",
  },
  "output.io_out": {
    does: "Switches the board's output terminals (DO0 or DO1) on or off when something happens.",
    example: "Turn on a warning light when an alert fires.",
    takes: "the moment something fires",
    gives: "nothing, it switches a real output",
    then: "switches the output.",
  },
  "output.buzzer": {
    does: "Sounds the board's buzzer when something happens.",
    example: "Beep when someone enters a danger zone.",
    takes: "the moment something fires",
    gives: "nothing, it makes a sound",
    then: "sounds the buzzer.",
  },
  "output.io_pwm": {
    does: "Sets the brightness or strength (from 0 to 255) of the board's L0 or L1 outputs.",
    example: "Dim a light depending on how many people are in the room.",
    takes: "a number",
    gives: "nothing, it sets an output level",
    then: "sets the output level.",
  },
  "variable.rs485": {
    inputs: false,
    does: "Reads values from a Modbus device (a common factory protocol), over the network or the RS485 cable.",
    example: "Read a machine's counter from a PLC.",
    takes: "a Modbus device",
    gives: "the numbers it reads",
    then: "reads from the device.",
  },
  "output.modbus_write": {
    does: "Writes a value to a Modbus device, for example to switch something on a PLC.",
    example: "Tell a PLC to stop the belt when a person is detected.",
    takes: "the moment to write, or a value",
    gives: "nothing, it writes to the device",
    then: "writes to the device.",
  },
  "variable.api": {
    inputs: false,
    does: "Fetches a number from a web address (an HTTP API) every few seconds.",
    example: "Read the outside temperature from a weather service.",
    takes: "a web address",
    gives: "the latest number",
    then: "fetches the value.",
  },
  "logic.delay": {
    does: "Waits for a set time before passing on, so whatever comes after it fires later.",
    example: "Wait 5 seconds after an alert before taking a photo.",
    takes: "the moment something fires",
    gives: "the same moment, but later",
    then: "waits, then carries on.",
  },
  "branch.cases": {
    does: "Checks a value and chooses a way out: if it is over a number, take this way, if not, take another. The first check that is true wins.",
    example: "If the time in the zone is over 20 seconds, raise an alert.",
    takes: "a number to check",
    gives: "a different way out for each check, and one for otherwise",
    then: "checks it and chooses a way out.",
  },
  "branch.multi": {
    does: "Checks several values at once and combines the answers with AND (all must be true) or OR (any one is enough). It has two ways out: pass and fail.",
    example: "Alert only if there is a person AND the machine is running.",
    takes: "several values",
    gives: "pass or fail",
    then: "checks all of them together.",
  },
  "logic.catch": {
    does: "Reacts when another node breaks, so a mistake is noticed instead of quietly showing zero.",
    example: "Send a warning when a formula fails.",
    takes: "a node that can fail",
    gives: "a signal when it fails",
    then: "reacts if it fails.",
  },
  "dashboard.card": {
    does: "Shows one value as a card on your dashboard: a big number, a gauge, or a GOOD or NG verdict.",
    example: "A big number showing how many people were counted today.",
    takes: "a value",
    gives: "nothing, it appears on a dashboard page",
    then: "shows it on a card.",
  },
  "dashboard.graph": {
    does: "Shows a value as a chart, over time or by group.",
    example: "A line chart of violations by day.",
    takes: "a value",
    gives: "nothing, it appears on a dashboard page",
    then: "draws it on a chart.",
  },
  "output.trigger": {
    does: "Marks the moment something becomes true and fires it. Everything you wire after it happens at that moment. It can also raise an alert or colour the objects involved.",
    example: "When the time in the zone goes over the limit: raise an alert, take a photo and write a table row.",
    takes: "a way out of a check (an If / Else if)",
    gives: "the moment, for other nodes to react to",
    then: "raises the moment.",
  },
  "output.mqtt_publish": {
    does: "Sends a value to an MQTT topic when something fires, so other devices can use it.",
    example: "Tell another system that a violation happened.",
    takes: "the moment something fires, and a value",
    gives: "nothing, it sends a message",
    then: "sends it out.",
  },
  "output.webhook": {
    does: "Calls a web address (HTTP) when an alert fires.",
    example: "Notify your own web service.",
    takes: "the moment something fires",
    gives: "nothing, it calls the web address",
    then: "calls the web address.",
  },
  "output.webhook_publish": {
    does: "Sends a value to a web address every few seconds, whether or not an alert fires.",
    example: "Keep a dashboard on another server up to date.",
    takes: "a value",
    gives: "nothing, it sends the value",
    then: "sends it regularly.",
  },
  "output.dataset": {
    does: "Saves a row of information into a database table each time something fires, so you can chart or export it later.",
    example: "Each violation adds a row with the object number and how long it stayed.",
    takes: "the moment something fires, and the values for the columns",
    gives: "a saved row in the database",
    then: "saves a row.",
  },
  "storage.query": {
    does: "Asks your database a question (using SQL, the database language) and gives the answer, to show on a card or send in a report.",
    example: "How many violations happened yesterday?",
    takes: "your database tables",
    gives: "the answer, as a table",
    then: "uses the tables to work out the answer.",
  },
  "storage.csv": {
    does: "Turns a query's rows into a spreadsheet file (CSV) you can attach to an email or a Telegram message.",
    example: "Attach yesterday's violations to the daily report email.",
    takes: "the result of a Query",
    gives: "a CSV file",
    then: "turns the rows into a file.",
  },
  "output.email": {
    does: "Sends an email when something fires.",
    example: "Email the supervisor when a violation happens.",
    takes: "the moment something fires",
    gives: "nothing, it sends an email",
    then: "sends an email.",
  },
  "integration.command": {
    does: "Answers a command typed in Telegram, such as /summary, and can offer buttons that run a query.",
    example: "Type /summary in Telegram to get today's numbers.",
    takes: "a Telegram command",
    gives: "buttons that run your queries",
    then: "answers the command.",
  },
  "integration.photos": {
    does: "Sends back recent photos when someone taps a Telegram button.",
    example: "Tap Photos to see the last 10 violation photos.",
    takes: "a Telegram button",
    gives: "recent photos",
    then: "sends the photos.",
  },
  "integration.watch": {
    does: "Checks a query on a timer and sends a message when its number crosses a limit.",
    example: "Message me when more than 50 violations happen in one day.",
    takes: "a Query",
    gives: "a message when the limit is crossed",
    then: "keeps watching the number.",
  },
  "output.telegram": {
    does: "Sends a Telegram message when something fires.",
    example: "Message the team when a violation happens.",
    takes: "the moment something fires",
    gives: "nothing, it sends a message",
    then: "sends a message.",
  },
  "output.snapshot": {
    does: "Saves a photo of the camera picture (with the boxes drawn on it) when something fires. Give it a name: its photos get their own page in Footage.",
    example: "Take a photo each time someone stays over the time limit.",
    takes: "the moment something fires",
    gives: "a saved photo",
    then: "takes a photo.",
  },
  "output.record": {
    does: "Records a short video clip when something fires, including a few seconds from before it happened. Give it a name: its clips get their own page in Footage.",
    example: "Record 5 seconds before and after a violation.",
    takes: "the moment something fires",
    gives: "a saved video clip",
    then: "records a clip.",
  },
  "math.arith": {
    does: "Adds, subtracts, multiplies or divides the values wired in.",
    example: "The total of two counters.",
    takes: "two or more numbers",
    gives: "the answer",
    then: "does the sum.",
  },
  "math.andor": {
    does: "Combines yes or no answers: AND is yes only if all are yes, OR is yes if any one is, and NOT flips the answer over.",
    example: "Alert only when a person is inside AND the machine is on.",
    takes: "yes or no answers",
    gives: "one yes or no answer",
    then: "combines the answers.",
  },
  "math.setvar": {
    does: "Stores a value in a Variable, so other nodes can read it later.",
    example: "Remember the last object number that was seen.",
    takes: "the variable, and the value to store",
    gives: "the stored value",
    then: "stores the value.",
  },
};

/** The connections worth explaining by hand. `handle` names the port the wire
 *  lands on when it is not the node's first one. */
export const LINKS = {
  "source.detection>filter.class": {
    title: "Pick the kind of object",
    result: "The AI's list of objects is cut down to the kinds you chose on the filter.",
    consequence: "Everything after the filter only sees those kinds of object. Use several filters to make one branch for each kind.",
  },
  "common.inject>source.detection": {
    handle: "snap",
    title: "Ask for a photo by hand",
    result: "Pressing the Inject asks the Snapshot stream to take a photo and look at it.",
    consequence: "The stream only looks when asked, so the camera can rest in between. Set the stream's Capture to Snapshot first.",
  },
  "filter.class>analytics.dwell": {
    title: "Time the chosen objects",
    result: "Only objects of the kinds you chose are timed.",
    consequence: "Wire a Zone Filter in too to time them inside one area. Tick OID (the number the AI gives each object it follows) to give each object its own clock.",
  },
  "filter.class>analytics.object": {
    title: "Watch for the chosen objects",
    result: "Object Detection only looks for the kinds you chose.",
    consequence: "It shows 1 while any of them is in view (or in the zone), and 0 when none is.",
  },
  "filter.class>analytics.counter": {
    title: "Count the chosen objects",
    result: "The counter only counts the kinds you chose.",
    consequence: "Other objects in the picture are never counted.",
  },
  "filter.class>analytics.anomaly": {
    title: "Judge photos by the chosen objects",
    result: "A photo is NG when it contains any object of the kinds you chose.",
    consequence: "Show GOOD or NG on a Verdict card, or use it to decide something.",
  },
  "zone.ref>analytics.dwell": {
    title: "Time inside one area",
    result: "The clock only runs while an object is inside this area.",
    consequence: "Without a zone, an object is timed for as long as it is anywhere in the picture.",
  },
  "analytics.dwell>branch.cases": {
    title: "Check the time against a limit",
    result: "The If / Else if compares the time with the limit you type, for example 20 seconds.",
    consequence: "With OID ticked, each object gets its own clock. Add a Tolerance on the Cycle Time to allow a few extra seconds before it counts as a violation.",
  },
  "analytics.object>branch.cases": {
    title: "Decide when something is found",
    result: "The If / Else if takes its first way out when Found passes your check.",
    consequence: "Wire that way out to a Trigger to raise it.",
  },
  "analytics.counter>branch.cases": {
    title: "Decide on a count",
    result: "The If / Else if compares the count with the limit you type.",
    consequence: "Wire the way out to a Trigger to raise an alert.",
  },
  "branch.cases>output.trigger": {
    title: "Raise the moment",
    result: "The Trigger names the moment the check is true and fires it.",
    consequence: "Everything you wire after the Trigger happens at that moment: photos, table rows, messages and outputs.",
  },
  "output.trigger>output.snapshot": {
    title: "Take a photo",
    result: "A photo is saved when the Trigger fires.",
    consequence: "It appears in Footage under the name you gave the Snapshot. If the Trigger waits for the timer, the photo is shown once the object has left.",
  },
  "output.trigger>output.dataset": {
    title: "Save a row",
    result: "A row is saved into the table when the Trigger fires.",
    consequence: "If the Trigger waits for the timer, the row waits until the object leaves, then holds the total time and how far past the limit it went.",
  },
  "analytics.anomaly>dashboard.card": {
    title: "Show the verdict",
    result: "A Verdict card shows GOOD or NG for the latest photo.",
    consequence: "Drag the card onto the Dashboard or an Analytics page in the Builder.",
  },
};

/** Plain words for the values a node publishes, for the "values you can pick"
 *  line. Anything not listed is shown with its underscores turned into spaces. */
export const VALUE_WORDS = {
  found: "something is there (1) or not (0)",
  count: "how many",
  verdict: "GOOD (0) or NG (1)",
  live_s: "how long the longest-staying object has been there now",
  avg_s: "the average time",
  max_s: "the longest time",
  min_s: "the shortest time",
  last_s: "the latest time",
  value: "the value",
  fired: "1 when it fires",
  runs: "how many times it ran",
  total: "the running total",
  trips: "how many trips",
  hot: "the busiest cells",
  spots: "the busy spots",
  tracked: "how many are followed",
  active: "how many are moving or active",
  idle: "how many are standing still",
  pairs: "how many pairs are close",
  nearest_px: "the closest distance (in pixels)",
  intrusions: "how many intrusions",
  inside_now: "how many are inside now",
  overlap_pct: "how much the bubbles overlap (percent)",
  due: "1 when it is time",
  next_s: "seconds until the next time",
  ok: "1 when it is working",
  messages: "how many messages arrived",
  state: "on or off",
  changes: "how many times it changed",
  held_s: "how long it has been on (seconds)",
  in: "how many came in",
  out: "how many went out",
  net: "in minus out",
  cross: "how many crossed",
  last_speed: "the latest speed",
  avg_speed: "the average speed",
};

export const valueWords = (metric) =>
  VALUE_WORDS[metric] || String(metric).replace(/_/g, " ");

/** First sentence of a description, for a short line. */
export function firstSentence(text) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (!t) return "";
  const m = t.match(/^(.+?[.!?])(\s|$)/);
  return (m ? m[1] : t).slice(0, 220);
}
