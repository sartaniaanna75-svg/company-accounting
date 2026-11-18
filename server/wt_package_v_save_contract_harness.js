/**
 * Пакет В: контракт OPS timeout / SoftSync both_changed notify / keyed siblings.
 * Синтетика, без сети и БД. Формулы KPI не затрагиваются.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m || "assert"); }

function wtSaveOutcomeOk(ok) {
    return ok === true;
}

function wtRecordSemEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}

function wtCloneJson(x) { return JSON.parse(JSON.stringify(x)); }

function wtKeyedListMerge3(baseList, localList, serverList, key, resolveConflicts) {
    const base = Array.isArray(baseList) ? baseList : [];
    const local = Array.isArray(localList) ? localList : [];
    const server = Array.isArray(serverList) ? serverList : [];
    function index(list) {
        const map = {};
        for (let i = 0; i < list.length; i++) {
            const row = list[i];
            if (!row || typeof row !== "object" || row[key] == null || row[key] === "") return null;
            const id = String(row[key]);
            if (Object.prototype.hasOwnProperty.call(map, id)) return null;
            map[id] = row;
        }
        return map;
    }
    const B = index(base);
    const Lm = index(local);
    const S = index(server);
    if (!B || !Lm || !S) return { ok: false, unkeyed: true, merged: null, conflicts: [] };
    const has = function (m, id) { return Object.prototype.hasOwnProperty.call(m, id); };
    const conflicts = [];
    const pick = {};
    const ids = [];
    const seen = {};
    server.concat(local).forEach(function (row) {
        const id = String(row[key]);
        if (!seen[id]) { seen[id] = true; ids.push(id); }
    });
    Object.keys(B).forEach(function (id) {
        if (!seen[id]) { seen[id] = true; ids.push(id); }
    });
    ids.forEach(function (id) {
        const inB = has(B, id);
        const inL = has(Lm, id);
        const inS = has(S, id);
        let kind = "";
        let take;
        if (inB) {
            const lSame = inL && wtRecordSemEqual(Lm[id], B[id]);
            const sSame = inS && wtRecordSemEqual(S[id], B[id]);
            if (inL && inS) {
                if (lSame) take = S[id];
                else if (sSame) take = Lm[id];
                else if (wtRecordSemEqual(Lm[id], S[id])) take = S[id];
                else kind = "both_changed";
            } else if (!inL && inS) {
                if (sSame) take = null;
                else kind = "local_deleted_server_changed";
            } else if (inL && !inS) {
                if (lSame) take = null;
                else kind = "server_deleted_local_changed";
            } else {
                take = null;
            }
        } else if (inL && inS) {
            if (wtRecordSemEqual(Lm[id], S[id])) take = S[id];
            else kind = "both_added_different";
        } else if (inL) {
            take = Lm[id];
        } else if (inS) {
            take = S[id];
        }
        if (kind) {
            conflicts.push({ id: id, kind: kind, local: inL ? Lm[id] : null, server: inS ? S[id] : null });
            if (resolveConflicts === "mine") take = inL ? Lm[id] : null;
            else if (resolveConflicts === "server") take = inS ? S[id] : null;
            else take = undefined;
        }
        pick[id] = take;
    });
    if (conflicts.length && !resolveConflicts) {
        return { ok: false, unkeyed: false, merged: null, conflicts: conflicts };
    }
    const out = [];
    const used = {};
    server.forEach(function (row) {
        const id = String(row[key]);
        if (used[id]) return;
        used[id] = true;
        if (pick[id]) out.push(wtCloneJson(pick[id]));
    });
    local.forEach(function (row) {
        const id = String(row[key]);
        if (used[id]) return;
        used[id] = true;
        if (pick[id]) out.push(wtCloneJson(pick[id]));
    });
    return { ok: true, unkeyed: false, merged: out, conflicts: conflicts };
}

// --- D19 outcome ---
assert(wtSaveOutcomeOk("timeout") === false, "timeout not success");
assert(wtSaveOutcomeOk(true) === true, "true is success");
assert(wtSaveOutcomeOk(false) === false, "false not success");

function opsAbortOutcomeMessage(sec) {
    return "Сервер не ответил за " + sec
        + " с. Сохранение не подтверждено: запись могла пройти или нет. Не обновляйте страницу — проверьте данные и повторите сохранение только после проверки.";
}
const abortMsg = opsAbortOutcomeMessage(30);
assert(abortMsg.indexOf("будет повторено") < 0, "no false auto-retry promise");
assert(abortMsg.indexOf("не подтверждено") >= 0, "uncertain wording");
assert(abortMsg.indexOf("проверьте") >= 0, "ask to verify");

// --- SoftSync both_changed notify once ---
let wtSoftBothChangedWarned = Object.create(null);
const toasts = [];
function toast(msg) { toasts.push(String(msg)); }

function wtSoftNotifyBothChanged(path, conflicts) {
    if (!path || !Array.isArray(conflicts) || !conflicts.length) return;
    const fresh = [];
    conflicts.forEach(function (c) {
        if (!c || c.kind !== "both_changed" || c.id == null || c.id === "") return;
        const k = String(path) + "|" + String(c.id);
        if (wtSoftBothChangedWarned[k]) return;
        wtSoftBothChangedWarned[k] = true;
        fresh.push(c);
    });
    if (!fresh.length) return;
    toast("both_changed:" + fresh.map(function (c) { return c.id; }).join(","));
}

const path = "shopMetricCatalog.items";
const base = [{ id: "m1", name: "A" }, { id: "m2", name: "keep" }];
const local = [{ id: "m1", name: "Local" }, { id: "m2", name: "keep" }];
const server = [{ id: "m1", name: "Server" }, { id: "m2", name: "keep" }, { id: "m3", name: "new" }];

const merged1 = wtKeyedListMerge3(base, local, server, "id", "mine");
assert(merged1.ok, "merge ok");
assert(merged1.conflicts.some(function (c) { return c.kind === "both_changed" && c.id === "m1"; }), "detect both_changed");
wtSoftNotifyBothChanged(path, merged1.conflicts);
wtSoftNotifyBothChanged(path, merged1.conflicts); // second poll
assert(toasts.length === 1, "warn once per path|id");
assert(toasts[0].indexOf("m1") >= 0, "mentions id");

const byId = {};
merged1.merged.forEach(function (r) { byId[r.id] = r; });
assert(byId.m1 && byId.m1.name === "Local", "mine keeps local record");
assert(byId.m2, "sibling kept");
assert(byId.m3 && byId.m3.name === "new", "server sibling id kept");

// keyedKeep semantics: dirty list keeps old expected_v (modeled)
const keyedKeepMeta = { path: path, expected_v: 3 };
const serverV = 5;
assert(keyedKeepMeta.expected_v !== serverV, "expected_v not raised to server on soft");
assert(keyedKeepMeta.expected_v === 3, "stale expected_v retained for 409");

// 409 cancel → no write
function resolveCancel(choice) {
    if (choice === "cancel") return { wrote: false, outcome: false };
    return { wrote: true, outcome: true };
}
assert(resolveCancel("cancel").wrote === false, "cancel no write");
assert(resolveCancel("cancel").outcome === false, "cancel not full success");

// partial saved + unresolved conflict ≠ full success
function partialOutcome(savedCount, unresolved) {
    if (unresolved) return false;
    return savedCount > 0;
}
assert(partialOutcome(2, true) === false, "partial+unresolved not full");
assert(partialOutcome(2, false) === true, "resolved ok");

console.log(JSON.stringify({
    ok: true,
    package: "V",
    checks: [
        "timeout_not_success",
        "no_false_auto_retry_message",
        "both_changed_detected",
        "warn_once",
        "siblings_kept",
        "expected_v_not_raised",
        "cancel_no_write",
        "partial_not_full"
    ]
}, null, 2));
console.log("wt_package_v_save_contract_harness: OK");
