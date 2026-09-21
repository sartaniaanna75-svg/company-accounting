let frUi = { areaId: "", weekIso: "", inner: "work", opKind: "all", sourceId: "", catId: "", date: "", q: "" };

function isFinanceRecMode() {
    return isSummary() && currentEntry === "financeRec";
}

function isFrNavSection(id) {
    return id === "financeRec" || id === "financeRec-settings";
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

function frMoney(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "0,00 ₽";
    return (typeof money2 === "function" ? money2(x) : x.toFixed(2) + " ₽");
}

function frNum(v) {
    const n = Number(String(v == null ? "" : v).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? Math.round(n * 100) / 100 : 0;
}

function ensureFinanceRec() {
    if (!appData || typeof appData !== "object") return null;
    if (!appData.financeRec || typeof appData.financeRec !== "object") {
        appData.financeRec = {};
    }
    const root = appData.financeRec;
    if (!Array.isArray(root.areas)) {
        root.areas = [
            { finance_area_id: "farea_shop", name: "Магазин Володарского", sort_order: 10, is_active: true },
            { finance_area_id: "farea_territory", name: "Территория", sort_order: 20, is_active: true }
        ];
    }
    if (!Array.isArray(root.sources)) root.sources = [];
    if (!Array.isArray(root.categories)) root.categories = [];
    if (!Array.isArray(root.operations)) root.operations = [];
    if (!Array.isArray(root.openings)) root.openings = [];
    if (!Array.isArray(root.period_states)) root.period_states = [];
    if (!Array.isArray(root.formulas)) root.formulas = [];
    if (!Array.isArray(root.audit)) root.audit = [];
    root.areas.forEach(function (a, i) {
        if (!a || typeof a !== "object") return;
        if (!a.finance_area_id) a.finance_area_id = "farea_" + (i + 1);
        if (a.is_active == null) a.is_active = true;
        if (a.sort_order == null) a.sort_order = (i + 1) * 10;
    });
    /* Справочники пустые: названия задаёт пользователь, ключ — finance_*_id. */
    root.areas.forEach(function (a) {
        if (!a) return;
        if (!root.formulas.some(function (f) { return f && f.finance_area_id === a.finance_area_id; })) {
            root.formulas.push({
                finance_area_id: a.finance_area_id,
                terms: [
                    { metric_id: "finm_inflow", sign: "+" },
                    { metric_id: "finm_outflow", sign: "-" }
                ]
            });
        }
    });
    return root;
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
    return list.find(function (a) { return a.finance_area_id === frUi.areaId; }) || list[0];
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

function frCarryIfNeeded(areaId, periodKey) {
    const sources = frSources(areaId, true);
    sources.forEach(function (s) {
        if (frOpeningRow(areaId, periodKey, s.finance_source_id)) return;
        const prevKey = frPrevPeriodKey(periodKey);
        if (!prevKey) return;
        const tot = frSourceTotals(areaId, prevKey, s.finance_source_id);
        ensureFinanceRec();
        appData.financeRec.openings.push({
            finance_area_id: areaId,
            period_key: periodKey,
            finance_source_id: s.finance_source_id,
            value: tot.closing,
            is_manual: false,
            carried: true
        });
    });
}

function frOps(areaId, periodKey) {
    return ((appData.financeRec && appData.financeRec.operations) || []).filter(function (o) {
        return o && !o.is_deleted && o.finance_area_id === areaId && o.period_key === periodKey;
    });
}

function frSourceTotals(areaId, periodKey, sourceId) {
    const opening = frOpeningValue(areaId, periodKey, sourceId);
    let inflow = 0, outflow = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        if (o.finance_source_id !== sourceId) return;
        const amt = frNum(o.amount);
        if (o.type === "in") inflow += amt;
        else outflow += amt;
    });
    return { opening: opening, inflow: inflow, outflow: outflow, closing: Math.round((opening + inflow - outflow) * 100) / 100 };
}

function frAreaTotals(areaId, periodKey) {
    const acc = { opening: 0, inflow: 0, outflow: 0, closing: 0 };
    frSources(areaId, true).forEach(function (s) {
        const t = frSourceTotals(areaId, periodKey, s.finance_source_id);
        acc.opening += t.opening;
        acc.inflow += t.inflow;
        acc.outflow += t.outflow;
        acc.closing += t.closing;
    });
    acc.opening = Math.round(acc.opening * 100) / 100;
    acc.inflow = Math.round(acc.inflow * 100) / 100;
    acc.outflow = Math.round(acc.outflow * 100) / 100;
    acc.closing = Math.round(acc.closing * 100) / 100;
    return acc;
}

function frMetricValue(metricId, areaId, periodKey) {
    const t = frAreaTotals(areaId, periodKey);
    if (metricId === "finm_opening") return t.opening;
    if (metricId === "finm_inflow") return t.inflow;
    if (metricId === "finm_outflow") return t.outflow;
    if (metricId === "finm_closing") return t.closing;
    if (metricId === "finm_net") return Math.round((t.inflow - t.outflow) * 100) / 100;
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
        { metric_id: "finm_inflow", sign: "+" },
        { metric_id: "finm_outflow", sign: "-" }
    ];
}

