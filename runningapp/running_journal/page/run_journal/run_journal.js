frappe.pages['run-journal'].on_page_load = function(wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: 'Run Journal', single_column: true });
    $(wrapper).find('.layout-main-section').html(frappe.render_template('run_journal', {}));
    
    // Make all functions global
    window.rjSearch = rjSearch;
    window.rjReset = rjReset;
    window.rjShowModal = rjShowModal;
    window.rjCloseModal = rjCloseModal;
    window.rjOpenDetail = rjOpenDetail;
    window.rjTickerNext = rjTickerNext;
    window.rjTickerPrev = rjTickerPrev;
    window.rjShowImport = rjShowImport;
    
    rjInit();
};
var rjState = { year: '', month: '', activity: '', tickerPage: 0, runs: [] };
var rjTickerRuns = [];

function rjInit() {
    var now = new Date();
    rjState.year = now.getFullYear().toString();
    rjState.month = String(now.getMonth() + 1).padStart(2, '0');
    var yearSel = document.getElementById('rj-year');
    if (yearSel) {
        yearSel.innerHTML = '<option value="">All Years</option>';
        for (var y = now.getFullYear(); y >= 2010; y--) {
            var opt = document.createElement('option');
            opt.value = y; opt.textContent = y;
            if (y == rjState.year) opt.selected = true;
            yearSel.appendChild(opt);
        }
    }
    var monthSel = document.getElementById('rj-month');
    if (monthSel) monthSel.value = rjState.month;
    rjSearch();
    rjLoadTicker();
}

function fmtDist(km) { return km >= 1 ? km.toFixed(2) + ' km' : Math.round(km * 1000) + ' m'; }
function fmtDur(sec) {
    if (!sec) return '--';
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = Math.round(sec%60);
    return h ? h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0') : m + ':' + String(s).padStart(2,'0');
}
function fmtPace(km, sec) {
    if (!sec || !km) return '--';
    var sPerKm = sec / km, m = Math.floor(sPerKm/60), s = Math.round(sPerKm%60);
    return m + ':' + String(s).padStart(2,'0') + '/km';
}
function fmtDate(d) {
    return new Date(d + 'T12:00:00').toLocaleDateString('en-US', {day:'numeric', month:'short', year:'numeric'});
}
function actPill(type) {
    var cls = type === 'Swimming' ? 'rj-act-swim' : type === 'Cycling' ? 'rj-act-cycle' : 'rj-act-run';
    return '<span class="' + cls + '">' + type + '</span>';
}

function rjSearch() {
    rjState.year = document.getElementById('rj-year').value;
    rjState.month = document.getElementById('rj-month').value;
    rjState.activity = document.getElementById('rj-activity').value;
    rjLoadStats();
}

function rjReset() {
    var now = new Date();
    rjState.year = now.getFullYear().toString();
    rjState.month = String(now.getMonth() + 1).padStart(2, '0');
    rjState.activity = '';
    document.getElementById('rj-year').value = rjState.year;
    document.getElementById('rj-month').value = rjState.month;
    document.getElementById('rj-activity').value = '';
    rjLoadStats();
}

function rjGetFilters() {
    var filters = [];
    if (rjState.year && rjState.month) {
        var start = rjState.year + '-' + rjState.month + '-01';
        var end = new Date(rjState.year, parseInt(rjState.month), 0);
        var endStr = rjState.year + '-' + rjState.month + '-' + String(end.getDate()).padStart(2,'0');
        filters.push(['date', '>=', start], ['date', '<=', endStr]);
    } else if (rjState.year) {
        filters.push(['date', '>=', rjState.year + '-01-01'], ['date', '<=', rjState.year + '-12-31']);
    }
    if (rjState.activity) filters.push(['activity_type', '=', rjState.activity]);
    return filters;
}

function rjLoadStats() {
    var filters = rjGetFilters();
    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Run',
            filters: filters,
            fields: ['name','run_name','date','activity_type','location','distance_km','duration_sec','elevation_gain','calories'],
            limit: 0,
            order_by: 'date desc'
        },
        callback: function(r) {
            rjState.runs = r.message || [];
            rjRenderStats(rjState.runs);
            rjRenderLabel();
        }
    });
}

function rjRenderLabel() {
    var parts = [];
    if (rjState.year) parts.push(rjState.year);
    if (rjState.month) {
        var months = ['','January','February','March','April','May','June','July','August','September','October','November','December'];
        parts.push(months[parseInt(rjState.month)]);
    }
    if (rjState.activity) parts.push(rjState.activity);
    var label = parts.length ? parts.join(' · ') : 'All Time';
    var el = document.getElementById('rj-section-label');
    if (el) el.textContent = label + ' · ' + rjState.runs.length + ' runs';
}

