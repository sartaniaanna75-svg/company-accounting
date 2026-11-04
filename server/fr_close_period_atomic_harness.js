/**
 * Сквозной сценарий Финконтроля против локального wt_api (FakeConn, 127.0.0.1).
 * Выполняет реальные функции DomainWriter из app.html и FR из fr-mod.js.
 * Запускается из server/test_fr_close_period_atomic.py (WT_TEST_BASE_URL, WT_TEST_SCENARIO).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "app.html"), "utf8").replace(/\r\n/g, "\n");
const FR = fs.readFileSync(path.join(ROOT, "fr-mod.js"), "utf8").replace(/\r\n/g, "\n");
const BASE = process.env.WT_TEST_BASE_URL;
const SCENARIO = process.env.WT_TEST_SCENARIO || "manual_then_close";
// Сценарии с настоящим диалогом конфликта (wtResolveRecordConflict / wtSaveRecordOp из app.html).
const REAL_DIALOG_ANSWERS = {
    yours_atomic_success: [true],
    yours_atomic_retry_fail: [true],
    yours_cancel: [false, false]
};
const REAL_DIALOG = Object.prototype.hasOwnProperty.call(REAL_DIALOG_ANSWERS, SCENARIO);
const confirmAnswers = (REAL_DIALOG_ANSWERS[SCENARIO] || []).slice();

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
    "wtDomainWriterOpsFromLeaves", "wtDomainWriterSave", "saveApp", "wtOpsFetchWithTimeout"
];
const FR_FNS = [
    "frSnapFinanceKeys", "frRestoreFinanceKeys", "frAwaitCriticalSave", "frOpPathById", "frSnapshotApplyConfirmed",
    "frFindOpById", "frCloneFinanceJson", "frSnapOpsByIds", "frRestoreOpsSnap", "frUniqueOpIds",
    "frMoneyOpsPasteLocked", "frPersistOpsById", "frAuditLeaf", "frDropLocalAuditIds",
    "frBeginMoneyOpsSnapshot", "frCollectChangedOpIds", "frQueueMoneyOpIds", "frFlushMoneyOpPasteQueue"
];
// Есть не во всех версиях fr-mod.js (стенд должен запускаться и до исправления).
const FR_FNS_OPTIONAL = ["frSnapOpsFromBefore"];

const log = { toasts: [], batches: [], confirms: 0, recordDialogs: [], puts: 0, singleOps: [] };
let cookie = "";
let failNthCloseBatch = 0;
let batchCounter = 0;
// После выбора «мой вариант» следующий batch получит отказ сервера (для yours_atomic_retry_fail).
let failBatchAfterYours = false;
let yoursChosen = false;
// Откат ручной операции: сетевой отказ / 500 следующего batch и действие «во время запроса».
let networkFailBatches = 0;
let serverFailBatches = 0;
let duringBatchHook = null;

function hostConfirm() {
    log.confirms++;
    const ans = confirmAnswers.length ? !!confirmAnswers.shift() : false;
    if (ans) yoursChosen = true;
    return ans;
}

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
            conflictPath: ""
        };
        log.batches.push(entry);
        if (duringBatchHook) {
            const hook = duringBatchHook;
            duringBatchHook = null;
            hook();
        }
        if (networkFailBatches > 0) {
            networkFailBatches -= 1;
            entry.status = -1;
            return Promise.reject(new TypeError("fetch failed"));
        }
        if (serverFailBatches > 0) {
            serverFailBatches -= 1;
            entry.status = 500;
            return Promise.resolve(new Response(JSON.stringify({ error: "db unavailable" }), { status: 500 }));
        }
        const failAfterYours = failBatchAfterYours && yoursChosen;
        if (failAfterYours) failBatchAfterYours = false;
        if ((failNthCloseBatch && batchCounter === failNthCloseBatch) || failAfterYours) {
            entry.status = 500;
            return Promise.resolve(new Response(JSON.stringify({ error: "db unavailable" }), { status: 500 }));
        }
        return rawFetch(url, opts).then(function (res) {
            entry.status = res.status;
            return res.text().then(function (t) {
                try { entry.conflictPath = (JSON.parse(t) || {}).path || ""; } catch (e) {}
                return new Response(t, { status: res.status, headers: { "Content-Type": "application/json" } });
            });
        });
    }
    if (url === "/api/state/op") {
        let body = null;
        try { body = JSON.parse(opts.body); } catch (e) { body = null; }
        const entry = { path: body && body.op && body.op.path, status: 0 };
        log.singleOps.push(entry);
        return rawFetch(url, opts).then(function (res) {
            entry.status = res.status;
            return res;
        });
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
var frUi = {};
function toast(msg, kind) { __log.toasts.push({ msg: String(msg), kind: kind || "" }); }
function wtSoftMarkDirty() {}
function wtSoftClearDirty() {}
function wtSoftProtectPendingLeaves() {}
function wtSoftFlushPendingRender() {}
function wtDomainWriterScheduleDeferred() {}
function wtDomainWriterMarkPendingDraft() {}
function wtDomainWriterLeavesFromDirty() { return []; }
function wtCollectChangedLeaves() { return []; }
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
function wtResolveRecordConflict(arg) { __log.recordDialogs.push(arg.path); return Promise.resolve("server"); }
function wtSaveRecordOp() { return Promise.resolve({ status: "error" }); }
function renderFinanceRec() {}
function currentAuthUserId() { return "user_admin"; }
function isAppAdmin() { return true; }
function ensureFinanceRec() {
    if (!appData.financeRec) appData.financeRec = {};
    ["operations", "audit", "openings", "period_states", "import_batches"].forEach(function (k) {
        if (!Array.isArray(appData.financeRec[k])) appData.financeRec[k] = [];
    });
}
`;

const code = prelude
    + extractConst(APP, "WT_KEYED_LIST_PATHS") + "\n"
    + "const WT_OPS_FETCH_TIMEOUT_MS = 30000;\n"
    + /^const WT_ATOMIC_YOURS_MAX_ROUNDS = .*$/m.exec(APP)[0] + "\n"
    + APP_FNS.map(function (n) { return extractFn(APP, n); }).join("\n")
    + "\n" + extractConst(FR, "FR_SNAPSHOT_LIST_ID_FIELDS") + "\n"
    + FR_FNS.map(function (n) { return extractFn(FR, n); }).join("\n")
    + "\n" + FR_FNS_OPTIONAL.map(function (n) {
        try { return extractFn(FR, n); } catch (e) { return ""; }
    }).join("\n")
    + (REAL_DIALOG
        ? "\n" + ["wtFormatConflictValue", "wtResolveRecordConflict", "wtSaveRecordOp"]
            .map(function (n) { return extractFn(APP, n); }).join("\n")
        : "")
    + `
__api.load = function (state) {
    appData = JSON.parse(JSON.stringify(state.payload));
    wtLastLoadedPayload = JSON.parse(JSON.stringify(state.payload));
    wtStateVersion = Number(state.version) || 0;
};
__api.app = function () { return appData; };
__api.base = function () { return wtLastLoadedPayload; };
__api.frPersistOpsById = frPersistOpsById;
__api.frAuditLeaf = frAuditLeaf;
__api.frSnapFinanceKeys = frSnapFinanceKeys;
__api.frAwaitCriticalSave = frAwaitCriticalSave;
__api.frSnapshotApplyConfirmed = frSnapshotApplyConfirmed;
__api.wtSetLocalRecordMeta = wtSetLocalRecordMeta;
__api.wtRecordVersion = wtRecordVersion;
__api.frBeginMoneyOpsSnapshot = frBeginMoneyOpsSnapshot;
__api.frCollectChangedOpIds = frCollectChangedOpIds;
__api.frFlushMoneyOpPasteQueue = frFlushMoneyOpPasteQueue;
__api.frUi = function () { return frUi; };
`;

const api = {};
const ctx = vm.createContext({
    __api: api, __log: log, __clientFetch: clientFetch, __confirm: hostConfirm,
    console, setTimeout, clearTimeout, Promise, JSON, Math, Date, Number, String, Object, Array, Error,
    AbortController, Response
});
vm.runInContext(code, ctx, { filename: "fr_close_period_atomic_harness.vm.js" });

function login(userId, pin) {
    return fetch(BASE + "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, pin: pin })
    }).then(function (res) {
        const sc = res.headers.get("set-cookie") || "";
        const m = /wt_session=[^;]+/.exec(sc);
        if (!m) throw new Error("login failed " + res.status);
        return m[0];
    });
}

function getState(withCookie) {
    return fetch(BASE + "/api/state", { headers: { Cookie: withCookie } }).then(function (r) { return r.json(); });
}

function postBatch(withCookie, ops) {
    return fetch(BASE + "/api/state/op/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json", Cookie: withCookie },
        body: JSON.stringify({ atomic: true, ops: ops })
    }).then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); });
}

function rootV(state, p) {
    const m = (state.payload && state.payload._wt_records) || {};
    return m[p] ? Number(m[p].v) || 0 : 0;
}

const AREA = "farea_shop";
const WEEK = "2026-W45";
const NEXT = "2026-W46";

function manualOp(id, auditId, amount) {
    const app = api.app();
    app.financeRec.operations.push({
        finance_operation_id: id, amount: amount, is_deleted: false, finance_area_id: AREA, period_key: WEEK,
        data_source: "manual"
    });
    app.financeRec.audit.push({ audit_id: auditId, kind: "op_add", finance_area_id: AREA, period_key: WEEK, new_value: amount });
    const leaf = api.frAuditLeaf(auditId);
    return api.frPersistOpsById([id], {
        atomic: true,
        createdIds: [id],
        extraLeaves: leaf ? [leaf] : [],
        createdAuditIds: [auditId],
        failToast: "Операция не записана"
    });
}

function closePeriod(closeAuditId) {
    frUiReset();
    const app = api.app();
    const snap = api.frSnapFinanceKeys(["period_states", "openings", "audit"]);
    app.financeRec.period_states.push({ finance_area_id: AREA, period_key: WEEK, status: "closed", closed_by: "user_admin" });
    app.financeRec.openings.push({ finance_area_id: AREA, period_key: NEXT, finance_source_id: "fsrc_cash", value: 500, carried: true });
    app.financeRec.audit.push({ audit_id: closeAuditId, kind: "close", finance_area_id: AREA, period_key: WEEK, new_value: "closed" });
    return api.frAwaitCriticalSave(
        snap,
        ["financeRec.period_states", "financeRec.openings", "financeRec.audit"],
        "Период закрыт.",
        "Период не закрыт на сервере."
    );
}

function frUiReset() { vm.runInContext("frUi = {};", ctx); }

function serverSummary(state) {
    const fr = (state.payload && state.payload.financeRec) || {};
    const ids = function (list, key) { return (list || []).map(function (x) { return x && x[key]; }); };
    return {
        period_closed: (fr.period_states || []).some(function (s) { return s && s.period_key === WEEK && s.status === "closed"; }),
        period_states: fr.period_states || [],
        opening_carried: (fr.openings || []).some(function (r) { return r && r.period_key === NEXT && r.carried; }),
        audit_ids: ids(fr.audit, "audit_id"),
        op_ids: ids(fr.operations, "finance_operation_id"),
        batch_ids: ids(fr.import_batches, "import_batch_id")
    };
}

function localSummary() {
    const fr = api.app().financeRec;
    return {
        period_closed: (fr.period_states || []).some(function (s) { return s && s.period_key === WEEK && s.status === "closed"; }),
        audit_ids: fr.audit.map(function (a) { return a.audit_id; }),
        op_ids: fr.operations.map(function (o) { return o.finance_operation_id; })
    };
}

async function importBulk(ourCookie) {
    // Как frSaveFinanceImportArraysOps: $bulk + партия + журнал одним atomic batch, затем версии/снимок.
    const app = api.app();
    const ops = [
        { finance_operation_id: "fop_imp1", amount: 11, is_deleted: false, import_batch_id: "fimp_t1" },
        { finance_operation_id: "fop_imp2", amount: 12, is_deleted: false, import_batch_id: "fimp_t1" }
    ];
    const batch = { import_batch_id: "fimp_t1", batch_key: "file_t1", rows_added: 2 };
    const aud = { audit_id: "faud_imp1", kind: "expense_import", detail: "fimp_t1" };
    ops.forEach(function (o) { app.financeRec.operations.push(o); });
    app.financeRec.import_batches.push(batch);
    app.financeRec.audit.push(aud);
    const res = await postBatch(ourCookie, [
        { path: "financeRec.operations.$bulk", expected_v: api.wtRecordVersion("financeRec.operations.$bulk"),
          value: ops.map(function (o) { return { expected_v: 0, value: o }; }) },
        { path: "financeRec.import_batches.fimp_t1", expected_v: 0, value: batch },
        { path: "financeRec.audit.faud_imp1", expected_v: 0, value: aud }
    ]);
    (res.body.results || []).forEach(function (row) {
        if (row.path === "financeRec.operations.$bulk") {
            (row.items || []).forEach(function (it) {
                api.wtSetLocalRecordMeta(it.path, it.record_v);
                const id = String(it.path).split(".")[2];
                const rec = ops.find(function (o) { return o.finance_operation_id === id; });
                api.frSnapshotApplyConfirmed(it.path, rec);
            });
            return;
        }
        api.wtSetLocalRecordMeta(row.path, row.record_v);
        api.frSnapshotApplyConfirmed(row.path, row.value);
    });
    return res.status;
}

function opAmounts(list) {
    const m = {};
    (list || []).forEach(function (o) { if (o && o.finance_operation_id) m[o.finance_operation_id] = o.amount; });
    return m;
}

function findLocalOp(id) {
    return api.app().financeRec.operations.find(function (o) { return o && o.finance_operation_id === id; });
}

// Правка существующей операции как в frCommit*/saveFrOp: снимок до мутации передаётся в persist.
function editOp(id, amount, auditId, extra) {
    const app = api.app();
    const before = api.frBeginMoneyOpsSnapshot();
    findLocalOp(id).amount = amount;
    app.financeRec.audit.push({ audit_id: auditId, kind: "op_edit", new_value: amount });
    const ids = api.frCollectChangedOpIds(before);
    const leaf = api.frAuditLeaf(auditId);
    return api.frPersistOpsById(ids, Object.assign({
        atomic: true,
        before: before,
        extraLeaves: leaf ? [leaf] : [],
        createdAuditIds: [auditId],
        failToast: "Правка не записана"
    }, extra || {}));
}