function frDiscrepancy(areaId, periodKey) {
    let v = 0;
    frFormulaTerms(areaId).forEach(function (term) {
        const n = frMetricValue(term.metric_id, areaId, periodKey);
        v += (term.sign === "-" ? -n : n);
    });
    return Math.round(v * 100) / 100;
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

function setFrArea(id) {
    frUi.areaId = id;
    renderFinanceRec();
}

function setFrInner(tab) {
    frUi.inner = tab === "settings" ? "settings" : "work";
    currentSection = frUi.inner === "settings" ? "financeRec-settings" : "financeRec";
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
        const area = frActiveArea();
        const w = frEnsureWeek();
        if (!area) {
            host.innerHTML = '<div class="note">Нет доступных направлений.</div>';
            return;
        }
        frCarryIfNeeded(area.finance_area_id, w.period_key);
        const tot = frAreaTotals(area.finance_area_id, w.period_key);
        const disc = frDiscrepancy(area.finance_area_id, w.period_key);
        const balanced = Math.abs(disc) < 0.005;
        const st = frPeriodState(area.finance_area_id, w.period_key);
        const closed = st.status === "closed";
        const canFill = frCanEditData();
        const canSetup = frCan("setup");
        const inner = (currentSection === "financeRec-settings" || frUi.inner === "settings") ? "settings" : "work";
        frUi.inner = inner;
        let html = '<div class="fr-wrap">';
        html += '<div class="fr-crumb">Финансовая сверка → <b>' + escapeHtml(area.name) + "</b></div>";
        html += '<div class="fr-tabs">';
        frAreas().forEach(function (a) {
            html += '<button type="button" class="metrics-tab' + (a.finance_area_id === area.finance_area_id ? " active" : "") + '" onclick="setFrArea(\'' + a.finance_area_id + "')\">"
                + escapeHtml(a.name) + "</button>";
        });
        html += "</div>";
        html += '<div class="fr-weekbar">';
        html += '<span class="fr-week-label">Отчётная неделя: ' + escapeHtml((typeof mgmtFmtRu === "function" ? mgmtFmtRu(w.start_date) : w.start_date) + " — " + (typeof mgmtFmtRu === "function" ? mgmtFmtRu(w.end_date) : w.end_date) + " (чт–ср)") + "</span>";
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(-1)">← Пред. неделя</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(1)">След. неделя →</button>';
        html += '<span class="fr-status-pill">' + escapeHtml(frStatusLabel(st.status)) + "</span>";
        html += '<button type="button" class="metrics-tab' + (inner === "work" ? " active" : "") + '" onclick="setFrInner(\'work\')">Сверка</button>';
        if (canSetup) html += '<button type="button" class="metrics-tab' + (inner === "settings" ? " active" : "") + '" onclick="setFrInner(\'settings\')">Настройки</button>';
        html += "</div>";
        if (inner === "settings") {
            html += renderFrSettingsHtml(area);
            host.innerHTML = html + "</div>";
            return;
        }
        html += '<div class="fr-cards">';
        html += '<div class="fr-card"><span>Остаток на начало</span><b>' + frMoney(tot.opening) + "</b></div>";
        html += '<div class="fr-card"><span>Поступило за неделю</span><b class="fr-in">' + frMoney(tot.inflow) + "</b></div>";
        html += '<div class="fr-card"><span>Расходы за неделю</span><b class="fr-out">' + frMoney(tot.outflow) + "</b></div>";
        html += '<div class="fr-card ' + (balanced ? "fr-ok" : "fr-bad") + '"><span>Расхождение</span><b>' + frMoney(disc) + "</b><span>" + (balanced ? "✓ Сверено" : "⚠ Не сошлось") + "</span></div>";
        html += "</div>";
        html += '<div class="fr-layout">';
        html += "<div>";
        html += renderFrSourcesTable(area.finance_area_id, w.period_key, canFill && !closed);
        html += renderFrOpsTable(area.finance_area_id, w.period_key, canFill && !closed);
        html += "</div><div>";
        html += renderFrControl(area.finance_area_id, w.period_key, tot, disc, balanced, st, closed);
        html += "</div></div></div>";
        host.innerHTML = html;
    } catch (err) {
        console.error(err);
        const host = document.getElementById("financeRecHost");
        if (host) host.innerHTML = '<div class="note">Ошибка финансовой сверки. Остальные разделы Work Time не затронуты.</div>';
    }
}

