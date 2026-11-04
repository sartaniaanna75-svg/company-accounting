/**
 * Этап 4 (аудит, без правки рабочего кода): широкие saveApp() Финконтроля против локального
 * wt_api (FakeConn, 127.0.0.1). Настоящие DomainWriter / wtCollectChangedLeaves / диалог конфликта
 * из app.html; действия обработчиков fr-mod.js воспроизведены теми же мутациями appData + saveApp().
 * Запускается из server/test_fr_stage4_audit.py (WT_TEST_BASE_URL, WT_TEST_SCENARIO, WT_TEST_CONFIRMS).
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
const confirmAnswers = JSON.parse(process.env.WT_TEST_CONFIRMS || "[]");

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
    "nextPrefixedId", "nextPrefixedUniqueId"
];
// Настоящие обработчики и сохранение Финконтроля из fr-mod.js.
const FR_FNS = [
    "frOpPathById", "frSnapshotApplyConfirmed", "frFindOpById", "frCloneFinanceJson", "frSnapOpsByIds",
    "frSnapOpsFromBefore", "frRestoreOpsSnap", "frUniqueOpIds", "frCollectChangedOpIds", "frBeginMoneyOpsSnapshot",
    "frMoneyOpsPasteLocked", "frQueueMoneyOpIds", "frPersistOpsById", "frAudit", "frAuditLeaf",
    "frDropLocalAuditIds", "frNormDate", "frOpKind", "frNum", "frRound",
    "saveFrDividend", "frQuickAddTransfer", "saveFrTransfer",
    // Транзиты (этап 4А): настоящие функции вместо заглушки frSyncTransitAfterTransfer.
    "frOpAccountId", "frOpAccountToId", "frNormalizeTransitRecord", "frTransits", "frTransitById",
    "frTransitPathById", "frBeginTransitsSnapshot", "frSnapTransitsByIds", "frCollectChangedTransitIds",
    "frTransitLeaf", "frTransitIdsMissingOnServer", "frRestoreTransitsSnap", "frPersistOpsWithTransits",
    "frMoneyOpsBlocked", "frTransitJournalRows", "frCreateTransferOp", "frSyncTransitAfterTransfer",
    "frJournalTransitRow", "frEnsureTransitForClose", "saveFrTransitSend", "saveFrTransitClose", "frDeleteOp",
    // Строки остатков/статусов по ключу (этап 4Б): нужны frPersistOpsById / frSnapshotApplyConfirmed.
    "frRowKeyDate", "frRowKeyPart", "frRowKey", "frRowPath", "frRowPathParts", "frRowIndexByKey", "frRowUpsert",
    "frScheduleRowFlush", "frCollectRowFlushLeaves", "frSendRowFlushChunk", "frFlushRowSideEffects"
];

const log = { toasts: [], batches: [], singleOps: [], confirms: [], puts: 0 };
let cookie = "";
let batchCounter = 0;
let failBatchNo = 0;
let networkFailBatchNo = 0;
// mutateBatch(body, n) — подменить тело пакета (отказ 2-й/3-й части); inFlight(n) — действие до ответа.
let mutateBatch = null;
let inFlight = null;

function rawFetch(url, opts) {
    const o = Object.assign({}, opts || {});
    o.headers = Object.assign({}, o.headers || {});
    if (cookie) o.headers.Cookie = cookie;
    delete o.credentials;
    return fetch(BASE + url, o);
}

function hostConfirm(msg) {
    const ans = confirmAnswers.length ? !!confirmAnswers.shift() : false;
    log.confirms.push({ answer: ans, text: String(msg).split("\n").slice(0, 3).join(" | ") });
    return ans;
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
        let body = null;
        try { body = JSON.parse(opts.body); } catch (e) { body = null; }
        const entry = { path: body && body.op && body.op.path, status: 0 };
        log.singleOps.push(entry);
        return rawFetch(url, opts).then(function (res) { entry.status = res.status; return res; });
    }
    return rawFetch(url, opts);
}

const prelude = `
var window = { confirm: __confirm };
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
function toast(msg, kind) { __log.toasts.push({ msg: String(msg), kind: kind || "" }); }
function wtSoftMarkDirty() {}
function wtSoftClearDirty() {}
function wtSoftProtectPendingLeaves() {}
function wtSoftFlushPendingRender() {}
function wtDomainWriterScheduleDeferred() {}
function wtDomainWriterMarkPendingDraft() {}
function wtDomainWriterLeavesFromDirty() { return []; }
function wtFilterPositionCatalogLeavesForSession(l) { return l; }
function wtIsPositionCatalogPath() { return false; }
function wtCanWritePositionCatalog() { return true; }
function wtAbsorbPositionCatalogNormalize() {}
function wtConfirmSessionIdentity() { return Promise.resolve(true); }
function wtOpsUiEnabled() { return true; }
function wtNewIdempotencyKey() { return "idem_" + Math.random().toString(36).slice(2); }
function stripLocalSecretsAfterSave() {}
function handleWtSaveUnauthorized() {}
function wtPerfConflict() {}
function wtToastSessionIdentityMismatch() {}
function putServerStateOnce() { __log.puts++; return Promise.resolve(false); }
function isAppAdmin() { return true; }
var frUi = {};
var FR_DATA_SOURCES = { manual: "manual", paste: "paste" };
var __form = {};
var document = { getElementById: function (id) { return Object.prototype.hasOwnProperty.call(__form, id) ? { value: __form[id] } : null; } };
function currentAuthUserId() { return "user_admin"; }
function ensureFinanceRec() {
    if (!appData.financeRec) appData.financeRec = {};
    ["operations", "audit", "openings", "period_states", "import_batches", "money_transits", "day_closings"].forEach(function (k) {
        if (!Array.isArray(appData.financeRec[k])) appData.financeRec[k] = [];
    });
    appData.financeRec.money_transits.forEach(function (t) { frNormalizeTransitRecord(t); });
    return appData.financeRec;
}
function frTransitAccountId() { return "fsrc_transit"; }
function frIsClosed() { return false; }
function frCan() { return true; }
function frMoney(n) { return String(n); }
function frCanEditData() { return true; }
function frActiveArea() { return { finance_area_id: "farea_shop", object_id: "farea_shop" }; }
function frWeek(iso) { return { start_date: "2026-11-02", end_date: "2026-11-08", period_key: "2026-W45" }; }
function frEnsureWeek() { return frWeek(); }
function frParseExpenseDate() { return ""; }
function frStampOperation(o, opts) {
    if (opts.object_id) { o.finance_area_id = opts.object_id; o.object_id = opts.object_id; }
    if (opts.account_id) { o.finance_source_id = opts.account_id; o.account_id = opts.account_id; }
    if (opts.account_to_id != null) { o.finance_source_to_id = opts.account_to_id; o.account_to_id = opts.account_to_id; }
    if (opts.op_type) o.movement_kind = opts.op_type;
    if (opts.data_source) o.data_source = opts.data_source;
    if (opts.revenue_direction != null) o.revenue_direction = opts.revenue_direction;
    return o;
}
function frSourceByAccountId(areaId, id) { return { finance_source_id: id, account_type: id === "fsrc_transit" ? "in_transit" : "cash" }; }
function frIsTransitSource(src) { return !!(src && src.account_type === "in_transit"); }
function closeMgmtModal() { __log.modalClosed = (__log.modalClosed || 0) + 1; }
function renderFinanceRec() {}
`;

const code = prelude
    + extractConst(APP, "WT_KEYED_LIST_PATHS") + "\n"
    + extractConst(APP, "WT_DOMAIN_TRACKED_TOPS") + "\n"
    + extractConst(APP, "WT_FR_ROW_KEYED_ROOTS") + "\n"
    + /^const FR_ROW_KEY_LISTS = .*$/m.exec(FR)[0] + "\n"
    + /^const FR_ROW_FLUSH_CHUNK = .*$/m.exec(FR)[0] + "\n"
    + "const WT_OPS_FETCH_TIMEOUT_MS = 30000;\n"
    + /^const WT_ATOMIC_YOURS_MAX_ROUNDS = .*$/m.exec(APP)[0] + "\n"
    + APP_FNS.map(function (n) { return extractFn(APP, n); }).join("\n")
    + "\n" + extractConst(FR, "FR_SNAPSHOT_LIST_ID_FIELDS") + "\n"
    + FR_FNS.map(function (n) { return extractFn(FR, n); }).join("\n")
    + `
__api.setForm = function (m) { __form = m; };
__api.frUi = function () { return frUi; };
__api.saveFrDividend = function () { return saveFrDividend(); };
__api.frQuickAddTransfer = function (d) { return frQuickAddTransfer(d); };
__api.saveFrTransfer = function () { return saveFrTransfer(); };
__api.saveFrTransitSend = function () { return saveFrTransitSend(); };
__api.saveFrTransitClose = function () { return saveFrTransitClose(); };
__api.frDeleteOp = function (id) { return frDeleteOp(id); };
__api.recordV = function (p) { return wtRecordVersion(p); };
__api.settle = function () {
    // Дождаться очереди OPS (старый код не возвращал промис сохранения).
    let rounds = 0;
    function step() {
        const chain = wtOpsChain;
        return chain.then(function () {
            return new Promise(function (r) { setTimeout(r, 60); });
        }).then(function () {
            rounds += 1;
            if (chain !== wtOpsChain && rounds < 20) return step();
            return true;
        });
    }
    return step();
};
__api.load = function (state) {
    appData = JSON.parse(JSON.stringify(state.payload));
    wtLastLoadedPayload = JSON.parse(JSON.stringify(state.payload));
    wtStateVersion = Number(state.version) || 0;
};
__api.app = function () { return appData; };
__api.saveApp = saveApp;
__api.collect = function () { return wtCollectChangedLeaves(wtLastLoadedPayload || {}, appData).map(function (l) { return l.path; }); };
`;

const api = {};
const ctx = vm.createContext({
    __api: api, __log: log, __clientFetch: clientFetch, __confirm: hostConfirm,
    console, setTimeout, clearTimeout, Promise, JSON, Math, Date, Number, String, Object, Array, Error,
    AbortController, Response
});
vm.runInContext(code, ctx, { filename: "fr_stage4_audit_harness.vm.js" });

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

function rootV(state, p) {
    const m = (state.payload && state.payload._wt_records) || {};
    return m[p] ? Number(m[p].v) || 0 : 0;
}

const AREA = "farea_shop";
const WEEK = "2026-W45";
const NEXT = "2026-W46";

// Чужой сотрудник (usr_dir) пишет поверх свежего состояния сервера — как его клиент.
async function otherWritesList(other, list, mutate) {
    const st = await getState(other);
    const fr = (st.payload && st.payload.financeRec) || {};
    const value = JSON.parse(JSON.stringify(fr[list] || []));
    mutate(value);
    return postBatch(other, [{ path: "financeRec." + list, expected_v: rootV(st, "financeRec." + list), value: value }]);
}

// Как frAudit: запись в конец массива журнала (широкий saveApp отправит financeRec.audit целиком).
function pushAudit(id, kind, extra) {
    api.app().financeRec.audit.push(Object.assign({ audit_id: id, kind: kind, finance_area_id: AREA, period_key: WEEK }, extra || {}));
}

// Как renderFinanceRec → frPeriodState (создаёт черновик недели) + frCarryIfNeeded (перенос в openings).
function simulateRender() {
    const fr = api.app().financeRec;
    if (!fr.period_states.some(function (p) { return p.period_key === WEEK; })) {
        fr.period_states.push({ finance_period_id: "fper_1", finance_area_id: AREA, period_key: WEEK, status: "draft" });
    }
    if (!fr.openings.some(function (o) { return o.period_key === WEEK && o.finance_source_id === "fsrc_card"; })) {
        fr.openings.push({ finance_area_id: AREA, period_key: WEEK, finance_source_id: "fsrc_card", value: 0, carried: true, is_manual: false });
    }
}

// Настоящий saveFrDividend из fr-mod.js; поля формы — как в openFrDividendForm.
async function dividend(id, amount) {
    api.setForm({
        frDivId: id || "", frDivAccount: "fsrc_cash", frDivAmt: String(amount),
        frDivDate: "2026-11-04", frDivCom: "дивиденды"
    });
    const ret = await Promise.resolve(api.saveFrDividend());
    await api.settle();
    return ret;
}

function newDividendIds() {
    return api.app().financeRec.operations
        .filter(function (o) { return o && o.movement_kind === "dividend"; })
        .map(function (o) { return o.finance_operation_id; });
}

function clone(v) { return JSON.parse(JSON.stringify(v == null ? null : v)); }

// Настоящий saveFrTransitSend; поля формы — как в openFrTransitSendForm.
async function sendToTransit(amount) {
    api.setForm({ frTsFrom: "fsrc_cash", frTsAmt: String(amount), frTsDate: "2026-11-04", frTsCom: "в банк" });
    const ret = await Promise.resolve(api.saveFrTransitSend());
    await api.settle();
    return ret;
}

// Настоящий saveFrTransitClose; поля формы — как в openFrTransitCloseForm.
async function closeTransit(transitId, amount) {
    api.setForm({ frTcId: transitId, frTcTo: "fsrc_bank", frTcAmt: String(amount), frTcDate: "2026-11-05", frTcCom: "" });
    const ret = await Promise.resolve(api.saveFrTransitClose());
    await api.settle();
    return ret;
}

function localFr() { return api.app().financeRec; }
function lastTransit() { const l = localFr().money_transits; return l[l.length - 1] || null; }
function sendOpOf(tid) {
    return localFr().operations.find(function (o) { return o.transit_id === tid && o.transit_role === "send"; }) || null;
}

async function dumpState(out) {
    const fr = serverFr(await getState(cookie));
    out.server_transits = fr.money_transits || [];
    out.server_ops = fr.operations || [];
    out.server_audit_ids = (fr.audit || []).map(function (a) { return a.audit_id; });
    out.local_transits = clone(localFr().money_transits);
    out.local_op_ids = localFr().operations.map(function (o) { return o.finance_operation_id; });
    out.local_audit_ids = localFr().audit.map(function (a) { return a.audit_id; });
}

function serverFr(state) {
    return (state.payload && state.payload.financeRec) || {};
}

async function main() {
    cookie = await login("user_admin", "5555");
    const other = await login("usr_dir", "4444");
    api.load(await getState(cookie));
    const out = { scenario: SCENARIO };

    if (SCENARIO === "day_closings_overwrite" || SCENARIO === "openings_overwrite") {
        const list = SCENARIO === "day_closings_overwrite" ? "day_closings" : "openings";
        const rowB = list === "day_closings"
            ? { finance_day_closing_id: "fdc_b", finance_area_id: AREA, object_id: AREA, date: "2026-11-04", account_id: "fsrc_bank", actual_balance: 900 }
            : { finance_area_id: AREA, object_id: AREA, period_key: WEEK, finance_source_id: "fsrc_bank", account_id: "fsrc_bank", value: 900, is_manual: true };
        const rowA = list === "day_closings"
            ? { finance_day_closing_id: "fdc_a", finance_area_id: AREA, object_id: AREA, date: "2026-11-04", account_id: "fsrc_cash", actual_balance: 100 }
            : { finance_area_id: AREA, object_id: AREA, period_key: WEEK, finance_source_id: "fsrc_cash", account_id: "fsrc_cash", value: 100, is_manual: true };
        // Сотрудник Б записал свой счёт; сотрудник А (данные загружены раньше) — другой счёт.
        out.other_status = (await otherWritesList(other, list, function (v) { v.push(rowB); })).status;
        api.app().financeRec[list].push(rowA);
        pushAudit("faud_a1", list === "day_closings" ? "day_actual" : "opening_edit", { new_value: 100 });
        out.collected = api.collect();
        out.save_result = await api.saveApp();
        out.server_list = serverFr(await getState(cookie))[list];
    } else if (SCENARIO === "render_sweep_period_states") {
        // Б закрыл неделю; у А отрисовка создала черновик недели и строку переноса (не сохранялись).
        out.other_status = (await otherWritesList(other, "period_states", function (v) {
            v.push({ finance_period_id: "fper_1", finance_area_id: AREA, period_key: WEEK, status: "closed", closed_by: "usr_dir" });
        })).status;
        simulateRender();
        out.collected_before_dividend = api.collect();
        out.save_result = await dividend("", 300);
        const st = await getState(cookie);
        out.server_period_states = serverFr(st).period_states;
        out.server_op_ids = (serverFr(st).operations || []).map(function (o) { return o.finance_operation_id; });
    } else if (SCENARIO === "render_sweep_no_conflict") {
        simulateRender();
        out.save_result = await dividend("", 300);
    } else if (SCENARIO === "dividend_success") {
        out.save_result = await dividend("", 300);
        out.local_div_ids = newDividendIds();
        const fr = serverFr(await getState(cookie));
        out.server_ops = fr.operations || [];
        out.server_audit = fr.audit || [];
    } else if (SCENARIO === "dividend_edit_conflict") {
        // Дивиденды fop_d0 уже на сервере; Б меняет сумму по id, А (старая версия) правит ту же запись.
        const seed = await postBatch(cookie, [{
            path: "financeRec.operations.fop_d0", expected_v: 0,
            value: { finance_operation_id: "fop_d0", amount: 50, is_deleted: false, movement_kind: "dividend", type: "out" }
        }]);
        out.seed_status = seed.status;
        api.load(await getState(cookie));
        const st = await getState(other);
        const cur = serverFr(st).operations.find(function (o) { return o.finance_operation_id === "fop_d0"; });
        out.other_status = (await postBatch(other, [{
            path: "financeRec.operations.fop_d0",
            expected_v: rootV(st, "financeRec.operations.fop_d0"),
            value: Object.assign({}, cur, { amount: 77 })
        }])).status;
        out.save_result = await dividend("fop_d0", 999);
        const local = api.app().financeRec.operations.find(function (o) { return o.finance_operation_id === "fop_d0"; });
        out.local_amount = local ? local.amount : null;
        out.local_audit_kinds = api.app().financeRec.audit.map(function (a) { return a.kind; });
        const fr = serverFr(await getState(cookie));
        out.server_amount = (fr.operations.find(function (o) { return o.finance_operation_id === "fop_d0"; }) || {}).amount;
        out.server_audit_kinds = (fr.audit || []).map(function (a) { return a.kind; });
    } else if (SCENARIO === "transfer_quick_from_transit") {
        api.setForm({
            "frTrFrom_2026-11-04": "fsrc_transit", "frTrTo_2026-11-04": "fsrc_cash",
            "frTrAmt_2026-11-04": "100", "frTrCom_2026-11-04": ""
        });
        out.ops_before = api.app().financeRec.operations.length;
        await Promise.resolve(api.frQuickAddTransfer("2026-11-04"));
        await api.settle();
        out.ops_after = api.app().financeRec.operations.length;
        out.server_op_count = (serverFr(await getState(cookie)).operations || []).length;
    } else if (SCENARIO === "transfer_modal_from_transit") {
        api.setForm({
            frTrId: "", frTrFromModal: "fsrc_transit", frTrToModal: "fsrc_cash",
            frTrAmtModal: "100", frTrDate: "2026-11-04", frTrComModal: ""
        });
        out.ops_before = api.app().financeRec.operations.length;
        await Promise.resolve(api.saveFrTransfer());
        await api.settle();
        out.ops_after = api.app().financeRec.operations.length;
        out.collected_after = api.collect();
    } else if (SCENARIO === "transit_send_partial") {
        // Б добавил операцию по id; у А (настоящий saveFrTransitSend) сервер отклоняет 3-ю часть пакета.
        out.other_status = (await postBatch(other, [{
            path: "financeRec.operations.fop_b1", expected_v: 0,
            value: { finance_operation_id: "fop_b1", amount: 5, is_deleted: false }
        }])).status;
        mutateBatch = function (body, n) { if (n === 1 && body.ops[2]) body.ops[2].expected_v = 999; };
        out.save_result = await sendToTransit(700);
        await dumpState(out);
    } else if (SCENARIO === "transit_send_ok") {
        out.save_result = await sendToTransit(700);
        const t = lastTransit();
        out.transit_id = t && t.transit_id;
        out.send_op = clone(t && sendOpOf(t.transit_id));
        out.transit_v = api.recordV("financeRec.money_transits." + out.transit_id);
        await dumpState(out);
        // Повторное чтение сервера (F5): тот же транзит, версия записи известна клиенту.
        api.load(await getState(cookie));
        out.reloaded_transits = clone(localFr().money_transits);
        out.reloaded_v = api.recordV("financeRec.money_transits." + out.transit_id);
    } else if (SCENARIO === "transit_close_partial_full") {
        out.send_result = await sendToTransit(700);
        const tid = lastTransit().transit_id;
        out.transit_id = tid;
        api.load(await getState(cookie));
        out.partial_result = await closeTransit(tid, 300);
        out.after_partial = clone(serverFr(await getState(cookie)).money_transits);
        out.full_result = await closeTransit(tid, 400);
        await dumpState(out);
    } else if (SCENARIO === "transit_two_create") {
        // Б (свой клиент, узкий пакет по id) создал транзит, А загружен раньше и создаёт другой.
        out.other_status = (await postBatch(other, [
            { path: "financeRec.operations.fop_b_ts", expected_v: 0, value: { finance_operation_id: "fop_b_ts", amount: 50, is_deleted: false, type: "transfer", transit_id: "ftr_001_bbbbb", transit_role: "send" } },
            { path: "financeRec.money_transits.ftr_001_bbbbb", expected_v: 0, value: { transit_id: "ftr_001_bbbbb", object_id: AREA, finance_area_id: AREA, amount_sent: 50, amount_closed: 0, amount_open: 50, status: "open", send_operation_id: "fop_b_ts", closes: [] } },
            { path: "financeRec.audit.faud_b_ts", expected_v: 0, value: { audit_id: "faud_b_ts", kind: "transit_send" } }
        ])).status;
        out.save_result = await sendToTransit(700);
        await dumpState(out);
    } else if (SCENARIO === "transit_two_edit_conflict") {
        out.send_result = await sendToTransit(700);
        const tid = lastTransit().transit_id;
        out.transit_id = tid;
        // Б закрыл часть того же транзита по актуальной версии.
        const st = await getState(other);
        const cur = clone(serverFr(st).money_transits.find(function (t) { return t.transit_id === tid; }));
        cur.amount_closed = 100;
        cur.closes.push({ close_id: "ftc_001", amount: 100, to_account_id: "fsrc_bank", operation_id: "fop_b_close" });
        out.other_status = (await postBatch(other, [{
            path: "financeRec.money_transits." + tid,
            expected_v: rootV(st, "financeRec.money_transits." + tid),
            value: cur
        }])).status;
        out.local_before = clone(lastTransit());
        out.close_result = await closeTransit(tid, 300);
        await dumpState(out);
    } else if (SCENARIO === "transit_send_network" || SCENARIO === "transit_send_fail_op2" || SCENARIO === "transit_send_fail_op3") {
        if (SCENARIO === "transit_send_network") networkFailBatchNo = 1;
        else {
            const idx = SCENARIO === "transit_send_fail_op2" ? 1 : 2;
            mutateBatch = function (body, n) { if (n === 1 && body.ops[idx]) body.ops[idx].expected_v = 999; };
        }
        out.save_result = await sendToTransit(700);
        await dumpState(out);
    } else if (SCENARIO === "transit_rollback_keeps_newer") {
        out.seed_result = await sendToTransit(100);
        const keepId = lastTransit().transit_id;
        out.keep_id = keepId;
        networkFailBatchNo = 2;
        // Пока пакет в пути: SoftSync/новая правка изменили другой транзит и только что созданный.
        inFlight = function (n) {
            if (n !== 2) return;
            const list = localFr().money_transits;
            list[0].comment = "правка во время сохранения";
            list[list.length - 1].comment = "новая правка нового транзита";
        };
        out.save_result = await sendToTransit(700);
        await dumpState(out);
    } else if (SCENARIO === "transit_double_click") {
        api.setForm({ frTsFrom: "fsrc_cash", frTsAmt: "700", frTsDate: "2026-11-04", frTsCom: "" });
        const p1 = Promise.resolve(api.saveFrTransitSend());
        const p2 = Promise.resolve(api.saveFrTransitSend());
        out.first = await p1;
        out.second = await p2;
        await api.settle();
        await dumpState(out);
    } else if (SCENARIO === "transit_delete_send" || SCENARIO === "transit_delete_send_fail") {
        out.send_result = await sendToTransit(700);
        const tid = lastTransit().transit_id;
        out.transit_id = tid;
        out.send_op_id = sendOpOf(tid).finance_operation_id;
        if (SCENARIO === "transit_delete_send_fail") failBatchNo = 2;
        out.delete_result = await Promise.resolve(api.frDeleteOp(out.send_op_id));
        await api.settle();
        out.local_send_op = clone(localFr().operations.find(function (o) { return o.finance_operation_id === out.send_op_id; }));
        await dumpState(out);
    } else if (SCENARIO === "transit_delete_blocked_partial" || SCENARIO === "transit_delete_close_op") {
        out.send_result = await sendToTransit(700);
        const tid = lastTransit().transit_id;
        out.transit_id = tid;
        out.close_result = await closeTransit(tid, 300);
        const closeOp = localFr().operations.find(function (o) { return o.transit_id === tid && o.transit_role === "close"; });
        out.close_op_id = closeOp && closeOp.finance_operation_id;
        out.target_id = SCENARIO === "transit_delete_close_op" ? out.close_op_id : sendOpOf(tid).finance_operation_id;
        out.delete_result = await Promise.resolve(api.frDeleteOp(out.target_id));
        await api.settle();
        await dumpState(out);
    } else if (SCENARIO === "transit_legacy_no_id") {
        // На сервере старый транзит без transit_id (записан широко); клиент присваивает id только локально.
        const st = await getState(other);
        out.other_status = (await postBatch(other, [{
            path: "financeRec.money_transits", expected_v: rootV(st, "financeRec.money_transits"),
            value: [{ object_id: AREA, finance_area_id: AREA, from_account_id: "fsrc_cash", transit_account_id: "fsrc_transit", amount_sent: 200, amount_closed: 0, closes: [] }]
        }])).status;
        api.load(await getState(cookie));
        out.ops_before = localFr().operations.length;
        out.close_result = await closeTransit("ftr_001", 50);
        out.local_ids = localFr().money_transits.map(function (t) { return t.transit_id; });
        await dumpState(out);
    } else if (SCENARIO === "transit_edit_send_below_received") {
        out.send_result = await sendToTransit(700);
        const tid = lastTransit().transit_id;
        out.transit_id = tid;
        out.close_result = await closeTransit(tid, 500);
        const sendId = sendOpOf(tid).finance_operation_id;
        api.setForm({
            frTrId: sendId, frTrFromModal: "fsrc_cash", frTrToModal: "fsrc_transit",
            frTrAmtModal: "300", frTrDate: "2026-11-04", frTrComModal: "в банк"
        });
        out.edit_result = await Promise.resolve(api.saveFrTransfer());
        await api.settle();
        await dumpState(out);
    } else if (SCENARIO === "transit_quick_to_transit") {
        api.setForm({
            "frTrFrom_2026-11-04": "fsrc_cash", "frTrTo_2026-11-04": "fsrc_transit",
            "frTrAmt_2026-11-04": "250", "frTrCom_2026-11-04": ""
        });
        out.save_result = await Promise.resolve(api.frQuickAddTransfer("2026-11-04"));
        await api.settle();
        await dumpState(out);
    } else if (SCENARIO === "dividend_network_error" || SCENARIO === "dividend_server_500") {
        if (SCENARIO === "dividend_network_error") networkFailBatchNo = 1;
        else failBatchNo = 1;
        out.save_result = await dividend("", 300);
        out.local_op_ids = api.app().financeRec.operations.map(function (o) { return o.finance_operation_id; });
        out.local_audit_ids = api.app().financeRec.audit.map(function (a) { return a.audit_id; });
        out.server_op_ids = (serverFr(await getState(cookie)).operations || []).map(function (o) { return o.finance_operation_id; });
    } else {
        throw new Error("unknown scenario " + SCENARIO);
    }
    out.log = log;
    return out;
}

main().then(function (out) {
    process.stdout.write("RESULT_JSON " + JSON.stringify(out) + "\n");
}, function (err) {
    process.stdout.write("RESULT_ERR " + String(err && err.stack || err) + "\n");
    process.exitCode = 1;
});
