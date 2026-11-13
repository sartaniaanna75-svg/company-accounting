/**
 * Замещение обзвона ОПТ: резолв Сам/выбор по табелю без авто-чужого;
 * unlock ввода при absence + cover self/other; гейт sales_opt money|number (зеркало app.html, без БД).
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

/**
 * Зеркало isSalesOptCoverMetric: category (raw или inferred) + hosts sales + money|number.
 * Назначение конкретному ТП в UI даёт peopleOf; здесь — свойства каталога.
 */
function inferTerritoryMetricCategory(item) {
  if (!item) return "";
  if (item.id === "metric_returns_count") return "sales_opt";
  const tabs = Array.isArray(item.work_tab_ids) ? item.work_tab_ids : [];
  if (item.department_id === "drivers") {
    const hasSales = tabs.indexOf("sales") !== -1;
    const hasDrivers = tabs.indexOf("drivers") !== -1;
    if (hasSales && !hasDrivers) return "sales_opt";
    if (hasDrivers && !hasSales) return "drivers";
    if (hasSales && hasDrivers) return "common";
    return "drivers";
  }
  if (item.department_id === "warehouse") return "warehouse";
  if (item.department_id === "office") return "office";
  if (item.department_id === "shop") return "";
  return "common";
}

function metricTerritoryCategory(item) {
  if (!item) return "";
  if (item.territory_category && item.territory_category !== "returns") {
    return item.territory_category;
  }
  return inferTerritoryMetricCategory(item);
}

function metricHostsOnDeptTab(item, deptCode, tabId) {
  if (!item || !deptCode) return false;
  if (Array.isArray(item.assignments) && item.assignments.length) {
    return item.assignments.some(function (row) {
      return row && row.department_id === deptCode
        && (!tabId || !row.work_tab_id || row.work_tab_id === tabId);
    });
  }
  const cat = metricTerritoryCategory(item);
  if (deptCode === "drivers" && tabId === "sales") {
    return cat === "sales_opt";
  }
  return false;
}

function isSalesOptCoverMetric(item) {
  if (!item || item.active === false) return false;
  if (metricTerritoryCategory(item) !== "sales_opt") return false;
  if (!metricHostsOnDeptTab(item, "drivers", "sales")) return false;
  const ft = String(item.fill_type || "");
  return ft === "money" || ft === "number";
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

// Gate: money / number / returns / inferred category
const loadedMoney = {
  id: "m_loaded",
  territory_category: "sales_opt",
  fill_type: "money",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const processedNumber = {
  id: "m_processed",
  territory_category: "sales_opt",
  fill_type: "number",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const returnsCount = {
  id: "metric_returns_count",
  territory_category: "sales_opt",
  fill_type: "number",
  work_tab_ids: ["sales", "drivers"],
  department_id: "drivers",
  active: true
};
const inferredOpt = {
  id: "m_inferred",
  territory_category: "",
  fill_type: "money",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const checkMetric = {
  id: "m_check",
  territory_category: "sales_opt",
  fill_type: "check",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const autoCalc = {
  id: "m_auto",
  territory_category: "sales_opt",
  fill_type: "auto_calc",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const vehicle = {
  id: "m_vehicle",
  territory_category: "sales_opt",
  fill_type: "vehicle",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: true
};
const warehouse = {
  id: "m_wh",
  territory_category: "warehouse",
  fill_type: "number",
  work_tab_ids: ["loaders"],
  department_id: "warehouse",
  active: true
};
const driversOnly = {
  id: "m_drv",
  territory_category: "drivers",
  fill_type: "number",
  work_tab_ids: ["drivers"],
  department_id: "drivers",
  active: true
};
const inactiveOpt = {
  id: "m_off",
  territory_category: "sales_opt",
  fill_type: "money",
  work_tab_ids: ["sales"],
  department_id: "drivers",
  active: false
};
const viaAssignments = {
  id: "m_asg",
  territory_category: "",
  fill_type: "money",
  department_id: "drivers",
  work_tab_ids: ["sales"],
  assignments: [{ department_id: "drivers", work_tab_id: "sales", assign_mode: "specific", employee_ids: ["e_nat"] }],
  active: true
};

assert(isSalesOptCoverMetric(loadedMoney), "money sales_opt cover");
assert(isSalesOptCoverMetric(processedNumber), "number sales_opt cover");
assert(isSalesOptCoverMetric(returnsCount), "returns count cover");
assert(metricTerritoryCategory(inferredOpt) === "sales_opt", "inferred sales_opt");
assert(isSalesOptCoverMetric(inferredOpt), "inferred category cover");
assert(isSalesOptCoverMetric(viaAssignments), "assignments hosts sales cover");
assert(!isSalesOptCoverMetric(checkMetric), "check not cover");
assert(!isSalesOptCoverMetric(autoCalc), "auto_calc not cover");
assert(!isSalesOptCoverMetric(vehicle), "vehicle not cover");
assert(!isSalesOptCoverMetric(warehouse), "warehouse not cover");
assert(!isSalesOptCoverMetric(driversOnly), "drivers tab not cover");
assert(!isSalesOptCoverMetric(inactiveOpt), "inactive not cover");

// Facts remain on primary: cover actor не меняет empId записи
const primaryId = "e_nat";
const actorId = "e_sveta";
const factsByEmp = {};
function saveFactForPrimary(empId, day, metricId, value, cover) {
  assert(cover && (cover.mode === "self" || (cover.mode === "other" && cover.actor_id)), "need cover to save");
  if (!factsByEmp[empId]) factsByEmp[empId] = {};
  if (!factsByEmp[empId][day]) factsByEmp[empId][day] = {};
  factsByEmp[empId][day][metricId] = value;
}
saveFactForPrimary(primaryId, "14", "m_loaded", 1000, byDay["16"]);
saveFactForPrimary(primaryId, "14", "metric_returns_count", 2, { mode: "other", actor_id: actorId });
assert(factsByEmp[primaryId]["14"].m_loaded === 1000, "sum on primary");
assert(factsByEmp[primaryId]["14"].metric_returns_count === 2, "returns on primary");
assert(!factsByEmp[actorId], "actor has no facts");

console.log("wt_sales_call_actors_harness: OK");
