/* Финконтроль: ежедневный учёт + недельная сверка (чт→ср). Расширяет существующий financeRec.
 *
 * Архитектура под будущую 1С (API сейчас НЕ подключаем):
 * — ручной ввод и Excel — временные источники (data_source);
 * — расчёты/экраны/СПУ опираются на сущности, а не на способ ввода;
 * — канонические поля (синхронизируются с текущими ID):
 *     object_id  ↔ finance_area_id
 *     account_id ↔ finance_source_id
 *     op_type    ↔ movement_kind
 *     revenue_direction — ОПТ/Розница/Склад/none (у счёта и снимок на операции)
 *     data_source — manual | excel | paste | 1c | ...
 *     external_id — внешний ключ 1С (пусто до интеграции).
 */
let frUi = {
    areaId: "",
    areaChosen: false,
    weekIso: "",
    inner: "work",
    workTab: "days",
    opKind: "all",
    sourceId: "",
    catId: "",
    date: "",
    q: "",
    importSourceId: "",
    importExpenseDate: "",
    mainCashCalcOpen: {},
    mainCashIncomeOpen: {},
    dividendDetailOpen: {},
    transitDetailOpen: {}
};

const FR_ACCOUNT_TYPES = [
    { id: "cash", label: "Касса" },
    { id: "bank", label: "Расчётный счёт" },
    { id: "card", label: "Карта" },
    { id: "in_transit", label: "Деньги в пути" },
    { id: "other", label: "Вклад/прочее" }
];

const FR_REVENUE_DIRS = [
    { id: "opt", label: "ОПТ" },
    { id: "retail", label: "Розница" },
    { id: "warehouse", label: "Склад" },
    { id: "none", label: "Не относится" }
];

function isFinanceRecMode() {
    return isSummary() && currentEntry === "financeRec";
}

function isFrNavSection(id) {
    return id === "financeRec"
        || id === "financeRec-settings"
        || id === "financeRec-shop"
        || id === "financeRec-territory";
}

function frNavSectionForArea(areaId) {
    return areaId === "farea_territory" ? "financeRec-territory" : "financeRec-shop";
}

function frAreaIdFromNavSection(sectionId) {
    if (sectionId === "financeRec-territory") return "farea_territory";
    if (sectionId === "financeRec-shop") return "farea_shop";
    return "";
}

function frApplyNavSection(sectionId) {
    ensureFinanceRec();
    const fromNav = frAreaIdFromNavSection(sectionId);
    if (fromNav) {
        const allow = frAllowedAreaIds();
        if (allow.indexOf(fromNav) !== -1) {
            frUi.areaId = fromNav;
            frUi.areaChosen = true;
        }
    }
    if (sectionId === "financeRec-settings") {
        frUi.workTab = "accounts";
    } else if (fromNav === "farea_territory") {
        if (!frUi.workTab || frUi.workTab === "days" || frUi.workTab === "income" || frUi.workTab === "non_income" || frUi.workTab === "settings") {
            frUi.workTab = "reconcile";
        }
    } else if (fromNav === "farea_shop") {
        if (!frUi.workTab || frUi.workTab === "days" || frUi.workTab === "income" || frUi.workTab === "non_income" || frUi.workTab === "settings") {
            frUi.workTab = "reconcile";
        }
    } else if (!frUi.workTab || frUi.workTab === "income" || frUi.workTab === "non_income" || frUi.workTab === "settings") {
        frUi.workTab = "days";
    }
    if (!frUi.areaId || frAllowedAreaIds().indexOf(frUi.areaId) === -1) {
        const ids = frAllowedAreaIds();
        frUi.areaId = ids[0] || "";
        frUi.areaChosen = !!frUi.areaId;
    }
}

/** Блоки недельной сверки Территории. direction — канон для будущей 1С. */
const FR_RECONCILE_BLOCKS = [
    { id: "opt", direction: "wholesale", label: "ОПТ", totalLabel: "ИТОГО ОПТ", opKind: "income" },
    { id: "retail", direction: "retail", label: "РОЗНИЦА", totalLabel: "ИТОГО РОЗНИЦА", opKind: "income" },
    { id: "warehouse", direction: "warehouse", label: "СКЛАД", totalLabel: "ИТОГО СКЛАД", opKind: "income" }
];

/** Блоки сверки Магазина Володарского (доход по всем денежным счетам; внедоход — отдельный блок). */
const FR_SHOP_RECONCILE_BLOCKS = [
    { id: "shop_income", label: "ДОХОД", totalLabel: "ИТОГО ДОХОД", opKind: "income" }
];

/** Legacy-имена строк «ВНЕ ДОХОДА» (раньше создавались как счета). Не создаём заново; историю не трогаем. */
const FR_SHOP_NON_INCOME_SEED = [
    "девочки", "аниса", "РИТА", "лара", "оля", "Вика", "раз"
];

const FR_DIR_TO_1C = { opt: "wholesale", retail: "retail", warehouse: "warehouse" };

/** Источники данных операции. «1c» зарезервирован — заполнение из API позже. */
const FR_DATA_SOURCES = {
    manual: "manual",
    excel: "excel",
    paste: "paste",
    onec: "1c"
};

function frIsTerritoryArea(areaOrId) {
    const id = typeof areaOrId === "string" ? areaOrId : (areaOrId && (areaOrId.object_id || areaOrId.finance_area_id));
    return id === "farea_territory";
}

function frRevenueDirLabel(dirId) {
    const hit = FR_REVENUE_DIRS.find(function (d) { return d.id === dirId; });
    return hit ? hit.label : "Не относится";
}

/** Канонический object_id объекта Финконтроля. */
function frObjectId(areaOrId) {
    if (!areaOrId) return "";
    if (typeof areaOrId === "string") return areaOrId;
    return areaOrId.object_id || areaOrId.finance_area_id || "";
}

/** Канонический account_id денежного счёта. */
function frAccountId(srcOrId) {
    if (!srcOrId) return "";
    if (typeof srcOrId === "string") return srcOrId;
    return srcOrId.account_id || srcOrId.finance_source_id || "";
}

function frOpType(o) {
    if (!o) return "expense";
    if (o.op_type) return o.op_type;
    return frOpKind(o);
}

function frOpDataSource(o) {
    return (o && o.data_source) || FR_DATA_SOURCES.manual;
}

function frLookupRevenueDirection(sourceId, areaId) {
    const sources = (appData && appData.financeRec && appData.financeRec.sources) || [];
    const src = sources.find(function (s) {
        return s && (s.finance_source_id === sourceId || s.account_id === sourceId);
    });
    const oid = areaId || (src && (src.object_id || src.finance_area_id)) || "";
    if (!src || !frIsTerritoryArea(oid)) return "none";
    return src.revenue_direction || "none";
}

/** Стабильный account_id операции (откуда). */
function frOpAccountId(o) {
    if (!o) return "";
    return o.account_id || o.finance_source_id || o.source_id || "";
}

/** Стабильный account_id назначения (для перемещений). */
function frOpAccountToId(o) {
    if (!o) return "";
    return o.account_to_id || o.finance_source_to_id || "";
}

/**
 * Направление выручки для операции.
 * Только income на счёте с направлением ОПТ/Розница/Склад.
 * Перемещения, внедоход и расходы — никогда не выручка.
 */
function frOpRevenueDirection(o, areaId) {
    if (!o || frOpKind(o) !== "income") return "none";
    const oid = areaId || o.object_id || o.finance_area_id || "";
    if (!frIsTerritoryArea(oid)) return "none";
    /* Актуальная настройка счёта — чтобы смена направления сразу отражалась в выручке */
    return frLookupRevenueDirection(frOpAccountId(o), oid);
}

