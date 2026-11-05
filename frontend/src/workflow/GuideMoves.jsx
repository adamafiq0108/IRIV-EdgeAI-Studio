// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// GuideMoves - short looping pictures (about 3 seconds each) that show how to work the Workflow
// canvas: drag a node in, open its properties, move around, zoom, right-click, delete.
// Drawn in CSS so they follow the theme and stay sharp; the styles are the gm-* rules in theme.css.
import { Minus, Mouse, Plus } from "lucide-react";

const Cursor = ({ className = "" }) => (
  <svg className={"gm-cursor " + className} width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M4 2l15 9-6.5 1.8L9.5 20z" fill="#111" stroke="#fff" strokeWidth="1.5" strokeLinejoin="round" />
  </svg>
);

const Node = ({ className = "", name = "Detection Stream", tag = "DET" }) => (
  <div className={"gm-node " + className}>
    <b>{name}</b><i>{tag}</i>
  </div>
);

const SCENES = {
  "mv-drag": () => (
    <>
      <div className="gm-list">
        <span>Detection Stream</span><span>Class Filter</span><span>Counter</span>
      </div>
      <Node className="gm-drag-ghost" />
      <Cursor className="gm-drag-cur" />
    </>
  ),
  "mv-dblclick": () => (
    <>
      <Node className="gm-dbl-node" />
      <span className="gm-ripple gm-dbl-r1" /><span className="gm-ripple gm-dbl-r2" />
      <div className="gm-panel"><b>Properties</b><span /><span /><span className="short" /></div>
      <Cursor className="gm-dbl-cur" />
    </>
  ),
  "mv-pan": () => (
    <>
      <div className="gm-world"><Node className="gm-pan-a" /><Node className="gm-pan-b" name="Counter" tag="CNT" /></div>
      <div className="gm-mouse"><Mouse size={34} strokeWidth={1.4} /><em className="gm-rbtn" /></div>
      <span className="gm-say">Hold the right mouse button and drag</span>
      <Cursor className="gm-pan-cur" />
    </>
  ),
  "mv-zoom": () => (
    <>
      <Node className="gm-zoom-node" />
      <div className="gm-mouse"><Mouse size={34} strokeWidth={1.4} /><span className="gm-wheel-up">&#9650;</span><span className="gm-wheel-down">&#9660;</span></div>
      <span className="gm-say">Scroll the wheel, or use + and -</span>
      <div className="gm-zbtns"><span className="gm-zin"><Plus size={14} /></span><span className="gm-zout"><Minus size={14} /></span></div>
    </>
  ),
  "mv-menu": () => (
    <>
      <Node className="gm-menu-node" />
      <span className="gm-ripple gm-menu-r" />
      <div className="gm-menu">
        <span>Cut</span><span>Copy</span><span>Duplicate</span><span>Disconnect wires</span>
        <span className="gm-menu-del">Delete</span>
      </div>
      <Cursor className="gm-menu-cur" />
    </>
  ),
  "mv-delete": () => (
    <>
      <Node className="gm-del-node" />
      <div className="gm-key"><kbd>Delete</kbd></div>
      <span className="gm-say">or right-click, then Delete</span>
    </>
  ),
};

export default function GuideMove({ kind }) {
  const Scene = SCENES[kind];
  if (!Scene) return null;
  return (
    <div className={"gm gm-s-" + kind.slice(3)} data-ng-snap={kind} data-gm={kind} aria-hidden="true">
      <Scene />
    </div>
  );
}
