/**
 * Эквивалентность финансовых расчётов: одна и та же последовательность действий на одинаковых
 * искусственных данных в базовой (WT_FR_BASE, напр. HEAD) и текущей (WT_FR_CUR) версиях fr-mod.js.
 * Локальный режим (wtServerMode=false, saveApp → true): сравниваются только данные и расчёты,
 * не транспорт сохранения. Id и отметки времени канонизируются.
 * Запускается из server/test_fr_calc_equivalence.py.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "app.html"), "utf8").replace(/\r\n/g, "\n");
const BASE_FILE = process.env.WT_FR_BASE;
const CUR_FILE = process.env.WT_FR_CUR || path.join(ROOT, "fr-mod.js");
const SCENARIO = process.env.WT_FR_SCENARIO || "transits";

function extractFn(src, name) {
    const m = new RegExp("^function " + name + "\\(", "m").exec(src);
    if (!m) throw new Error("function not found: " + name);
    const end = src.indexOf("\n}\n", m.index);
    return src.slice(m.index, end + 2);
}

const APP_FNS = [
    "nextPrefixedId", "nextPrefixedUniqueId", "mgmtIsoFromDate", "mgmtParseIso", "mgmtSpuWeekStartDay",
    "mgmtSpuReportingWeek", "mgmtSpuShiftReportingWeek"
];

const prelude = `
var appData = null;
var wtServerMode = false;
var wtLastLoadedPayload = null;
var currentSection = "financeRec";
var __form = {};
var __promptAnswer = "";
var __toasts = [];
var __rnd = 12345;
Math.random = function () { __rnd = (__rnd * 1103515245 + 12345) % 2147483648; return __rnd / 2147483648; };
var document = {
    getElementById: function (id) { return Object.prototype.hasOwnProperty.call(__form, id) ? { value: __form[id] } : null; },
    querySelectorAll: function () { return []; }
};
var window = { confirm: function () { return true; } };
function prompt() { return __promptAnswer; }
function confirm() { return true; }
function mgmtPad(n) { return String(n).padStart(2, "0"); }
function toast(msg, kind) { __toasts.push({ msg: String(msg), kind: kind || "" }); }
function saveApp() { return Promise.resolve(true); }
function closeMgmtModal() {}
function openMgmtModal() {}
function escapeHtml(s) { return String(s == null ? "" : s); }
function escapeAttribute(s) { return String(s == null ? "" : s); }
function currentAuthUserId() { return "user_admin"; }
function isAppAdmin() { return true; }
function wtRecordVersion() { return 0; }
function wtSoftMarkDirty() {}
function wtSoftClearDirty() {}
`;

function makeCtx(frFile) {
    const fr = fs.readFileSync(frFile, "utf8").replace(/\r\n/g, "\n");
    const ctx = vm.createContext({ console, setTimeout, clearTimeout, Promise, JSON, Math, Date, Number, String, Object, Array, Error, RegExp });
    vm.runInContext(prelude + APP_FNS.map(function (n) { return extractFn(APP, n); }).join("\n"), ctx, { filename: "prelude.js" });
    vm.runInContext(fr, ctx, { filename: path.basename(frFile) });
    // Права и активный объект — фиксированы (проверяются расчёты, не доступ); отрисовка DOM не нужна.
    vm.runInContext(`
        frCan = function () { return true; };
        frActiveArea = function () { return appData.financeRec.areas[0]; };
        renderFinanceRec = function () {};
    `, ctx);
    return ctx;
}

const AREA = "farea_shop";
const WEEK_START = "2026-11-05";

function seed() {
    return {
        users: [],
        financeRec: {
            areas: [{ finance_area_id: AREA, object_id: AREA, name: "Магазин Володарского", sort_order: 10, is_active: true, primary_cash_source_id: "fsrc_cash" }],
            sources: [
                { finance_source_id: "fsrc_cash", account_id: "fsrc_cash", finance_area_id: AREA, object_id: AREA, name: "Основная касса", account_type: "cash", is_active: true, sort_order: 1 },
                { finance_source_id: "fsrc_cash2", account_id: "fsrc_cash2", finance_area_id: AREA, object_id: AREA, name: "Касса 2", account_type: "cash", is_active: true, sort_order: 2, in_actual_balance: false },
                { finance_source_id: "fsrc_bank", account_id: "fsrc_bank", finance_area_id: AREA, object_id: AREA, name: "Р/с", account_type: "bank", is_active: true, sort_order: 3 },
                { finance_source_id: "fsrc_card", account_id: "fsrc_card", finance_area_id: AREA, object_id: AREA, name: "Карта", account_type: "card", is_active: true, sort_order: 4 },
                { finance_source_id: "fsrc_transit", account_id: "fsrc_transit", finance_area_id: AREA, object_id: AREA, name: "Деньги в пути", account_type: "in_transit", is_active: true, sort_order: 5 }
            ],
            categories: [],
            operations: [
                { finance_operation_id: "fop_seed1", object_id: AREA, finance_area_id: AREA, account_id: "fsrc_cash", finance_source_id: "fsrc_cash", date: "2026-11-05", type: "in", op_type: "income", movement_kind: "income", amount: 5000, is_deleted: false },
                { finance_operation_id: "fop_seed2", object_id: AREA, finance_area_id: AREA, account_id: "fsrc_bank", finance_source_id: "fsrc_bank", date: "2026-11-06", type: "out", op_type: "expense", movement_kind: "expense", amount: 300, is_deleted: false },
                { finance_operation_id: "fop_prev1", object_id: AREA, finance_area_id: AREA, account_id: "fsrc_cash", finance_source_id: "fsrc_cash", date: "2026-10-30", type: "in", op_type: "income", movement_kind: "income", amount: 1200, is_deleted: false }
            ],
            money_transits: [],
            openings: [
                { finance_area_id: AREA, object_id: AREA, period_key: "week:2026-10-29", finance_source_id: "fsrc_cash", account_id: "fsrc_cash", value: 1000, is_manual: true },
                { finance_area_id: AREA, object_id: AREA, period_key: "week:2026-10-29", finance_source_id: "fsrc_bank", account_id: "fsrc_bank", value: 2000, is_manual: true }
            ],
            day_closings: [],
            period_states: [],
            formulas: [],
            audit: [],
            import_batches: []
        }
    };
}

/* Старые данные: строка статуса только с object_id и транзит без transit_id. */
function legacySeed() {
    const s = seed();
    s.financeRec.period_states.push({ object_id: AREA, period_key: "week:" + WEEK_START, status: "closed", closed_by: "old_user" });
    s.financeRec.money_transits.push({ object_id: AREA, from_account_id: "fsrc_cash", transit_account_id: "fsrc_transit", amount_sent: 100, amount_closed: 0, send_date: "2026-11-05", comment: "старый" });
    return s;
}

