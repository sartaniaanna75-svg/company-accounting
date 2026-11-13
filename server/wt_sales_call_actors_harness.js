/**
 * Замещение обзвона ОПТ: резолв Сам/выбор по табелю без авто-чужого.
 * Зеркало resolveSalesCallActor / salesRepTimesheetPresent (без БД / браузера).
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

console.log("wt_sales_call_actors_harness: OK");
