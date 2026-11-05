/**
 * Контракт доставки navLayouts сотруднику: только ключи доступных разделов;
 * порядок item_ids сохраняется; недоступные вкладки пропускаются без смены порядка.
 */
function assert(c, m) { if (!c) throw new Error(m); }

function filterNavLayouts(navLayouts, loginCodes) {
  const allowed = {};
  (loginCodes || []).forEach(function (c) { allowed[String(c)] = true; });
  const out = {};
  Object.keys(navLayouts || {}).forEach(function (key) {
    if (allowed[key]) out[key] = navLayouts[key];
  });
  return out;
}

function applyOrderToAllowed(itemIds, allowedIds) {
  const allow = {};
  (allowedIds || []).forEach(function (id) { allow[String(id)] = true; });
  const out = [];
  (itemIds || []).forEach(function (id) {
    const s = String(id);
    if (allow[s]) {
      out.push(s);
      delete allow[s];
    }
  });
  return out;
}

const layouts = {
  shop: { saved: true, groups: [{ item_ids: ["a", "b", "c", "d"] }] },
  warehouse: { saved: true, groups: [{ item_ids: ["w1", "w2"] }] },
  office: { saved: true, groups: [{ item_ids: ["o1"] }] },
  admin: { saved: true, groups: [{ item_ids: ["users"] }] }
};

const shopOnly = filterNavLayouts(layouts, ["shop"]);
assert(Object.keys(shopOnly).join(",") === "shop", "shop staff only shop key");
assert(JSON.stringify(shopOnly.shop.groups[0].item_ids) === JSON.stringify(["a", "b", "c", "d"]), "shop order intact");

const multi = filterNavLayouts(layouts, ["shop", "warehouse"]);
assert(!!multi.shop && !!multi.warehouse, "multi gets shop+warehouse");
assert(!multi.office && !multi.admin, "multi no office/admin");

const shown = applyOrderToAllowed(["a", "b", "c", "d"], ["c", "a"]);
assert(JSON.stringify(shown) === JSON.stringify(["a", "c"]), "subset keeps admin relative order");

console.log("OK wt_nav_layout_delivery_harness");
