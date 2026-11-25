/**
 * Паритет колонок хаба «Склад» с dwork: deptMetricFields(warehouse, tab).
 * Без БД / браузера — зеркало ветки warehouse в deptMetricFields.
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

function metricShowsOnTab(item, tab) {
  const ids = (item && item.work_tab_ids) || [];
  if (!ids.length) return true;
  return ids.indexOf(tab) >= 0;
}

function metricAssignedToDeptTab(item, deptCode, tabId) {
  const rows = (item && item.assignments) || [];
  return rows.some(function (row) {
    return row && row.department_id === deptCode && (!tabId || !row.work_tab_id || row.work_tab_id === tabId);
  });
}

/** Упрощённое зеркало deptMetricFields для warehouse без people/common. */
function warehouseDeptMetricIds(catalog, tab) {
  const out = [];
  const seen = {};
  catalog.forEach(function (it) {
    if (!shopCatalogItemShownInUi(it)) return;
    if (metricDepartmentId(it) === "shop") return;
    if (Array.isArray(it.assignments) && it.assignments.length) {
      if (!metricAssignedToDeptTab(it, "warehouse", tab)) return;
      if (seen[it.id]) return;
      seen[it.id] = true;
      out.push(it.id);
      return;
    }
    const cat = metricTerritoryCategory(it);
    if (tab === "cashiers") {
      if (cat === "sales_retail" && metricShowsOnTab(it, tab)) {
        if (!seen[it.id]) {
          seen[it.id] = true;
          out.push(it.id);
        }
      }
      return;
    }
    if (cat === "warehouse" && metricShowsOnTab(it, tab)) {
      if (!seen[it.id]) {
        seen[it.id] = true;
        out.push(it.id);
      }
    }
  });
  return out;
}

const catalog = [
  {
    id: "m_loader",
    department_id: "warehouse",
    territory_category: "warehouse",
    work_tab_ids: ["loaders"],
    active: true,
    show_column: true
  },
  {
    id: "m_picker",
    department_id: "warehouse",
    territory_category: "warehouse",
    work_tab_ids: ["pickers"],
    active: true,
    show_column: true
  },
  {
    id: "m_cashier_retail",
    department_id: "warehouse",
    territory_category: "sales_retail",
    work_tab_ids: ["cashiers"],
    active: true,
    show_column: true
  },
  {
    id: "m_wh_blanket",
    department_id: "warehouse",
    territory_category: "warehouse",
    active: true,
    show_column: true
  },
  {
    id: "m_assigned_cashier",
    department_id: "warehouse",
    territory_category: "warehouse",
    active: true,
    show_column: true,
    assignments: [
      { department_id: "warehouse", work_tab_id: "cashiers", employee_ids: ["e1"] }
    ]
  },
  {
    id: "m_assigned_dup",
    department_id: "warehouse",
    territory_category: "sales_retail",
    work_tab_ids: ["cashiers"],
    active: true,
    show_column: true,
    assignments: [
      { department_id: "warehouse", work_tab_id: "cashiers", employee_ids: ["e1"] },
      { department_id: "warehouse", work_tab_id: "cashiers", employee_ids: ["e2"] }
    ]
  },
  {
    id: "m_office",
    department_id: "office",
    territory_category: "office",
    active: true,
    show_column: true
  }
];

const loaders = warehouseDeptMetricIds(catalog, "loaders");
assert(loaders.indexOf("m_loader") >= 0, "loader on loaders");
assert(loaders.indexOf("m_wh_blanket") >= 0, "blanket warehouse on loaders");
assert(loaders.indexOf("m_cashier_retail") < 0, "sales_retail not on loaders");
assert(loaders.indexOf("m_assigned_cashier") < 0, "cashier assignment not on loaders");

const cashiers = warehouseDeptMetricIds(catalog, "cashiers");
assert(cashiers.indexOf("m_cashier_retail") >= 0, "sales_retail on cashiers");
assert(cashiers.indexOf("m_assigned_cashier") >= 0, "assignment on cashiers");
assert(cashiers.indexOf("m_loader") < 0, "loader tab not on cashiers");
assert(cashiers.indexOf("m_wh_blanket") < 0, "warehouse blanket not on cashiers without cashiers tab");
assert(cashiers.filter(function (id) { return id === "m_assigned_dup"; }).length === 1, "dedup one column");

const pickers = warehouseDeptMetricIds(catalog, "pickers");
assert(pickers.indexOf("m_picker") >= 0, "picker on pickers");
assert(pickers.indexOf("m_cashier_retail") < 0, "retail not on pickers");

console.log("wt_territory_warehouse_hub_groups_harness: OK");
