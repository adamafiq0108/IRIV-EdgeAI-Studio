// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// httpsLinks - the HTTPS card's words that are pure logic: the addresses the
// box answers HTTPS on, and the one-line state. No imports, so
// tests/check_httpslinks.mjs runs it with plain node (no bundler needed).

/** Every https:// address the certificate covers, the way a person would type
 *  it: names first (<host>.local), then addresses. No localhost / loopback
 *  (only the box's own screen could use them), and never a bare one-label
 *  name: no root may vouch for one (core/services/tlscert.py, Pipi M1), and a
 *  certificate from an older root that still carries it is not offered.
 *  Port 443 needs no port in the address; otherwise it is spelled out. */
export function httpsUrls(d) {
  if (!d || !d.enabled || !d.leaf) return [];
  const port = d.port443 ? "" : `:${d.port}`;
  const loop = new Set(["localhost", "127.0.0.1", "::1"]);
  const names = (d.leaf.names || []).filter((n) => !loop.has(n) && n.includes("."));
  const ips = (d.leaf.ips || []).filter((ip) => !loop.has(ip))
    .map((ip) => (ip.includes(":") ? `[${ip}]` : ip));
  return [...names, ...ips].map((h) => `https://${h}${port}/`);
}

/** "Off" / "On - port 8443" / "On - ports 443 and 8443" / "On, not answering". */
export function httpsState(d) {
  if (!d || !d.enabled) return "Off";
  if (!d.listening) return "On, not answering";
  return d.port443 ? `On - ports 443 and ${d.port}` : `On - port ${d.port}`;
}

/** A date the way the card shows it: 10 Oct 2027. */
export function shortDate(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return String(iso);
  return t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
