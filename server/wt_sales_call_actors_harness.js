/**
 * Замещение обзвона ОПТ: резолв Сам/выбор по табелю без авто-чужого;
 * unlock ввода при absence + cover self/other (зеркало app.html, без БД).
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function salesRepTimesheetPresent(status) {
  return status === "Я" || status === "О";
}

function timesheetMetricsAbsenceLabel(status) {
  if (status === "В") return "В";
  if (status === "Б") return "Б";
  if (status === "ОТ") return "ОТ";
  if (status === "П") return "П";
  if (status === "Л") return "ЛО";
  return "";
}

function resolveSalesCallActor(stored, timesheetStatus) {
  if (stored && stored.mode) {
    const mode = String(stored.mode);
    if (mode === "self" || mode === "none" || mode === "other" || mode === "unset") {
      return {
        source: "stored",
        mode: mode,
        actor_id: mode === "other" ? String(stored.actor_id || "") : ""
      };
    }
  }
  if (salesRepTimesheetPresent(timesheetStatus)) {
    return { source: "derived", mode: "self", actor_id: "" };
  }
  return { source: "derived", mode: "unset", actor_id: "" };
}

/** Зеркало salesOptCoverAllowsMetricInput при isSalesOptCoverMetric === true. */
function coverAllowsMetricInput(resolved) {
  if (!resolved) return false;
  if (resolved.mode === "self") return true;
  if (resolved.mode === "other" && resolved.actor_id) return true;
  return false;
}

function inputAllowedDespiteLock(timesheetStatus, stored, isCoverMetric) {
  const lock = timesheetMetricsAbsenceLabel(timesheetStatus);
  if (!lock) return true;
  if (!isCoverMetric) return false;
  return coverAllowsMetricInput(resolveSalesCallActor(stored, timesheetStatus));
}

// «О» = опоздание = рабочий день → Сам
assert(salesRepTimesheetPresent("Я"), "Я present");
assert(salesRepTimesheetPresent("О"), "О present");
assert(!salesRepTimesheetPresent("В"), "В absent");
assert(!salesRepTimesheetPresent("ОТ"), "ОТ absent");
assert(!salesRepTimesheetPresent("Б"), "Б absent");
assert(!salesRepTimesheetPresent("П"), "П absent");
assert(!salesRepTimesheetPresent("Л"), "Л absent");
assert(!salesRepTimesheetPresent(""), "empty not present");

assert(timesheetMetricsAbsenceLabel("Л") === "ЛО", "Л → ЛО label");
assert(timesheetMetricsAbsenceLabel("Я") === "", "Я no lock");
assert(timesheetMetricsAbsenceLabel("О") === "", "О no lock");

let r = resolveSalesCallActor(null, "Я");
assert(r.source === "derived" && r.mode === "self", "Я → derived Сам");

r = resolveSalesCallActor(null, "О");
assert(r.source === "derived" && r.mode === "self", "О → derived Сам");

r = resolveSalesCallActor(null, "В");
assert(r.source === "derived" && r.mode === "unset", "В → Не указано, без авто");

r = resolveSalesCallActor(null, "ОТ");
assert(r.mode === "unset", "ОТ → unset");

r = resolveSalesCallActor(null, "Б");
assert(r.mode === "unset", "Б → unset");

r = resolveSalesCallActor(null, "П");
assert(r.mode === "unset", "П → unset");

r = resolveSalesCallActor(null, "Л");
assert(r.mode === "unset", "Л → unset");

r = resolveSalesCallActor(null, "");
assert(r.mode === "unset", "пусто → unset");

r = resolveSalesCallActor({ mode: "other", actor_id: "e_sveta" }, "Я");
assert(r.source === "stored" && r.mode === "other" && r.actor_id === "e_sveta", "store overrides Я");

r = resolveSalesCallActor({ mode: "other", actor_id: "e_sveta" }, "В");
assert(r.source === "stored" && r.actor_id === "e_sveta", "store survives timesheet В");

r = resolveSalesCallActor({ mode: "self" }, "В");
assert(r.source === "stored" && r.mode === "self", "explicit Сам in weekend kept");

r = resolveSalesCallActor({ mode: "none" }, "Б");
assert(r.mode === "none", "none kept");

// Unlock: absence + cover
["В", "Б", "ОТ", "П", "Л"].forEach(function (st) {
  assert(!inputAllowedDespiteLock(st, null, true), st + "+unset → lock");
  assert(!inputAllowedDespiteLock(st, { mode: "none" }, true), st + "+none → lock");
  assert(!inputAllowedDespiteLock(st, { mode: "other", actor_id: "" }, true), st + "+other empty → lock");
  assert(inputAllowedDespiteLock(st, { mode: "self" }, true), st + "+self → allow");
  assert(inputAllowedDespiteLock(st, { mode: "other", actor_id: "e_sveta" }, true), st + "+other → allow");
  assert(!inputAllowedDespiteLock(st, { mode: "other", actor_id: "e_sveta" }, false), st + " non-cover metric → lock");
});
assert(inputAllowedDespiteLock("Я", null, true), "Я no lock → allow");

// Per-day store: соседние дни независимы
const byDay = {
  "16": { mode: "other", actor_id: "e_sveta" },
  "17": { mode: "other", actor_id: "e_olga" },
  "18": { mode: "other", actor_id: "e_lera" }
};
assert(resolveSalesCallActor(byDay["16"], "ОТ").actor_id === "e_sveta", "16 Светлана");
assert(resolveSalesCallActor(byDay["17"], "ОТ").actor_id === "e_olga", "17 Ольга");
assert(resolveSalesCallActor(byDay["18"], "ОТ").actor_id === "e_lera", "18 Лера");
assert(resolveSalesCallActor(null, "ОТ").mode === "unset", "день без rec → unset");
assert(inputAllowedDespiteLock("ОТ", byDay["16"], true), "16 unlock");
assert(!inputAllowedDespiteLock("ОТ", null, true), "день без cover lock");

console.log("wt_sales_call_actors_harness: OK");