const TS_KEYS = { created_at: 1, updated_at: 1, deleted_at: 1, at: 1, closed_at: 1 };
const ID_RE = /^(ftr|fop|faud|ftc|fdc|fper)_\d{3}(_[a-z0-9]+)?$/;

/** Канонизация: id по порядку первого появления, без отметок времени. */
function canon(value) {
    const map = {};
    const count = {};
    function id(s) {
        if (!Object.prototype.hasOwnProperty.call(map, s)) {
            const p = s.split("_")[0];
            count[p] = (count[p] || 0) + 1;
            map[s] = "<" + p + count[p] + ">";
        }
        return map[s];
    }
    function walk(v) {
        if (Array.isArray(v)) return v.map(walk);
        if (v && typeof v === "object") {
            const out = {};
            Object.keys(v).sort().forEach(function (k) {
                if (TS_KEYS[k]) return;
                out[k] = walk(v[k]);
            });
            return out;
        }
        if (typeof v === "string" && ID_RE.test(v)) return id(v);
        if (typeof v === "string") return v.replace(/(ftr|fop|faud|ftc|fdc|fper)_\d{3}(_[a-z0-9]+)?/g, function (m) { return id(m); });
        return v;
    }
    return walk(value);
}

function metrics(ctx) {
    return vm.runInContext(`(function () {
        const a = "${AREA}";
        const out = {};
        ["week:2026-10-29", "week:2026-11-05", "week:2026-11-12"].forEach(function (pk) {
            const w = frWeek(pk.replace(/^week:/, ""));
            const m = {};
            m.areaTotals = frAreaTotals(a, pk);
            m.weekCalculated = frWeekCalculated(a, pk);
            m.weekActualClosing = frWeekActualClosing(a, pk);
            m.weekFactCheck = frWeekFactCheck(a, pk);
            m.mainCashWeek = frMainCashWeekCalc(a, pk);
            m.mainCashDays = frMainCashDayRows(a, pk);
            m.dividendWeek = frDividendSumForWeek(a, pk);
            m.cashDividendWeek = frCashDividendSumForWeek(a, pk);
            m.cardTransitSum = frCardTransitSum(a, pk);
            m.weekFactMoneyTotal = frWeekFactMoneyTotal(a, pk);
            m.sources = {};
            m.days = {};
            frSources(a, true).forEach(function (s) {
                const sid = s.finance_source_id;
                m.sources[sid] = {
                    totals: frSourceTotals(a, pk, sid),
                    incomingOpening: frWeekIncomingOpening(a, pk, sid),
                    carry: frCarryClosingValue(a, pk, w.end_date, sid)
                };
                frWeekDates(w).forEach(function (d) {
                    m.days[sid + "@" + d] = frDayCalculated(a, pk, d, sid);
                });
            });
            out[pk] = m;
        });
        out.transitOpenSum = frTransitOpenSum(a);
        out.transitJournal = frTransitJournalRows(a);
        return JSON.parse(JSON.stringify(out));
    })()`, ctx);
}