function createWithBefore(id, amount) {
    const before = api.frBeginMoneyOpsSnapshot();
    api.app().financeRec.operations.push({ finance_operation_id: id, amount: amount, is_deleted: false, data_source: "paste" });
    return api.frPersistOpsById(api.frCollectChangedOpIds(before), { atomic: true, before: before });
}

async function runRollbackScenario(out) {
    if (SCENARIO === "rollback_create_network") {
        networkFailBatches = 1;
        out.result = await manualOp("fop_m1", "faud_m1", 100);
    } else if (SCENARIO === "rollback_create_server_500") {
        serverFailBatches = 1;
        out.result = await manualOp("fop_m1", "faud_m1", 100);
    } else if (SCENARIO === "rollback_edit_network") {
        networkFailBatches = 1;
        out.result = await editOp("fop_a", 555, "faud_e1");
    } else if (SCENARIO === "rollback_keeps_newer_local") {
        networkFailBatches = 1;
        duringBatchHook = function () {
            // Пока запрос в пути: пользователь снова правит ту же операцию, SoftSync приносит чужую.
            findLocalOp("fop_a").amount = 777;
            api.app().financeRec.operations.push({ finance_operation_id: "fop_other", amount: 42, is_deleted: false });
        };
        out.result = await editOp("fop_a", 555, "faud_e1");
    } else if (SCENARIO === "rollback_paste_queue") {
        const ui = api.frUi();
        ui._niPasteLock = true;
        ui._pasteOpIds = {};
        out.queued = [await editOp("fop_a", 201, "faud_p1"), await createWithBefore("fop_p1", 50)];
        ui._niPasteLock = false;
        networkFailBatches = 1;
        out.result = await api.frFlushMoneyOpPasteQueue({ atomic: true, failToast: "Вставка не записана" });
    } else if (SCENARIO === "rollback_busy") {
        api.frUi()._moneyOpsBusy = true;
        out.result = await editOp("fop_a", 333, "faud_b1");
        out.busy_after = !!api.frUi()._moneyOpsBusy;
    } else if (SCENARIO === "rollback_success_control") {
        out.result = await editOp("fop_a", 555, "faud_e1");
    }
}

