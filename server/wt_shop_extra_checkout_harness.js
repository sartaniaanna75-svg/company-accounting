/**
 * Пересчёты (пул ответственных) + дополнительная касса:
 * итоги магазина, ср. чек, персональная атрибуция, без двойного учёта.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function shopMetricPositionKey(position) {
  return String(position || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function employeeMatchesMetricCategory(employee, category) {
  const pos = shopMetricPositionKey(employee && employee.position);
  if (!pos) return false;
  return (category.positions || []).some(function (p) {
    return pos === p || pos.indexOf(p) !== -1;
  });
}

function employeeWorkGroupIds(employee) {
  const ids = [];
  function add(id) {
    const s = String(id || "").trim();
    if (!s || ids.indexOf(s) !== -1) return;
    ids.push(s);
  }
  if (Array.isArray(employee.work_group_ids)) employee.work_group_ids.forEach(add);
  if (Array.isArray(employee.work_tab_ids)) employee.work_tab_ids.forEach(add);
  add(employee.work_group_id);
  add(employee.primary_work_tab_id);
  add(employee.work_tab_id);
  return ids;
}

function shopEmployeeHomeCategoryId(employee) {
  const ids = employeeWorkGroupIds(employee);
  const order = ["pickers", "cashiers", "managers"];
  for (let i = 0; i < order.length; i++) {
    if (ids.indexOf(order[i]) !== -1) return order[i];
  }
  return ids[0] || "";
}

function shopEmployeeIsShopManagersGroup(employee) {
  const tab = employee.work_tab_id || "";
  if (tab) return tab === "managers";
  return shopEmployeeHomeCategoryId(employee) === "managers";
}

const MANAGERS_CAT = { positions: ["директор", "администратор", "кладовщик", "руководитель"] };

function shopEmployeeIsRecountResponsibleCandidate(employee) {
  if (!employee || !employee.id) return false;
  if (shopEmployeeIsShopManagersGroup(employee)) return true;
  if (employeeWorkGroupIds(employee).indexOf("managers") !== -1) return true;
  return employeeMatchesMetricCategory(employee, MANAGERS_CAT);
}

function shopRecountResponsiblePoolIds(employees, explicitPool) {
  if (Array.isArray(explicitPool) && explicitPool.length) {
    return explicitPool.filter(Boolean);
  }
  return (employees || []).filter(shopEmployeeIsRecountResponsibleCandidate).map(function (e) {
    return e.id;
  });
}

function shopMetricAvgCheck(sales, receipts) {
  const r = Number(receipts || 0);
  return r ? Number(sales || 0) / r : 0;
}

function shopCashierShopDayTotals(slots, channels, day) {
  let sales = 0;
  let receipts = 0;
  (slots || []).forEach(function (slot) {
    const rec = slot && slot[String(day)];
    sales += Number((rec && rec.sales) || 0);
    receipts += Number((rec && rec.receipts) || 0);
  });
  Object.keys(channels || {}).forEach(function (chId) {
    const rec = channels[chId] && channels[chId][String(day)];
    sales += Number((rec && rec.sales) || 0);
    receipts += Number((rec && rec.receipts) || 0);
  });
  return { sales: sales, receipts: receipts, avgCheck: shopMetricAvgCheck(sales, receipts) };
}

function attributedForEmployee(employeeId, slots, extraDays) {
  let sales = 0;
  let receipts = 0;
  Object.keys(slots || {}).forEach(function (slotId) {
    const dayMap = slots[slotId] || {};
    Object.keys(dayMap).forEach(function (day) {
      const rec = dayMap[day];
      const actual = (rec && rec.actual_cashier_employee_id) || slotId;
      if (actual !== employeeId) return;
      sales += Number((rec && rec.sales) || 0);
      receipts += Number((rec && rec.receipts) || 0);
    });
  });
  Object.keys(extraDays || {}).forEach(function (day) {
    const rec = extraDays[day];
    if (!rec || String(rec.operator_employee_id || "") !== employeeId) return;
    sales += Number(rec.sales || 0);
    receipts += Number(rec.receipts || 0);
  });
  return { sales: sales, receipts: receipts, avgCheck: shopMetricAvgCheck(sales, receipts) };
}

/* 1) Директор multi-group: pickers+managers → в пуле */
{
  const director = {
    id: "dir1",
    position: "Директор",
    work_group_ids: ["pickers", "managers"],
    work_tab_id: "pickers"
  };
  const cashier = { id: "c1", position: "Кассир", work_group_ids: ["cashiers"], work_tab_id: "cashiers" };
  assert(shopEmployeeHomeCategoryId(director) === "pickers", "1: old home = pickers");
  assert(shopEmployeeIsRecountResponsibleCandidate(director), "1: director is candidate");
  assert(!shopEmployeeIsRecountResponsibleCandidate(cashier), "1: cashier not candidate");
  const pool = shopRecountResponsiblePoolIds([director, cashier]);
  assert(pool.indexOf("dir1") !== -1, "1: director in pool");
  assert(pool.indexOf("c1") === -1, "1: cashier not in pool");
}