function snapshot(ctx) {
    const fr = vm.runInContext("JSON.parse(JSON.stringify(appData.financeRec))", ctx);
    // Брошенная пустая операция отклонённого перевода (is_deleted без deleted_by) — в расчёт не входит;
    // этап 4А её больше не создаёт. Удаления пользователем (deleted_by) сравниваются.
    const abandoned = (fr.operations || []).filter(function (o) { return o && o.is_deleted && !o.deleted_by; }).length;
    return canon({
        abandoned_deleted_ops: abandoned,
        operations: (fr.operations || []).filter(function (o) { return !(o && o.is_deleted && !o.deleted_by); }),
        money_transits: fr.money_transits,
        openings: fr.openings,
        day_closings: fr.day_closings,
        period_states: fr.period_states,
        audit: fr.audit,
        metrics: metrics(ctx)
    });
}

function settle() { return new Promise(function (r) { setTimeout(r, 0); }); }

async function call(ctx, form, expr, promptAnswer) {
    ctx.__form = form || {};
    ctx.__promptAnswer = promptAnswer || "";
    const ret = vm.runInContext(expr, ctx);
    await Promise.resolve(ret);
    await settle();
    await settle();
}

function findId(ctx, expr) { return vm.runInContext(expr, ctx); }

/* Побочные изменения данных настоящей renderFinanceRec (без DOM): перенос текущей недели и строка статуса. */
const RENDER_EFFECTS = "(function () { const a = frActiveArea(); const w = frEnsureWeek();"
    + " frCarryIfNeeded(a.finance_area_id, w.period_key); frPeriodState(a.finance_area_id, w.period_key); })()";

