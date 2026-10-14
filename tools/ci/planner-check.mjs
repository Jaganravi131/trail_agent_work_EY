#!/usr/bin/env node
/* Exercise the free tool for real, in a fake DOM, and fail on anything a reader would see.
 *
 * Why this file exists: `03-tool/index.html` is the portfolio's only interactive product, and until
 * now the repo's tests checked its *markup* (the gate, the strings, the wiring) rather than running
 * its arithmetic. The README claimed nine rota patterns had been tested headlessly — a claim nobody
 * in a fresh clone could re-run. This makes the claim executable: the page's script is evaluated as
 * written, `go()` is called with real inputs, and the plan and the .ics are asserted.
 *
 * No dependencies, no network, and no DOM library: the page uses a dozen DOM calls, so a dozen
 * stubs are enough, and a stub that is *not* enough fails loudly instead of passing quietly.
 *
 *   node tools/ci/planner-check.mjs [path/to/index.html]
 */

import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";

const FILE = process.argv[2] || "03-tool/index.html";
const html = fs.readFileSync(FILE, "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) {
  console.error(`::error::${FILE}: no <script> block found — the harness and the page have diverged`);
  process.exit(1);
}
const SRC = m[1];

// ---------------------------------------------------------------- fake DOM ----
const IDS = ["start", "end", "breaks", "lights", "caff", "napok", "nap", "out", "err", "card",
             "phases", "totals", "calc", "print", "ics", "savenote"];

function el(id) {
  return {
    id, value: "", checked: false, hidden: false, textContent: "", innerHTML: "",
    _listeners: {},
    addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
    setAttribute() {}, getAttribute() { return null; },
    appendChild() {}, removeChild() {}, remove() {}, click() { this._clicked = true; },
    focus() {}, scrollIntoView() {},
    // enough of the real API surface that a page which uses more of it fails loudly here
    insertAdjacentHTML(pos, frag) { this.innerHTML = pos === "beforeend" ? this.innerHTML + frag : frag + this.innerHTML; },
    insertAdjacentText(pos, text) { this.textContent += text; },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    dataset: {}, style: {},
  };
}

