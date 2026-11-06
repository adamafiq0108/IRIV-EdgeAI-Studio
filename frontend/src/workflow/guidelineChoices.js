// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// What each CHOICE in a node's dropdowns does, for the Nodes Guideline tour
// (pages/NodeGuideline.jsx): picking an option there shows its explanation.
//
// Keyed node type -> field label -> option text, exactly as the properties
// panel renders them. A field whose options come from the box's own data
// (models, zones, cameras, tables...) has a "*" entry for "one of these".
// Same word rules as guidelineText.js: plain words, no code-style names.
export const CHOICE_GUIDE = {
  "analytics.counter": {
    "Count": {
      "How many are in view now": "Shows how many objects are in view this moment. The number goes up and down as things come and go.",
      "Total seen (each object once)": "A running total where each object is counted once by its object ID. Kept per day on the box, so it survives a restart.",
      "How many are in the zone now": "Shows how many objects are inside the wired zone this moment. The number goes up and down as things come and go.",
      "Total that entered (each object once)": "A running total of objects that entered the zone, each counted once by its object ID. Kept per day, so it survives a restart."
    }
  },
  "analytics.dwell": {
    "What to measure": {
      "Time present": "Measures how long each object stays, in the zone or in the whole view. Use it for waiting times or loitering.",
      "Countdown": "Gives a time budget that each arrival uses up. Watch the time left and alert when it reaches 0.",
      "Occupied clock": "One clock that runs while anything is in the zone and stops when it is empty. Needs a Zone wired in."
    },
    "Start over": {
      "every day": "The time budget fills up again at midnight each day.",
      "never": "The budget keeps going down until detection restarts."
    },
    "Per-object time arrives": {
      "Live, continuously": "Gives the running time of each stay while the object is still there. Good for cards and limits.",
      "Once, when they leave": "Records each finished stay once, with the object ID, how long it stayed and when it came and left. Good for a Data Table."
    }
  },
  "analytics.expr": {
    "Expression": {
      "Templates…": "A list of ready-made formulas. Pick one to put it in the Expression box, then wire values into the inputs its names create.",
      "Violation percentage: (violation_count / max(object_count, 1)) * 100": "Fills the Expression box with this formula, replacing what was there. It gives what percent of objects are in violation. Wire a violation count and an object count into its inputs.",
      "Total: a + b": "Fills the Expression box with this formula, replacing what was there. It adds two values. Wire one value into each input.",
      "Difference: entry_count - exit_count": "Fills the Expression box with this formula, replacing what was there. It takes one value away from another, such as people out from people in.",
      "Average of two: (a + b) / 2": "Fills the Expression box with this formula, replacing what was there. It gives the average of two values. Wire one value into each input.",
      "Percentage: (part / total) * 100": "Fills the Expression box with this formula, replacing what was there. It shows one value as a percent of another. Wire the part and the total into its inputs.",
      "Occupancy %: people_inside / capacity * 100": "Fills the Expression box with this formula, replacing what was there. It shows how full a place is, as a percent. Wire the count inside and the capacity into its inputs.",
      "Remaining capacity: capacity - people_inside": "Fills the Expression box with this formula, replacing what was there. It shows how many more can come in. Wire the capacity and the count inside into its inputs.",
      "Alert flag: iff(violations_now > 0, 1, 0)": "Fills the Expression box with this formula, replacing what was there. It gives 1 while there is any violation and 0 when there is none, handy for an alarm or a light.",
      "Clamped 0..100: clamp(value, 0, 100)": "Fills the Expression box with this formula, replacing what was there. It keeps a value between 0 and 100, so a percent never goes below 0 or above 100."
    }
  },
  "analytics.total": {
    "Accumulate": {
      "count events (+1 each time it turns on)": "Adds 1 each time the wired value goes from 0 to above 0. Use it to count how many times something happened.",
      "increment (adds every increase)": "Adds every rise of the wired value to the total. Drops never subtract. Use it to total a growing number."
    },
    "Start over": {
      "never": "The total keeps adding up and only resets when detection restarts or the Reset port is used.",
      "every day": "The total goes back to zero at midnight, local time. Use it for daily totals."
    },
    "After a restart": {
      "start from zero": "When detection restarts, the total starts again from 0.",
      "continue from the last saved total": "When detection restarts, the total carries on from the number it had, which is saved on this box."
    }
  },
  "analytics.travel": {
    "Also show speed": {
      "Off": "Measures only how long each trip takes. Nothing else to set.",
      "On": "Also works out speed. You must enter the real distance between the two lines."
    },
    "Speed unit": {
      "km/h": "Shows speed in kilometres per hour, common for cars and roads.",
      "mph": "Shows speed in miles per hour.",
      "m/s": "Shows speed in metres per second, handy for people walking or slow machines."
    }
  },
  "branch.cases": {
    "Conditions": {
      ">": "Holds when the input is greater than the value, for example more than 5 people.",
      ">=": "Holds when the input is greater than or equal to the value.",
      "<": "Holds when the input is less than the value.",
      "<=": "Holds when the input is less than or equal to the value.",
      "==": "Holds when the input is exactly equal to the value. Good for 0/1 answers or exact counts.",
      "!=": "Holds when the input is anything other than the value.",
      "and": "This extra test must hold as well as the row above it for the condition to pass.",
      "or": "The condition passes if either this extra test or the row above it holds.",
      "*": "Picks which wired input this test reads. Only inputs with a wire into them are listed."
    }
  },
  "branch.multi": {
    "Passes when": {
      "ALL conditions hold (AND)": "The node passes only when every condition is true. Use it when several things must be true together.",
      "ANY condition holds (OR)": "The node passes when at least one condition is true. Use it when any one of them is enough."
    },
    "Conditions": {
      ">": "Holds when the value is greater than the number.",
      ">=": "Holds when the value is greater than or equal to the number.",
      "<": "Holds when the value is less than the number.",
      "<=": "Holds when the value is less than or equal to the number.",
      "==": "Holds when the value is exactly equal to the number.",
      "!=": "Holds when the value is anything other than the number.",
      "*": "Picks which wired value this condition tests. Only inputs with a wire in them are listed; one marked not wired has lost its wire."
    }
  },
  "common.inject": {
    "Payload": {
      "a-z string": "Sends a piece of text you type. Text can be sent on by MQTT, HTTP or Python, but cannot be used in sums.",
      "0-9 number": "Sends a number you type. The nodes after it can test it and use it in sums.",
      "true / false": "Sends true or false, read as 1 or 0. Use it to switch something on or off by hand.",
      "JSON": "Sends structured data you type in a box, which must be valid JSON. Useful for MQTT, HTTP or Python nodes that expect it.",
      "timestamp": "Sends the time at the moment it fires, so there is nothing to type. Good when you only need a signal to start something."
    },
    "Trigger": {
      "manual - click the node": "It fires only when you click Inject on the node or its dashboard button. Good for testing.",
      "automatic - every interval": "It fires by itself again and again, every interval you set below. The button becomes Start and Stop."
    }
  },
  "dashboard.card": {
    "Show as": {
      "stat tile - one value (number or text)": "Shows one big value, such as a count or a word. The simplest card and the default.",
      "gauge - a dial": "Shows the value as a dial between a low and a high end. Good for levels such as how full an area is.",
      "status - a state": "Shows a big word with a colour, such as COMPLIANT in green. Each number the value can take gets its own label and colour.",
      "table - the latest rows": "Shown when the Source is recorded events or a data table. Lists the newest recorded rows instead of one value.",
      "status pill (old)": "Only shown on cards saved with the old OK / ALERT pill. It keeps working, but it is no longer offered for new cards."
    },
    "Source": {
      "what is wired in - a rule, a column or a Query": "Shows whatever is wired into this widget: a rule's value, a Data Table column or a Query. Nothing wired means nothing to show yet.",
      "the value it publishes": "Shown when a rule is wired in. The widget shows that rule's live value.",
      "recorded events": "Counts the events that were saved, so it can answer how many so far. Needs no wire: unwired, every matching event counts.",
      "the events it raised": "Shown when a rule is wired in. The widget counts the events that rule raised and saved, instead of its live value.",
      "a data table": "Reads a data table that you name below, instead of a wire. Only offered when the workflow has a Data Table node."
    },
    "Show the value as": {
      "the number itself": "Shows the value as a number. Right for counts and totals.",
      "the text itself": "Shows the value as written. Use it when the value is a word, such as a class name held in a variable.",
      "YES / NO": "Shows YES when the value is above 0, and NO otherwise. Good for a found or not found answer.",
      "ON / OFF": "Shows ON when the value is above 0, and OFF otherwise. Good for a machine or a light.",
      "TRUE / FALSE": "Shows TRUE when the value is above 0, and FALSE otherwise."
    },
    "Metric": {
      "active": "Offered for a status card. It is 1 while the wired rule's alert condition holds, which is the usual thing a status card shows.",
      "*": "Picks which of the wired node's numbers this card shows, when that node gives out more than one."
    },
    "Date range": {
      "Live value": "For a card fed by a live rule. Shows the number as it is right now. This is the default.",
      "Date Selection": "For a card fed by a live rule. The date picker on the Analytics page decides which period is shown.",
      "Today": "Covers today only, from midnight to now.",
      "Weekly": "For a card fed by a live rule. Shows the rule's count for this week.",
      "Monthly": "For a card fed by a live rule. Shows the rule's count for this month.",
      "All time": "For a card reading recorded rows. Uses every row ever recorded.",
      "Follow the Analytics date picker": "For a card reading recorded rows. The date picker on the Analytics page decides which rows are used.",
      "This week": "For a card reading recorded rows. Uses the rows from this week.",
      "This month": "For a card reading recorded rows. Uses the rows from this month.",
      "Last 3 hours": "Uses the rows from the last 3 hours.",
      "Last 6 hours": "Uses the rows from the last 6 hours.",
      "Last 12 hours": "Uses the rows from the last 12 hours.",
      "Last 24 hours": "Uses the rows from the last 24 hours.",
      "Past week": "Uses the rows from the last 7 days.",
      "Past month": "Uses the rows from the last 30 days.",
      "Past 3 months": "Uses the rows from the last 90 days.",
      "Past 6 months": "Uses the rows from the last 180 days.",
      "Past year": "Uses the rows from the last 365 days.",
      "Pick dates...": "Opens a start and an end date so the card covers an exact stretch of time."
    },
    "Which table": {
      "- choose a table -": "Pick the data table this widget reads. Tables are the ones recorded by Data Table nodes in this workflow.",
      "*": "Reads the rows that this Data Table node records, so you can count or total anything you chose to log."
    },
    "Work out": {
      "total rows": "Counts how many rows were recorded. Works on any column, and is the usual choice for counting events.",
      "total value": "Adds up the numbers in the chosen column, for example the total parts made. Needs a number column.",
      "average value": "The average of the numbers in the chosen column, such as the average wait time. Needs a number column.",
      "smallest value": "The lowest number recorded in the chosen column. Needs a number column.",
      "largest value": "The highest number recorded in the chosen column, such as the busiest moment. Needs a number column.",
      "the latest value": "Shows the newest value recorded in the chosen column, like a live reading taken from the table.",
      "total different values": "Counts how many different values appear, for example how many different cameras or zones raised an event.",
      "value changes": "Counts how many times the chosen column changed to a new value, for example how often a machine switched state."
    },
    "Of which column": {
      "- choose a column -": "Pick the column to work the answer out of. Until you pick one, there is nothing to total or average.",
      "*": "Picks that column of the table. The card or chart then works its answer out of the values recorded in it."
    },
    "One point per": {
      "nothing - one total, or by time": "No column grouping. The card gives one total, or groups by time if a time grouping is set below.",
      "nothing - group by time": "No column grouping. The points are grouped by time instead.",
      "*": "Gives one point or bar for each value of that column, for example one per zone. A time grouping then steps aside."
    },
    "Group by time": {
      "Automatic": "Lets the system pick a sensible gap between points, such as an hour, or a day for longer periods.",
      "None - one total": "No grouping by time. Everything in the period is added into one total.",
      "Every minute": "One point for each minute. Good for short, busy periods.",
      "Every 5 minutes": "One point for each 5 minutes.",
      "Every 30 minutes": "One point for each half hour.",
      "Every hour": "One point for each hour. A common choice for a day's view.",
      "Every 6 hours": "One point for each 6 hours.",
      "Every 12 hours": "One point for each 12 hours.",
      "Every day": "One point for each day. A common choice for a week or a month.",
      "Every week": "One point for each week. Good for a view of several months.",
      "Every month": "One point for each calendar month. Good for a view of a year.",
      "Custom...": "Type your own gap: a number plus minutes, hours or days. It must be at least 1 minute and at most 90 days.",
      "minutes": "Shown for Custom. The number you typed counts in minutes.",
      "hours": "Shown for Custom. The number you typed counts in hours.",
      "days": "Shown for Custom. The number you typed counts in days."
    },
    "Only events of type": {
      "any": "Counts every kind of recorded event.",
      "violation": "Counts only events recorded as violations.",
      "alert": "Counts only events recorded as alerts.",
      "info": "Counts only events recorded as information."
    },
    "And where…": {
      "- field -": "Pick a recorded detail to filter on, such as the camera or the zone. All conditions must match for an event to count.",
      "- value -": "Pick the value that detail must have. The list shows values that were actually recorded.",
      "*": "Picking a detail or value limits the count to events where that detail has that value, for example only events from one camera."
    },
    "Alert when": {
      ">": "Holds when the value is greater than the number. Then the pill shows its alert word.",
      ">=": "Holds when the value is greater than or equal to the number. Then the pill shows its alert word.",
      "<": "Holds when the value is less than the number. Then the pill shows its alert word.",
      "<=": "Holds when the value is less than or equal to the number. Then the pill shows its alert word.",
      "==": "Holds when the value is exactly equal to the number. Then the pill shows its alert word.",
      "!=": "Holds when the value is anything other than the number. Then the pill shows its alert word."
    },
    "States": {
      "green": "Shows this state in green. Use it for the normal, all good state.",
      "amber": "Shows this state in amber. Use it for a warning.",
      "red": "Shows this state in red. Use it for a problem or a violation.",
      "grey": "Shows this state in grey. Use it for idle or no data.",
      "custom colour…": "Lets you choose an exact colour for this state with the colour box beside it."
    },
    "What each line does": {
      "Publish a value I can wire (no alert)": "Each threshold line becomes a 1 or 0 value that other nodes can use. No event is raised. This is the default.",
      "...and raise an alert when it is crossed": "Also raises an alert in the events log when the value crosses a line. Then you can set the severity and a quiet period.",
      "Nothing - the lines only colour the card": "The lines only change the colours. They make no value and no alert."
    },
    "Severity": {
      "Info": "The alert is recorded as information only. Use it for things worth noting but not urgent.",
      "Warning": "The alert is recorded as a warning. This is the default.",
      "Critical": "The alert is recorded as critical. Use it for problems that need action right away."
    }
  },
  "dashboard.graph": {
    "Show as": {
      "Line chart": "Draws the values as a line over time. Good for seeing trends. Every setting carries over when you switch.",
      "Area chart": "Like a line chart, with the area under the line filled in, to show the amount over time.",
      "Bar chart (Time based)": "Draws one bar for each time step, such as each hour or day, to compare periods side by side.",
      "Bar chart (Category)": "Draws one bar for each camera, input or column value, to compare them.",
      "Radar chart": "Only shown for a chart already saved as a radar. New radar charts cannot be chosen yet.",
      "Pie chart": "Shows how a total is split into parts, one slice per category.",
      "Donut chart": "Like a pie chart with an empty centre, to show how a total is split."
    },
    "Source": {
      "what is wired in - a rule, a column or a Query": "Shows whatever is wired into this widget: a rule's value, a Data Table column or a Query. Nothing wired means nothing to show yet.",
      "the value it publishes": "Shown when a rule is wired in. The widget shows that rule's live value.",
      "recorded events": "Counts the events that were saved, so it can answer how many so far. Needs no wire: unwired, every matching event counts.",
      "the events it raised": "Shown when a rule is wired in. The widget counts the events that rule raised and saved, instead of its live value.",
      "a data table": "Reads a data table that you name below, instead of a wire. Only offered when the workflow has a Data Table node."
    },
    "Metric": {
      "*": "Picks which of the wired node's numbers this chart draws, when that node gives out more than one."
    },
    "Which table": {
      "- choose a table -": "Pick the data table this widget reads. Tables are the ones recorded by Data Table nodes in this workflow.",
      "*": "Reads the rows that this Data Table node records, so you can count or total anything you chose to log."
    },
    "Only events of type": {
      "any": "Counts every kind of recorded event.",
      "violation": "Counts only events recorded as violations.",
      "alert": "Counts only events recorded as alerts.",
      "info": "Counts only events recorded as information."
    },
    "And where…": {
      "- field -": "Pick a recorded detail to filter on, such as the camera or the zone. All conditions must match for an event to count.",
      "- value -": "Pick the value that detail must have. The list shows values that were actually recorded.",
      "*": "Picking a detail or value limits the count to events where that detail has that value, for example only events from one camera."
    },
    "X axis": {
      "Time": "Time runs along the bottom, so you see how the value changes. Then you choose the interval and the date range.",
      "Database column": "Gives one point for each value of a table column instead of time. Then you pick the column.",
      "Category": "For a bar chart. Gives one bar per camera, input or column value instead of time. This makes it a category bar chart."
    },
    "X column": {
      "- choose a column -": "Pick the column whose values run along the bottom.",
      "*": "Gives one point for each value of that column."
    },
    "Category field": {
      "Camera / Input": "One bar or slice for each wired input, such as each camera.",
      "*": "Gives one bar or slice for each value of that column, for example one per zone."
    },
    "Category / metric": {
      "Camera / Input": "One spoke for each wired input, such as each camera.",
      "*": "Gives one spoke for each value of that column."
    },
    "Interval": {
      "Hour": "The last 24 hours, one point per hour.",
      "Day": "The last 7 days, one point per day.",
      "Week": "Each day of the last week, or each week over a longer range. Choose which under Show.",
      "Month": "Each day of the last 30 days, or each calendar month. Choose which under Show.",
      "Custom": "Set your own date range and your own gap between points."
    },
    "Show": {
      "Each day (last 7 days)": "For the Week interval. One point for each of the last 7 days.",
      "Each week (last 3 months)": "For the Week interval. One point for each week over the last 3 months.",
      "Each day (last 30 days)": "For the Month interval. One point for each of the last 30 days.",
      "Each calendar month (last year)": "For the Month interval. One point for each calendar month over the last year."
    },
    "Group by": {
      "Automatic": "Lets the system pick a sensible gap between points, such as an hour, or a day for longer periods.",
      "Every minute": "One point for each minute. Good for short, busy periods.",
      "Every 5 minutes": "One point for each 5 minutes.",
      "Every 30 minutes": "One point for each half hour.",
      "Every hour": "One point for each hour. A common choice for a day's view.",
      "Every 6 hours": "One point for each 6 hours.",
      "Every 12 hours": "One point for each 12 hours.",
      "Every day": "One point for each day. A common choice for a week or a month.",
      "Every week": "One point for each week. Good for a view of several months.",
      "Every month": "One point for each calendar month. Good for a view of a year.",
      "Custom...": "Type your own gap: a number plus minutes, hours or days. It must be at least 1 minute and at most 90 days.",
      "minutes": "Shown for Custom. The number you typed counts in minutes.",
      "hours": "Shown for Custom. The number you typed counts in hours.",
      "days": "Shown for Custom. The number you typed counts in days."
    },
    "Date range": {
      "All time": "Shown when the X axis is a column. Uses every recorded row, with no date limit.",
      "Last 3 hours": "Shows the last 3 hours up to now.",
      "Last 6 hours": "Shows the last 6 hours up to now.",
      "Last 12 hours": "Shows the last 12 hours up to now.",
      "Last 24 hours": "Shows the last 24 hours up to now. The default for a time chart.",
      "Today": "Shows today, from 12 am to now.",
      "Past week": "Shows the last 7 days up to now.",
      "Past month": "Shows the last 30 days up to now.",
      "Past 3 months": "Shows the last 90 days up to now.",
      "Past 6 months": "Shows the last 180 days up to now.",
      "Past year": "Shows the last 365 days up to now.",
      "Follow the Analytics date picker": "The date picker on the Analytics page decides, so one pick there changes every chart that follows it.",
      "Pick dates...": "Opens a start and an end date so you can chart an exact stretch of time."
    },
    "Y axis": {
      "Automatic - by column type": "Lets the system decide: number columns are averaged and other columns are counted.",
      "Count records": "Counts how many rows or readings land on each point.",
      "Count non-null values": "Counts the rows where the chosen column actually has a value, skipping empty ones.",
      "Count Difference Value": "Counts how many different values the chosen column holds on each point.",
      "Count Value Changes": "Counts how many times the chosen column changed to a new value on each point.",
      "Sum": "Adds up the numbers of the chosen column on each point. Needs a number column.",
      "Average": "The average of the chosen column on each point. Needs a number column.",
      "Minimum": "The lowest value of the chosen column on each point. Needs a number column.",
      "Maximum": "The highest value of the chosen column on each point. Needs a number column."
    },
    "Value": {
      "Automatic - by column type": "Lets the system decide: number columns are averaged and other columns are counted.",
      "Count records": "Counts how many rows or readings land on each point.",
      "Count non-null values": "Counts the rows where the chosen column actually has a value, skipping empty ones.",
      "Count Difference Value": "Counts how many different values the chosen column holds on each point.",
      "Count Value Changes": "Counts how many times the chosen column changed to a new value on each point.",
      "Sum": "Adds up the numbers of the chosen column on each point. Needs a number column.",
      "Average": "The average of the chosen column on each point. Needs a number column.",
      "Minimum": "The lowest value of the chosen column on each point. Needs a number column.",
      "Maximum": "The highest value of the chosen column on each point. Needs a number column."
    },
    "Of column": {
      "- choose a column -": "Pick the column to work the value out of. Until you pick one, nothing is drawn.",
      "Use the chart's": "In an input's override. This input uses the same column as the rest of the chart.",
      "*": "Picks that column of the table. The card or chart then works its answer out of the values recorded in it."
    },
    "Calculation": {
      "Use the chart's": "This input uses the chart's own calculation. Pick another one only when this input needs a different answer.",
      "Count records": "Counts how many rows or readings land on each point.",
      "Count non-null values": "Counts the rows where the chosen column actually has a value, skipping empty ones.",
      "Count Difference Value": "Counts how many different values the chosen column holds on each point.",
      "Count Value Changes": "Counts how many times the chosen column changed to a new value on each point.",
      "Sum": "Adds up the numbers of the chosen column on each point. Needs a number column.",
      "Average": "The average of the chosen column on each point. Needs a number column.",
      "Minimum": "The lowest value of the chosen column on each point. Needs a number column.",
      "Maximum": "The highest value of the chosen column on each point. Needs a number column."
    },
    "Date column": {
      "Automatic (table date, else when saved)": "Uses the table's own date column if it has one, otherwise the moment each row was saved.",
      "when the row was saved": "Places each row at the moment it was saved.",
      "*": "Places each row in time using that date column of the table."
    },
    "Thresholds for this input": {
      "Use the shared thresholds": "This input uses the same threshold lines as the rest of the chart.",
      "Set thresholds for this input": "Gives this input, for example one camera, its own threshold lines."
    },
    "What each line does": {
      "Publish a value I can wire (no alert)": "Each threshold line becomes a 1 or 0 value that other nodes can use. No event is raised. This is the default.",
      "...and raise an alert when it is crossed": "Also raises an alert in the events log when the value crosses a line. Then you can set the severity and a quiet period.",
      "Nothing - the lines only colour the card": "The lines only change the colours. They make no value and no alert."
    },
    "Severity": {
      "Info": "The alert is recorded as information only. Use it for things worth noting but not urgent.",
      "Warning": "The alert is recorded as a warning. This is the default.",
      "Critical": "The alert is recorded as critical. Use it for problems that need action right away."
    }
  },
  "filter.preprocess": {
    "Steps": {
      "+ add a step...": "Pick an adjustment to add to the end of the list. The picture goes through the steps in order. Up to 8 steps.",
      "Grayscale": "Removes the colour, so every step after it works on a black and white picture.",
      "Exposure": "Changes the brightness and mid-tones. Use it for a camera that is too dark or washed out.",
      "Sharpness": "Makes edges and fine detail stand out. Too much can draw halos the detector may mistake for objects.",
      "Blur": "Smooths the picture. The usual way to stop camera noise looking like movement.",
      "Edges (Sobel)": "Keeps only the edges of shapes and throws the rest away.",
      "Mask to a zone": "The detector only sees inside the zones you pick. It hides the picture before the AI runs, unlike a Zone Filter."
    },
    "Zones": {
      "- choose a zone -": "Pick a zone the detector is allowed to see. Draw zones on the Zones page first.",
      "+ add another zone...": "Pick one more zone to add to the mask.",
      "*": "Adds that zone to the mask. Use a zone drawn on the same camera, or the wrong part of the picture is hidden."
    },
    "Fill the hidden part with": {
      "black - 0.8 ms a frame": "Paints the hidden part black. Fast, and the usual choice.",
      "blur - no hard edge, 7.8 ms a frame": "Blurs the hidden part, so there is no hard edge at the zone border. About ten times slower, so use it only if the edge causes false detections."
    },
    "Kind": {
      "gaussian - weighted, 0.9 ms at k5": "A soft, smooth blur. Also the faster of the two, so it is the usual choice.",
      "box - a flat average, 4.1 ms at k5": "A plain average blur. About four times slower than gaussian at the same size."
    }
  },
  "integration.command": {
    "(no label)": {
      "Last hour": "The button reports on the 60 minutes up to the moment it runs. A rolling hour, so a check at 14:05 covers 13:05 to 14:05.",
      "Last 24 hours": "The button reports on the full 24 hours up to the moment it runs, whatever the time of day.",
      "Today": "The button reports on today only, from midnight on the box's clock.",
      "Yesterday": "The button reports on the whole of yesterday, midnight to midnight. Good for a report about the previous day.",
      "This week": "The button reports on this week so far, starting from Monday at midnight.",
      "Last 7 days": "The button reports on today and the six days before it, whatever day the week starts on.",
      "This month": "The button reports on this month so far, from the 1st at midnight.",
      "Last 30 days": "The button reports on today and the 29 days before it.",
      "All time": "The button runs its Query with no time limit. Use it when the Query already says which rows it wants.",
      "Ask for dates": "When the button is tapped, the bot asks the person to type the dates. One date means that day; two dates mean a range, up to a year.",
      "Table": "The answer arrives as a text table of the rows. The safe choice; it suits any result.",
      "Bar": "The answer arrives as a bar chart: the first column gives the labels, the first number column after it gives the bar heights.",
      "Line": "The answer arrives as a line chart, good for a trend over time such as a count per day. The first column is the labels.",
      "Pie": "The answer arrives as a pie chart, good for shares of a whole such as counts per class. The first column is the slice names."
    }
  },
  "integration.photos": {
    "Camera": {
      "Any camera": "Sends snapshots saved by any camera in the time window.",
      "*": "Sends only the snapshots that camera saved in the time window."
    }
  },
  "integration.watch": {
    "Over which period": {
      "Last hour": "Looks at the 60 minutes up to the moment it runs. A rolling hour, so a check at 14:05 covers 13:05 to 14:05.",
      "Last 24 hours": "Looks at the full 24 hours up to the moment it runs, whatever the time of day.",
      "Today": "Looks at today only, from midnight on the box's clock. Early in the morning that is only a few hours.",
      "Yesterday": "Looks at the whole of yesterday, midnight to midnight. Good for a report about the previous day.",
      "This week": "Looks at this week so far, starting from Monday at midnight.",
      "Last 7 days": "Looks at today and the six days before it, whatever day the week starts on.",
      "This month": "Looks at this month so far, from the 1st at midnight.",
      "Last 30 days": "Looks at today and the 29 days before it.",
      "All time": "No time limit: every row the Query can find is counted. Use it when the Query already says which rows it wants."
    },
    "Send when": {
      ">": "Sends when the Query's number is greater than the value you type. Use it for 'more than 10 violations'.",
      "≥": "Sends when the Query's number is greater than or equal to the value you type.",
      "<": "Sends when the Query's number is less than the value you type, for example too few items packed.",
      "≤": "Sends when the Query's number is less than or equal to the value you type.",
      "=": "Sends only when the Query's number is exactly the value you type.",
      "≠": "Sends whenever the Query's number is anything other than the value you type."
    }
  },
  "logic.catch": {
    "Raise as": {
      "note (info)": "The error is logged as a note, the lowest level. Use it when you only want a record of it.",
      "alert (warn)": "The error is raised as an alert. The default, for faults someone should look at.",
      "violation (critical)": "The error is raised as a violation, the most serious level. Use it when a broken rule must be fixed at once."
    }
  },
  "math.andor": {
    "Operation": {
      "And": "Gives 1 only when every wired answer is true, and 0 otherwise.",
      "Or": "Gives 1 when at least one wired answer is true, and 0 when none is.",
      "Not": "Flips one 0/1 answer: 1 becomes 0 and 0 becomes 1."
    },
    "Period": {
      "all time": "Only for an input fed by a Data Table column. Uses every row ever recorded.",
      "today": "Only for an input fed by a Data Table column. Uses rows recorded since midnight today.",
      "this week": "Only for an input fed by a Data Table column. Uses rows from the last 7 days, counting today.",
      "this month": "Only for an input fed by a Data Table column. Uses rows from the last 30 days, counting today."
    }
  },
  "math.arith": {
    "Operation": {
      "Add": "Adds the wired values together. Use it to total several counts.",
      "Subtract": "Takes each next value away, working top to bottom. Use it for a difference, such as in minus out.",
      "Multiply": "Multiplies the values together, for example a count times a weight per item.",
      "Divide": "Divides by each next value, working top to bottom. Use it for a ratio or a rate.",
      "Remainder": "Divides and keeps only what is left over, so 7 and 3 give 1. Useful for every-Nth patterns."
    },
    "Period": {
      "all time": "Uses every row ever recorded in the wired table column.",
      "today": "Uses only the rows recorded today.",
      "this week": "Uses only the rows recorded this week.",
      "this month": "Uses only the rows recorded this month."
    }
  },
  "math.setvar": {
    "Value type": {
      "auto (now: whole number)": "Auto: the type is read from the value, and right now it is a whole number. Typing 12 gives whole, 4.5 decimal, words give text.",
      "auto (now: whole number, from the Variable)": "Auto: the type is read from the value, and right now it is a whole number, because the wired Variable says so.",
      "auto (now: decimal)": "Auto: the type is read from the value, and right now it is a decimal number. Typing 12 gives whole, 4.5 decimal, words give text.",
      "auto (now: decimal, from the Variable)": "Auto: the type is read from the value, and right now it is a decimal number, because the wired Variable says so.",
      "auto (now: text)": "Auto: the type is read from the value, and right now it is text. Typing 12 gives whole, 4.5 decimal, words give text.",
      "auto (now: text, from the Variable)": "Auto: the type is read from the value, and right now it is text, because the wired Variable says so.",
      "decimal": "Stores the number exactly as it comes, decimals kept. A type set on the wired Variable wins over this.",
      "whole number": "Rounds the value to a whole number, wired values too. Use it for counts like 12 people.",
      "text": "Stores a word, such as a shift name. Cards and Data Tables can show it, but formulas cannot calculate with it."
    }
  },
  "note.comment": {
    "Layer": {
      "behind the nodes": "The note sits under the nodes, so a big note can frame a whole branch without hiding it. The default.",
      "in front of the nodes": "The note sits on top of the nodes. Either way it comes to the front while selected."
    }
  },
  "output.buzzer": {
    "When this fires": {
      "turn it on (and leave it on)": "The buzzer starts and keeps sounding after the alert ends. You need another node set to turn it off.",
      "turn it off": "The buzzer stops. Use it on the rule that ends the problem, after another node turned it on.",
      "pulse it": "The buzzer sounds for the pulse length you set, then stops by itself. The sensible choice for most alarms.",
      "toggle it": "Each time this fires, the buzzer switches: off becomes on, on becomes off."
    }
  },
  "output.dataset": {
    "Table": {
      "- choose a table -": "No table picked yet. Pick one of the tables below; tables are created on the Database page.",
      "*": "Rows are written into this table (or read from it with Select). Switching to another table clears the column settings, because its columns differ."
    },
    "Operation": {
      "Insert - record rows": "This node writes new rows into the table. Use it to keep a record of what happened.",
      "Select - read only": "This node writes nothing. Use it to read columns back out of the table and show them on cards."
    },
    "Write a row": {
      "when something alerts": "A row is written each time a rule wired into this node alerts. Several rules can feed one table, each writing its own row.",
      "each time something crosses a line": "One row the moment an object crosses the line of the Line Counter wired in, either way. The number of rows equals the number of crossings.",
      "every few seconds": "A row is written on a timer, every so many seconds (5 at the least). Good for logging a level, such as a count through the day.",
      "when a value changes": "A row is written the moment a wired value changes. You can set a minimum gap so a jittery value does not flood the table."
    },
    "When the same object is seen again": {
      "Add a new row": "Every sighting gets its own row, so you keep the full history of the object.",
      "Update the existing row": "Keeps one row per object and refreshes it with the latest values and the time it was last seen. Needs a column that records the object."
    },
    "Object IDs in a row": {
      "One row per object": "Each object gets its own row.",
      "One row for the whole group (IDs listed together)": "One row lists every object present, IDs together such as \"4, 5\". A new row is saved only when that group changes."
    },
    "Columns": {
      "not recorded": "This node leaves the column empty. Choose it for columns you do not need here.",
      "pick a value": "Fills the column with a value from the workflow, such as a counter's number. Pick which one in the list that appears.",
      "from the event": "Fills the column with a detail of the moment that wrote the row, such as when it happened or which camera. Pick the detail in the next list.",
      "fixed value": "Fills the column with the same text every time, such as \"night shift\". Type it in the box that appears.",
      "when it happened": "Records the date and time of the event that wrote the row.",
      "which camera": "Records which camera the event came from.",
      "which zone": "Records which zone the event happened in.",
      "which way it crossed (in / out / cross)": "Records the direction of a line crossing: in, out, or cross for a line with no in or out side.",
      "the tracked object's id": "Records the object's ID, so the table knows which object the row is about. Needed for one row per object.",
      "what was detected (person, car…)": "Records the class of the object that was detected, such as person or car.",
      "the alert text": "Records the alert's message text.",
      "the value that triggered it": "Records the number that set off the event.",
      "how long the stay lasted, seconds (Time in Zone + OID)": "Records how many seconds the object stayed. Needs a Time in Zone node with object IDs turned on.",
      "when the object entered (Time in Zone + OID)": "Records the time the object came in. Needs a Time in Zone node with object IDs turned on.",
      "when the object left (Time in Zone + OID)": "Records the time the object left. Needs a Time in Zone node with object IDs turned on.",
      "how far past the limit the stay went, seconds (Trigger set to wait for the timer)": "Records how many seconds the stay went over its limit. Needs a Trigger set to wait for the timer to end.",
      "event type": "Records what kind of event wrote the row.",
      "severity": "Records how serious the event was, such as alert or violation.",
      "which AI model": "Records which AI model made the detection.",
      "how far from the camera, mm (depth camera)": "Records the object's distance from the camera in millimetres. Needs a depth camera.",
      "measured length, mm (depth camera)": "Records the object's measured length in millimetres. Needs a depth camera and a measurement set up for that class.",
      "measured width, mm (depth camera)": "Records the object's measured width in millimetres. Needs a depth camera and a measurement set up for that class.",
      "measured height, mm (depth camera)": "Records the object's measured height in millimetres. Needs a depth camera and a measurement set up for that class.",
      "distance to the object's top, mm (depth camera)": "Records the distance to the top of the object in millimetres. Needs a depth camera.",
      "measured diameter, mm (depth camera)": "Records the object's measured diameter in millimetres. Needs a depth camera and a measurement set up for that class."
    }
  },
  "output.io_out": {
    "Port": {
      "- choose a terminal -": "No terminal picked yet. Pick one of the outputs below that your relay or device is wired to.",
      "DO0 - Digital output 0": "Drives digital output 0 on the board's terminal block. Pick it if your relay or device is wired there.",
      "DO1 - Digital output 1": "Drives digital output 1 on the board's terminal block. Pick it if your relay or device is wired there.",
      "*": "Drives this terminal on the board. The text after its name shows its live state: ready, or in use by another program."
    },
    "When this fires": {
      "turn it on (and leave it on)": "Switches the output on and keeps it on after the alert clears. Pair it with a second node set to turn it off.",
      "turn it off": "Switches the output off. Use it on the rule that ends the condition, to release an output left on.",
      "pulse it": "Switches the output on for a short time, then releases it. Set the length in milliseconds below.",
      "toggle it": "Flips the output each time this fires: on if it was off, off if it was on."
    }
  },
  "output.io_pwm": {
    "Port": {
      "- choose a terminal -": "Pick which board terminal this node drives.",
      "L0 - LED / PWM 0": "Drives the L0 terminal on the board, used for an LED or a dimmable output.",
      "L1 - LED / PWM 1": "Drives the L1 terminal on the board, used for an LED or a dimmable output.",
      "L0 - LED / PWM 0 - ready": "Drives the L0 terminal on the board, used for an LED or a dimmable output. It is free to use.",
      "L1 - LED / PWM 1 - ready": "Drives the L1 terminal on the board, used for an LED or a dimmable output. It is free to use.",
      "L0 - LED / PWM 0 - in use by another program": "L0 is already held by another program. Free it there, or this node cannot drive it.",
      "L1 - LED / PWM 1 - in use by another program": "L1 is already held by another program. Free it there, or this node cannot drive it."
    },
    "Level comes from": {
      "a fixed level": "Always sets the same level, which you type from 0 (off) to 255 (full). 128 is half.",
      "the value wired into this node": "The level follows a wired value, scaled between the low and high numbers you set, for example dimming with a count."
    },
    "Which value": {
      "*": "Picks which of the wired node's numbers sets the level."
    },
    "Update": {
      "when the rule fires": "Sets the level each time the wired rule fires. This is the default.",
      "continuously (follow the value)": "Keeps changing the level to follow the wired value all the time, with no rule needed. Only one wire may feed it."
    }
  },
  "output.modbus_write": {
    "FC": {
      "FC 5: Force Single Coil": "Writes one on/off value (a coil) at the address. Use it to switch a single output on a machine.",
      "FC 6: Preset Single Register": "Writes one number (a register) at the address. This is the default.",
      "FC 15: Force Multiple Coils": "Writes several on/off values in a row, starting at the address. Quantity says how many.",
      "FC 16: Preset Multiple Registers": "Writes several numbers in a row, starting at the address. Quantity says how many."
    },
    "Server": {
      "none": "No server chosen yet. Pick or add a Modbus Server, or the node has nowhere to write.",
      "*": "Writes to this Modbus Server, the device set up with its address. Use the pencil to edit it."
    }
  },
  "output.record": {
    "Camera": {
      "Follow the rule": "Records from the camera of the rule that fired it. This is what you want in most cases.",
      "*": "Always records from this camera, even when the rule watches another one. Use it when the view you need is on a different camera."
    },
    "Record": {
      "Once per violation": "Records one clip per episode. It can record again only after a quiet gap with no new alert.",
      "Once per object": "Records one clip for each tracked object, so the same person is never recorded twice.",
      "Every N seconds": "Records at most one clip per interval you set, for as long as the alert keeps firing.",
      "Every alert": "Records a clip for every alert. Can be noisy and fill storage fast if the alert fires very often."
    }
  },
  "output.snapshot": {
    "Camera": {
      "Follow the rule": "Saves the picture from the camera of the rule that fires it. The usual choice.",
      "*": "Always saves the picture from this camera, even when the rule watches another one. Use it when the view you need is elsewhere."
    },
    "Take a photo": {
      "Once per violation": "One photo per episode. It takes another only after a quiet gap with no alert, which you set below.",
      "Once per object": "One photo per tracked object, by its object ID, so the same person is never photographed twice.",
      "Every N seconds": "At most one photo per interval while the alert keeps firing. Set the interval below.",
      "Every alert": "One photo for every alert. Can be noisy: a rule that fires on every frame saves a photo per frame."
    }
  },
  "output.telegram": {
    "Send to": {
      "All linked people": "Sends to everyone linked to this system's bot under Settings, Telegram. People linked later are included automatically.",
      "Selected people": "Sends only to the people or groups you tick in the list that appears. Someone removed under Settings gets nothing more.",
      "A chat ID (advanced)": "Sends to one chat whose number you type. The person must message the bot first, and the bot must be in a group to post there."
    }
  },
  "output.webhook": {
    "Method": {
      "POST": "Sends the payload to the URL. The usual choice for sending data to a service. The default.",
      "GET": "Calls the URL without sending the payload. Use it when the service only needs to be called."
    }
  },
  "output.webhook_publish": {
    "Method": {
      "POST": "Sends the value inside the request body. The usual choice for sending data to a web service.",
      "GET": "Makes a plain request with no body, so the Body setting is not sent. Use it only if the service expects GET."
    },
    "Which value": {
      "*": "Sends this number from the node wired in."
    },
    "Body": {
      "just the number (3)": "The request body is only the number, such as 3. Simple for services that expect one value.",
      "JSON - key, value, timestamp": "The body is JSON with the value's name, the value and the time. Better when the service needs to know what the number is."
    }
  },
  "output.zone_switch": {
    "Zone to switch": {
      "choose a zone…": "Pick the zone this node turns on and off.",
      "*": "This zone is switched by the wired value while the app runs. After a restart, the zone goes back to its saved setting."
    },
    "Which value decides": {
      "*": "Picks which of the wired node's numbers turns the zone on or off."
    },
    "When the wired value is 1": {
      "enable the zone (0 disables it)": "A wired 1 turns the zone on, so it counts and draws; 0 turns it off.",
      "disable the zone (0 enables it)": "A wired 1 turns the zone off, so it goes silent and invisible; 0 turns it on."
    }
  },
  "source.detection": {
    "Capture": {
      "Video - every frame": "The AI checks every camera frame, all the time. The usual choice for live counting and alerts.",
      "Snapshot - only when triggered": "The AI checks only when something wired into the snap input turns on, such as an Inject, a Trigger or a sensor. Use it when a check now and then is enough."
    },
    "Camera between snaps": {
      "Automatic": "The app decides: a USB or local camera is closed between snaps, and a network camera stays open but reads slowly. Right for most setups.",
      "Close the camera (lowest CPU)": "The camera is closed between snaps so the box works the least. Each snap waits about a second for the camera to open.",
      "Keep it open (fastest snap)": "The camera stays open between snaps, so each snap starts at once. The box works harder while it waits."
    },
    "Model": {
      "All models": "This branch uses what every enabled AI model found. Keep it when you do not need to tell the models apart.",
      "*": "This branch only sees what this one AI model found. Use it to build a rule for one model's objects."
    },
    "Camera": {
      "Every camera": "The branch runs on all cameras and adds the results together. Good for rules about objects, but not for rules that use a Zone.",
      "*": "The branch watches only this camera. A rule that uses a Zone must use the camera that zone was drawn on."
    }
  },
  "storage.csv": {
    "Rows to include": {
      "all rows from the link": "The file holds every row the wired Query returns.",
      "most rows (a limit)": "The file keeps only the first rows, up to a number you set, to keep a message small. The rest are cut from the end."
    }
  },
  "storage.query": {
    "Period": {
      "all time": "The statement sees every row ever recorded.",
      "follow the Analytics range": "The rows follow the date picker on the Analytics page, so the card changes when you pick other dates. With no picker, it uses today.",
      "today": "The statement sees only rows recorded since midnight today.",
      "this week": "The statement sees rows from the last 7 days, counting today.",
      "this month": "The statement sees rows from the last 30 days, counting today."
    }
  },
  "variable.board_input": {
    "Port": {
      "- choose a terminal -": "No terminal picked yet. Pick one of the inputs below that your switch or sensor is wired to.",
      "DI0 - Digital input 0": "Reads digital input 0 on the board's terminal block: 1 while the contact is closed, 0 while open.",
      "DI1 - Digital input 1": "Reads digital input 1 on the board's terminal block: 1 while the contact is closed, 0 while open.",
      "BUTTON - User button": "Reads the push button on the board itself. Handy for testing a workflow by hand.",
      "*": "Reads this terminal on the board. The text after its name shows what it reads right now, or that another program holds it."
    }
  },
  "variable.rs485": {
    "Server": {
      "none": "No Modbus Server picked yet. Pick one of the servers below, or add one with the plus button.",
      "*": "Reads from this Modbus Server, the device connection set up once (over the network or a serial port). Several nodes can share it."
    },
    "FC": {
      "FC 1: Read Coil Status": "Reads coils: on/off points the device can also be told to switch. Pick it when the datasheet lists the value as a coil.",
      "FC 2: Read Input Status": "Reads on/off inputs the device only reports, such as a contact or a sensor. Read only.",
      "FC 3: Read Holding Registers": "Reads holding registers: numbers the device stores and that can also be written. The most common choice.",
      "FC 4: Read Input Registers": "Reads input registers: numbers the device only reports, such as a measured temperature. Read only."
    },
    "Baud rate": {
      "1200": "Talks at 1200 bits per second. It must match the device's own setting, or nothing will be heard.",
      "2400": "Talks at 2400 bits per second. It must match the device's own setting, or nothing will be heard.",
      "4800": "Talks at 4800 bits per second. It must match the device's own setting, or nothing will be heard.",
      "9600": "Talks at 9600 bits per second, the default and the most common speed. It must match the device's own setting.",
      "19200": "Talks at 19200 bits per second. It must match the device's own setting, or nothing will be heard.",
      "38400": "Talks at 38400 bits per second. It must match the device's own setting, or nothing will be heard.",
      "57600": "Talks at 57600 bits per second. It must match the device's own setting, or nothing will be heard.",
      "115200": "Talks at 115200 bits per second. It must match the device's own setting, or nothing will be heard."
    }
  },
  "variable.schedule": {
    "Runs": {
      "Every N minutes": "Runs every so many minutes, counted from midnight, so 15 lands on :00, :15, :30 and :45.",
      "Hourly": "Runs once an hour, at the minute past the hour you choose.",
      "Daily": "Runs once a day at the time you choose, on the box's clock.",
      "Weekly": "Runs once a week, on the day and at the time you choose.",
      "Monthly": "Runs once a month, on the day and at the time you choose."
    },
    "Day": {
      "Monday": "Runs every Monday at the time you choose.",
      "Tuesday": "Runs every Tuesday at the time you choose.",
      "Wednesday": "Runs every Wednesday at the time you choose.",
      "Thursday": "Runs every Thursday at the time you choose.",
      "Friday": "Runs every Friday at the time you choose.",
      "Saturday": "Runs every Saturday at the time you choose.",
      "Sunday": "Runs every Sunday at the time you choose."
    },
    "Which day": {
      "A day I pick": "Runs on the day of the month you type. A month too short for that day runs on its last day instead.",
      "The last day of the month": "Runs on the last day of every month, whether it has 28, 30 or 31 days. Good for a month-end report."
    }
  },
  "zone.ref": {
    "Zone": {
      "- choose a zone -": "Pick the zone this node points at. Draw and edit zones on the Zones page first.",
      "*": "Points the branch at this zone, so only detections inside it go on. A zone marked disabled is turned off right now."
    }
  }
};

/** The explanation for one option, or "" when there is none. */
const squash = (t) => String(t || "").replace(/\s+/g, " ").trim().toLowerCase();

export function choiceText(type, field, option) {
  const byField = CHOICE_GUIDE[type] || {};
  // Labels and options are matched as written, then ignoring spacing and case: the panel
  // collapses a double space the code wrote, and a live suffix (" - ready") is left to "*".
  const f = byField[field] || byField[Object.keys(byField).find((k) => squash(k) === squash(field))];
  if (!f) return "";
  if (f[option]) return f[option];
  const k = Object.keys(f).find((x) => x !== "*" && squash(x) === squash(option));
  return (k && f[k]) || f["*"] || "";
}