/* 2) Явный recountResponsiblePool сохраняет приоритет и не автодополняется */
{
  const director = {
    id: "dir1",
    position: "Директор",
    work_group_ids: ["managers"],
    work_tab_id: "managers"
  };
  const pool = shopRecountResponsiblePoolIds([director], ["admin_only"]);
  assert(pool.length === 1 && pool[0] === "admin_only", "2: explicit pool wins");
  assert(pool.indexOf("dir1") === -1, "2: director not auto-added to explicit pool");
}

/* 3) Должность директора без managers-группы */
{
  const director = { id: "dir2", position: "директор магазина", work_group_ids: ["cashiers"] };
  assert(shopEmployeeIsRecountResponsibleCandidate(director), "3: position match");
}

/* 4) Итог дня = слоты + КСО + доп; ср. чек = сумма/сумма */
{
  const slots = [
    { "5": { sales: 1000, receipts: 10 } },
    { "5": { sales: 500, receipts: 5 } }
  ];
  const channels = {
    self_checkout: { "5": { sales: 200, receipts: 4 } },
    extra_checkout: { "5": { sales: 300, receipts: 6, operator_employee_id: "c1" } }
  };
  const day = shopCashierShopDayTotals(slots, channels, 5);
  assert(day.sales === 2000, "4: day sales " + day.sales);
  assert(day.receipts === 25, "4: day receipts " + day.receipts);
  assert(Math.abs(day.avgCheck - 80) < 1e-9, "4: avg = 2000/25");
}

/* 5) Без доп. кассы прежние цифры */
{
  const slots = [{ "5": { sales: 1000, receipts: 10 } }];
  const onlySco = { self_checkout: { "5": { sales: 200, receipts: 4 } } };
  const a = shopCashierShopDayTotals(slots, onlySco, 5);
  const b = shopCashierShopDayTotals(slots, Object.assign({}, onlySco, {
    extra_checkout: { "5": {} }
  }), 5);
  assert(a.sales === b.sales && a.receipts === b.receipts, "5: empty extra does not change");
}

/* 6) Персональный итог: слот + доп. касса */
{
  const slots = {
    c1: { "5": { sales: 1000, receipts: 10 } },
    c2: { "5": { sales: 400, receipts: 4, actual_cashier_employee_id: "c1" } }
  };
  const extra = { "5": { sales: 300, receipts: 6, operator_employee_id: "c1" } };
  const tot = attributedForEmployee("c1", slots, extra);
  assert(tot.sales === 1700, "6: personal sales " + tot.sales);
  assert(tot.receipts === 20, "6: personal receipts " + tot.receipts);
  const orphan = attributedForEmployee("c9", slots, {
    "5": { sales: 50, receipts: 1 }
  });
  assert(orphan.sales === 0 && orphan.receipts === 0, "6: no operator → not attributed");
}

/* 7) Двойной учёт канала не допускается: один day record */
{
  const channels = {
    extra_checkout: { "5": { sales: 100, receipts: 2 } }
  };
  const day = shopCashierShopDayTotals([], channels, 5);
  assert(day.sales === 100 && day.receipts === 2, "7: channel once");
}

console.log("wt_shop_extra_checkout_harness: OK");
