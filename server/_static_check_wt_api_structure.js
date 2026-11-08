/**
 * Static structure check for wt_api.py without Python interpreter.
 * Not a substitute for python -m unittest / py_compile.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "wt_api.py");
const s = fs.readFileSync(file, "utf8");
const lines = s.split(/\r?\n/);
const need = [
  "def _salary_record_meaningful",
  "def _access_list_meaningful",
  "def _vacation_plan_row_meaningful",
  "def _metric_emp_payload_meaningful",
  "def employee_linked_history_labels",
  "_access_list_meaningful(mmap.get(eid))",
  "_salary_record_meaningful(bucket.get(eid))",
  "_vacation_plan_row_meaningful(row)"
];
for (let i = 0; i < need.length; i++) {
  if (s.indexOf(need[i]) === -1) {
    console.error("MISSING " + need[i]);
    process.exit(1);
  }
}
// Rough bracket balance outside strings/comments.
const stack = [];
let inTrip = null;
let inStr = null;
let esc = false;
for (let li = 0; li < lines.length; li++) {
  const line = lines[li];
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    const two = line.slice(i, i + 2);
    const three = line.slice(i, i + 3);
    if (inTrip) {
      if (three === inTrip) {
        inTrip = null;
        i += 2;
      }
      continue;
    }
    if (inStr) {
      if (esc) {
        esc = false;
        continue;
      }
      if (ch === "\\") {
        esc = true;
        continue;
      }
      if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === "#") break;
    if ((ch === '"' || ch === "'") && two === ch + ch && line[i + 2] === ch) {
      inTrip = ch + ch + ch;
      i += 2;
      continue;
    }
    if (ch === '"' || ch === "'") {
      inStr = ch;
      continue;
    }
    if (ch === "(" || ch === "[" || ch === "{") stack.push({ ch: ch, line: li + 1 });
    if (ch === ")" || ch === "]" || ch === "}") {
      const want = ch === ")" ? "(" : ch === "]" ? "[" : "{";
      const top = stack.pop();
      if (!top || top.ch !== want) {
        console.error("BRACKET_MISMATCH at line " + (li + 1));
        process.exit(1);
      }
    }
  }
}
if (stack.length) {
  console.error("UNCLOSED " + stack[stack.length - 1].ch + " from line " + stack[stack.length - 1].line);
  process.exit(1);
}
console.log("STATIC_PY_STRUCTURE_OK lines=" + lines.length + " (not py_compile / not unittest)");