async function main() {
    cookie = await login("user_admin", "5555");
    const other = await login("usr_dir", "4444");
    const initial = await getState(cookie);
    api.load(initial);
    const out = { scenario: SCENARIO };
    out.audit_root_v0 = rootV(initial, "financeRec.audit");

    if (SCENARIO === "ops_disabled_manual") {
        out.manual_ok = await manualOp("fop_m1", "faud_m1", 100);
        out.local = localSummary();
        out.server = serverSummary(await getState(cookie));
        out.log = log;
        return out;
    }

    if (SCENARIO.indexOf("rollback_") === 0) {
        await runRollbackScenario(out);
        out.local = localSummary();
        out.local_amounts = opAmounts(api.app().financeRec.operations);
        const st = await getState(cookie);
        out.server = serverSummary(st);
        out.server_amounts = opAmounts(((st.payload || {}).financeRec || {}).operations);
        out.log = { toasts: log.toasts, batches: log.batches, puts: log.puts };
        return out;
    }

    if (SCENARIO === "import_then_close") {
        out.import_status = await importBulk(cookie);
    } else {
        out.manual_ok = await manualOp("fop_m1", "faud_m1", 100);
    }
    const afterOp = await getState(cookie);
    out.audit_root_v1 = rootV(afterOp, "financeRec.audit");
    out.local_audit_root_v = api.wtRecordVersion("financeRec.audit");
    const batchesBeforeClose = log.batches.length;

    if (SCENARIO === "other_user_audit") {
        const r = await postBatch(other, [
            { path: "financeRec.audit.faud_other", expected_v: 0, value: { audit_id: "faud_other", kind: "op_add" } }
        ]);
        out.other_status = r.status;
    }
    if (SCENARIO === "concurrent_period_states" || REAL_DIALOG) {
        const st = await getState(other);
        const r = await postBatch(other, [{
            path: "financeRec.period_states",
            expected_v: rootV(st, "financeRec.period_states"),
            value: [{ finance_area_id: AREA, period_key: WEEK, status: "review", by: "usr_dir" }]
        }]);
        out.other_status = r.status;
    }
    if (SCENARIO === "retry_server_fail") {
        // Первый close-пакет получит 409 по журналу, повтор — отказ сервера 500.
        failNthCloseBatch = batchCounter + 2;
    }
    if (SCENARIO === "yours_atomic_retry_fail") failBatchAfterYours = true;

    out.close_ok = await closePeriod("faud_close");
    out.close_batches = log.batches.slice(batchesBeforeClose);
    out.local = localSummary();
    out.server = serverSummary(await getState(cookie));
    out.log = {
        toasts: log.toasts, confirms: log.confirms, recordDialogs: log.recordDialogs, puts: log.puts,
        singleOps: log.singleOps
    };
    return out;
}

main().then(function (out) {
    process.stdout.write("RESULT_JSON " + JSON.stringify(out) + "\n");
}, function (err) {
    process.stdout.write("RESULT_ERR " + String(err && err.stack || err) + "\n");
    process.exitCode = 1;
});