/** Шаги сценария: { name, run(ctx) }. Одинаковые для обеих версий. */
const SCENARIOS = {
    transits: [
        { name: "send_700", run: function (c) { return call(c, { frTsFrom: "fsrc_cash", frTsAmt: "700", frTsDate: "2026-11-06", frTsCom: "в банк" }, "saveFrTransitSend()"); } },
        { name: "send_500", run: function (c) { return call(c, { frTsFrom: "fsrc_cash", frTsAmt: "500", frTsDate: "2026-11-07", frTsCom: "второй" }, "saveFrTransitSend()"); } },
        { name: "close_partial_300", run: function (c) {
            const tid = findId(c, "appData.financeRec.money_transits[0].transit_id");
            return call(c, { frTcId: tid, frTcTo: "fsrc_bank", frTcAmt: "300", frTcDate: "2026-11-08", frTcCom: "" }, "saveFrTransitClose()");
        } },
        { name: "close_full_rest_of_second", run: function (c) {
            const tid = findId(c, "appData.financeRec.money_transits[1].transit_id");
            return call(c, { frTcId: tid, frTcTo: "fsrc_card", frTcAmt: "500", frTcDate: "2026-11-09", frTcCom: "" }, "saveFrTransitClose()");
        } },
        { name: "edit_send_below_received", run: function (c) {
            const opId = findId(c, "appData.financeRec.money_transits[0].send_operation_id");
            return call(c, { frTrId: opId, frTrFromModal: "fsrc_cash", frTrToModal: "fsrc_transit", frTrAmtModal: "200", frTrDate: "2026-11-06", frTrComModal: "в банк" }, "saveFrTransfer()");
        } },
        { name: "quick_transfer_cash_bank", run: function (c) {
            return call(c, { "frTrFrom_2026-11-07": "fsrc_cash", "frTrTo_2026-11-07": "fsrc_bank", "frTrAmt_2026-11-07": "50", "frTrCom_2026-11-07": "" }, "frQuickAddTransfer('2026-11-07')");
        } },
        { name: "quick_transfer_to_transit", run: function (c) {
            return call(c, { "frTrFrom_2026-11-08": "fsrc_bank", "frTrTo_2026-11-08": "fsrc_transit", "frTrAmt_2026-11-08": "120", "frTrCom_2026-11-08": "" }, "frQuickAddTransfer('2026-11-08')");
        } },
        { name: "quick_transfer_from_transit_refused", run: function (c) {
            return call(c, { "frTrFrom_2026-11-08": "fsrc_transit", "frTrTo_2026-11-08": "fsrc_cash", "frTrAmt_2026-11-08": "10", "frTrCom_2026-11-08": "" }, "frQuickAddTransfer('2026-11-08')");
        } },
        { name: "dividend_80", run: function (c) {
            return call(c, { frDivId: "", frDivAccount: "fsrc_cash", frDivAmt: "80", frDivDate: "2026-11-09", frDivCom: "дивиденды" }, "saveFrDividend()");
        } },
        { name: "dividend_edit_95", run: function (c) {
            const id = findId(c, "appData.financeRec.operations.filter(function (o) { return o.movement_kind === 'dividend' || o.op_type === 'dividend'; })[0].finance_operation_id");
            return call(c, { frDivId: id, frDivAccount: "fsrc_cash", frDivAmt: "95", frDivDate: "2026-11-09", frDivCom: "дивиденды" }, "saveFrDividend()");
        } },
        { name: "delete_send_with_receipts_refused", run: function (c) {
            const opId = findId(c, "appData.financeRec.money_transits[1].send_operation_id");
            return call(c, {}, "frDeleteOp(" + JSON.stringify(opId) + ")");
        } },
        { name: "delete_close_op", run: function (c) {
            const opId = findId(c, "appData.financeRec.money_transits[0].closes[0].operation_id");
            return call(c, {}, "frDeleteOp(" + JSON.stringify(opId) + ")");
        } },
        { name: "delete_send_without_receipts", run: function (c) {
            const opId = findId(c, "appData.financeRec.money_transits[2].send_operation_id");
            return call(c, {}, "frDeleteOp(" + JSON.stringify(opId) + ")");
        } }
    ],
    // Этап 4Б: остатки, фактические остатки, статусы, перенос; побочные изменения отрисовки.
    balances: [
        { name: "render_week", run: function (c) { return call(c, {}, RENDER_EFFECTS); } },
        { name: "day_actual_cash", run: function (c) { return call(c, {}, "frSaveDayActual('fsrc_cash', '2026-11-06', '5800')"); } },
        { name: "day_actual_bank_week_end", run: function (c) { return call(c, {}, "frSaveDayActual('fsrc_bank', frEnsureWeek().end_date, '1700')"); } },
        { name: "week_actual_cash", run: function (c) { return call(c, {}, "frSaveWeekActualBalance('fsrc_cash', '6100')"); } },
        { name: "week_actual_cash2_refused", run: function (c) { return call(c, {}, "frSaveWeekActualBalance('fsrc_cash2', '10')"); } },
        { name: "opening_bank_edit", run: function (c) { return call(c, {}, "frSaveOpening('fsrc_bank', '2100')", "корректировка"); } },
        { name: "opening_cash2_refused", run: function (c) { return call(c, {}, "frSaveOpening('fsrc_cash2', '50')", "x"); } },
        { name: "opening_no_reason_cancelled", run: function (c) { return call(c, {}, "frSaveOpening('fsrc_bank', '2200')", ""); } },
        { name: "status_filled", run: function (c) { return call(c, {}, "frSetStatus('filled')"); } },
        { name: "status_review", run: function (c) { return call(c, {}, "frSetStatus('review')"); } },
        { name: "status_reconciled", run: function (c) { return call(c, {}, "frSetStatus('reconciled')"); } },
        { name: "push_carry", run: function (c) { return call(c, {}, "frPushCarry()"); } },
        { name: "close_period_forced", run: function (c) { return call(c, {}, "frClosePeriod()", "принудительно"); } },
        { name: "render_after_close", run: function (c) { return call(c, {}, RENDER_EFFECTS); } },
        { name: "reopen_period", run: function (c) { return call(c, {}, "frReopenPeriod()", "исправить"); } },
        { name: "day_actual_clear", run: function (c) { return call(c, {}, "frSaveDayActual('fsrc_cash', '2026-11-06', '')"); } },
        { name: "render_next_week", run: function (c) { return call(c, {}, "frUi.weekIso = '2026-11-12'; " + RENDER_EFFECTS); } },
        { name: "opening_next_cash_manual", run: function (c) { return call(c, {}, "frSaveOpening('fsrc_cash', '7000')", "сверка"); } },
        { name: "render_next_week_again", run: function (c) { return call(c, {}, RENDER_EFFECTS); } },
        { name: "back_to_week", run: function (c) { return call(c, {}, "frUi.weekIso = '2026-11-05'; " + RENDER_EFFECTS); } }
    ]
};
SCENARIOS.balances_legacy = SCENARIOS.balances;