function renderFrSourcesTable(areaId, periodKey, canEdit) {
    let html = '<div class="fr-box" style="margin-bottom:12px"><h3>Источники средств</h3><div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    html += "<th>Источник средств</th><th class=\"fr-num\">Остаток на начало</th><th class=\"fr-num\">Поступило</th><th class=\"fr-num\">Выдано / расход</th><th class=\"fr-num\">Остаток на конец</th></tr></thead><tbody>";
    const acc = { opening: 0, inflow: 0, outflow: 0, closing: 0 };
    frSources(areaId, false).forEach(function (s) {
        const t = frSourceTotals(areaId, periodKey, s.finance_source_id);
        acc.opening += t.opening; acc.inflow += t.inflow; acc.outflow += t.outflow; acc.closing += t.closing;
        html += "<tr><td>" + escapeHtml(s.name) + "</td>";
        html += '<td class="fr-num">' + (canEdit
            ? '<input style="width:120px;text-align:right" value="' + escapeAttribute(String(t.opening)) + '" onchange="frSaveOpening(\'' + s.finance_source_id + "',this.value)\">"
            : frMoney(t.opening)) + "</td>";
        html += '<td class="fr-num fr-in">' + frMoney(t.inflow) + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(t.outflow) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.closing) + "</td></tr>";
    });
    html += '</tbody><tfoot><tr><td>ИТОГО</td><td class="fr-num">' + frMoney(acc.opening) + '</td><td class="fr-num">' + frMoney(acc.inflow)
        + '</td><td class="fr-num">' + frMoney(acc.outflow) + '</td><td class="fr-num">' + frMoney(acc.closing) + "</td></tr></tfoot></table></div></div>";
    return html;
}

