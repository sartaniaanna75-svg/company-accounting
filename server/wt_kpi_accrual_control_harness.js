/**
 * Контрольные начисления KPI: реальные функции из app.html в vm.
 * Зависимости фактов/сотрудников — синтетические stubs (не формулы).
 *
 * Режимы:
 *   (default) — таблица сценариев 1–10, JSON lines + SUMMARY
 *   WT_KPI_MODE=bonus — одна сумма: WT_KPI_PAYLOAD + WT_KPI_EMP + WT_KPI_MONTH + WT_KPI_FACTS
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "app.html"), "utf8").replace(/\r\n/g, "\n");

function extractFn(src, name) {
    const m = new RegExp("^function " + name + "\\(", "m").exec(src);
    if (!m) throw new Error("function not found: " + name);
    const end = src.indexOf("\n}\n", m.index);
    if (end < 0) throw new Error("function end not found: " + name);
    return src.slice(m.index, end + 2);
}

function extractConstLine(src, name) {
    const m = new RegExp("^const " + name + " = [^;]+;", "m").exec(src);
    if (!m) throw new Error("const not found: " + name);
    return m[0];
}

const REAL_FNS = [
    "canonicalMetricId",
    "canonicalSourceType",
    "shopPeriodMonthKey",
    "shopPeriodApplies",
    "shopKpiBudget",
    "shopKpiMaxCoef",
    "shopKpiCalcType",
    "shopKpiSourceType",
    "shopKpiControlTime",
    "shopKpiSharedFund",
    "shopPreferPrimaryWriteoffKpi",
    "shopKpiDefsCurrent",
    "shopKpiAssigned",
    "shopRuleFromKpiDef",
    "isShopTimesheet730Kpi",
    "isShopMaxLimitKpi",
    "isShopCheckKpi",
    "isShopKpiSharedBudgetEligible",
    "isShopKpiSharedBudget",
    "shopKpiPayout",
    "shopKpiSharedBudgetAllocation",
    "shopKpiSharedBudgetPayout",
    "shopEmployeeActiveRules",
    "shopEmployeeBonusLines",
    "shopEmployeeBonusTotal"
];

const REAL_CONSTS = [
    "METRIC_SHELF_LABELS_ID",
    "METRIC_WRITEOFF_LOSSES_ID",
    "SHOP_KPI_SHARED_BUDGET_MODE"
];

// shopPeriodMonthKey may be function — extract
function tryExtract(name) {
    try { return extractFn(APP, name); } catch (e) { return null; }
}

let code = "";
REAL_CONSTS.forEach(function (n) {
    try { code += extractConstLine(APP, n) + "\n"; }
    catch (e) {
        if (n === "METRIC_SHELF_LABELS_ID") code += 'const METRIC_SHELF_LABELS_ID = "metric_shelf_labels_ok";\n';
        else if (n === "METRIC_WRITEOFF_LOSSES_ID") code += 'const METRIC_WRITEOFF_LOSSES_ID = "metric_writeoff_losses";\n';
        else if (n === "SHOP_KPI_SHARED_BUDGET_MODE") code += 'const SHOP_KPI_SHARED_BUDGET_MODE = "shared_budget";\n';
    }
});

// Aliases не тянем из app.html (там зависимости на другие константы) — для контрольных метрик не нужны.
code += "const METRIC_ID_ALIASES = {};\n";

if (!tryExtract("shopPeriodMonthKey")) {
    code += "function shopPeriodMonthKey(v) { if (v == null || v === '') return ''; const s = String(v); return s.length >= 7 ? s.slice(0, 7) : s; }\n";
} else {
    code += extractFn(APP, "shopPeriodMonthKey") + "\n";
}

REAL_FNS.forEach(function (n) {
    if (n === "shopPeriodMonthKey") return;
    code += extractFn(APP, n) + "\n";
});

const prelude = `
var appData = { shopPayroll: { kpiDefs: [], assignments: [], salaries: [], receivingShared: false, sharedBudgets: {} }, workspaces: [] };
var selectedMonth = "2026-11";
var SHOP_SELF_CHECKOUT_ID = "__self_checkout__";
var WRITEOFF_KPI_NAME = "Списание/потери";
var __facts = {};
var __employees = {};

function ensureShopPayroll() { return appData.shopPayroll; }
function getEmployee(id) { return __employees[id] || { id: id, name: id }; }
function includeInMonthPayroll() { return true; }
function isSalesRep() { return false; }
function isOutsourcingEmployee() { return false; }
function shopMetricCatalogById() { return null; }
function shopKpiTemplate() { return null; }
function shopShelfParticipation() { return ""; }
function shopPickCanonicalWriteoffKpiId() { return ""; }
function shopKpiName(rule) { return (rule && rule.name) || ""; }
function shopCheckKpiPlanDays() { return 0; }
function shopCheckKpiPayout() { return { coef: 0, amount: 0 }; }
function isShopReceivingShared() { return false; }
function shopReceivingSharedPayout() { return { coef: 0, amount: 0, shared: true }; }
function shopSalaryEmployees() {
  return Object.keys(__employees).map(function (id) { return __employees[id]; });
}
function shopBonusMetricFact(rule, employeeId, month) {
  const mid = canonicalMetricId(rule && (rule.metricId || rule.metric_id));
  const key = String(employeeId) + "|" + mid;
  const row = __facts[key];
  if (row) return { fact: Number(row.fact || 0), planAuto: Number(row.planAuto || 0), label: "" };
  return { fact: 0, planAuto: 0, label: "" };
}
function shopShelfKpiDefForEmployee(employeeId, defs) { return (defs && defs[0]) || null; }
`;

function makeCtx() {
    const ctx = vm.createContext({
        console: console,
        JSON: JSON,
        Math: Math,
        Date: Date,
        Number: Number,
        String: String,
        Object: Object,
        Array: Array,
        Error: Error,
        RegExp: RegExp,
        parseInt: parseInt,
        isFinite: isFinite
    });
    vm.runInContext(prelude + "\n" + code, ctx, { filename: "wt_kpi_accrual_control.vm.js" });
    return ctx;
}

function setFacts(ctx, map) {
    vm.runInContext("__facts = " + JSON.stringify(map || {}) + ";", ctx);
}

function setEmployees(ctx, ids) {
    const emp = {};
    (ids || []).forEach(function (id) { emp[id] = { id: id, name: id }; });
    vm.runInContext("__employees = " + JSON.stringify(emp) + ";", ctx);
}

function setPayroll(ctx, payroll, month) {
    vm.runInContext(
        "appData.shopPayroll = " + JSON.stringify(payroll) + ";\n"
        + "selectedMonth = " + JSON.stringify(month || "2026-11") + ";",
        ctx
    );
}

function roundKop(n) {
    return Math.round(Number(n) * 100) / 100;
}

function runBonus(ctx, empId, month) {
    return Number(vm.runInContext(
        "shopEmployeeBonusTotal(" + JSON.stringify(empId) + ", " + JSON.stringify(month) + ")",
        ctx
    ));
}

function runPayout(ctx, rule, fact, plan, empId, month) {
    return vm.runInContext(
        "shopKpiPayout("
        + JSON.stringify(rule) + ","
        + JSON.stringify(fact) + ","
        + JSON.stringify(plan) + ","
        + JSON.stringify(empId) + ","
        + JSON.stringify(month) + ")",
        ctx
    );
}

const MODE = process.env.WT_KPI_MODE || "scenarios";

if (MODE === "bonus") {
    const payload = JSON.parse(process.env.WT_KPI_PAYLOAD || "{}");
    const emp = process.env.WT_KPI_EMP || "e1";
    const month = process.env.WT_KPI_MONTH || "2026-11";
    const facts = JSON.parse(process.env.WT_KPI_FACTS || "{}");
    const ctx = makeCtx();
    setPayroll(ctx, payload.shopPayroll || payload, month);
    setFacts(ctx, facts);
    setEmployees(ctx, Object.keys(facts).map(function (k) { return k.split("|")[0]; }).concat([emp]));
    const amount = runBonus(ctx, emp, month);
    console.log(JSON.stringify({ ok: true, amount: amount, mode: "real_functions" }));
    process.exit(0);
}

// --- scenarios ---
const results = [];
function add(name, expected, actual, note) {
    const exp = roundKop(expected);
    const act = roundKop(actual);
    const pass = exp === act;
    results.push({
        scenario: name,
        expected: exp,
        actual: act,
        pass: pass,
        engine: "real_app_html_functions",
        note: note || ""
    });
    console.log((pass ? "PASS" : "FAIL") + " | " + name + " | expected=" + exp + " | actual=" + act);
    if (!pass) {
        console.error("MISMATCH: " + name);
        process.exit(2);
    }
}

const ctx = makeCtx();
const MONTH = "2026-11";
const metric = "metric_sales_plan_ctrl";

// Base plan/budget rule (budget_ratio)
const baseRule = {
    id: "kpv_ctrl",
    kpi_id: "kpi_ctrl",
    metric_id: metric,
    metricId: metric,
    name: "Контрольный KPI",
    calcType: "budget_ratio",
    type: "plan",
    budget: 5000,
    amount: 5000,
    plan: 25000,
    maxCoefPercent: 100,
    distribution_mode: "",
    validFrom: "2026-11",
    validTo: null,
    active: true
};

setEmployees(ctx, ["e1", "e2"]);
setPayroll(ctx, { kpiDefs: [baseRule], assignments: [], salaries: [], receivingShared: false, sharedBudgets: {} }, MONTH);

// 1. 50%
{
    const pay = runPayout(ctx, baseRule, 12500, 25000, "e1", MONTH);
    add("1. План 50%", 2500, pay.amount, "shopKpiPayout budget_ratio");
}
// 2. 100%
{
    const pay = runPayout(ctx, baseRule, 25000, 25000, "e1", MONTH);
    add("2. План 100%", 5000, pay.amount, "shopKpiPayout budget_ratio");
}
// 3. Over-plan capped at 100%
{
    const pay = runPayout(ctx, baseRule, 40000, 25000, "e1", MONTH);
    add("3. Перевыполнение с cap 100%", 5000, pay.amount, "shopKpiPayout min(raw,maxCoef)");
}

// Shared budget setup
const sharedDef = {
    id: "kpv_share",
    kpi_id: "kpi_share",
    metric_id: metric,
    metricId: metric,
    name: "Общий бюджет",
    calcType: "budget_ratio",
    distribution_mode: "shared_budget",
    shared_fund: 1000,
    budget: 0,
    amount: 0,
    plan: 0,
    maxCoefPercent: 100,
    validFrom: "2026-11",
    validTo: null,
    active: true
};
const sharedAsg = [
    { id: "kpa_e1", kpi_id: "kpi_share", employee_id: "e1", validFrom: "2026-11", validTo: null, is_active: true },
    { id: "kpa_e2", kpi_id: "kpi_share", employee_id: "e2", validFrom: "2026-11", validTo: null, is_active: true }
];
setPayroll(ctx, { kpiDefs: [sharedDef], assignments: sharedAsg, salaries: [], receivingShared: false, sharedBudgets: {} }, MONTH);
const factsShare = {};
factsShare["e1|" + metric] = { fact: 30 };
factsShare["e2|" + metric] = { fact: 70 };
setFacts(ctx, factsShare);

// 4–5 shared
{
    const a1 = runPayout(ctx, sharedDef, 0, 0, "e1", MONTH);
    const a2 = runPayout(ctx, sharedDef, 0, 0, "e2", MONTH);
    add("4. Общий бюджет e1 (30/100 от 1000)", 300, a1.amount, "shopKpiSharedBudgetPayout");
    add("5. Пропорция e2 (70/100 от 1000)", 700, a2.amount, "shopKpiSharedBudgetPayout");
}

// Remainder distribution: fund 101, equal facts
{
    const def101 = Object.assign({}, sharedDef, { shared_fund: 101 });
    setPayroll(ctx, { kpiDefs: [def101], assignments: sharedAsg, salaries: [], receivingShared: false, sharedBudgets: {} }, MONTH);
    const factsEq = {};
    factsEq["e1|" + metric] = { fact: 1 };
    factsEq["e2|" + metric] = { fact: 1 };
    setFacts(ctx, factsEq);
    const a1 = runPayout(ctx, def101, 0, 0, "e1", MONTH);
    const a2 = runPayout(ctx, def101, 0, 0, "e2", MONTH);
    // floor 50+50=100, +1 by rest sort → e1 first (localeCompare)
    add("5b. Остаток копеек fund=101 (e1/e2)", 51, a1.amount, "remainder-by-rest");
    add("5c. Остаток копеек fund=101 e2", 50, a2.amount, "remainder-by-rest");
}

// 6–7 assign off/on with individual budget_ratio via bonus total
const indDef = Object.assign({}, baseRule, { id: "kpv_ind", kpi_id: "kpi_ind", plan: 10000, budget: 2000, amount: 2000 });
const asgOn = [{ id: "kpa_ind", kpi_id: "kpi_ind", employee_id: "e1", validFrom: "2026-11", validTo: null, is_active: true }];
setPayroll(ctx, { kpiDefs: [indDef], assignments: asgOn, salaries: [], receivingShared: false, sharedBudgets: {} }, MONTH);
const factsInd = {};
factsInd["e1|" + metric] = { fact: 10000, planAuto: 10000 };
setFacts(ctx, factsInd);
{
    const on = runBonus(ctx, "e1", MONTH);
    add("7. Назначение включено (100% от 2000)", 2000, on, "shopEmployeeBonusTotal");
}
setPayroll(ctx, {
    kpiDefs: [indDef],
    assignments: [{ id: "kpa_ind", kpi_id: "kpi_ind", employee_id: "e1", validFrom: "2026-11", validTo: null, is_active: false }],
    salaries: [], receivingShared: false, sharedBudgets: {}
}, MONTH);
{
    const off = runBonus(ctx, "e1", MONTH);
    add("6. Назначение отключено (is_active=false)", 0, off, "shopKpiAssigned + bonusTotal");
}
setPayroll(ctx, { kpiDefs: [indDef], assignments: asgOn, salaries: [], receivingShared: false, sharedBudgets: {} }, MONTH);
{
    const again = runBonus(ctx, "e1", MONTH);
    add("7b. Повторное включение назначения", 2000, again, "shopEmployeeBonusTotal");
}

// 8. Multiple versions — Nov sees newer
const vOld = Object.assign({}, indDef, {
    id: "kpv_v1", kpi_id: "kpi_ver", budget: 1000, amount: 1000,
    validFrom: "2026-10", validTo: "2026-10"
});
const vNew = Object.assign({}, indDef, {
    id: "kpv_v2", kpi_id: "kpi_ver", budget: 3000, amount: 3000, plan: 10000,
    validFrom: "2026-11", validTo: null
});
setPayroll(ctx, {
    kpiDefs: [vOld, vNew],
    assignments: [{ id: "kpa_ver", kpi_id: "kpi_ver", employee_id: "e1", validFrom: "2026-10", validTo: null, is_active: true }],
    salaries: [], receivingShared: false, sharedBudgets: {}
}, MONTH);
setFacts(ctx, factsInd);
{
    const nov = runBonus(ctx, "e1", "2026-11");
    add("8. Несколько версий: ноябрь = live 3000", 3000, nov, "shopKpiDefsCurrent picks latest");
    const oct = runBonus(ctx, "e1", "2026-10");
    add("8b. Октябрь = старая версия 1000", 1000, oct, "shopKpiDefsCurrent by month");
}

// 9. Close version (validTo ends before month)
const closed = Object.assign({}, indDef, {
    id: "kpv_closed", kpi_id: "kpi_closed", budget: 4000, amount: 4000,
    validFrom: "2026-09", validTo: "2026-10"
});
setPayroll(ctx, {
    kpiDefs: [closed],
    assignments: [{ id: "kpa_c", kpi_id: "kpi_closed", employee_id: "e1", validFrom: "2026-09", validTo: null, is_active: true }],
    salaries: [], receivingShared: false, sharedBudgets: {}
}, MONTH);
{
    const afterClose = runBonus(ctx, "e1", "2026-11");
    add("9. После закрытия версии (validTo=2026-10)", 0, afterClose, "shopPeriodApplies");
}

// 10. Period after KPI end
const ended = Object.assign({}, indDef, {
    id: "kpv_end", kpi_id: "kpi_end", budget: 1500, amount: 1500,
    validFrom: "2026-11", validTo: "2026-11"
});
setPayroll(ctx, {
    kpiDefs: [ended],
    assignments: [{ id: "kpa_end", kpi_id: "kpi_end", employee_id: "e1", validFrom: "2026-11", validTo: null, is_active: true }],
    salaries: [], receivingShared: false, sharedBudgets: {}
}, MONTH);
{
    const inWindow = runBonus(ctx, "e1", "2026-11");
    add("10a. В окне действия (2026-11)", 1500, inWindow, "shopEmployeeBonusTotal");
    const after = runBonus(ctx, "e1", "2026-12");
    add("10. Период после окончания (2026-12)", 0, after, "shopPeriodApplies");
}

console.log(JSON.stringify({
    ok: true,
    engine: "real_app_html_functions",
    stubs: ["shopBonusMetricFact", "getEmployee", "ensureShopPayroll", "shopMetricCatalogById"],
    results: results
}, null, 2));
console.log("wt_kpi_accrual_control_harness: OK");