async function runVersion(file) {
    const ctx = makeCtx(file);
    ctx.appData = SCENARIO === "balances_legacy" ? legacySeed() : seed();
    vm.runInContext("frUi.weekIso = '" + WEEK_START + "'; frUi.areaId = '" + AREA + "'; ensureFinanceRec();", ctx);
    const steps = [{ name: "initial", state: snapshot(ctx) }];
    for (const step of SCENARIOS[SCENARIO]) {
        await step.run(ctx);
        // Состояние занятости после шага: сохранение завершено.
        vm.runInContext("frUi._moneyOpsBusy = false; frUi._criticalSaveBusy = false;", ctx);
        steps.push({ name: step.name, state: snapshot(ctx), toasts: ctx.__toasts.splice(0).filter(function (t) { return t.kind === "error"; }).map(function (t) { return t.msg; }) });
    }
    return steps;
}

function firstDiff(a, b, p) {
    if (JSON.stringify(a) === JSON.stringify(b)) return null;
    if (a && b && typeof a === "object" && typeof b === "object") {
        const keys = Array.from(new Set(Object.keys(a).concat(Object.keys(b))));
        for (const k of keys) {
            const d = firstDiff(a[k], b[k], p + "." + k);
            if (d) return d;
        }
    }
    return { path: p, base: a, cur: b };
}

(async function main() {
    const base = await runVersion(BASE_FILE);
    const cur = await runVersion(CUR_FILE);
    const out = { scenario: SCENARIO, steps: [] };
    for (let i = 0; i < base.length; i++) {
        const bs = Object.assign({}, base[i].state, { abandoned_deleted_ops: 0 });
        const cs = Object.assign({}, cur[i].state, { abandoned_deleted_ops: 0 });
        out.steps.push({
            name: base[i].name,
            metrics_equal: JSON.stringify(base[i].state.metrics) === JSON.stringify(cur[i].state.metrics),
            equal: JSON.stringify(bs) === JSON.stringify(cs),
            diff: firstDiff(bs, cs, ""),
            base_abandoned: base[i].state.abandoned_deleted_ops,
            cur_abandoned: cur[i].state.abandoned_deleted_ops,
            base_errors: base[i].toasts || [],
            cur_errors: cur[i].toasts || [],
            transits: cur[i].state.money_transits,
            openings: cur[i].state.openings,
            day_closings: cur[i].state.day_closings,
            period_states: cur[i].state.period_states,
            audit_kinds: (cur[i].state.audit || []).map(function (a) { return a.kind; })
        });
    }
    process.stdout.write("RESULT_JSON " + JSON.stringify(out) + "\n");
})().catch(function (err) {
    process.stdout.write("RESULT_ERR " + String(err && err.stack || err) + "\n");
    process.exitCode = 1;
});