function renderFrOpsTable(areaId, periodKey, canEdit) {
    let ops = frOps(areaId, periodKey);
    if (frUi.opKind === "in") ops = ops.filter(function (o) { return o.type === "in"; });
    if (frUi.opKind === "out") ops = ops.filter(function (o) { return o.type === "out"; });
    if (frUi.sourceId) ops = ops.filter(function (o) { return o.finance_source_id === frUi.sourceId; });
    if (frUi.catId) ops = ops.filter(function (o) { return o.finance_category_id === frUi.catId; });
    if (frUi.date) ops = ops.filter(function (o) { return o.date === frUi.date; });
    const q = String(frUi.q || "").toLowerCase();
    if (q) ops = ops.filter(function (o) { return String(o.comment || "").toLowerCase().indexOf(q) !== -1; });
    let html = '<div class="fr-box"><h3>Движения денег</h3>';
    html += '<div class="fr-filters">';
    html += '<button type="button" class="btn btn-small' + (frUi.opKind === "all" ? " btn-primary" : "") + '" onclick="frUi.opKind=\'all\';renderFinanceRec()">Все</button>';
    html += '<button type="button" class="btn btn-small' + (frUi.opKind === "in" ? " btn-primary" : "") + '" onclick="frUi.opKind=\'in\';renderFinanceRec()">Поступления</button>';
    html += '<button type="button" class="btn btn-small' + (frUi.opKind === "out" ? " btn-primary" : "") + '" onclick="frUi.opKind=\'out\';renderFinanceRec()">Расходы</button>';
    html += '<select onchange="frUi.sourceId=this.value;renderFinanceRec()"><option value="">Источник</option>'
        + frSources(areaId).map(function (s) { return '<option value="' + s.finance_source_id + '"' + (frUi.sourceId === s.finance_source_id ? " selected" : "") + ">" + escapeHtml(s.name) + "</option>"; }).join("")
        + "</select>";
    html += '<select onchange="frUi.catId=this.value;renderFinanceRec()"><option value="">Статья</option>'
        + frCats(areaId).map(function (s) { return '<option value="' + s.finance_category_id + '"' + (frUi.catId === s.finance_category_id ? " selected" : "") + ">" + escapeHtml(s.name) + "</option>"; }).join("")
        + "</select>";
    html += '<input type="date" value="' + escapeAttribute(frUi.date || "") + '" onchange="frUi.date=this.value;renderFinanceRec()" title="Дата">';
    html += '<input placeholder="Поиск" value="' + escapeAttribute(frUi.q || "") + '" oninput="frUi.q=this.value" onchange="renderFinanceRec()">';
    if (canEdit) {
        html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrOpForm()">+ Добавить операцию</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="openFrQuickOps()">Быстрый ввод</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="openFrPasteOps()">Импорт из Excel</button>';
    }
    html += "</div><div class=\"fr-table-wrap\"><table class=\"fr-table\"><thead><tr><th>Дата</th><th>Тип</th><th>Статья</th><th>Источник</th><th class=\"fr-num\">Приход</th><th class=\"fr-num\">Расход</th><th>Комментарий</th><th></th></tr></thead><tbody>";
    if (!ops.length) html += '<tr><td colspan="8"><div class="empty-row">Нет операций за неделю.</div></td></tr>';
    ops.slice().sort(function (a, b) { return String(a.date || "").localeCompare(String(b.date || "")); }).forEach(function (o) {
        const src = frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_id; });
        const cat = frCats(areaId, true).find(function (s) { return s.finance_category_id === o.finance_category_id; });
        html += "<tr><td>" + escapeHtml(typeof mgmtFmtRu === "function" ? mgmtFmtRu(o.date) : o.date) + "</td><td>"
            + (o.type === "in" ? "Приход" : "Расход") + "</td><td>" + escapeHtml((cat && cat.name) || "") + "</td><td>"
            + escapeHtml((src && src.name) || "") + '</td><td class="fr-num fr-in">' + (o.type === "in" ? frMoney(o.amount) : "—")
            + '</td><td class="fr-num fr-out">' + (o.type === "out" ? frMoney(o.amount) : "—") + "</td><td>"
            + escapeHtml(o.comment || "") + "</td><td>";
        if (canEdit) {
            html += '<button type="button" class="btn btn-small" onclick="openFrOpForm(\'' + o.finance_operation_id + "')\">✎</button> "
                + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\'' + o.finance_operation_id + "')\">✕</button>";
        }
        html += "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    return html;
}