function rjRenderStats(runs) {
    var totalDist = runs.reduce(function(a,r) { return a + (r.distance_km||0); }, 0);
    var totalSec = runs.reduce(function(a,r) { return a + (r.duration_sec||0); }, 0);
    var totalElev = runs.reduce(function(a,r) { return a + (r.elevation_gain||0); }, 0);
    var totalCal = runs.reduce(function(a,r) { return a + (r.calories||0); }, 0);
    var avgDist = runs.length ? totalDist / runs.length : 0;
    var longest = runs.length ? Math.max.apply(null, runs.map(function(r) { return r.distance_km||0; })) : 0;

    var cards = [
        { val: runs.length, lbl: 'Total runs', clickable: true, color: '' },
        { val: fmtDist(totalDist), lbl: 'Total distance', clickable: true, color: '' },
        { val: fmtDur(totalSec), lbl: 'Total time', clickable: true, color: '' },
        { val: fmtPace(totalDist, totalSec), lbl: 'Avg pace', clickable: false, color: '' },
        { val: fmtDist(avgDist), lbl: 'Avg distance', clickable: true, color: 'blue' },
        { val: Math.round(totalElev) + ' m', lbl: 'Total elevation', clickable: true, color: 'blue' },
        { val: fmtDist(longest), lbl: 'Longest run', clickable: true, color: 'green' },
        { val: Math.round(totalCal) + ' kcal', lbl: 'Total calories', clickable: false, color: 'amber' }
    ];

    var html = cards.map(function(c) {
        return '<div class="rj-card' + (c.clickable ? '' : ' no-click') + '"' +
            (c.clickable ? ' onclick="rjShowModal()"' : '') + '>' +
            '<div class="rj-card-val ' + c.color + '">' + c.val + '</div>' +
            '<div class="rj-card-lbl">' + c.lbl + '</div>' +
            (c.clickable ? '<div class="rj-card-sub">click to view</div>' : '') +
            '</div>';
    }).join('');

    var el = document.getElementById('rj-cards');
    if (el) el.innerHTML = html;
    var sub = document.getElementById('rj-total-sub');
    if (sub) sub.textContent = runs.length + ' runs · ' + fmtDist(totalDist);
}

function rjShowModal() {
    var runs = rjState.runs;
    var rows = runs.map(function(r) {
        return '<tr onclick="rjOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td>' +
            '<td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location || '--') + '</td>' +
            '<td style="color:#f1f5f9;font-weight:600">' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td>' +
            '<td>' + fmtPace(r.distance_km, r.duration_sec) + '</td>' +
            '</tr>';
    }).join('');
    var el = document.getElementById('rj-modal-rows');
    if (el) el.innerHTML = rows;
    var title = document.getElementById('rj-modal-title');
    if (title) title.textContent = runs.length + ' runs';
    var modal = document.getElementById('rj-modal');
    if (modal) modal.style.display = 'flex';
}

function rjCloseModal() {
    var modal = document.getElementById('rj-modal');
    if (modal) modal.style.display = 'none';
}

function rjOpenDetail(name) {
    window.open('/app/run-detail?name=' + name, '_blank');
}

function rjLoadTicker() {
    var filters = rjState.activity ? [['activity_type', '=', rjState.activity]] : [];
    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Run',
            filters: filters,
            fields: ['name','run_name','date','activity_type','location','distance_km','duration_sec','calories'],
            limit: 200,
            order_by: 'date desc'
        },
        callback: function(r) {
            rjTickerRuns = r.message || [];
            rjRenderTicker();
        }
    });
}

function rjRenderTicker() {
    var page = rjState.tickerPage;
    var pageSize = 4;
    var start = page * pageSize;
    var items = rjTickerRuns.slice(start, start + pageSize);
    var totalPages = Math.ceil(rjTickerRuns.length / pageSize);
    var html = items.map(function(r) {
        var emoji = r.activity_type === 'Swimming' ? '\u{1F3CA}' : r.activity_type === 'Cycling' ? '\u{1F6B4}' : '\u{1F3C3}';
        return '<div class="rj-ticker-item" onclick="rjOpenDetail(\'' + r.name + '\')">' +
            emoji + ' ' + fmtDate(r.date) + ' &middot; ' + (r.location || '') +
            ' &middot; ' + fmtDist(r.distance_km||0) +
            (r.duration_sec ? ' &middot; ' + fmtPace(r.distance_km, r.duration_sec) : '') +
            '</div>';
    }).join('');
    var el = document.getElementById('rj-ticker');
    if (el) el.innerHTML = html;
    var pg = document.getElementById('rj-ticker-page');
    if (pg) pg.textContent = (page+1) + '/' + Math.max(1, totalPages);
}

function rjTickerNext() {
    var totalPages = Math.ceil(rjTickerRuns.length / 4);
    if (rjState.tickerPage < totalPages - 1) { rjState.tickerPage++; rjRenderTicker(); }
}
function rjTickerPrev() {
    if (rjState.tickerPage > 0) { rjState.tickerPage--; rjRenderTicker(); }
}

function rjShowImport() {
    frappe.msgprint('GPX bulk import coming soon. Use Strava sync for now.');
}