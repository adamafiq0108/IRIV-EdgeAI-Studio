// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The settings of every workflow node, in plain words, for the Nodes Guideline
// page (pages/NodeGuideline.jsx). Keys are node types, the same ones as
// NODE_GUIDE in guidelineText.js.
//
// A "property" is a field the inspector panel shows when the node is selected
// on the Workflow page, so `name` is the label exactly as the inspector prints
// it. Each node is a list of pages of at most three items, in the order the
// inspector shows them, so the page can step through them like slides.
//
// Written to stay TRUE to the inspector (pages/Workflow.jsx Inspector,
// workflow/TriggerInspector.jsx): a field that only appears in some situations
// says so in its text. Only settings that really are shown are listed. Same
// word rules as guidelineText.js (no code-style names, no jargon).
export const NODE_PROPS = {
  "source.detection": [
    { title: "Capture", items: [
      { name: "Capture", text: "How the camera is read: every frame (Video), or only when something tells it to take a picture (Snapshot). Video is the usual choice." },
      { name: "Camera between snaps", text: "Only shown for Snapshot. Decides whether the camera is closed or kept open between pictures. Leave it on Automatic unless snaps feel slow or the box is busy." },
    ] },
    { title: "Model and camera", items: [
      { name: "Model", text: "Which AI model looks at the picture. All models uses every model loaded in Camera Setup. Only shown when the system has a model to choose from." },
      { name: "Camera", text: "Which camera this branch watches. Every camera runs it on all cameras and adds the results up. Only shown when the system has more than one camera." },
    ] },
  ],

  "filter.class": [
    { title: "Class Filter", items: [
      { name: "On / Off switch", text: "The switch at the top right of the panel. Off pauses the rules after this filter, here and on the dashboard. It is On by default." },
      { name: "Name", text: "How the dashboard lists this filter, so it can be paused there. Needed when the workflow has more than one Class Filter." },
      { name: "Object classes", text: "Tick the kinds of object to keep, such as person or car. Leave all unticked to keep every class." },
    ] },
  ],

  "filter.preprocess": [
    { title: "Steps", items: [
      { name: "Steps", text: "A list of picture adjustments done in order before anything is detected. Add steps (Grayscale, Exposure, Sharpness, Blur, Edges, Mask to a zone), switch each on or off, and move them up or down. Order matters." },
      { name: "Show the processed picture", text: "Tick to see the adjusted picture in the live preview. It only changes what you see, never what is recorded." },
    ] },
    { title: "Mask to a zone step", items: [
      { name: "Zones", text: "The zones the detector is allowed to see. Draw them on the Zones page first." },
      { name: "Invert (keep the outside)", text: "Off keeps only what is inside the zones. On hides what is inside and keeps the rest." },
      { name: "Fill the hidden part with", text: "Black is the cheap choice. Blur avoids a hard edge at the border, but costs much more time per picture." },
    ] },
  ],

  "zone.ref": [
    { title: "Zone", items: [
      { name: "Zone", text: "Which zone this node points at. Draw and edit the zones on the Zones page, then pick one here." },
    ] },
  ],

  "analytics.object": [
    { title: "Basics", items: [
      { name: "Name", text: "The name shown wherever this node's numbers appear: cards, pickers and tables." },
      { name: "OID", text: "A tick box at the top right of the panel. Ticked, the node also gives out the ID of each object it finds, so a Data Table can record who each row was about." },
    ] },
    { title: "Violation timer", items: [
      { name: "Violation timer", text: "Immediately counts an object the moment it is seen. After a time counts it only if it stays. Immediately is the default." },
      { name: "Counts as found once it has stayed for", text: "Shown after choosing After a time. How many seconds an object must stay before it counts. Needs a Video stream, not a Snapshot one." },
    ] },
  ],

  "analytics.anomaly": [
    { title: "Anomaly", items: [
      { name: "Name", text: "The name shown wherever this node's results appear: cards, pickers and tables." },
      { name: "Violation timer", text: "Immediately marks NG the moment something is found. After a time marks it only if it stays. Immediately is the default." },
      { name: "Counts as NG once it has stayed for", text: "Shown after choosing After a time. How many seconds something must stay before it counts as NG. Needs a Video stream." },
    ] },
  ],

  "analytics.counter": [
    { title: "Basics", items: [
      { name: "Name", text: "The name shown wherever this counter's numbers appear: cards, pickers and tables." },
      { name: "Count", text: "Either the number there right now, or a total where each object is counted once. The wording follows what you wired in (a zone, a line, or the whole view)." },
      { name: "OID", text: "A tick box at the top right of the panel. Ticked, the counter also gives out the ID of each object it counts." },
    ] },
    { title: "Advanced: counting", items: [
      { name: "Counting", text: "Real-time counting counts what is there this moment. Minimum stay counts an object only once it has stayed a while." },
      { name: "Counts as 1 once it has stayed for", text: "Shown for Minimum stay. Seconds an object must stay before it counts, so something passing through counts for nothing." },
      { name: "Still counts for", text: "Shown for Minimum stay. Seconds an object keeps counting after it was last seen, so one missed picture does not make the number flicker." },
    ] },
  ],

  "analytics.dwell": [
    { title: "Basics", items: [
      { name: "Name", text: "The name shown wherever this node's times appear: cards, pickers and tables." },
      { name: "What to measure", text: "Time present measures how long objects stay. Countdown gives each arrival a time budget. Occupied clock runs while the place is in use at all." },
      { name: "OID", text: "A tick box at the top right of the panel, shown for Time present. Ticked, the node also records each stay with the object's ID." },
    ] },
    { title: "Countdown", items: [
      { name: "Time budget (s)", text: "Shown for Countdown. The seconds the countdown starts from. The default is 60." },
      { name: "Each arrival uses (s)", text: "Shown for Countdown. How many seconds each new arrival takes off the budget. The default is 1." },
      { name: "Start over", text: "Shown for Countdown. Every day refills the budget at midnight. Never keeps spending it until detection restarts." },
    ] },
    { title: "Advanced", items: [
      { name: "Tolerance (seconds)", text: "Shown for Time present. Extra time past the limit that is still allowed before a test on this time raises a violation. 0 means none." },
      { name: "Per-object time arrives", text: "Shown when OID is ticked. Live gives the running clock of the stay in progress. Once records each finished stay when the object leaves." },
    ] },
  ],

  "analytics.travel": [
    { title: "Basics", items: [
      { name: "Name", text: "The name shown wherever this node's trip times appear: cards, pickers and tables." },
      { name: "Also show speed", text: "Off measures trip times only. On also works out speed, which needs the real distance between the two lines." },
    ] },
    { title: "Speed", items: [
      { name: "Distance between the lines (m)", text: "Shown when speed is on. The real distance on the ground from the entrance line to the exit line, in metres. The default is 10." },
      { name: "Speed unit", text: "Shown when speed is on. Show speed in km/h, mph or m/s. The default is km/h." },
    ] },
  ],

  "analytics.proximity": [
    { title: "Name and classes", items: [
      { name: "Name", text: "The name shown wherever this node's numbers appear: cards, pickers and tables." },
      { name: "Class A", text: "The first kind of object in the pair, such as person. Any class means every kind." },
      { name: "Class B", text: "The second kind of object in the pair, such as forklift. Choosing the same class twice measures nearest neighbours of that class." },
    ] },
    { title: "Models", items: [
      { name: "Model A", text: "Only shown when the system has two or more models. Limits the first object to what one model found. Any model means all of them." },
      { name: "Model B", text: "Only shown when the system has two or more models. Limits the second object to what one model found." },
    ] },
    { title: "Distance and drawing", items: [
      { name: "Too close when nearer than", text: "How near two objects must be to count as too close, as a share of the picture width. The default is 15 percent. It does not change if the camera resolution does." },
      { name: "Draw the link line", text: "Whether a line is drawn between a close pair on the live picture: always, only while alerting, or never." },
    ] },
  ],

  "analytics.guard": [
    { title: "The bubble", items: [
      { name: "Name", text: "The name shown wherever this node's numbers appear: cards, pickers and tables." },
      { name: "Bubble around", text: "Tick the kinds of object that get a bubble around them, such as a forklift. With none ticked nothing is protected." },
      { name: "Bubble size", text: "How far the bubble reaches beyond the object's own box, as a share of that box. The default is 50 percent." },
    ] },
    { title: "Intruders", items: [
      { name: "Intruders: count these entering it", text: "Tick the kinds of object that count as an intruder when they enter a bubble, such as people." },
      { name: "Counts as an intrusion when", text: "Either the intruder's centre is inside the bubble, or the two bubbles touch or overlap. The centre is the default." },
      { name: "How far in before it counts", text: "Only shown for touch or overlap. How deep the overlap must be before it counts. 0 means any touch." },
    ] },
    { title: "Drawing", items: [
      { name: "draw the bubble on the feed", text: "A tick box. Ticked, the bubbles are drawn on the live picture. It is on by default." },
    ] },
  ],

  "analytics.heatmap": [
    { title: "Heatmap", items: [
      { name: "Name", text: "The name shown wherever this heatmap's numbers appear: cards, pickers and tables." },
    ] },
  ],

  "analytics.trails": [
    { title: "Trails", items: [
      { name: "Name", text: "The name shown wherever this node's numbers appear: cards, pickers and tables." },
      { name: "Trail behind", text: "Tick the kinds of object that leave a trail. With none ticked, every class does." },
      { name: "Trail length", text: "How many seconds of movement each trail keeps before it fades. The default is 10." },
    ] },
    { title: "Idle and drawing", items: [
      { name: "Counts as idle after", text: "How many seconds without moving before an object counts as idle, such as a stopped forklift. The default is 5." },
      { name: "draw the dotted trail on the feed", text: "A tick box. Ticked, the dotted trails are drawn on the live picture. It is on by default." },
    ] },
  ],

  "analytics.total": [
    { title: "Counting", items: [
      { name: "Name", text: "The name shown wherever this total appears: cards, pickers and tables." },
      { name: "Accumulate", text: "Count events adds 1 each time the wired value turns on. Increment adds every rise in the value. A drop never takes anything away." },
      { name: "Ignore blips shorter than (s)", text: "A change only counts once it has lasted this many seconds, so a one-frame flicker does not add up. 2 to 3 seconds is a good start. 0 reacts at once." },
    ] },
    { title: "Resetting", items: [
      { name: "Start over", text: "Never keeps adding until detection restarts. Every day resets the total at midnight. Never is the default." },
      { name: "After a restart", text: "Start from zero begins again after a restart. Continue from the last saved total picks up where it left off." },
    ] },
  ],

  "analytics.expr": [
    { title: "Formula", items: [
      { name: "Name", text: "The name shown wherever this formula's answer appears: cards, pickers and tables." },
      { name: "Expression", text: "The calculation itself, written with the names of the values wired in. Use the templates and the buttons for operators if you are not sure how to start." },
    ] },
  ],

  "variable.schedule": [
    { title: "When it runs", items: [
      { name: "Name", text: "The name shown wherever this schedule appears." },
      { name: "Runs", text: "How often it comes round: every few minutes, hourly, daily, weekly or monthly. The other settings follow from this choice." },
      { name: "Time", text: "Shown for daily, weekly and monthly. The time of day it runs, on the box's own clock. The default is 12:00." },
    ] },
    { title: "Minutes and days", items: [
      { name: "Every", text: "Shown for every few minutes. The gap in minutes, counted from midnight, so 15 lands on :00, :15, :30 and :45." },
      { name: "Minutes past the hour", text: "Shown for hourly. Which minute of each hour it runs." },
      { name: "Day", text: "Shown for weekly. Which day of the week it runs." },
    ] },
    { title: "Monthly", items: [
      { name: "Which day", text: "Shown for monthly. Pick a day yourself, or the last day of the month." },
      { name: "Day of the month", text: "Shown when you pick a day yourself. A month too short for it runs on its last day." },
    ] },
    { title: "Advanced", items: [
      { name: "Send it when the box comes back", text: "A tick box in Advanced. Off skips a run the box slept through. On sends it as soon as the box is back, with the values as they are at that moment." },
      { name: "Only between", text: "In Advanced, when it may run. A daily window such as 08:00 to 17:00. Blank means any time. A window across midnight works for night shifts." },
      { name: "Only on", text: "In Advanced, when it may run. Pick the weekdays it counts on. None selected means every day." },
    ] },
    { title: "Date range", items: [
      { name: "Only between the dates", text: "In Advanced, when it may run. Two dates, both included, for something that only matters during a trial or shutdown. Blank means no end." },
    ] },
  ],

  "variable.mqtt": [
    { title: "Connection and topic", items: [
      { name: "Name", text: "The name shown wherever this value appears: cards, pickers and tables." },
      { name: "Connection", text: "The MQTT broker this listens to. Set it up once and share it between nodes. With none picked it uses this box's own broker." },
      { name: "Topic", text: "The topic to listen to. Wildcards work: a plus sign matches one level and a hash sign matches everything under it." },
    ] },
    { title: "Message", items: [
      { name: "Value in the message", text: "Which field inside the message holds the number, for example data.count. Leave blank if the whole message is the number." },
    ] },
  ],

  "variable.board_input": [
    { title: "Board Input", items: [
      { name: "Name", text: "The name shown wherever this input's value appears: cards, pickers and tables." },
      { name: "Port", text: "Which input terminal on the board to read. Each option shows what it reads right now, so pick the one that is wired." },
      { name: "Contact is normally closed (invert)", text: "A tick box. Tick it if the wired contact is closed when idle, so that closing counts as off and opening as on." },
    ] },
  ],

  "output.zone_switch": [
    { title: "Zone Switch", items: [
      { name: "Zone to switch", text: "The zone this node turns on or off while the system runs. The zone's own saved setting returns after a restart." },
      { name: "Which value decides", text: "Only shown when the wired node offers several values. Which of them decides whether the zone is switched." },
      { name: "When the wired value is 1", text: "Either enable the zone, or disable it. The opposite happens when the value is 0." },
    ] },
  ],

  "output.io_out": [
    { title: "Output", items: [
      { name: "Port", text: "Which output terminal on the board this drives. Each option shows whether it is ready or already in use." },
      { name: "When this fires", text: "Turn it on and leave it on, turn it off, pulse it, or toggle it. A latched On stays put until another node turns it off." },
      { name: "Pulse length", text: "Only shown for pulse. How many milliseconds it stays on before it releases. The default is 500." },
    ] },
    { title: "Wiring", items: [
      { name: "Active high (uncheck for a relay that closes on 0)", text: "A tick box. Leave it ticked for most relays. Untick it for a relay that closes when the signal is low." },
    ] },
  ],

  "output.buzzer": [
    { title: "Buzzer", items: [
      { name: "When this fires", text: "Turn the buzzer on, turn it off, pulse it, or toggle it. Pulse is the sensible choice, because a buzzer left on is an alarm nobody can silence." },
      { name: "Pulse length", text: "Only shown for pulse. How many milliseconds the buzzer sounds. The default is 500." },
    ] },
  ],

  "output.io_pwm": [
    { title: "Port and level", items: [
      { name: "Port", text: "Which board terminal gets the varying signal." },
      { name: "Level comes from", text: "A fixed level you type, or the value wired into this node." },
      { name: "Level", text: "Only shown for a fixed level. From 0 to 255: 0 is off, 128 is half and 255 is full." },
    ] },
    { title: "Following a value", items: [
      { name: "Which value", text: "Only shown when following a wired value that offers several. Which of them sets the level." },
      { name: "Value range", text: "Only shown when following a wired value. What counts as 0 and what counts as full, so a value that only reaches 12 does not look like a broken lamp." },
    ] },
    { title: "Timing", items: [
      { name: "Update", text: "Change the level only when the rule fires, or continuously as the value moves." },
      { name: "Frequency", text: "How fast the signal switches, in hertz. 1000 suits a lamp. It is not accurate enough for a servo." },
    ] },
  ],

  "variable.rs485": [
    { title: "Name and device", items: [
      { name: "Name", text: "The name shown wherever this reading appears: cards, pickers and tables." },
      { name: "Server", text: "The Modbus Server this reads from, over a network cable or a serial port. Several nodes can share one." },
      { name: "FC", text: "Which kind of table to read, taken from the device's datasheet: coils, inputs, holding registers or input registers." },
    ] },
    { title: "Where to read", items: [
      { name: "Unit-Id", text: "The device's address on the line. Blank uses the server's own address." },
      { name: "Address", text: "Where in the device to start reading. A datasheet's 40001 usually means 0 here." },
      { name: "Quantity", text: "How many registers to read, counting up from the address. The default is 1." },
    ] },
    { title: "Timing", items: [
      { name: "How often to read", text: "Seconds between reads. 2 is the lowest. 0 reads only when something wired into its Trigger port fires. The default is 5." },
    ] },
  ],

  "output.modbus_write": [
    { title: "Name and device", items: [
      { name: "Name", text: "A label for this node." },
      { name: "Unit-Id", text: "The device's address on the line. Blank uses the server's own address." },
      { name: "FC", text: "How to write: a single coil, a single register, several coils or several registers. Check the device's datasheet." },
    ] },
    { title: "Where to write", items: [
      { name: "Address", text: "Where in the device to write. Required." },
      { name: "Quantity", text: "For writing several coils or registers: how many, counting up from the address. Required." },
      { name: "Delay to activate input", text: "A tick box. Ticked, anything arriving in the first seconds after the app starts is ignored, so a restart never writes an old value onto a machine." },
    ] },
    { title: "Start delay and server", items: [
      { name: "Start delay (s)", text: "Only shown when the delay is ticked. How many seconds to ignore after the app starts. The default is 5." },
      { name: "Server", text: "The Modbus Server this writes through. Set it up once and share it between nodes." },
    ] },
  ],

  "variable.api": [
    { title: "Where to ask", items: [
      { name: "Name", text: "The name shown wherever this value appears: cards, pickers and tables." },
      { name: "URL", text: "The web address to fetch the value from." },
      { name: "How often to read", text: "Seconds between fetches. 2 is the lowest. The default is 5." },
    ] },
    { title: "Reading the answer", items: [
      { name: "Path to the value", text: "Which value in the answer to use, for example data.count. Leave blank if the answer is the number itself." },
      { name: "Fallback", text: "The number used until the first successful read. The default is 0." },
      { name: "Headers", text: "Extra lines sent with every request, such as a key the service asks for." },
    ] },
    { title: "Sign-in", items: [
      { name: "Bearer token", text: "A secret token sent to prove who you are. It replaces any sign-in header you added by hand." },
    ] },
  ],

  "logic.delay": [
    { title: "Delay", items: [
      { name: "Wait for", text: "How many seconds the nodes after this one wait before they fire. Two delays in a row add up. The default is 5." },
    ] },
  ],

  "branch.cases": [
    { title: "Conditions", items: [
      { name: "Conditions", text: "One row per test, each reading its own input. The first row that holds wins, like if, else if. Each row gets its own way out on the node." },
      { name: "Add an ELSE output for when none of them holds", text: "A tick box. Ticked, an extra way out fires when none of the conditions holds. It is on by default." },
    ] },
  ],

  "branch.multi": [
    { title: "Conditions", items: [
      { name: "Passes when", text: "ALL means every condition must hold. ANY means one is enough." },
      { name: "Conditions", text: "One row per test, each reading its own input. The combined answer sends the node out by Pass or Fail." },
      { name: "Add an output for when the test fails", text: "A tick box. Ticked, the Fail way out is there. It is on by default." },
    ] },
  ],

  "logic.catch": [
    { title: "Catch", items: [
      { name: "Raise as", text: "How serious the event is: a note, an alert or a violation." },
      { name: "Message (optional)", text: "The text shown when this fires. Leave blank for the default wording." },
      { name: "Cooldown (s)", text: "The shortest time between two events, so one fault does not flood you. The default is 60." },
    ] },
  ],

  "dashboard.card": [
    { title: "Title and shape", items: [
      { name: "Title", text: "The name shown on the card. Also lists the card in the dashboard editors so it can be placed on another page." },
      { name: "Caption", text: "Only shown for a single-number tile. The small line under the value. Blank shows nothing." },
      { name: "Show as", text: "The shape of the card: a tile, a gauge, a status or a table. Only the shapes that suit what feeds the card are offered." },
    ] },
    { title: "What it shows", items: [
      { name: "Source", text: "Only shown when nothing wired in decides it. Take the value the rule publishes, the events it raised, or a data table." },
      { name: "Metric", text: "Only shown when the wired node publishes several values. Which of them the card shows." },
      { name: "Date range", text: "Which period the card covers: the live number, or the recorded period of your choice. Following the Analytics date picker lets that page decide." },
    ] },
    { title: "Value display", items: [
      { name: "Show the value as", text: "Only for a tile showing a wired value. Turn a 0 or 1 into words such as YES / NO, or show the number or the text as it is." },
      { name: "Unit label", text: "A short unit printed beside the value, such as kg or people. Only shown for shapes that print one." },
    ] },
  ],

  "dashboard.graph": [
    { title: "Title and shape", items: [
      { name: "Title", text: "The name shown on the chart. Also lists the chart in the dashboard editors." },
      { name: "Show as", text: "Another chart of the same family, such as an area or a line. Every setting carries over. Use Change chart type for a different family." },
      { name: "Source", text: "Only shown when nothing wired in decides it. Draw the live value, the events a rule raised, or a data table." },
    ] },
    { title: "Which value", items: [
      { name: "Metric", text: "Only shown when the wired node publishes several values. Which of them the chart draws." },
    ] },
  ],

  "common.inject": [
    { title: "Name and payload", items: [
      { name: "Name", text: "The name shown wherever this node appears." },
      { name: "Payload", text: "What each press puts on the wire: a number, true or false, text, JSON, or the time it fired. A number or true or false can be tested and calculated with." },
      { name: "Topic", text: "Optional. A label that travels beside the payload." },
    ] },
    { title: "When it fires", items: [
      { name: "Inject once after", text: "A tick box. Ticked, it fires once this many seconds after the app starts. The default is 0.1." },
      { name: "Trigger", text: "Manual means you click the node. Automatic fires by itself on a repeat, and the button becomes Start and Stop." },
      { name: "every", text: "Only shown for automatic. How long between firings, as a number and seconds, minutes or hours." },
    ] },
    { title: "More", items: [
      { name: "Start repeating when the app starts", text: "Only shown for automatic. Off waits for you to press Start on the node. It is on by default." },
      { name: "Frames per snapshot", text: "Only shown when wired into a Snapshot Detection Stream. How many camera frames it detects each time this fires. The default is 1." },
    ] },
  ],

  "output.trigger": [
    { title: "Name and firing", items: [
      { name: "Output name", text: "A name for this moment, shown in the workflow and in alerts. Required." },
      { name: "Firing", text: "How often it may fire: every time, at most once every N seconds, once for each object ID, or when a new object ID appears." },
      { name: "Seconds between two fires", text: "Only shown for at most once every N seconds. The shortest gap, from 1 to 86400 seconds. It applies to the alert and the payload alike." },
    ] },
    { title: "Raise an alert", items: [
      { name: "Raise an alert", text: "A tick box. Ticked, firing also raises an alert." },
      { name: "Severity", text: "Only shown when raising an alert. How serious it is: a note, an alert or a violation." },
      { name: "Message (optional)", text: "Only shown when raising an alert. The text of the alert. Blank falls back to the output name." },
    ] },
    { title: "Alert timing and boxes", items: [
      { name: "Raise again every (s)", text: "Only shown when raising an alert and firing every time. How often the alert repeats while the moment keeps holding. The default is 30." },
      { name: "Highlight objects", text: "A tick box. Ticked, the objects of the rule are drawn in a colour while it holds." },
      { name: "Bounding box colour", text: "Only shown when highlighting objects. The colour of their boxes. Red is the default." },
    ] },
    { title: "Highlights", items: [
      { name: "Only boxes inside the zone", text: "Only shown when a zone is wired in. Ticked, only objects inside it take the colour." },
      { name: "Highlight the zone", text: "A tick box. Ticked, the zone itself changes colour while the rule holds." },
      { name: "Zone colour", text: "Only shown when highlighting the zone. The colour the zone turns while the rule holds." },
    ] },
    { title: "Payload", items: [
      { name: "Payload when triggered", text: "A tick box that lets you choose what is sent when it fires. Otherwise it sends 1." },
      { name: "Payload when not triggered", text: "A tick box that lets you choose what is sent when it is not triggered. Otherwise it sends 0." },
      { name: "Output behavior", text: "Follow the condition live, re-check every N seconds, or stay active for N seconds and then turn off. A box appears for the seconds when needed." },
    ] },
    { title: "Advanced", items: [
      { name: "Skip an object the table already holds", text: "In Advanced. Ticked, an object already saved in your Data Table is not sent again. Wire the table's object ID column into the Current OID port." },
      { name: "Total difference threshold", text: "Only shown with the skip option. How much the value must change before the same object is sent again. 0 means any change counts." },
      { name: "Wait for the timer to end", text: "A tick box. The alert fires now, but the table row and the photo after it are finished once the object has left. Only works behind a timer." },
    ] },
  ],

  "output.mqtt_publish": [
    { title: "Where and what", items: [
      { name: "Connection", text: "The MQTT broker this publishes to. Set it up once and share it between nodes. With none picked it uses this box's own broker." },
      { name: "Topic", text: "The exact topic to publish to. No wildcards." },
      { name: "Which value", text: "Only shown when the wired node offers several values. Which one is sent." },
    ] },
    { title: "Keeping the last value", items: [
      { name: "retain (late subscribers get the last value)", text: "A tick box. Ticked, anyone who connects later still receives the most recent message." },
    ] },
  ],

  "output.webhook": [
    { title: "Request", items: [
      { name: "URL", text: "The web address to send the request to." },
      { name: "Method", text: "POST sends the payload. GET just asks the address. POST is the default." },
      { name: "Payload (JSON)", text: "The data sent with the request, written as JSON." },
    ] },
    { title: "Extras", items: [
      { name: "Headers", text: "Extra lines sent with every request, such as a key the service asks for." },
      { name: "Bearer token", text: "A secret token sent to prove who you are. It replaces any sign-in header you added by hand." },
    ] },
  ],

  "output.webhook_publish": [
    { title: "Request", items: [
      { name: "URL", text: "The web address to send the value to." },
      { name: "Method", text: "POST or GET. POST is the default." },
      { name: "Which value", text: "Only shown when the wired node offers several values. Which one is sent." },
    ] },
    { title: "Timing and body", items: [
      { name: "Every", text: "Seconds between requests. It sends the current number on this clock whether or not anything is wrong. The default is 5." },
      { name: "Body", text: "Just the number, or JSON with the name, the value and the time." },
      { name: "Headers", text: "Extra lines sent with every request, such as a key the service asks for." },
    ] },
    { title: "Sign-in", items: [
      { name: "Bearer token", text: "A secret token sent to prove who you are. It replaces any sign-in header you added by hand." },
    ] },
  ],

  "output.dataset": [
    { title: "Table and mode", items: [
      { name: "Table", text: "Which table to fill in. Tables are designed on the Database page, and this node cannot create one." },
      { name: "Operation", text: "Insert records rows. Select writes nothing and only reads columns back out into cards." },
      { name: "Write a row", text: "When a row is written: when something alerts, each time something crosses a line, every few seconds, or when a value changes." },
    ] },
    { title: "Timing and repeats", items: [
      { name: "Every", text: "Only shown for every few seconds. Seconds between rows. 5 is the lowest. The default is 60." },
      { name: "No faster than", text: "Only shown for when a value changes. The shortest gap between rows about the same object, to calm a jittery value. 0 writes every change." },
      { name: "When the same object is seen again", text: "Add a new row keeps every sighting. Update the existing row keeps one row per object. It needs a column that records the object." },
    ] },
    { title: "Groups", items: [
      { name: "Object IDs in a row", text: "Only shown when a column records the object. One row per object, or one row for the whole group with the IDs listed together." },
    ] },
  ],

  "storage.query": [
    { title: "Query", items: [
      { name: "Name", text: "A required name, so the Dashboard and Analytics editors can list what this query publishes." },
      { name: "Statement", text: "The question you ask the tables, written as a SELECT. Read the tables you wired in by the names the Database page shows." },
      { name: "Period", text: "Which rows the statement sees: all time, or follow the Analytics range, today, this week or this month. The statement must use the period placeholders for it to take effect." },
    ] },
  ],

  "storage.csv": [
    { title: "CSV file", items: [
      { name: "File name", text: "What the attachment is called. Blank borrows the name of the Query wired in." },
      { name: "Rows to include", text: "All rows from the Query, or only the first few, to keep a message small." },
      { name: "Most rows to include", text: "Only shown when limiting rows. The most rows the file holds, counted from the top." },
    ] },
  ],

  "output.email": [
    { title: "Message", items: [
      { name: "To", text: "The people who receive it, one address per row, up to ten." },
      { name: "Subject", text: "The subject line. You can place values inside it using the placeholders." },
      { name: "Message", text: "The body of the email. Buttons under the box insert values from what is wired in." },
    ] },
    { title: "Sender", items: [
      { name: "Send from", text: "Which mailbox it goes through. Mailboxes are set up once under Settings, Email. Leave on Default mailbox for the usual one." },
    ] },
  ],

  "integration.command": [
    { title: "Command", items: [
      { name: "Command", text: "The word people type after a slash in Telegram, such as summary. Letters, numbers and underscores only." },
      { name: "Menu heading", text: "Shown above the buttons when someone sends the command. Blank uses the command word." },
      { name: "Note under the heading (optional)", text: "One line of context, such as what the report covers." },
    ] },
    { title: "Buttons", items: [
      { name: "Buttons", text: "One row per button: its label, the period it covers, and whether the answer is a table, a bar, a line or a pie chart. Each button gets a port that a Query is wired into." },
    ] },
  ],

  "integration.photos": [
    { title: "Snapshots", items: [
      { name: "Camera", text: "Which camera's pictures to send. Blank sends whatever any camera saved in the period." },
      { name: "How many to send", text: "All of them (up to 60), or a number from 1 to 60. When there are more, the newest are sent. The default is 10." },
    ] },
  ],

  "integration.watch": [
    { title: "What to watch", items: [
      { name: "Watch name", text: "What this watch is called in the message and the log." },
      { name: "Check every", text: "How often the Query runs, in minutes. The default is 15. It checks on a timer and does not react live." },
      { name: "Over which period", text: "Which stretch of time the Query looks at, from the last hour to all time. Today is the default." },
    ] },
    { title: "When to send", items: [
      { name: "Send when", text: "A comparison against the first number the Query returns, such as greater than 10." },
      { name: "Then stay quiet for", text: "After sending, say nothing more for this many minutes, even if it crosses again. The default is 60." },
    ] },
  ],

  "output.telegram": [
    { title: "Who hears it", items: [
      { name: "Posts as", text: "Shown for information. The bot assigned to this system under Settings, Telegram. One system uses one bot." },
      { name: "Send to", text: "All linked people, only the people you tick, or one chat ID for advanced use. People follow the list under Settings, Telegram." },
      { name: "Chat ID", text: "Only shown for a chat ID. The number of the person, group or channel. A person must message the bot once first." },
    ] },
    { title: "Message", items: [
      { name: "Message", text: "The text sent. Buttons under the box insert values from what is wired in." },
    ] },
  ],

  "output.snapshot": [
    { title: "Camera and moment", items: [
      { name: "Camera", text: "Only shown with more than one camera. Which camera it saves from. Follow the rule uses the camera of the rule that fires it." },
      { name: "Take a photo", text: "Once per violation, once per object, every N seconds, or every alert. Once per violation is the default." },
      { name: "Interval (s)", text: "Shown for every N seconds, where it is the gap between photos. For once per violation the same box is called Quiet gap that ends the episode (s)." },
    ] },
    { title: "Picture options", items: [
      { name: "Tag with object ID (when available)", text: "A tick box. Stamps the object's ID in the corner of the picture and into its file name, when the rule can say which object it was." },
      { name: "Crop the violation", text: "A tick box. Saves a close-up of the objects that broke the rule beside the full photo. It costs a little disk space." },
      { name: "Page name", text: "The Footage page this node's photos appear on, apart from other Snapshots. Give it a name." },
    ] },
    { title: "PDF notice", items: [
      { name: "Notice title", text: "The red heading of the PDF notice. Blank prints VIOLATION NOTICE." },
      { name: "What was detected", text: "Printed under Observation on the notice. Blank prints the detected class." },
      { name: "Location", text: "Printed as the place of the violation. Blank prints nothing." },
    ] },
  ],

  "output.record": [
    { title: "Clip length", items: [
      { name: "Include after the moment (s, max 5)", text: "How many seconds of video to keep after the moment, up to 5." },
      { name: "Include before the moment (s, max 5)", text: "How many seconds of run-up to keep, up to 5. Above 0 the camera keeps a short buffer in memory all the time. 0 turns that off." },
      { name: "Page name", text: "The Footage page this node's clips appear on, apart from other Record Clips. Give it a name." },
    ] },
    { title: "Camera and moment", items: [
      { name: "Camera", text: "Only shown with more than one camera. Which camera it records. Follow the rule uses the camera of the rule that fires it." },
      { name: "Record", text: "Once per violation, once per object, every N seconds, or every alert. Once per violation is the default." },
      { name: "Interval (s)", text: "Shown for every N seconds, where it is the gap between clips. For once per violation the same box is called Quiet gap that ends the episode (s)." },
    ] },
    { title: "Tagging", items: [
      { name: "Tag with object ID (when available)", text: "A tick box. Stamps the object's ID in the corner of the clip and into its file name, when the rule can say which object it was." },
    ] },
  ],

  "math.arith": [
    { title: "Arithmetic", items: [
      { name: "Operation", text: "Add, subtract, multiply, divide or remainder." },
      { name: "Input a", text: "The first number. Wire a value in, or type a number if nothing is wired." },
      { name: "Input b", text: "The second number. Wire a value in, or type a number. Use the Add input button for more numbers." },
    ] },
  ],

  "math.andor": [
    { title: "Logic", items: [
      { name: "Operation", text: "And is 1 when every answer is true. Or is 1 when any is. Not flips a single true or false answer over." },
      { name: "Input a", text: "The first answer. Wire a value in, or type 0 or 1 if nothing is wired." },
      { name: "Input b", text: "The second answer. Not shown for Not. Use the Add input button for more answers." },
    ] },
  ],

  "math.setvar": [
    { title: "Change Variable", items: [
      { name: "Value type", text: "What the variable holds: a decimal, a whole number or text. Auto reads it from the value." },
      { name: "Variable to set", text: "Wire the Variable you want to change into this input. It is not typed here." },
      { name: "Set it to", text: "The new value: type a number or text, or wire a value in." },
    ] },
  ],
};