function renderFrControl(areaId, periodKey, tot, disc, balanced, st, closed) {
    let html = '<div class="fr-ctrl ' + (balanced ? "fr-ok" : "fr-bad") + '"><h3>Контрольная сверка</h3>';
    html += '<div class="fr-muted">Формула из настроек. Ссылки по ID показателей.</div>';
    frFormulaTerms(areaId).forEach(function (term) {
        const names = { finm_opening: "Начальные остатки", finm_inflow: "Поступления", finm_outflow: "Расходы", finm_closing: "Конечные остатки", finm_net: "Чистое движение" };
        html += '<div style="display:flex;justify-content:space-between;margin:4px 0"><span>' + (term.sign === "-" ? "− " : "+ ") + escapeHtml(names[term.metric_id] || term.metric_id)
            + "</span><b>" + frMoney(frMetricValue(term.metric_id, areaId, periodKey)) + "</b></div>";
    });
    html += '<div class="fr-disc">' + (disc > 0 ? "+" : "") + frMoney(disc) + "</div>";
    html += "<div>" + (balanced ? "✓ Сверено" : "⚠ Не сошлось") + "</div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Статус периода</h3>';
    html += "<p>" + escapeHtml(frStatusLabel(st.status)) + (closed ? " · только просмотр" : "") + "</p>";
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'filled\')">Отметить заполненным</button> ';
    if (frCan("check") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'review\')">На проверку</button> ';
    if (frCan("check") && !closed && balanced) html += '<button type="button" class="btn btn-primary btn-small" onclick="frSetStatus(\'reconciled\')">Сверено</button> ';
    if (frCan("close") && !closed) html += '<button type="button" class="btn btn-primary btn-small" onclick="frClosePeriod()">Закрыть период</button> ';
    if (closed && frCan("setup")) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frReopenPeriod()">Открыть закрытый период</button>';
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Перенос остатков</h3><p class="fr-muted">Остаток на конец этой недели станет остатком на начало следующей. Источники не копируются, используются те же ID.</p>';
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frPushCarry()">Перенести в следующую неделю</button>';
    html += "</div>";
    return html;
}

function renderFrSettingsHtml(area) {
    let html = '<div class="fr-layout"><div class="fr-box"><h3>Источники средств</h3>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'source\')">+ Источник</button>';
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Порядок</th><th>Статус</th><th></th></tr></thead><tbody>';
    frSources(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(String(s.sort_order || "")) + "</td><td>"
            + (s.is_active === false ? "Неактивен" : "Активен") + '</td><td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'source\',\'' + s.finance_source_id + "')\">✎</button></td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="fr-box"><h3>Статьи движения</h3>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'cat\')">+ Статья</button>';
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Тип</th><th>Статус</th><th></th></tr></thead><tbody>';
    frCats(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.default_type === "in" ? "Приход" : (s.default_type === "out" ? "Расход" : "Любой")) + "</td><td>"
            + (s.is_active === false ? "Неактивна" : "Активна") + '</td><td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'cat\',\'' + s.finance_category_id + "')\">✎</button></td></tr>";
    });
    html += "</tbody></table></div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Настройка сверки</h3><p class="fr-muted">Контрольное расхождение = сумма выбранных показателей со знаками. ID: finm_opening, finm_inflow, finm_outflow, finm_closing.</p>';
    const terms = frFormulaTerms(area.finance_area_id);
    html += '<div id="frFormulaBox">';
    terms.forEach(function (t, i) {
        html += '<div class="fr-filters" data-i="' + i + '"><select class="frSign"><option value="+"' + (t.sign !== "-" ? " selected" : "") + ">+</option><option value=\"-\"" + (t.sign === "-" ? " selected" : "") + ">-</option></select>";
        html += '<select class="frMetric">'
            + [["finm_opening", "Начальные остатки"], ["finm_inflow", "Поступления"], ["finm_outflow", "Расходы"], ["finm_closing", "Конечные остатки"], ["finm_net", "Поступило − расходы"]].map(function (p) {
                return '<option value="' + p[0] + '"' + (t.metric_id === p[0] ? " selected" : "") + ">" + p[1] + "</option>";
            }).join("") + "</select></div>";
    });
    html += "</div>";
    html += '<button type="button" class="btn btn-secondary btn-small" onclick="frAddFormulaTerm()">+ Показатель</button> ';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="frSaveFormula()">Сохранить формулу</button></div>';
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
    const why = prompt("Причина корректировки входящего остатка:");
    if (!String(why || "").trim()) { renderFinanceRec(); return; }
    let row = frOpeningRow(area.finance_area_id, w.period_key, sourceId);
    if (!row) {
        row = { finance_area_id: area.finance_area_id, period_key: w.period_key, finance_source_id: sourceId };
        appData.financeRec.openings.push(row);
    }
    row.value = next;
    row.is_manual = true;
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
    let html = "<h3>" + (rec ? "Операция" : "Новая операция") + "</h3>";
    html += '<input type="hidden" id="frOpId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frOpDate" type="date" value="' + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Тип</label><select id="frOpType"><option value="in"' + (rec && rec.type === "in" ? " selected" : "") + ">Приход</option><option value=\"out\"" + (!rec || rec.type === "out" ? " selected" : "") + ">Расход</option></select></div>";
    html += '<div class="form-group"><label>Статья</label><select id="frOpCat">' + frCats(area.finance_area_id).map(function (c) {
        return '<option value="' + c.finance_category_id + '"' + (rec && rec.finance_category_id === c.finance_category_id ? " selected" : "") + ">" + escapeHtml(c.name) + "</option>";
    }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Источник</label><select id="frOpSrc">' + frSources(area.finance_area_id).map(function (c) {
        return '<option value="' + c.finance_source_id + '"' + (rec && rec.finance_source_id === c.finance_source_id ? " selected" : "") + ">" + escapeHtml(c.name) + "</option>";
    }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frOpAmt" value="' + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frOpComment" value="' + escapeAttribute((rec && rec.comment) || "") + '"></div>';
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
    rec.finance_area_id = area.finance_area_id;
    rec.period_key = w.period_key;
    rec.date = ((document.getElementById("frOpDate") || {}).value || w.start_date);
    rec.type = ((document.getElementById("frOpType") || {}).value || "out") === "in" ? "in" : "out";
    rec.finance_category_id = ((document.getElementById("frOpCat") || {}).value || "");
    rec.finance_source_id = ((document.getElementById("frOpSrc") || {}).value || "");
    rec.amount = amt;
    rec.comment = String(((document.getElementById("frOpComment") || {}).value || "")).trim();
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    frAudit(id ? "op_edit" : "op_add", { finance_operation_id: rec.finance_operation_id, old_value: old, new_value: amt, finance_source_id: rec.finance_source_id });
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
}

