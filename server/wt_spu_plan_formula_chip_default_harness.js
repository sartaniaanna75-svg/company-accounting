/**
 * Дефолт чипа формулы плана = ind_plan_0; факта = ind_fact_0.
 * Не мигрирует сохранённые токены. Зеркало UI-правил app.html.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function normalizeTok(tok) {
  const kind = String((tok && tok.value_kind) || "fact") === "plan" ? "plan" : "fact";
  let off = Number(tok && tok.week_offset);
  if (!Number.isFinite(off)) off = 0;
  return { t: "ind", id: String((tok && tok.id) || ""), value_kind: kind, week_offset: Math.trunc(off) };
}

function fromKindKey(key, id) {
  if (key === "ind_fact_m1") return normalizeTok({ id: id, value_kind: "fact", week_offset: -1 });
  if (key === "ind_plan_0") return normalizeTok({ id: id, value_kind: "plan", week_offset: 0 });
  if (key === "ind_plan_m1") return normalizeTok({ id: id, value_kind: "plan", week_offset: -1 });
  return normalizeTok({ id: id, value_kind: "fact", week_offset: 0 });
}

/** Как кнопка «+ Добавить элемент» в spuIndFormulaChipsHtml(which). */
function defaultAddKind(which) {
  return which === "plan" ? "ind_plan_0" : "ind_fact_0";
}

function addElement(list, which) {
  const key = defaultAddKind(which);
  list.push(fromKindKey(key, ""));
  return list[list.length - 1];
}

// 1) Новый операнд формулы плана → plan / week 0
{
  const planFx = [];
  const tok = addElement(planFx, "plan");
  assert(tok.value_kind === "plan", "plan default kind");
  assert(tok.week_offset === 0, "plan default week");
  assert(defaultAddKind("plan") === "ind_plan_0", "plan add key");
}

// 2) Новый операнд формулы факта → fact
{
  const factFx = [];
  const tok = addElement(factFx, "fact");
  assert(tok.value_kind === "fact", "fact default kind");
  assert(tok.week_offset === 0, "fact default week");
  assert(defaultAddKind("fact") === "ind_fact_0", "fact add key");
}

// 3) Ручной fact внутри формулы плана
{
  const planFx = [];
  addElement(planFx, "plan");
  planFx[0] = fromKindKey("ind_fact_0", "mind_a");
  assert(planFx[0].value_kind === "fact", "manual fact in plan fx");
  assert(planFx[0].id === "mind_a", "id kept");
}

// 4) Разные операнды — разные источники
{
  const planFx = [
    fromKindKey("ind_plan_0", "mind_sum"),
    { t: "op", v: "/" },
    fromKindKey("ind_fact_m1", "mind_qty")
  ];
  assert(planFx[0].value_kind === "plan" && planFx[0].id === "mind_sum", "op0 plan sum");
  assert(planFx[2].value_kind === "fact" && planFx[2].week_offset === -1, "op2 fact prev");
}

// 5–7) Сохранение / reopen / старые fact без миграции
{
  const saved = [
    { t: "ind", id: "mind_sum", value_kind: "fact", week_offset: 0 },
    { t: "op", v: "/" },
    { t: "ind", id: "mind_qty", value_kind: "fact", week_offset: 0 }
  ];
  const draft = JSON.parse(JSON.stringify(saved));
  draft[0] = fromKindKey("ind_plan_0", "mind_sum");
  assert(saved[0].value_kind === "fact", "saved not migrated without save");
  const reopen = JSON.parse(JSON.stringify(saved));
  assert(reopen[0].value_kind === "fact" && reopen[2].value_kind === "fact", "reopen keeps fact");
}

// 8) Отсутствующий план ≠ факт
{
  function readOperand(tok, store) {
    const n = normalizeTok(tok);
    if (n.value_kind === "plan") {
      return Object.prototype.hasOwnProperty.call(store, "plan|" + n.id) ? store["plan|" + n.id] : null;
    }
    return Object.prototype.hasOwnProperty.call(store, "fact|" + n.id) ? store["fact|" + n.id] : null;
  }
  const store = { "fact|a": 99 };
  assert(readOperand(fromKindKey("ind_plan_0", "a"), store) == null, "missing plan not fact");
  assert(readOperand(fromKindKey("ind_fact_0", "a"), store) === 99, "fact still readable");
}

console.log("wt_spu_plan_formula_chip_default_harness: OK");