function makeContext(host = "") {
  const nodes = new Map(IDS.map((id) => [id, el(id)]));
  const created = [];
  const document = {
    querySelector: (sel) => {
      const id = String(sel).replace(/^#/, "").split(/[\s.[]/)[0];
      if (!nodes.has(id)) throw new Error(`harness: the page asked for '#${id}', which the stub has no node for`);
      return nodes.get(id);
    },
    querySelectorAll: (sel) => String(sel).split(",").map((s) => document.querySelector(s.trim())),
    createElement: (tag) => { const n = el(`<${tag}>`); n.href = ""; n.download = ""; created.push(n); return n; },
    getElementById: (id) => document.querySelector("#" + id),
    body: { appendChild() {}, removeChild() {} },
    addEventListener() {},
  };
  const store = new Map();
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  const win = {
    print() { win._printed = (win._printed || 0) + 1; },
    setTimeout: (fn) => { try { fn(); } catch { /* revoked URL in a stub */ } return 0; },
    localStorage,
    Blob: class { constructor(parts) { this.size = String(parts.join("")).length; this.text = String(parts.join("")); } },
    URL: { createObjectURL: (b) => `blob:stub/${b.size}`, revokeObjectURL() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "harness" },
    alert() {}, scrollTo() {},
  };
  const location = { host, href: (host ? `https://${host}/03-tool/` : "file:///03-tool/index.html"), reload() {}, hash: "" };
  const ctx = {
    document, window: win, localStorage, location, navigator: win.navigator, setTimeout: win.setTimeout,
    Blob: win.Blob, URL: win.URL, console, Date, Math, JSON, Intl, encodeURIComponent, decodeURIComponent,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx, { filename: FILE });
  return { ctx, nodes, created };
}

// ------------------------------------------------------------------ helpers ----
let checks = 0, failures = [];
function ok(cond, label) {
  checks++;
  if (!cond) failures.push(label);
}
function eq(a, b, label) { ok(a === b, `${label}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); }

function run(inputs) {
  const { ctx, nodes, created } = makeContext();
  nodes.get("start").value = inputs.start ?? "";
  nodes.get("end").value = inputs.end ?? "";
  nodes.get("breaks").value = inputs.breaks ?? "two";
  nodes.get("lights").value = inputs.lights ?? "some";
  nodes.get("caff").value = inputs.caff ?? "mid";
  nodes.get("napok").checked = !!inputs.nap;
  // The page's own entry point, with no special-casing: whatever a click does, this does.
  vm.runInContext("go()", ctx);
  const out = nodes.get("out");
  const err = nodes.get("err");
  const plan = ctx.window.__plan || null;
  let ics = null;
  if (plan) ics = vm.runInContext("icsFor(window.__plan)", ctx);
  // the ICS button, exercised through its listener so the download wiring is covered too
  nodes.get("ics")._listeners.click?.forEach((fn) => fn());
  const rendered = [out.innerHTML, nodes.get("phases").innerHTML, nodes.get("totals").innerHTML,
                     nodes.get("card").textContent].filter(Boolean).join("\n");
  return { ctx, nodes, out, err, plan, ics, created, html: rendered, card: nodes.get("card").textContent || "" };
}

const clock = (t) => t;   // relative minutes are asserted numerically; clock strings only for format checks
const isTime = (s) => /^\d{2}:\d{2}$/.test(s);

// --------------------------------------------------------------- the patterns ----
const PATTERNS = [
  { name: "12h nights, 19:00–07:00, nap allowed", start: "19:00", end: "07:00", nap: true,
    len: 720, bed: clock(810), core: 270, total: 6 },
  { name: "12h nights, 20:00–08:00, no nap", start: "20:00", end: "08:00", nap: false,
    len: 720, bed: clock(810), core: 270, total: 4.5 },
  { name: "4-on-4-off, day 1: 22:00–06:00", start: "22:00", end: "06:00", nap: true,
    len: 480, bed: clock(570), core: 270, total: 6 },
  { name: "16h shift, 15:00–07:00", start: "15:00", end: "07:00", nap: true,
    len: 960, bed: clock(1050), core: 270, total: 6, long: true },
  { name: "day shift, 07:00–15:00", start: "07:00", end: "15:00", nap: false,
    len: 480, core: 450, total: 7.5, daytime: true },
  { name: "12h day, 06:00–18:30", start: "06:00", end: "18:30", nap: false,
    len: 750, core: 450, total: 7.5, daytime: true },
  { name: "night, end exactly at the sleep-window edge: 18:00–06:00", start: "18:00", end: "06:00", nap: true,
    len: 720, bed: clock(810), core: 270, total: 6 },
  { name: "24h on-call, 00:00–00:00", start: "00:00", end: "00:00", nap: false,
    len: 1440, bed: clock(1530), core: 270, total: 4.5 },
  { name: "low-caffeine setting, 19:00–07:00", start: "19:00", end: "07:00", nap: true, caff: "low",
    len: 720, bed: clock(810), core: 270, total: 6, ceiling: 200 },
];

for (const pat of PATTERNS) {
  const r = run(pat);
  const tag = pat.name;
  if (!r.plan) {
    failures.push(`${tag}: go() produced no plan (error was «${r.err.textContent}»)`);
    continue;
  }
  eq(r.err.textContent, "", `${tag} · go() reported an error even though it produced a plan`);
  const p = r.plan;
  eq(p.len, pat.len, `${tag} · shift length in minutes`);
  eq(p.daytime, !!pat.daytime, `${tag} · classified as day vs night`);
  if (pat.bed !== undefined) eq(p.bed, pat.bed, `${tag} · bedtime in shift-relative minutes`);
  eq(p.total, pat.total, `${tag} · planned hours of sleep`);
  if (pat.ceiling) eq(p.ceiling, pat.ceiling, `${tag} · caffeine ceiling`);

  // The invariants the tool is for.
  ok(p.tCaff <= p.bed - 120, `${tag} · last caffeine at least 2 h before bed (it was ${p.tCaff})`);
  ok(p.tFood === p.bed - 180, `${tag} · last food 3 h before bed`);
  ok(p.tLight === p.bed - 60, `${tag} · lights down 1 h before bed`);
  ok(p.wake === p.bed + p.core, `${tag} · wake = bed + core sleep`);
  ok(p.d2 <= p.tCaff, `${tag} · dose #2 never lands after the curfew`);
  ok(p.d1 <= p.d2, `${tag} · dose order`);
  ok(p.dose1mg + p.dose2mg <= p.ceiling, `${tag} · daily ceiling respected`);
  ok(p.bed > p.len, `${tag} · bed comes after clock-out`);
  if (p.nap !== null) {
    eq(p.nap, -180, `${tag} · nap starts 3 h before the shift`);
    eq(p.napEnd, -90, `${tag} · nap is 90 minutes, not 30–60 (inertia band)`);
  }
  ok([450, 270].includes(p.core), `${tag} · core sleep is one of the two planned blocks`);

  // What the reader actually sees.
  ok(r.html.length > 1200, `${tag} · the output panel has content (${r.html.length} chars)`);
  for (const bad of ["NaN", "undefined", "null", "Infinity", "invalid date"]) {
    ok(!r.html.includes(bad), `${tag} · rendered output contains «${bad}»`);
    ok(!r.card.includes(bad), `${tag} · fridge card contains «${bad}»`);
  }
  for (const phrase of ["Descent", "Hold", "Reentry", "not medical advice", "drive"]) {
    ok((r.html + r.card).toLowerCase().includes(phrase.toLowerCase()),
       `${tag} · output never says «${phrase}» — the safety framing is part of the product`);
  }
  const times = [...r.html.matchAll(/(\d{2}:\d{2})/g)].map((x) => x[1]);
  ok(times.length >= 6, `${tag} · only ${times.length} clock times rendered`);
  ok(times.every(isTime), `${tag} · a malformed clock time was rendered`);

  // The .ics file, which is the part that leaves the browser.
  const ics = r.ics || "";
  const lines = ics.split("\r\n");
  const wantEvents = p.nap === null ? 8 : 9;
  eq(lines.filter((l) => l === "BEGIN:VEVENT").length, wantEvents,
     `${tag} · VEVENT count in the .ics (a nap alarm is only exported when the plan has a nap)`);
  eq(lines.filter((l) => l === "END:VEVENT").length, lines.filter((l) => l === "BEGIN:VEVENT").length,
     `${tag} · BEGIN/END VEVENT balance`);
  ok(ics.startsWith("BEGIN:VCALENDAR") && ics.trimEnd().endsWith("END:VCALENDAR"),
     `${tag} · calendar wrapper`);
  ok(/^VERSION:2\.0$/m.test(ics) && /^CALSCALE:GREGORIAN$/m.test(ics), `${tag} · required calendar headers`);
  const dts = [...ics.matchAll(/DTSTART:(\S+)/g)].map((x) => x[1]);
  eq(dts.length, wantEvents, `${tag} · DTSTART count`);
  eq(new Set(dts).size, dts.length, `${tag} · two alarms land on the same minute — one of them is noise`);
  ok(dts.every((d) => /^\d{8}T\d{6}$/.test(d)), `${tag} · a DTSTART is not local-basic format (${dts[0]})`);
  const uids = [...ics.matchAll(/^UID:(.+)$/gm)].map((x) => x[1]);
  eq(new Set(uids).size, uids.length, `${tag} · duplicate UIDs — a calendar app deduplicates those and drops an alarm`);
  ok(uids.every((u) => u.endsWith("@shiftreset")), `${tag} · UID domain`);
  if (p.nap === null) ok(!/Anchor nap/.test(ics), `${tag} · a nap alarm was exported for a shift with no nap`);
  const longest = lines.reduce((a, l) => Math.max(a, Buffer.byteLength(l, "utf8")), 0);
  ok(longest <= 75, `${tag} · an .ics line is ${longest} bytes; RFC 5545 asks for folding at 75`);
  eq(r.created.length, 1, `${tag} · expected one download anchor to be built`);
  ok(r.created[0]?.download === "shift-reset-alarms.ics", `${tag} · download filename`);
}

// the fridge card stamps where it came from, so a printed page can be traced back to a shift
const stamped = makeContext("nightshift.example");
stamped.nodes.get("start").value = "19:00"; stamped.nodes.get("end").value = "07:00";
vm.runInContext("go()", stamped.ctx);
ok(stamped.nodes.get("card").textContent.includes("nightshift.example"),
   "fridge card · names the host it was printed from");
const unstamped = makeContext("");
unstamped.nodes.get("start").value = "19:00"; unstamped.nodes.get("end").value = "07:00";
vm.runInContext("go()", unstamped.ctx);
ok(/local copy/i.test(unstamped.nodes.get("card").textContent),
   "fridge card · says so when opened as a file rather than a website");

// ------------------------------------------------------- the failure surfaces ----
const blank = run({ start: "", end: "07:00" });
eq(blank.err.textContent, "Set both a start and an end time.", "blank start · human-readable message");
ok(blank.out.hidden, "blank start · no stale output left on screen");
eq(blank.plan, null, "blank start · no plan object");

const tiny = run({ start: "19:00", end: "21:00" });
ok(/^A shift under 3 hours/.test(tiny.err.textContent), `short shift · message was «${tiny.err.textContent}»`);
ok(!/NaN/.test(tiny.err.textContent), "short shift · the error is prose, not a number");

const garbage = run({ start: "banana", end: "07:00" });
ok(garbage.err.textContent.length > 0, "unparseable input · nothing thrown to the console in silence");

const backwards = run({ start: "07:00", end: "19:00" });   // same two clock times, reversed
ok(backwards.plan?.len === 720, `reversed times · expected the 12 h that spans midnight, got ${backwards.plan?.len}`);

// persistence: the page writes state to localStorage on a successful calc
const { ctx: c2, nodes: n2 } = makeContext();
n2.get("start").value = "20:00"; n2.get("end").value = "06:00";
n2.get("breaks").value = "short"; n2.get("lights").value = "none"; n2.get("caff").value = "high";
vm.runInContext("go()", c2);
const saved = JSON.parse(c2.window.localStorage.getItem("srp.v1") || "null");
ok(saved, "localStorage · a successful calculation must persist the state");
eq(saved?.start, "20:00", "localStorage · the shift round-trips");
eq(saved?.end, "06:00", "localStorage · end round-trips");
ok(["none", "short", "two"].includes(saved?.breaks), `localStorage · breaks stored as a value, got ${JSON.stringify(saved?.breaks)}`);
eq(saved?.lights, "none", "localStorage · the blackout setting round-trips");
eq(saved?.caff, "high", "localStorage · the caffeine setting round-trips");

// the change-listeners exist, so editing a field after a calc re-runs it
const { nodes: n3, ctx: c3 } = makeContext();
for (const id of ["start", "end", "breaks", "lights", "caff", "napok"]) {
  ok((n3.get(id)._listeners.change || []).length > 0, `wiring · #${id} has no change listener`);
}
ok((n3.get("calc")._listeners.click || []).length === 1, "wiring · the Calculate button is bound once");

console.log(`planner-check: ${PATTERNS.length} rota patterns + failure surfaces · ${checks} assertions`);
if (failures.length) {
  console.error(`\n${failures.length} FAILURE(S):`);
  for (const f of failures.slice(0, 40)) console.error("  · " + f);
  process.exit(1);
}
console.log("ALL GOOD — the tool's arithmetic, rendering, .ics export, error paths and wiring hold up");