function frDeleteOp(id) {
    if (!frCanEditData()) return;
    const rec = (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; });
    if (!rec) return;
    rec.is_deleted = true;
    rec.deleted_at = new Date().toISOString();
    rec.deleted_by = typeof currentAuthUserId === "function" ? currentAuthUserId() : "";
    frAudit("op_delete", { finance_operation_id: id, old_value: rec.amount, finance_source_id: rec.finance_source_id, detail: rec.comment || "" });
    saveApp();
    renderFinanceRec();
}

function openFrQuickOps() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    let html = "<h3>Быстрый ввод</h3><p class=\"note\">Несколько строк подряд. Одна строка — один тип и одна сумма.</p>";
    for (let i = 0; i < 6; i++) {
        html += '<div class="fr-filters" style="margin-bottom:6px">';
        html += '<input type="date" id="frQDate' + i + '" value="' + escapeAttribute(w.start_date) + '">';
        html += '<select id="frQType' + i + '"><option value="in">Приход</option><option value="out" selected>Расход</option></select>';
        html += '<select id="frQCat' + i + '">' + frCats(area.finance_area_id).map(function (c) { return '<option value="' + c.finance_category_id + '">' + escapeHtml(c.name) + "</option>"; }).join("") + "</select>";
        html += '<select id="frQSrc' + i + '">' + frSources(area.finance_area_id).map(function (c) { return '<option value="' + c.finance_source_id + '">' + escapeHtml(c.name) + "</option>"; }).join("") + "</select>";
        html += '<input id="frQAmt' + i + '" placeholder="Сумма" style="width:90px">';
        html += '<input id="frQCom' + i + '" placeholder="Комментарий">';
        html += "</div>";
    }
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrQuickOps()">Сохранить строки</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrQuickOps() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    let n = 0;
    for (let i = 0; i < 6; i++) {
        const amt = frNum((document.getElementById("frQAmt" + i) || {}).value);
        if (!(amt > 0)) continue;
        appData.financeRec.operations.push({
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
            finance_area_id: area.finance_area_id,
            period_key: w.period_key,
            date: ((document.getElementById("frQDate" + i) || {}).value || w.start_date),
            type: ((document.getElementById("frQType" + i) || {}).value || "out") === "in" ? "in" : "out",
            finance_category_id: ((document.getElementById("frQCat" + i) || {}).value || ""),
            finance_source_id: ((document.getElementById("frQSrc" + i) || {}).value || ""),
            amount: amt,
            comment: String(((document.getElementById("frQCom" + i) || {}).value || "")).trim(),
            is_deleted: false
        });
        n += 1;
    }
    frAudit("op_quick", { detail: "строк: " + n });
    saveApp();
    closeMgmtModal();
    toast(n ? ("Сохранено операций: " + n) : "Нет заполненных сумм");
    renderFinanceRec();
}

