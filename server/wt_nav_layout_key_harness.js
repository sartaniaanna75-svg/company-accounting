/**
 * Контракт единого ключа меню раздела (без DOM / без live БД).
 * admin ≠ summary; отделы и FR/SPU стабильны; один раздел → один ключ для admin и сотрудника.
 */
function resolveNavLayoutStorageKey(ctx) {
  ctx = ctx || {};
  if (ctx.territoryMetrics) return "territoryMetrics";
  if (ctx.org) return "org";
  if (ctx.spu) return "spu";
  if (ctx.financeRec) return "financeRec";
  if (ctx.adminEntry) return "admin";
  if (ctx.summary) return "summary";
  return String(ctx.workspaceCode || "").trim();
}

function navLayoutsPath(key) {
  return "navLayouts." + key;
}

/** Порядок доступных пунктов: layout order ∩ allowed (права не расширяются). */
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
  Object.keys(allow).forEach(function (id) { out.push(id); });
  return out;
}

function assert(c, m) { if (!c) throw new Error(m); }

// admin entry vs Общее — разные ключи
assert(resolveNavLayoutStorageKey({ adminEntry: true, summary: true }) === "admin", "admin entry -> admin");
assert(resolveNavLayoutStorageKey({ summary: true }) === "summary", "summary entry -> summary");
assert(resolveNavLayoutStorageKey({ adminEntry: true }) !== resolveNavLayoutStorageKey({ summary: true }), "admin !== summary");

// Один раздел: администратор и сотрудник → один ключ
assert(resolveNavLayoutStorageKey({ summary: true }) === "summary", "admin in summary -> summary");
assert(resolveNavLayoutStorageKey({ summary: true }) === resolveNavLayoutStorageKey({ summary: true }), "staff same summary key");

["shop", "office", "warehouse", "drivers"].forEach(function (code) {
  const adminKey = resolveNavLayoutStorageKey({ workspaceCode: code });
  const staffKey = resolveNavLayoutStorageKey({ workspaceCode: code });
  assert(adminKey === code, "dept admin key " + code);
  assert(staffKey === code, "dept staff key " + code);
  assert(adminKey === staffKey, "admin+staff share " + code);
  assert(navLayoutsPath(code) === "navLayouts." + code, "OPS path " + code);
});

assert(resolveNavLayoutStorageKey({ financeRec: true }) === "financeRec", "financeRec");
assert(resolveNavLayoutStorageKey({ spu: true }) === "spu", "spu");
assert(resolveNavLayoutStorageKey({ org: true }) === "org", "org");
assert(resolveNavLayoutStorageKey({ territoryMetrics: true }) === "territoryMetrics", "territoryMetrics");

// Изоляция: shop не равен office
assert(resolveNavLayoutStorageKey({ workspaceCode: "shop" }) !== resolveNavLayoutStorageKey({ workspaceCode: "office" }), "shop !== office");
assert(navLayoutsPath("shop") !== navLayoutsPath("summary"), "shop path !== summary");
assert(navLayoutsPath("admin") !== navLayoutsPath("summary"), "admin path !== summary");

// Права: сотрудник не получает лишних пунктов
const layoutOrder = ["a", "b", "c", "d"];
const staffAllowed = ["c", "a"];
const shown = applyOrderToAllowed(layoutOrder, staffAllowed);
assert(JSON.stringify(shown) === JSON.stringify(["a", "c"]), "subset keeps admin order");
assert(shown.indexOf("b") === -1 && shown.indexOf("d") === -1, "no extra rights");

console.log("OK wt_nav_layout_key_harness");
