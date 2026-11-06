// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The Modbus RTU request frame of a Modbus Read, standard or custom.
//
// A standard read is ID, FC, ST (start address, 2 bytes high first), Q
// (quantity, 2 bytes high first), CRC (2 bytes LOW first). Some devices want
// something else between FC and CRC -- a password, a different order, wider
// fields -- and the Advanced editor lets that layout be built per node. The
// SAME rules are in core/services/workflow.py (modbus_frame_hex) so the
// preview here and the bytes the box sends can never differ.

export const FIELD_SIZES = [1, 2, 3, 4, 6, 8];

/** CRC-16/MODBUS: start at 0xFFFF; for each byte XOR it in, then 8 times shift
 *  right, XOR-ing the reflected polynomial 0xA001 whenever the bit shifted out
 *  was 1. The result is sent LOW byte first. */
export function crc16Modbus(bytes) {
  let crc = 0xffff;
  for (const b of bytes) {
    crc ^= b & 0xff;
    for (let i = 0; i < 8; i += 1) crc = crc & 1 ? (crc >>> 1) ^ 0xa001 : crc >>> 1;
  }
  return crc & 0xffff;
}

/** The default movable fields of a read: ST then Q, 2 bytes each, high first. */
export function standardFields() {
  return [
    { key: "st", name: "ST", desc: "Starting address", size: 2, order: "hi" },
    { key: "q", name: "Q", desc: "Quantity of registers", size: 2, order: "hi" },
  ];
}

/** "0A" / "000a" / "0x0A" -> a clean upper-case hex string, or null if not hex. */
export function cleanHex(v) {
  const s = String(v ?? "").trim().replace(/^0x/i, "").replace(/\s+/g, "");
  return /^[0-9a-fA-F]*$/.test(s) ? s.toUpperCase() : null;
}

/** A number as `size` bytes, high first or low first. Values too big for the
 *  size keep their LOW bytes (what a device would see anyway). */
export function toBytes(value, size, order) {
  let n = BigInt(value);
  const out = [];
  for (let i = 0; i < size; i += 1) { out.unshift(Number(n & 0xffn)); n >>= 8n; }
  return order === "lo" ? out.reverse() : out;
}

/**
 * Build the frame bytes.
 *
 * @param {object} f  {id, fc, fields:[{key, name, size, order, hex}]} where ID
 *                    is a number, FC a hex string, and each field's hex is its
 *                    value (ST / Q fields carry the node's address / quantity).
 * @returns {{bytes:number[], parts:[{name, kind, idx}], crc:number}}  the full
 *          frame INCLUDING the CRC, and which field each byte belongs to.
 */
export function buildFrame(f) {
  const bytes = [], parts = [];
  const push = (arr, name, kind) => arr.forEach((b, i) => {
    bytes.push(b);
    parts.push({ name: arr.length > 1 ? `${name} ${i + 1}` : name, kind });
  });
  push([Number(f.id) & 0xff], "ID", "locked");
  push([parseInt(cleanHex(f.fc) || "0", 16) & 0xff], "FC", "locked");
  for (const fld of f.fields || []) {
    // A block of bytes given as is (a Write's data for FC 15 / 16).
    if (Array.isArray(fld.raw)) {
      push(fld.raw, fld.name || "?", fld.key === "custom" ? "custom" : "standard");
      continue;
    }
    const hex = cleanHex(fld.hex) || "0";
    push(toBytes(BigInt(`0x${hex || "0"}`), fld.size, fld.order), fld.name || "?",
         fld.key === "custom" ? "custom" : "standard");
  }
  const crc = crc16Modbus(bytes);
  push([crc & 0xff, crc >> 8], "CRC", "locked");
  return { bytes, parts, crc };
}

export const hex2 = (b) => b.toString(16).toUpperCase().padStart(2, "0");
export const frameText = (bytes) => bytes.map(hex2).join(" ");

/** The default movable fields of a WRITE, as the standard request lays them
 *  out: FC 5 / 6 = address + value; FC 15 / 16 = address, quantity, byte
 *  count, data. `val` and `bc` are filled from the value being written. */