function openFrPasteOps() {
    if (!frCanEditData()) return;
    let html = "<h3>Импорт из Excel</h3><p class=\"note\">Вставьте строки: Дата, Тип (приход/расход), Статья, Источник, Сумма, Комментарий. Без внешних библиотек.</p>";
    html += '<textarea id="frPasteBox" rows="10" style="width:100%"></textarea>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrPasteOps()">Импортировать</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrPasteOps() {
    const area = frActiveArea();
    const w = frEnsureWeek();
    const text = String((document.getElementById("frPasteBox") || {}).value || "");
    const lines = text.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean);
    let n = 0;
    lines.forEach(function (line) {
        const p = line.split(/\t|;|,/).map(function (x) { return String(x || "").trim(); });
        if (p.length < 5) return;
        const typeRaw = p[1].toLowerCase();
        const type = (typeRaw.indexOf("прих") >= 0 || typeRaw === "in" || typeRaw === "+") ? "in" : "out";
        const cat = frCats(area.finance_area_id).find(function (c) { return String(c.name).toLowerCase() === p[2].toLowerCase(); });
        const src = frSources(area.finance_area_id).find(function (c) { return String(c.name).toLowerCase() === p[3].toLowerCase(); });
        const amt = frNum(p[4]);
        if (!(amt > 0) || !src) return;
        appData.financeRec.operations.push({
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
            finance_area_id: area.finance_area_id,
            period_key: w.period_key,
            date: /^\d{4}-\d{2}-\d{2}$/.test(p[0]) ? p[0] : w.start_date,
            type: type,
            finance_category_id: cat ? cat.finance_category_id : "",
            finance_source_id: src.finance_source_id,
            amount: amt,
            comment: p[5] || "",
            is_deleted: false
        });
        n += 1;
    });
    frAudit("op_paste", { detail: "строк: " + n });
    saveApp();
    closeMgmtModal();
    toast(n ? ("Импортировано: " + n) : "Нет распознанных строк");
    renderFinanceRec();
}

