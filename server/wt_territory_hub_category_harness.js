/**
 * Членство в хабе «Метрик территории»: категория, не org_unit_ids.
 * Зеркало metricBelongsToTerritoryHub после фикса (без БД / браузера).
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function shopCatalogItemShownInUi(item) {
  if (!item || item.show_column === false) return false;
  if (item.show_in_work_metrics === false) return false;
  return item.active !== false;
}

function metricDepartmentId(item) {
  return String((item && item.department_id) || "").trim();
}

function metricTerritoryCategory(item) {
  return String((item && item.territory_category) || "").trim();
}

function metricBelongsToTerritoryHub(item, spec) {
  // Категория → раздел «Метрик территории». org_unit_ids — только СПУ, не членство в хабе.
  if (!item || !spec || !shopCatalogItemShownInUi(item)) return false;
  if (metricDepartmentId(item) === "shop") return false;
  const cat = metricTerritoryCategory(item);
  if (spec.allTerritory) return cat === "common";
  return cat === spec.id;
}

const HUBS = {
  sales_opt: { id: "sales_opt", departmentId: "drivers", workTabIds: ["sales"] },
  sales_retail: { id: "sales_retail", departmentId: "warehouse", workTabIds: ["cashiers"] },
  office: { id: "office", departmentId: "office", workTabIds: ["main"] },
  warehouse: { id: "warehouse", departmentId: "warehouse", workTabIds: ["loaders", "pickers", "managers"] },
  drivers: { id: "drivers", departmentId: "drivers", workTabIds: ["drivers"] },
  common: { id: "common", departmentId: "", workTabIds: null, allTerritory: true }
};

function metricAssignedToDeptTab(item, deptCode, tabId) {
  const rows = (item && item.assignments) || [];
  return rows.some(function (row) {
    return row && row.department_id === deptCode && (!tabId || !row.work_tab_id || row.work_tab_id === tabId);
  });
}

/** Упрощённое зеркало ветки assignments в deptMetricFields. */
function inDriversSalesWorkTable(item) {
  if (!shopCatalogItemShownInUi(item)) return false;
  if (metricDepartmentId(item) === "shop") return false;
  if (Array.isArray(item.assignments) && item.assignments.length) {
    return metricAssignedToDeptTab(item, "drivers", "sales");
  }
  return metricTerritoryCategory(item) === "sales_opt";
}

const assembled = {
  id: "m_assembled_opt",
  name: "Собранные заявки на складе ОПТ",
  department_id: "drivers",
  territory_category: "sales_opt",
  work_tab_ids: ["sales"],
  org_unit_ids: ["warehouse"],
  active: true,
  show_column: true,
  assignments: [{
    assignment_id: "a1",
    department_id: "drivers",
    work_tab_id: "sales",
    assign_mode: "specific",
    employee_ids: ["e1", "e2", "e3", "e4"],
    extra_employee_ids: ["e_wh"]
  }]
};

const loaded = {
  id: "m_loaded_opt",
  name: "Загруженные заявки от торговых ОПТ",
  department_id: "drivers",
  territory_category: "sales_opt",
  work_tab_ids: ["sales"],
  org_unit_ids: [],
  active: true,
  show_column: true,
  assignments: [{
    assignment_id: "a2",
    department_id: "drivers",
    work_tab_id: "sales",
    assign_mode: "specific",
    employee_ids: ["e1"]
  }]
};

const warehouseOnly = {
  id: "m_wh",
  name: "Складская метрика",
  department_id: "warehouse",
  territory_category: "warehouse",
  org_unit_ids: ["sales_opt"],
  active: true,
  show_column: true,
  assignments: [{ department_id: "warehouse", work_tab_id: "loaders", employee_ids: ["e_wh"] }]
};

const commonMetric = {
  id: "m_common",
  name: "Общая",
  department_id: "office",
  territory_category: "common",
  org_unit_ids: ["warehouse"],
  active: true,
  show_column: true
};

const shopMetric = {
  id: "m_shop",
  name: "Магазин",
  department_id: "shop",
  territory_category: "",
  active: true,
  show_column: true
};

assert(metricBelongsToTerritoryHub(assembled, HUBS.sales_opt), "assembled in sales_opt despite warehouse org_unit");
assert(metricBelongsToTerritoryHub(loaded, HUBS.sales_opt), "loaded in sales_opt");
assert(inDriversSalesWorkTable(assembled), "assembled still in Торговые");
assert(inDriversSalesWorkTable(loaded), "loaded still in Торговые");

["office", "warehouse", "drivers", "sales_retail", "common"].forEach(function (hid) {
  assert(!metricBelongsToTerritoryHub(assembled, HUBS[hid]), "assembled not in " + hid);
  assert(!metricBelongsToTerritoryHub(loaded, HUBS[hid]), "loaded not in " + hid);
});

assert(!metricBelongsToTerritoryHub(warehouseOnly, HUBS.sales_opt), "warehouse cat not in sales_opt even with sales_opt org_unit");
assert(metricBelongsToTerritoryHub(warehouseOnly, HUBS.warehouse), "warehouse cat in warehouse hub");
assert(metricBelongsToTerritoryHub(commonMetric, HUBS.common), "common in allTerritory hub");
assert(!metricBelongsToTerritoryHub(commonMetric, HUBS.sales_opt), "common not in sales_opt");
assert(!metricBelongsToTerritoryHub(shopMetric, HUBS.sales_opt), "shop excluded");
assert(!metricBelongsToTerritoryHub(shopMetric, HUBS.warehouse), "shop excluded from warehouse");

console.log("wt_territory_hub_category_harness: OK");
