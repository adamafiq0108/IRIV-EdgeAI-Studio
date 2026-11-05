// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Workflow Guideline - the stepped primer in the right-hand rail.
//
// Five steps, each a video plus what that step teaches. It replaced four
// paragraphs of prose that said the same things in the order they were
// written rather than the order they are done: an operator meeting this
// canvas has to learn a SEQUENCE (detection, then zones, then analytics,
// then output), and a sequence is a thing you step through.
//
// ─────────────────────────────────────────────────────────────────────────
//  ★ TO ADD YOUR VIDEOS: fill in the `video` field of each STEP below.
//
//      video: "/static/guide/workflow-overview.mp4"   ← a file you ship
//      video: "https://…/overview.mp4"                ← or a URL
//
//    A step with `video: ""` shows the placeholder instead, so a missing
//    file is never a broken player. Put the files anywhere the browser can
//    fetch them; core/static/ is served at /static/ on both the hub and a
//    master, so core/static/guide/<name>.mp4 needs no route.
//
//    `poster` is optional -- a still shown before play. Same rules.
// ─────────────────────────────────────────────────────────────────────────
import { useState } from "react";

const STEPS = [
  {
    key: "workflow",
    title: "Workflow Overview",
    video: "",
    poster: "",
    body: [
      "The workflow is the overall structure of your analytics pipeline.",
      "It connects each step - from detection to output - to process the "
      + "data and produce results.",
    ],
    learn: [
      "Understand the overall workflow structure",
      "How data flows between nodes",
      "Key components and their roles",
    ],
    tip: "Drag nodes from the palette on the left and wire them together "
       + "left → right to build or modify the pipeline.",
  },
  {
    key: "detection",
    title: "Detection",
    video: "",
    poster: "",
    body: [
      "Every pipeline starts at the Detection Stream: the objects the model "
      + "found, frame by frame.",
      "A Class Filter after it decides WHICH of those objects the rest of "
      + "the branch is allowed to see.",
    ],
    learn: [
      "Where the detections come from",
      "Narrowing a branch to one class with a Class Filter",
      "Scoping a branch to one camera or one model",
    ],
    tip: "Leave the Class Filter empty and everything passes - a filter is a "
       + "narrowing, never a requirement.",
  },
  {
    key: "zones",
    title: "Zones",
    video: "",
    poster: "",
    body: [
      "A Zone node points a branch at a place you drew on the Zones page.",
      "An area answers “is it inside”; a line answers “did it cross”.",
    ],
    learn: [
      "Wiring a zone into a branch",
      "When to use an area and when to use a line",
      "Why a rule that uses a zone runs on that zone's camera",
    ],
    tip: "No zone wired in means the whole frame - which is what the Frame "
       + "Counter is for.",
  },
  {
    key: "analytics",
    title: "Analytics",
    video: "",
    poster: "",
    body: [
      "Analytics nodes are what the pipeline MEASURES: how many are here, "
      + "how long they stayed, how many crossed.",
      "Each one publishes its numbers under the name you give it.",
    ],
    learn: [
      "Choosing the right counter for the question",
      "Naming a node, and where that name shows up",
      "Combining values with a Formula",
    ],
    tip: "Name every node you care about - the name is what you will see on "
       + "cards, pickers and tables later.",
  },
  {
    key: "output",
    title: "Output",
    video: "",
    poster: "",
    body: [
      "Outputs are what the pipeline DOES: raise an alert, save a photo or "
      + "clip, publish to MQTT, record a row, draw a card.",
      "Most of them fire on an Alert wired in front of them.",
    ],
    learn: [
      "Raising an alert from a measured value",
      "Recording rows to a Data Table",
      "Putting a number on the Dashboard or Analytics page",
    ],
    tip: "A Dashboard card wired to a node is the fastest way to check the "
       + "pipeline is measuring what you meant.",
  },
];

/** The video for a step, or the placeholder when none is set yet. */
function StepVideo({ step }) {
  if (!step.video) {
    return (
      <div className="wf-guide-video wf-guide-video-empty">
        <span className="wf-guide-play" aria-hidden>▶</span>
        <span className="muted small">Video for “{step.title}”</span>
      </div>
    );
  }
  return (
    // `key` on the source's URL so switching steps loads the new file
    // instead of leaving the previous one playing under a new caption.
    <video className="wf-guide-video" key={step.video} controls
           poster={step.poster || undefined} preload="metadata">
      <source src={step.video} />
      Your browser cannot play this video.
    </video>
  );
}

export default function Guideline({ onHide }) {
  const [at, setAt] = useState(0);
  const step = STEPS[at];
  const last = at === STEPS.length - 1;

  return (
    <div className="wf-guide">
      <div className="wf-help-head">
        <div className="section-title">Workflow Guideline</div>
        {onHide && (
          <button className="btn btn-sm" onClick={onHide}
                  title="Hide this guide - the ? tab on the right brings it back">
            Hide
          </button>
        )}
      </div>

      {/* The five steps as a strip of numbered chips: where you are, how far
          there is to go, and every step reachable without stepping through
          the ones between. */}
      <ol className="wf-guide-steps">
        {STEPS.map((s, i) => (
          <li key={s.key}>
            <button type="button"
                    className={"wf-guide-step"
                      + (i === at ? " is-active" : "")
                      + (i < at ? " is-done" : "")}
                    onClick={() => setAt(i)}>
              <span className="wf-guide-num">{i + 1}</span>
              <span className="wf-guide-step-name">{s.title}</span>
            </button>
          </li>
        ))}
      </ol>

      <StepVideo step={step} />

      <h3 className="wf-guide-title">{at + 1}. {step.title}</h3>
      {step.body.map((p) => <p key={p} className="muted small">{p}</p>)}

      <div className="wf-guide-box">
        <div className="wf-guide-box-title">In this step you will learn:</div>
        <ul className="wf-guide-list">
          {step.learn.map((l) => (
            <li key={l}><span className="wf-guide-check" aria-hidden>✓</span>{l}</li>
          ))}
        </ul>
      </div>

      {step.tip && (
        <div className="wf-guide-tip">
          <div className="wf-guide-box-title">
            <span aria-hidden>💡</span> Tip
          </div>
          <p className="muted small" style={{ margin: 0 }}>{step.tip}</p>
        </div>
      )}

      <div className="wf-guide-nav">
        <button className="btn btn-sm" disabled={at === 0}
                onClick={() => setAt((i) => i - 1)}>← Previous</button>
        <div style={{ flex: 1 }} />
        {/* On the last step the button FINISHES rather than pretending there
            is a sixth: closing the guide is the honest "next". */}
        <button className="btn btn-sm btn-accent"
                onClick={() => (last ? onHide?.() : setAt((i) => i + 1))}>
          {last ? "Finish" : "Next →"}
        </button>
      </div>

      {/* The keyboard/mouse map the prose version carried. Kept, folded: it
          is a reference to come back to, not a step to walk. */}
      <details className="wf-guide-ref">
        <summary className="muted small">Shortcuts &amp; mouse</summary>
        <p className="muted small">
          Ctrl+X/C/V cut-copy-paste · Ctrl+Z/Y undo-redo · Del delete ·
          Ctrl+A select all · Ctrl+S save now.
        </p>
        <p className="muted small">
          <strong>Right- or middle-drag</strong> pans,{" "}
          <strong>left-drag</strong> draws a selection box, scroll zooms,{" "}
          <strong>right-click</strong> opens the menu.
        </p>
      </details>
    </div>
  );
}