function openFrDictForm(kind, id) {
    if (!frCan("setup")) return;
    const area = frActiveArea();
    const list = kind === "source" ? appData.financeRec.sources : appData.financeRec.categories;
    const rec = id ? list.find(function (x) { return x && (kind === "source" ? x.finance_source_id : x.finance_category_id) === id; }) : null;
    let html = "<h3>" + (kind === "source" ? "Источник средств" : "Статья движения") + "</h3>";
    html += '<input type="hidden" id="frDictKind" value="' + kind + '"><input type="hidden" id="frDictId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Название</label><input id="frDictName" value="' + escapeAttribute(rec ? rec.name : "") + '"></div>';
    html += '<div class="form-group"><label>Порядок</label><input id="frDictOrder" type="number" value="' + escapeAttribute(String(rec ? rec.sort_order : 10)) + '"></div>';
    if (kind === "cat") {
        html += '<div class="form-group"><label>Тип по умолчанию</label><select id="frDictType"><option value="any">Любой</option><option value="in"' + (rec && rec.default_type === "in" ? " selected" : "") + ">Приход</option><option value=\"out\"" + (rec && rec.default_type === "out" ? " selected" : "") + ">Расход</option></select></div>";
    }
    html += '<div class="form-group"><label>Статус</label><select id="frDictActive"><option value="1"' + (!rec || rec.is_active !== false ? " selected" : "") + ">Активен</option><option value=\"0\"" + (rec && rec.is_active === false ? " selected" : "") + ">Неактивен</option></select></div>";
    html += '<div class="note">Запись с историей не удаляется физически — только деактивируется.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrDict()">Сохранить</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrDict() {
    if (!frCan("setup")) return;
    const kind = ((document.getElementById("frDictKind") || {}).value || "source");
    const id = ((document.getElementById("frDictId") || {}).value || "");
    const name = String((document.getElementById("frDictName") || {}).value || "").replace(/\s+/g, " ").trim();
    if (!name) { toast("Укажите название", "error"); return; }
    const area = frActiveArea();
    const list = kind === "source" ? appData.financeRec.sources : appData.financeRec.categories;
    const key = kind === "source" ? "finance_source_id" : "finance_category_id";
    const prefix = kind === "source" ? "fsrc" : "fcat";
    let rec = id ? list.find(function (x) { return x && x[key] === id; }) : null;
    if (!rec) {
        rec = { finance_area_id: area.finance_area_id };
        rec[key] = nextPrefixedId(prefix, list.map(function (x) { return x && x[key]; }));
        list.push(rec);
    }
    rec.name = name;
    rec.sort_order = Number((document.getElementById("frDictOrder") || {}).value || 10);
    rec.is_active = ((document.getElementById("frDictActive") || {}).value || "1") !== "0";
    if (kind === "cat") rec.default_type = ((document.getElementById("frDictType") || {}).value || "any");
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
        + '<select class="frMetric"><option value="finm_opening">Начальные остатки</option><option value="finm_inflow">Поступления</option>'
        + '<option value="finm_outflow">Расходы</option><option value="finm_closing">Конечные остатки</option>'
        + '<option value="finm_net">Поступило − расходы</option></select>';
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
        const metric = (row.querySelector(".frMetric") || {}).value || "finm_inflow";
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
    const disc = frDiscrepancy(area.finance_area_id, w.period_key);
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
        const tot = frSourceTotals(area.finance_area_id, w.period_key, s.finance_source_id);
        let row = frOpeningRow(area.finance_area_id, next.period_key, s.finance_source_id);
        if (!row) {
            row = { finance_area_id: area.finance_area_id, period_key: next.period_key, finance_source_id: s.finance_source_id };
            appData.financeRec.openings.push(row);
        }
        if (row.is_manual && !silent) return;
        row.value = tot.closing;
        row.carried = true;
        row.is_manual = false;
    });
    if (!silent) {
        saveApp();
        toast("Остатки перенесены на следующую неделю.");
        renderFinanceRec();
    }
}

function financeRecFolderCardHtml() {
    return folderCardHtml("summary-folder", "financeRec", "Финконтроль", "Финансовая сверка поступлений, расходов и остатков", "");
}
