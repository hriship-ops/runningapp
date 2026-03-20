var rwState = { year: '', month: '', activity: '', tickerPage: 0, runs: [] };
var rwTickerRuns = [];

document.addEventListener('DOMContentLoaded', function() {
    var now = new Date();
    rwState.year = now.getFullYear().toString();
    rwState.month = String(now.getMonth() + 1).padStart(2, '0');
    var yearSel = document.getElementById('rw-year');
    if (yearSel) {
        yearSel.innerHTML = '<option value="">All Years</option>';
        for (var y = now.getFullYear(); y >= 2010; y--) {
            var opt = document.createElement('option');
            opt.value = y; opt.textContent = y;
            if (y == rwState.year) opt.selected = true;
            yearSel.appendChild(opt);
        }
    }
    var monthSel = document.getElementById('rw-month');
    if (monthSel) monthSel.value = rwState.month;
    rwSearch();
    rwLoadTicker();
});

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
    var cls = type === 'Swimming' ? 'rw-act-swim' : type === 'Cycling' ? 'rw-act-cycle' : type === 'Walk' ? 'rw-act-walk' : 'rw-act-run';
    return '<span class="' + cls + '">' + type + '</span>';
}

function rwGetFilters() {
    var filters = [];
    if (rwState.year && rwState.month) {
        var start = rwState.year + '-' + rwState.month + '-01';
        var end = new Date(rwState.year, parseInt(rwState.month), 0);
        var endStr = rwState.year + '-' + rwState.month + '-' + String(end.getDate()).padStart(2,'0');
        filters.push(['date', '>=', start], ['date', '<=', endStr]);
    } else if (rwState.year) {
        filters.push(['date', '>=', rwState.year + '-01-01'], ['date', '<=', rwState.year + '-12-31']);
    }
    if (rwState.activity) filters.push(['activity_type', '=', rwState.activity]);
    return filters;
}

function rwSearch() {
    rwState.year = document.getElementById('rw-year').value;
    rwState.month = document.getElementById('rw-month').value;
    rwState.activity = document.getElementById('rw-activity').value;
    rwLoadStats();
}

function rwReset() {
    var now = new Date();
    rwState.year = now.getFullYear().toString();
    rwState.month = String(now.getMonth() + 1).padStart(2, '0');
    rwState.activity = '';
    document.getElementById('rw-year').value = rwState.year;
    document.getElementById('rw-month').value = rwState.month;
    document.getElementById('rw-activity').value = '';
    rwLoadStats();
}

function rwLoadStats() {
    var filters = rwGetFilters();
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': 'fetch'},
        body: JSON.stringify({filters: JSON.stringify(filters)})
    })
    .then(r => r.json())
    .then(data => {
        rwState.runs = data.message || [];
        rwRenderStats(rwState.runs);
        rwRenderLabel();
    });
}

function rwLoadTicker() {
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': 'fetch'},
        body: JSON.stringify({filters: JSON.stringify([])})
    })
    .then(r => r.json())
    .then(data => {
        rwTickerRuns = data.message || [];
        rwRenderTicker();
    });
}

function rwRenderLabel() {
    var parts = [];
    if (rwState.year) parts.push(rwState.year);
    if (rwState.month) {
        var months = ['','January','February','March','April','May','June','July','August','September','October','November','December'];
        parts.push(months[parseInt(rwState.month)]);
    }
    if (rwState.activity) parts.push(rwState.activity);
    var label = parts.length ? parts.join(' · ') : 'All Time';
    var el = document.getElementById('rw-section-label');
    if (el) el.textContent = label + ' · ' + rwState.runs.length + ' runs';
}

function rwRenderStats(runs) {
    var totalDist = runs.reduce(function(a,r) { return a + (r.distance_km||0); }, 0);
    var totalSec = runs.reduce(function(a,r) { return a + (r.duration_sec||0); }, 0);
    var totalElev = runs.reduce(function(a,r) { return a + (r.elevation_gain||0); }, 0);
    var totalCal = runs.reduce(function(a,r) { return a + (r.calories||0); }, 0);
    var avgDist = runs.length ? totalDist / runs.length : 0;
    var longest = runs.length ? Math.max.apply(null, runs.map(function(r) { return r.distance_km||0; })) : 0;

    var cards = [
        { val: runs.length, lbl: 'Total runs', clickable: true },
        { val: fmtDist(totalDist), lbl: 'Total distance', clickable: true },
        { val: fmtDur(totalSec), lbl: 'Total time', clickable: true },
        { val: fmtPace(totalDist, totalSec), lbl: 'Avg pace', clickable: false },
        { val: fmtDist(avgDist), lbl: 'Avg distance', clickable: true },
        { val: Math.round(totalElev) + ' m', lbl: 'Total elevation', clickable: true },
        { val: fmtDist(longest), lbl: 'Longest run', clickable: true },
        { val: Math.round(totalCal) + ' kcal', lbl: 'Total calories', clickable: false }
    ];

    var html = cards.map(function(c) {
        return '<div class="rw-card' + (c.clickable ? '' : ' no-click') + '"' +
            (c.clickable ? ' onclick="rwShowModal()"' : '') + '>' +
            '<div class="rw-card-val">' + c.val + '</div>' +
            '<div class="rw-card-lbl">' + c.lbl + '</div>' +
            (c.clickable ? '<div class="rw-card-sub">click to view</div>' : '') +
            '</div>';
    }).join('');

    var el = document.getElementById('rw-cards');
    if (el) el.innerHTML = html;
    var sub = document.getElementById('rw-total-sub');
    if (sub) sub.textContent = runs.length + ' runs · ' + fmtDist(totalDist);
}

function rwShowModal() {
    var rows = rwState.runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td>' +
            '<td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location || '--') + '</td>' +
            '<td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td>' +
            '<td>' + fmtPace(r.distance_km, r.duration_sec) + '</td>' +
            '</tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = rwState.runs.length + ' runs';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwCloseModal(e) {
    if (!e || e.target === document.getElementById('rw-modal')) {
        document.getElementById('rw-modal').style.display = 'none';
    }
}

function rwOpenDetail(name) {
    window.open('/run-detail?name=' + name, '_blank');
}

function rwRenderTicker() {
    var page = rwState.tickerPage;
    var pageSize = 4;
    var start = page * pageSize;
    var items = rwTickerRuns.slice(start, start + pageSize);
    var totalPages = Math.ceil(rwTickerRuns.length / pageSize);
    var html = items.map(function(r) {
        var emoji = r.activity_type === 'Swimming' ? '🏊' : r.activity_type === 'Cycling' ? '🚴' : r.activity_type === 'Walk' ? '🚶' : '🏃';
        return '<div class="rw-ticker-item" onclick="rwOpenDetail(\'' + r.name + '\')">' +
            emoji + ' ' + fmtDate(r.date) + ' · ' + (r.location || '') +
            ' · ' + fmtDist(r.distance_km||0) +
            (r.duration_sec ? ' · ' + fmtPace(r.distance_km, r.duration_sec) : '') +
            '</div>';
    }).join('');
    document.getElementById('rw-ticker').innerHTML = html;
    document.getElementById('rw-ticker-page').textContent = (page+1) + '/' + Math.max(1, totalPages);
}

function rwTickerNext() {
    var totalPages = Math.ceil(rwTickerRuns.length / 4);
    if (rwState.tickerPage < totalPages - 1) { rwState.tickerPage++; rwRenderTicker(); }
}
function rwTickerPrev() {
    if (rwState.tickerPage > 0) { rwState.tickerPage--; rwRenderTicker(); }
}