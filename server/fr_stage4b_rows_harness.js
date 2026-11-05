/**
 * Этап 4Б: остатки, фактические остатки, статусы периода — настоящие обработчики fr-mod.js
 * (файл целиком, с расчётами) + настоящие DomainWriter / wtCollectChangedLeaves из app.html
 * против локального wt_api (FakeConn, 127.0.0.1). Искусственные данные, без live / Timeweb.
 * Запускается из server/test_fr_stage4b_rows.py (WT_TEST_BASE_URL, WT_TEST_SCENARIO).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "app.html"), "utf8").replace(/\r\n/g, "\n");
const FR = fs.readFileSync(path.join(ROOT, "fr-mod.js"), "utf8").replace(/\r\n/g, "\n");
const BASE = process.env.WT_TEST_BASE_URL;
const SCENARIO = process.env.WT_TEST_SCENARIO || "";

function extractFn(src, name) {
    const m = new RegExp("^function " + name + "\\(", "m").exec(src);
    if (!m) throw new Error("function not found: " + name);
    const end = src.indexOf("\n}\n", m.index);
    return src.slice(m.index, end + 2);
}

function extractConst(src, name) {
    const m = new RegExp("^const " + name + " = ", "m").exec(src);
    if (!m) throw new Error("const not found: " + name);
    const end = src.indexOf("\n};", m.index);
    return src.slice(m.index, end + 3);
}

const APP_FNS = [
    "wtCloneJson", "wtJsonCanonEqual", "wtGetByPath", "wtSetByPath", "wtRecordVersion", "wtSetLocalRecordMeta",
    "wtKeyedListKey", "wtRecordSemNorm", "wtRecordSemEqual", "wtKeyedListMerge3", "wtConflictBackupSave",
    "wtKeyedListConflictLabel", "wtKeyedListRowCaption", "wtKeyedListResolveConflict",
    "wtDomainWriterAdoptServerValue", "wtDomainWriterResolveConflicts", "wtDomainWriterApplyBatchLocal",
    "wtDomainWriterOpsFromLeaves", "wtDomainWriterSave", "saveApp", "wtOpsFetchWithTimeout",
    "wtCollectChangedLeaves", "wtFormatConflictValue", "wtResolveRecordConflict", "wtSaveRecordOp",
    "nextPrefixedId", "nextPrefixedUniqueId", "mgmtIsoFromDate", "mgmtParseIso", "mgmtSpuWeekStartDay",
    "mgmtSpuReportingWeek", "mgmtSpuShiftReportingWeek"
];

const log = { toasts: [], batches: [], singleOps: [], confirms: [], puts: 0 };
let cookie = "";
let batchCounter = 0;
let failBatchNo = 0;
let networkFailBatchNo = 0;
let mutateBatch = null;
let inFlight = null;

function rawFetch(url, opts) {
    const o = Object.assign({}, opts || {});
    o.headers = Object.assign({}, o.headers || {});
    if (cookie) o.headers.Cookie = cookie;
    delete o.credentials;
    return fetch(BASE + url, o);
}

function clientFetch(url, opts) {
    if (url === "/api/state/op/batch") {
        batchCounter += 1;
        let body = null;
        try { body = JSON.parse(opts.body); } catch (e) { body = null; }
        const entry = {
            n: batchCounter,
            atomic: !!(body && body.atomic),
            paths: ((body && body.ops) || []).map(function (o) { return o.path; }),
            status: 0,
            results: []
        };
        log.batches.push(entry);
        if (mutateBatch && body) {
            mutateBatch(body, batchCounter);
            opts = Object.assign({}, opts, { body: JSON.stringify(body) });
        }
        if (inFlight) inFlight(batchCounter);
        if (networkFailBatchNo && batchCounter === networkFailBatchNo) {
            entry.status = -1;
            return Promise.reject(new TypeError("fetch failed"));
        }
        if (failBatchNo && batchCounter === failBatchNo) {
            entry.status = 500;
            return Promise.resolve(new Response(JSON.stringify({ error: "db unavailable" }), { status: 500 }));
        }
        return rawFetch(url, opts).then(function (res) {
            entry.status = res.status;
            return res.text().then(function (t) {
                try {
                    const b = JSON.parse(t) || {};
                    entry.results = (b.results || []).map(function (r) { return r.path + ":" + (r.status || r.error); });
                    if (b.path) entry.conflictPath = b.path;
                } catch (e) {}
                return new Response(t, { status: res.status, headers: { "Content-Type": "application/json" } });
            });
        });
    }
    if (url === "/api/state/op") {
        log.singleOps.push(opts && opts.body);
    }
    if (url === "/api/state" && opts && opts.method === "PUT") log.puts++;
    return rawFetch(url, opts);
}

const prelude = `
var window = { confirm: function () { return false; } };
var localStorage = { _m: {}, getItem: function (k) { return this._m[k] || null; }, setItem: function (k, v) { this._m[k] = String(v); } };
var fetch = __clientFetch;
var WT_RECORDS_KEY = "_wt_records";
var appData = null;
var wtLastLoadedPayload = null;
var wtStateVersion = 0;
var wtSoftAppliedVersion = 0;
var wtOpsChain = Promise.resolve();
var wtPutChain = Promise.resolve();
var wtServerMode = true;
var wtSessionIdentityMismatch = false;
var wtSoftApplying = false;
var wtOpsBlockIncidentalPut = false;
var wtPutPaused = false;
var wtKeepUnsavedDraft = false;
var wtAllowConflictRetry = false;
var wtPauseToastAt = 0;
var wtOpsServerCapable = true;
var wtOpsUiEnabledCache = null;
var wtDomainWriterDeferredPending = false;
var wtPutPayloadOverride = null;
var currentSection = "financeRec";
var __form = {};
var __promptAnswer = "";
var document = {
    getElementById: function (id) { return Object.prototype.hasOwnProperty.call(__form, id) ? { value: __form[id] } : null; },
    querySelectorAll: function () { return []; }
};
function prompt() { return __promptAnswer; }
function confirm() { return false; }
function mgmtPad(n) { return String(n).padStart(2, "0"); }
function toast(msg, kind) { __log.toasts.push({ msg: String(msg), kind: kind || "" }); }
function wtSoftMarkDirty() {}
function wtSoftClearDirty() {}
function wtSoftHasDirty() { return false; }
function wtSoftProtectPendingLeaves() {}
function wtSoftFlushPendingRender() {}
function wtDomainWriterScheduleDeferred() {}
function wtDomainWriterMarkPendingDraft() {}
function wtDomainWriterLeavesFromDirty() { return []; }
function wtFilterPositionCatalogLeavesForSession(l) { return l; }
function wtIsPositionCatalogPath() { return false; }
function wtCanWritePositionCatalog() { return true; }
function wtConfirmSessionIdentity() { return Promise.resolve(true); }
function wtOpsUiEnabled() { return true; }
function wtNewIdempotencyKey() { return "idem_" + Math.random().toString(36).slice(2); }
function stripLocalSecretsAfterSave() {}
function handleWtSaveUnauthorized() {}
function wtPerfConflict() {}
function wtToastSessionIdentityMismatch() {}
function putServerStateOnce() { __log.puts++; return Promise.resolve(false); }
function closeMgmtModal() {}
function openMgmtModal() {}
function escapeHtml(s) { return String(s == null ? "" : s); }
function escapeAttribute(s) { return String(s == null ? "" : s); }
function currentAuthUserId() { return "user_admin"; }
function isAppAdmin() { return true; }
`;

const code = prelude
    + extractConst(APP, "WT_KEYED_LIST_PATHS") + "\n"
    + extractConst(APP, "WT_DOMAIN_TRACKED_TOPS") + "\n"
    + extractConst(APP, "WT_FR_ROW_KEYED_ROOTS") + "\n"
    + "const WT_OPS_FETCH_TIMEOUT_MS = 30000;\n"
    + /^const WT_ATOMIC_YOURS_MAX_ROUNDS = .*$/m.exec(APP)[0] + "\n"
    + APP_FNS.map(function (n) { return extractFn(APP, n); }).join("\n");

const api = {};
const ctx = vm.createContext({
    __api: api, __log: log, __clientFetch: clientFetch,
    console, setTimeout, clearTimeout, Promise, JSON, Math, Date, Number, String, Object, Array, Error, RegExp,
    AbortController, Response, unescape, encodeURIComponent
});
vm.runInContext(code, ctx, { filename: "fr_stage4b_app.vm.js" });
vm.runInContext(FR, ctx, { filename: "fr-mod.js" });
// Права/объект фиксированы; отрисовка без DOM — только её побочные изменения данных
// (перенос текущей недели и строка статуса недели), как в renderFinanceRec.
vm.runInContext(`
    frCan = function () { return true; };
    frActiveArea = function () { return appData.financeRec.areas[0]; };
    renderFinanceRec = function () {
        ensureFinanceRec();
        const a = frActiveArea();
        const w = frEnsureWeek();
        frCarryIfNeeded(a.finance_area_id, w.period_key);
        frPeriodState(a.finance_area_id, w.period_key);
    };
    __api.eval = function (expr) { return eval(expr); };
    __api.load = function (state) {
        appData = JSON.parse(JSON.stringify(state.payload));
        wtLastLoadedPayload = JSON.parse(JSON.stringify(state.payload));
        wtStateVersion = Number(state.version) || 0;
        wtSoftAppliedVersion = wtStateVersion;
        frUi.weekIso = "2026-11-05";
        frUi.areaId = "farea_shop";
    };
    __api.setPrompt = function (v) { __promptAnswer = v; };
    __api.collect = function () { return wtCollectChangedLeaves(wtLastLoadedPayload || {}, appData).map(function (l) { return l.path; }); };
    __api.idle = function () { return !frUi._moneyOpsBusy && !frUi._rowFlushTimer && !frUi._rowFlushBusy; };
    __api.chain = function () { return wtOpsChain; };
`, ctx);

function settle() {
    let rounds = 0;
    function step() {
        const chain = api.chain();
        return chain.then(function () {
            return new Promise(function (r) { setTimeout(r, 120); });
        }).then(function () {
            rounds += 1;
            if ((chain !== api.chain() || !api.idle()) && rounds < 40) return step();
            return true;
        });
    }
    return step();
}

function login(userId, pin) {
    return fetch(BASE + "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, pin: pin })
    }).then(function (res) {
        const m = /wt_session=[^;]+/.exec(res.headers.get("set-cookie") || "");
        if (!m) throw new Error("login failed " + res.status);
        return m[0];
    });
}

function getState(withCookie) {
    return fetch(BASE + "/api/state", { headers: { Cookie: withCookie } }).then(function (r) { return r.json(); });
}

function postBatch(withCookie, ops, atomic) {
    return fetch(BASE + "/api/state/op/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json", Cookie: withCookie },
        body: JSON.stringify({ atomic: atomic !== false, ops: ops })
    }).then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); });
}

function recV(state, p) {
    const m = (state.payload && state.payload._wt_records) || {};
    return m[p] ? Number(m[p].v) || 0 : 0;
}

function clone(v) { return JSON.parse(JSON.stringify(v == null ? null : v)); }
function serverFr(state) { return (state.payload && state.payload.financeRec) || {}; }
function localFr() { return api.eval("appData.financeRec"); }
function rowKey(list, row) { return api.eval("frRowKey(" + JSON.stringify(list) + "," + JSON.stringify(row) + ")"); }
function rowPath(list, row) { return "financeRec." + list + "." + rowKey(list, row); }

const AREA = "farea_shop";
const WEEK = "week:2026-11-05";
const NEXT = "week:2026-11-12";

// Другой сотрудник пишет строку по ключу поверх свежего состояния сервера — как его клиент.
async function otherWritesRow(other, list, row) {
    const st = await getState(other);
    const p = rowPath(list, row);
    return (await postBatch(other, [{ path: p, expected_v: recV(st, p), value: row }])).status;
}

function opening(src, value, period, extra) {
    return Object.assign({
        finance_area_id: AREA, object_id: AREA, period_key: period || WEEK,
        finance_source_id: src, account_id: src, data_source: "manual", external_id: "",
        value: value, is_manual: true, carried: false
    }, extra || {});
}

async function act(expr, promptAnswer) {
    api.setPrompt(promptAnswer || "");
    const ret = await Promise.resolve(api.eval(expr));
    await settle();
    return ret === undefined ? null : ret;
}

async function dump(out) {
    const fr = serverFr(await getState(cookie));
    out.server = { openings: fr.openings || [], day_closings: fr.day_closings || [], period_states: fr.period_states || [], audit: fr.audit || [], money_transits: fr.money_transits || [], operations: fr.operations || [] };
    const lf = localFr();
    out.local = { openings: clone(lf.openings), day_closings: clone(lf.day_closings), period_states: clone(lf.period_states), audit: clone(lf.audit) };
    out.collected = api.collect();
    out.calc = api.eval(`(function () {
        const a = "${AREA}";
        const r = {};
        ["${WEEK}", "${NEXT}"].forEach(function (pk) {
            const w = frWeek(pk.replace(/^week:/, ""));
            r[pk] = { check: frWeekFactCheck(a, pk), status: frPeriodState(a, pk).status,
                      cashOpening: frWeekIncomingOpening(a, pk, "fsrc_cash"), bankOpening: frWeekIncomingOpening(a, pk, "fsrc_bank"),
                      cashCarry: frCarryClosingValue(a, pk, w.end_date, "fsrc_cash") };
        });
        return JSON.parse(JSON.stringify(r));
    })()`);
}

async function main() {
    cookie = await login("user_admin", "5555");
    const other = await login("usr_dir", "4444");
    api.load(await getState(cookie));
    // Экран открыт: отрисовка посчитала перенос и строку статуса (как в рабочем приложении).
    api.eval("renderFinanceRec()");
    // Искусственный seed не нормализован (в рабочих данных areas/sources/operations уже нормализованы):
    // снимок этих ключей = нормализованные данные; строки остатков/статусов не трогаем.
    // money_transits не выравниваем: старый транзит без transit_id должен остаться «сырым» на сервере.
    api.eval(`Object.keys(appData.financeRec).forEach(function (k) {
        if (FR_ROW_KEY_LISTS.indexOf(k) >= 0 || k === "money_transits") return;
        wtLastLoadedPayload.financeRec[k] = JSON.parse(JSON.stringify(appData.financeRec[k]));
    })`);
    const out = { scenario: SCENARIO };
    out.end_date = api.eval("frEnsureWeek().end_date");

    if (SCENARIO === "day_actual_two_employees") {
        out.other_status = await otherWritesRow(other, "day_closings", {
            finance_day_closing_id: "fdc_b", finance_area_id: AREA, object_id: AREA, period_key: WEEK,
            date: "2026-11-06", finance_source_id: "fsrc_bank", account_id: "fsrc_bank", actual_balance: 900, actual_closing: 900
        });
        out.result = await act("frSaveDayActual('fsrc_cash', '2026-11-06', '5800')");
    } else if (SCENARIO === "opening_two_employees") {
        out.other_status = await otherWritesRow(other, "openings", opening("fsrc_bank", 900));
        out.result = await act("frSaveOpening('fsrc_cash', '1500')", "корректировка");
    } else if (SCENARIO === "opening_same_row_conflict") {
        out.other_status = await otherWritesRow(other, "openings", opening("fsrc_cash", 900));
        out.local_before = clone(localFr().openings);
        out.result = await act("frSaveOpening('fsrc_cash', '1500')", "корректировка");
    } else if (SCENARIO === "day_actual_network" || SCENARIO === "day_actual_500") {
        if (SCENARIO === "day_actual_network") networkFailBatchNo = 1;
        else failBatchNo = 1;
        out.local_before = clone(localFr());
        out.result = await act("frSaveDayActual('fsrc_cash', '2026-11-06', '5800')");
        out.local_after = clone(localFr());
    } else if (SCENARIO === "week_actual_ok" || SCENARIO === "week_actual_fail_op2" || SCENARIO === "week_actual_fail_last") {
        if (SCENARIO !== "week_actual_ok") {
            mutateBatch = function (body, n) {
                if (n !== 1) return;
                const idx = SCENARIO === "week_actual_fail_op2" ? 1 : body.ops.length - 1;
                body.ops[idx].expected_v = 999;
            };
        }
        out.local_before = clone(localFr());
        out.result = await act("frSaveWeekActualBalance('fsrc_cash', '6000')");
        out.local_after = clone(localFr());
    } else if (SCENARIO === "status_close_reopen") {
        out.status_result = await act("frSetStatus('filled')");
        out.close_result = await act("frClosePeriod()", "принудительно");
        out.after_close = clone(serverFr(await getState(cookie)));
        out.reopen_result = await act("frReopenPeriod()", "исправить ошибку");
    } else if (SCENARIO === "status_conflict") {
        const st = await getState(other);
        const local = clone(localFr().period_states.find(function (p) { return p.period_key === WEEK; }));
        const p = rowPath("period_states", local);
        out.other_status = (await postBatch(other, [{ path: p, expected_v: recV(st, p), value: Object.assign({}, local, { status: "closed", closed_by: "usr_dir" }) }])).status;
        out.result = await act("frSetStatus('review')");
    } else if (SCENARIO === "push_carry_ok" || SCENARIO === "push_carry_conflict") {
        if (SCENARIO === "push_carry_conflict") {
            out.other_status = await otherWritesRow(other, "openings", opening("fsrc_bank", 4321, NEXT));
        }
        out.result = await act("frPushCarry()");
    } else if (SCENARIO === "render_flush_keeps_other_rows") {
        // Б записал ручной остаток счёта после загрузки А; у А отрисовка посчитала перенос этого счёта.
        out.other_status = await otherWritesRow(other, "openings", opening("fsrc_bank", 900));
        out.collected_before = api.collect();
        out.result = await act("saveApp()");
    } else if (SCENARIO === "double_click") {
        api.setPrompt("");
        const p1 = Promise.resolve(api.eval("frSaveDayActual('fsrc_cash', '2026-11-06', '5800')"));
        const p2 = Promise.resolve(api.eval("frSaveDayActual('fsrc_bank', '2026-11-06', '777')"));
        out.first = await p1;
        out.second = await p2;
        await settle();
    } else if (SCENARIO === "rollback_keeps_newer") {
        networkFailBatchNo = 1;
        inFlight = function (n) {
            if (n !== 1) return;
            const row = localFr().day_closings.find(function (r) { return r.date === "2026-11-06"; });
            if (row) row.comment = "правка во время сохранения";
        };
        out.result = await act("frSaveDayActual('fsrc_cash', '2026-11-06', '5800')");
    } else if (SCENARIO === "status_rereads") {
        // Статус → закрытие → открытие; после каждого действия — повторное чтение сервера.
        out.steps = [];
        for (const step of [["frSetStatus('filled')", ""], ["frClosePeriod()", "принудительно"], ["frReopenPeriod()", "исправить"]]) {
            const n = log.toasts.length;
            const res = await act(step[0], step[1]);
            out.steps.push({
                action: step[0], result: res, server: clone(serverFr(await getState(cookie))),
                local_states: clone(localFr().period_states), toasts: log.toasts.slice(n)
            });
        }
    } else if (SCENARIO === "status_500" || SCENARIO === "close_500") {
        failBatchNo = 1;
        out.local_before = clone(localFr());
        out.result = SCENARIO === "status_500" ? await act("frSetStatus('filled')") : await act("frClosePeriod()", "принудительно");
        out.local_after = clone(localFr());
    } else if (SCENARIO === "close_conflict") {
        // Б записал строку статуса недели раньше; закрытие А — 409, ничего не записано.
        const local = clone(localFr().period_states.find(function (p) { return p.finance_area_id === AREA && p.period_key === WEEK; }));
        out.other_status = await otherWritesRow(other, "period_states", Object.assign({}, local, { status: "review", reviewed_by: "usr_dir" }));
        out.local_before = clone(localFr());
        out.result = await act("frClosePeriod()", "принудительно");
        out.local_after = clone(localFr());
    } else if (SCENARIO === "status_two_employees") {
        out.other_status = await otherWritesRow(other, "day_closings", {
            finance_area_id: AREA, object_id: AREA, period_key: WEEK, date: "2026-11-06",
            finance_source_id: "fsrc_bank", account_id: "fsrc_bank", actual_balance: 900, actual_closing: 900
        });
        out.result = await act("frSetStatus('filled')");
    } else if (SCENARIO === "guard_unaddressable_status") {
        // Обработчик правит не ту строку, на которую указывает ключ (вторая строка с тем же ключом).
        api.eval(`(function () {
            const a = frActiveArea(); const w = frEnsureWeek();
            const dup = JSON.parse(JSON.stringify(frPeriodState(a.finance_area_id, w.period_key)));
            appData.financeRec.period_states.push(dup);
            const orig = frPeriodState;
            frPeriodState = function (x, y) { return x === a.finance_area_id && y === w.period_key ? dup : orig(x, y); };
            __api.restorePS = function () { frPeriodState = orig; };
        })()`);
        out.local_before = clone(localFr());
        out.result = await act("frClosePeriod()", "принудительно");
        out.local_after = clone(localFr());
        api.eval("__api.restorePS()");
    } else if (SCENARIO === "transit_legacy_open_save" || SCENARIO === "transit_legacy_directory"
        || SCENARIO === "transit_legacy_dividend" || SCENARIO === "transit_legacy_day_actual") {
        out.local_transits = clone(localFr().money_transits);
        out.collected_before = api.collect();
        if (SCENARIO === "transit_legacy_open_save") out.result = await act("saveApp()");
        if (SCENARIO === "transit_legacy_directory") out.result = await act("frSetPrimaryCash('fsrc_cash2')");
        if (SCENARIO === "transit_legacy_dividend") {
            api.eval("__form = { frDivId: '', frDivAccount: 'fsrc_cash', frDivAmt: '80', frDivDate: '2026-11-09', frDivCom: 'дивиденды' }");
            out.result = await act("saveFrDividend()");
            out.after_wide = await act("saveApp()");
        }
        if (SCENARIO === "transit_legacy_day_actual") out.result = await act("frSaveDayActual('fsrc_cash', '2026-11-06', '5800')");
    } else if (SCENARIO === "transit_send_softsync_inflight") {
        // SoftSync во время записи вернул серверный список без нового транзита.
        inFlight = function (n) {
            if (n !== 1) return;
            api.eval("appData.financeRec.money_transits = JSON.parse(JSON.stringify(wtLastLoadedPayload.financeRec.money_transits || []))");
        };
        api.eval("__form = { frTsFrom: 'fsrc_cash', frTsAmt: '700', frTsDate: '2026-11-06', frTsCom: 'в банк' }");
        out.result = await act("saveFrTransitSend()");
        out.local_transits = clone(localFr().money_transits);
    } else if (SCENARIO.indexOf("ops250_") === 0) {
        // 250+ операций: правка одной ячейки / операции — в пакете только она и запись журнала.
        api.eval(`(function () {
            document.querySelector = function () { return null; };
            __api.cell = function (attrs, value, row) {
                const tr = row ? {
                    getAttribute: function (k) { return row.attrs[k] == null ? null : row.attrs[k]; },
                    setAttribute: function (k, v) { row.attrs[k] = v; },
                    querySelector: function (sel) { return sel === ".fr-ni-comment" ? { value: row.comment } : null; },
                    querySelectorAll: function () { return []; }
                } : null;
                return {
                    value: value,
                    getAttribute: function (k) { return attrs[k] == null ? null : attrs[k]; },
                    closest: function (sel) { return sel === "tr" ? tr : null; }
                };
            };
        })()`);
        const revenueCell = "frCommitRevenueCell(__api.cell({ 'data-source-id': 'fsrc_cash', 'data-date': '2026-11-06', 'data-op-kind': 'income' }, '777'))";
        const nonIncomeCell = "frCommitNonIncomeCell(__api.cell({ 'data-date': '2026-11-06', 'data-ni-channel': 'cash' }, '150', { attrs: {}, comment: 'возврат поставщика' }))";
        const editForm = "__form = { frOpId: 'fop_bulk_010', frOpAmt: '999', frOpKind: 'expense', frOpDate: '2026-11-04', frOpComment: 'правка', frOpSrc: 'fsrc_cash' }";
        if (SCENARIO === "ops250_revenue_cell") out.result = await act(revenueCell);
        if (SCENARIO === "ops250_non_income_cell") out.result = await act(nonIncomeCell);
        if (SCENARIO === "ops250_edit_op") {
            api.eval(editForm);
            out.result = await act("saveFrOp()");
        }
        if (SCENARIO === "ops250_two_employees") {
            // Б загрузил данные до записи А и правит другую операцию по своей версии.
            const st0 = await getState(other);
            out.revenue_result = await act(revenueCell);
            const p100 = "financeRec.operations.fop_bulk_100";
            const op100 = clone(serverFr(st0).operations.find(function (o) { return o.finance_operation_id === "fop_bulk_100"; }));
            op100.amount = 4321;
            out.other_after_a = (await postBatch(other, [{ path: p100, expected_v: recV(st0, p100), value: op100 }])).status;
            // Б правит ещё одну операцию; у А данные загружены раньше — А правит свою через окно.
            const st1 = await getState(other);
            const p101 = "financeRec.operations.fop_bulk_101";
            const op101 = clone(serverFr(st1).operations.find(function (o) { return o.finance_operation_id === "fop_bulk_101"; }));
            op101.amount = 1234;
            out.other_before_a = (await postBatch(other, [{ path: p101, expected_v: recV(st1, p101), value: op101 }])).status;
            api.eval(editForm);
            out.edit_result = await act("saveFrOp()");
        }
        // F5: повторная загрузка с сервера.
        api.load(await getState(cookie));
        out.reloaded_ops = clone(localFr().operations);
    } else {
        throw new Error("unknown scenario " + SCENARIO);
    }
    await dump(out);
    out.log = log;
    return out;
}

main().then(function (out) {
    process.stdout.write("RESULT_JSON " + JSON.stringify(out) + "\n");
}, function (err) {
    process.stdout.write("RESULT_ERR " + String(err && err.stack || err) + "\n");
    process.exitCode = 1;
});
