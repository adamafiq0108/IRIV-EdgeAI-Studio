// appPassword - the sending mailbox takes an APP password only (Tok Aba,
// 2026-10-14: "only allow app password").
//
// Where the box can tell, it refuses a normal account password: for Gmail
// (smtp.gmail.com / googlemail, and a blank server, which means Gmail) an app
// password is 16 lowercase letters, shown by Google in four groups of four.
// Spaces between the groups are accepted and dropped before saving. Other
// providers issue other formats, so for any other server a non-empty value is
// accepted. The server makes the same check (core/services/mail_accounts.py
// clean_app_password), and only for a password that is being SET: a saved
// one is never re-checked, and a blank field keeps the stored one.

export const APP_PASSWORD_MESSAGE = "That does not look like an app password. Gmail app "
  + "passwords have 16 letters (for example abcd efgh ijkl mnop). Create one in your "
  + "Google Account security settings.";

const GMAIL_HOST = /(^|\.)(gmail|googlemail)\.com$/i;
const GMAIL_APP_PASSWORD = /^[a-z]{4}( ?[a-z]{4}){3}$/;

/** A blank server is Gmail: that is what the box falls back to. */
export function isGmailHost(host) {
  const h = String(host || "").trim();
  return !h || GMAIL_HOST.test(h);
}

/** "" when the password is acceptable for this server, else the message. A
 *  blank password is not judged here (blank = keep the stored one / required
 *  is said elsewhere). */
export function appPasswordProblem(password, host) {
  const p = String(password || "").trim();
  if (!p || !isGmailHost(host)) return "";
  return GMAIL_APP_PASSWORD.test(p) ? "" : APP_PASSWORD_MESSAGE;
}

/** What is stored: a Gmail app password without its spaces; anything else
 *  exactly as typed. */
export function cleanAppPassword(password, host) {
  const p = String(password || "");
  return isGmailHost(host) && !appPasswordProblem(p, host) ? p.replace(/\s+/g, "") : p;
}

//: The steps, in Google's own words, for the "How to create one" hint.
export const GMAIL_APP_PASSWORD_STEPS = [
  "Open your Google Account and go to Security.",
  "Turn on 2-Step Verification if it is not on yet.",
  "Open 2-Step Verification, then App passwords at the bottom of the page.",
  "Name it (for example IRIV box) and press Create.",
  "Copy the 16 letters shown and paste them here.",
];
