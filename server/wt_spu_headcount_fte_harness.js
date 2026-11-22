/**
 * FTE-численность СПУ (headcount): Я/О / рабочие дни, выходные из знаменателя,
 * воскресенье Территории, аутсорсинг, округление итога.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function pad(n) {
  return n < 10 ? "0" + n : String(n);
}

function isoFromParts(y, m, d) {
  return y + "-" + pad(m) + "-" + pad(d);
}

/** Чт 19.11.2026 – ср 25.11.2026 */
function weekDays() {
  const start = new Date(2026, 10, 19, 12, 0, 0);
  const out = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i, 12, 0, 0);
    const iso = isoFromParts(d.getFullYear(), d.getMonth() + 1, d.getDate());
    out.push({
      iso: iso,
      month: iso.slice(0, 7),
      day: d.getDate(),
      weekday: d.getDay()
    });
  }
  return out;
}

function dayWeekday(d) {
  if (d.weekday != null) return d.weekday;
  const p = String(d.iso).split("-").map(Number);
  return new Date(p[0], p[1] - 1, p[2], 12, 0, 0).getDay();
}

function isScheduleWeekend(deptCode, dayInfo, status) {
  if (String(status || "") === "В") return true;
  if (["office", "warehouse", "drivers"].indexOf(deptCode) !== -1 && dayWeekday(dayInfo) === 0) {
    return true;
  }
  return false;
}

function isWorked(st) {
  return st === "Я" || st === "О";
}

function isStaffMark(st) {
  return st === "Я" || st === "О" || st === "ОТ" || st === "Б" || st === "Л" || st === "П";
}

function peek(w, empId, day, month) {
  const rec = w && w.timesheet && w.timesheet[month] && w.timesheet[month][empId]
    && w.timesheet[month][empId][day];
  return rec || { status: "" };
}

function fteShare(w, empId, deptCode, days, firedOn) {
  let sched = 0;
  let worked = 0;
  days.forEach(function (d) {
    const iso = String(d.iso).slice(0, 10);
    if (firedOn && iso >= firedOn) return;
    const st = peek(w, empId, d.day, d.month).status || "";
    if (isScheduleWeekend(deptCode, d, st)) return;
    sched += 1;
    if (isWorked(st)) worked += 1;
  });
  if (sched <= 0) return null;
  return worked / sched;
}

function hasStaffMarks(w, empId, days) {
  for (let i = 0; i < days.length; i++) {
    const d = days[i];
    if (isStaffMark(peek(w, empId, d.day, d.month).status || "")) return true;
  }
  return false;
}

function isActive(e, weekEnd) {
  if (!e || e.id === "self_checkout") return false;
  const fo = e.fired_on ? String(e.fired_on).slice(0, 10) : "";
  if (fo) return fo > weekEnd;
  return e.jobStatus !== "fired";
}

function headcountEquivalent(workspaces, deptCode, days) {
  const codes = deptCode
    ? [deptCode]
    : ["shop", "office", "warehouse", "drivers"];
  const weekEnd = days.length ? String(days[days.length - 1].iso).slice(0, 10) : "";
  const seen = {};
  let sum = 0;
  codes.forEach(function (code) {
    const w = workspaces[code];
    if (!w) return;
    const territory = code !== "shop";
    (w.employees || []).forEach(function (e) {
      if (!e || !e.id || seen[e.id] || e.id === "self_checkout") return;
      seen[e.id] = true;
      const outsrc = e.outsourcing === true;
      const marks = hasStaffMarks(w, e.id, days);
      if (outsrc && territory && !marks) {
        if (isActive(e, weekEnd)) sum += 1;
        return;
      }
      const share = fteShare(w, e.id, code, days, e.fired_on ? String(e.fired_on).slice(0, 10) : "");
      if (share != null) sum += share;
    });
  });
  return Math.round(sum);
}

function fillWeek(w, empId, statusByIso) {
  if (!w.timesheet) w.timesheet = {};
  weekDays().forEach(function (d) {
    if (!w.timesheet[d.month]) w.timesheet[d.month] = {};
    if (!w.timesheet[d.month][empId]) w.timesheet[d.month][empId] = {};
    const st = statusByIso[d.iso] != null ? statusByIso[d.iso] : "";
    w.timesheet[d.month][empId][d.day] = { status: st };
  });
}

function mapStatuses(pattern) {
  /* pattern: array of 7 statuses Thu..Wed */
  const days = weekDays();
  const o = {};
  days.forEach(function (d, i) {
    o[d.iso] = pattern[i] || "";
  });
  return o;
}

const days = weekDays();
assert(days[0].weekday === 4, "thu");
assert(days[6].weekday === 3, "wed");
assert(days[3].weekday === 0, "sun");

// 1 full shop week
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Я", "Я", "Я", "Я", "Я"]));
  assert(headcountEquivalent({ shop: w }, "shop", days) === 1, "1 full");
}

// 2 schedule weekends В do not reduce
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "В"]));
  assert(headcountEquivalent({ shop: w }, "shop", days) === 1, "2 weekends");
}

// 3 vacation reduces
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Я", "В", "ОТ", "Я", "В"]));
  /* sched=5, worked=4 → 0.8 → round 1 alone; use two people for clear fraction later */
  const share = fteShare(w, "a", "shop", days, "");
  assert(Math.abs(share - 0.8) < 1e-9, "3 vac share " + share);
}

