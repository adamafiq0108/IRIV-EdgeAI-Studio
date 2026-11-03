// Source text for the .mjs checks, with Windows line ends folded to LF.
//
// WHY: a checkout with core.autocrlf=true (every Windows laptop that cloned
// the repo with Git's default) gives the WORKING files CRLF line ends, while
// the checks match multi-line snippets written with "\n". test_daycolour
// failed only on such checkouts for exactly that reason: the source was
// right, the bytes on disk said "\r\n". Every .mjs check that reads a source
// file reads it through here, so a check means the same on Linux and Windows.
//
// Not a test itself: run_all.py only picks up check_*.mjs and test_*.mjs.
import { readFileSync } from "node:fs";

/** A text file as UTF-8 with CRLF folded to LF. */
export function readText(path) {
  return readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
