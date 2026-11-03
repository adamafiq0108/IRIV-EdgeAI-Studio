// Shared visual treatment for log rows and their level labels.
//
// ONE vocabulary for all three Diagnostics pages. The Log Hub speaks the
// five logging levels; the event store writes its own three words
// (info / warn / crit, core/services/functions.py:114). Those are the SAME
// three degrees of "how much should this worry me", so they wear the same
// fill / outline / dash treatment rather than a second colour scheme on the
// next page along - severityLevel() is the map, and the chip keeps showing
// the writer's own word through `text` so nothing is renamed under the
// operator who filtered for it.
const MARK = {
  CRITICAL: "\u25FC",   // filled square
  ERROR: "\u25C6",      // filled diamond
  WARNING: "\u25B2",    // triangle
};

const KNOWN = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

const slug = (level) => {
  const l = String(level || "").toUpperCase();
  return KNOWN.includes(l) ? l.toLowerCase() : "";
};

/** Any writer's severity word -> one of the five levels, or "" if it is not
 *  one this system has a treatment for (which renders as the plain chip, not
 *  as a wrong one). */
export function severityLevel(severity) {
  const s = String(severity || "").trim().toUpperCase();
  if (!s) return "";
  if (KNOWN.includes(s)) return s;
  if (["CRIT", "CRITICAL", "FATAL", "ALARM", "ALERT"].includes(s)) return "CRITICAL";
  if (["ERR", "ERROR", "FAIL", "FAILED"].includes(s)) return "ERROR";
  if (["WARN", "WARNING", "MINOR"].includes(s)) return "WARNING";
  if (["INFO", "NOTICE", "OK"].includes(s)) return "INFO";
  if (["DEBUG", "TRACE"].includes(s)) return "DEBUG";
  return "";
}

export function logRowClass(level) {
  const s = slug(level);
  return s ? `log-row log-row-${s}` : "log-row";
}

/** `level` picks the treatment; `text`, when given, is the word shown. They
 *  differ only where a page has its own vocabulary over the same five
 *  degrees - an event row reads "crit" and wears CRITICAL. */
export function LogLevelTag({ level, text }) {
  const s = slug(level);
  const mark = MARK[String(level || "").toUpperCase()];
  return (
    <span className={"log-level" + (s ? ` log-level-${s}` : "")}>
      {mark && <span className="log-level-mark" aria-hidden="true">{mark}</span>}
      {text === undefined || text === null || text === "" ? level : text}
    </span>
  );
}