/** Сумма выручки направления за неделю (только поступления покупателей / income). */
function frDirectionWeekIncome(areaId, periodKey, dirId) {
    if (!dirId || dirId === "none") return 0;
    let sum = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        if (frOpKind(o) !== "income") return;
        if (frOpRevenueDirection(o, areaId) !== dirId) return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

/** Сумма выручки направления за день. */
function frDirectionDayIncome(areaId, periodKey, dirId, date) {
    if (!dirId || dirId === "none") return 0;
    const want = frNormDate(date);
    let sum = 0;
    frOpsForDate(areaId, periodKey, want, "income").forEach(function (o) {
        if (frOpRevenueDirection(o, areaId) !== dirId) return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

/**
 * Нормализует операцию к интеграционной схеме, не ломая legacy-поля.
 * Расчёты продолжают читать finance_area_id / finance_source_id / movement_kind.
 */
function frNormalizeOperationRecord(o) {
    if (!o || typeof o !== "object") return o;
    if (!o.finance_operation_id && o.operation_id) o.finance_operation_id = o.operation_id;
    if (!o.finance_area_id && o.object_id) o.finance_area_id = o.object_id;
    if (!o.finance_source_id && o.account_id) o.finance_source_id = o.account_id;
    if (!o.finance_source_to_id && o.account_to_id) o.finance_source_to_id = o.account_to_id;
    if (!o.movement_kind && o.op_type) o.movement_kind = o.op_type;

    o.object_id = o.object_id || o.finance_area_id || "";
    o.account_id = o.account_id || o.finance_source_id || "";
    if (o.finance_source_to_id || o.account_to_id) {
        o.account_to_id = o.account_to_id || o.finance_source_to_id || "";
    }
    o.op_type = o.op_type || o.movement_kind || (o.type === "in" ? "income" : (o.type === "transfer" ? "transfer" : "expense"));
    if (!o.data_source) {
        o.data_source = o.import_batch_id ? FR_DATA_SOURCES.excel : FR_DATA_SOURCES.manual;
    }
    if (o.external_id == null) o.external_id = "";
    if (o.external_system == null) o.external_system = "";
    if (o.revenue_direction == null || o.revenue_direction === "") {
        o.revenue_direction = frLookupRevenueDirection(o.account_id || o.finance_source_id, o.object_id || o.finance_area_id);
    }
    if (o.object_id) o.finance_area_id = o.object_id;
    if (o.account_id) o.finance_source_id = o.account_id;
    if (o.account_to_id) o.finance_source_to_id = o.account_to_id;
    if (o.op_type) o.movement_kind = o.op_type;
    return o;
}

/** Проставляет интеграционные поля при создании/сохранении операции. */
function frStampOperation(o, opts) {
    opts = opts || {};
    if (!o || typeof o !== "object") return o;
    if (opts.object_id) o.finance_area_id = opts.object_id;
    if (opts.account_id) o.finance_source_id = opts.account_id;
    if (opts.account_to_id != null) o.finance_source_to_id = opts.account_to_id;
    if (opts.op_type) o.movement_kind = opts.op_type;
    if (opts.data_source) o.data_source = opts.data_source;
    if (opts.external_id != null) o.external_id = opts.external_id;
    if (opts.external_system != null) o.external_system = opts.external_system;
    if (opts.revenue_direction != null) o.revenue_direction = opts.revenue_direction;
    frNormalizeOperationRecord(o);
    const kind = frOpKind(o);
    const oid = o.object_id || o.finance_area_id || "";
    /* Выручка — только income; направление берём со счёта. Перевод/внедоход/расход — не выручка. */
    if (kind === "income" && frIsTerritoryArea(oid)) {
        const dir = (opts.revenue_direction && opts.revenue_direction !== "")
            ? opts.revenue_direction
            : frLookupRevenueDirection(frOpAccountId(o), oid);
        o.revenue_direction = FR_REVENUE_DIRS.some(function (d) { return d.id === dir; }) ? dir : "none";
        o.direction = FR_DIR_TO_1C[o.revenue_direction] || "";
    } else if (kind === "non_income" || kind === "transfer" || kind === "expense" || kind === "dividend") {
        o.revenue_direction = "none";
        o.direction = "";
    }
    return o;
}

function frNormalizeBalanceRow(row) {
    if (!row || typeof row !== "object") return row;
    if (!row.finance_area_id && row.object_id) row.finance_area_id = row.object_id;
    if (!row.finance_source_id && row.account_id) row.finance_source_id = row.account_id;
    row.object_id = row.object_id || row.finance_area_id || "";
    row.account_id = row.account_id || row.finance_source_id || "";
    if (row.object_id) row.finance_area_id = row.object_id;
    if (row.account_id) row.finance_source_id = row.account_id;
    if (row.data_source == null) row.data_source = FR_DATA_SOURCES.manual;
    if (row.external_id == null) row.external_id = "";
    /* actual_balance ↔ actual_closing (совместимость) */
    if (row.actual_balance != null && row.actual_balance !== ""
        && (row.actual_closing == null || row.actual_closing === "")) {
        row.actual_closing = row.actual_balance;
    }
    if (row.actual_closing != null && row.actual_closing !== ""
        && (row.actual_balance == null || row.actual_balance === "")) {
        row.actual_balance = row.actual_closing;
    }
    return row;
}

function frPermRank(value) {
    if (value === "setup") return 5;
    if (value === "close") return 4;
    if (value === "check") return 3;
    if (value === "fill" || value === "edit") return 2;
    if (value === "view") return 1;
    return 0;
}

function frUserLevel() {
    if (typeof isAppAdmin === "function" && isAppAdmin()) return "setup";
    const u = typeof currentAuthUser === "function" ? currentAuthUser() : null;
    const setup = (u && u.permissions && u.permissions.financeRecSetup) || "none";
    if (frPermRank(setup) > 0 || setup === "edit" || setup === "view") return "setup";
    const v = (u && u.permissions && u.permissions.financeRec) || "none";
    return v;
}

function frCan(min) {
    try {
        if (typeof isAppAdmin === "function" && isAppAdmin()) return true;
        return frPermRank(frUserLevel()) >= frPermRank(min || "view");
    } catch (err) {
        console.error(err);
        return false;
    }
}

/** Управление структурой счетов/касс — только Администратор. */
function frCanManageAccounts() {
    try {
        return typeof isAppAdmin === "function" && isAppAdmin();
    } catch (err) {
        return false;
    }
}

/**
 * Есть ли использование account_id в операциях, остатках, перемещениях.
 * Проверяется только в рамках object_id счёта.
 */
function frAccountUsage(areaId, accountId) {
    const aid = accountId;
    const oid = areaId;
    const hits = { operations: 0, openings: 0, day_closings: 0, total: 0 };
    if (!aid || !oid) return hits;
    ((appData.financeRec && appData.financeRec.operations) || []).forEach(function (o) {
        if (!o) return;
        /* Учитываем и soft-deleted — история уже ссылается на account_id. */
        if ((o.object_id || o.finance_area_id) !== oid) return;
        const from = o.account_id || o.finance_source_id || o.source_id;
        const to = o.account_to_id || o.finance_source_to_id;
        if (from === aid || to === aid) hits.operations++;
    });
    ((appData.financeRec && appData.financeRec.openings) || []).forEach(function (r) {
        if (!r) return;
        if ((r.object_id || r.finance_area_id) !== oid) return;
        if ((r.account_id || r.finance_source_id) !== aid) return;
        /* Автоперенос нуля не считаем использованием — иначе ошибочный счёт нельзя удалить. */
        if (r.is_manual || Math.abs(Number(r.value) || 0) >= 0.005) hits.openings++;
    });
    ((appData.financeRec && appData.financeRec.day_closings) || []).forEach(function (r) {
        if (!r) return;
        if ((r.object_id || r.finance_area_id) !== oid) return;
        if ((r.account_id || r.finance_source_id) !== aid) return;
        if (r.value != null && r.value !== "") hits.day_closings++;
    });
    ((appData.financeRec && appData.financeRec.money_transits) || []).forEach(function (t) {
        if (!t) return;
        if ((t.object_id || t.finance_area_id) !== oid) return;
        if (t.from_account_id === aid || t.transit_account_id === aid) hits.operations++;
        (t.closes || []).forEach(function (c) {
            if (c && c.to_account_id === aid) hits.operations++;
        });
    });
    hits.total = hits.operations + hits.openings + hits.day_closings;
    return hits;
}

function frAccountIsUsed(areaId, accountId) {
    return frAccountUsage(areaId, accountId).total > 0;
}

function frMoney(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "0,00 ₽";
    return (typeof money2 === "function" ? money2(x) : x.toFixed(2) + " ₽");
}

function frNum(v) {
    const n = Number(String(v == null ? "" : v).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? Math.round(n * 100) / 100 : 0;
}

function frRound(n) {
    return Math.round(frNum(n) * 100) / 100;
}

function frFmtDate(iso) {
    return typeof mgmtFmtRu === "function" ? mgmtFmtRu(iso) : String(iso || "");
}

function frShiftIso(iso, days) {
    const d = typeof mgmtParseIso === "function" ? mgmtParseIso(iso) : new Date(String(iso) + "T12:00:00");
    if (!d || isNaN(d.getTime())) return iso;
    d.setDate(d.getDate() + Number(days || 0));
    return typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(d) : iso;
}

function ensureFinanceRec() {
    if (!appData || typeof appData !== "object") return null;
    if (!appData.financeRec || typeof appData.financeRec !== "object") {
        appData.financeRec = {};
    }
    const root = appData.financeRec;
    if (!Array.isArray(root.areas)) {
        root.areas = [
            { finance_area_id: "farea_shop", object_id: "farea_shop", name: "Магазин Володарского", sort_order: 10, is_active: true },
            { finance_area_id: "farea_territory", object_id: "farea_territory", name: "Территория", sort_order: 20, is_active: true }
        ];
    }
    if (!Array.isArray(root.sources)) root.sources = [];
    if (!Array.isArray(root.categories)) root.categories = [];
    if (!Array.isArray(root.operations)) root.operations = [];
    if (!Array.isArray(root.money_transits)) root.money_transits = [];
    if (!Array.isArray(root.openings)) root.openings = [];
    if (!Array.isArray(root.day_closings)) root.day_closings = [];
    if (!Array.isArray(root.period_states)) root.period_states = [];
    if (!Array.isArray(root.formulas)) root.formulas = [];
    if (!Array.isArray(root.audit)) root.audit = [];
    if (!Array.isArray(root.import_batches)) root.import_batches = [];
    root.areas.forEach(function (a, i) {
        if (!a || typeof a !== "object") return;
        if (!a.finance_area_id && a.object_id) a.finance_area_id = a.object_id;
        if (!a.finance_area_id) a.finance_area_id = "farea_" + (i + 1);
        a.object_id = a.object_id || a.finance_area_id;
        a.finance_area_id = a.object_id;
        if (a.is_active == null) a.is_active = true;
        if (a.sort_order == null) a.sort_order = (i + 1) * 10;
        if (a.primary_cash_source_id == null && a.primary_cash_account_id) {
            a.primary_cash_source_id = a.primary_cash_account_id;
        }
        if (a.primary_cash_source_id == null) a.primary_cash_source_id = "";
        a.primary_cash_account_id = a.primary_cash_source_id || "";
        if (a.external_id == null) a.external_id = "";
    });
    root.sources.forEach(function (s, i) {
        if (!s || typeof s !== "object") return;
        if (!s.finance_source_id && s.account_id) s.finance_source_id = s.account_id;
        if (!s.finance_source_id) s.finance_source_id = "fsrc_" + (i + 1);
        if (!s.finance_area_id && s.object_id) s.finance_area_id = s.object_id;
        s.account_id = s.account_id || s.finance_source_id;
        s.finance_source_id = s.account_id;
        s.object_id = s.object_id || s.finance_area_id || "";
        if (s.object_id) s.finance_area_id = s.object_id;
        if (!s.account_type || !FR_ACCOUNT_TYPES.some(function (t) { return t.id === s.account_type; })) {
            s.account_type = "cash";
        }
        if (s.is_active == null) s.is_active = true;
        if (s.sort_order == null) s.sort_order = (i + 1) * 10;
        if (s.external_id == null) s.external_id = "";
        /* Участие в фактическом остатке недели: по умолчанию да (совместимость). */
        if (s.in_actual_balance == null) s.in_actual_balance = true;
        if (s.storage_account_id == null) s.storage_account_id = "";
        if (s.storage_account_id === s.account_id || s.storage_account_id === s.finance_source_id) {
            s.storage_account_id = "";
        }
        if (frIsTerritoryArea(s.object_id || s.finance_area_id)) {
            if (!s.revenue_direction || !FR_REVENUE_DIRS.some(function (d) { return d.id === s.revenue_direction; })) {
                s.revenue_direction = "none";
            }
            if (s.shop_block == null) s.shop_block = "";
        } else if (frIsShopArea(s.object_id || s.finance_area_id)) {
            if (s.revenue_direction == null) s.revenue_direction = "";
            /* shop_block — устаревший признак «Доход/Внедоход»; поле оставляем в данных, в логике не используем */
            if (s.shop_block == null) s.shop_block = "";
        } else if (s.revenue_direction == null) {
            s.revenue_direction = "";
        }
    });
    /* Очистить битые ссылки «основное место хранения» */
    root.sources.forEach(function (s) {
        if (!s || !s.storage_account_id) return;
        const host = root.sources.find(function (x) {
            return x && (x.finance_source_id === s.storage_account_id || x.account_id === s.storage_account_id);
        });
        if (!host || (host.object_id || host.finance_area_id) !== (s.object_id || s.finance_area_id)) {
            s.storage_account_id = "";
        }
    });
    root.operations.forEach(function (o) {
        if (!o || typeof o !== "object") return;
        if (!o.movement_kind && !o.op_type) {
            o.movement_kind = o.type === "in" ? "income" : (o.type === "transfer" ? "transfer" : "expense");
        }
        frNormalizeOperationRecord(o);
        if (o.movement_kind === "transfer" || o.op_type === "transfer") {
            o.type = "transfer";
            o.movement_kind = "transfer";
            o.op_type = "transfer";
            if (!o.finance_source_to_id) o.finance_source_to_id = "";
            if (!o.account_to_id) o.account_to_id = o.finance_source_to_id || "";
        } else if (o.movement_kind === "income" || o.op_type === "income"
            || o.movement_kind === "non_income" || o.op_type === "non_income") {
            o.type = "in";
            if (o.op_type === "non_income" || o.movement_kind === "non_income") {
                o.movement_kind = "non_income";
                o.op_type = "non_income";
            } else {
                o.movement_kind = "income";
                o.op_type = "income";
            }
        } else if (o.movement_kind === "dividend" || o.op_type === "dividend") {
            o.type = "out";
            o.movement_kind = "dividend";
            o.op_type = "dividend";
        } else {
            o.type = "out";
            o.movement_kind = "expense";
            o.op_type = "expense";
        }
        /* Выручка направлений: income → со счёта; иначе не выручка */
        {
            const k = frOpKind(o);
            const oid = o.object_id || o.finance_area_id || "";
            if (k === "income" && frIsTerritoryArea(oid)) {
                o.revenue_direction = frLookupRevenueDirection(frOpAccountId(o), oid);
                o.direction = FR_DIR_TO_1C[o.revenue_direction] || "";
            } else if (k === "non_income" || k === "transfer" || k === "expense" || k === "dividend") {
                o.revenue_direction = "none";
                o.direction = "";
            }
        }
        if (!o.import_fingerprint) o.import_fingerprint = "";
        if (!o.import_batch_id) o.import_batch_id = "";
        /* Старые импорты могли хранить «кому выдано» только в comment */
        if ((o.payee == null || String(o.payee).trim() === "") && o.comment) {
            o.payee = String(o.comment).trim();
        }
        if (o.payee == null) o.payee = "";
        if (o.operation == null) o.operation = "";
        const nd = frNormDate(o.date);
        if (nd) {
            o.date = nd;
            const wk = frWeek(nd);
            if (wk && wk.period_key) o.period_key = wk.period_key;
        }
    });
    (root.money_transits || []).forEach(function (t) {
        frNormalizeTransitRecord(t);
    });
    (root.openings || []).forEach(frNormalizeBalanceRow);
    (root.day_closings || []).forEach(frNormalizeBalanceRow);
    root.areas.forEach(function (a) {
        if (!a) return;
        const oid = a.object_id || a.finance_area_id;
        if (!root.formulas.some(function (f) { return f && (f.finance_area_id === oid || f.object_id === oid); })) {
            root.formulas.push({
                finance_area_id: oid,
                object_id: oid,
                terms: [
                    { metric_id: "finm_income", sign: "+" },
                    { metric_id: "finm_non_income", sign: "+" },
                    { metric_id: "finm_outflow", sign: "-" }
                ]
            });
        } else {
            root.formulas.forEach(function (f) {
                if (!f) return;
                if (f.finance_area_id === oid || f.object_id === oid) {
                    f.object_id = oid;
                    f.finance_area_id = oid;
                }
            });
        }
        const cashList = (root.sources || []).filter(function (s) {
            return s && (s.finance_area_id === oid || s.object_id === oid) && s.account_type === "cash" && s.is_active !== false;
        });
        const primaryId = a.primary_cash_source_id || a.primary_cash_account_id || "";
        if (primaryId) {
            const ok = cashList.some(function (s) {
                return s.finance_source_id === primaryId || s.account_id === primaryId;
            });
            if (!ok) {
                a.primary_cash_source_id = cashList[0] ? (cashList[0].account_id || cashList[0].finance_source_id) : "";
            } else {
                a.primary_cash_source_id = primaryId;
            }
        } else if (cashList.length) {
            a.primary_cash_source_id = cashList[0].account_id || cashList[0].finance_source_id;
        }
        a.primary_cash_account_id = a.primary_cash_source_id || "";
    });
    /* Больше не создаём псевдо-счета «ВНЕ ДОХОДА» — внедоход вводится комментарием. */
    if (!root.shop_non_income_seeded) {
        root.shop_non_income_seeded = true;
    }
    /* Один раз: legacy shop_block=non_income — деактивируем (не удаляем), чтобы не попадали в таблицу дохода. */
    if (!root.shop_block_retired) {
        (root.sources || []).forEach(function (s) {
            if (!s || !frIsShopArea(s.object_id || s.finance_area_id)) return;
            if (s.shop_block === "non_income") {
                s.is_active = false;
            }
        });
        root.shop_block_retired = true;
    }
    return root;
}

function frAccountTypeLabel(typeId) {
    const hit = FR_ACCOUNT_TYPES.find(function (t) { return t.id === typeId; });
    return hit ? hit.label : "Счёт";
}

/** Участвует ли счёт в сверке фактических остатков недели. */
function frInActualBalance(src) {
    if (!src || src.is_active === false) return false;
    return src.in_actual_balance !== false;
}

/** Активные счета объекта для блока «Фактические остатки». */
function frActualBalanceSources(areaId) {
    return frSources(areaId, false).filter(frInActualBalance);
}

/** Подпись «основного места хранения» (инфо, без операций). */
function frStorageAccountLabel(areaId, src) {
    if (!src || !src.storage_account_id) return "";
    const host = frSourceByAccountId(areaId, src.storage_account_id);
    return host ? (host.name || src.storage_account_id) : "";
}

/**
 * Канал расхода по типу счёта (без выбора пользователем):
 * cash → «Касса»; bank/card/(прочее безнал) → «Банк».
 */
function frAccountExpenseChannel(srcOrType) {
    const typeId = typeof srcOrType === "string"
        ? srcOrType
        : (srcOrType && srcOrType.account_type) || "";
    if (typeId === "cash") return "cash";
    return "bank";
}

function frSourceByAccountId(areaId, accountId) {
    return frSources(areaId, true).find(function (s) {
        return s && (s.finance_source_id === accountId || s.account_id === accountId);
    }) || null;
}

function frExpenseChannel(o, areaId) {
    const sid = frOpAccountId(o);
    const src = frSourceByAccountId(areaId, sid);
    return frAccountExpenseChannel(src);
}

function frExpenseChannelLabel(channel) {
    return channel === "cash" ? "Касса" : "Банк";
}

/** Сумма расходов объекта за день по каналу: cash | bank | all. */
function frExpenseSumForDay(areaId, periodKey, date, channel) {
    let sum = 0;
    frOpsForDate(areaId, periodKey, date, "expense").forEach(function (o) {
        if (channel && channel !== "all" && frExpenseChannel(o, areaId) !== channel) return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

/** Сумма расходов за неделю по каналу. */
function frExpenseSumForWeek(areaId, periodKey, channel) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    let sum = 0;
    frWeekDates(w).forEach(function (d) {
        sum += frExpenseSumForDay(areaId, periodKey, d, channel);
    });
    return frRound(sum);
}

/** Расходы дня, сгруппированные по счёту (внутри канала). */
function frExpenseOpsGroupedByAccount(areaId, periodKey, date, channel) {
    const map = {};
    frOpsForDate(areaId, periodKey, date || null, "expense").forEach(function (o) {
        if (date && frNormDate(o.date) !== frNormDate(date)) return;
        if (channel && channel !== "all" && frExpenseChannel(o, areaId) !== channel) return;
        const sid = frOpAccountId(o) || "_none";
        if (!map[sid]) map[sid] = { account_id: sid, ops: [], sum: 0 };
        map[sid].ops.push(o);
        map[sid].sum += frNum(o.amount);
    });
    const list = Object.keys(map).map(function (k) {
        map[k].sum = frRound(map[k].sum);
        map[k].ops.sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        return map[k];
    });
    list.sort(function (a, b) {
        const sa = frSourceByAccountId(areaId, a.account_id);
        const sb = frSourceByAccountId(areaId, b.account_id);
        return Number((sa && sa.sort_order) || 0) - Number((sb && sb.sort_order) || 0)
            || String((sa && sa.name) || a.account_id).localeCompare(String((sb && sb.name) || b.account_id), "ru");
    });
    return list;
}

function frPrimaryCashId(areaId) {
    const area = ((appData.financeRec && appData.financeRec.areas) || []).find(function (a) {
        return a && a.finance_area_id === areaId;
    });
    if (!area) return "";
    if (area.primary_cash_source_id) {
        const src = frSources(areaId, true).find(function (s) {
            return s.finance_source_id === area.primary_cash_source_id;
        });
        if (src && src.account_type === "cash" && src.is_active !== false) return src.finance_source_id;
    }
    const firstCash = frSources(areaId, false).find(function (s) { return s.account_type === "cash"; });
    return firstCash ? firstCash.finance_source_id : "";
}

function frIsCashAccountType(typeId) {
    return typeId === "cash";
}

function frIsCashSource(src) {
    return !!(src && frIsCashAccountType(src.account_type));
}

/**
 * Недельный расчёт Основной кассы объекта (без создания перемещений).
 * opening(осн.) + наличные income всех касс объекта + наличный внедоход − расходы «Касса»
 * (− чистый уход наличности в безнал через реальные перемещения).
 * Каждая операция учитывается один раз; безнал не входит.
 */
function frMainCashWeekCalc(areaId, periodKey) {
    const primaryId = frPrimaryCashId(areaId);
    if (!primaryId) {
        return {
            primaryId: "",
            opening: 0,
            cashIncome: 0,
            cashNonIncome: 0,
            cashExpense: 0,
            cashDividend: 0,
            cashTransferNetOut: 0,
            calculated: 0,
            incomeBySource: []
        };
    }
    const opening = frOpeningValue(areaId, periodKey, primaryId);
    const incomeMap = {};
    let cashIncome = 0;
    let cashNonIncome = 0;
    let cashTransferNetOut = 0;

    frOps(areaId, periodKey).forEach(function (o) {
        const amt = frNum(o.amount);
        if (!(amt > 0)) return;
        const kind = frOpKind(o);
        if (kind === "transfer") {
            const fromId = frOpAccountId(o);
            const toId = frOpAccountToId(o);
            const fromCash = frIsCashSource(frSourceByAccountId(areaId, fromId));
            const toCash = frIsCashSource(frSourceByAccountId(areaId, toId));
            if (fromCash && !toCash) cashTransferNetOut += amt;
            else if (toCash && !fromCash) cashTransferNetOut -= amt;
            return;
        }
        if (kind === "dividend" || kind === "expense") return;
        const sid = frOpAccountId(o);
        const src = frSourceByAccountId(areaId, sid);
        if (!frIsCashSource(src)) return;
        if (kind === "income") {
            cashIncome += amt;
            if (!incomeMap[sid]) {
                incomeMap[sid] = { account_id: sid, name: src.name || sid, amount: 0 };
            }
            incomeMap[sid].amount += amt;
        } else if (kind === "non_income") {
            cashNonIncome += amt;
        }
    });

    const cashExpense = frExpenseSumForWeek(areaId, periodKey, "cash");
    const cashDividend = frCashDividendSumForWeek(areaId, periodKey);
    cashIncome = frRound(cashIncome);
    cashNonIncome = frRound(cashNonIncome);
    cashTransferNetOut = frRound(cashTransferNetOut);
    const calculated = frRound(
        opening + cashIncome + cashNonIncome - cashExpense - cashDividend - cashTransferNetOut
    );

    const incomeBySource = Object.keys(incomeMap).map(function (k) {
        incomeMap[k].amount = frRound(incomeMap[k].amount);
        return incomeMap[k];
    }).sort(function (a, b) {
        const sa = frSourceByAccountId(areaId, a.account_id);
        const sb = frSourceByAccountId(areaId, b.account_id);
        return Number((sa && sa.sort_order) || 0) - Number((sb && sb.sort_order) || 0)
            || String(a.name || "").localeCompare(String(b.name || ""), "ru");
    });

    return {
        primaryId: primaryId,
        opening: frRound(opening),
        cashIncome: cashIncome,
        cashNonIncome: cashNonIncome,
        cashExpense: cashExpense,
        cashDividend: cashDividend,
        cashTransferNetOut: cashTransferNetOut,
        calculated: calculated,
        incomeBySource: incomeBySource
    };
}

/** Расчётный остаток для строки в блоке факт. остатков (осн. касса — сводная формула). */
function frWeekEndCalculatedForAccount(areaId, periodKey, endDate, sourceId) {
    const primaryId = frPrimaryCashId(areaId);
    if (primaryId && sourceId === primaryId) {
        return frMainCashWeekCalc(areaId, periodKey).calculated;
    }
    /* Прочие наличные кассы объекта: в недельном факте наличность уже в Основной кассе. */
    if (primaryId) {
        const src = frSourceByAccountId(areaId, sourceId);
        if (frIsCashSource(src)) return 0;
    }
    return frDayCalculated(areaId, periodKey, endDate, sourceId).calculated;
}

/** Значение для переноса на следующую неделю: осн. касса — сводная, остальные — дневной расчёт. */
function frCarryClosingValue(areaId, periodKey, endDate, sourceId) {
    const primaryId = frPrimaryCashId(areaId);
    if (primaryId && sourceId === primaryId) {
        return frMainCashWeekCalc(areaId, periodKey).calculated;
    }
    return frDayCalculated(areaId, periodKey, endDate, sourceId).calculated;
}

function frToggleMainCashCalc(areaId) {
    if (!frUi.mainCashCalcOpen) frUi.mainCashCalcOpen = {};
    frUi.mainCashCalcOpen[areaId] = !frUi.mainCashCalcOpen[areaId];
    renderFinanceRec();
}

function frToggleMainCashIncome(areaId) {
    if (!frUi.mainCashIncomeOpen) frUi.mainCashIncomeOpen = {};
    frUi.mainCashIncomeOpen[areaId] = !frUi.mainCashIncomeOpen[areaId];
    renderFinanceRec();
}

/* ========== Дивиденды / Деньги в пути ========== */

function frNormalizeTransitRecord(t) {
    if (!t || typeof t !== "object") return t;
    if (!t.transit_id) t.transit_id = nextPrefixedId("ftr", ((appData.financeRec && appData.financeRec.money_transits) || []).map(function (x) {
        return x && x.transit_id;
    }));
    t.object_id = t.object_id || t.finance_area_id || "";
    if (t.object_id) t.finance_area_id = t.object_id;
    t.from_account_id = t.from_account_id || "";
    t.transit_account_id = t.transit_account_id || "";
    t.amount_sent = frRound(frNum(t.amount_sent));
    t.amount_closed = frRound(frNum(t.amount_closed));
    if (t.amount_closed < 0) t.amount_closed = 0;
    if (t.amount_closed > t.amount_sent) t.amount_closed = t.amount_sent;
    t.amount_open = frRound(Math.max(0, t.amount_sent - t.amount_closed));
    if (!Array.isArray(t.closes)) t.closes = [];
    t.closes.forEach(function (c) {
        if (!c) return;
        c.amount = frRound(frNum(c.amount));
        c.to_account_id = c.to_account_id || "";
        c.date = frNormDate(c.date) || c.date || "";
        if (c.comment == null) c.comment = "";
    });
    if (t.amount_open < 0.005) {
        t.status = "closed";
        t.amount_open = 0;
    } else if (t.amount_closed >= 0.005) {
        t.status = "partial";
    } else {
        t.status = "open";
    }
    if (t.comment == null) t.comment = "";
    if (t.send_date) t.send_date = frNormDate(t.send_date) || t.send_date;
    return t;
}

function frTransitStatusLabel(status) {
    if (status === "closed") return "Закрыто";
    if (status === "partial") return "Частично закрыто";
    return "В пути";
}

function frIsTransitSource(src) {
    return !!(src && src.account_type === "in_transit");
}

function frTransitAccountId(areaId) {
    const list = frSources(areaId, false).filter(frIsTransitSource);
    if (!list.length) {
        const any = frSources(areaId, true).filter(frIsTransitSource);
        return any.length ? any[0].finance_source_id : "";
    }
    return list[0].finance_source_id;
}

function frTransits(areaId) {
    ensureFinanceRec();
    return ((appData.financeRec && appData.financeRec.money_transits) || []).filter(function (t) {
        return t && (t.object_id || t.finance_area_id) === areaId;
    });
}

function frTransitById(transitId) {
    ensureFinanceRec();
    return ((appData.financeRec && appData.financeRec.money_transits) || []).find(function (t) {
        return t && t.transit_id === transitId;
    }) || null;
}

function frTransitOpenSum(areaId) {
    let sum = 0;
    frTransits(areaId).forEach(function (t) {
        frNormalizeTransitRecord(t);
        if (t.status === "closed") return;
        sum += frNum(t.amount_open);
    });
    return frRound(sum);
}

function frTransitOpenList(areaId) {
    return frTransits(areaId).filter(function (t) {
        frNormalizeTransitRecord(t);
        return t.status !== "closed" && frNum(t.amount_open) >= 0.005;
    }).sort(function (a, b) {
        return String(a.send_date || "").localeCompare(String(b.send_date || ""))
            || String(a.transit_id || "").localeCompare(String(b.transit_id || ""));
    });
}

function frTransitFromPrevWeek(areaId, weekStart) {
    const start = frNormDate(weekStart);
    return frTransitOpenList(areaId).filter(function (t) {
        const d = frNormDate(t.send_date);
        return d && start && d < start;
    });
}

function frTransitPrevWeekSum(areaId, weekStart) {
    let sum = 0;
    frTransitFromPrevWeek(areaId, weekStart).forEach(function (t) {
        sum += frNum(t.amount_open);
    });
    return frRound(sum);
}

function frDividendOps(areaId, periodKey) {
    return frOps(areaId, periodKey).filter(function (o) {
        return frOpKind(o) === "dividend";
    });
}

function frDividendSumForWeek(areaId, periodKey, accountId) {
    let sum = 0;
    frDividendOps(areaId, periodKey).forEach(function (o) {
        if (accountId && frOpAccountId(o) !== accountId) return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

function frCashDividendSumForWeek(areaId, periodKey) {
    let sum = 0;
    frDividendOps(areaId, periodKey).forEach(function (o) {
        const src = frSourceByAccountId(areaId, frOpAccountId(o));
        if (!frIsCashSource(src)) return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

function frWeekFactMoneyTotal(areaId, periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const sources = frActualBalanceSources(areaId);
    let sumActual = 0;
    let sumCalc = 0;
    let allFilled = sources.length > 0;
    sources.forEach(function (s) {
        const sid = s.finance_source_id;
        const calc = frWeekEndCalculatedForAccount(areaId, periodKey, w.end_date, sid);
        sumCalc += calc;
        if (frIsTransitSource(s)) {
            sumActual += calc;
            return;
        }
        const actual = frDayActual(areaId, w.end_date, sid);
        if (actual == null) allFilled = false;
        else sumActual += actual;
    });
    if (!sources.length) return { value: null, mode: "none" };
    if (allFilled) return { value: frRound(sumActual), mode: "actual" };
    return { value: frRound(sumCalc), mode: "calc" };
}

function frCreateTransferOp(areaId, fromId, toId, amount, date, comment, extra) {
    ensureFinanceRec();
    const opWeek = frWeek(date);
    const rec = {
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) {
            return x && x.finance_operation_id;
        })),
        period_key: (opWeek && opWeek.period_key) || "",
        date: frNormDate(date) || date,
        type: "transfer",
        finance_category_id: "",
        amount: frRound(frNum(amount)),
        comment: String(comment || "").trim(),
        is_deleted: false,
        created_at: new Date().toISOString()
    };
    if (extra && extra.transit_id) rec.transit_id = extra.transit_id;
    if (extra && extra.transit_role) rec.transit_role = extra.transit_role;
    frStampOperation(rec, {
        object_id: areaId,
        account_id: fromId,
        account_to_id: toId,
        op_type: "transfer",
        data_source: FR_DATA_SOURCES.manual,
        revenue_direction: "none"
    });
    appData.financeRec.operations.push(rec);
    return rec;
}

/** После создания/правки transfer: завести transit при отправке на счёт in_transit. */
function frSyncTransitAfterTransfer(areaId, op, isNew) {
    if (!op || frOpKind(op) !== "transfer" || op.is_deleted) return;
    const toId = frOpAccountToId(op);
    const fromId = frOpAccountId(op);
    const toSrc = frSourceByAccountId(areaId, toId);
    const fromSrc = frSourceByAccountId(areaId, fromId);
    if (frIsTransitSource(toSrc) && !frIsTransitSource(fromSrc)) {
        if (op.transit_id) {
            const existing = frTransitById(op.transit_id);
            if (existing && existing.send_operation_id === op.finance_operation_id) {
                existing.amount_sent = frRound(frNum(op.amount));
                existing.send_date = frNormDate(op.date) || op.date;
                existing.from_account_id = fromId;
                existing.transit_account_id = toId;
                existing.comment = op.comment || existing.comment || "";
                frNormalizeTransitRecord(existing);
                return;
            }
        }
        if (!isNew && op.transit_id) return;
        ensureFinanceRec();
        const t = {
            transit_id: nextPrefixedId("ftr", appData.financeRec.money_transits.map(function (x) {
                return x && x.transit_id;
            })),
            object_id: areaId,
            finance_area_id: areaId,
            send_date: frNormDate(op.date) || op.date,
            from_account_id: fromId,
            transit_account_id: toId,
            amount_sent: frRound(frNum(op.amount)),
            amount_closed: 0,
            comment: op.comment || "",
            status: "open",
            send_operation_id: op.finance_operation_id,
            closes: [],
            created_at: new Date().toISOString()
        };
        frNormalizeTransitRecord(t);
        op.transit_id = t.transit_id;
        op.transit_role = "send";
        appData.financeRec.money_transits.push(t);
    }
}

function frToggleDividendDetail(areaId) {
    if (!frUi.dividendDetailOpen) frUi.dividendDetailOpen = {};
    frUi.dividendDetailOpen[areaId] = !frUi.dividendDetailOpen[areaId];
    if (frUi.dividendDetailOpen[areaId] && frUi.transitDetailOpen) {
        frUi.transitDetailOpen[areaId] = false;
    }
    renderFinanceRec();
}

function frToggleTransitDetail(areaId) {
    if (!frUi.transitDetailOpen) frUi.transitDetailOpen = {};
    frUi.transitDetailOpen[areaId] = !frUi.transitDetailOpen[areaId];
    if (frUi.transitDetailOpen[areaId] && frUi.dividendDetailOpen) {
        frUi.dividendDetailOpen[areaId] = false;
    }
    renderFinanceRec();
}

function frSetPrimaryCash(sourceId) {
    if (!frCanManageAccounts()) {
        toast("Изменять счета и кассы может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area) return;
    const src = frSources(area.finance_area_id, true).find(function (s) {
        return s.finance_source_id === sourceId;
    });
    if (!src || src.account_type !== "cash") {
        toast("Основной можно назначить только счёт типа «Касса».", "error");
        return;
    }
    area.primary_cash_source_id = sourceId;
    area.primary_cash_account_id = sourceId;
    frAudit("primary_cash", { finance_source_id: sourceId, detail: src.name || "" });
    saveApp();
    renderFinanceRec();
    toast("Основная касса сохранена", "success");
}

function frAllowedAreaIds() {
    ensureFinanceRec();
    const all = (appData.financeRec.areas || []).filter(function (a) { return a && a.is_active !== false; });
    if (typeof isAppAdmin === "function" && isAppAdmin()) return all.map(function (a) { return a.finance_area_id; });
    const u = typeof currentAuthUser === "function" ? currentAuthUser() : null;
    const spec = (u && u.permissions && u.permissions.financeRecAreas) || "both";
    if (spec === "shop") return all.filter(function (a) { return a.finance_area_id === "farea_shop"; }).map(function (a) { return a.finance_area_id; });
    if (spec === "territory") return all.filter(function (a) { return a.finance_area_id === "farea_territory"; }).map(function (a) { return a.finance_area_id; });
    return all.map(function (a) { return a.finance_area_id; });
}

function frAreas() {
    const allow = frAllowedAreaIds();
    return ((appData.financeRec && appData.financeRec.areas) || []).filter(function (a) {
        return a && allow.indexOf(a.finance_area_id) !== -1;
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frActiveArea() {
    const list = frAreas();
    if (!list.length) return null;
    if (!frUi.areaId || !list.some(function (a) { return a.finance_area_id === frUi.areaId; })) {
        frUi.areaId = list[0].finance_area_id;
    }
    frUi.areaChosen = true;
    return list.find(function (a) { return a.finance_area_id === frUi.areaId; }) || list[0];
}

function frChooseArea(id) {
    const list = frAreas();
    if (!list.some(function (a) { return a.finance_area_id === id; })) {
        toast("Нет доступа к этому объекту", "error");
        return;
    }
    frUi.areaId = id;
    frUi.areaChosen = true;
    if (frIsTerritoryArea(id) || frIsShopArea(id)) {
        if (!frUi.workTab || frUi.workTab === "days" || frUi.workTab === "income" || frUi.workTab === "non_income" || frUi.workTab === "settings") {
            frUi.workTab = "reconcile";
        }
    } else if (!frUi.workTab || frUi.workTab === "income" || frUi.workTab === "non_income" || frUi.workTab === "settings") {
        frUi.workTab = "days";
    }
    frUi.inner = "work";
    currentSection = frNavSectionForArea(id);
    if (typeof saveSession === "function") saveSession();
    if (typeof applyModeUI === "function") applyModeUI();
    renderFinanceRec();
    /* подсветка пункта левого меню */
    try {
        document.querySelectorAll(".nav-btn[data-section]").forEach(function (btn) {
            const on = btn.style.display !== "none" && btn.getAttribute("data-section") === currentSection;
            btn.classList.toggle("active", on);
        });
    } catch (err) {}
}

function frChangeArea() {
    /* Выбор объекта — только через левое меню. */
    return;
}

function setFrArea(id) {
    frChooseArea(id);
}

function frWeek(iso) {
    const src = iso || frUi.weekIso || (typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(new Date()) : "");
    return typeof mgmtSpuReportingWeek === "function" ? mgmtSpuReportingWeek(src) : { start_date: src, end_date: src, period_key: "week:" + src };
}

function frEnsureWeek() {
    const w = frWeek(frUi.weekIso);
    frUi.weekIso = w.start_date;
    return w;
}

function frWeekDates(w) {
    w = w || frEnsureWeek();
    const out = [];
    let d = w.start_date;
    for (let i = 0; i < 7; i++) {
        out.push(d);
        d = frShiftIso(d, 1);
    }
    return out;
}

function frShortDayHead(iso) {
    const names = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
    const d = typeof mgmtParseIso === "function" ? mgmtParseIso(iso) : new Date(String(iso) + "T12:00:00");
    if (!d || isNaN(d.getTime())) return String(iso || "");
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    return names[d.getDay()] + " " + dd + "." + mm;
}

function frFmtCellAmount(n) {
    const x = frNum(n);
    if (!x) return "";
    return x.toFixed(2).replace(".", ",");
}

function frRevenueSources(areaId, dirId) {
    /* includeInactive: история и сверка не ломаются после деактивации */
    return frSources(areaId, true).filter(function (s) {
        return s && s.revenue_direction === dirId;
    });
}

function frIsShopArea(areaOrId) {
    const id = typeof areaOrId === "string" ? areaOrId : (areaOrId && (areaOrId.object_id || areaOrId.finance_area_id));
    return id === "farea_shop";
}

/**
 * Счета Магазина для блока «ДОХОД» в сверке: все денежные счета объекта.
 * Legacy shop_block=non_income (псевдо-строки комментариев) в доход не включаем.
 */
function frShopIncomeSources(areaId) {
    return frSources(areaId, true).filter(function (s) {
        if (!s) return false;
        if (s.shop_block === "non_income") return false;
        return true;
    });
}

/** Конфиг блоков недельной таблицы для объекта (общий механизм, разная структура). */
function frReconcileBlocks(areaId) {
    if (frIsTerritoryArea(areaId)) return FR_RECONCILE_BLOCKS;
    if (frIsShopArea(areaId)) return FR_SHOP_RECONCILE_BLOCKS;
    return [];
}

function frBlockSources(areaId, block) {
    if (!block) return [];
    if (frIsTerritoryArea(areaId)) return frRevenueSources(areaId, block.id);
    if (frIsShopArea(areaId)) return frShopIncomeSources(areaId);
    return [];
}

function frGridEntryRole(opKind) {
    return opKind === "non_income" ? "non_income_grid" : "revenue_grid";
}

function frIsGridOp(o, opKind) {
    if (!o) return false;
    const kind = opKind || frOpKind(o);
    if (o.entry_role === frGridEntryRole(kind)) return true;
    if (o.grid_cell === true && frOpKind(o) === kind) return true;
    return false;
}

function frFindGridOp(areaId, sourceId, date, opKind) {
    const want = frNormDate(date);
    const kind = opKind === "non_income" ? "non_income" : "income";
    return ((appData.financeRec && appData.financeRec.operations) || []).find(function (o) {
        if (!o || o.is_deleted) return false;
        if ((o.object_id || o.finance_area_id) !== areaId) return false;
        if ((o.account_id || o.finance_source_id || o.source_id) !== sourceId) return false;
        if (frNormDate(o.date) !== want) return false;
        return frIsGridOp(o, kind) && frOpKind(o) === kind;
    }) || null;
}

function frGetGridCellAmount(areaId, sourceId, date, opKind) {
    const kind = opKind === "non_income" ? "non_income" : "income";
    const grid = frFindGridOp(areaId, sourceId, date, kind);
    if (grid) return frNum(grid.amount);
    let sum = 0;
    frOpsForDate(areaId, frWeek(date).period_key, date, kind).forEach(function (o) {
        if (frOpAccountId(o) !== sourceId) return;
        /* Внедоход не смешиваем с выручкой в ячейках дохода */
        if (kind === "income" && frOpKind(o) !== "income") return;
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

function frIsRevenueGridOp(o) {
    return frIsGridOp(o, "income");
}

function frFindRevenueGridOp(areaId, sourceId, date) {
    return frFindGridOp(areaId, sourceId, date, "income");
}

function frGetRevenueCellAmount(areaId, sourceId, date) {
    return frGetGridCellAmount(areaId, sourceId, date, "income");
}

/**
 * Ячейка сверки → факт со стабильными ID (income | non_income).
 * Не трогает расходы и перемещения. Под будущую 1С: object_id, source_id, date, amount, data_source.
 */
function frSetGridCell(areaId, sourceId, date, amount, dataSource, opKind) {
    ensureFinanceRec();
    const kind = opKind === "non_income" ? "non_income" : "income";
    const amt = frRound(frNum(amount));
    const src = frSources(areaId, true).find(function (s) {
        return s && (s.finance_source_id === sourceId || s.account_id === sourceId);
    });
    const dirId = (src && src.revenue_direction) || "none";
    const opWeek = frWeek(date);
    const want = frNormDate(date) || date;
    const entryRole = frGridEntryRole(kind);

    const grid = frFindGridOp(areaId, sourceId, want, kind);
    (appData.financeRec.operations || []).forEach(function (o) {
        if (!o || o.is_deleted) return;
        if (grid && o.finance_operation_id === grid.finance_operation_id) return;
        if ((o.object_id || o.finance_area_id) !== areaId) return;
        if ((o.account_id || o.finance_source_id) !== sourceId) return;
        if (frNormDate(o.date) !== want) return;
        if (frOpKind(o) !== kind) return;
        o.is_deleted = true;
        o.deleted_at = new Date().toISOString();
        o.deleted_reason = "grid_replace";
    });

    if (!(amt > 0)) {
        if (grid) {
            grid.is_deleted = true;
            grid.deleted_at = new Date().toISOString();
            grid.deleted_reason = "grid_clear";
            grid.amount = 0;
        }
        return null;
    }

    if (grid) {
        grid.amount = amt;
        grid.period_key = opWeek.period_key;
        grid.date = want;
        grid.entry_role = entryRole;
        grid.grid_cell = true;
        grid.source_id = sourceId;
        if (kind === "income" && frIsTerritoryArea(areaId)) {
            grid.direction = FR_DIR_TO_1C[dirId] || grid.direction || "";
        }
        grid.data_source = dataSource || grid.data_source || FR_DATA_SOURCES.manual;
        grid.updated_at = new Date().toISOString();
        frStampOperation(grid, {
            object_id: areaId,
            account_id: sourceId,
            op_type: kind,
            data_source: grid.data_source,
            revenue_direction: (frIsTerritoryArea(areaId) && dirId !== "none") ? dirId : ""
        });
        return grid;
    }

    const rec = {
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        period_key: opWeek.period_key,
        date: want,
        type: "in",
        finance_category_id: "",
        amount: amt,
        comment: "",
        entry_role: entryRole,
        grid_cell: true,
        source_id: sourceId,
        direction: (kind === "income" && frIsTerritoryArea(areaId)) ? (FR_DIR_TO_1C[dirId] || "") : "",
        is_deleted: false,
        created_at: new Date().toISOString()
    };
    frStampOperation(rec, {
        object_id: areaId,
        account_id: sourceId,
        op_type: kind,
        data_source: dataSource || FR_DATA_SOURCES.manual,
        revenue_direction: (frIsTerritoryArea(areaId) && dirId !== "none") ? dirId : ""
    });
    appData.financeRec.operations.push(rec);
    return rec;
}

function frSetRevenueCell(areaId, sourceId, date, amount, dataSource) {
    return frSetGridCell(areaId, sourceId, date, amount, dataSource, "income");
}

function frBlockDayTotal(areaId, periodKey, blockOrId, date) {
    const blocks = frReconcileBlocks(areaId);
    const block = typeof blockOrId === "object" ? blockOrId
        : blocks.find(function (b) { return b.id === blockOrId; });
    if (!block) return 0;
    let sum = 0;
    frBlockSources(areaId, block).forEach(function (s) {
        sum += frGetGridCellAmount(areaId, s.finance_source_id, date, block.opKind || "income");
    });
    return frRound(sum);
}

function frBlockWeekTotal(areaId, periodKey, blockOrId, dates) {
    let sum = 0;
    (dates || []).forEach(function (d) { sum += frBlockDayTotal(areaId, periodKey, blockOrId, d); });
    return frRound(sum);
}

function frHandedInForDay(areaId, periodKey, date) {
    let sum = 0;
    frReconcileBlocks(areaId).forEach(function (b) {
        if ((b.opKind || "income") !== "income") return;
        sum += frBlockDayTotal(areaId, periodKey, b, date);
    });
    return frRound(sum);
}

/** Сумма всех внедоходовых операций объекта за день (не входит в ОПТ/Розницу/Склад). */
function frNonIncomeForDay(areaId, periodKey, date) {
    let sum = 0;
    frOpsForDate(areaId, periodKey, date, "non_income").forEach(function (o) {
        sum += frNum(o.amount);
    });
    return frRound(sum);
}

function frNonIncomeGridForDay(areaId, periodKey, date) {
    return frNonIncomeForDay(areaId, periodKey, date);
}

function frAreaDayAggregate(areaId, periodKey, date) {
    const sources = frSources(areaId, true);
    let opening = 0;
    let expense = 0;
    let dividend = 0;
    let transferIn = 0;
    let transferOut = 0;
    sources.forEach(function (s) {
        const sid = s.finance_source_id;
        opening += frDayOpening(areaId, date, sid);
        const mv = frDayMovements(areaId, periodKey, date, sid);
        expense += mv.expense;
        dividend += mv.dividend;
        transferIn += mv.transfer_in;
        transferOut += mv.transfer_out;
    });
    const handed = frHandedInForDay(areaId, periodKey, date);
    const nonIncome = frNonIncomeForDay(areaId, periodKey, date);
    const transferNet = frRound(transferIn - transferOut);
    const calculated = frRound(opening + handed + nonIncome - expense - dividend + transferNet);
    return {
        opening: frRound(opening),
        handed: handed,
        non_income: nonIncome,
        expense: frRound(expense),
        dividend: frRound(dividend),
        transfer_in: frRound(transferIn),
        transfer_out: frRound(transferOut),
        transfer_net: transferNet,
        calculated: calculated
    };
}

function frNonIncomeCommentKey(comment) {
    return String(comment || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function frNonIncomeOpComment(o, areaId) {
    if (!o) return "";
    const c = String(o.comment || "").trim();
    if (c) return c;
    const sid = o.account_id || o.finance_source_id || o.source_id;
    const src = (frSources(areaId, true) || []).find(function (s) {
        return s && (s.finance_source_id === sid || s.account_id === sid);
    });
    return src && src.name ? String(src.name).trim() : "";
}

function frNonIncomeLineKey(o, areaId) {
    if (o && o.non_income_line_id) return "id:" + o.non_income_line_id;
    return "c:" + frNonIncomeCommentKey(frNonIncomeOpComment(o, areaId));
}

/** Строки внедоходовых за неделю: существующие операции + группировка по комментарию / line_id. */
function frNonIncomeWeekLines(areaId, periodKey, dates) {
    const dateSet = {};
    (dates || []).forEach(function (d) { dateSet[d] = true; });
    const map = {};
    frOps(areaId, periodKey).forEach(function (o) {
        if (frOpKind(o) !== "non_income") return;
        const d = frNormDate(o.date);
        if (!d || !dateSet[d]) return;
        if (!(frNum(o.amount) > 0) && !String(o.comment || "").trim()) return;
        const key = frNonIncomeLineKey(o, areaId);
        if (!map[key]) {
            map[key] = {
                key: key,
                lineId: o.non_income_line_id || "",
                comment: frNonIncomeOpComment(o, areaId),
                byDate: {}
            };
        }
        if (!map[key].lineId && o.non_income_line_id) map[key].lineId = o.non_income_line_id;
        if (!map[key].comment) map[key].comment = frNonIncomeOpComment(o, areaId);
        if (!map[key].byDate[d]) map[key].byDate[d] = { amount: 0, opIds: [] };
        map[key].byDate[d].amount = frRound(map[key].byDate[d].amount + frNum(o.amount));
        map[key].byDate[d].opIds.push(o.finance_operation_id);
    });
    return Object.keys(map).map(function (k) { return map[k]; }).sort(function (a, b) {
        return String(a.comment || "").localeCompare(String(b.comment || ""), "ru");
    });
}

function frNonIncomeCommentSuggestions(areaId) {
    const seen = {};
    const out = [];
    ((appData.financeRec && appData.financeRec.operations) || []).forEach(function (o) {
        if (!o || o.is_deleted) return;
        if ((o.object_id || o.finance_area_id) !== areaId) return;
        if (frOpKind(o) !== "non_income") return;
        const c = String(o.comment || "").trim();
        if (!c) return;
        const k = frNonIncomeCommentKey(c);
        if (seen[k]) return;
        seen[k] = true;
        out.push(c);
    });
    out.sort(function (a, b) { return a.localeCompare(b, "ru"); });
    return out.slice(0, 100);
}

function frFindNonIncomeOpsForLine(areaId, periodKey, dates, lineKey, comment) {
    const dateSet = {};
    (dates || []).forEach(function (d) { dateSet[d] = true; });
    const wantKey = lineKey || ("c:" + frNonIncomeCommentKey(comment));
    return frOps(areaId, periodKey).filter(function (o) {
        if (frOpKind(o) !== "non_income") return false;
        const d = frNormDate(o.date);
        if (!d || !dateSet[d]) return false;
        return frNonIncomeLineKey(o, areaId) === wantKey
            || (wantKey.indexOf("c:") === 0 && frNonIncomeCommentKey(frNonIncomeOpComment(o, areaId)) === frNonIncomeCommentKey(comment));
    });
}

/**
 * Ячейка внедоходового: комментарий + дата + сумма.
 * Не трогает доходы/расходы/перемещения. Пишется на основную кассу объекта.
 */
function frSetNonIncomeCell(areaId, lineKey, comment, date, amount, dataSource) {
    ensureFinanceRec();
    const amt = frRound(frNum(amount));
    const want = frNormDate(date) || date;
    const opWeek = frWeek(want);
    const cashId = frPrimaryCashId(areaId) || (frSources(areaId, false)[0] && frSources(areaId, false)[0].finance_source_id) || "";
    const commentText = String(comment || "").replace(/\s+/g, " ").trim();
    let lineId = "";
    if (lineKey && String(lineKey).indexOf("id:") === 0) lineId = String(lineKey).slice(3);

    const existing = ((appData.financeRec && appData.financeRec.operations) || []).filter(function (o) {
        if (!o || o.is_deleted) return false;
        if ((o.object_id || o.finance_area_id) !== areaId) return false;
        if (frOpKind(o) !== "non_income") return false;
        if (frNormDate(o.date) !== want) return false;
        if (lineId && o.non_income_line_id === lineId) return true;
        if (lineId) return false;
        return frNonIncomeCommentKey(frNonIncomeOpComment(o, areaId)) === frNonIncomeCommentKey(commentText);
    });

    if (!(amt > 0)) {
        existing.forEach(function (o) {
            o.is_deleted = true;
            o.deleted_at = new Date().toISOString();
            o.deleted_reason = "non_income_clear";
        });
        return { lineId: lineId, lineKey: lineKey };
    }

    if (!lineId) {
        lineId = nextPrefixedId("fnil", (appData.financeRec.operations || []).map(function (x) {
            return x && x.non_income_line_id;
        }));
    }

    let rec = existing[0] || null;
    existing.slice(1).forEach(function (o) {
        o.is_deleted = true;
        o.deleted_at = new Date().toISOString();
        o.deleted_reason = "non_income_merge";
    });

    if (!rec) {
        rec = {
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
            period_key: opWeek.period_key,
            date: want,
            type: "in",
            finance_category_id: "",
            amount: amt,
            comment: commentText,
            entry_role: "non_income_grid",
            grid_cell: true,
            non_income_line_id: lineId,
            source_id: cashId,
            is_deleted: false,
            created_at: new Date().toISOString()
        };
        frStampOperation(rec, {
            object_id: areaId,
            account_id: cashId,
            op_type: "non_income",
            data_source: dataSource || FR_DATA_SOURCES.manual,
            revenue_direction: ""
        });
        appData.financeRec.operations.push(rec);
    } else {
        rec.amount = amt;
        rec.comment = commentText;
        rec.non_income_line_id = lineId;
        rec.entry_role = "non_income_grid";
        rec.grid_cell = true;
        rec.period_key = opWeek.period_key;
        rec.date = want;
        rec.data_source = dataSource || rec.data_source || FR_DATA_SOURCES.manual;
        rec.updated_at = new Date().toISOString();
        if (!rec.finance_source_id && cashId) {
            frStampOperation(rec, {
                object_id: areaId,
                account_id: cashId,
                op_type: "non_income",
                data_source: rec.data_source,
                revenue_direction: ""
            });
        } else {
            rec.op_type = "non_income";
            rec.movement_kind = "non_income";
            rec.type = "in";
            rec.object_id = areaId;
            rec.finance_area_id = areaId;
        }
    }
    return { lineId: lineId, lineKey: "id:" + lineId, op: rec };
}

function frRenameNonIncomeLine(areaId, periodKey, dates, lineKey, newComment) {
    const commentText = String(newComment || "").replace(/\s+/g, " ").trim();
    const ops = frFindNonIncomeOpsForLine(areaId, periodKey, dates, lineKey, "");
    let lineId = lineKey && String(lineKey).indexOf("id:") === 0 ? String(lineKey).slice(3) : "";
    ops.forEach(function (o) {
        o.comment = commentText;
        if (!lineId && o.non_income_line_id) lineId = o.non_income_line_id;
        if (!lineId) {
            lineId = nextPrefixedId("fnil", (appData.financeRec.operations || []).map(function (x) {
                return x && x.non_income_line_id;
            }));
        }
        o.non_income_line_id = lineId;
        o.updated_at = new Date().toISOString();
    });
    return lineId ? ("id:" + lineId) : ("c:" + frNonIncomeCommentKey(commentText));
}

function frTerritoryDayAggregate(areaId, periodKey, date) {
    return frAreaDayAggregate(areaId, periodKey, date);
}

function frAreaWeekOpening(areaId, periodKey) {
    let sum = 0;
    frSources(areaId, true).forEach(function (s) {
        sum += frOpeningValue(areaId, periodKey, s.finance_source_id);
    });
    return frRound(sum);
}

function frTerritoryWeekOpening(areaId, periodKey) {
    return frAreaWeekOpening(areaId, periodKey);
}

function frSources(areaId, includeInactive) {
    const aid = areaId || (frActiveArea() && frActiveArea().finance_area_id);
    return ((appData.financeRec && appData.financeRec.sources) || []).filter(function (s) {
        return s && s.finance_area_id === aid && (includeInactive || s.is_active !== false);
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frCats(areaId, includeInactive) {
    const aid = areaId || (frActiveArea() && frActiveArea().finance_area_id);
    return ((appData.financeRec && appData.financeRec.categories) || []).filter(function (s) {
        return s && s.finance_area_id === aid && (includeInactive || s.is_active !== false);
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frPeriodState(areaId, periodKey) {
    ensureFinanceRec();
    let row = (appData.financeRec.period_states || []).find(function (p) {
        return p && p.finance_area_id === areaId && p.period_key === periodKey;
    });
    if (!row) {
        const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
        row = {
            finance_period_id: nextPrefixedId("fper", (appData.financeRec.period_states || []).map(function (x) { return x && x.finance_period_id; })),
            finance_area_id: areaId,
            period_key: periodKey,
            start_date: w.start_date,
            end_date: w.end_date,
            status: "draft"
        };
        appData.financeRec.period_states.push(row);
    }
    return row;
}

function frIsClosed(areaId, periodKey) {
    const st = frPeriodState(areaId, periodKey);
    return st && st.status === "closed";
}

function frCanEditData() {
    if (!frCan("fill")) return false;
    const a = frActiveArea();
    const w = frEnsureWeek();
    if (!a) return false;
    if (frIsClosed(a.finance_area_id, w.period_key) && !frCan("setup")) return false;
    return true;
}

function frOpeningRow(areaId, periodKey, sourceId) {
    ensureFinanceRec();
    return (appData.financeRec.openings || []).find(function (o) {
        return o && o.finance_area_id === areaId && o.period_key === periodKey && o.finance_source_id === sourceId;
    }) || null;
}

function frOpeningValue(areaId, periodKey, sourceId) {
    const row = frOpeningRow(areaId, periodKey, sourceId);
    return row ? frNum(row.value) : 0;
}

function frPrevPeriodKey(periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const prev = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, -1) : null;
    return prev ? prev.period_key : "";
}

function frNormDate(iso) {
    const s = String(iso == null ? "" : iso).trim();
    if (!s) return "";
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
    const parsed = frParseExpenseDate(s);
    return parsed || "";
}

function frOpKind(o) {
    if (!o) return "expense";
    if (o.op_type === "transfer" || o.movement_kind === "transfer" || o.type === "transfer") return "transfer";
    if (o.op_type === "dividend" || o.movement_kind === "dividend") return "dividend";
    if (o.op_type === "income" || o.op_type === "non_income" || o.op_type === "expense") return o.op_type;
    if (o.movement_kind === "income" || o.movement_kind === "non_income" || o.movement_kind === "expense") return o.movement_kind;
    return o.type === "in" ? "income" : "expense";
}

function frOps(areaId, periodKey) {
    ensureFinanceRec();
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const inWeek = {};
    frWeekDates(w).forEach(function (d) { inWeek[d] = true; });
    return ((appData.financeRec && appData.financeRec.operations) || []).filter(function (o) {
        if (!o || o.is_deleted) return false;
        if ((o.object_id || o.finance_area_id) !== areaId) return false;
        const d = frNormDate(o.date);
        /* Дата внутри недели — операция входит в расчёт, даже если period_key сбился. */
        if (d && inWeek[d]) return true;
        return o.period_key === periodKey;
    });
}

function frOpsForDate(areaId, periodKey, date, kind) {
    const want = frNormDate(date);
    return frOps(areaId, periodKey).filter(function (o) {
        if (want) {
            const od = frNormDate(o.date);
            if (od !== want) return false;
        }
        if (!kind) return true;
        if (kind === "transfer") return frOpKind(o) === "transfer";
        return frOpKind(o) === kind;
    });
}

function frDayClosingRow(areaId, date, sourceId) {
    ensureFinanceRec();
    const want = frNormDate(date) || date;
    return (appData.financeRec.day_closings || []).find(function (r) {
        if (!r) return false;
        if ((r.object_id || r.finance_area_id) !== areaId) return false;
        if ((frNormDate(r.date) || r.date) !== want) return false;
        return (r.account_id || r.finance_source_id) === sourceId;
    }) || null;
}

function frDayActual(areaId, date, sourceId) {
    const row = frDayClosingRow(areaId, date, sourceId);
    if (!row) return null;
    const v = (row.actual_balance != null && row.actual_balance !== "")
        ? row.actual_balance
        : row.actual_closing;
    if (v == null || v === "") return null;
    return frNum(v);
}

function frDayMovements(areaId, periodKey, date, sourceId) {
    let income = 0, nonIncome = 0, expense = 0, dividend = 0, transferIn = 0, transferOut = 0;
    const want = frNormDate(date) || date;
    frOps(areaId, periodKey).forEach(function (o) {
        if (frNormDate(o.date) !== want) return;
        const amt = frNum(o.amount);
        if (!(amt > 0)) return;
        const kind = frOpKind(o);
        if (kind === "transfer") {
            if (frOpAccountId(o) === sourceId) transferOut += amt;
            if (frOpAccountToId(o) === sourceId) transferIn += amt;
            return;
        }
        if (frOpAccountId(o) !== sourceId) return;
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else if (kind === "dividend") dividend += amt;
        else if (kind === "expense") expense += amt;
    });
    return {
        income: frRound(income),
        non_income: frRound(nonIncome),
        expense: frRound(expense),
        dividend: frRound(dividend),
        transfer_in: frRound(transferIn),
        transfer_out: frRound(transferOut)
    };
}

function frDayOpening(areaId, date, sourceId) {
    const curWeek = frWeek(date);
    if (date === curWeek.start_date) {
        return frOpeningValue(areaId, curWeek.period_key, sourceId);
    }
    const prev = frShiftIso(date, -1);
    if (!prev || prev === date) {
        return frOpeningValue(areaId, curWeek.period_key, sourceId);
    }
    /* Конечный расчётный остаток предыдущего дня = начало текущего; факт не обязателен. */
    const prevActual = frDayActual(areaId, prev, sourceId);
    if (prevActual != null) return prevActual;
    const prevWeek = frWeek(prev);
    return frDayCalculated(areaId, prevWeek.period_key, prev, sourceId).calculated;
}

function frDayCalculated(areaId, periodKey, date, sourceId) {
    const opening = frDayOpening(areaId, date, sourceId);
    const mv = frDayMovements(areaId, periodKey, date, sourceId);
    const calculated = frRound(
        opening + mv.income + mv.non_income + mv.transfer_in
            - mv.expense - mv.dividend - mv.transfer_out
    );
    const actual = frDayActual(areaId, date, sourceId);
    const discrepancy = actual == null ? null : frRound(actual - calculated);
    return {
        opening: opening,
        income: mv.income,
        non_income: mv.non_income,
        expense: mv.expense,
        dividend: mv.dividend,
        transfer_in: mv.transfer_in,
        transfer_out: mv.transfer_out,
        calculated: calculated,
        actual: actual,
        discrepancy: discrepancy,
        status: actual == null ? "calc" : (Math.abs(discrepancy) < 0.005 ? "ok" : "bad")
    };
}

function frCarryIfNeeded(areaId, periodKey) {
    const sources = frSources(areaId, true);
    const prevKey = frPrevPeriodKey(periodKey);
    if (!prevKey) return;
    const prevW = frWeek(String(prevKey).replace(/^week:/, ""));
    sources.forEach(function (s) {
        const sid = s.finance_source_id;
        let row = frOpeningRow(areaId, periodKey, sid);
        if (row && row.is_manual) return;
        const wedActual = frDayActual(areaId, prevW.end_date, sid);
        const value = wedActual != null
            ? wedActual
            : frCarryClosingValue(areaId, prevKey, prevW.end_date, sid);
        if (!row) {
            ensureFinanceRec();
            row = {
                finance_area_id: areaId,
                object_id: areaId,
                period_key: periodKey,
                finance_source_id: sid,
                account_id: sid,
                data_source: FR_DATA_SOURCES.manual,
                is_manual: false,
                carried: true
            };
            frNormalizeBalanceRow(row);
            appData.financeRec.openings.push(row);
        }
        row.value = value;
        row.carried = true;
        row.is_manual = false;
    });
}

function frSourceTotals(areaId, periodKey, sourceId) {
    const opening = frOpeningValue(areaId, periodKey, sourceId);
    let income = 0, nonIncome = 0, expense = 0, dividend = 0, transferIn = 0, transferOut = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        const amt = frNum(o.amount);
        if (!(amt > 0)) return;
        const kind = frOpKind(o);
        if (kind === "transfer") {
            if (frOpAccountId(o) === sourceId) transferOut += amt;
            if (frOpAccountToId(o) === sourceId) transferIn += amt;
            return;
        }
        if (frOpAccountId(o) !== sourceId) return;
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else if (kind === "dividend") dividend += amt;
        else if (kind === "expense") expense += amt;
    });
    income = frRound(income);
    nonIncome = frRound(nonIncome);
    expense = frRound(expense);
    dividend = frRound(dividend);
    transferIn = frRound(transferIn);
    transferOut = frRound(transferOut);
    const inflow = frRound(income + nonIncome);
    const closing = frRound(opening + income + nonIncome + transferIn - expense - dividend - transferOut);
    return {
        opening: opening,
        income: income,
        non_income: nonIncome,
        expense: expense,
        dividend: dividend,
        transfer_in: transferIn,
        transfer_out: transferOut,
        inflow: inflow,
        outflow: frRound(expense + dividend),
        closing: closing
    };
}

function frAreaTotals(areaId, periodKey) {
    const acc = {
        opening: 0, income: 0, non_income: 0, expense: 0, dividend: 0,
        transfer_in: 0, transfer_out: 0, inflow: 0, outflow: 0, closing: 0
    };
    frSources(areaId, true).forEach(function (s) {
        const t = frSourceTotals(areaId, periodKey, s.finance_source_id);
        acc.opening += t.opening;
        acc.income += t.income;
        acc.non_income += t.non_income;
        acc.expense += t.expense;
        acc.dividend += t.dividend;
        acc.transfer_in += t.transfer_in;
        acc.transfer_out += t.transfer_out;
        acc.inflow += t.inflow;
        acc.outflow += t.outflow;
        acc.closing += t.closing;
    });
    Object.keys(acc).forEach(function (k) { acc[k] = frRound(acc[k]); });
    return acc;
}

function frWeekActualClosing(areaId, periodKey) {
    /* Сумма конечных остатков всех счетов направления (включая неактивные с остатком). */
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    let sum = 0;
    frSources(areaId, true).forEach(function (s) {
        sum += frDayCalculated(areaId, periodKey, w.end_date, s.finance_source_id).calculated;
    });
    return { value: frRound(sum), hasActual: true };
}

function frHasWeekFacts(areaId, periodKey) {
    return frSources(areaId, false).length > 0;
}

function frWeekCalculated(areaId, periodKey) {
    const tot = frAreaTotals(areaId, periodKey);
    /* Расчётный остаток компании: начало + поступления − расходы − дивиденды (перемещения не доход/расход). */
    return frRound(tot.opening + tot.income + tot.non_income - tot.expense - tot.dividend);
}

function frWeekDiscrepancy(areaId, periodKey) {
    const calculated = frWeekCalculated(areaId, periodKey);
    const accountsEnd = frWeekActualClosing(areaId, periodKey).value;
    return frRound(accountsEnd - calculated);
}

function frFirstBadDay(areaId, periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const days = frWeekDates(w);
    for (let i = 0; i < days.length; i++) {
        const date = days[i];
        const sources = frSources(areaId, false);
        for (let j = 0; j < sources.length; j++) {
            const row = frDayCalculated(areaId, periodKey, date, sources[j].finance_source_id);
            if (row.status === "bad") return date;
        }
    }
    return "";
}

function frMetricValue(metricId, areaId, periodKey) {
    const t = frAreaTotals(areaId, periodKey);
    if (metricId === "finm_opening") return t.opening;
    if (metricId === "finm_income") return t.income;
    if (metricId === "finm_non_income") return t.non_income;
    if (metricId === "finm_inflow") return t.inflow;
    if (metricId === "finm_outflow") return t.outflow;
    if (metricId === "finm_closing") return t.closing;
    if (metricId === "finm_net") return frRound(t.income + t.non_income - t.expense);
    if (metricId === "finm_actual_closing") return frWeekActualClosing(areaId, periodKey).value;
    if (metricId === "finm_discrepancy") return frWeekDiscrepancy(areaId, periodKey);
    return 0;
}

function financeRecReadMetric(metricId, areaId, periodKey) {
    try {
        ensureFinanceRec();
        return frMetricValue(metricId, areaId, periodKey);
    } catch (err) {
        console.error(err);
        return null;
    }
}

function frFormulaTerms(areaId) {
    const row = ((appData.financeRec && appData.financeRec.formulas) || []).find(function (f) { return f && f.finance_area_id === areaId; });
    return (row && row.terms && row.terms.length) ? row.terms : [
        { metric_id: "finm_income", sign: "+" },
        { metric_id: "finm_non_income", sign: "+" },
        { metric_id: "finm_outflow", sign: "-" }
    ];
}

function frDiscrepancy(areaId, periodKey) {
    return frWeekDiscrepancy(areaId, periodKey);
}

function frAudit(kind, extra) {
    try {
        ensureFinanceRec();
        const a = frActiveArea();
        const w = frEnsureWeek();
        appData.financeRec.audit.push(Object.assign({
            audit_id: nextPrefixedId("faud", appData.financeRec.audit.map(function (x) { return x && x.audit_id; })),
            at: new Date().toISOString(),
            user_id: typeof currentAuthUserId === "function" ? currentAuthUserId() : "",
            kind: kind,
            finance_area_id: a ? a.finance_area_id : "",
            period_key: w.period_key
        }, extra || {}));
    } catch (err) { console.error(err); }
}

function frStatusLabel(st) {
    return { draft: "Черновик", filled: "Заполнено", review: "На проверке", reconciled: "Сверено", closed: "Закрыто" }[st] || "Черновик";
}

function frShiftWeek(delta) {
    const w = frEnsureWeek();
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, delta) : w;
    frUi.weekIso = next.start_date;
    renderFinanceRec();
}

function setFrInner(tab) {
    if (tab === "settings") {
        frUi.workTab = "accounts";
        frUi.inner = "work";
    } else {
        frUi.inner = "work";
        if (frUi.workTab === "accounts") frUi.workTab = "days";
    }
    const area = frActiveArea();
    currentSection = area ? frNavSectionForArea(area.finance_area_id) : "financeRec-shop";
    if (typeof saveSession === "function") saveSession();
    renderFinanceRec();
}

function setFrWorkTab(tab) {
    const ok = { reconcile: 1, days: 1, week: 1, expense: 1, transfer: 1, accounts: 1 };
    const map = { income: "days", non_income: "days", settings: "accounts" };
    const next = map[tab] || tab;
    frUi.workTab = ok[next] ? next : "days";
    frUi.inner = "work";
    const area = frActiveArea();
    if (area && frIsTerritoryArea(area) && frUi.workTab === "days") {
        /* для Территории основной экран — Сверка */
    }
    currentSection = area ? frNavSectionForArea(area.finance_area_id) : "financeRec-shop";
    if (typeof saveSession === "function") saveSession();
    renderFinanceRec();
}

function renderFinanceRec() {
    try {
        ensureFinanceRec();
        const host = document.getElementById("financeRecHost");
        if (!host) return;
        if (!frCan("view")) {
            host.innerHTML = '<div class="note">Нет доступа к финансовой сверке.</div>';
            return;
        }
        const areas = frAreas();
        if (!areas.length) {
            host.innerHTML = '<div class="note">Нет доступных объектов.</div>';
            return;
        }
        const area = frActiveArea();
        if (!area) {
            host.innerHTML = '<div class="note">Выберите объект в левом меню: Магазин Володарского или Территория.</div>';
            return;
        }
        /* синхронизация активного пункта меню с объектом */
        const navSec = frNavSectionForArea(area.finance_area_id);
        if (currentSection === "financeRec" || currentSection === "financeRec-settings"
            || !isFrNavSection(currentSection)) {
            currentSection = navSec;
        }
        const w = frEnsureWeek();
        frCarryIfNeeded(area.finance_area_id, w.period_key);
        const tot = frAreaTotals(area.finance_area_id, w.period_key);
        const weekCalc = frWeekCalculated(area.finance_area_id, w.period_key);
        const accountsEnd = frWeekActualClosing(area.finance_area_id, w.period_key).value;
        const disc = frWeekDiscrepancy(area.finance_area_id, w.period_key);
        const hasFacts = frHasWeekFacts(area.finance_area_id, w.period_key);
        const balanced = hasFacts && Math.abs(disc) < 0.005;
        const st = frPeriodState(area.finance_area_id, w.period_key);
        const closed = st.status === "closed";
        const canFill = frCanEditData();
        const isTerritory = frIsTerritoryArea(area);
        const isShop = frIsShopArea(area);
        if (!frUi.workTab || frUi.workTab === "income" || frUi.workTab === "non_income") {
            frUi.workTab = (isTerritory || isShop) ? "reconcile" : "days";
        }
        if (frUi.workTab === "settings") frUi.workTab = "accounts";
        if ((isTerritory || isShop) && frUi.workTab === "days") frUi.workTab = "reconcile";
        frUi.inner = "work";

        let html = '<div class="fr-wrap">';
        html += '<div class="fr-crumb">Финконтроль → <b class="fr-object-name">' + escapeHtml(area.name) + "</b></div>";
        html += '<div class="fr-weekbar">';
        html += '<span class="fr-week-label">Рабочая неделя: '
            + escapeHtml(frFmtDate(w.start_date) + " — " + frFmtDate(w.end_date) + " (чт–ср)") + "</span>";
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(-1)">← Пред. неделя</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeekToCurrent()">Текущая</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(1)">След. неделя →</button>';
        html += '<span class="fr-status-pill">' + escapeHtml(frStatusLabel(st.status)) + "</span>";
        html += "</div>";

        html += '<div class="fr-cards fr-cards-5">';
        if (isTerritory || isShop) {
            const divSum = frDividendSumForWeek(area.finance_area_id, w.period_key);
            const transitOpen = frTransitOpenSum(area.finance_area_id);
            const factMoney = frWeekFactMoneyTotal(area.finance_area_id, w.period_key);
            html += '<div class="fr-card"><span>Выручка</span><b class="fr-in">' + frMoney(tot.income) + "</b></div>";
            html += '<div class="fr-card"><span>Расходы</span><b class="fr-out">' + frMoney(tot.expense) + "</b></div>";
            html += '<div class="fr-card fr-card-clickable' + (divSum > 0 ? " fr-card-dividend" : "") + '"'
                + ' onclick="frToggleDividendDetail(\'' + escapeAttribute(area.finance_area_id) + '\')" title="Детализация дивидендов">'
                + "<span>Дивиденды</span><b class=\"fr-out\">" + frMoney(divSum) + "</b></div>";
            html += '<div class="fr-card fr-card-clickable' + (transitOpen > 0 ? " fr-card-transit-warn" : " fr-card-transit-ok") + '"'
                + ' onclick="frToggleTransitDetail(\'' + escapeAttribute(area.finance_area_id) + '\')" title="Контроль денег в пути">'
                + "<span>Деньги в пути</span><b>" + frMoney(transitOpen) + "</b>"
                + (transitOpen < 0.005
                    ? '<span class="fr-transit-ok-mark">✓</span>'
                    : '<span class="fr-transit-warn-mark">есть незакрытые</span>')
                + "</div>";
            html += '<div class="fr-card"><span>Фактический остаток денег</span><b>'
                + (factMoney.value == null ? "—" : frMoney(factMoney.value)) + "</b>"
                + (factMoney.mode === "calc" ? '<span class="fr-muted">расчётный</span>' : "")
                + "</div>";
        } else {
            html += '<div class="fr-card"><span>Остаток на начало недели</span><b>' + frMoney(tot.opening) + "</b></div>";
            html += '<div class="fr-card"><span>Доходы</span><b class="fr-in">' + frMoney(tot.income) + "</b></div>";
            html += '<div class="fr-card"><span>Внедоходовые</span><b class="fr-non">' + frMoney(tot.non_income) + "</b></div>";
            html += '<div class="fr-card"><span>Расходы</span><b class="fr-out">' + frMoney(tot.expense) + "</b></div>";
            html += '<div class="fr-card ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><span>Расхождение</span><b>'
                + (hasFacts ? frMoney(disc) : "—") + "</b><span>"
                + (hasFacts ? (balanced ? "✓ 0 ₽ — неделя сошлась" : "⚠ Не сошлось") : "Добавьте счета") + "</span></div>";
        }
        html += "</div>";
        if (isTerritory || isShop) {
            const prevTransitBanner = frTransitPrevWeekSum(area.finance_area_id, w.start_date);
            if (prevTransitBanner >= 0.005) {
                html += '<div class="fr-transit-carry-banner" onclick="frToggleTransitDetail(\''
                    + escapeAttribute(area.finance_area_id) + '\')">'
                    + "🔴 В пути с прошлой недели: " + frMoney(prevTransitBanner) + "</div>";
            }
        }
        if ((isTerritory || isShop) && frUi.dividendDetailOpen && frUi.dividendDetailOpen[area.finance_area_id]) {
            html += renderFrDividendDetailPanel(area.finance_area_id, w, canFill && !closed);
        }
        if ((isTerritory || isShop) && frUi.transitDetailOpen && frUi.transitDetailOpen[area.finance_area_id]) {
            html += renderFrTransitDetailPanel(area.finance_area_id, w, canFill && !closed);
        }

        if (isTerritory && frUi.workTab !== "reconcile") {
            html += renderFrTerritoryRevenueStrip(area.finance_area_id, w.period_key);
        }

        html += '<div class="fr-work-tabs">';
        const tabs = (isTerritory || isShop)
            ? [
                ["reconcile", "Сверка"],
                ["expense", "Расходы"],
                ["transfer", "Перемещения"],
                ["accounts", "Счета и кассы"],
                ["week", "Неделя"]
            ]
            : [
                ["days", "День"],
                ["week", "Неделя"],
                ["expense", "Расходы"],
                ["transfer", "Перемещения"],
                ["accounts", "Счета и кассы"]
            ];
        tabs.forEach(function (t) {
            html += '<button type="button" class="metrics-tab' + (frUi.workTab === t[0] ? " active" : "") + '" onclick="setFrWorkTab(\'' + t[0] + "')\">"
                + escapeHtml(t[1]) + "</button>";
        });
        html += "</div>";

        if (frUi.workTab === "reconcile" && (isTerritory || isShop)) {
            html += renderFrReconcileTab(area.finance_area_id, w, canFill && !closed);
        } else if (frUi.workTab === "expense") html += renderFrExpenseTab(area.finance_area_id, w, canFill && !closed);
        else if (frUi.workTab === "transfer") html += renderFrTransferTab(area.finance_area_id, w, canFill && !closed);
        else if (frUi.workTab === "days") html += renderFrDaysTab(area.finance_area_id, w, canFill && !closed);
        else if (frUi.workTab === "accounts") html += renderFrSettingsHtml(area);
        else html += renderFrWeekTab(area.finance_area_id, w, tot, weekCalc, accountsEnd, disc, balanced, st, closed, hasFacts);

        html += "</div>";
        host.innerHTML = html;
        if (frUi.workTab === "reconcile" && (isTerritory || isShop)) {
            frBindReconcileGrid();
        }
        try {
            document.querySelectorAll(".nav-btn[data-section]").forEach(function (btn) {
                if (btn.getAttribute("data-finrec") !== "1") return;
                const on = btn.style.display !== "none" && btn.getAttribute("data-section") === frNavSectionForArea(area.finance_area_id);
                btn.classList.toggle("active", on);
            });
        } catch (navErr) {}
    } catch (err) {
        console.error(err);
        const host = document.getElementById("financeRecHost");
        if (host) host.innerHTML = '<div class="note">Ошибка финансовой сверки. Остальные разделы Work Time не затронуты.</div>';
    }
}

function renderFrTerritoryRevenueStrip(areaId, periodKey) {
    const dirs = [
        { id: "opt", label: "ОПТ" },
        { id: "retail", label: "Розница" },
        { id: "warehouse", label: "Склад" }
    ];
    let html = '<div class="fr-cards fr-cards-3" style="margin-top:0">';
    dirs.forEach(function (d) {
        const income = frDirectionWeekIncome(areaId, periodKey, d.id);
        html += '<div class="fr-card"><span>Выручка · ' + escapeHtml(d.label) + "</span><b class=\"fr-in\">"
            + frMoney(income) + "</b>"
            + '<span class="fr-muted">поступления на счета направления; перемещения и внедоход не входят</span></div>';
    });
    html += "</div>";
    return html;
}

function renderFrTerritoryReconcileTab(areaId, w, canEdit) {
    return renderFrReconcileTab(areaId, w, canEdit);
}

function renderFrReconcileTab(areaId, w, canEdit) {
    const dates = frWeekDates(w);
    const blocks = frReconcileBlocks(areaId);
    const isTerritory = frIsTerritoryArea(areaId);
    const isShop = frIsShopArea(areaId);
    const weekOpen = frAreaWeekOpening(areaId, w.period_key);
    let html = '<div class="fr-box fr-reconcile-box">';
    if (isTerritory) {
        html += "<h3>Сверка недели · ОПТ / Розница / Склад</h3>";
        html += '<div class="fr-muted" style="margin-bottom:8px">'
            + "Вводите суммы прямо в ячейки или вставьте из 1С (Ctrl+V). Итоги считаются автоматически. "
            + "Состав источников настраивается во вкладке «Счета и кассы» (направление выручки). "
            + "Внедоходовые — отдельный блок со свободным комментарием, не входят в выручку ОПТ/Розница/Склад.</div>";
    } else {
        html += "<h3>Сверка недели · Магазин Володарского</h3>";
        html += '<div class="fr-muted" style="margin-bottom:8px">'
            + "Ввод в ячейки и Ctrl+V, как в Excel. Счета — места хранения денег (вкладка «Счета и кассы»). "
            + "Внедоходовые поступления — отдельный блок со свободным комментарием, не настройка счёта.</div>";
    }
    html += '<div class="fr-week-open">Остаток на начало недели: <b>' + frMoney(weekOpen) + "</b></div>";

    html += '<div class="fr-table-wrap fr-reconcile-wrap"><table class="fr-table fr-reconcile-table" id="frReconcileTable">';
    html += "<thead><tr><th class=\"fr-src-col\">Источник</th>";
    dates.forEach(function (d) {
        html += '<th class="fr-num fr-day-col">' + escapeHtml(frShortDayHead(d)) + "</th>";
    });
    html += '<th class="fr-num fr-total-col">Итого</th></tr></thead><tbody>';

    let rowIdx = 0;
    const editableCells = [];

    blocks.forEach(function (block) {
        if ((block.opKind || "income") !== "income") return;
        const sources = frBlockSources(areaId, block);
        const opKind = "income";
        html += '<tr class="fr-block-head"><td colspan="' + (dates.length + 2) + '"><b>'
            + escapeHtml(block.label) + "</b></td></tr>";
        if (!sources.length) {
            html += '<tr class="fr-empty-src"><td colspan="' + (dates.length + 2) + '" class="fr-muted">';
            if (isTerritory) {
                html += "Нет счетов с направлением «" + escapeHtml(block.label)
                    + "». Назначьте направление во вкладке «Счета и кассы».";
            } else {
                html += "Нет счетов. Добавьте кассу, карту или расчётный счёт во вкладке «Счета и кассы».";
            }
            html += "</td></tr>";
        }
        sources.forEach(function (s) {
            const sid = s.finance_source_id;
            let rowSum = 0;
            html += '<tr class="fr-src-row" data-source-id="' + escapeAttribute(sid)
                + '" data-block="' + escapeAttribute(block.id)
                + '" data-op-kind="income">';
            html += "<td>" + escapeHtml(s.name || sid)
                + (s.is_active === false ? ' <span class="fr-muted">(неактивен)</span>' : "") + "</td>";
            dates.forEach(function (d, c) {
                const val = frGetGridCellAmount(areaId, sid, d, opKind);
                rowSum += val;
                if (canEdit) {
                    editableCells.push({ r: rowIdx, c: c, sourceId: sid, date: d, opKind: opKind });
                    html += '<td class="fr-num fr-cell-td">'
                        + '<input type="text" class="fr-rev-cell" inputmode="decimal" autocomplete="off"'
                        + ' data-r="' + rowIdx + '" data-c="' + c + '"'
                        + ' data-source-id="' + escapeAttribute(sid) + '"'
                        + ' data-date="' + escapeAttribute(d) + '"'
                        + ' data-block="' + escapeAttribute(block.id) + '"'
                        + ' data-op-kind="income"'
                        + ' value="' + escapeAttribute(frFmtCellAmount(val)) + '"'
                        + ' aria-label="' + escapeAttribute((s.name || sid) + " " + frShortDayHead(d)) + '">'
                        + "</td>";
                } else {
                    html += '<td class="fr-num">' + (val ? frMoney(val) : "—") + "</td>";
                }
            });
            html += '<td class="fr-num fr-row-total" data-source-id="' + escapeAttribute(sid)
                + '" data-op-kind="income"><b>'
                + frMoney(frRound(rowSum)) + "</b></td></tr>";
            rowIdx++;
        });
        html += '<tr class="fr-total-row" data-block-total="' + escapeAttribute(block.id) + '">';
        html += "<td><b>" + escapeHtml(block.totalLabel) + "</b></td>";
        let blockWeek = 0;
        dates.forEach(function (d) {
            const t = frBlockDayTotal(areaId, w.period_key, block, d);
            blockWeek += t;
            html += '<td class="fr-num fr-block-day" data-block="' + escapeAttribute(block.id)
                + '" data-date="' + escapeAttribute(d) + '"><b>' + frMoney(t) + "</b></td>";
        });
        html += '<td class="fr-num fr-block-week" data-block="' + escapeAttribute(block.id) + '"><b>'
            + frMoney(frRound(blockWeek)) + "</b></td></tr>";
    });

    html += '<tr class="fr-grand-row">';
    html += "<td><b>" + (isTerritory ? "Всего сдано" : "Итого доход") + "</b></td>";
    let grandWeek = 0;
    dates.forEach(function (d) {
        const t = frHandedInForDay(areaId, w.period_key, d);
        grandWeek += t;
        html += '<td class="fr-num fr-handed-day" data-date="' + escapeAttribute(d) + '"><b>'
            + frMoney(t) + "</b></td>";
    });
    html += '<td class="fr-num fr-handed-week"><b>' + frMoney(frRound(grandWeek)) + "</b></td></tr>";

    /* Расходы Касса / Банк / Всего — по дням из существующих операций */
    html += renderFrReconcileExpenseRows(areaId, w, dates);

    html += "</tbody></table></div>";
    html += '<div class="fr-muted" style="margin-top:6px">Итоговые строки нельзя редактировать. '
        + "Вставка Ctrl+V поддерживает одну сумму и диапазон ячеек. "
        + "Нажмите на сумму расходов, чтобы открыть детализацию.</div>";

    html += renderFrNonIncomeReconcileBlock(areaId, w, dates, canEdit);

    html += renderFrWeekActualBalances(areaId, w, canEdit);

    html += '<div class="fr-day-reconcile">';
    html += "<h3>Сверка дня</h3>";
    html += '<div class="fr-muted" style="margin-bottom:8px">'
        + "Остаток на начало + " + (isTerritory ? "сдано (ОПТ+Розница+Склад)" : "доход")
        + " + внедоходовые поступления − расходы ± перемещения "
        + "= расчётный остаток. Конец дня → начало следующего автоматически. "
        + "Внедоходовые — реальный приход денег, но не выручка.</div>";
    html += '<div class="fr-table-wrap"><table class="fr-table fr-day-agg-table"><thead><tr>';
    html += "<th>День</th><th class=\"fr-num\">Начало</th>"
        + '<th class="fr-num">' + (isTerritory ? "Сдано" : "Доход") + "</th>"
        + "<th class=\"fr-num\">Внедоход.</th><th class=\"fr-num\">Расходы</th>"
        + "<th class=\"fr-num\">Перемещения</th><th class=\"fr-num\">Конец (расчёт)</th><th></th>";
    html += "</tr></thead><tbody>";
    dates.forEach(function (d) {
        const agg = frAreaDayAggregate(areaId, w.period_key, d);
        html += "<tr>";
        html += "<td>" + escapeHtml(frShortDayHead(d)) + "</td>";
        html += '<td class="fr-num">' + frMoney(agg.opening) + "</td>";
        html += '<td class="fr-num fr-in">' + frMoney(agg.handed) + "</td>";
        html += '<td class="fr-num fr-non">' + frMoney(agg.non_income) + "</td>";
        html += '<td class="fr-num fr-out">'
            + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({date:\''
            + escapeAttribute(d) + "'})\">" + frMoney(agg.expense) + "</button></td>";
        html += '<td class="fr-num">' + frMoney(agg.transfer_net) + "</td>";
        html += '<td class="fr-num"><b>' + frMoney(agg.calculated) + "</b></td>";
        html += '<td><button type="button" class="btn btn-small btn-secondary" onclick="frShowExpenseDetail({date:\''
            + escapeAttribute(d) + "'})\">Детализация</button></td>";
        html += "</tr>";
    });
    html += "</tbody></table></div></div>";

    html += "</div>";
    frUi._reconcileCells = editableCells;
    return html;
}

function renderFrNonIncomeReconcileBlock(areaId, w, dates, canEdit) {
    const lines = frNonIncomeWeekLines(areaId, w.period_key, dates);
    const suggestions = frNonIncomeCommentSuggestions(areaId);
    const listId = "frNiSuggest_" + String(areaId || "").replace(/\W+/g, "_");
    const isTerritory = frIsTerritoryArea(areaId);
    let html = '<div class="fr-non-income-block" style="margin-top:14px">';
    html += "<h3>ВНЕДОХОДОВЫЕ ПОСТУПЛЕНИЯ</h3>";
    html += '<div class="fr-muted" style="margin-bottom:8px">'
        + "Отдельный тип операции: свободный комментарий (например: «Макулатура», «Возврат подотчёта») и сумма по дню. "
        + "Увеличивает остаток денег на счёте/кассе, но не является выручкой и не делает сам счёт «внедоходовым». ";
    if (isTerritory) {
        html += "Не входит в ОПТ / Розницу / Склад. ";
    }
    html += "Подсказки из ранее введённых комментариев необязательны.</div>";
    if (suggestions.length) {
        html += '<datalist id="' + escapeAttribute(listId) + '">';
        suggestions.forEach(function (s) {
            html += '<option value="' + escapeAttribute(s) + '">';
        });
        html += "</datalist>";
    }
    html += '<div class="fr-table-wrap fr-reconcile-wrap"><table class="fr-table fr-reconcile-table" id="frNonIncomeTable">';
    html += '<thead><tr><th class="fr-src-col">Комментарий</th>';
    dates.forEach(function (d) {
        html += '<th class="fr-num fr-day-col">' + escapeHtml(frShortDayHead(d)) + "</th>";
    });
    html += '<th class="fr-num fr-total-col">Итого</th></tr></thead><tbody>';

    let niRow = 0;
    function renderNiRow(line, isEmpty) {
        const key = isEmpty ? "new" : line.key;
        const comment = isEmpty ? "" : (line.comment || "");
        let rowSum = 0;
        let row = '<tr class="fr-ni-row" data-line-key="' + escapeAttribute(key) + '">';
        row += "<td>";
        if (canEdit) {
            row += '<input type="text" class="fr-ni-comment" placeholder="Комментарий"'
                + (suggestions.length ? ' list="' + escapeAttribute(listId) + '"' : "")
                + ' value="' + escapeAttribute(comment) + '"'
                + ' data-line-key="' + escapeAttribute(key) + '"'
                + ' autocomplete="off">';
        } else {
            row += escapeHtml(comment || "—");
        }
        row += "</td>";
        dates.forEach(function (d, c) {
            const val = isEmpty ? 0 : frNum(line.byDate[d] && line.byDate[d].amount);
            rowSum += val;
            if (canEdit) {
                row += '<td class="fr-num fr-cell-td">'
                    + '<input type="text" class="fr-ni-cell" inputmode="decimal" autocomplete="off"'
                    + ' data-ni-r="' + niRow + '" data-ni-c="' + c + '"'
                    + ' data-line-key="' + escapeAttribute(key) + '"'
                    + ' data-date="' + escapeAttribute(d) + '"'
                    + ' value="' + escapeAttribute(frFmtCellAmount(val)) + '">'
                    + "</td>";
            } else {
                row += '<td class="fr-num">' + (val ? frMoney(val) : "—") + "</td>";
            }
        });
        row += '<td class="fr-num fr-ni-row-total"><b>' + frMoney(frRound(rowSum)) + "</b></td></tr>";
        niRow++;
        return row;
    }

    lines.forEach(function (line) { html += renderNiRow(line, false); });
    if (canEdit) html += renderNiRow(null, true);

    html += '<tr class="fr-total-row fr-ni-total-row">';
    html += "<td><b>ИТОГО ВНЕДОХОДОВЫЕ</b></td>";
    let niWeek = 0;
    dates.forEach(function (d) {
        const t = frNonIncomeForDay(areaId, w.period_key, d);
        niWeek += t;
        html += '<td class="fr-num fr-ni-day" data-date="' + escapeAttribute(d) + '"><b>'
            + frMoney(t) + "</b></td>";
    });
    html += '<td class="fr-num fr-ni-week"><b>' + frMoney(frRound(niWeek)) + "</b></td></tr>";
    html += "</tbody></table></div></div>";
    return html;
}

/**
 * Фактические остатки на КОНЕЦ НЕДЕЛИ (среда).
 * Только счета с «Участвует в фактическом остатке».
 * Расчётный — автоматически; фактический — только ручной ввод (не подставляем).
 */
function renderFrWeekActualBalances(areaId, w, canEdit) {
    const endDate = w.end_date;
    const sources = frActualBalanceSources(areaId);
    const isTerritory = frIsTerritoryArea(areaId);
    const primaryId = frPrimaryCashId(areaId);
    const mainCalc = primaryId ? frMainCashWeekCalc(areaId, w.period_key) : null;
    const calcOpen = !!(frUi.mainCashCalcOpen && frUi.mainCashCalcOpen[areaId]);
    const incomeOpen = !!(frUi.mainCashIncomeOpen && frUi.mainCashIncomeOpen[areaId]);
    const colCount = isTerritory ? 5 : 4;

    let html = '<div class="fr-box fr-week-actual-box" style="margin-top:14px">';
    html += "<h3>ФАКТИЧЕСКИЕ ОСТАТКИ НА КОНЕЦ НЕДЕЛИ</h3>";
    html += '<div class="fr-muted" style="margin-bottom:8px">'
        + "Остатки на конец среды (" + escapeHtml(frFmtDate(endDate)) + "). "
        + "Показаны только счета/кассы с настройкой «Участвует в фактическом остатке». "
        + "Контрольные ежедневные кассы (без галочки) остаются в таблице поступлений, но сюда не входят. "
        + "Фактический остаток сотрудник вводит вручную — расчётный <b>не подставляется</b>. "
        + "Расчётный остаток Основной кассы = начало + все наличные поступления объекта + наличный внедоход − наличные расходы "
        + "(без двойного учёта и без безнала).</div>";

    if (!sources.length) {
        html += '<div class="note">Нет счетов с включённым участием в фактическом остатке. '
            + "Администратор настраивает это во вкладке «Счета и кассы».</div></div>";
        return html;
    }

    html += '<div class="fr-table-wrap"><table class="fr-table fr-week-actual-table"><thead><tr>';
    html += "<th>Счёт / касса</th>";
    if (isTerritory) html += "<th>Направление</th>";
    html += '<th class="fr-num">Расчётный остаток</th>'
        + '<th class="fr-num">Фактический остаток</th>'
        + '<th class="fr-num">Расхождение</th></tr></thead><tbody>';

    let sumCalc = 0;
    let sumActual = 0;
    let sumDisc = 0;
    let allFilled = true;
    let anyFilled = false;

    sources.forEach(function (s) {
        const sid = s.finance_source_id;
        const isPrimary = primaryId && sid === primaryId;
        const isTransit = frIsTransitSource(s);
        const calc = frWeekEndCalculatedForAccount(areaId, w.period_key, endDate, sid);
        /* Деньги в пути: один источник — расчёт по перемещениям, без повторного ручного ввода. */
        const actual = isTransit ? calc : frDayActual(areaId, endDate, sid);
        sumCalc += calc;
        html += '<tr data-account-id="' + escapeAttribute(sid) + '">';
        html += "<td>" + escapeHtml(s.name)
            + (isPrimary ? ' <span class="fr-badge fr-badge-ok">осн. касса</span>' : "")
            + (isTransit ? ' <span class="fr-badge fr-badge-wait">в пути</span>' : "")
            + "</td>";
        if (isTerritory) {
            html += "<td>" + escapeHtml(frRevenueDirLabel(s.revenue_direction || "none")) + "</td>";
        }
        html += '<td class="fr-num">' + frMoney(calc);
        if (isPrimary && mainCalc) {
            html += '<div class="fr-main-cash-toggle">'
                + '<button type="button" class="fr-link-btn" onclick="frToggleMainCashCalc(\''
                + escapeAttribute(areaId) + '\')">'
                + (calcOpen ? "Скрыть расчёт" : "Показать расчёт")
                + "</button></div>";
        }
        html += "</td>";
        html += '<td class="fr-num">';
        if (isTransit) {
            html += frMoney(calc)
                + '<div class="fr-muted" style="font-size:11px">по незакрытым перемещениям</div>';
            anyFilled = true;
            const disc = 0;
            sumActual += calc;
            sumDisc += disc;
            html += "</td>";
            html += '<td class="fr-num fr-disc-cell is-ok">' + frMoney(0) + "</td>";
        } else if (canEdit) {
            html += '<input type="text" class="fr-week-actual-inp" inputmode="decimal" autocomplete="off"'
                + ' style="width:130px;text-align:right"'
                + ' data-account-id="' + escapeAttribute(sid) + '"'
                + ' data-date="' + escapeAttribute(endDate) + '"'
                + ' placeholder="введите факт"'
                + ' value="' + escapeAttribute(actual == null ? "" : String(actual).replace(".", ",")) + '"'
                + " onchange=\"frSaveWeekActualBalance('" + escapeAttribute(sid) + "',this.value)\">";
            html += "</td>";
            if (actual == null) {
                allFilled = false;
                html += '<td class="fr-num fr-disc-cell">—</td>';
            } else {
                anyFilled = true;
                const disc = frRound(actual - calc);
                sumActual += actual;
                sumDisc += disc;
                const cls = Math.abs(disc) < 0.005 ? "is-ok" : "is-bad";
                html += '<td class="fr-num fr-disc-cell ' + cls + '">' + frMoney(disc) + "</td>";
            }
        } else {
            html += actual == null ? "—" : frMoney(actual);
            html += "</td>";
            if (actual == null) {
                allFilled = false;
                html += '<td class="fr-num fr-disc-cell">—</td>';
            } else {
                anyFilled = true;
                const disc = frRound(actual - calc);
                sumActual += actual;
                sumDisc += disc;
                const cls = Math.abs(disc) < 0.005 ? "is-ok" : "is-bad";
                html += '<td class="fr-num fr-disc-cell ' + cls + '">' + frMoney(disc) + "</td>";
            }
        }
        html += "</tr>";

        if (isPrimary && mainCalc && calcOpen) {
            html += '<tr class="fr-main-cash-detail"><td colspan="' + colCount + '">';
            html += '<div class="fr-main-cash-calc">';
            html += "<div><b>Расчёт Основной кассы</b></div>";
            html += '<div class="fr-main-cash-line"><span>Остаток на начало недели</span>'
                + '<span class="fr-num">' + frMoney(mainCalc.opening) + "</span></div>";
            html += '<div class="fr-main-cash-line">'
                + '<span>+ Наличные поступления '
                + '<button type="button" class="fr-link-btn" onclick="frToggleMainCashIncome(\''
                + escapeAttribute(areaId) + '\')">'
                + (incomeOpen ? "▲" : "▼")
                + "</button></span>"
                + '<span class="fr-num">' + frMoney(mainCalc.cashIncome) + "</span></div>";
            if (incomeOpen) {
                html += '<div class="fr-main-cash-income-break">';
                if (!mainCalc.incomeBySource.length) {
                    html += '<div class="fr-muted">Нет наличных поступлений за неделю</div>';
                } else {
                    mainCalc.incomeBySource.forEach(function (row) {
                        html += '<div class="fr-main-cash-line fr-main-cash-sub">'
                            + "<span>" + escapeHtml(row.name) + "</span>"
                            + '<span class="fr-num">' + frMoney(row.amount) + "</span></div>";
                    });
                    html += '<div class="fr-main-cash-line fr-main-cash-sub"><span><b>Итого</b></span>'
                        + '<span class="fr-num"><b>' + frMoney(mainCalc.cashIncome) + "</b></span></div>";
                }
                html += "</div>";
            }
            html += '<div class="fr-main-cash-line"><span>+ Внедоходовые поступления наличными</span>'
                + '<span class="fr-num">' + frMoney(mainCalc.cashNonIncome) + "</span></div>";
            html += '<div class="fr-main-cash-line"><span>− Наличные расходы</span>'
                + '<span class="fr-num">' + frMoney(mainCalc.cashExpense) + "</span></div>";
            if (Math.abs(mainCalc.cashDividend || 0) >= 0.005) {
                html += '<div class="fr-main-cash-line"><span>− Дивиденды</span>'
                    + '<span class="fr-num">' + frMoney(mainCalc.cashDividend) + "</span></div>";
            }
            if (Math.abs(mainCalc.cashTransferNetOut) >= 0.005) {
                html += '<div class="fr-main-cash-line"><span>'
                    + (mainCalc.cashTransferNetOut >= 0 ? "−" : "+")
                    + " Перемещения наличность ↔ безнал / в пути</span>"
                    + '<span class="fr-num">' + frMoney(Math.abs(mainCalc.cashTransferNetOut)) + "</span></div>";
            }
            html += '<div class="fr-main-cash-line fr-main-cash-total"><span><b>Расчётный остаток Основной кассы</b></span>'
                + '<span class="fr-num"><b>' + frMoney(mainCalc.calculated) + "</b></span></div>";
            html += "</div></td></tr>";
        }
    });

    sumCalc = frRound(sumCalc);
    sumActual = frRound(sumActual);
    sumDisc = frRound(sumDisc);

    const colPad = isTerritory ? 2 : 1;
    html += "</tbody><tfoot>";
    html += "<tr><td colspan=\"" + colPad + "\"><b>ИТОГО РАСЧЁТНЫЙ ОСТАТОК</b></td>"
        + '<td class="fr-num"><b>' + frMoney(sumCalc) + "</b></td><td></td><td></td></tr>";
    html += "<tr><td colspan=\"" + colPad + "\"><b>ИТОГО ФАКТИЧЕСКИЙ ОСТАТОК</b></td><td></td>"
        + '<td class="fr-num"><b>' + (anyFilled ? frMoney(sumActual) : "—") + "</b></td><td></td></tr>";
    html += "<tr><td colspan=\"" + colPad + "\"><b>ОБЩЕЕ РАСХОЖДЕНИЕ</b></td><td></td><td></td>";
    if (!allFilled) {
        html += '<td class="fr-num fr-disc-cell">—</td></tr>';
        html += '<tr><td colspan="' + (colPad + 3) + '" class="fr-muted">Заполните фактические остатки по всем счетам из списка, чтобы проверить сходимость недели.</td></tr>';
    } else if (Math.abs(sumDisc) < 0.005) {
        html += '<td class="fr-num fr-disc-cell is-ok"><b>✓ Неделя сошлась</b></td></tr>';
    } else {
        html += '<td class="fr-num fr-disc-cell is-bad"><b>' + frMoney(sumDisc) + "</b></td></tr>";
    }
    html += "</tfoot></table></div></div>";
    return html;
}

/** Сохранение фактического остатка на конец среды выбранной недели. */
function frSaveWeekActualBalance(sourceId, value) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    if (!area || !sourceId) return;
    const w = frEnsureWeek();
    const endDate = w.end_date;
    ensureFinanceRec();
    let row = frDayClosingRow(area.finance_area_id, endDate, sourceId);
    const raw = String(value == null ? "" : value).trim();
    if (!row) {
        row = {
            finance_day_closing_id: nextPrefixedId("fdc", (appData.financeRec.day_closings || []).map(function (x) { return x && x.finance_day_closing_id; })),
            finance_area_id: area.finance_area_id,
            object_id: area.object_id || area.finance_area_id,
            period_key: w.period_key,
            date: endDate,
            finance_source_id: sourceId,
            account_id: sourceId,
            data_source: FR_DATA_SOURCES.manual
        };
        frNormalizeBalanceRow(row);
        appData.financeRec.day_closings.push(row);
    }
    /* Сохранять факт только для счетов, участвующих в фактическом остатке */
    const src = frSourceByAccountId(area.finance_area_id, sourceId);
    if (src && !frInActualBalance(src)) {
        toast("Этот счёт не участвует в фактическом остатке", "error");
        renderFinanceRec();
        return;
    }
    row.period_key = w.period_key;
    row.date = endDate;
    row.object_id = area.object_id || area.finance_area_id;
    row.finance_area_id = row.object_id;
    row.account_id = sourceId;
    row.finance_source_id = sourceId;
    if (raw === "") {
        row.actual_balance = null;
        row.actual_closing = null;
    } else {
        const num = frNum(raw);
        row.actual_balance = num;
        row.actual_closing = num;
    }
    row.updated_at = new Date().toISOString();
    frNormalizeBalanceRow(row);
    const calc = frWeekEndCalculatedForAccount(area.finance_area_id, w.period_key, endDate, sourceId);
    frAudit("week_actual_balance", {
        finance_source_id: sourceId,
        account_id: sourceId,
        detail: endDate + " " + w.period_key,
        new_value: row.actual_balance,
        old_value: calc
    });
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, 1) : null;
    if (next) frCarryIfNeeded(area.finance_area_id, next.period_key);
    saveApp();
    renderFinanceRec();
}

function frBindReconcileGrid() {
    const table = document.getElementById("frReconcileTable");
    if (table) {
        table.querySelectorAll(".fr-rev-cell").forEach(function (inp) {
            inp.addEventListener("focus", function () {
                try { inp.select(); } catch (err) {}
            });
            inp.addEventListener("keydown", function (e) {
                if (e.key === "Enter") {
                    e.preventDefault();
                    frCommitRevenueCell(inp, FR_DATA_SOURCES.manual);
                    frFocusReconcileNeighbor(inp, e.shiftKey ? -1 : 1, 0);
                } else if (e.key === "ArrowDown" && !e.altKey) {
                    e.preventDefault();
                    frCommitRevenueCell(inp, FR_DATA_SOURCES.manual);
                    frFocusReconcileNeighbor(inp, 1, 0);
                } else if (e.key === "ArrowUp" && !e.altKey) {
                    e.preventDefault();
                    frCommitRevenueCell(inp, FR_DATA_SOURCES.manual);
                    frFocusReconcileNeighbor(inp, -1, 0);
                }
            });
            inp.addEventListener("blur", function () {
                if (frUi._reconcilePasteLock) return;
                if (frCommitRevenueCell(inp, FR_DATA_SOURCES.manual)) {
                    frRefreshReconcileTotalsDom();
                    frRefreshDayAggDom();
                } else {
                    frRefreshReconcileTotalsDom();
                }
            });
            inp.addEventListener("paste", function (e) {
                frOnReconcilePaste(e, inp);
            });
        });
    }
    frBindNonIncomeGrid();
}

function frBindNonIncomeGrid() {
    const table = document.getElementById("frNonIncomeTable");
    if (!table) return;
    table.querySelectorAll(".fr-ni-comment").forEach(function (inp) {
        inp.addEventListener("blur", function () {
            if (frUi._reconcilePasteLock) return;
            frCommitNonIncomeComment(inp);
        });
        inp.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                frCommitNonIncomeComment(inp);
                const tr = inp.closest("tr");
                const firstAmt = tr && tr.querySelector(".fr-ni-cell");
                if (firstAmt) { firstAmt.focus(); try { firstAmt.select(); } catch (err) {} }
            }
        });
    });
    table.querySelectorAll(".fr-ni-cell").forEach(function (inp) {
        inp.addEventListener("focus", function () {
            try { inp.select(); } catch (err) {}
        });
        inp.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                const needRender = frCommitNonIncomeCell(inp, FR_DATA_SOURCES.manual);
                if (needRender === "render") {
                    const r = inp.getAttribute("data-ni-r");
                    const c = inp.getAttribute("data-ni-c");
                    renderFinanceRec();
                    setTimeout(function () {
                        const el = document.querySelector('.fr-ni-cell[data-ni-r="' + r + '"][data-ni-c="' + c + '"]');
                        if (el) { el.focus(); try { el.select(); } catch (err) {} }
                    }, 0);
                    return;
                }
                frFocusNonIncomeNeighbor(inp, e.shiftKey ? -1 : 1, 0);
            }
        });
        inp.addEventListener("blur", function () {
            if (frUi._reconcilePasteLock) return;
            const res = frCommitNonIncomeCell(inp, FR_DATA_SOURCES.manual);
            if (res === "render") {
                renderFinanceRec();
                return;
            }
            frRefreshNonIncomeTotalsDom();
            frRefreshDayAggDom();
        });
        inp.addEventListener("paste", function (e) {
            frOnNonIncomePaste(e, inp);
        });
    });
}

function frFocusNonIncomeNeighbor(inp, dRow, dCol) {
    const r = Number(inp.getAttribute("data-ni-r")) + dRow;
    const c = Number(inp.getAttribute("data-ni-c")) + dCol;
    const next = document.querySelector('.fr-ni-cell[data-ni-r="' + r + '"][data-ni-c="' + c + '"]');
    if (next) {
        next.focus();
        try { next.select(); } catch (err) {}
    }
}

function frCommitNonIncomeComment(inp) {
    if (!inp || !frCanEditData()) return false;
    const area = frActiveArea();
    if (!area) return false;
    const tr = inp.closest("tr");
    if (!tr) return false;
    let lineKey = tr.getAttribute("data-line-key") || inp.getAttribute("data-line-key") || "";
    const comment = String(inp.value || "").replace(/\s+/g, " ").trim();
    inp.value = comment;
    if (lineKey === "new" || !lineKey) {
        /* комментарий для новой строки сохранится при вводе суммы */
        return false;
    }
    const w = frEnsureWeek();
    const dates = frWeekDates(w);
    const newKey = frRenameNonIncomeLine(area.finance_area_id, w.period_key, dates, lineKey, comment);
    if (newKey && newKey !== lineKey) {
        tr.setAttribute("data-line-key", newKey);
        tr.querySelectorAll("[data-line-key]").forEach(function (el) {
            el.setAttribute("data-line-key", newKey);
        });
    }
    saveApp();
    return true;
}

function frCommitNonIncomeCell(inp, dataSource) {
    if (!inp || !frCanEditData()) return false;
    const area = frActiveArea();
    if (!area) return false;
    const tr = inp.closest("tr");
    if (!tr) return false;
    const commentInp = tr.querySelector(".fr-ni-comment");
    const comment = String((commentInp && commentInp.value) || "").replace(/\s+/g, " ").trim();
    let lineKey = tr.getAttribute("data-line-key") || "new";
    const date = inp.getAttribute("data-date");
    if (!date) return false;
    const raw = String(inp.value || "").trim();
    const amt = raw === "" ? 0 : frNum(raw);
    if (amt > 0 && !comment) {
        toast("Сначала укажите комментарий", "error");
        inp.value = "";
        if (commentInp) commentInp.focus();
        return false;
    }
    const w = frEnsureWeek();
    const dates = frWeekDates(w);
    const lines = frNonIncomeWeekLines(area.finance_area_id, w.period_key, dates);
    const prevLine = lines.find(function (l) { return l.key === lineKey; });
    const prev = prevLine && prevLine.byDate[date] ? frNum(prevLine.byDate[date].amount) : 0;
    if (Math.abs(prev - amt) < 0.005 && lineKey !== "new") {
        inp.value = frFmtCellAmount(amt);
        return false;
    }
    const wasNew = lineKey === "new";
    const result = frSetNonIncomeCell(
        area.finance_area_id,
        lineKey === "new" ? "" : lineKey,
        comment,
        date,
        amt,
        dataSource || FR_DATA_SOURCES.manual
    );
    if (result && result.lineKey) {
        tr.setAttribute("data-line-key", result.lineKey);
        tr.querySelectorAll("[data-line-key]").forEach(function (el) {
            el.setAttribute("data-line-key", result.lineKey);
        });
        lineKey = result.lineKey;
    }
    inp.value = frFmtCellAmount(amt);
    frAudit("non_income_grid", {
        detail: comment + " " + date,
        old_value: prev,
        new_value: amt
    });
    saveApp();
    /* после первой суммы в новой строке — перерисовать, чтобы появилась пустая строка */
    if (wasNew && amt > 0) return "render";
    /* если опустошили все ячейки строки — тоже можно перерисовать, но обновления итогов достаточно */
    frRefreshNonIncomeTotalsDom();
    return true;
}

function frOnNonIncomePaste(e, inp) {
    if (!e || !inp || !frCanEditData()) return;
    const clip = e.clipboardData || window.clipboardData;
    if (!clip) return;
    const text = clip.getData("text") || clip.getData("text/plain") || "";
    if (!String(text).trim()) return;
    const matrix = frParsePasteMatrix(text);
    e.preventDefault();
    frUi._reconcilePasteLock = true;
    const startR = Number(inp.getAttribute("data-ni-r"));
    const startC = Number(inp.getAttribute("data-ni-c"));
    let needRender = false;
    matrix.forEach(function (row, ri) {
        row.forEach(function (cell, ci) {
            const target = document.querySelector(
                '.fr-ni-cell[data-ni-r="' + (startR + ri) + '"][data-ni-c="' + (startC + ci) + '"]'
            );
            if (!target) return;
            target.value = frFmtCellAmount(frNum(cell));
            const res = frCommitNonIncomeCell(target, FR_DATA_SOURCES.paste);
            if (res === "render") needRender = true;
        });
    });
    frUi._reconcilePasteLock = false;
    if (needRender) {
        renderFinanceRec();
        return;
    }
    frRefreshNonIncomeTotalsDom();
    frRefreshDayAggDom();
}

function frRefreshNonIncomeTotalsDom() {
    const area = frActiveArea();
    if (!area) return;
    const w = frEnsureWeek();
    const dates = frWeekDates(w);
    const areaId = area.finance_area_id;
    let week = 0;
    dates.forEach(function (d) {
        const t = frNonIncomeForDay(areaId, w.period_key, d);
        week += t;
        const el = document.querySelector('.fr-ni-day[data-date="' + d + '"]');
        if (el) el.innerHTML = "<b>" + frMoney(t) + "</b>";
    });
    const gw = document.querySelector(".fr-ni-week");
    if (gw) gw.innerHTML = "<b>" + frMoney(frRound(week)) + "</b>";
    document.querySelectorAll("#frNonIncomeTable tr.fr-ni-row").forEach(function (tr) {
        let sum = 0;
        tr.querySelectorAll(".fr-ni-cell").forEach(function (inp) {
            sum += frNum(inp.value);
        });
        const tot = tr.querySelector(".fr-ni-row-total");
        if (tot) tot.innerHTML = "<b>" + frMoney(frRound(sum)) + "</b>";
    });
}

function frFocusReconcileNeighbor(inp, dRow, dCol) {
    const r = Number(inp.getAttribute("data-r")) + dRow;
    const c = Number(inp.getAttribute("data-c")) + dCol;
    const next = document.querySelector('.fr-rev-cell[data-r="' + r + '"][data-c="' + c + '"]');
    if (next) {
        next.focus();
        try { next.select(); } catch (err) {}
    }
}

function frCommitRevenueCell(inp, dataSource) {
    if (!inp || !frCanEditData()) return false;
    const area = frActiveArea();
    if (!area) return false;
    if (!frIsTerritoryArea(area) && !frIsShopArea(area)) return false;
    const sourceId = inp.getAttribute("data-source-id");
    const date = inp.getAttribute("data-date");
    const opKind = inp.getAttribute("data-op-kind") === "non_income" ? "non_income" : "income";
    if (!sourceId || !date) return false;
    const raw = String(inp.value || "").trim();
    const amt = raw === "" ? 0 : frNum(raw);
    const prev = frGetGridCellAmount(area.finance_area_id, sourceId, date, opKind);
    if (Math.abs(prev - amt) < 0.005) {
        inp.value = frFmtCellAmount(amt);
        return false;
    }
    frSetGridCell(area.finance_area_id, sourceId, date, amt, dataSource || FR_DATA_SOURCES.manual, opKind);
    inp.value = frFmtCellAmount(amt);
    frAudit("revenue_grid", {
        finance_source_id: sourceId,
        detail: date + " " + opKind,
        old_value: prev,
        new_value: amt
    });
    saveApp();
    return true;
}

function frParsePasteMatrix(text) {
    const rows = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    while (rows.length && !String(rows[rows.length - 1]).trim()) rows.pop();
    return rows.map(function (line) {
        if (line.indexOf("\t") !== -1) return line.split("\t");
        if (line.indexOf(";") !== -1 && line.indexOf(",") === -1) return line.split(";");
        return [line];
    });
}

function frOnReconcilePaste(e, inp) {
    if (!e || !inp || !frCanEditData()) return;
    const clip = e.clipboardData || window.clipboardData;
    if (!clip) return;
    const text = clip.getData("text") || clip.getData("text/plain") || "";
    if (!String(text).trim()) return;
    const matrix = frParsePasteMatrix(text);
    const flatOne = matrix.length === 1 && matrix[0].length === 1;
    if (flatOne) {
        e.preventDefault();
        const amt = frNum(matrix[0][0]);
        inp.value = frFmtCellAmount(amt);
        frCommitRevenueCell(inp, FR_DATA_SOURCES.paste);
        frRefreshReconcileTotalsDom();
        frRefreshDayAggDom();
        return;
    }
    e.preventDefault();
    e.stopPropagation();
    frUi._reconcilePasteLock = true;
    const startR = Number(inp.getAttribute("data-r"));
    const startC = Number(inp.getAttribute("data-c"));
    let changed = 0;
    matrix.forEach(function (row, ri) {
        row.forEach(function (cell, ci) {
            const target = document.querySelector(
                '.fr-rev-cell[data-r="' + (startR + ri) + '"][data-c="' + (startC + ci) + '"]'
            );
            if (!target) return;
            const amt = frNum(cell);
            target.value = frFmtCellAmount(amt);
            if (frCommitRevenueCell(target, FR_DATA_SOURCES.paste)) changed++;
        });
    });
    frUi._reconcilePasteLock = false;
    frRefreshReconcileTotalsDom();
    if (changed > 0) {
        const focusR = startR + matrix.length - 1;
        const focusC = startC + (matrix[matrix.length - 1] || []).length - 1;
        renderFinanceRec();
        setTimeout(function () {
            const el = document.querySelector('.fr-rev-cell[data-r="' + focusR + '"][data-c="' + focusC + '"]');
            if (el) { el.focus(); try { el.select(); } catch (err) {} }
        }, 0);
    }
}

function frRefreshReconcileTotalsDom() {
    const area = frActiveArea();
    if (!area || (!frIsTerritoryArea(area) && !frIsShopArea(area))) return;
    const w = frEnsureWeek();
    const dates = frWeekDates(w);
    const areaId = area.finance_area_id;
    const blocks = frReconcileBlocks(areaId);

    blocks.forEach(function (block) {
        let blockWeek = 0;
        dates.forEach(function (d) {
            const t = frBlockDayTotal(areaId, w.period_key, block, d);
            blockWeek += t;
            const el = document.querySelector('.fr-block-day[data-block="' + block.id + '"][data-date="' + d + '"]');
            if (el) el.innerHTML = "<b>" + frMoney(t) + "</b>";
        });
        const bw = document.querySelector('.fr-block-week[data-block="' + block.id + '"]');
        if (bw) bw.innerHTML = "<b>" + frMoney(frRound(blockWeek)) + "</b>";
    });

    let grandWeek = 0;
    dates.forEach(function (d) {
        const t = frHandedInForDay(areaId, w.period_key, d);
        grandWeek += t;
        const el = document.querySelector('.fr-handed-day[data-date="' + d + '"]');
        if (el) el.innerHTML = "<b>" + frMoney(t) + "</b>";
    });
    const gw = document.querySelector(".fr-handed-week");
    if (gw) gw.innerHTML = "<b>" + frMoney(frRound(grandWeek)) + "</b>";

    document.querySelectorAll(".fr-src-row").forEach(function (tr) {
        const sid = tr.getAttribute("data-source-id");
        const opKind = tr.getAttribute("data-op-kind") === "non_income" ? "non_income" : "income";
        if (!sid) return;
        let sum = 0;
        dates.forEach(function (d) { sum += frGetGridCellAmount(areaId, sid, d, opKind); });
        const cell = tr.querySelector(".fr-row-total");
        if (cell) cell.innerHTML = "<b>" + frMoney(frRound(sum)) + "</b>";
    });
    frRefreshNonIncomeTotalsDom();
}

function frRefreshDayAggDom() {
    const area = frActiveArea();
    if (!area || (!frIsTerritoryArea(area) && !frIsShopArea(area))) return;
    const w = frEnsureWeek();
    const table = document.querySelector(".fr-day-agg-table tbody");
    if (!table) return;
    const dates = frWeekDates(w);
    const rows = table.querySelectorAll("tr");
    dates.forEach(function (d, i) {
        const tr = rows[i];
        if (!tr) return;
        const agg = frAreaDayAggregate(area.finance_area_id, w.period_key, d);
        const cells = tr.querySelectorAll("td");
        if (cells.length < 7) return;
        cells[1].innerHTML = frMoney(agg.opening);
        cells[2].innerHTML = frMoney(agg.handed);
        cells[3].innerHTML = frMoney(agg.non_income);
        cells[4].innerHTML = '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({date:\''
            + d + "'})\">" + frMoney(agg.expense) + "</button>";
        cells[5].innerHTML = frMoney(agg.transfer_net);
        cells[6].innerHTML = "<b>" + frMoney(agg.calculated) + "</b>";
    });
}

/**
 * Строки расходов в таблице сверки недели (Касса / Банк / Всего).
 * Суммы кликабельны → детализация.
 */
function renderFrReconcileExpenseRows(areaId, w, dates) {
    const channels = [
        { id: "cash", label: "Расходы Касса" },
        { id: "bank", label: "Расходы Банк" },
        { id: "all", label: "Всего расходов" }
    ];
    let html = '<tr class="fr-block-head fr-expense-head"><td colspan="' + (dates.length + 2)
        + '"><b>РАСХОДЫ</b> <span class="fr-muted">(по типу счёта списания: касса / банк·карта)</span></td></tr>';
    channels.forEach(function (ch) {
        const rowCls = ch.id === "all" ? "fr-total-row fr-expense-total-row" : "fr-expense-row";
        html += '<tr class="' + rowCls + '" data-expense-channel="' + ch.id + '">';
        html += "<td>" + (ch.id === "all" ? "<b>" + escapeHtml(ch.label) + "</b>" : escapeHtml(ch.label)) + "</td>";
        let weekSum = 0;
        dates.forEach(function (d) {
            const t = frExpenseSumForDay(areaId, w.period_key, d, ch.id);
            weekSum += t;
            html += '<td class="fr-num fr-out fr-expense-day" data-channel="' + escapeAttribute(ch.id)
                + '" data-date="' + escapeAttribute(d) + '">';
            if (t) {
                html += '<button type="button" class="fr-exp-link" title="Детализация"'
                    + " onclick=\"frShowExpenseDetail({channel:'" + ch.id + "',date:'"
                    + escapeAttribute(d) + "'})\">" + frMoney(t) + "</button>";
            } else {
                html += "—";
            }
            html += "</td>";
        });
        weekSum = frRound(weekSum);
        html += '<td class="fr-num fr-expense-week" data-channel="' + escapeAttribute(ch.id) + '">';
        if (weekSum) {
            html += '<button type="button" class="fr-exp-link" title="Детализация за неделю"'
                + " onclick=\"frShowExpenseDetail({channel:'" + ch.id + "'})\"><b>"
                + frMoney(weekSum) + "</b></button>";
        } else {
            html += "<b>—</b>";
        }
        html += "</td></tr>";
    });
    return html;
}

function frFmtExpenseDetailTitle(opts) {
    opts = opts || {};
    const parts = ["Расходы"];
    if (opts.channel && opts.channel !== "all") parts.push(frExpenseChannelLabel(opts.channel));
    if (opts.accountId) {
        const src = frSourceByAccountId(frActiveArea() && frActiveArea().finance_area_id, opts.accountId);
        parts.push((src && src.name) || opts.accountId);
    }
    if (opts.date) parts.push(frFmtDate(opts.date) || opts.date);
    else parts.push("за неделю");
    return parts.join(" · ");
}

/**
 * Детализация расходов: РАСХОДЫ → Касса/Банк → счёт → операции.
 * opts: { channel: 'cash'|'bank'|'all', date?: iso, accountId?: string }
 */
function frShowExpenseDetail(opts) {
    opts = opts || {};
    const area = frActiveArea();
    if (!area) return;
    const areaId = area.finance_area_id;
    const w = frEnsureWeek();
    const channel = opts.channel || "all";
    const date = frNormDate(opts.date) || "";
    const accountId = opts.accountId || "";

    let html = "<h3>" + escapeHtml(frFmtExpenseDetailTitle({
        channel: channel === "all" && !accountId ? "" : channel,
        accountId: accountId,
        date: date
    })) + "</h3>";

    /* Хлебные крошки / назад */
    html += '<div class="fr-muted" style="margin-bottom:8px">';
    html += '<button type="button" class="btn btn-small btn-secondary" onclick="frShowExpenseDetail({})">Расходы</button>';
    if (channel && channel !== "all") {
        html += " → ";
        html += '<button type="button" class="btn btn-small btn-secondary" onclick="frShowExpenseDetail({channel:\''
            + channel + "'" + (date ? ",date:'" + escapeAttribute(date) + "'" : "") + "})\">"
            + escapeHtml(frExpenseChannelLabel(channel)) + "</button>";
    }
    if (accountId) {
        const src = frSourceByAccountId(areaId, accountId);
        html += " → <b>" + escapeHtml((src && src.name) || accountId) + "</b>";
    }
    if (date) html += " · " + escapeHtml(frFmtDate(date));
    else html += " · неделя";
    html += "</div>";

    /* Уровень 1: каналы */
    if (!accountId && (!channel || channel === "all")) {
        const cash = date
            ? frExpenseSumForDay(areaId, w.period_key, date, "cash")
            : frExpenseSumForWeek(areaId, w.period_key, "cash");
        const bank = date
            ? frExpenseSumForDay(areaId, w.period_key, date, "bank")
            : frExpenseSumForWeek(areaId, w.period_key, "bank");
        const all = frRound(cash + bank);
        html += '<div class="fr-muted" style="margin-bottom:8px">Касса или банк определяется автоматически по типу счёта списания.</div>';
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>'
            + "<th>Блок</th><th class=\"fr-num\">Сумма</th><th></th></tr></thead><tbody>";
        [
            { id: "cash", label: "Расходы КАССА", sum: cash },
            { id: "bank", label: "Расходы БАНК", sum: bank }
        ].forEach(function (row) {
            html += "<tr><td><b>" + escapeHtml(row.label) + "</b></td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.sum) + "</td><td>";
            html += '<button type="button" class="btn btn-small" onclick="frShowExpenseDetail({channel:\''
                + row.id + "'" + (date ? ",date:'" + escapeAttribute(date) + "'" : "") + "})\">Открыть</button>";
            html += "</td></tr>";
        });
        html += '<tr class="fr-total-row"><td><b>Всего расходов</b></td><td class="fr-num"><b>'
            + frMoney(all) + "</b></td><td></td></tr>";
        html += "</tbody></table></div>";
        html += '<div class="toolbar" style="margin-top:8px">'
            + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Закрыть</button></div>';
        openMgmtModal(html);
        return;
    }

    /* Уровень 2: счета внутри канала */
    if (!accountId && channel && channel !== "all") {
        const groups = date
            ? frExpenseOpsGroupedByAccount(areaId, w.period_key, date, channel)
            : (function () {
                const byAcc = {};
                frWeekDates(w).forEach(function (d) {
                    frExpenseOpsGroupedByAccount(areaId, w.period_key, d, channel).forEach(function (g) {
                        if (!byAcc[g.account_id]) {
                            byAcc[g.account_id] = { account_id: g.account_id, ops: [], sum: 0 };
                        }
                        byAcc[g.account_id].ops = byAcc[g.account_id].ops.concat(g.ops);
                        byAcc[g.account_id].sum += g.sum;
                    });
                });
                return Object.keys(byAcc).map(function (k) {
                    byAcc[k].sum = frRound(byAcc[k].sum);
                    return byAcc[k];
                }).sort(function (a, b) {
                    const sa = frSourceByAccountId(areaId, a.account_id);
                    const sb = frSourceByAccountId(areaId, b.account_id);
                    return Number((sa && sa.sort_order) || 0) - Number((sb && sb.sort_order) || 0);
                });
            })();
        let sum = 0;
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>'
            + "<th>Счёт / касса</th><th>Тип</th><th class=\"fr-num\">Сумма</th><th></th></tr></thead><tbody>";
        if (!groups.length) {
            html += '<tr><td colspan="4"><div class="empty-row">Нет расходов</div></td></tr>';
        }
        groups.forEach(function (g) {
            sum += g.sum;
            const src = frSourceByAccountId(areaId, g.account_id);
            html += "<tr><td>" + escapeHtml((src && src.name) || g.account_id || "—") + "</td>";
            html += "<td>" + escapeHtml(frAccountTypeLabel(src && src.account_type)) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(g.sum) + "</td><td>";
            html += '<button type="button" class="btn btn-small" onclick="frShowExpenseDetail({channel:\''
                + channel + "',accountId:'" + escapeAttribute(g.account_id) + "'"
                + (date ? ",date:'" + escapeAttribute(date) + "'" : "") + "})\">Операции</button>";
            html += "</td></tr>";
        });
        if (groups.length) {
            html += '<tr class="fr-total-row"><td colspan="2"><b>Итого '
                + escapeHtml(frExpenseChannelLabel(channel)) + "</b></td>"
                + '<td class="fr-num"><b>' + frMoney(frRound(sum)) + "</b></td><td></td></tr>";
        }
        html += "</tbody></table></div>";
        html += '<div class="toolbar" style="margin-top:8px">'
            + '<button type="button" class="btn btn-secondary" onclick="frShowExpenseDetail('
            + (date ? "{date:'" + escapeAttribute(date) + "'}" : "{}") + ')">← Назад</button> '
            + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Закрыть</button></div>';
        openMgmtModal(html);
        return;
    }

    /* Уровень 3: операции по счёту */
    let ops = [];
    if (date) {
        ops = frOpsForDate(areaId, w.period_key, date, "expense").filter(function (o) {
            return frOpAccountId(o) === accountId;
        });
    } else {
        ops = frOps(areaId, w.period_key).filter(function (o) {
            return frOpKind(o) === "expense" && frOpAccountId(o) === accountId;
        });
    }
    if (channel && channel !== "all") {
        ops = ops.filter(function (o) { return frExpenseChannel(o, areaId) === channel; });
    }
    ops.sort(function (a, b) {
        return String(a.date || "").localeCompare(String(b.date || ""))
            || String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
    });
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    if (!date) html += "<th>Дата</th>";
    html += '<th>Кому выдано</th><th class="fr-num">Сумма</th><th>Хозяйственная операция</th>';
    html += "</tr></thead><tbody>";
    if (!ops.length) {
        html += '<tr><td colspan="' + (date ? 3 : 4) + '"><div class="empty-row">Нет операций</div></td></tr>';
    }
    let sum = 0;
    ops.forEach(function (o) {
        sum += frNum(o.amount);
        html += "<tr>";
        if (!date) html += "<td>" + escapeHtml(frFmtDate(o.date) || o.date || "—") + "</td>";
        html += "<td>" + escapeHtml(frExpensePayee(o)) + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(o.amount) + "</td>";
        html += "<td>" + escapeHtml(frExpenseOperation(o)) + "</td></tr>";
    });
    if (ops.length) {
        if (!date) {
            html += '<tr class="fr-total-row"><td colspan="2"><b>Итого</b></td>'
                + '<td class="fr-num"><b>' + frMoney(frRound(sum)) + "</b></td><td></td></tr>";
        } else {
            html += '<tr class="fr-total-row"><td><b>Итого</b></td>'
                + '<td class="fr-num"><b>' + frMoney(frRound(sum)) + "</b></td><td></td></tr>";
        }
    }
    html += "</tbody></table></div>";
    html += '<div class="toolbar" style="margin-top:8px">'
        + '<button type="button" class="btn btn-secondary" onclick="frShowExpenseDetail({channel:\''
        + (channel || "all") + "'" + (date ? ",date:'" + escapeAttribute(date) + "'" : "")
        + "})\">← Назад</button> "
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Закрыть</button></div>';
    openMgmtModal(html);
}

function frShowDayExpenseDetail(date) {
    frShowExpenseDetail({ date: frNormDate(date) || date, channel: "all" });
}

function frKindTitle(kind) {
    if (kind === "income") return "Доходы";
    if (kind === "non_income") return "Внедоходовые поступления";
    if (kind === "transfer") return "Перемещения между своими счетами";
    return "Расходы";
}

function frKindNote(kind) {
    if (kind === "income") return "Ежедневные поступления выручки / доходов. Увеличивают остаток выбранного счёта.";
    if (kind === "non_income") return "Поступления, которые увеличивают остаток денег, но НЕ являются доходом бизнеса.";
    if (kind === "transfer") return "Перемещение НЕ доход и НЕ расход: на одном счёте сумма уменьшается, на другом увеличивается.";
    return "Расходы денежных средств. Перед загрузкой Excel выберите счёт списания.";
}

function renderFrMovementTab(areaId, w, kind, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekSum = 0;
    let html = '<div class="fr-box"><h3>' + escapeHtml(frKindTitle(kind)) + "</h3>";
    html += '<div class="fr-muted" style="margin-bottom:10px">' + escapeHtml(frKindNote(kind)) + "</div>";
    if (!sources.length) {
        html += '<div class="note">Сначала добавьте счета и кассы во вкладке «Настройки».</div></div>';
        return html;
    }
    days.forEach(function (date) {
        const ops = frOpsForDate(areaId, w.period_key, date, kind).slice().sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        let daySum = 0;
        ops.forEach(function (o) { daySum += frNum(o.amount); });
        daySum = frRound(daySum);
        weekSum += daySum;
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span>";
        html += '<span class="fr-day-total">Итого за день: ' + frMoney(daySum) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Счёт / касса</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
        if (canEdit) html += "<th></th>";
        html += "</tr></thead><tbody>";
        if (!ops.length) {
            html += '<tr><td colspan="' + (canEdit ? 4 : 3) + '"><div class="empty-row">Нет записей</div></td></tr>';
        }
        ops.forEach(function (o) {
            const src = sources.find(function (s) { return s.finance_source_id === o.finance_source_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_id; });
            html += "<tr><td>" + escapeHtml((src && src.name) || o.finance_source_id || "—") + "</td>";
            html += '<td class="fr-num">' + frMoney(o.amount) + "</td>";
            html += '<td class="fr-comment-cell">' + escapeHtml(o.comment || "") + "</td>";
            if (canEdit) {
                html += '<td><button type="button" class="btn btn-small" onclick="openFrOpForm(\'' + o.finance_operation_id + "')\">✎</button> "
                    + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\'' + o.finance_operation_id + "')\">✕</button></td>";
            }
            html += "</tr>";
        });
        html += "</tbody></table></div>";
        if (canEdit) {
            html += '<div class="fr-inline-add">';
            html += '<select id="frAddSrc_' + kind + '_' + date + '">'
                + sources.map(function (s) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '">' + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<input class="fr-amt" id="frAddAmt_' + kind + '_' + date + '" placeholder="Сумма" inputmode="decimal">';
            html += '<input class="fr-com" id="frAddCom_' + kind + '_' + date + '" placeholder="Комментарий">';
            html += '<button type="button" class="btn btn-primary btn-small" onclick="frQuickAddOp(\'' + kind + "','" + date + "')\">+ Добавить</button>";
            html += "</div>";
        }
        html += "</div>";
    });
    html += '<div class="fr-day-head" style="border-radius:10px;margin-top:4px"><span>Итого за неделю</span><span class="fr-day-total">'
        + frMoney(frRound(weekSum)) + "</span></div>";
    html += "</div>";
    return html;
}

function frExpensePayee(o) {
    if (!o) return "—";
    const p = String(o.payee != null && o.payee !== "" ? o.payee : "").trim();
    if (p) return p;
    const c = String(o.comment || "").trim();
    return c || "—";
}

function frExpenseOperation(o) {
    if (!o) return "—";
    const op = String(o.operation || o.hoz_operation || "").trim();
    return op || "—";
}

function renderFrExpenseTab(areaId, w, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekCash = 0;
    let weekBank = 0;
    let weekRows = 0;
    let html = '<div class="fr-box"><h3>Расходы</h3>';
    if (canEdit && sources.length) {
        html += '<div class="fr-toolbar-row">'
            + '<button type="button" class="btn btn-primary btn-small" onclick="openFrExcelImport()">Загрузить Excel</button> '
            + '<button type="button" class="btn btn-secondary btn-small" onclick="frShowExpenseDetail({})">Детализация</button>'
            + '<span class="fr-muted">Касса или банк определяется по типу счёта списания. Для наличных — основная касса по умолчанию.</span>'
            + "</div>";
    } else {
        html += '<div class="fr-toolbar-row">'
            + '<button type="button" class="btn btn-secondary btn-small" onclick="frShowExpenseDetail({})">Детализация</button></div>';
    }
    html += '<div class="fr-muted" style="margin-bottom:10px">'
        + "Расходы разделены на <b>Касса</b> (наличные) и <b>Банк</b> (расчётный счёт, карта). "
        + "Колонки операций: Кому выдано | Сумма | Хозяйственная операция.</div>";
    if (!sources.length) {
        html += '<div class="note">Сначала добавьте счета и кассы во вкладке «Счета и кассы».</div></div>';
        return html;
    }

    function renderChannelBlock(channel, dayIso, ops) {
        const label = channel === "cash" ? "Расходы КАССА" : "Расходы БАНК";
        let sum = 0;
        ops.forEach(function (o) { sum += frNum(o.amount); });
        sum = frRound(sum);
        weekRows += ops.length;
        let block = '<div class="fr-expense-channel" style="margin:8px 0 4px">';
        block += '<div class="fr-day-head" style="background:#f8fafc">'
            + "<span><b>" + escapeHtml(label) + "</b></span>"
            + '<span class="fr-day-total">'
            + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({channel:\''
            + channel + "',date:'" + escapeAttribute(dayIso) + "'})\">" + frMoney(sum) + "</button>"
            + "</span></div>";
        if (!ops.length) {
            block += '<div class="empty-row" style="padding:6px 10px">Нет записей</div>';
            block += "</div>";
            return { html: block, sum: sum };
        }
        /* Группировка по счёту внутри канала */
        const byAcc = {};
        ops.forEach(function (o) {
            const sid = frOpAccountId(o) || "_none";
            if (!byAcc[sid]) byAcc[sid] = [];
            byAcc[sid].push(o);
        });
        Object.keys(byAcc).forEach(function (sid) {
            const src = frSourceByAccountId(areaId, sid);
            const accName = (src && src.name) || sid || "—";
            let accSum = 0;
            byAcc[sid].forEach(function (o) { accSum += frNum(o.amount); });
            block += '<div style="padding:4px 10px 2px;font-weight:600">'
                + '<button type="button" class="fr-exp-link" style="font-weight:600" onclick="frShowExpenseDetail({channel:\''
                + channel + "',accountId:'" + escapeAttribute(sid) + "',date:'"
                + escapeAttribute(dayIso) + "'})\">" + escapeHtml(accName) + "</button>"
                + ' <span class="fr-muted">(' + escapeHtml(frAccountTypeLabel(src && src.account_type)) + ") · "
                + frMoney(frRound(accSum)) + "</span></div>";
            block += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
            block += '<th>Кому выдано</th><th class="fr-num">Сумма</th><th>Хозяйственная операция</th>';
            if (canEdit) block += "<th></th>";
            block += "</tr></thead><tbody>";
            byAcc[sid].forEach(function (o) {
                block += "<tr>";
                block += "<td>" + escapeHtml(frExpensePayee(o)) + "</td>";
                block += '<td class="fr-num">' + frMoney(o.amount) + "</td>";
                block += "<td>" + escapeHtml(frExpenseOperation(o)) + "</td>";
                if (canEdit) {
                    block += '<td><button type="button" class="btn btn-small" onclick="openFrOpForm(\''
                        + o.finance_operation_id + "')\">✎</button> "
                        + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\''
                        + o.finance_operation_id + "')\">✕</button></td>";
                }
                block += "</tr>";
            });
            block += "</tbody></table></div>";
        });
        block += "</div>";
        return { html: block, sum: sum };
    }

    days.forEach(function (date) {
        const ops = frOpsForDate(areaId, w.period_key, date, "expense").slice().sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        const cashOps = ops.filter(function (o) { return frExpenseChannel(o, areaId) === "cash"; });
        const bankOps = ops.filter(function (o) { return frExpenseChannel(o, areaId) === "bank"; });
        const cashPart = renderChannelBlock("cash", date, cashOps);
        const bankPart = renderChannelBlock("bank", date, bankOps);
        weekCash += cashPart.sum;
        weekBank += bankPart.sum;
        const dayTotal = frRound(cashPart.sum + bankPart.sum);

        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span>";
        if (canEdit) {
            html += '<button type="button" class="btn btn-secondary btn-small" onclick="openFrExcelImport(\''
                + escapeAttribute(date) + "')\">Загрузить Excel</button>";
        }
        html += "</div>";
        html += cashPart.html;
        html += bankPart.html;
        html += '<div class="fr-day-head" style="border-top:1px solid #e5e7eb;background:#fff">'
            + "<span><b>Всего расходов за день</b></span>"
            + '<span class="fr-day-total"><b>'
            + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({date:\''
            + escapeAttribute(date) + "'})\"><b>" + frMoney(dayTotal) + "</b></button>"
            + "</b></span></div>";
        html += '<div class="fr-muted" style="padding:0 10px 6px">Касса '
            + frMoney(cashPart.sum) + " + Банк " + frMoney(bankPart.sum) + " = " + frMoney(dayTotal) + "</div>";
        if (canEdit) {
            html += '<div class="fr-inline-add">';
            html += '<select id="frAddSrc_expense_' + date + '">'
                + sources.map(function (s) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '">'
                        + escapeHtml(s.name) + " (" + escapeHtml(frAccountTypeLabel(s.account_type)) + ")</option>";
                }).join("") + "</select>";
            html += '<input class="fr-amt" id="frAddAmt_expense_' + date + '" placeholder="Сумма" inputmode="decimal">';
            html += '<input class="fr-com" id="frAddCom_expense_' + date + '" placeholder="Кому выдано">';
            html += '<button type="button" class="btn btn-primary btn-small" onclick="frQuickAddOp(\'expense\',\''
                + date + "')\">+ Добавить</button>";
            html += "</div>";
        }
        html += "</div>";
    });

    weekCash = frRound(weekCash);
    weekBank = frRound(weekBank);
    const weekAll = frRound(weekCash + weekBank);
    html += '<div class="fr-box" style="margin-top:8px">';
    html += "<h3>Итого за неделю</h3>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Расходы касса</span><b class="fr-out">'
        + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({channel:\'cash\'})">'
        + frMoney(weekCash) + "</button></b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Расходы банк</span><b class="fr-out">'
        + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({channel:\'bank\'})">'
        + frMoney(weekBank) + "</button></b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span><b>Всего расходов</b></span><b class="fr-out">'
        + '<button type="button" class="fr-exp-link" onclick="frShowExpenseDetail({})"><b>'
        + frMoney(weekAll) + "</b></button></b></div>";
    html += '<div class="fr-muted">' + weekRows + " операций · касса + банк = всего</div>";
    html += "</div></div>";
    return html;
}

function renderFrDividendDetailPanel(areaId, w, canEdit) {
    const ops = frDividendOps(areaId, w.period_key).slice().sort(function (a, b) {
        return String(a.date || "").localeCompare(String(b.date || ""))
            || String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
    });
    let html = '<div class="fr-box fr-dividend-panel">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">';
    html += "<h3 style=\"margin:0\">Дивиденды за неделю</h3>";
    html += "<div>";
    if (canEdit) {
        html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDividendForm()">+ Дивиденды</button> ';
    }
    html += '<button type="button" class="btn btn-secondary btn-small" onclick="frToggleDividendDetail(\''
        + escapeAttribute(areaId) + '\')">Скрыть</button>';
    html += "</div></div>";
    html += '<div class="fr-muted" style="margin:8px 0">Отдельный отток денег. Не входит в обычные расходы. Уменьшает остаток выбранного счёта/кассы.</div>';
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>'
        + "<th>Дата</th><th>Счёт / касса</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
    if (canEdit) html += "<th></th>";
    html += "</tr></thead><tbody>";
    if (!ops.length) {
        html += '<tr><td colspan="' + (canEdit ? 5 : 4) + '"><div class="empty-row">Нет дивидендов за неделю</div></td></tr>';
    }
    ops.forEach(function (o) {
        const src = frSourceByAccountId(areaId, frOpAccountId(o));
        html += "<tr><td>" + escapeHtml(frFmtDate(o.date)) + "</td>";
        html += "<td>" + escapeHtml((src && src.name) || "—") + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(o.amount) + "</td>";
        html += "<td>" + escapeHtml(o.comment || "") + "</td>";
        if (canEdit) {
            html += '<td><button type="button" class="btn btn-small" onclick="openFrDividendForm(\''
                + escapeAttribute(o.finance_operation_id) + "')\">✎</button> "
                + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\''
                + escapeAttribute(o.finance_operation_id) + "')\">✕</button></td>";
        }
        html += "</tr>";
    });
    html += '</tbody><tfoot><tr><td colspan="2"><b>Итого</b></td>'
        + '<td class="fr-num"><b>' + frMoney(frDividendSumForWeek(areaId, w.period_key)) + "</b></td>"
        + '<td colspan="' + (canEdit ? 2 : 1) + '"></td></tr></tfoot></table></div></div>';
    return html;
}

function renderFrTransitDetailPanel(areaId, w, canEdit) {
    const openList = frTransitOpenList(areaId);
    const all = frTransits(areaId).slice().sort(function (a, b) {
        const ao = a.status === "closed" ? 1 : 0;
        const bo = b.status === "closed" ? 1 : 0;
        if (ao !== bo) return ao - bo;
        return String(b.send_date || "").localeCompare(String(a.send_date || ""))
            || String(b.transit_id || "").localeCompare(String(a.transit_id || ""));
    });
    const openSum = frTransitOpenSum(areaId);
    let html = '<div class="fr-box fr-transit-panel">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">';
    html += "<h3 style=\"margin:0\">Деньги в пути</h3>";
    html += "<div>";
    if (canEdit) {
        html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrTransitSendForm()">+ Отправить в путь</button> ';
    }
    html += '<button type="button" class="btn btn-secondary btn-small" onclick="frToggleTransitDetail(\''
        + escapeAttribute(areaId) + '\')">Скрыть</button>';
    html += "</div></div>";
    html += '<div class="fr-muted" style="margin:8px 0">Деньги уже вышли из одного места хранения, но ещё не поступили в конечное. '
        + "Это не расход и не выручка. Незакрытый остаток переносится между неделями с тем же transit_id.</div>";
    if (openSum >= 0.005) {
        html += '<div class="fr-transit-open-sum">Незакрыто сейчас: <b>' + frMoney(openSum) + "</b></div>";
    } else {
        html += '<div class="fr-transit-open-sum is-ok">Незакрытых денег в пути нет ✓</div>';
    }
    if (!frTransitAccountId(areaId)) {
        html += '<div class="note">Добавьте счёт типа «Деньги в пути» во вкладке «Счета и кассы».</div></div>';
        return html;
    }
    if (canEdit && openList.length) {
        html += '<div style="margin:8px 0 4px"><b>Незакрытые — закрыть / деньги поступили</b></div>';
        openList.forEach(function (t) {
            const from = frSourceByAccountId(areaId, t.from_account_id);
            html += '<div class="fr-transit-open-row">'
                + "<span>" + escapeHtml(frFmtDate(t.send_date)) + " · "
                + escapeHtml((from && from.name) || "—") + " · осталось "
                + frMoney(t.amount_open) + "</span>"
                + '<button type="button" class="btn btn-primary btn-small" onclick="openFrTransitCloseForm(\''
                + escapeAttribute(t.transit_id) + '\')">Закрыть / Деньги поступили</button>'
                + "</div>";
        });
    }
    html += '<div class="fr-table-wrap" style="margin-top:10px"><table class="fr-table"><thead><tr>'
        + "<th>Дата</th><th>Откуда</th><th class=\"fr-num\">Отправлено</th>"
        + "<th class=\"fr-num\">Закрыто</th><th class=\"fr-num\">Осталось</th>"
        + "<th>Куда поступило</th><th>Статус</th></tr></thead><tbody>";
    if (!all.length) {
        html += '<tr><td colspan="7"><div class="empty-row">История пуста</div></td></tr>';
    }
    all.forEach(function (t) {
        frNormalizeTransitRecord(t);
        const from = frSourceByAccountId(areaId, t.from_account_id);
        const destNames = [];
        (t.closes || []).forEach(function (c) {
            const to = frSourceByAccountId(areaId, c.to_account_id);
            destNames.push((to && to.name) || "—");
        });
        const rowCls = t.status === "closed" ? "" : " fr-transit-row-open";
        html += '<tr class="' + rowCls + '">';
        html += "<td>" + escapeHtml(frFmtDate(t.send_date)) + "</td>";
        html += "<td>" + escapeHtml((from && from.name) || "—") + "</td>";
        html += '<td class="fr-num">' + frMoney(t.amount_sent) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.amount_closed) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.amount_open) + "</td>";
        html += "<td>" + escapeHtml(destNames.length ? destNames.join(", ") : "—") + "</td>";
        html += "<td>" + escapeHtml(frTransitStatusLabel(t.status));
        if (canEdit && t.status !== "closed") {
            html += ' <button type="button" class="fr-link-btn" onclick="openFrTransitCloseForm(\''
                + escapeAttribute(t.transit_id) + '\')">закрыть</button>';
        }
        html += "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    return html;
}

function openFrDividendForm(id) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const sources = frSources(area.finance_area_id, false);
    if (!sources.length) {
        toast("Нет активных счетов", "error");
        return;
    }
    const rec = id ? (appData.financeRec.operations || []).find(function (o) {
        return o && o.finance_operation_id === id;
    }) : null;
    const primaryId = frPrimaryCashId(area.finance_area_id);
    let html = "<h3>" + (rec ? "Дивиденды" : "Новые дивиденды") + "</h3>";
    html += '<input type="hidden" id="frDivId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frDivDate" type="date" value="'
        + escapeAttribute((rec && rec.date) || w.end_date || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Счёт / касса, откуда выданы</label><select id="frDivAccount">';
    sources.forEach(function (s) {
        const sel = rec
            ? frOpAccountId(rec) === s.finance_source_id
            : s.finance_source_id === primaryId;
        html += '<option value="' + escapeAttribute(s.finance_source_id) + '"'
            + (sel ? " selected" : "") + ">" + escapeHtml(s.name) + "</option>";
    });
    html += "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frDivAmt" value="'
        + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frDivCom" value="'
        + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="note">Дивиденды не смешиваются с обычными расходами, но уменьшают остаток выбранного счёта.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrDividend()">Сохранить</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrDividend() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const id = ((document.getElementById("frDivId") || {}).value || "");
    const accountId = ((document.getElementById("frDivAccount") || {}).value || "");
    const amt = frNum((document.getElementById("frDivAmt") || {}).value);
    const date = ((document.getElementById("frDivDate") || {}).value || w.start_date);
    const comment = String(((document.getElementById("frDivCom") || {}).value) || "").trim();
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!accountId) { toast("Выберите счёт", "error"); return; }
    ensureFinanceRec();
    let rec = id ? (appData.financeRec.operations || []).find(function (o) {
        return o && o.finance_operation_id === id;
    }) : null;
    if (!rec) {
        rec = {
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) {
                return x && x.finance_operation_id;
            })),
            created_at: new Date().toISOString()
        };
        appData.financeRec.operations.push(rec);
    }
    const opWeek = frWeek(date);
    rec.period_key = (opWeek && opWeek.period_key) || w.period_key;
    rec.date = frNormDate(date) || date;
    rec.type = "out";
    rec.finance_category_id = "";
    rec.amount = amt;
    rec.comment = comment;
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    frStampOperation(rec, {
        object_id: area.object_id || area.finance_area_id,
        account_id: accountId,
        op_type: "dividend",
        data_source: rec.data_source || FR_DATA_SOURCES.manual,
        revenue_direction: "none"
    });
    frAudit(id ? "dividend_edit" : "dividend_add", {
        finance_source_id: accountId,
        detail: date,
        new_value: amt
    });
    saveApp();
    closeMgmtModal();
    if (!frUi.dividendDetailOpen) frUi.dividendDetailOpen = {};
    frUi.dividendDetailOpen[area.finance_area_id] = true;
    renderFinanceRec();
}

function openFrTransitSendForm() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const transitId = frTransitAccountId(area.finance_area_id);
    if (!transitId) {
        toast("Сначала добавьте счёт типа «Деньги в пути»", "error");
        return;
    }
    const sources = frSources(area.finance_area_id, false).filter(function (s) {
        return !frIsTransitSource(s);
    });
    if (!sources.length) {
        toast("Нет счетов-источников", "error");
        return;
    }
    const primaryId = frPrimaryCashId(area.finance_area_id);
    let html = "<h3>Отправить деньги в путь</h3>";
    html += '<div class="form-group"><label>Дата отправки</label><input id="frTsDate" type="date" value="'
        + escapeAttribute(w.end_date || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Откуда</label><select id="frTsFrom">';
    sources.forEach(function (s) {
        html += '<option value="' + escapeAttribute(s.finance_source_id) + '"'
            + (s.finance_source_id === primaryId ? " selected" : "") + ">"
            + escapeHtml(s.name) + "</option>";
    });
    html += "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frTsAmt" value=""></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frTsCom" value=""></div>';
    html += '<div class="note">Создаётся реальное перемещение на счёт «Деньги в пути» и запись контроля (transit_id). '
        + "Общая сумма денег компании не меняется.</div>";
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrTransitSend()">Отправить</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrTransitSend() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const areaId = area.object_id || area.finance_area_id;
    const fromId = ((document.getElementById("frTsFrom") || {}).value || "");
    const amt = frNum((document.getElementById("frTsAmt") || {}).value);
    const date = ((document.getElementById("frTsDate") || {}).value || "");
    const comment = String(((document.getElementById("frTsCom") || {}).value) || "").trim();
    const transitAcc = frTransitAccountId(areaId);
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!fromId || !transitAcc) { toast("Выберите счёт", "error"); return; }
    if (fromId === transitAcc) { toast("Счета должны отличаться", "error"); return; }
    const op = frCreateTransferOp(areaId, fromId, transitAcc, amt, date, comment);
    frSyncTransitAfterTransfer(areaId, op, true);
    frAudit("transit_send", { finance_source_id: fromId, detail: transitAcc + " " + date, new_value: amt });
    saveApp();
    closeMgmtModal();
    if (!frUi.transitDetailOpen) frUi.transitDetailOpen = {};
    frUi.transitDetailOpen[areaId] = true;
    renderFinanceRec();
}

function openFrTransitCloseForm(transitId) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const t = frTransitById(transitId);
    if (!t || (t.object_id || t.finance_area_id) !== (area.object_id || area.finance_area_id)) {
        toast("Запись не найдена", "error");
        return;
    }
    frNormalizeTransitRecord(t);
    if (t.status === "closed" || frNum(t.amount_open) < 0.005) {
        toast("Уже закрыто", "error");
        return;
    }
    const sources = frSources(area.finance_area_id, false).filter(function (s) {
        return !frIsTransitSource(s);
    });
    const w = frEnsureWeek();
    let html = "<h3>Закрыть / Деньги поступили</h3>";
    html += '<input type="hidden" id="frTcId" value="' + escapeAttribute(transitId) + '">';
    html += '<div class="note">Осталось в пути: <b>' + frMoney(t.amount_open) + "</b></div>";
    html += '<div class="form-group"><label>Дата поступления</label><input id="frTcDate" type="date" value="'
        + escapeAttribute(w.end_date || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Куда поступили</label><select id="frTcTo">';
    sources.forEach(function (s) {
        html += '<option value="' + escapeAttribute(s.finance_source_id) + '">'
            + escapeHtml(s.name) + "</option>";
    });
    html += "</select></div>";
    html += '<div class="form-group"><label>Сумма поступления</label><input id="frTcAmt" value="'
        + escapeAttribute(String(t.amount_open).replace(".", ",")) + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frTcCom" value=""></div>';
    html += '<div class="note">Можно закрыть частично. Сумма не больше незакрытого остатка.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrTransitClose()">Сохранить</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrTransitClose() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const areaId = area.object_id || area.finance_area_id;
    const transitId = ((document.getElementById("frTcId") || {}).value || "");
    const toId = ((document.getElementById("frTcTo") || {}).value || "");
    const amt = frNum((document.getElementById("frTcAmt") || {}).value);
    const date = ((document.getElementById("frTcDate") || {}).value || "");
    const comment = String(((document.getElementById("frTcCom") || {}).value) || "").trim();
    const t = frTransitById(transitId);
    if (!t || (t.object_id || t.finance_area_id) !== areaId) {
        toast("Запись не найдена", "error");
        return;
    }
    frNormalizeTransitRecord(t);
    const openAmt = frNum(t.amount_open);
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (amt - openAmt > 0.005) {
        toast("Нельзя закрыть больше, чем осталось в пути (" + frMoney(openAmt) + ")", "error");
        return;
    }
    if (!toId || frIsTransitSource(frSourceByAccountId(areaId, toId))) {
        toast("Выберите конечный счёт", "error");
        return;
    }
    const transitAcc = t.transit_account_id || frTransitAccountId(areaId);
    const op = frCreateTransferOp(areaId, transitAcc, toId, amt, date, comment, {
        transit_id: t.transit_id,
        transit_role: "close"
    });
    t.amount_closed = frRound(frNum(t.amount_closed) + amt);
    t.closes.push({
        close_id: nextPrefixedId("ftc", (t.closes || []).map(function (x) { return x && x.close_id; })),
        date: frNormDate(date) || date,
        to_account_id: toId,
        amount: amt,
        comment: comment,
        operation_id: op.finance_operation_id
    });
    t.updated_at = new Date().toISOString();
    frNormalizeTransitRecord(t);
    frAudit("transit_close", {
        finance_source_id: toId,
        detail: t.transit_id + " " + date,
        new_value: amt
    });
    saveApp();
    closeMgmtModal();
    if (!frUi.transitDetailOpen) frUi.transitDetailOpen = {};
    frUi.transitDetailOpen[areaId] = true;
    renderFinanceRec();
}

function renderFrTransferTab(areaId, w, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekSum = 0;
    let html = '<div class="fr-box"><h3>Перемещения между своими счетами</h3>';
    html += '<div class="fr-muted" style="margin-bottom:10px">'
        + escapeHtml(frKindNote("transfer"))
        + (frIsTerritoryArea(areaId)
            ? " Пример: р/с Розница → р/с ОПТ уменьшает Розницу и увеличивает ОПТ по остаткам, но не меняет выручку направлений."
            : "")
        + " Для контроля незакрытых сумм отправляйте на счёт типа «Деньги в пути».</div>";
    if (canEdit && frTransitAccountId(areaId)) {
        html += '<div style="margin-bottom:10px">'
            + '<button type="button" class="btn btn-primary btn-small" onclick="openFrTransitSendForm()">+ Отправить в путь</button> '
            + '<button type="button" class="btn btn-secondary btn-small" onclick="frToggleTransitDetail(\''
            + escapeAttribute(areaId) + '\')">Журнал денег в пути</button>'
            + ' <button type="button" class="btn btn-secondary btn-small" onclick="openFrDividendForm()">+ Дивиденды</button></div>';
    } else if (canEdit) {
        html += '<div style="margin-bottom:10px">'
            + '<button type="button" class="btn btn-secondary btn-small" onclick="openFrDividendForm()">+ Дивиденды</button></div>';
    }
    if (sources.length < 2) {
        html += '<div class="note">Для перемещений нужно минимум два активных счёта.</div></div>';
        return html;
    }
    days.forEach(function (date) {
        const ops = frOpsForDate(areaId, w.period_key, date, "transfer").slice().sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        let daySum = 0;
        ops.forEach(function (o) { daySum += frNum(o.amount); });
        daySum = frRound(daySum);
        weekSum += daySum;
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span>";
        html += '<span class="fr-day-total">Итого за день: ' + frMoney(daySum) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Откуда</th><th>Куда</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
        if (canEdit) html += "<th></th>";
        html += "</tr></thead><tbody>";
        if (!ops.length) {
            html += '<tr><td colspan="' + (canEdit ? 5 : 4) + '"><div class="empty-row">Нет перемещений</div></td></tr>';
        }
        ops.forEach(function (o) {
            const from = sources.find(function (s) { return s.finance_source_id === o.finance_source_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_id; });
            const to = sources.find(function (s) { return s.finance_source_id === o.finance_source_to_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_to_id; });
            html += "<tr><td>" + escapeHtml((from && from.name) || "—") + "</td>";
            html += "<td>" + escapeHtml((to && to.name) || "—") + "</td>";
            html += '<td class="fr-num">' + frMoney(o.amount) + "</td>";
            html += "<td>" + escapeHtml(o.comment || "") + "</td>";
            if (canEdit) {
                html += '<td><button type="button" class="btn btn-small" onclick="openFrTransferForm(\'' + o.finance_operation_id + "')\">✎</button> "
                    + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\'' + o.finance_operation_id + "')\">✕</button></td>";
            }
            html += "</tr>";
        });
        html += "</tbody></table></div>";
        if (canEdit) {
            html += '<div class="fr-inline-add fr-inline-transfer">';
            html += '<select id="frTrFrom_' + date + '" title="Откуда">'
                + sources.map(function (s) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '">' + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<span class="fr-muted">→</span>';
            html += '<select id="frTrTo_' + date + '" title="Куда">'
                + sources.map(function (s, i) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '"' + (i === 1 ? " selected" : "") + ">"
                        + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<input class="fr-amt" id="frTrAmt_' + date + '" placeholder="Сумма" inputmode="decimal">';
            html += '<input class="fr-com" id="frTrCom_' + date + '" placeholder="Комментарий">';
            html += '<button type="button" class="btn btn-primary btn-small" onclick="frQuickAddTransfer(\'' + date + "')\">+ Переместить</button>";
            html += "</div>";
        }
        html += "</div>";
    });
    html += '<div class="fr-day-head" style="border-radius:10px;margin-top:4px"><span>Итого перемещений за неделю</span><span class="fr-day-total">'
        + frMoney(frRound(weekSum)) + "</span></div>";
    if (canEdit) {
        html += '<div style="margin-top:10px"><button type="button" class="btn btn-secondary btn-small" onclick="openFrTransferForm()">+ Перемещение (форма)</button></div>';
    }
    html += "</div>";
    return html;
}

function frQuickAddTransfer(date) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const from = ((document.getElementById("frTrFrom_" + date) || {}).value || "");
    const to = ((document.getElementById("frTrTo_" + date) || {}).value || "");
    const amt = frNum((document.getElementById("frTrAmt_" + date) || {}).value);
    const comment = String(((document.getElementById("frTrCom_" + date) || {}).value) || "").trim();
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!from || !to) { toast("Выберите счета", "error"); return; }
    if (from === to) { toast("Счета «откуда» и «куда» должны отличаться", "error"); return; }
    ensureFinanceRec();
    const opWeek = frWeek(date);
    const rec = {
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        period_key: opWeek.period_key || w.period_key,
        date: date,
        type: "transfer",
        finance_category_id: "",
        amount: amt,
        comment: comment,
        is_deleted: false,
        created_at: new Date().toISOString()
    };
    frStampOperation(rec, {
        object_id: area.object_id || area.finance_area_id,
        account_id: from,
        account_to_id: to,
        op_type: "transfer",
        data_source: FR_DATA_SOURCES.manual,
        revenue_direction: "none"
    });
    appData.financeRec.operations.push(rec);
    const areaId = area.object_id || area.finance_area_id;
    const fromSrc = frSourceByAccountId(areaId, from);
    if (frIsTransitSource(fromSrc)) {
        toast("Закрытие денег в пути — через «Закрыть / Деньги поступили»", "error");
        rec.is_deleted = true;
        saveApp();
        renderFinanceRec();
        return;
    }
    frSyncTransitAfterTransfer(areaId, rec, true);
    frAudit("transfer_add", { finance_source_id: from, detail: to + " " + date, new_value: amt });
    saveApp();
    renderFinanceRec();
}

function frQuickAddOp(kind, date) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const srcEl = document.getElementById("frAddSrc_" + kind + "_" + date);
    const amtEl = document.getElementById("frAddAmt_" + kind + "_" + date);
    const comEl = document.getElementById("frAddCom_" + kind + "_" + date);
    const amt = frNum(amtEl && amtEl.value);
    const src = srcEl && srcEl.value;
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!src) { toast("Выберите счёт", "error"); return; }
    ensureFinanceRec();
    const movement_kind = kind === "non_income" ? "non_income" : (kind === "income" ? "income" : "expense");
    const opWeek = frWeek(date);
    const objectId = area.object_id || area.finance_area_id;
    const rec = {
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        period_key: opWeek.period_key || w.period_key,
        date: date,
        type: movement_kind === "expense" ? "out" : "in",
        finance_category_id: "",
        amount: amt,
        comment: String((comEl && comEl.value) || "").trim(),
        is_deleted: false,
        created_at: new Date().toISOString()
    };
    frStampOperation(rec, {
        object_id: objectId,
        account_id: src,
        op_type: movement_kind,
        data_source: FR_DATA_SOURCES.manual
    });
    appData.financeRec.operations.push(rec);
    frAudit("op_add", { finance_source_id: src, new_value: amt, detail: movement_kind + " " + date });
    saveApp();
    renderFinanceRec();
}

function renderFrDaysTab(areaId, w, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    const isTerritory = frIsTerritoryArea(areaId);
    let html = '<div class="fr-box"><h3>День — расчёт по каждому счёту</h3>';
    html += '<div class="fr-muted" style="margin-bottom:10px">'
        + "Остаток на начало + поступления + внедоходовые + входящие перемещения − расходы − исходящие перемещения "
        + "= <b>расчётный остаток на конец дня</b>. Конец дня автоматически становится началом следующего. "
        + "Расчётный остаток сотрудник не вводит. Перемещения не доход и не расход.</div>";
    if (canEdit) {
        html += '<div class="fr-toolbar-row">'
            + '<button type="button" class="btn btn-secondary btn-small" onclick="frUi.opKind=\'income\';openFrOpForm()">+ Доход</button>'
            + '<button type="button" class="btn btn-secondary btn-small" onclick="frUi.opKind=\'non_income\';openFrOpForm()">+ Внедоходовое</button>'
            + "</div>";
    }
    if (!sources.length) {
        html += '<div class="note">Нет активных счетов и касс.</div></div>';
        return html;
    }
    days.forEach(function (date) {
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Счёт / касса</th><th>Тип</th>";
        if (isTerritory) html += "<th>Направление</th>";
        html += "<th class=\"fr-num\">Начало</th>"
            + "<th class=\"fr-num\">Поступления</th>"
            + "<th class=\"fr-num\">Внедоходовые</th>"
            + "<th class=\"fr-num\">Вх. перемещ.</th>"
            + "<th class=\"fr-num\">Расходы</th>"
            + "<th class=\"fr-num\">Исх. перемещ.</th>"
            + "<th class=\"fr-num\">Конец дня</th></tr></thead><tbody>";
        let dayEnd = 0;
        sources.forEach(function (s) {
            const row = frDayCalculated(areaId, w.period_key, date, s.finance_source_id);
            dayEnd += row.calculated;
            html += "<tr>";
            html += "<td>" + escapeHtml(s.name)
                + (frPrimaryCashId(areaId) === s.finance_source_id ? ' <span class="fr-badge fr-badge-ok">осн. касса</span>' : "")
                + "</td>";
            html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
            if (isTerritory) html += "<td>" + escapeHtml(frRevenueDirLabel(s.revenue_direction || "none")) + "</td>";
            html += '<td class="fr-num">' + frMoney(row.opening) + "</td>";
            html += '<td class="fr-num fr-in">' + frMoney(row.income) + "</td>";
            html += '<td class="fr-num fr-non">' + frMoney(row.non_income) + "</td>";
            html += '<td class="fr-num fr-in">' + frMoney(row.transfer_in) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.expense) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.transfer_out) + "</td>";
            html += '<td class="fr-num"><b>' + frMoney(row.calculated) + "</b></td>";
            html += "</tr>";
        });
        const leadCols = isTerritory ? 9 : 8;
        html += '</tbody><tfoot><tr><td colspan="' + leadCols + '">Итого по счетам на конец дня</td>'
            + '<td class="fr-num"><b>' + frMoney(frRound(dayEnd)) + "</b></td></tr></tfoot></table></div>";
        html += "</div>";
    });
    html += "</div>";
    return html;
}

function frSaveDayActual(sourceId, date, value) {
    /* Факт остатка опционален (инвентаризация); расчётный остаток всегда считается автоматически. */
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    ensureFinanceRec();
    let row = frDayClosingRow(area.finance_area_id, date, sourceId);
    const raw = String(value == null ? "" : value).trim();
    if (!row) {
        row = {
            finance_day_closing_id: nextPrefixedId("fdc", (appData.financeRec.day_closings || []).map(function (x) { return x && x.finance_day_closing_id; })),
            finance_area_id: area.finance_area_id,
            object_id: area.object_id || area.finance_area_id,
            period_key: w.period_key,
            date: date,
            finance_source_id: sourceId,
            account_id: sourceId,
            data_source: FR_DATA_SOURCES.manual
        };
        frNormalizeBalanceRow(row);
        appData.financeRec.day_closings.push(row);
    }
    if (raw === "") {
        row.actual_closing = null;
        row.actual_balance = null;
    } else {
        const num = frNum(raw);
        row.actual_closing = num;
        row.actual_balance = num;
    }
    row.updated_at = new Date().toISOString();
    frNormalizeBalanceRow(row);
    const calc = frDayCalculated(area.finance_area_id, w.period_key, date, sourceId);
    frAudit("day_actual", {
        finance_source_id: sourceId,
        detail: date,
        new_value: row.actual_closing,
        old_value: calc.calculated
    });
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, 1) : null;
    if (date === w.end_date && next) frCarryIfNeeded(area.finance_area_id, next.period_key);
    saveApp();
    renderFinanceRec();
}

function renderFrWeekTab(areaId, w, tot, weekCalc, accountsEnd, disc, balanced, st, closed, hasFacts) {
    const sources = frSources(areaId, false);
    const isTerritory = frIsTerritoryArea(areaId);
    let html = '<div class="fr-layout"><div>';
    html += '<div class="fr-box"><h3>Сверка недели (чт–ср)</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">'
        + "Расчётный остаток = начало недели + поступления − расходы. "
        + "Сравнивается с суммой конечных остатков всех счетов. Перемещения не доход и не расход.</div>";
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    html += "<th>Счёт / касса</th><th>Тип</th>";
    if (isTerritory) html += "<th>Направление</th>";
    html += "<th class=\"fr-num\">Начало</th>"
        + "<th class=\"fr-num\">Доходы</th><th class=\"fr-num\">Внедоходовые</th>"
        + "<th class=\"fr-num\">Расходы</th><th class=\"fr-num\">Перемещения ±</th>"
        + "<th class=\"fr-num\">Конец недели</th></tr></thead><tbody>";
    sources.forEach(function (s) {
        const t = frSourceTotals(areaId, w.period_key, s.finance_source_id);
        const netTransfer = frRound(t.transfer_in - t.transfer_out);
        html += "<tr><td>" + escapeHtml(s.name)
            + (frPrimaryCashId(areaId) === s.finance_source_id ? ' <span class="fr-badge fr-badge-ok">осн.</span>' : "")
            + "</td>";
        html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
        if (isTerritory) html += "<td>" + escapeHtml(frRevenueDirLabel(s.revenue_direction || "none")) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.opening) + "</td>";
        html += '<td class="fr-num fr-in">' + frMoney(t.income) + "</td>";
        html += '<td class="fr-num fr-non">' + frMoney(t.non_income) + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(t.expense) + "</td>";
        html += '<td class="fr-num">' + (netTransfer >= 0 ? "+" : "") + frMoney(netTransfer) + "</td>";
        html += '<td class="fr-num"><b>' + frMoney(t.closing) + "</b></td></tr>";
    });
    html += '</tbody><tfoot><tr><td colspan="' + (isTerritory ? 3 : 2) + '">ИТОГО по счетам</td>';
    html += '<td class="fr-num">' + frMoney(tot.opening) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.income) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.non_income) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.expense) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(tot.transfer_in - tot.transfer_out)) + "</td>";
    html += '<td class="fr-num"><b>' + frMoney(accountsEnd) + "</b></td></tr></tfoot></table></div>";
    html += "</div></div><div>";
    html += '<div class="fr-ctrl ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><h3>Контрольная сверка</h3>';
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Начало недели</span><b>' + frMoney(tot.opening) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>+ Поступления (доходы)</span><b class="fr-in">' + frMoney(tot.income) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>+ Внедоходовые</span><b class="fr-non">' + frMoney(tot.non_income) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>− Расходы</span><b class="fr-out">' + frMoney(tot.expense) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between;border-top:1px solid #e5e7eb;padding-top:6px"><span>Расчётный остаток</span><b>' + frMoney(weekCalc) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Сумма остатков счетов</span><b>' + frMoney(accountsEnd) + "</b></div>";
    html += '<div class="fr-muted" style="margin-top:8px">РАСХОЖДЕНИЕ = итог счетов − расчётный</div>';
    html += '<div class="fr-disc">' + (hasFacts ? frMoney(disc) : "—") + "</div>";
    html += "<div>" + (hasFacts ? (balanced ? "✓ 0 ₽ — неделя сошлась" : "⚠ Есть расхождение") : "Добавьте счета в настройках") + "</div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Статус периода</h3>';
    html += "<p>" + escapeHtml(frStatusLabel(st.status)) + (closed ? " · только просмотр" : "") + "</p>";
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'filled\')">Отметить заполненным</button> ';
    if (frCan("check") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'review\')">На проверку</button> ';
    if (frCan("check") && !closed && balanced) html += '<button type="button" class="btn btn-primary btn-small" onclick="frSetStatus(\'reconciled\')">Сверено</button> ';
    if (frCan("close") && !closed) html += '<button type="button" class="btn btn-primary btn-small" onclick="frClosePeriod()">Закрыть период</button> ';
    if (closed && frCan("setup")) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frReopenPeriod()">Открыть закрытый период</button>';
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Перенос остатков</h3>';
    html += '<p class="fr-muted">Расчётный остаток среды станет началом следующей недели (четверг).</p>';
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frPushCarry()">Перенести в следующую неделю</button>';
    html += "</div></div></div>";
    return html;
}

function renderFrSettingsHtml(area) {
    const primaryId = frPrimaryCashId(area.finance_area_id);
    const isTerritory = frIsTerritoryArea(area);
    const isShop = frIsShopArea(area);
    const canManage = frCanManageAccounts();
    let html = '<div class="fr-layout"><div class="fr-box"><h3>Счета и кассы</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Справочник денежных счетов объекта «'
        + escapeHtml(area.name) + "». Данные этого объекта хранятся отдельно. Можно несколько счетов одного типа.</div>";
    if (isTerritory) {
        html += '<div class="note" style="margin-bottom:8px">Для Территории дополнительно укажите <b>направление выручки</b> счёта: ОПТ, Розница, Склад или «Не относится». '
            + "Счёт — место хранения денег; направление нужно только для аналитики выручки. "
            + "Перемещения между счетами не меняют выручку направлений.</div>";
    }
    if (isShop) {
        html += '<div class="note" style="margin-bottom:8px">Счёт или касса — место хранения и движения денег. '
            + "Внедоходовые поступления вводятся отдельным блоком в сверке (комментарий + сумма), а не настройкой счёта. "
            + "Деление ОПТ / Розница / Склад для магазина не используется.</div>";
    }
    html += '<div class="note" style="margin-bottom:8px">'
        + "<b>Участвует в фактическом остатке</b> — счёт попадает в сверку остатков на конец недели. "
        + "Контрольные ежедневные кассы (Торг и т.п.) можно оставить без галочки: они остаются в таблице поступлений, "
        + "но не требуют отдельных «сдач»-перемещений. "
        + "<b>Основное место хранения</b> — справочная привязка (например Торг 1 → Основная касса), без создания операций.</div>";
    if (!canManage) {
        html += '<div class="note" style="margin-bottom:8px">Просмотр справочника. Создавать, изменять, менять порядок, деактивировать и удалять счета может только Администратор.</div>';
    }
    if (canManage) {
        html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'source\')">+ Счёт / касса</button>';
        html += '<div class="fr-muted" style="margin:8px 0 4px">Перетащите строку за ☰, чтобы изменить порядок. Порядок свой для каждого объекта и используется в таблицах сверки.</div>';
    }
    html += '<table class="fr-table fr-sources-table" id="frSourcesTable" style="margin-top:4px"><thead><tr>';
    if (canManage) html += '<th class="fr-drag-col" title="Перетащить"></th>';
    html += "<th>Название</th><th>Тип</th>";
    if (isTerritory) html += "<th>Направление выручки</th>";
    html += "<th>Факт. остаток</th><th>Место хранения</th>";
    html += "<th>Статус</th><th>Основная касса</th>";
    if (canManage) html += "<th></th>";
    html += "</tr></thead><tbody>";
    frSources(area.finance_area_id, true).forEach(function (s) {
        const isPrimary = primaryId === s.finance_source_id;
        const aid = s.account_id || s.finance_source_id;
        const inactive = s.is_active === false;
        const inActual = s.in_actual_balance !== false;
        const storageLabel = frStorageAccountLabel(area.finance_area_id, s);
        html += '<tr data-account-id="' + escapeAttribute(aid) + '"';
        if (canManage) {
            html += ' ondragover="frOnSourceDragOver(event)"'
                + ' ondragleave="frOnSourceDragLeave(event)"'
                + ' ondrop="frOnSourceDrop(event,\'' + escapeAttribute(aid) + '\')"';
        }
        html += ">";
        if (canManage) {
            html += '<td class="fr-drag-col">'
                + '<span class="fr-drag-handle" title="Перетащить" draggable="true"'
                + ' ondragstart="frOnSourceDragStart(event,\'' + escapeAttribute(aid) + '\')"'
                + ' ondragend="frOnSourceDragEnd(event)">☰</span></td>';
        }
        html += "<td>" + escapeHtml(s.name) + "</td>";
        html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
        if (isTerritory) html += "<td>" + escapeHtml(frRevenueDirLabel(s.revenue_direction || "none")) + "</td>";
        html += "<td>";
        if (canManage && !inactive) {
            html += '<label style="cursor:pointer;white-space:nowrap">'
                + '<input type="checkbox"' + (inActual ? " checked" : "")
                + " onchange=\"frToggleInActualBalance('" + s.finance_source_id + "',this.checked)\"> ✓</label>";
        } else {
            html += inActual ? "✓" : "—";
        }
        html += "</td>";
        html += "<td>" + (storageLabel ? escapeHtml(storageLabel) : "—") + "</td>";
        html += "<td>" + (inactive ? "Неактивен" : "Активен") + "</td>";
        html += "<td>";
        if (s.account_type === "cash" && !inactive) {
            if (isPrimary) html += '<span class="fr-badge fr-badge-ok">Основная</span>';
            else if (canManage) {
                html += '<button type="button" class="btn btn-small btn-secondary" onclick="frSetPrimaryCash(\''
                    + s.finance_source_id + "')\">Сделать основной</button>";
            } else html += "—";
        } else html += "—";
        html += "</td>";
        if (canManage) {
            html += '<td class="fr-src-actions" style="white-space:nowrap">';
            html += '<button type="button" class="btn btn-small" title="Редактировать" onclick="openFrDictForm(\'source\',\''
                + s.finance_source_id + "')\">Редактировать</button> ";
            if (inactive) {
                html += '<button type="button" class="btn btn-small btn-secondary" onclick="frActivateSource(\''
                    + s.finance_source_id + "')\">Активировать</button> ";
            } else {
                html += '<button type="button" class="btn btn-small btn-secondary" onclick="frDeactivateSource(\''
                    + s.finance_source_id + "')\">Деактивировать</button> ";
            }
            html += '<button type="button" class="btn btn-small btn-danger" onclick="frDeleteSource(\''
                + s.finance_source_id + "')\">Удалить</button>";
            html += "</td>";
        }
        html += "</tr>";
    });
    const colSpan = (canManage ? 2 : 0) + 6 + (isTerritory ? 1 : 0);
    if (!frSources(area.finance_area_id, true).length) {
        html += '<tr><td colspan="' + colSpan + '"><div class="empty-row">Счетов пока нет</div></td></tr>';
    }
    html += "</tbody></table></div>";
    html += '<div class="fr-box"><h3>Статьи движения (необязательно)</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Для ежедневных расходов статья не обязательна — используйте комментарий / получателя.</div>';
    if (frCan("setup")) {
        html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'cat\')">+ Статья</button>';
    }
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Тип</th><th>Статус</th><th></th></tr></thead><tbody>';
    frCats(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.default_type === "in" ? "Приход" : (s.default_type === "out" ? "Расход" : "Любой")) + "</td><td>"
            + (s.is_active === false ? "Неактивна" : "Активна") + "</td><td>";
        if (frCan("setup")) {
            html += '<button type="button" class="btn btn-small" onclick="openFrDictForm(\'cat\',\'' + s.finance_category_id + "')\">✎</button>";
        }
        html += "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Входящие остатки недели</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Остаток на начало четверга по каждому счёту. Обычно переносится автоматически со среды предыдущей недели.</div>';
    html += '<table class="fr-table"><thead><tr><th>Счёт / касса</th>';
    if (isTerritory) html += "<th>Направление</th>";
    html += '<th class="fr-num">Остаток на начало недели</th></tr></thead><tbody>';
    const w = frEnsureWeek();
    frSources(area.finance_area_id, true).forEach(function (s) {
        const v = frOpeningValue(area.finance_area_id, w.period_key, s.finance_source_id);
        html += "<tr><td>" + escapeHtml(s.name)
            + (s.is_active === false ? ' <span class="fr-muted">(неактивен)</span>' : "") + "</td>";
        if (isTerritory) html += "<td>" + escapeHtml(frRevenueDirLabel(s.revenue_direction || "none")) + "</td>";
        html += '<td class="fr-num">';
        if (frCanEditData()) {
            html += '<input style="width:120px;text-align:right" value="' + escapeAttribute(String(v))
                + "\" onchange=\"frSaveOpening('" + s.finance_source_id + "',this.value)\">";
        } else html += frMoney(v);
        html += "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Настройка сверки (для СПУ)</h3>';
    html += '<p class="fr-muted">Показатели: finm_opening, finm_income, finm_non_income, finm_outflow, finm_closing, finm_discrepancy. Недельное расхождение = сумма остатков счетов − (начало + поступления − расходы). Перемещения не доход и не расход.</p>';
    const terms = frFormulaTerms(area.finance_area_id);
    html += '<div id="frFormulaBox">';
    terms.forEach(function (t, i) {
        html += '<div class="fr-filters" data-i="' + i + '"><select class="frSign"><option value="+"' + (t.sign !== "-" ? " selected" : "") + ">+</option><option value=\"-\"" + (t.sign === "-" ? " selected" : "") + ">-</option></select>";
        html += '<select class="frMetric">'
            + [
                ["finm_opening", "Начальные остатки"],
                ["finm_income", "Доходы"],
                ["finm_non_income", "Внедоходовые"],
                ["finm_inflow", "Все поступления"],
                ["finm_outflow", "Расходы"],
                ["finm_closing", "Расчётный остаток"],
                ["finm_actual_closing", "Сумма остатков счетов"],
                ["finm_discrepancy", "Расхождение"],
                ["finm_net", "Доходы + внедоходовые − расходы"]
            ].map(function (p) {
                return '<option value="' + p[0] + '"' + (t.metric_id === p[0] ? " selected" : "") + ">" + p[1] + "</option>";
            }).join("") + "</select></div>";
    });
    html += "</div>";
    if (frCan("setup")) {
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frAddFormulaTerm()">+ Показатель</button> ';
        html += '<button type="button" class="btn btn-primary btn-small" onclick="frSaveFormula()">Сохранить формулу</button>';
    }
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Права / доступы</h3><p class="fr-muted">Права задаются в «Сотрудники, должности и доступы». Отдельная система пользователей не создаётся.</p>';
    if (typeof isAppAdmin === "function" && isAppAdmin()) {
        html += '<button type="button" class="btn btn-secondary" onclick="showSection(\'users\')">Открыть доступы</button>';
    }
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>История изменений</h3><div class="fr-table-wrap"><table class="fr-table"><thead><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Детали</th></tr></thead><tbody>';
    const aud = (appData.financeRec.audit || []).filter(function (x) { return x && x.finance_area_id === area.finance_area_id; }).slice(-40).reverse();
    if (!aud.length) html += '<tr><td colspan="4">Пока нет записей.</td></tr>';
    aud.forEach(function (x) {
        html += "<tr><td>" + escapeHtml(String(x.at || "").replace("T", " ").slice(0, 19)) + "</td><td>" + escapeHtml(x.user_id || "") + "</td><td>"
            + escapeHtml(x.kind || "") + "</td><td>" + escapeHtml(x.detail || x.comment || "") + "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    return html;
}

function frSaveOpening(sourceId, value) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const old = frOpeningValue(area.finance_area_id, w.period_key, sourceId);
    const next = frNum(value);
    let why = "";
    /* Причина нужна только при корректировке уже заданного ненулевого остатка. */
    if (Math.abs(old) >= 0.005) {
        why = prompt("Причина корректировки входящего остатка:");
        if (!String(why || "").trim()) { renderFinanceRec(); return; }
    } else {
        why = "начальный остаток";
    }
    let row = frOpeningRow(area.finance_area_id, w.period_key, sourceId);
    if (!row) {
        row = {
            finance_area_id: area.finance_area_id,
            object_id: area.object_id || area.finance_area_id,
            period_key: w.period_key,
            finance_source_id: sourceId,
            account_id: sourceId,
            data_source: FR_DATA_SOURCES.manual
        };
        frNormalizeBalanceRow(row);
        appData.financeRec.openings.push(row);
    }
    row.value = next;
    row.is_manual = true;
    row.carried = false;
    row.updated_at = new Date().toISOString();
    frAudit("opening_edit", { old_value: old, new_value: next, finance_source_id: sourceId, comment: String(why).trim() });
    saveApp();
    renderFinanceRec();
}

function openFrOpForm(id) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    if (rec && frOpKind(rec) === "transfer") {
        openFrTransferForm(id);
        return;
    }
    const kind = rec ? frOpKind(rec)
        : (frUi.opKind === "non_income" || frUi.workTab === "non_income" ? "non_income"
            : (frUi.opKind === "expense" || frUi.workTab === "expense" ? "expense" : "income"));
    frUi.opKind = kind;
    let html = "<h3>" + (rec ? "Операция" : "Новая операция") + "</h3>";
    html += '<input type="hidden" id="frOpId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frOpDate" type="date" value="' + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Тип движения</label><select id="frOpKind">'
        + '<option value="income"' + (kind === "income" ? " selected" : "") + ">Доход</option>"
        + '<option value="non_income"' + (kind === "non_income" ? " selected" : "") + ">Внедоходовое поступление</option>"
        + '<option value="expense"' + (kind === "expense" ? " selected" : "") + ">Расход</option>"
        + "</select></div>";
    html += '<div class="form-group"><label>Счёт / касса</label><select id="frOpSrc">' + frSources(area.finance_area_id).map(function (c) {
        return '<option value="' + c.finance_source_id + '"' + (rec && rec.finance_source_id === c.finance_source_id ? " selected" : "") + ">"
            + escapeHtml(c.name) + " (" + escapeHtml(frAccountTypeLabel(c.account_type)) + ")</option>";
    }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frOpAmt" value="' + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий / получатель</label><input id="frOpComment" value="' + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrOp()">Сохранить</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrOp() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const id = ((document.getElementById("frOpId") || {}).value || "");
    const amt = frNum((document.getElementById("frOpAmt") || {}).value);
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    let rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    const old = rec ? rec.amount : null;
    if (!rec) {
        rec = { finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })) };
        appData.financeRec.operations.push(rec);
    }
    const movement_kind = ((document.getElementById("frOpKind") || {}).value || "expense");
    const objectId = area.object_id || area.finance_area_id;
    rec.date = ((document.getElementById("frOpDate") || {}).value || w.start_date);
    const opWeek = frWeek(rec.date);
    rec.period_key = opWeek.period_key || w.period_key;
    rec.finance_category_id = rec.finance_category_id || "";
    rec.amount = amt;
    rec.comment = String(((document.getElementById("frOpComment") || {}).value || "")).trim();
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    const kind = movement_kind === "income" || movement_kind === "non_income" ? movement_kind : "expense";
    rec.type = kind === "expense" ? "out" : "in";
    frStampOperation(rec, {
        object_id: objectId,
        account_id: ((document.getElementById("frOpSrc") || {}).value || ""),
        op_type: kind,
        data_source: rec.data_source || FR_DATA_SOURCES.manual
    });
    frAudit(id ? "op_edit" : "op_add", { finance_operation_id: rec.finance_operation_id, old_value: old, new_value: amt, finance_source_id: rec.finance_source_id });
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
}

function frDeleteOp(id) {
    if (!frCanEditData()) return;
    const rec = (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; });
    if (!rec) return;
    if (frOpKind(rec) === "transfer" && rec.transit_id && rec.transit_role === "send") {
        const t = frTransitById(rec.transit_id);
        if (t && frNum(t.amount_closed) >= 0.005) {
            toast("Нельзя удалить: по этой отправке уже есть поступления", "error");
            return;
        }
        if (t) {
            t.amount_sent = 0;
            t.amount_closed = 0;
            t.amount_open = 0;
            t.status = "closed";
            t.updated_at = new Date().toISOString();
        }
    }
    rec.is_deleted = true;
    rec.deleted_at = new Date().toISOString();
    rec.deleted_by = typeof currentAuthUserId === "function" ? currentAuthUserId() : "";
    frAudit("op_delete", { finance_operation_id: id, old_value: rec.amount, finance_source_id: rec.finance_source_id, detail: rec.comment || "" });
    saveApp();
    renderFinanceRec();
}

function frOnSourceDragStart(e, accountId) {
    if (!frCanManageAccounts()) return;
    frUi._dragAccountId = accountId;
    try {
        e.dataTransfer.setData("text/plain", accountId);
        e.dataTransfer.effectAllowed = "move";
    } catch (err) {}
    const tr = e.target && e.target.closest ? e.target.closest("tr[data-account-id]") : null;
    if (tr) tr.classList.add("fr-dragging");
}

function frOnSourceDragOver(e) {
    if (!frCanManageAccounts() || !frUi._dragAccountId) return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = "move"; } catch (err) {}
    const tr = e.target && e.target.closest ? e.target.closest("tr[data-account-id]") : null;
    if (!tr) return;
    document.querySelectorAll("#frSourcesTable tr.fr-drag-over").forEach(function (r) {
        if (r !== tr) r.classList.remove("fr-drag-over");
    });
    tr.classList.add("fr-drag-over");
}

function frOnSourceDragLeave(e) {
    const tr = e.target && e.target.closest ? e.target.closest("tr[data-account-id]") : null;
    if (!tr) return;
    const rel = e.relatedTarget;
    if (rel && tr.contains(rel)) return;
    tr.classList.remove("fr-drag-over");
}

function frOnSourceDragEnd() {
    frUi._dragAccountId = "";
    document.querySelectorAll("#frSourcesTable tr.fr-dragging, #frSourcesTable tr.fr-drag-over").forEach(function (r) {
        r.classList.remove("fr-dragging", "fr-drag-over");
    });
}

function frOnSourceDrop(e, targetAccountId) {
    if (!frCanManageAccounts()) return;
    e.preventDefault();
    e.stopPropagation();
    const fromId = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || frUi._dragAccountId || "";
    /* не сбрасывать fromId до чтения — frOnSourceDragEnd очищает _dragAccountId */
    const savedFrom = fromId;
    frOnSourceDragEnd();
    if (!savedFrom || !targetAccountId || savedFrom === targetAccountId) return;
    frReorderSources(savedFrom, targetAccountId);
}

/**
 * Меняет только sort_order счетов текущего объекта (по account_id).
 * Не трогает остатки, операции и ID.
 */
function frReorderSources(fromAccountId, toAccountId) {
    if (!frCanManageAccounts()) {
        toast("Изменять порядок счетов может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area) return;
    ensureFinanceRec();
    const areaId = area.object_id || area.finance_area_id;
    const list = frSources(areaId, true).slice();
    const fromIdx = list.findIndex(function (s) {
        return (s.account_id || s.finance_source_id) === fromAccountId;
    });
    const toIdx = list.findIndex(function (s) {
        return (s.account_id || s.finance_source_id) === toAccountId;
    });
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;
    const moved = list.splice(fromIdx, 1)[0];
    list.splice(toIdx, 0, moved);
    list.forEach(function (s, i) {
        s.sort_order = (i + 1) * 10;
    });
    frAudit("source_reorder", {
        finance_area_id: areaId,
        detail: fromAccountId + " → " + toAccountId,
        finance_source_id: fromAccountId
    });
    saveApp();
    renderFinanceRec();
    toast("Порядок счетов сохранён", "success");
}

/** Снять назначение основной кассы, если деактивирован/удалён этот счёт. */
function frClearPrimaryIfSource(area, sourceId) {
    if (!area || !sourceId) return;
    if (area.primary_cash_source_id !== sourceId && area.primary_cash_account_id !== sourceId) return;
    area.primary_cash_source_id = "";
    area.primary_cash_account_id = "";
    const other = frSources(area.object_id || area.finance_area_id, false).find(function (s) {
        return s.finance_source_id !== sourceId && s.account_type === "cash";
    });
    if (other) {
        area.primary_cash_source_id = other.finance_source_id;
        area.primary_cash_account_id = other.finance_source_id;
    }
}

function frToggleInActualBalance(sourceId, enabled) {
    if (!frCanManageAccounts()) {
        toast("Изменять счета и кассы может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area || !sourceId) return;
    const areaId = area.object_id || area.finance_area_id;
    const rec = (appData.financeRec.sources || []).find(function (x) {
        return x && x.finance_source_id === sourceId
            && (x.object_id || x.finance_area_id) === areaId;
    });
    if (!rec) return;
    rec.in_actual_balance = !!enabled;
    frAudit("source_actual_flag", {
        finance_area_id: areaId,
        finance_source_id: sourceId,
        detail: rec.name + (enabled ? " → в факт. остатке" : " → только контроль")
    });
    saveApp();
    renderFinanceRec();
    toast(enabled ? "Участвует в фактическом остатке" : "Исключён из фактического остатка", "success");
}

function frActivateSource(sourceId) {
    if (!frCanManageAccounts()) {
        toast("Изменять счета и кассы может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area || !sourceId) return;
    const areaId = area.object_id || area.finance_area_id;
    const rec = (appData.financeRec.sources || []).find(function (x) {
        return x && x.finance_source_id === sourceId
            && (x.object_id || x.finance_area_id) === areaId;
    });
    if (!rec) return;
    rec.is_active = true;
    frAudit("source_activate", {
        finance_area_id: areaId,
        finance_source_id: sourceId,
        detail: rec.name || ""
    });
    saveApp();
    renderFinanceRec();
    toast("Счёт/касса активирован", "success");
}

function frDeactivateSource(sourceId) {
    if (!frCanManageAccounts()) {
        toast("Изменять счета и кассы может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area || !sourceId) return;
    const areaId = area.object_id || area.finance_area_id;
    const rec = (appData.financeRec.sources || []).find(function (x) {
        return x && x.finance_source_id === sourceId
            && (x.object_id || x.finance_area_id) === areaId;
    });
    if (!rec) return;
    if (rec.is_active === false) {
        toast("Счёт/касса уже неактивен", "info");
        return;
    }
    const name = rec.name || sourceId;
    if (!confirm("Деактивировать счёт/кассу «" + name + "» ?\n\nИстория сохранится. Для новых операций счёт предлагаться не будет.")) return;
    rec.is_active = false;
    frClearPrimaryIfSource(area, sourceId);
    frAudit("source_deactivate", {
        finance_area_id: areaId,
        finance_source_id: sourceId,
        detail: name
    });
    saveApp();
    renderFinanceRec();
    toast("Счёт/касса деактивирован", "success");
}

/**
 * Удаление счёта/кассы. Физически — только если account_id нигде не использовался.
 * Иначе предлагается деактивация.
 */
function frDeleteSource(sourceId) {
    if (!frCanManageAccounts()) {
        toast("Удалять счета и кассы может только Администратор", "error");
        return;
    }
    const area = frActiveArea();
    if (!area || !sourceId) return;
    ensureFinanceRec();
    const areaId = area.object_id || area.finance_area_id;
    const list = appData.financeRec.sources || [];
    const idx = list.findIndex(function (x) {
        return x && x.finance_source_id === sourceId
            && (x.object_id || x.finance_area_id) === areaId;
    });
    if (idx < 0) return;
    const rec = list[idx];
    const name = rec.name || sourceId;
    const aid = rec.account_id || rec.finance_source_id;
    if (frAccountIsUsed(areaId, aid)) {
        if (confirm(
            "Счёт/касса «" + name + "» уже используется в поступлениях, расходах, остатках, перемещениях или сверках.\n\n"
            + "Физическое удаление невозможно — история должна сохраниться.\n\nДеактивировать?"
        )) {
            rec.is_active = false;
            frClearPrimaryIfSource(area, sourceId);
            frAudit("source_deactivate", {
                finance_area_id: areaId,
                finance_source_id: sourceId,
                detail: name + " (вместо удаления)"
            });
            saveApp();
            renderFinanceRec();
            toast("Счёт/касса деактивирован", "success");
        }
        return;
    }
    if (!confirm("Удалить счёт/кассу «" + name + "» ?")) return;
    list.splice(idx, 1);
    list.forEach(function (s) {
        if (s && s.storage_account_id === aid) s.storage_account_id = "";
    });
    frClearPrimaryIfSource(area, sourceId);
    frAudit("source_delete", {
        finance_area_id: areaId,
        finance_source_id: sourceId,
        detail: name
    });
    saveApp();
    renderFinanceRec();
    toast("Счёт/касса удалён", "success");
}

function openFrDictForm(kind, id) {
    const isSource = kind === "source";
    if (isSource) {
        if (!frCanManageAccounts()) {
            toast("Изменять счета и кассы может только Администратор", "error");
            return;
        }
    } else if (!frCan("setup")) {
        return;
    }
    const area = frActiveArea();
    if (!area) { toast("Сначала выберите объект", "error"); return; }
    const list = isSource ? appData.financeRec.sources : appData.financeRec.categories;
    const rec = id ? list.find(function (x) {
        return x && (isSource ? x.finance_source_id : x.finance_category_id) === id;
    }) : null;
    if (isSource && id && rec) {
        const areaId = area.object_id || area.finance_area_id;
        if ((rec.object_id || rec.finance_area_id) !== areaId) {
            toast("Счёт принадлежит другому объекту", "error");
            return;
        }
    }
    let html = "<h3>" + (isSource ? "Счёт / касса" : "Статья движения") + "</h3>";
    html += '<input type="hidden" id="frDictKind" value="' + kind + '"><input type="hidden" id="frDictId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Название</label><input id="frDictName" value="' + escapeAttribute(rec ? rec.name : "") + '"></div>';
    if (isSource) {
        html += '<div class="form-group"><label>Тип</label><select id="frDictAccountType">';
        FR_ACCOUNT_TYPES.forEach(function (t) {
            html += '<option value="' + t.id + '"'
                + ((rec ? rec.account_type : "cash") === t.id ? " selected" : "") + ">"
                + escapeHtml(t.label) + "</option>";
        });
        html += "</select></div>";
        if (frIsTerritoryArea(area)) {
            const curDir = (rec && rec.revenue_direction) || "none";
            html += '<div class="form-group"><label>Направление выручки</label><select id="frDictRevenueDir">';
            FR_REVENUE_DIRS.forEach(function (d) {
                html += '<option value="' + d.id + '"' + (curDir === d.id ? " selected" : "") + ">"
                    + escapeHtml(d.label) + "</option>";
            });
            html += "</select></div>";
            html += '<div class="note">ОПТ / Розница / Склад — аналитика выручки Территории. '
                + "«Не относится» — для касс, денег в пути и прочих счетов без выручки. "
                + "Сам счёт остаётся местом хранения денег; перемещение не меняет выручку направлений.</div>";
        }
        /* Для Магазина Володарского — без «Доход/Внедоход» и без ОПТ/Розница/Склад */
        const inActual = !rec || rec.in_actual_balance !== false;
        html += '<div class="form-group"><label>'
            + '<input type="checkbox" id="frDictInActual"' + (inActual ? " checked" : "") + "> "
            + "Участвует в фактическом остатке</label>"
            + '<div class="fr-muted" style="margin-top:4px">Если включено — счёт попадает в блок «Фактические остатки на конец недели». '
            + "Контрольные ежедневные кассы обычно выключают.</div></div>";
        const selfId = rec ? (rec.account_id || rec.finance_source_id) : "";
        const curStorage = (rec && rec.storage_account_id) || "";
        html += '<div class="form-group"><label>Основное место хранения (необязательно)</label><select id="frDictStorage">';
        html += '<option value="">— не задано —</option>';
        frSources(area.finance_area_id, false).forEach(function (s) {
            const sid = s.finance_source_id;
            if (sid === selfId) return;
            html += '<option value="' + escapeAttribute(sid) + '"'
                + (curStorage === sid ? " selected" : "") + ">"
                + escapeHtml(s.name) + " (" + escapeHtml(frAccountTypeLabel(s.account_type)) + ")</option>";
        });
        html += "</select>";
        html += '<div class="fr-muted" style="margin-top:4px">Справочно: куда фактически сдаются деньги (например Торг 1 → Основная касса). '
            + "Операции перемещения автоматически <b>не создаются</b>.</div></div>";
    }
    html += '<div class="form-group"><label>Порядок</label><input id="frDictOrder" type="number" value="' + escapeAttribute(String(rec ? rec.sort_order : 10)) + '"></div>';
    if (kind === "cat") {
        html += '<div class="form-group"><label>Тип по умолчанию</label><select id="frDictType"><option value="any">Любой</option><option value="in"' + (rec && rec.default_type === "in" ? " selected" : "") + ">Приход</option><option value=\"out\"" + (rec && rec.default_type === "out" ? " selected" : "") + ">Расход</option></select></div>";
    }
    html += '<div class="form-group"><label>Статус</label><select id="frDictActive"><option value="1"' + (!rec || rec.is_active !== false ? " selected" : "") + ">Активен</option><option value=\"0\"" + (rec && rec.is_active === false ? " selected" : "") + ">Неактивен</option></select></div>";
    if (isSource) {
        html += '<div class="note">Стабильный account_id сохраняется при переименовании и деактивации. '
            + "Полное удаление возможно только если счёт нигде не использовался.</div>";
    } else {
        html += '<div class="note">Запись с историей не удаляется физически — только деактивируется. ID стабильный.</div>';
    }
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrDict()">Сохранить</button> ';
    if (isSource && id) {
        html += '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal();frDeleteSource(\''
            + escapeAttribute(id) + "')\">Удалить</button> ";
    }
    html += '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrDict() {
    const kind = ((document.getElementById("frDictKind") || {}).value || "source");
    if (kind === "source") {
        if (!frCanManageAccounts()) {
            toast("Изменять счета и кассы может только Администратор", "error");
            return;
        }
    } else if (!frCan("setup")) {
        return;
    }
    const id = ((document.getElementById("frDictId") || {}).value || "");
    const name = String((document.getElementById("frDictName") || {}).value || "").replace(/\s+/g, " ").trim();
    if (!name) { toast("Укажите название", "error"); return; }
    const area = frActiveArea();
    if (!area) return;
    const list = kind === "source" ? appData.financeRec.sources : appData.financeRec.categories;
    const key = kind === "source" ? "finance_source_id" : "finance_category_id";
    const prefix = kind === "source" ? "fsrc" : "fcat";
    const areaId = area.object_id || area.finance_area_id;
    let rec = id ? list.find(function (x) {
        return x && x[key] === id
            && (kind !== "source" || (x.object_id || x.finance_area_id) === areaId);
    }) : null;
    if (id && !rec) {
        toast("Запись не найдена в текущем объекте", "error");
        return;
    }
    if (!rec) {
        rec = { finance_area_id: areaId, object_id: areaId };
        if (kind === "source") {
            rec.in_actual_balance = true;
            rec.storage_account_id = "";
        }
        rec[key] = nextPrefixedId(prefix, list.map(function (x) { return x && x[key]; }));
        list.push(rec);
    }
    rec.name = name;
    rec.sort_order = Number((document.getElementById("frDictOrder") || {}).value || 10);
    rec.is_active = ((document.getElementById("frDictActive") || {}).value || "1") !== "0";
    if (kind === "source") {
        const typeId = ((document.getElementById("frDictAccountType") || {}).value || "cash");
        rec.account_type = FR_ACCOUNT_TYPES.some(function (t) { return t.id === typeId; }) ? typeId : "cash";
        rec.object_id = areaId;
        rec.finance_area_id = areaId;
        /* account_id стабилен: не пересоздаём при редактировании */
        rec.account_id = rec.account_id || rec.finance_source_id;
        rec.finance_source_id = rec.account_id;
        if (rec.external_id == null) rec.external_id = "";
        if (frIsTerritoryArea(area)) {
            const dir = ((document.getElementById("frDictRevenueDir") || {}).value || "none");
            rec.revenue_direction = FR_REVENUE_DIRS.some(function (d) { return d.id === dir; }) ? dir : "none";
            rec.shop_block = "";
        } else if (frIsShopArea(area)) {
            rec.revenue_direction = "";
            /* legacy non_income оставляем для фильтра сверки; остальные значения больше не используем */
            if (rec.shop_block !== "non_income") rec.shop_block = "";
        } else {
            rec.revenue_direction = "";
        }
        const inActualEl = document.getElementById("frDictInActual");
        rec.in_actual_balance = !!(inActualEl && inActualEl.checked);
        let storageId = String(((document.getElementById("frDictStorage") || {}).value) || "").trim();
        if (storageId === rec.finance_source_id || storageId === rec.account_id) storageId = "";
        if (storageId) {
            const host = frSourceByAccountId(areaId, storageId);
            if (!host || (host.object_id || host.finance_area_id) !== areaId) storageId = "";
        }
        rec.storage_account_id = storageId;
        if (rec.account_type === "cash" && !area.primary_cash_source_id) {
            area.primary_cash_source_id = rec.finance_source_id;
            area.primary_cash_account_id = rec.finance_source_id;
        }
        if (area.primary_cash_source_id === rec.finance_source_id && (rec.account_type !== "cash" || rec.is_active === false)) {
            frClearPrimaryIfSource(area, rec.finance_source_id);
        } else {
            area.primary_cash_account_id = area.primary_cash_source_id || "";
        }
        frAudit(id ? "source_edit" : "source_add", {
            finance_area_id: areaId,
            finance_source_id: rec.finance_source_id,
            detail: rec.name
        });
    }
    if (kind === "cat") {
        rec.finance_area_id = areaId;
        rec.object_id = areaId;
        rec.default_type = ((document.getElementById("frDictType") || {}).value || "any");
    }
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
    toast("Сохранено", "success");
}

function frAddFormulaTerm() {
    const box = document.getElementById("frFormulaBox");
    if (!box) return;
    const div = document.createElement("div");
    div.className = "fr-filters";
    div.innerHTML = '<select class="frSign"><option value="+">+</option><option value="-">-</option></select>'
        + '<select class="frMetric">'
        + '<option value="finm_opening">Начальные остатки</option>'
        + '<option value="finm_income">Доходы</option>'
        + '<option value="finm_non_income">Внедоходовые</option>'
        + '<option value="finm_inflow">Все поступления</option>'
        + '<option value="finm_outflow">Расходы</option>'
        + '<option value="finm_closing">Расчётный остаток</option>'
        + '<option value="finm_actual_closing">Фактический остаток</option>'
        + '<option value="finm_discrepancy">Расхождение</option>'
        + '<option value="finm_net">Доходы + внедоходовые − расходы</option>'
        + "</select>";
    box.appendChild(div);
}

function frSaveFormula() {
    if (!frCan("setup")) return;
    const area = frActiveArea();
    const box = document.getElementById("frFormulaBox");
    if (!box) return;
    const terms = [];
    box.querySelectorAll(".fr-filters").forEach(function (row) {
        const sign = (row.querySelector(".frSign") || {}).value || "+";
        const metric = (row.querySelector(".frMetric") || {}).value || "finm_income";
        terms.push({ metric_id: metric, sign: sign === "-" ? "-" : "+" });
    });
    let row = (appData.financeRec.formulas || []).find(function (f) { return f && f.finance_area_id === area.finance_area_id; });
    if (!row) { row = { finance_area_id: area.finance_area_id }; appData.financeRec.formulas.push(row); }
    row.terms = terms;
    saveApp();
    toast("Формула сверки сохранена", "success");
}

function frSetStatus(status) {
    if (status === "filled" && !frCan("fill")) return;
    if ((status === "review" || status === "reconciled") && !frCan("check")) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const st = frPeriodState(area.finance_area_id, w.period_key);
    if (st.status === "closed" && !frCan("setup")) return;
    const old = st.status;
    st.status = status;
    frAudit("status", { old_value: old, new_value: status });
    saveApp();
    renderFinanceRec();
}

function frClosePeriod() {
    const area = frActiveArea();
    const w = frEnsureWeek();
    const disc = frWeekDiscrepancy(area.finance_area_id, w.period_key);
    if (Math.abs(disc) >= 0.005) {
        if (!frCan("setup")) {
            toast("Период не может быть закрыт. Контрольная сверка не равна 0.", "error");
            return;
        }
        const why = prompt("Принудительное закрытие. Укажите причину:");
        if (!String(why || "").trim()) return;
        frAudit("force_close", { comment: why, old_value: disc });
    }
    if (!frCan("close") && !frCan("setup")) return;
    const st = frPeriodState(area.finance_area_id, w.period_key);
    st.status = "closed";
    st.closed_at = new Date().toISOString();
    st.closed_by = typeof currentAuthUserId === "function" ? currentAuthUserId() : "";
    frPushCarry(true);
    frAudit("close", { new_value: "closed" });
    saveApp();
    renderFinanceRec();
    toast("Период закрыт.");
}

function frReopenPeriod() {
    if (!frCan("setup")) return;
    const why = prompt("Причина открытия закрытого периода:");
    if (!String(why || "").trim()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const st = frPeriodState(area.finance_area_id, w.period_key);
    st.status = "review";
    frAudit("reopen", { comment: why });
    saveApp();
    renderFinanceRec();
}

function frPushCarry(silent) {
    const area = frActiveArea();
    const w = frEnsureWeek();
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, 1) : null;
    if (!next) return;
    frSources(area.finance_area_id, true).forEach(function (s) {
        const sid = s.finance_source_id;
        const wedActual = frDayActual(area.finance_area_id, w.end_date, sid);
        const value = wedActual != null
            ? wedActual
            : frCarryClosingValue(area.finance_area_id, w.period_key, w.end_date, sid);
        let row = frOpeningRow(area.finance_area_id, next.period_key, sid);
        if (!row) {
            row = {
                finance_area_id: area.finance_area_id,
                object_id: area.object_id || area.finance_area_id,
                period_key: next.period_key,
                finance_source_id: sid,
                account_id: sid,
                data_source: FR_DATA_SOURCES.manual
            };
            frNormalizeBalanceRow(row);
            appData.financeRec.openings.push(row);
        }
        if (row.is_manual && !silent) return;
        row.value = value;
        row.carried = true;
        row.is_manual = false;
    });
    if (!silent) {
        saveApp();
        toast("Остатки перенесены на следующую неделю.");
        renderFinanceRec();
    }
}

function frShiftWeekToCurrent() {
    const iso = typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(new Date()) : frUi.weekIso;
    frUi.weekIso = frWeek(iso).start_date;
    renderFinanceRec();
}

function openFrTransferForm(id) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const sources = frSources(area.finance_area_id, false);
    if (sources.length < 2) {
        toast("Нужно минимум два активных счёта", "error");
        return;
    }
    const rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    let html = "<h3>" + (rec ? "Перемещение" : "Новое перемещение") + "</h3>";
    html += '<input type="hidden" id="frTrId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frTrDate" type="date" value="'
        + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Откуда</label><select id="frTrFromModal">'
        + sources.map(function (s) {
            return '<option value="' + s.finance_source_id + '"'
                + (rec && rec.finance_source_id === s.finance_source_id ? " selected" : "") + ">"
                + escapeHtml(s.name) + "</option>";
        }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Куда</label><select id="frTrToModal">'
        + sources.map(function (s, i) {
            const sel = rec ? rec.finance_source_to_id === s.finance_source_id : i === 1;
            return '<option value="' + s.finance_source_id + '"' + (sel ? " selected" : "") + ">"
                + escapeHtml(s.name) + "</option>";
        }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frTrAmtModal" value="'
        + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frTrComModal" value="'
        + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="note">Перемещение не является доходом или расходом компании.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrTransfer()">Сохранить</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrTransfer() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const id = ((document.getElementById("frTrId") || {}).value || "");
    const from = ((document.getElementById("frTrFromModal") || {}).value || "");
    const to = ((document.getElementById("frTrToModal") || {}).value || "");
    const amt = frNum((document.getElementById("frTrAmtModal") || {}).value);
    const date = ((document.getElementById("frTrDate") || {}).value || w.start_date);
    const comment = String(((document.getElementById("frTrComModal") || {}).value) || "").trim();
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!from || !to || from === to) { toast("Выберите разные счета «откуда» и «куда»", "error"); return; }
    ensureFinanceRec();
    let rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    if (!rec) {
        rec = { finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })) };
        appData.financeRec.operations.push(rec);
    }
    const opWeek = frWeek(date);
    rec.period_key = opWeek.period_key || w.period_key;
    rec.date = date;
    rec.type = "transfer";
    rec.finance_category_id = "";
    rec.amount = amt;
    rec.comment = comment;
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    frStampOperation(rec, {
        object_id: area.object_id || area.finance_area_id,
        account_id: from,
        account_to_id: to,
        op_type: "transfer",
        data_source: rec.data_source || FR_DATA_SOURCES.manual,
        revenue_direction: "none"
    });
    const areaId = area.object_id || area.finance_area_id;
    const fromSrc = frSourceByAccountId(areaId, from);
    if (frIsTransitSource(fromSrc) && !rec.transit_role) {
        toast("Закрытие денег в пути — через «Закрыть / Деньги поступили»", "error");
        return;
    }
    frSyncTransitAfterTransfer(areaId, rec, !id);
    frAudit(id ? "transfer_edit" : "transfer_add", { finance_source_id: from, detail: to, new_value: amt });
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
}

function frHashStr(s) {
    let h = 2166136261;
    const str = String(s || "");
    for (let i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16);
}

function frExpenseFingerprint(areaId, sourceId, date, amount, payee) {
    return frHashStr([
        areaId,
        sourceId,
        String(date || ""),
        frRound(amount).toFixed(2),
        String(payee || "").replace(/\s+/g, " ").trim().toLowerCase()
    ].join("|"));
}

function frParseExpenseDate(raw) {
    const s = String(raw == null ? "" : raw).trim();
    if (!s) return "";
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return m[1] + "-" + m[2] + "-" + m[3];
    m = s.match(/^(\d{1,2})[./](\d{1,2})[./](\d{2,4})/);
    if (m) {
        let y = Number(m[3]);
        if (y < 100) y += 2000;
        const dd = String(m[1]).padStart(2, "0");
        const mm = String(m[2]).padStart(2, "0");
        return y + "-" + mm + "-" + dd;
    }
    m = s.match(/^(\d{1,2})[./](\d{1,2})$/);
    if (m) {
        const w = frEnsureWeek();
        const y = String(w.start_date || "").slice(0, 4) || String(new Date().getFullYear());
        return y + "-" + String(m[2]).padStart(2, "0") + "-" + String(m[1]).padStart(2, "0");
    }
    if (/^\d{5}(\.\d+)?$/.test(s)) {
        /* Excel serial date */
        const serial = Math.floor(Number(s));
        const epoch = new Date(Date.UTC(1899, 11, 30));
        epoch.setUTCDate(epoch.getUTCDate() + serial);
        return typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(epoch) : epoch.toISOString().slice(0, 10);
    }
    return "";
}

function frSplitImportLine(line) {
    const t = String(line || "");
    if (t.indexOf("\t") >= 0) return t.split("\t");
    if (t.indexOf(";") >= 0) return t.split(";");
    if (t.indexOf(",") >= 0) {
        const out = [];
        let cur = "";
        let q = false;
        for (let i = 0; i < t.length; i++) {
            const ch = t[i];
            if (ch === '"') { q = !q; continue; }
            if (ch === "," && !q) { out.push(cur); cur = ""; continue; }
            cur += ch;
        }
        out.push(cur);
        return out;
    }
    return t.split(/\s{2,}/);
}

function frNormalizeImportHeader(cell) {
    return String(cell == null ? "" : cell)
        .toLowerCase()
        .replace(/ё/g, "е")
        /* zero-width / BOM — удаляем (не заменяем пробелом), иначе «К[ZWSP]ому» ≠ «кому» */
        .replace(/[\u200B-\u200D\uFEFF]/g, "")
        /* неразрывные и прочие «широкие» пробелы → обычный пробел */
        .replace(/[\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]/g, " ")
        .replace(/[^a-zа-я0-9]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function frDetectImportColumns(headerCells) {
    const map = { date: -1, amount: -1, payee: -1, operation: -1 };
    headerCells.forEach(function (cell, i) {
        const h = frNormalizeImportHeader(cell);
        if (!h) return;
        if (map.date < 0 && (h === "дата" || h.indexOf("дата") === 0 || h === "date")) {
            map.date = i;
            return;
        }
        if (map.amount < 0 && (h.indexOf("сумм") === 0 || h === "amount" || h === "sum" || h === "suma")) {
            map.amount = i;
            return;
        }
        /* «Кому выдано» — до проверки «операция», чтобы не перепутать колонки */
        if (map.payee < 0 && (
            h.indexOf("кому") >= 0
            || h.indexOf("выдано") >= 0
            || h.indexOf("получател") >= 0
            || h.indexOf("контрагент") >= 0
            || h === "payee"
            || h === "фио"
            || h === "сотрудник"
        )) {
            map.payee = i;
            return;
        }
        /* «Хозяйственная операция» — необязательная колонка */
        if (map.operation < 0 && (
            h.indexOf("хозяйственн") >= 0
            || h.indexOf("хоз операц") >= 0
            || h === "операция"
            || h === "хоз операция"
            || h === "operation"
            || (h.indexOf("операц") >= 0 && h.indexOf("кому") < 0 && h.indexOf("выдано") < 0)
        )) {
            map.operation = i;
        }
    });
    const found = map.date >= 0 || map.amount >= 0 || map.payee >= 0 || map.operation >= 0;
    return found ? map : null;
}

/** Текст из ячейки: trim + обычные пробелы вместо NBSP. */
function frImportCellText(raw) {
    return String(raw == null ? "" : raw)
        .replace(/[\u00A0\u1680\u2000-\u200B\u202F\u205F\u3000\uFEFF]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

/**
 * Если колонка «Кому выдано» не распознана по заголовку —
 * взять текст из незанятых date/amount/operation ячеек.
 */
function frImportFallbackPayee(cells, colMap, date, amountIdx) {
    if (!cells || !cells.length) return "";
    const skip = {};
    if (colMap) {
        if (colMap.date >= 0) skip[colMap.date] = true;
        if (colMap.amount >= 0) skip[colMap.amount] = true;
        if (colMap.operation >= 0) skip[colMap.operation] = true;
        if (colMap.payee >= 0) skip[colMap.payee] = true;
    }
    if (amountIdx >= 0) skip[amountIdx] = true;
    const parts = [];
    cells.forEach(function (raw, i) {
        if (skip[i]) return;
        const c = frImportCellText(raw);
        if (!c) return;
        if (date && frParseExpenseDate(c) === date) return;
        const n = frNum(c);
        /* чистая сумма без букв — не «кому выдано» */
        if (Number.isFinite(n) && Math.abs(n) > 0 && !/[a-zа-я]/i.test(c)) return;
        parts.push(c);
    });
    return parts[0] || "";
}

function frParseExpenseImportText(text) {
    const lines = String(text || "").replace(/^\uFEFF/, "").split(/\r?\n/);
    const rows = [];
    let colMap = null;
    let headerConsumed = false;
    lines.forEach(function (line) {
        const cells = frSplitImportLine(line).map(function (c) { return frImportCellText(c); });
        if (!cells.length || cells.every(function (c) { return !c; })) return;
        if (!headerConsumed) {
            const detected = frDetectImportColumns(cells);
            if (detected && (detected.date >= 0 || detected.amount >= 0 || detected.payee >= 0)) {
                colMap = detected;
                headerConsumed = true;
                return;
            }
            headerConsumed = true;
        }
        let date = "";
        let amount = 0;
        let payeeName = "";
        let operation = "";
        let amountIdx = -1;
        if (colMap) {
            if (colMap.date >= 0) date = frParseExpenseDate(cells[colMap.date] || "");
            if (colMap.amount >= 0) {
                amount = Math.abs(frNum(cells[colMap.amount]));
                amountIdx = colMap.amount;
            }
            if (colMap.payee >= 0) payeeName = frImportCellText(cells[colMap.payee] || "");
            if (colMap.operation >= 0) operation = frImportCellText(cells[colMap.operation] || "");
            /* Колонка «Хозяйственная операция» необязательна — отсутствие не ошибка. */
            if (!payeeName) {
                payeeName = frImportFallbackPayee(cells, colMap, date, amountIdx);
            }
        } else {
            for (let i = 0; i < cells.length; i++) {
                const n = frNum(cells[i]);
                const d = frParseExpenseDate(cells[i]);
                if (!date && d) { date = d; continue; }
                if (amountIdx < 0 && /^-?\d/.test(String(cells[i] || "").replace(/\s/g, "")) && Number.isFinite(n) && Math.abs(n) > 0) {
                    amount = Math.abs(n);
                    amountIdx = i;
                }
            }
            payeeName = frImportFallbackPayee(cells, null, date, amountIdx);
            const leftovers = [];
            cells.forEach(function (c, i) {
                if (i === amountIdx) return;
                if (date && frParseExpenseDate(c) === date) return;
                if (c && c !== payeeName) leftovers.push(c);
            });
            operation = leftovers[0] || "";
        }
        if (!(amount > 0)) return;
        if (!date) date = frEnsureWeek().start_date;
        rows.push({
            date: date,
            amount: frRound(amount),
            payee: payeeName || "",
            payee_name: payeeName || "",
            operation: operation || "",
            raw: cells.join(" | ")
        });
    });
    return rows;
}

function frIsoToRuDate(iso) {
    const d = frNormDate(iso);
    if (!d || d.length < 10) return "";
    return d.slice(8, 10) + "." + d.slice(5, 7) + "." + d.slice(0, 4);
}

function frRuDateToIso(raw) {
    const s = String(raw == null ? "" : raw).trim();
    if (!s) return "";
    return frNormDate(frParseExpenseDate(s) || s);
}

function frDefaultExpenseImportDate() {
    if (frUi.importExpenseDate && frNormDate(frUi.importExpenseDate)) {
        return frNormDate(frUi.importExpenseDate);
    }
    const today = typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(new Date()) : "";
    const w = frEnsureWeek();
    const days = frWeekDates(w);
    if (today && days.indexOf(today) !== -1) return today;
    return w.start_date || today || "";
}

function openFrExcelImport(dayIso) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const sources = frSources(area.finance_area_id, false);
    if (!sources.length) {
        toast("Сначала создайте счета в настройках", "error");
        return;
    }
    const primary = frPrimaryCashId(area.finance_area_id);
    if (!frUi.importSourceId || !sources.some(function (s) { return s.finance_source_id === frUi.importSourceId; })) {
        frUi.importSourceId = primary || sources[0].finance_source_id;
    }
    const preset = frNormDate(dayIso) || frDefaultExpenseImportDate();
    frUi.importExpenseDate = preset;
    let html = "<h3>Загрузить расходы из Excel</h3>";
    html += '<div class="form-group"><label>Дата расходов</label>'
        + '<input type="text" id="frImportExpenseDate" placeholder="дд.мм.гггг" inputmode="numeric" '
        + 'value="' + escapeAttribute(frIsoToRuDate(preset)) + '" style="max-width:160px">'
        + '<div class="fr-muted" style="margin-top:4px">Дата из Excel не меняется — поле нужно для контроля. '
        + "Если в файле есть другие даты, загрузка будет остановлена.</div></div>";
    html += '<div class="form-group"><label>С какого счёта оплачены расходы</label><select id="frImportSrc" onchange="frUi.importSourceId=this.value">';
    sources.forEach(function (s) {
        const mark = (primary === s.finance_source_id ? " — основная касса" : "");
        html += '<option value="' + s.finance_source_id + '"'
            + (frUi.importSourceId === s.finance_source_id ? " selected" : "") + ">"
            + escapeHtml(s.name) + " (" + escapeHtml(frAccountTypeLabel(s.account_type)) + ")"
            + escapeHtml(mark) + "</option>";
    });
    html += "</select></div>";
    html += '<div class="note">Для обычного наличного расходника выберите основную кассу (подставлена по умолчанию). '
        + "Можно выбрать файл .xlsx / CSV или вставить таблицу (Ctrl+V). "
        + "Если в поле вставки есть данные — импортируется вставка, даже если выбран файл. "
        + "Колонки распознаются по заголовкам: Дата, Сумма, Кому выдано; Хозяйственная операция — необязательна. "
        + "Порядок колонок в Excel не важен. "
        + "В программе: Кому выдано | Сумма | Хозяйственная операция. Строки без суммы игнорируются.</div>";
    html += '<div class="form-group"><label>Файл (.xlsx, CSV, TXT)</label>'
        + '<input type="file" id="frImportFile" accept=".csv,.txt,.tsv,.xlsx,.xls"></div>';
    html += '<div class="form-group"><label>Или вставьте таблицу из Excel (Ctrl+V)</label>'
        + '<textarea id="frImportPaste" rows="8" style="width:100%" placeholder="Дата\tСумма\tКому выдано"></textarea></div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="frRunExpenseImport()">Импортировать</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function frShowImportDateMismatch(expectedIso, datesInFile) {
    const expectedRu = frIsoToRuDate(expectedIso) || expectedIso;
    const foreign = datesInFile.filter(function (d) { return d !== expectedIso; });
    let html = "<h3>Проверьте даты в файле</h3>";
    html += "<p>Вы выбрали <b>" + escapeHtml(expectedRu) + "</b>.</p>";
    if (foreign.length === 1) {
        html += "<p>В файле также найдены операции за <b>" + escapeHtml(frIsoToRuDate(foreign[0]) || foreign[0])
            + "</b>.</p>";
    } else {
        html += "<p>В файле найдены операции за другие даты:</p><ul>";
        foreign.forEach(function (d) {
            html += "<li><b>" + escapeHtml(frIsoToRuDate(d) || d) + "</b></li>";
        });
        html += "</ul>";
    }
    html += '<div class="note">Все даты в файле: '
        + datesInFile.map(function (d) { return frIsoToRuDate(d) || d; }).map(escapeHtml).join(", ")
        + ".</div>";
    html += "<p>Файл <b>не загружен</b>. Даты из Excel не подменяются. "
        + "Исправьте файл или выберите другую «Дату расходов» и повторите импорт.</p>";
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="closeMgmtModal()">Понятно</button></div>';
    openMgmtModal(html);
}

function frInflateRaw(bytes) {
    if (typeof DecompressionStream === "undefined") {
        return Promise.reject(new Error("Браузер не поддерживает распаковку ZIP"));
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Response(stream).arrayBuffer().then(function (ab) {
        return new Uint8Array(ab);
    });
}

function frZipReadFiles(arrayBuffer) {
    const u8 = new Uint8Array(arrayBuffer);
    const view = new DataView(arrayBuffer);
    let eocd = -1;
    const minScan = Math.max(0, u8.length - 65557);
    for (let i = u8.length - 22; i >= minScan; i--) {
        if (view.getUint32(i, true) === 0x06054b50) {
            eocd = i;
            break;
        }
    }
    if (eocd < 0) return Promise.reject(new Error("Файл не является ZIP/XLSX"));
    const cdEntries = view.getUint16(eocd + 10, true);
    let p = view.getUint32(eocd + 16, true);
    const jobs = [];
    for (let e = 0; e < cdEntries; e++) {
        if (p + 46 > u8.length || view.getUint32(p, true) !== 0x02014b50) break;
        const method = view.getUint16(p + 10, true);
        const compSize = view.getUint32(p + 20, true);
        const nameLen = view.getUint16(p + 28, true);
        const extraLen = view.getUint16(p + 30, true);
        const commentLen = view.getUint16(p + 32, true);
        const localOff = view.getUint32(p + 42, true);
        const name = new TextDecoder("utf-8").decode(u8.subarray(p + 46, p + 46 + nameLen));
        p += 46 + nameLen + extraLen + commentLen;
        const lNameLen = view.getUint16(localOff + 26, true);
        const lExtraLen = view.getUint16(localOff + 28, true);
        const dataStart = localOff + 30 + lNameLen + lExtraLen;
        const data = u8.subarray(dataStart, dataStart + compSize);
        jobs.push(
            (method === 0
                ? Promise.resolve(data)
                : method === 8
                    ? frInflateRaw(data)
                    : Promise.reject(new Error("Неподдерживаемое сжатие в XLSX: " + method))
            ).then(function (raw) {
                return { name: name, data: raw };
            })
        );
    }
    return Promise.all(jobs).then(function (list) {
        const map = {};
        list.forEach(function (item) {
            const key = String(item.name || "").replace(/\\/g, "/");
            map[key] = item.data;
            /* На случай доступа по исходному имени. */
            if (key !== item.name) map[item.name] = item.data;
        });
        return map;
    });
}

function frXmlDecodeEntities(s) {
    return String(s || "")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'")
        .replace(/&#(\d+);/g, function (_, n) { return String.fromCharCode(Number(n)); })
        .replace(/&#x([0-9a-fA-F]+);/g, function (_, h) { return String.fromCharCode(parseInt(h, 16)); })
        .replace(/&amp;/g, "&");
}

function frBytesToUtf8(bytes) {
    return new TextDecoder("utf-8").decode(bytes);
}

function frXlsxSharedStrings(xml) {
    const out = [];
    const re = /<si\b[^>]*>([\s\S]*?)<\/si>/gi;
    let m;
    while ((m = re.exec(xml))) {
        const parts = [];
        const tre = /<t\b[^>]*>([\s\S]*?)<\/t>/gi;
        let tm;
        while ((tm = tre.exec(m[1]))) parts.push(frXmlDecodeEntities(tm[1]));
        out.push(parts.join(""));
    }
    return out;
}

function frXlsxColRow(ref) {
    const m = String(ref || "").match(/^([A-Z]+)(\d+)$/i);
    if (!m) return null;
    let col = 0;
    const letters = m[1].toUpperCase();
    for (let i = 0; i < letters.length; i++) col = col * 26 + (letters.charCodeAt(i) - 64);
    return { col: col - 1, row: Number(m[2]) - 1 };
}

function frXlsxSheetToMatrix(sheetXml, shared) {
    const rows = {};
    const cellRe = /<c\b([^>]*)>([\s\S]*?)<\/c>|<c\b([^>]*)\/>/gi;
    let m;
    while ((m = cellRe.exec(sheetXml))) {
        const attrs = m[1] || m[3] || "";
        const body = m[2] || "";
        const refM = attrs.match(/\br="([^"]+)"/i);
        if (!refM) continue;
        const pos = frXlsxColRow(refM[1]);
        if (!pos) continue;
        const typeM = attrs.match(/\bt="([^"]+)"/i);
        const type = typeM ? typeM[1] : "";
        let val = "";
        if (type === "inlineStr") {
            const tM = body.match(/<t\b[^>]*>([\s\S]*?)<\/t>/i);
            val = tM ? frXmlDecodeEntities(tM[1]) : "";
        } else {
            const vM = body.match(/<v\b[^>]*>([\s\S]*?)<\/v>/i);
            const raw = vM ? frXmlDecodeEntities(vM[1]) : "";
            if (type === "s") {
                const idx = Number(raw);
                val = shared && shared[idx] != null ? String(shared[idx]) : raw;
            } else {
                val = raw;
            }
        }
        if (!rows[pos.row]) rows[pos.row] = {};
        rows[pos.row][pos.col] = val;
    }
    const rowIdx = Object.keys(rows).map(Number).sort(function (a, b) { return a - b; });
    return rowIdx.map(function (ri) {
        const cols = rows[ri];
        const maxCol = Math.max.apply(null, Object.keys(cols).map(Number).concat([-1]));
        const line = [];
        for (let c = 0; c <= maxCol; c++) line.push(cols[c] != null ? String(cols[c]) : "");
        return line;
    });
}

function frXlsxPickSheetPath(files) {
    const names = Object.keys(files).map(function (n) { return String(n || "").replace(/\\/g, "/"); });
    const prefer = names.filter(function (n) {
        return /^xl\/worksheets\/sheet\d+\.xml$/i.test(n);
    }).sort(function (a, b) {
        const na = Number((a.match(/sheet(\d+)/i) || [])[1] || 99);
        const nb = Number((b.match(/sheet(\d+)/i) || [])[1] || 99);
        return na - nb;
    });
    return prefer[0] || "";
}

function frXlsxArrayBufferToText(arrayBuffer) {
    return frZipReadFiles(arrayBuffer).then(function (files) {
        const ssBytes = files["xl/sharedStrings.xml"] || files["xl\\sharedStrings.xml"];
        const shared = ssBytes ? frXlsxSharedStrings(frBytesToUtf8(ssBytes)) : [];
        const sheetPath = frXlsxPickSheetPath(files);
        if (!sheetPath) throw new Error("В книге нет листа worksheet");
        const sheetBytes = files[sheetPath] || files[sheetPath.replace(/\//g, "\\")];
        if (!sheetBytes) throw new Error("Не удалось открыть лист " + sheetPath);
        const matrix = frXlsxSheetToMatrix(frBytesToUtf8(sheetBytes), shared);
        if (!matrix.length) throw new Error("Лист пустой");
        return matrix.map(function (row) {
            return row.map(function (cell) {
                return String(cell == null ? "" : cell).replace(/\t/g, " ").replace(/\r?\n/g, " ");
            }).join("\t");
        }).join("\n");
    });
}

function frRunExpenseImport() {
    if (!frCanEditData()) return;
    const sourceId = ((document.getElementById("frImportSrc") || {}).value || frUi.importSourceId || "");
    if (!sourceId) { toast("Выберите счёт списания", "error"); return; }
    frUi.importSourceId = sourceId;
    const expectedDate = frRuDateToIso(((document.getElementById("frImportExpenseDate") || {}).value || ""));
    if (!expectedDate) {
        toast("Укажите дату расходов (дд.мм.гггг)", "error");
        return;
    }
    frUi.importExpenseDate = expectedDate;
    const fileInput = document.getElementById("frImportFile");
    const file = fileInput && fileInput.files && fileInput.files[0];
    const paste = String(((document.getElementById("frImportPaste") || {}).value) || "");

    /* Приоритет: если есть вставка — импортируем её, выбранный .xlsx не блокирует. */
    if (paste.trim()) {
        frApplyExpenseImport(paste, sourceId, {
            file_name: "paste",
            file_size: paste.length,
            file_mtime: Date.now(),
            from_paste: true,
            expected_date: expectedDate
        });
        return;
    }

    if (!file) {
        toast("Выберите файл или вставьте данные из Excel", "error");
        return;
    }

    const name = String(file.name || "").toLowerCase();
    const meta = {
        file_name: file.name || "",
        file_size: file.size || 0,
        file_mtime: file.lastModified || 0,
        expected_date: expectedDate
    };

    if (name.endsWith(".xlsx")) {
        const reader = new FileReader();
        reader.onload = function () {
            frXlsxArrayBufferToText(reader.result).then(function (text) {
                frApplyExpenseImport(text, sourceId, meta);
            }).catch(function (err) {
                console.error(err);
                toast("Не удалось прочитать .xlsx: " + (err && err.message ? err.message : "ошибка"), "error");
            });
        };
        reader.onerror = function () { toast("Ошибка чтения файла", "error"); };
        reader.readAsArrayBuffer(file);
        return;
    }

    if (name.endsWith(".xls")) {
        toast("Старый формат .xls не поддерживается. Сохраните файл как .xlsx или вставьте таблицу (Ctrl+V).", "error");
        return;
    }

    const reader = new FileReader();
    reader.onload = function () {
        try {
            frApplyExpenseImport(String(reader.result || ""), sourceId, meta);
        } catch (err) {
            console.error(err);
            toast("Не удалось прочитать файл", "error");
        }
    };
    reader.onerror = function () { toast("Ошибка чтения файла", "error"); };
    reader.readAsText(file, "UTF-8");
}

function frApplyExpenseImport(text, sourceId, meta) {
    const area = frActiveArea();
    const w = frEnsureWeek();
    ensureFinanceRec();
    meta = meta || {};
    const rows = frParseExpenseImportText(text);
    if (!rows.length) {
        toast("Не найдено строк с суммой", "error");
        return;
    }
    const expectedDate = frNormDate(meta.expected_date);
    if (!expectedDate) {
        toast("Укажите дату расходов (дд.мм.гггг)", "error");
        return;
    }
    /* Контроль даты: даты из Excel не подменяем, но чужие даты блокируют загрузку. */
    const dateSet = {};
    rows.forEach(function (r) {
        if (!(r.amount > 0)) return;
        const d = frNormDate(r.date);
        if (d) dateSet[d] = true;
    });
    const datesInFile = Object.keys(dateSet).sort();
    if (!datesInFile.length) {
        toast("В файле не найдены даты операций", "error");
        return;
    }
    const hasForeign = datesInFile.some(function (d) { return d !== expectedDate; });
    if (hasForeign) {
        frShowImportDateMismatch(expectedDate, datesInFile);
        return;
    }
    const batchKey = frHashStr([
        area.finance_area_id,
        sourceId,
        expectedDate,
        meta.file_name || "",
        meta.file_size || 0,
        meta.file_mtime || 0,
        frHashStr(String(text || "").replace(/\s+/g, " "))
    ].join("|"));
    const existingBatch = (appData.financeRec.import_batches || []).find(function (b) {
        return b && b.batch_key === batchKey && b.finance_area_id === area.finance_area_id;
    });
    if (existingBatch) {
        toast("Этот файл (или такой же набор строк) уже импортирован — повторно не загружен.", "error");
        return;
    }
    const existingFp = {};
    (appData.financeRec.operations || []).forEach(function (o) {
        if (!o || o.is_deleted) return;
        if (o.finance_area_id !== area.finance_area_id) return;
        if (o.import_fingerprint) existingFp[o.import_fingerprint] = true;
        if (frOpKind(o) === "expense") {
            existingFp[frExpenseFingerprint(o.finance_area_id, o.finance_source_id, o.date, o.amount, o.comment)] = true;
        }
    });
    const batchId = nextPrefixedId("fimp", (appData.financeRec.import_batches || []).map(function (x) { return x && x.import_batch_id; }));
    let added = 0;
    let skippedDup = 0;
    let skippedNoAmt = 0;
    rows.forEach(function (r) {
        if (!(r.amount > 0)) { skippedNoAmt++; return; }
        const fp = frExpenseFingerprint(area.finance_area_id, sourceId, r.date, r.amount, r.payee_name || r.payee);
        if (existingFp[fp]) { skippedDup++; return; }
        existingFp[fp] = true;
        const opWeek = frWeek(r.date);
        const payeeText = String(r.payee_name || r.payee || "").trim();
        const operationText = String(r.operation || "").trim();
        const rec = {
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
            period_key: opWeek.period_key || w.period_key,
            date: r.date,
            type: "out",
            finance_category_id: "",
            amount: r.amount,
            comment: payeeText,
            payee: payeeText,
            operation: operationText,
            import_fingerprint: fp,
            import_batch_id: batchId,
            is_deleted: false,
            created_at: new Date().toISOString()
        };
        frStampOperation(rec, {
            object_id: area.object_id || area.finance_area_id,
            account_id: sourceId,
            op_type: "expense",
            data_source: (meta && meta.from_paste) ? FR_DATA_SOURCES.paste : FR_DATA_SOURCES.excel
        });
        appData.financeRec.operations.push(rec);
        added++;
    });
    if (added > 0) {
        appData.financeRec.import_batches.push({
            import_batch_id: batchId,
            batch_key: batchKey,
            finance_area_id: area.finance_area_id,
            object_id: area.object_id || area.finance_area_id,
            finance_source_id: sourceId,
            account_id: sourceId,
            data_source: (meta && meta.from_paste) ? FR_DATA_SOURCES.paste : FR_DATA_SOURCES.excel,
            file_name: meta.file_name || "",
            rows_added: added,
            rows_dup: skippedDup,
            at: new Date().toISOString()
        });
        frAudit("expense_import", {
            finance_source_id: sourceId,
            detail: (meta.file_name || "paste") + " +" + added + " / dup " + skippedDup,
            new_value: added
        });
        saveApp();
        closeMgmtModal();
        frUi.workTab = "expense";
        renderFinanceRec();
        toast("Импортировано расходов: " + added
            + (skippedDup ? ("; пропущено дублей: " + skippedDup) : "")
            + (skippedNoAmt ? ("; без суммы: " + skippedNoAmt) : ""), "success");
    } else {
        toast(skippedDup ? "Все строки уже были загружены ранее (дубли)." : "Нечего импортировать.", "error");
    }
}

function financeRecFolderCardHtml() {
    return folderCardHtml("summary-folder", "financeRec", "Финконтроль", "Финансовая сверка поступлений, расходов и остатков", "");
}
