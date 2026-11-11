/**
 * Локальные сценарии пула/истории/суммы «Ответственный за пересчёт» (Территория / склад).
 * Без БД и без браузера — зеркало правил из app.html.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function shopMetricNameKey(name) {
  return String(name || "").toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

const WAREHOUSE_RECOUNT_POSITIONS_NAME = "Количество позиций в пересчёте";

function resolvePositionsMetricId(pool, candidates) {
  const explicit = pool && pool.recount_source_metric_id ? String(pool.recount_source_metric_id) : "";
  if (explicit && candidates.some(function (c) { return c.id === explicit && c.valid; })) return explicit;
  const want = shopMetricNameKey(WAREHOUSE_RECOUNT_POSITIONS_NAME);
  const hits = (candidates || []).filter(function (c) {
    return c.valid && shopMetricNameKey(c.name) === want;
  });
  if (hits.length === 1) return hits[0].id;
  return "";
}

function totalState(metricId, peopleFacts) {
  if (!metricId) return { configured: false, sum: null };
  let sum = 0;
  (peopleFacts || []).forEach(function (row) {
    if (!row || row.home === "shop") return;
    sum += Number(row.value || 0);
  });
  return { configured: true, sum: sum };
}

function cellText(st) {
  if (!st.configured) return "—";
  return st.sum ? String(st.sum) : "—";
}

function poolIdsFromAssignments(assignments, homeById) {
  const ids = {};
  function consider(id) {
    if (!id) return;
    const home = homeById[id] || "";
    if (home === "shop") return;
    if (home !== "office" && home !== "warehouse") return;
    ids[id] = true;
  }
  (assignments || []).forEach(function (row) {
    if (!row) return;
    const d = String(row.department_id || "");
    if (d !== "office" && d !== "warehouse") return;
    (row.employee_ids || []).forEach(consider);
    (row.extra_employee_ids || []).forEach(consider);
  });
  return Object.keys(ids);
}

function candidates(pool, monthStore, homeById, workingById) {
  const selectedIds = {};
  Object.keys(monthStore || {}).forEach(function (day) {
    const id = monthStore[day] && monthStore[day].employee_id;
    if (id) selectedIds[id] = true;
  });
  const ids = {};
  pool.forEach(function (id) {
    if (homeById[id] === "shop") return;
    if (workingById[id] || selectedIds[id]) ids[id] = true;
  });
  Object.keys(selectedIds).forEach(function (id) {
    if (homeById[id] === "shop") return;
    ids[id] = true;
  });
  return Object.keys(ids);
}

function applySelect(store, day, employeeId, actor) {
  const dayKey = String(day);
  const id = String(employeeId || "");
  const prev = store[dayKey] && store[dayKey].employee_id ? String(store[dayKey].employee_id) : "";
  if (!id) {
    if (store[dayKey] && typeof store[dayKey] === "object") {
      const rec = store[dayKey];
      if (prev) {
        if (!Array.isArray(rec.history)) rec.history = [];
        rec.history.push({ from_employee_id: prev, to_employee_id: "", changed_by_user_id: actor });
      }
      rec.employee_id = "";
    }
    return store[dayKey] || null;
  }
  const rec = store[dayKey] && typeof store[dayKey] === "object" ? store[dayKey] : {};
  rec.employee_id = id;
  if (prev && prev !== id) {
    if (!Array.isArray(rec.history)) rec.history = [];
    rec.history.push({ from_employee_id: prev, to_employee_id: id, changed_by_user_id: actor });
  }
  store[dayKey] = rec;
  return rec;
}

const homeById = {
  e_wh: "warehouse",
  e_off: "office",
  e_shop: "shop",
  e_drv: "drivers"
};
const workingById = { e_wh: true, e_off: true, e_shop: true, e_drv: true };

assert(resolvePositionsMetricId({ recount_source_metric_id: "" }, []) === "", "no source → empty");
assert(cellText(totalState("", [])) === "—", "unconfigured cell is dash not 0");

const cand = [
  { id: "m_other", name: "Пересчёт возвратов", valid: true },
  { id: "m_pos", name: "Количество позиций в пересчёте", valid: true }
];
assert(resolvePositionsMetricId({}, cand) === "m_pos", "exact unique name");
assert(resolvePositionsMetricId({ recount_source_metric_id: "m_pos" }, cand) === "m_pos", "explicit id");
assert(resolvePositionsMetricId({ recount_source_metric_id: "m_other" }, cand) === "m_other", "explicit overrides name");
assert(resolvePositionsMetricId({}, [
  { id: "a", name: "Количество позиций в пересчёте", valid: true },
  { id: "b", name: "Количество позиций в пересчёте", valid: true }
]) === "", "ambiguous exact name → empty");

const st = totalState("m_pos", [
  { home: "warehouse", value: 10 },
  { home: "warehouse", value: 7 },
  { home: "shop", value: 100 }
]);
assert(st.configured && st.sum === 17, "sum ignores shop");
assert(cellText(totalState("m_pos", [])) === "—", "configured zero day is dash");

const pool = poolIdsFromAssignments([
  { department_id: "warehouse", employee_ids: ["e_wh", "e_shop"] },
  { department_id: "office", employee_ids: ["e_off"] },
  { department_id: "shop", employee_ids: ["e_shop"] },
  { department_id: "warehouse", extra_employee_ids: ["e_drv"] }
], homeById);
assert(pool.indexOf("e_wh") !== -1, "warehouse in pool");
assert(pool.indexOf("e_off") !== -1, "office in pool");
assert(pool.indexOf("e_shop") === -1, "shop excluded from pool");
assert(pool.indexOf("e_drv") === -1, "drivers excluded from pool");

const store = {};
applySelect(store, 5, "e_wh", "u1");
assert(store["5"].employee_id === "e_wh", "select employee");
applySelect(store, 12, "e_off", "u1");
assert(store["12"].employee_id === "e_off", "future date select");
applySelect(store, 3, "e_wh", "u1");
applySelect(store, 3, "", "u1");
assert(store["3"].employee_id === "", "empty value");
assert(store["3"].history.length >= 1, "history on clear");
assert(store["5"].employee_id === "e_wh", "kept selection for retention");

const narrowedPool = ["e_off"];
const candPeople = candidates(narrowedPool, store, homeById, workingById);
assert(candPeople.indexOf("e_off") !== -1, "current pool member");
assert(candPeople.indexOf("e_wh") !== -1, "history retention after pool shrink");
assert(candPeople.indexOf("e_shop") === -1, "shop never in candidates");

console.log("wt_territory_recount_responsible_harness: OK");