// 4 sick
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Б", "В", "Я", "Я", "В"]));
  assert(Math.abs(fteShare(w, "a", "shop", days, "") - 0.8) < 1e-9, "4 sick");
}

// 5 personal
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Л", "Я", "В", "Я", "Я", "В"]));
  assert(Math.abs(fteShare(w, "a", "shop", days, "") - 0.8) < 1e-9, "5 personal");
}

// 6 skip
{
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "П", "В", "Я", "Я", "В"]));
  assert(Math.abs(fteShare(w, "a", "shop", days, "") - 0.8) < 1e-9, "6 skip");
}

// 7 fired mid-week: fired_on = Monday 23.11 → дни до увольнения считаются
{
  const w = {
    employees: [{ id: "a", jobStatus: "fired", fired_on: "2026-11-23" }],
    timesheet: {}
  };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "Я"]));
  /* Thu–Sat: В в вс/сб? pattern: Я Я Я В | mon+ → fo cuts; sched Thu Fri (Sat В) = 2 Я → 1 */
  assert(Math.abs(fteShare(w, "a", "shop", days, "2026-11-23") - 1) < 1e-9, "7 fo share");
  assert(headcountEquivalent({ shop: w }, "shop", days) === 1, "7 fired mid-week counts days before");
}

// 8–9 rounding
assert(Math.round(12.49) === 12, "8");
assert(Math.round(12.5) === 13, "9");
{
  const emps = [];
  const w = { employees: emps, timesheet: {} };
  /* 12 people at 1.0 + one at 0.49 → 12.49 → 12 */
  for (let i = 0; i < 12; i++) {
    const id = "p" + i;
    emps.push({ id: id, jobStatus: "working" });
    fillWeek(w, id, mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "В"]));
  }
  emps.push({ id: "frac", jobStatus: "working" });
  /* 0.49 ≈ need worked/sched; 49/100 not fit week; use 2/5=0.4 and adjust — use raw Math.round path via sum */
  /* 12 * 1 + 0.49 simulated: one employee 2/5=0.4 + another — simpler assert Math.round only above */
  fillWeek(w, "frac", mapStatuses(["Я", "Я", "ОТ", "В", "ОТ", "ОТ", "В"])); /* 2/5=0.4 */
  assert(headcountEquivalent({ shop: w }, "shop", days) === 12, "8 sum 12.4→12");
}
{
  const emps = [];
  const w = { employees: emps, timesheet: {} };
  for (let i = 0; i < 12; i++) {
    const id = "q" + i;
    emps.push({ id: id, jobStatus: "working" });
    fillWeek(w, id, mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "В"]));
  }
  emps.push({ id: "half", jobStatus: "working" });
  fillWeek(w, "half", mapStatuses(["Я", "Я", "Я", "В", "ОТ", "ОТ", "В"])); /* 3/5=0.6 → 12.6→13 */
  assert(headcountEquivalent({ shop: w }, "shop", days) === 13, "9 sum 12.6→13");
}

// 10 territory Sunday not absence
{
  const w = { employees: [{ id: "t", jobStatus: "working" }], timesheet: {} };
  /* Sun empty, Sat В, weekdays Я */
  fillWeek(w, "t", mapStatuses(["Я", "Я", "Я", "", "Я", "Я", "В"]));
  assert(Math.abs(fteShare(w, "t", "office", days, "") - 1) < 1e-9, "10 sun");
  assert(headcountEquivalent({ office: w }, "office", days) === 1, "10 office");
}

// 11 outsourcing no marks → 1
{
  const w = {
    employees: [{ id: "o", jobStatus: "working", outsourcing: true }],
    timesheet: {}
  };
  assert(headcountEquivalent({ office: w }, "office", days) === 1, "11 out");
}

// 12 fired outsourcing
{
  const w = {
    employees: [{ id: "o", jobStatus: "fired", fired_on: "2026-11-01", outsourcing: true }],
    timesheet: {}
  };
  assert(headcountEquivalent({ office: w }, "office", days) === 0, "12 out fired");
}

// 13–14 no double across workspaces / company
{
  const shop = {
    employees: [{ id: "x", jobStatus: "working" }],
    timesheet: {}
  };
  const office = {
    employees: [{ id: "x", jobStatus: "working" }],
    timesheet: {}
  };
  fillWeek(shop, "x", mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "В"]));
  fillWeek(office, "x", mapStatuses(["Я", "Я", "Я", "", "Я", "Я", "В"]));
  assert(headcountEquivalent({ shop: shop, office: office }, "", days) === 1, "14 company dedupe");
  assert(headcountEquivalent({ shop: shop }, "shop", days) === 1, "13 shop");
}

// 15 week switch: empty other week → 0
{
  const other = [];
  const start = new Date(2026, 10, 12, 12, 0, 0);
  for (let i = 0; i < 7; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i, 12, 0, 0);
    const iso = isoFromParts(d.getFullYear(), d.getMonth() + 1, d.getDate());
    other.push({ iso: iso, month: iso.slice(0, 7), day: d.getDate(), weekday: d.getDay() });
  }
  const w = { employees: [{ id: "a", jobStatus: "working" }], timesheet: {} };
  fillWeek(w, "a", mapStatuses(["Я", "Я", "Я", "В", "Я", "Я", "В"]));
  assert(headcountEquivalent({ shop: w }, "shop", days) === 1, "15 this week");
  assert(headcountEquivalent({ shop: w }, "shop", other) === 0, "15 other week empty marks");
}

console.log("wt_spu_headcount_fte_harness: OK");