export function writeFields(fc) {
  const st = { key: "st", name: "ST", desc: "Starting address", size: 2, order: "hi" };
  if (Number(fc) === 15 || Number(fc) === 16) {
    return [st,
      { key: "q", name: "Q", desc: Number(fc) === 15 ? "Quantity of coils" : "Quantity of registers", size: 2, order: "hi" },
      { key: "bc", name: "BC", desc: "Byte count (auto)", size: 1, order: "hi" },
      { key: "val", name: "DATA", desc: "The values written (auto)", size: 2, order: "hi" }];
  }
  return [st, { key: "val", name: "VALUE", desc: Number(fc) === 5 ? "Coil: FF00 on, 0000 off (auto)" : "The value written (auto)", size: 2, order: "hi" }];
}

/** The data bytes a standard write of `fc` carries for `value` (repeated
 *  `quantity` times for a block). Mirrors app/buses.py modbus_write_frame. */
export function writeData(fc, value, quantity) {
  const v = Math.trunc(Number(value) || 0);
  const n = Math.max(1, Math.trunc(Number(quantity) || 1));
  if (Number(fc) === 5) return v ? [0xff, 0x00] : [0x00, 0x00];
  if (Number(fc) === 15) {
    const out = [];
    for (let k = 0; k < n; k += 8) {
      let b = 0;
      for (let i = 0; i < 8 && k + i < n; i += 1) if (v) b |= 1 << i;
      out.push(b);
    }
    return out;
  }
  const one = [(v >> 8) & 0xff, v & 0xff];
  return Number(fc) === 16 ? Array.from({ length: n }, () => one).flat() : one;
}

/** Warnings about a layout -- shown, never blocking. `write` for a Modbus Write. */
export function frameWarnings(f, write = false) {
  const out = [];
  const id = Number(f.id);
  if (id === 0) out.push("ID 0 is broadcast: every device acts on it and none replies, so a read gets no answer.");
  if (id >= 248 && id <= 255) out.push(`ID ${id} is in the reserved range (248-255).`);
  const fc = parseInt(cleanHex(f.fc) || "0", 16);
  const keys = (f.fields || []).map((x) => x.key);
  const stdFcs = write ? [5, 6, 15, 16] : [1, 2, 3, 4];
  if (stdFcs.includes(fc) && keys.includes("q") && keys.includes("st") && keys.indexOf("q") < keys.indexOf("st")) {
    out.push(`Q is placed before ST. The standard ${write ? "write" : "read"} sends the starting address first.`);
  }
  if (write && keys.includes("val") && keys.includes("st") && keys.indexOf("val") < keys.indexOf("st")) {
    out.push("The value is placed before ST. The standard write sends the address first.");
  }
  if (write && keys.includes("bc") && keys.includes("val") && keys.indexOf("bc") > keys.indexOf("val")) {
    out.push("The byte count is placed after the data. The standard write sends it just before the data.");
  }
  for (const x of f.fields || []) {
    if ((x.key === "st" || x.key === "q") && x.order === "lo") {
      out.push(`${x.name} is sent low byte first (Lo → Hi). Standard Modbus sends it high byte first.`);
    }
  }
  if (keys.includes("custom")) {
    out.push("This frame contains a manufacturer-specific field. A standard Modbus device will reject it, so check that your device actually expects it here.");
  }
  if (!write && (fc < 1 || fc > 4)) {
    out.push(`FC ${hex2(fc)} is not a standard read (01-04). The reply is still read as ${fc === 1 || fc === 2 ? "bits" : "registers"}.`);
  }
  if (write && !stdFcs.includes(fc)) {
    out.push(`FC ${hex2(fc)} is not a standard write (05, 06, 0F, 10). The data is still laid out as the node's FC writes it.`);
  }
  return out;
}

/** Is this layout the standard one (so no custom frame is needed)? */
export function isStandardLayout(fields, fcHex, nodeFc) {
  const std = standardFields();
  return parseInt(cleanHex(fcHex) || "0", 16) === Number(nodeFc)
    && fields.length === 2
    && fields.every((x, i) => x.key === std[i].key && x.size === 2 && x.order === "hi");
}
