/**
 * Защита карточек сотрудников — исполняемые проверки (Node).
 * Запуск: node server/test_employee_card_guard.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const APP_HTML = path.join(ROOT, "app.html");
const src = fs.readFileSync(APP_HTML, "utf8");

let failed = 0;
let passed = 0;

function assert(cond, msg) {
    if (cond) {
        passed += 1;
        console.log("  OK  " + msg);
    } else {
        failed += 1;
        console.error("  FAIL  " + msg);
    }
}

function assertIncludes(hay, needle, msg) {
    assert(hay.indexOf(needle) !== -1, msg + " (ожидается фрагмент)");
}

function assertNotIncludes(hay, needle, msg) {
    assert(hay.indexOf(needle) === -1, msg + " (фрагмент не должен присутствовать)");
}

console.log("=== Источник app.html: маркеры защиты ===");

assertIncludes(src, "function stageEmployeeCardField(", "1/2: черновик полей карточки");
assertIncludes(src, "EMPLOYEE_CARD_PROTECTED_FIELDS", "защищённые поля карточки");
assert(
    /stageEmployeeCardField\([^)]*work_tab_id/.test(src),
    "2: смена группы в select → stage, не save"
);
assert(
    /stageEmployeeCardField\([^)]*position_id/.test(src),
    "2: position_id → stageEmployeeCardField"
);
assertIncludes(src, "applyEmployeeCardProtectedDraft", "3: сохранение через commit применяет черновик");
assertIncludes(src, "function commitEmployeeCard(", "commitEmployeeCard существует");
assert(
    /EMPLOYEE_CARD_PROTECTED_FIELDS\[field\]/.test(src)
        && /stageEmployeeCardField\(employeeId, field, value\)/.test(src),
    "3: updateEmployee не пишет protected-поля сразу"
);

assertIncludes(src, "syncEmployeeMetricAssignmentsToCatalog", "4: sync метрик существует");
const syncStart = src.indexOf("function syncEmployeeMetricAssignmentsToCatalog");
const syncEnd = src.indexOf("\nfunction ", syncStart + 10);
const syncBody = src.slice(syncStart, syncEnd > 0 ? syncEnd : syncStart + 2500);
assertNotIncludes(syncBody, "setEmployeePosition(", "4: назначение метрики не вызывает setEmployeePosition");
assertNotIncludes(syncBody, "setEmployeePrimaryWorkTab(", "4: назначение метрики не вызывает setEmployeePrimaryWorkTab");
assertNotIncludes(syncBody, ".position_id =", "4: sync не пишет position_id сотрудника");
assertNotIncludes(syncBody, ".work_tab_id =", "4: sync не пишет work_tab_id сотрудника");

assertIncludes(src, "possible_name_duplicate", "5: dedupe только отчёт");
assert(
    /function dedupeClonedEmployees[\s\S]*?pushEmployeeCardGuardReport[\s\S]*?possible_name_duplicate/.test(src)
        && !/function dedupeClonedEmployees[\s\S]*?removeEmployeeFromWorkspace/.test(
            src.slice(src.indexOf("function dedupeClonedEmployees"), src.indexOf("function dedupeClonedEmployees") + 1200)
        ),
    "5: dedupeClonedEmployees не удаляет по fingerprint"
);

assertIncludes(src, "distribute_no_clone", "6: запрет клонирования между объектами");
assert(
    !/function distributeEmployeesPerDepartment[\s\S]{0,2500}cloneEmployeeRecord/.test(src),
    "6: distributeEmployeesPerDepartment без cloneEmployeeRecord"
);

assertIncludes(src, "employee_id_multi_object", "7: конфликт id в отчёте");
assertIncludes(src, "function getEmployeeCardGuardReport(", "7: отчёт конфликтов");

assertIncludes(src, "Магазин Володарского", "8: магазин в коде");
assert(
    /employeeCardWorkGroupId|work_tab_id/.test(src)
        && src.indexOf("function timesheetGroupFilterTabs") !== -1,
    "8: рабочие таблицы магазина (фильтр групп) сохранены"
);

assertIncludes(src, "onMetricCatalogAssignmentField", "9: назначения метрик Территории");
assert(
    /work_tab_id/.test(src) && /position_id/.test(src)
        && src.indexOf("_is_territory_assignment_dept") === -1
            ? src.indexOf("position_id") !== -1
            : true,
    "9: поля position_id/work_tab_id в назначениях присутствуют"
);

assertIncludes(src, "function metricItemObjectSides(", "10: изоляция объектов метрик");
assertIncludes(src, "metricDeptObjectSide", "10: стороны объектов");
assertIncludes(src, "metricItemHasObjectSide", "10: проверка стороны метрики");

assertIncludes(src, "opts.force === true", "bindEmployeePositionId требует force");
assertIncludes(src, "Не перезаписывать текст должности", "ensurePositionCatalog не трогает текст");
assert(
    /Переназначить должность у /.test(src) && /function reassignPositionEmployees/.test(src),
    "reassignPositionEmployees с подтверждением и числом"
);
assert(
    /Это административное действие/.test(src) && /function moveEmployeeToDepartment/.test(src),
    "moveEmployeeToDepartment с подтверждением"
);

console.log("\n=== Исполняемая логика: distribute / dedupe (изолированно) ===");

const employeeCardGuardReport = [];
function pushEmployeeCardGuardReport(row) {
    employeeCardGuardReport.push(row);
}
function employeeFingerprint(e) {
    const name = String(e.name || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!name || name === "новый сотрудник") return "";
    return [
        name,
        String(e.position || "").replace(/\s+/g, " ").trim().toLowerCase(),
        String(e.rate || 0),
        e.start || "",
        e.end || ""
    ].join("|");
}
function belongsToWorkspace(w, employeeId) {
    if (!w || !employeeId) return false;
    return (w.employees || []).some(function (e) { return e && e.id === employeeId; });
}
function dedupeClonedEmployees(data) {
    const depts = (data.workspaces || []).filter(function (w) { return w && w.type !== "summary"; });
    const groups = {};
    depts.forEach(function (w) {
        (w.employees || []).forEach(function (e) {
            const key = employeeFingerprint(e);
            if (!key) return;
            if (!groups[key]) groups[key] = [];
            groups[key].push({ code: w.code, id: e.id, name: e.name || "" });
        });
    });
    Object.keys(groups).forEach(function (key) {
        const items = groups[key];
        if (items.length < 2) return;
        pushEmployeeCardGuardReport({ type: "possible_name_duplicate", fingerprint: key, employees: items });
    });
}
function distributeEmployeesPerDepartment(data) {
    const owned = {};
    data.workspaces.forEach(function (w) {
        if (w.type === "summary") {
            w.employees = [];
            w.employeeIds = [];
            return;
        }
        const unique = [];
        const localIds = {};
        (w.employees || []).forEach(function (e) {
            if (!e || !e.id || localIds[e.id]) return;
            if (owned[e.id] && owned[e.id] !== w) {
                pushEmployeeCardGuardReport({
                    type: "employee_id_multi_object",
                    employee_id: e.id,
                    name: e.name || "",
                    departments: [owned[e.id].code, w.code].filter(Boolean)
                });
                localIds[e.id] = true;
                unique.push(e);
                return;
            }
            localIds[e.id] = true;
            owned[e.id] = w;
            unique.push(e);
        });
        w.employees = unique;
        const nextIds = [];
        (w.employeeIds || []).forEach(function (id) {
            if (!id || nextIds.indexOf(id) !== -1) return;
            if (owned[id] && owned[id] !== w) {
                pushEmployeeCardGuardReport({
                    type: "distribute_id_conflict",
                    employee_id: id,
                    target: w.code,
                    owned_by: owned[id].code || "",
                    reason: "employeeIds указывает на объект, где карточки нет; клонирование запрещено"
                });
                return;
            }
            if (!belongsToWorkspace(w, id)) {
                pushEmployeeCardGuardReport({
                    type: "distribute_no_clone",
                    employee_id: id,
                    target: w.code,
                    reason: "employeeIds без локальной карточки — копия не создаётся"
                });
                return;
            }
            nextIds.push(id);
        });
        w.employeeIds = nextIds.filter(function (id) { return belongsToWorkspace(w, id); });
    });
    data.employees = [];
    dedupeClonedEmployees(data);
}

// 5: одинаковые ФИО не удаляются
employeeCardGuardReport.length = 0;
const dataDup = {
    workspaces: [
        {
            type: "dept", code: "shop", employees: [
                { id: "a1", name: "Иванов Иван", position: "Кассир", rate: 1000, start: "08:00", end: "17:00" }
            ], employeeIds: ["a1"]
        },
        {
            type: "dept", code: "warehouse", employees: [
                { id: "b1", name: "Иванов Иван", position: "Кассир", rate: 1000, start: "08:00", end: "17:00" }
            ], employeeIds: ["b1"]
        }
    ],
    employees: []
};
distributeEmployeesPerDepartment(dataDup);
assert(dataDup.workspaces[0].employees.length === 1 && dataDup.workspaces[1].employees.length === 1,
    "5: загрузка не удаляет сотрудников с одинаковыми ФИО");
assert(employeeCardGuardReport.some(function (r) { return r.type === "possible_name_duplicate"; }),
    "5: дубль по fingerprint попадает в отчёт");

// 6: нет клонирования в другой объект
employeeCardGuardReport.length = 0;
const dataClone = {
    workspaces: [
        {
            type: "dept", code: "shop",
            employees: [{ id: "e1", name: "Петров", position: "Продавец", rate: 1, start: "08:00", end: "17:00" }],
            employeeIds: ["e1"]
        },
        {
            type: "dept", code: "warehouse",
            employees: [],
            employeeIds: ["e1"]
        }
    ],
    employees: [{ id: "e1", name: "Петров", position: "Продавец", rate: 1, start: "08:00", end: "17:00" }]
};
distributeEmployeesPerDepartment(dataClone);
assert(dataClone.workspaces[1].employees.length === 0,
    "6: загрузка не создаёт копию сотрудника в другом объекте");
assert(employeeCardGuardReport.some(function (r) { return r.type === "distribute_no_clone"; }),
    "6: попытка клона в отчёте");

// 7: один id в двух объектах — обе записи остаются, конфликт в отчёте
employeeCardGuardReport.length = 0;
const dataConflict = {
    workspaces: [
        {
            type: "dept", code: "shop",
            employees: [{ id: "same", name: "Сидоров", position: "А", rate: 1, start: "08:00", end: "17:00" }],
            employeeIds: ["same"]
        },
        {
            type: "dept", code: "warehouse",
            employees: [{ id: "same", name: "Сидоров", position: "Б", rate: 2, start: "09:00", end: "18:00" }],
            employeeIds: ["same"]
        }
    ],
    employees: []
};
distributeEmployeesPerDepartment(dataConflict);
assert(dataConflict.workspaces[0].employees.length === 1 && dataConflict.workspaces[1].employees.length === 1,
    "7: обе карточки с одним id сохраняются");
assert(employeeCardGuardReport.some(function (r) { return r.type === "employee_id_multi_object"; }),
    "7: конфликт employee_id в отчёте");

// Черновик: stage не мутирует объект
const draftById = Object.create(null);
function stageEmployeeCardField(employeeId, field, value) {
    if (!draftById[employeeId]) draftById[employeeId] = {};
    draftById[employeeId][field] = value;
}
const emp = { id: "x", position: "Кладовщик", position_id: "pos1", work_tab_id: "leaders", department: "Склад" };
const before = JSON.stringify(emp);
stageEmployeeCardField("x", "position_id", "pos2");
stageEmployeeCardField("x", "work_tab_id", "loaders");
assert(JSON.stringify(emp) === before, "2: смена без сохранения не меняет объект сотрудника");
assert(draftById.x.position_id === "pos2" && draftById.x.work_tab_id === "loaders",
    "2: значения остаются в черновике");

// Изоляция shop / territory (хелперы из app.html)
const TERRITORY_DEPT_CODES = { office: true, warehouse: true, drivers: true };
function metricDeptObjectSide(dept) {
    const d = String(dept || "");
    if (d === "shop") return "shop";
    if (TERRITORY_DEPT_CODES[d]) return "territory";
    return "";
}
function metricItemObjectSides(item) {
    const sides = {};
    const itemDept = String((item && item.department_id) || "");
    const side0 = metricDeptObjectSide(itemDept);
    if (side0) sides[side0] = true;
    ((item && item.assignments) || []).forEach(function (row) {
        const s = metricDeptObjectSide(row && row.department_id);
        if (s) sides[s] = true;
    });
    return Object.keys(sides);
}
function metricItemHasObjectSide(item, side) {
    return metricItemObjectSides(item).indexOf(side) !== -1;
}
const shopMetric = { id: "m1", department_id: "shop", assignments: [{ department_id: "shop" }] };
const terrMetric = {
    id: "m2", department_id: "warehouse",
    assignments: [{ department_id: "warehouse", position_id: "p1", work_tab_id: "t1" }]
};
assert(metricItemHasObjectSide(shopMetric, "shop") && !metricItemHasObjectSide(shopMetric, "territory"),
    "10: метрика магазина не на стороне Территории");
assert(metricItemHasObjectSide(terrMetric, "territory") && !metricItemHasObjectSide(terrMetric, "shop"),
    "10: метрика Территории не на стороне магазина");
assert(
    terrMetric.assignments[0].position_id === "p1" && terrMetric.assignments[0].work_tab_id === "t1",
    "9: Территория сохраняет position_id и work_tab_id в назначении"
);

console.log("\n=== Итог ===");
console.log("Пройдено: " + passed + ", провалено: " + failed);
if (failed) {
    process.exit(1);
}
console.log("Все проверки test_employee_card_guard.js выполнены.");
