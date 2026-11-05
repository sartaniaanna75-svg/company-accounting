/**
 * Узкое сохранение employeeIds (toggleAssign / setTimesheetEmployee) против FakeConn.
 * Запуск: server/test_wt_timesheet_assign_ops.py
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "app.html"), "utf8").replace(/\r\n/g, "\n");
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
    "wtWorkspaceEmployeeIdsPath", "wtSaveWorkspaceEmployeeIds", "wtRestoreWorkspaceEmployeeIdsFromBaseline",
    "wtSoftMarkDirty", "wtSoftClearDirty", "wtSoftProtectPendingLeaves", "wtSoftHasDirty", "wtSoftDirtyHasPrefix",
    "wtIsPlainObject"
];

let cookie = "";
const log = { toasts: [], batches: [], puts: 0, confirms: [] };

function hostConfirm(msg) {
    const ans = confirmAnswers.length ? !!confirmAnswers.shift() : false;
    log.confirms.push({ answer: ans, text: String(msg).split("\n").slice(0, 2).join(" | ") });
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
        let body = null;
        try { body = JSON.parse(opts.body); } catch (e) { body = null; }
        const entry = {
            n: log.batches.length + 1,
            atomic: !!(body && body.atomic),
            paths: ((body && body.ops) || []).map(function (o) { return o.path; }),
            values: ((body && body.ops) || []).map(function (o) { return o.value; }),
            status: 0,
            results: []
        };
        log.batches.push(entry);
        return rawFetch(url, opts).then(function (res) {
            entry.status = res.status;
            return res.text().then(function (t) {
                try {
                    const b = JSON.parse(t) || {};
                    entry.results = (b.results || []).map(function (r) {
                        return r.path + ":" + (r.status || r.error);
                    });
                } catch (e) {}
                return new Response(t, { status: res.status, headers: { "Content-Type": "application/json" } });
            });
        });
    }
    if (url === "/api/state" && opts && String(opts.method || "").toUpperCase() === "PUT") {
        log.puts += 1;
    }
    return rawFetch(url, opts);
}

const prelude = `
var window = { confirm: __confirm };
var localStorage = { _m: {}, getItem: function (k) { return this._m[k] || null; }, setItem: function (k, v) { this._m[k] = String(v); } };
var fetch = __clientFetch;
var document = { hidden: false, getElementById: function () { return null; }, activeElement: null };
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
var wtOpsUiEnabledCache = true;
var wtDomainWriterDeferredPending = false;
var wtPutPayloadOverride = null;
var wtSoftDirtyPaths = Object.create(null);
var wtMeIdentityCache = { at: Date.now(), userId: "user_admin" };
var wtMeConfirmInflight = null;
var wtServerUser = { user_id: "user_admin", role: "admin", legacy_role: "admin", is_active: true, permissions: {}, departments: ["shop", "summary"] };
var currentUserId = "user_admin";
function toast(msg, kind) { __log.toasts.push({ msg: String(msg), kind: kind || "" }); }
function wtSoftFlushPendingRender() {}
function wtDomainWriterScheduleDeferred() {}
function wtDomainWriterMarkPendingDraft() {}
function wtDomainWriterLeavesFromDirty() { return []; }
function wtFilterPositionCatalogLeavesForSession(l) { return l || []; }
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
`;

const code = prelude
    + extractConst(APP, "WT_KEYED_LIST_PATHS") + "\n"
    + extractConst(APP, "WT_DOMAIN_TRACKED_TOPS") + "\n"
    + extractConst(APP, "WT_FR_ROW_KEYED_ROOTS") + "\n"
    + "const WT_OPS_FETCH_TIMEOUT_MS = 30000;\n"
    + (/^const WT_ATOMIC_YOURS_MAX_ROUNDS = .*$/m.exec(APP) || ["const WT_ATOMIC_YOURS_MAX_ROUNDS = 5;"])[0] + "\n"
    + APP_FNS.map(function (n) { return extractFn(APP, n); }).join("\n")
    + `
__api.load = function (state) {
    appData = JSON.parse(JSON.stringify(state.payload));
    wtLastLoadedPayload = JSON.parse(JSON.stringify(state.payload));
    wtStateVersion = Number(state.version) || 0;
    wtSoftAppliedVersion = wtStateVersion;
    wtSoftDirtyPaths = Object.create(null);
};
__api.ids = function (code) {
    code = code || "warehouse";
    const w = (appData.workspaces || []).find(function (x) { return x && x.code === code; });
    return w ? (w.employeeIds || []).slice() : null;
};
__api.ws = function (code) {
    return (appData.workspaces || []).find(function (x) { return x && x.code === code; });
};
__api.injectNoiseLeaves = function (n) {
    // Имитация сотен локальных расхождений относительно baseline (как на экране админа).
    if (!appData.shopMetrics) appData.shopMetrics = {};
    if (!wtLastLoadedPayload.shopMetrics) wtLastLoadedPayload.shopMetrics = {};
    for (let i = 0; i < n; i++) {
        const key = "noise_" + i;
        appData.shopMetrics[key] = { v: i };
        // baseline без этих ключей → collect увидит n листьев shopMetrics.*
    }
};
__api.collectCount = function () {
    return wtCollectChangedLeaves(wtLastLoadedPayload || {}, appData).length;
};
__api.saveIds = function (code, ids, silent) {
    const w = __api.ws(code || "warehouse");
    w.employeeIds = ids.slice();
    return wtSaveWorkspaceEmployeeIds(w, { silentFail: !!silent });
};
__api.restore = function (code) {
    wtRestoreWorkspaceEmployeeIdsFromBaseline(__api.ws(code || "warehouse"));
};
__api.settle = function () {
    let rounds = 0;
    function step() {
        const chain = wtOpsChain;
        return chain.then(function () {
            return new Promise(function (r) { setTimeout(r, 40); });
        }).then(function () {
            rounds += 1;
            if (chain !== wtOpsChain && rounds < 40) return step();
            return true;
        });
    }
    return step();
};
__api.setUser = function (uid, role) {
    wtServerUser = {
        user_id: uid, role: role || "user", legacy_role: role || "staff",
        is_active: true, permissions: { timesheet: "edit" }, departments: ["warehouse"]
    };
    currentUserId = uid;
    wtMeIdentityCache = { at: Date.now(), userId: uid };
};
`;

const api = {};
const ctx = vm.createContext({
    __api: api, __log: log, __clientFetch: clientFetch, __confirm: hostConfirm,
    console, setTimeout, clearTimeout, Promise, JSON, Math, Date, Number, String, Object, Array, Error,
    AbortController, Response
});
vm.runInContext(code, ctx, { filename: "wt_timesheet_assign_harness.vm.js" });

async function login(userId, pin) {
    const res = await fetch(BASE + "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, pin: pin })
    });
    const m = /wt_session=[^;]+/.exec(res.headers.get("set-cookie") || "");
    if (!m) throw new Error("login failed " + res.status);
    cookie = m[0];
}

async function getState() {
    const r = await fetch(BASE + "/api/state", { headers: { Cookie: cookie } });
    return r.json();
}

async function main() {
    await login("user_admin", "5555");
    const state = await getState();
    api.load(state);
    const out = { scenario: SCENARIO };

    if (SCENARIO === "add_f5") {
        out.before = api.ids("warehouse");
        out.save = await Promise.resolve(api.saveIds("warehouse", ["e_wh", "e_tat"], true));
        await api.settle();
        out.local = api.ids("warehouse");
        out.afterF5 = ((await getState()).payload.workspaces.find(function (w) { return w.code === "warehouse"; }) || {}).employeeIds;
        out.shopAfter = ((await getState()).payload.workspaces.find(function (w) { return w.code === "shop"; }) || {}).employeeIds;
        out.log = log;
        return out;
    }

    if (SCENARIO === "remove_f5") {
        // Сначала добавить
        await Promise.resolve(api.saveIds("warehouse", ["e_wh", "e_tat"], true));
        await api.settle();
        api.load(await getState());
        out.save = await Promise.resolve(api.saveIds("warehouse", ["e_wh"], true));
        await api.settle();
        out.afterF5 = ((await getState()).payload.workspaces.find(function (w) { return w.code === "warehouse"; }) || {}).employeeIds;
        out.log = log;
        return out;
    }

    if (SCENARIO === "noise_409") {
        api.injectNoiseLeaves(408);
        out.collectCount = api.collectCount();
        out.save = await Promise.resolve(api.saveIds("warehouse", ["e_wh", "e_tat"], true));
        await api.settle();
        out.afterF5 = ((await getState()).payload.workspaces.find(function (w) { return w.code === "warehouse"; }) || {}).employeeIds;
        out.batchPaths = log.batches.map(function (b) { return b.paths; });
        out.batchSizes = log.batches.map(function (b) { return b.paths.length; });
        out.toasts = log.toasts;
        out.log = log;
        return out;
    }

    if (SCENARIO === "forbidden_revert") {
        // staff без manager — сервер отклонит employeeIds
        await login("usr_wh", "2222");
        api.setUser("usr_wh", "user");
        api.load(await getState());
        const before = api.ids("warehouse");
        out.before = before;
        out.save = await Promise.resolve(api.saveIds("warehouse", ["e_wh", "e_tat"], true));
        await api.settle();
        if (!out.save) api.restore("warehouse");
        out.local = api.ids("warehouse");
        out.afterF5 = ((await getState()).payload.workspaces.find(function (w) { return w.code === "warehouse"; }) || {}).employeeIds;
        out.log = log;
        return out;
    }

    if (SCENARIO === "two_users_conflict") {
        // A сохраняет [e_wh, e_tat]
        out.saveA = await Promise.resolve(api.saveIds("warehouse", ["e_wh", "e_tat"], true));
        await api.settle();
        // B: устаревший expected_v=0 и старый список без tat — conflict
        const other = await login("user_admin", "5555");
        void other;
        const st = await getState();
        const path = "workspaces.warehouse.employeeIds";
        // Голый OPS от «второй вкладки» со stale v
        const res = await fetch(BASE + "/api/state/op/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json", Cookie: cookie },
            body: JSON.stringify({
                idempotency_key: "stale_b",
                atomic: false,
                ops: [{ path: path, expected_v: 0, value: ["e_wh"] }]
            })
        });
        const body = await res.json();
        out.bStatus = res.status;
        out.bBody = body;
        out.db = ((await getState()).payload.workspaces.find(function (w) { return w.code === "warehouse"; }) || {}).employeeIds;
        out.log = log;
        return out;
    }

    throw new Error("unknown scenario " + SCENARIO);
}

main().then(function (out) {
    process.stdout.write("RESULT_JSON " + JSON.stringify(out) + "\n");
}, function (err) {
    process.stdout.write("RESULT_ERR " + String(err && err.stack || err) + "\n");
    process.exitCode = 1;
});
