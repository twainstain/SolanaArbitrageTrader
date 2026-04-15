"""Dashboard HTML page served by FastAPI.

Provides a live-updating dashboard with:
  - Current status (execution, pause, circuit breaker)
  - Time-windowed aggregations (15m to 1m)
  - Per-chain breakdown
  - Recent opportunities table
  - PnL summary

Auto-refreshes every 30 seconds via meta refresh.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Arbitrage Trader Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
               background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; }
        h2 { color: #8b949e; margin: 20px 0 10px; font-size: 14px; text-transform: uppercase; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card-title { font-size: 12px; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; }
        .card-value { font-size: 28px; font-weight: bold; color: #f0f6fc; }
        .card-sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
        .status-ok { color: #3fb950; }
        .status-warn { color: #d29922; }
        .status-bad { color: #f85149; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 8px; border-bottom: 2px solid #30363d; color: #8b949e;
             font-size: 12px; text-transform: uppercase; cursor: pointer; }
        th:hover { color: #58a6ff; }
        td { padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
        tr:hover { background: #1c2128; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; }
        .tag-detected { background: #1f6feb33; color: #58a6ff; }
        .tag-approved { background: #23863533; color: #3fb950; }
        .tag-rejected { background: #da362933; color: #f85149; }
        .tag-submitted { background: #9e6a0333; color: #d29922; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .tab { padding: 6px 16px; border-radius: 6px; background: #21262d; color: #8b949e;
               cursor: pointer; font-size: 13px; border: 1px solid #30363d; text-decoration: none; }
        .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
        .filter-row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
        .filter-row label { color: #8b949e; font-size: 13px; }
        .btn { padding: 6px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
               border: 1px solid #30363d; font-weight: bold; }
        .btn-green { background: #238636; color: #fff; border-color: #238636; }
        .btn-green:hover { background: #2ea043; }
        .btn-red { background: #da3633; color: #fff; border-color: #da3633; }
        .btn-red:hover { background: #f85149; }
        .btn-gray { background: #21262d; color: #8b949e; border-color: #30363d; }
        .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; }
        select { background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px;
                 padding: 6px 12px; font-size: 13px; }
        .bar-chart { display: flex; align-items: flex-end; gap: 4px; height: 120px;
                     background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                     padding: 12px; margin-top: 8px; }
        .bar-group { display: flex; flex-direction: column; align-items: center; flex: 1; }
        .bar-stack { display: flex; flex-direction: column-reverse; width: 100%; max-width: 40px; }
        .bar-win { background: #3fb950; min-height: 2px; border-radius: 2px 2px 0 0; }
        .bar-loss { background: #f85149; min-height: 0; border-radius: 0 0 2px 2px; }
        .bar-label { font-size: 10px; color: #8b949e; margin-top: 4px; text-align: center; }
        .chart-legend { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; }
        .legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
        .sort-arrow { font-size: 10px; color: #484f58; }
        .sort-arrow.active { color: #58a6ff; }
        th.sorted { color: #58a6ff; }
        tr.exec-row { background: #1c2128; cursor: pointer; }
        tr.exec-row:hover { background: #242a33; }
        tr.exec-row td:first-child { border-left: 3px solid #d29922; padding-left: 5px; }
        tr.exec-detail { display: none; }
        tr.exec-detail.open { display: table-row; }
        tr.exec-detail td { background: #161b22; padding: 12px 16px; border-left: 3px solid #30363d; }
        .exec-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
        .exec-item { font-size: 12px; }
        .exec-label { color: #8b949e; text-transform: uppercase; font-size: 10px; }
        .exec-value { color: #f0f6fc; font-family: monospace; }
    </style>
</head>
<body>
    <h1>Arbitrage Trader Dashboard</h1>

    <!-- Scanner Controls -->
    <div class="controls" id="scanner-controls">
        <span style="color:#8b949e;font-size:13px">Scanner:</span>
        <span id="scanner-status" class="tag tag-detected">loading...</span>
        <button class="btn btn-green" onclick="controlScanner('start')">Start</button>
        <button class="btn btn-red" onclick="controlScanner('stop')">Stop</button>
        <span style="color:#30363d">|</span>
        <span style="color:#8b949e;font-size:13px">Execution:</span>
        <button class="btn btn-gray" id="exec-toggle" onclick="toggleExecution()">loading...</button>
    </div>

    <!-- Per-Chain Execution Status -->
    <h2>Chain Execution Status</h2>
    <table id="chain-exec-table" style="margin-bottom:16px">
        <thead><tr>
            <th>Chain</th><th>Mode</th><th>Routers</th><th>Flash Loans</th><th>Action</th>
        </tr></thead>
        <tbody id="chain-exec-body"></tbody>
    </table>

    <!-- Status Cards -->
    <h2>System Status</h2>
    <div class="grid" id="status-grid"></div>

    <!-- Wallet Balance -->
    <div class="grid" id="wallet-grid" style="margin-top:12px"></div>

    <!-- Links -->
    <div style="margin:12px 0">
        <a href="ops" class="tab" style="text-decoration:none">Operations &amp; DEX Health &rarr;</a>
        <a href="analytics" class="tab" style="text-decoration:none">PnL Analytics &rarr;</a>
    </div>

    <!-- Chain Filter + Time Window Tabs + Custom Range -->
    <h2>Performance <span style="font-size:11px;color:#8b949e;font-weight:normal;text-transform:none">(times in EST)</span></h2>
    <div class="filter-row">
        <label>Chain:</label>
        <select id="chain-filter" onchange="onChainFilter()">
            <option value="">All Chains</option>
        </select>
        <span style="color:#30363d">|</span>
        <label>From:</label>
        <input type="datetime-local" id="range-start" style="background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:12px">
        <label>To:</label>
        <input type="datetime-local" id="range-end" style="background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:12px">
        <button class="btn btn-green" style="padding:4px 12px;font-size:12px" onclick="applyRange()">Apply</button>
        <button class="btn btn-gray" style="padding:4px 12px;font-size:12px" onclick="clearRange()">Clear</button>
    </div>
    <div class="tabs" id="window-tabs"></div>
    <div class="grid" id="window-grid"></div>

    <!-- Hourly Bar Chart -->
    <h2>Hourly Win/Loss (24h)</h2>
    <div class="chart-legend">
        <span><span class="legend-dot" style="background:#3fb950"></span>Approved/Included</span>
        <span><span class="legend-dot" style="background:#f85149"></span>Rejected/Reverted</span>
    </div>
    <div id="bar-chart-container"></div>

    <!-- Per-Chain -->
    <h2>Per Chain (24h)</h2>
    <table id="chain-table">
        <thead><tr>
            <th onclick="sortChains('chain')">Chain</th>
            <th onclick="sortChains('total')">Total</th>
            <th onclick="sortChains('approved')">Approved</th>
            <th onclick="sortChains('rejected')">Rejected</th>
        </tr></thead>
        <tbody></tbody>
    </table>

    <!-- Recent Opportunities (filtered by selected time window) -->
    <h2 id="opp-header">Recent Opportunities</h2>
    <table id="opp-table">
        <thead><tr>
            <th onclick="sortOpps('id')">ID <span class="sort-arrow" data-col="id"></span></th>
            <th onclick="sortOpps('pair')">Pair <span class="sort-arrow" data-col="pair"></span></th>
            <th onclick="sortOpps('chain')">Chain <span class="sort-arrow" data-col="chain"></span></th>
            <th onclick="sortOpps('buy')">Buy <span class="sort-arrow" data-col="buy"></span></th>
            <th onclick="sortOpps('sell')">Sell <span class="sort-arrow" data-col="sell"></span></th>
            <th onclick="sortOpps('spread')">Spread <span class="sort-arrow" data-col="spread"></span></th>
            <th onclick="sortOpps('profit')">Expected Profit <span class="sort-arrow" data-col="profit"></span></th>
            <th onclick="sortOpps('realized')">Realized PnL <span class="sort-arrow" data-col="realized"></span></th>
            <th onclick="sortOpps('status')">Status <span class="sort-arrow" data-col="status"></span></th>
            <th onclick="sortOpps('time')">Time (EST) <span class="sort-arrow" data-col="time"></span></th>
        </tr></thead>
        <tbody></tbody>
    </table>

    <script>
    // Auto-detect base path so the dashboard works both at /dashboard
    // and at /apps/arb-trader/dashboard (behind CloudFront path routing).
    const API_BASE = window.location.pathname.split('/dashboard')[0];

    const WINDOWS = ['5m','15m','1h','4h','8h','24h','3d','1w','1m'];
    let currentWindow = '15m';
    let selectedChain = '';
    let customStart = '';
    let customEnd = '';
    let oppSortField = 'profit';
    let oppSortDesc = true;
    let chainSortField = 'total';
    let chainData = [];
    let oppData = [];

    async function fetchJSON(url) { const r = await fetch(API_BASE + url); return r.json(); }

    // Convert UTC ISO timestamp to EST display string.
    function toEST(isoUtc) {
        if (!isoUtc) return '';
        try {
            // Ensure it's parsed as UTC.
            let d = new Date(isoUtc.endsWith('Z') || isoUtc.includes('+') ? isoUtc : isoUtc + 'Z');
            return d.toLocaleString('en-US', {timeZone:'America/New_York', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
        } catch(e) { return isoUtc.slice(11,19); }
    }
    function toESTShort(isoUtc) {
        if (!isoUtc) return '';
        try {
            let d = new Date(isoUtc.endsWith('Z') || isoUtc.includes('+') ? isoUtc : isoUtc + 'Z');
            return d.toLocaleString('en-US', {timeZone:'America/New_York', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
        } catch(e) { return isoUtc.slice(11,19); }
    }

    // Convert local datetime-local input (EST) to UTC ISO for the API.
    function estInputToUTC(val) {
        if (!val) return '';
        // datetime-local gives us a naive datetime — interpret as EST.
        let d = new Date(val + ':00');
        // Create date in EST timezone.
        let estStr = val + ':00';
        try {
            let parts = val.split('T');
            let [y,m,dy] = parts[0].split('-').map(Number);
            let [hr,mn] = parts[1].split(':').map(Number);
            // Build a date string with EST offset and let Date parse it.
            let est = new Date(`${parts[0]}T${parts[1]}:00-05:00`);
            // During EDT (Mar-Nov) offset is -04:00, but this is close enough.
            // Use Intl to get proper offset.
            let formatter = new Intl.DateTimeFormat('en-US', {timeZone:'America/New_York', timeZoneName:'short'});
            let tzParts = formatter.formatToParts(new Date());
            let tzName = tzParts.find(p => p.type === 'timeZoneName')?.value || 'EST';
            let offset = tzName.includes('EDT') ? '-04:00' : '-05:00';
            est = new Date(`${parts[0]}T${parts[1]}:00${offset}`);
            return est.toISOString();
        } catch(e) { return d.toISOString(); }
    }

    function applyRange() {
        let startVal = document.getElementById('range-start').value;
        let endVal = document.getElementById('range-end').value;
        if (!startVal) return;
        customStart = estInputToUTC(startVal);
        customEnd = endVal ? estInputToUTC(endVal) : '';
        currentWindow = '';
        loadWindows(); loadOpportunities();
    }
    function clearRange() {
        customStart = '';
        customEnd = '';
        document.getElementById('range-start').value = '';
        document.getElementById('range-end').value = '';
        currentWindow = '15m';
        loadWindows(); loadOpportunities();
    }

    function statusClass(val, goodIf) {
        if (goodIf === 'true') return val ? 'status-ok' : 'status-bad';
        if (goodIf === 'false') return val ? 'status-bad' : 'status-ok';
        return '';
    }
    function tagClass(s) {
        if (['approved','included','simulated','dry_run','simulation_approved'].includes(s)) return 'tag-approved';
        if (['rejected','reverted','simulation_failed'].includes(s)) return 'tag-rejected';
        if (['submitted'].includes(s)) return 'tag-submitted';
        return 'tag-detected';
    }
    function isWin(s) { return ['approved','included','simulated','dry_run','submitted','simulation_approved'].includes(s); }

    async function loadChainFilter() {
        const chains = await fetchJSON('/dashboard/distinct-chains');
        const sel = document.getElementById('chain-filter');
        sel.innerHTML = '<option value="">All Chains</option>' +
            chains.map(c => `<option value="${c}" ${c===selectedChain?'selected':''}>${c}</option>`).join('');
    }

    function onChainFilter() {
        selectedChain = document.getElementById('chain-filter').value;
        loadWindows(); loadOpportunities(); loadBarChart();
    }

    async function loadStatus() {
        const [health, exec, pause, metrics, pnl] = await Promise.all([
            fetchJSON('/health'), fetchJSON('/execution'), fetchJSON('/pause'),
            fetchJSON('/metrics'), fetchJSON('/pnl'),
        ]);
        const grid = document.getElementById('status-grid');
        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Execution</div>
                <div class="card-value ${statusClass(exec.execution_enabled,'true')}">${exec.execution_enabled ? 'ENABLED' : 'DISABLED'}</div>
            </div>
            <div class="card">
                <div class="card-title">Paused</div>
                <div class="card-value ${statusClass(pause.paused,'false')}">${pause.paused ? 'YES' : 'NO'}</div>
            </div>
            <div class="card">
                <div class="card-title">Opportunities Detected</div>
                <div class="card-value">${metrics.opportunities_detected}</div>
                <div class="card-sub">${metrics.opportunities_per_minute}/min</div>
            </div>
            <div class="card">
                <div class="card-title">Trades Included</div>
                <div class="card-value">${metrics.executions_included}</div>
                <div class="card-sub">Inclusion: ${metrics.inclusion_rate_pct}% | Revert: ${metrics.revert_rate_pct}%</div>
            </div>
            <div class="card">
                <div class="card-title">Execution PnL</div>
                <div class="card-value">${Number(pnl.total_profit || 0).toFixed(6)}</div>
                <div class="card-sub">${pnl.successful || 0} included / ${pnl.reverted || 0} reverted / ${pnl.not_included || 0} not included</div>
            </div>
            <div class="card">
                <div class="card-title">Avg Latency</div>
                <div class="card-value">${metrics.avg_latency_ms}ms</div>
                <div class="card-sub">P95: ${metrics.p95_latency_ms}ms</div>
            </div>
        `;
    }

    async function loadWindows() {
        const tabs = document.getElementById('window-tabs');
        const customActive = customStart ? 'active' : '';
        tabs.innerHTML = WINDOWS.map(w =>
            `<a class="tab ${w===currentWindow && !customStart?'active':''}" onclick="setWindow('${w}')">${w}</a>`
        ).join('') + (customStart ? `<a class="tab active">Custom</a>` : '');

        let data;
        if (customStart) {
            let url = `/dashboard/range?start=${encodeURIComponent(customStart)}`;
            if (customEnd) url += `&end=${encodeURIComponent(customEnd)}`;
            if (selectedChain) url += `&chain=${selectedChain}`;
            data = await fetchJSON(url);
        } else {
            const chainParam = selectedChain ? `?chain=${selectedChain}` : '';
            data = await fetchJSON(`/dashboard/window/${currentWindow}${chainParam}`);
        }
        const grid = document.getElementById('window-grid');
        const o = data.opportunities || {};
        const t = data.trades || {};
        const p = data.profit || {};
        const totalExpected = Number(p.total_expected_profit || 0);
        const avgExpected = Number(p.avg_expected_profit || 0);
        const maxExpected = Number(p.max_expected_profit || 0);
        const profitCount = p.priced_count || 0;
        const totalRealized = Number(t.total_profit || 0);
        const tradeCount = t.total_trades || 0;
        const hasRealTrades = tradeCount > 0;

        // Show realized profit when we have actual trades, expected otherwise
        const mainProfit = hasRealTrades ? totalRealized : totalExpected;
        const mainLabel = hasRealTrades ? 'Realized Profit' : 'Expected Profit';
        const mainClass = mainProfit > 0 ? 'status-ok' : mainProfit < 0 ? 'status-bad' : '';

        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Opportunities (${currentWindow})</div>
                <div class="card-value">${o.total || 0}</div>
                <div class="card-sub">${profitCount} profitable</div>
            </div>
            <div class="card">
                <div class="card-title">${mainLabel} (${currentWindow})</div>
                <div class="card-value ${mainClass}">${mainProfit.toFixed(6)} ETH</div>
                <div class="card-sub">~$${(mainProfit * 2300).toFixed(2)}</div>
            </div>
            <div class="card">
                <div class="card-title">Avg Profit / Opp (${currentWindow})</div>
                <div class="card-value">${avgExpected.toFixed(6)} ETH</div>
                <div class="card-sub">~$${(avgExpected * 2300).toFixed(2)}</div>
            </div>
            <div class="card">
                <div class="card-title">Best Single Opp (${currentWindow})</div>
                <div class="card-value">${maxExpected.toFixed(6)} ETH</div>
                <div class="card-sub">~$${(maxExpected * 2300).toFixed(2)}</div>
            </div>
            <div class="card">
                <div class="card-title">Trades (${currentWindow})</div>
                <div class="card-value">${tradeCount}</div>
                <div class="card-sub">${t.successful || 0} ok / ${t.reverted || 0} reverted</div>
            </div>
            ${hasRealTrades ? `
            <div class="card">
                <div class="card-title">Expected vs Realized</div>
                <div class="card-value">${((totalRealized / (totalExpected || 1)) * 100).toFixed(1)}%</div>
                <div class="card-sub">Expected: ${totalExpected.toFixed(6)} | Realized: ${totalRealized.toFixed(6)}</div>
            </div>` : `
            <div class="card">
                <div class="card-title">Mode</div>
                <div class="card-value status-warn">SIMULATION</div>
                <div class="card-sub">Enable execution to see realized profit</div>
            </div>`}
        `;
    }

    async function loadChains() {
        chainData = await fetchJSON('/dashboard/chains');
        renderChains();
    }

    function renderChains() {
        let data = [...chainData];
        if (chainSortField === 'chain') data.sort((a,b) => a.chain.localeCompare(b.chain));
        else if (chainSortField === 'approved') data.sort((a,b) => (b.funnel.approved||0) - (a.funnel.approved||0));
        else if (chainSortField === 'rejected') data.sort((a,b) => (b.funnel.rejected||0) - (a.funnel.rejected||0));
        else data.sort((a,b) => b.total - a.total);

        const tbody = document.querySelector('#chain-table tbody');
        let rows = '';
        for (const c of data) {
            const f = c.funnel || {};
            const total = c.total || 0;
            const approved = (f.approved||0) + (f.included||0) + (f.dry_run||0);
            const rejected = f.rejected || 0;
            rows += '<tr>';
            rows += '<td><b>' + c.chain + '</b></td>';
            rows += '<td>' + total + '</td>';
            rows += '<td style="color:#3fb950">' + approved + '</td>';
            rows += '<td style="color:#f85149">' + rejected + '</td>';
            rows += '</tr>';
        }
        tbody.innerHTML = rows;
    }

    function sortChains(field) { chainSortField = field; renderChains(); }

    async function loadOpportunities() {
        let url = '/opportunities?limit=50';
        if (customStart) {
            url += '&start=' + encodeURIComponent(customStart);
            if (customEnd) url += '&end=' + encodeURIComponent(customEnd);
        } else {
            url += '&window=' + currentWindow;
        }
        if (selectedChain) url += '&chain=' + selectedChain;
        oppData = await fetchJSON(url);
        renderOpps();
    }

    function hasExecution(o) {
        return ['submitted','included','reverted','not_included'].includes(o.status);
    }
    function execPriority(s) {
        if (s === 'included') return 6;
        if (s === 'submitted') return 5;
        if (s === 'reverted') return 4;
        if (s === 'not_included') return 3;
        if (['approved','dry_run','simulation_approved','simulated'].includes(s)) return 2;
        if (s === 'rejected' || s === 'simulation_failed') return 0;
        return 1;
    }
    function isApproved(s) {
        return ['approved','included','dry_run','simulation_approved','simulated','submitted','reverted','not_included'].includes(s);
    }

    function renderOpps() {
        const chainLabel = selectedChain ? ' (' + selectedChain + ')' : '';
        document.getElementById('opp-header').textContent =
            'Opportunities (' + currentWindow + chainLabel + ') \u2014 ' + oppData.length + ' found';

        let data = [...oppData];

        // Sort: executed first, then by selected field
        const dir = oppSortDesc ? -1 : 1;
        data.sort((a,b) => {
            // Always: executed transactions first
            const ae = execPriority(a.status);
            const be = execPriority(b.status);
            if (ae !== be) return be - ae;

            // Then by selected column
            if (oppSortField === 'profit') {
                return dir * (Number(b.expected_net_profit||0) - Number(a.expected_net_profit||0));
            } else if (oppSortField === 'realized') {
                return dir * (Number(b.actual_net_profit||0) - Number(a.actual_net_profit||0));
            } else if (oppSortField === 'spread') {
                return dir * (Number(b.spread_bps||0) - Number(a.spread_bps||0));
            } else if (oppSortField === 'status') {
                return dir * (execPriority(b.status) - execPriority(a.status));
            } else if (oppSortField === 'time') {
                return dir * b.detected_at.localeCompare(a.detected_at);
            } else if (oppSortField === 'pair') {
                return dir * a.pair.localeCompare(b.pair);
            } else if (oppSortField === 'chain') {
                return dir * a.chain.localeCompare(b.chain);
            } else if (oppSortField === 'buy') {
                return dir * a.buy_dex.localeCompare(b.buy_dex);
            } else if (oppSortField === 'sell') {
                return dir * a.sell_dex.localeCompare(b.sell_dex);
            } else if (oppSortField === 'id') {
                return dir * a.opportunity_id.localeCompare(b.opportunity_id);
            }
            return 0;
        });

        // Update sort arrows
        document.querySelectorAll('.sort-arrow').forEach(el => {
            const col = el.dataset.col;
            if (col === oppSortField) {
                el.textContent = oppSortDesc ? '\u25BC' : '\u25B2';
                el.className = 'sort-arrow active';
            } else {
                el.textContent = '';
                el.className = 'sort-arrow';
            }
        });

        const tbody = document.querySelector('#opp-table tbody');
        let rows = '';
        for (const o of data.slice(0, 50)) {
            const profitWeth = o.expected_net_profit ? Number(o.expected_net_profit) : 0;
            const profitUsd = (profitWeth * 2300).toFixed(2);
            const profitColor = profitWeth > 0 ? '#3fb950' : '#f85149';
            const showExpected = isApproved(o.status);
            const hasExec = hasExecution(o);
            const rowClass = hasExec ? 'exec-row' : '';
            const rowClick = hasExec ? `onclick="toggleExecDetail('${o.opportunity_id}')"` : '';

            // Realized PnL column
            let realizedHtml = '<span style="color:#484f58">-</span>';
            if (o.actual_net_profit !== null && o.actual_net_profit !== undefined) {
                const rp = Number(o.actual_net_profit);
                const rpUsd = (rp * 2300).toFixed(2);
                const rpColor = rp > 0 ? '#3fb950' : rp < 0 ? '#f85149' : '#8b949e';
                realizedHtml = `<span style="color:${rpColor};font-weight:bold">$${rpUsd}</span>`;
            }

            rows += `<tr class="${rowClass}" ${rowClick}>
                <td><a href="${API_BASE}/opportunity/${o.opportunity_id}" style="color:#58a6ff" onclick="event.stopPropagation()">${o.opportunity_id.slice(4,16)}</a></td>
                <td>${o.pair}</td>
                <td>${o.chain}</td>
                <td>${o.buy_dex}</td>
                <td>${o.sell_dex}</td>
                <td>${(Number(o.spread_bps)/100).toFixed(2)}%</td>
                <td>${showExpected ? `<span style="color:${profitColor};font-weight:bold">$${profitUsd}</span>` : '<span style="color:#484f58">-</span>'}</td>
                <td>${realizedHtml}</td>
                <td><span class="tag ${tagClass(o.status)}">${o.status}</span></td>
                <td title="${o.detected_at}">${toESTShort(o.detected_at)}</td>
            </tr>`;

            // Expandable execution detail row
            if (hasExec) {
                const txHash = o.tx_hash || 'n/a';
                const txShort = txHash.length > 12 ? txHash.slice(0,10) + '...' : txHash;
                const gasUsed = o.exec_gas_used || 0;
                const gasCost = o.exec_gas_cost_base ? Number(o.exec_gas_cost_base).toFixed(6) : '0';
                const realProfit = o.realized_profit_quote ? Number(o.realized_profit_quote).toFixed(4) : '0';
                const netProfit = o.actual_net_profit ? Number(o.actual_net_profit).toFixed(6) : '0';
                const incl = o.exec_included ? 'YES' : 'NO';
                const rev = o.exec_reverted ? 'YES' : 'NO';
                const profCur = o.profit_currency || '';

                rows += `<tr class="exec-detail" id="detail-${o.opportunity_id}">
                    <td colspan="10">
                        <div class="exec-grid">
                            <div class="exec-item"><div class="exec-label">TX Hash</div>
                                <div class="exec-value">${txHash !== 'n/a' ? `<a href="https://arbiscan.io/tx/${txHash}" target="_blank" style="color:#58a6ff">${txShort}</a>` : 'n/a'}</div></div>
                            <div class="exec-item"><div class="exec-label">Included</div>
                                <div class="exec-value" style="color:${o.exec_included ? '#3fb950' : '#f85149'}">${incl}</div></div>
                            <div class="exec-item"><div class="exec-label">Reverted</div>
                                <div class="exec-value" style="color:${o.exec_reverted ? '#f85149' : '#3fb950'}">${rev}</div></div>
                            <div class="exec-item"><div class="exec-label">Gas Used</div>
                                <div class="exec-value">${gasUsed.toLocaleString()}</div></div>
                            <div class="exec-item"><div class="exec-label">Gas Cost</div>
                                <div class="exec-value">${gasCost} ETH</div></div>
                            <div class="exec-item"><div class="exec-label">Realized Profit ${profCur}</div>
                                <div class="exec-value">${realProfit}</div></div>
                            <div class="exec-item"><div class="exec-label">Net PnL (base)</div>
                                <div class="exec-value" style="color:${Number(netProfit) >= 0 ? '#3fb950' : '#f85149'}">${netProfit} ETH</div></div>
                            <div class="exec-item"><div class="exec-label">Expected</div>
                                <div class="exec-value">${profitUsd !== '0.00' ? '$'+profitUsd : '-'}</div></div>
                        </div>
                    </td>
                </tr>`;
            }
        }
        tbody.innerHTML = rows;
    }

    function sortOpps(field) {
        if (oppSortField === field) {
            oppSortDesc = !oppSortDesc;
        } else {
            oppSortField = field;
            oppSortDesc = true;
        }
        renderOpps();
    }

    function toggleExecDetail(oppId) {
        const row = document.getElementById('detail-' + oppId);
        if (row) row.classList.toggle('open');
    }

    async function loadBarChart() {
        const rows = await fetchJSON('/dashboard/hourly-bars');
        const container = document.getElementById('bar-chart-container');

        // Group by chain.
        const chains = {};
        for (const r of rows) {
            if (selectedChain && r.chain !== selectedChain) continue;
            if (!chains[r.chain]) chains[r.chain] = { wins: 0, losses: 0 };
            if (isWin(r.status)) chains[r.chain].wins += r.cnt;
            else chains[r.chain].losses += r.cnt;
        }

        const sorted = Object.entries(chains).sort((a,b) => (b[1].wins+b[1].losses) - (a[1].wins+a[1].losses));
        const maxVal = Math.max(1, ...sorted.map(([,v]) => v.wins + v.losses));

        if (sorted.length === 0) {
            container.innerHTML = '<div class="bar-chart" style="justify-content:center;align-items:center;color:#484f58">No data for selected filter</div>';
            return;
        }

        container.innerHTML = '<div class="bar-chart">' + sorted.map(([chain, v]) => {
            const winH = Math.max(2, (v.wins / maxVal) * 90);
            const lossH = Math.max(0, (v.losses / maxVal) * 90);
            return `<div class="bar-group">
                <div class="bar-stack">
                    <div class="bar-win" style="height:${winH}px" title="${v.wins} wins"></div>
                    ${v.losses > 0 ? `<div class="bar-loss" style="height:${lossH}px" title="${v.losses} losses"></div>` : ''}
                </div>
                <div class="bar-label">${chain}</div>
                <div class="bar-label" style="color:#3fb950">${v.wins}</div>
                <div class="bar-label" style="color:#f85149">${v.losses}</div>
            </div>`;
        }).join('') + '</div>';
    }

    function setWindow(w) {
        customStart = ''; customEnd = '';
        document.getElementById('range-start').value = '';
        document.getElementById('range-end').value = '';
        currentWindow = w;
        loadWindows(); loadOpportunities(); loadBarChart();
    }

    // --- Scanner control functions ---

    async function loadScannerStatus() {
        const data = await fetchJSON('/scanner');
        const el = document.getElementById('scanner-status');
        if (data.running) {
            el.textContent = 'RUNNING';
            el.className = 'tag tag-approved';
        } else {
            el.textContent = 'STOPPED';
            el.className = 'tag tag-rejected';
        }
        const execBtn = document.getElementById('exec-toggle');
        if (data.execution_enabled) {
            execBtn.textContent = 'GLOBAL: LIVE — Click to Disable';
            execBtn.className = 'btn btn-red';
        } else {
            execBtn.textContent = 'GLOBAL: SIMULATION — Click to Enable';
            execBtn.className = 'btn btn-gray';
        }
    }

    async function loadChainExecStatus() {
        const data = await fetchJSON('/execution');
        const tbody = document.getElementById('chain-exec-body');
        const chains = data.chains || {};
        let rows = '';
        for (const [chain, info] of Object.entries(chains)) {
            const mode = info.mode;
            let modeTag, modeColor;
            if (mode === 'live') {
                modeTag = 'LIVE'; modeColor = 'tag-approved';
            } else if (mode === 'simulated') {
                modeTag = 'SIMULATED'; modeColor = 'tag-submitted';
            } else {
                modeTag = 'DISABLED'; modeColor = 'tag-rejected';
            }
            const routers = info.has_routers ? '<span style="color:#3fb950">Yes</span>' : '<span style="color:#f85149">No</span>';
            const aave = info.has_aave ? '<span style="color:#3fb950">Yes</span>' : '<span style="color:#f85149">No</span>';

            let actions = '';
            if (info.executable) {
                if (mode === 'live') {
                    actions = `<button class="btn btn-red" style="padding:3px 10px;font-size:11px" onclick="setChainMode('${chain}','simulated')">Pause</button>`;
                    actions += ` <button class="btn btn-gray" style="padding:3px 10px;font-size:11px" onclick="setChainMode('${chain}','disabled')">Disable</button>`;
                } else if (mode === 'simulated') {
                    actions = `<button class="btn btn-green" style="padding:3px 10px;font-size:11px" onclick="setChainMode('${chain}','live')">Go Live</button>`;
                    actions += ` <button class="btn btn-gray" style="padding:3px 10px;font-size:11px" onclick="setChainMode('${chain}','disabled')">Disable</button>`;
                } else {
                    actions = `<button class="btn btn-green" style="padding:3px 10px;font-size:11px" onclick="setChainMode('${chain}','simulated')">Enable</button>`;
                }
            } else {
                actions = '<span style="color:#484f58;font-size:11px">Not executable</span>';
            }

            rows += `<tr>
                <td><b>${chain}</b></td>
                <td><span class="tag ${modeColor}">${modeTag}</span></td>
                <td>${routers}</td>
                <td>${aave}</td>
                <td>${actions}</td>
            </tr>`;
        }
        tbody.innerHTML = rows;
    }

    async function setChainMode(chain, mode) {
        await fetch(API_BASE + '/execution', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({chain: chain, mode: mode}),
        });
        setTimeout(() => { loadChainExecStatus(); loadScannerStatus(); }, 300);
    }

    async function controlScanner(action) {
        await fetch(API_BASE + '/scanner/' + action, {method: 'POST'});
        setTimeout(loadScannerStatus, 500);
    }

    async function toggleExecution() {
        const current = await fetchJSON('/execution');
        await fetch(API_BASE + '/execution', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled: !current.execution_enabled}),
        });
        setTimeout(() => { loadScannerStatus(); loadChainExecStatus(); }, 300);
    }

    async function loadWalletBalance() {
        try {
            const data = await fetchJSON('/wallet/balance');
            const grid = document.getElementById('wallet-grid');
            if (!data.address || data.error) {
                grid.innerHTML = '';
                return;
            }
            const addr = data.address;
            const short = addr.slice(0,6) + '...' + addr.slice(-4);
            let cards = `<div class="card">
                <div class="card-title">Wallet</div>
                <div class="card-value" style="font-size:16px"><a href="https://arbiscan.io/address/${addr}" target="_blank" style="color:#58a6ff">${short}</a></div>
            </div>`;
            let totalEth = 0;
            for (const [chain, bal] of Object.entries(data.balances)) {
                if (bal === null) continue;
                totalEth += bal;
                const usd = (bal * 2300).toFixed(2);
                const color = bal > 0.001 ? '#3fb950' : '#f85149';
                cards += `<div class="card">
                    <div class="card-title">${chain} Balance</div>
                    <div class="card-value" style="color:${color}">${bal.toFixed(6)} ETH</div>
                    <div class="card-sub">~$${usd}</div>
                </div>`;
            }
            const totalUsd = (totalEth * 2300).toFixed(2);
            cards += `<div class="card">
                <div class="card-title">Total Balance</div>
                <div class="card-value">${totalEth.toFixed(6)} ETH</div>
                <div class="card-sub">~$${totalUsd}</div>
            </div>`;
            grid.innerHTML = cards;
        } catch(e) { console.warn('Wallet balance load failed:', e); }
    }

    async function init() {
        await loadChainFilter();
        await Promise.all([loadStatus(), loadWindows(), loadChains(), loadOpportunities(), loadBarChart(), loadScannerStatus(), loadChainExecStatus()]);
        // Load wallet balance async (non-blocking, may be slow due to RPC calls)
        loadWalletBalance();
    }
    init();
    // Refresh scanner and chain status every 10 seconds.
    setInterval(loadScannerStatus, 10000);
    setInterval(loadChainExecStatus, 10000);
    </script>
</body>
</html>"""


OPPORTUNITY_DETAIL_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Opportunity Detail</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
               background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 8px; }
        h2 { color: #8b949e; margin: 24px 0 10px; font-size: 14px; text-transform: uppercase; }
        .back { color: #58a6ff; text-decoration: none; margin-bottom: 16px; display: inline-block; }
        .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                   padding: 16px; margin-bottom: 16px; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 8px 12px; color: #8b949e; font-size: 12px;
             text-transform: uppercase; width: 200px; }
        td { padding: 8px 12px; font-size: 14px; }
        .tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; }
        .tag-approved { background: #23863533; color: #3fb950; }
        .tag-rejected { background: #da362933; color: #f85149; }
        .tag-detected { background: #1f6feb33; color: #58a6ff; }
        .mono { font-family: monospace; color: #f0f6fc; }
        .empty { color: #484f58; font-style: italic; }
    </style>
</head>
<body>
    <h1 id="title">Loading...</h1>
    <div id="content"></div>

    <script>
    // Auto-detect base path (works at /opportunity/X and /apps/arb-trader/opportunity/X)
    const API_BASE = window.location.pathname.split('/opportunity/')[0];
    const oppId = window.location.pathname.split('/').pop();

    document.getElementById('title').insertAdjacentHTML('beforebegin',
        `<a class="back" href="${API_BASE}/dashboard">&larr; Back to Dashboard</a>`);

    function val(v) { return v !== null && v !== undefined ? v : '<span class="empty">n/a</span>'; }
    function num(v, d=6) { return v !== null && v !== undefined ? Number(v).toFixed(d) : '<span class="empty">n/a</span>'; }
    function toEST(isoUtc) {
        if (!isoUtc) return 'n/a';
        try {
            let d = new Date(isoUtc.endsWith('Z') || isoUtc.includes('+') ? isoUtc : isoUtc + 'Z');
            return d.toLocaleString('en-US', {timeZone:'America/New_York', year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false}) + ' EST';
        } catch(e) { return isoUtc; }
    }

    function renderSection(title, rows) {
        if (!rows) return `<h2>${title}</h2><div class="section"><p class="empty">No data</p></div>`;
        let html = `<h2>${title}</h2><div class="section"><table>`;
        for (const [k, v] of Object.entries(rows)) {
            html += `<tr><th>${k.replace(/_/g,' ')}</th><td class="mono">${val(v)}</td></tr>`;
        }
        html += '</table></div>';
        return html;
    }

    async function load() {
        const resp = await fetch(`${API_BASE}/opportunities/${oppId}/full`);
        if (!resp.ok) { document.getElementById('title').textContent = 'Not Found'; return; }
        const data = await resp.json();

        const o = data.opportunity;
        document.getElementById('title').textContent = `${o.pair} — ${o.opportunity_id}`;

        let html = '';

        // Opportunity overview
        html += renderSection('Opportunity', {
            'ID': o.opportunity_id,
            'Pair': o.pair,
            'Chain': o.chain || 'unknown',
            'Buy DEX': o.buy_dex,
            'Sell DEX': o.sell_dex,
            'Spread': num(o.spread_bps / 100, 4) + '%',
            'Status': `<span class="tag tag-${['approved','included','dry_run','simulation_approved'].includes(o.status)?'approved':o.status==='rejected'?'rejected':'detected'}">${o.status}</span>`,
            'Detected At': toEST(o.detected_at),
            'Updated At': toEST(o.updated_at),
        });

        // Pricing — cost waterfall breakdown
        const p = data.pricing;
        if (p) {
            const inp = Number(p.input_amount || 0);
            const out = Number(p.estimated_output || 0);
            const fee = Number(p.fee_cost || 0);
            const slip = Number(p.slippage_cost || 0);
            const gas = Number(p.gas_estimate || 0);
            const net = Number(p.expected_net_profit || 0);
            const netUsd = (net * 2220).toFixed(2);
            const netColor = net > 0 ? '#3fb950' : '#f85149';

            html += `<h2>Cost Breakdown</h2><div class="section">
            <table>
                <tr><th>Buy Cost (input)</th><td class="mono">$${inp.toFixed(2)}</td></tr>
                <tr><th>Sell Proceeds (output)</th><td class="mono">$${out.toFixed(2)}</td></tr>
                <tr style="border-top:1px solid #30363d">
                    <th>Gross Spread</th>
                    <td class="mono">$${(out - inp).toFixed(2)}</td></tr>
                <tr><th style="padding-left:30px">- DEX Fees</th>
                    <td class="mono" style="color:#f85149">-$${fee.toFixed(4)}</td></tr>
                <tr><th style="padding-left:30px">- Slippage</th>
                    <td class="mono" style="color:#f85149">-$${slip.toFixed(4)}</td></tr>
                <tr><th style="padding-left:30px">- Gas</th>
                    <td class="mono" style="color:#f85149">-${gas.toFixed(6)} ETH</td></tr>
                <tr style="border-top:2px solid #58a6ff">
                    <th style="font-size:14px;color:#f0f6fc">Net Profit</th>
                    <td class="mono" style="font-size:16px;font-weight:bold;color:${netColor}">
                        ${net.toFixed(6)} ETH (~$${netUsd})
                    </td></tr>
            </table>
            <div style="margin-top:8px;color:#484f58;font-size:11px">Priced at ${toEST(p.created_at)}</div>
            </div>`;
        } else {
            html += renderSection('Cost Breakdown', null);
        }

        // Risk Decision
        const r = data.risk_decision;
        if (r) {
            let thresholds = r.threshold_snapshot;
            let details = {};
            try { details = typeof thresholds === 'string' ? JSON.parse(thresholds) : thresholds; } catch(e) {}

            const isSimApproved = r.reason_code === 'simulation_approved';
            const verdictTag = isSimApproved
                ? '<span class="tag tag-approved">SIMULATION APPROVED</span>'
                : r.approved
                    ? '<span class="tag tag-approved">APPROVED</span>'
                    : '<span class="tag tag-rejected">REJECTED</span>';

            let riskHtml = `<h2>Risk Decision</h2><div class="section"><table>
                <tr><th>Verdict</th><td class="mono">${verdictTag}</td></tr>
                <tr><th>Reason</th><td class="mono">${r.reason_code}</td></tr>`;

            if (details.reason_detail)
                riskHtml += `<tr><th>Detail</th><td class="mono">${details.reason_detail}</td></tr>`;

            riskHtml += `<tr style="border-top:1px solid #30363d"><th colspan="2" style="color:#58a6ff;padding-top:12px">Analysis</th></tr>`;

            if (details.net_profit !== undefined)
                riskHtml += `<tr><th>Net Profit</th><td class="mono">${num(details.net_profit, 6)} ETH</td></tr>`;
            if (details.gross_spread_pct !== undefined)
                riskHtml += `<tr><th>Gross Spread</th><td class="mono">${num(details.gross_spread_pct, 4)}%</td></tr>`;

            // Fee breakdown
            const hasFees = details.dex_fees || details.flash_loan_fee || details.slippage_cost || details.gas_cost;
            if (hasFees) {
                riskHtml += `<tr style="border-top:1px solid #30363d"><th colspan="2" style="color:#58a6ff;padding-top:12px">Fee Components</th></tr>`;
                if (details.dex_fees && details.dex_fees !== '0')
                    riskHtml += `<tr><th>DEX Fees</th><td class="mono">$${num(details.dex_fees, 4)}</td></tr>`;
                if (details.fee_included !== undefined)
                    riskHtml += `<tr><th>Fee Pre-Included</th><td class="mono">${details.fee_included ? '<span style="color:#3fb950">Yes</span> (on-chain quoter)' : 'No (calculated)'}</td></tr>`;
                if (details.flash_loan_fee && details.flash_loan_fee !== '0')
                    riskHtml += `<tr><th>Flash Loan Fee</th><td class="mono">$${num(details.flash_loan_fee, 4)}</td></tr>`;
                if (details.slippage_cost && details.slippage_cost !== '0')
                    riskHtml += `<tr><th>Slippage</th><td class="mono">$${num(details.slippage_cost, 4)}</td></tr>`;
                if (details.gas_cost && details.gas_cost !== '0')
                    riskHtml += `<tr><th>Gas Cost</th><td class="mono">${num(details.gas_cost, 6)} ETH</td></tr>`;
            }

            // Risk signals
            const hasRiskSignals = details.liquidity_score !== undefined || (details.warning_flags && details.warning_flags.length > 0);
            if (hasRiskSignals) {
                riskHtml += `<tr style="border-top:1px solid #30363d"><th colspan="2" style="color:#58a6ff;padding-top:12px">Risk Signals</th></tr>`;
                if (details.liquidity_score !== undefined)
                    riskHtml += `<tr><th>Liquidity Score</th><td class="mono">${details.liquidity_score}</td></tr>`;
                if (details.warning_flags && details.warning_flags.length > 0)
                    riskHtml += `<tr><th>Warning Flags</th><td class="mono" style="color:#f85149">${details.warning_flags.join(', ')}</td></tr>`;
            }

            if (details.simulation)
                riskHtml += `<tr><th>Mode</th><td class="mono"><span class="tag tag-approved">SIMULATION</span> — would execute if live</td></tr>`;

            riskHtml += `<tr style="border-top:1px solid #30363d"><th>Decided At</th><td class="mono">${toEST(r.created_at)}</td></tr>`;
            riskHtml += '</table></div>';
            html += riskHtml;
        } else {
            html += renderSection('Risk Decision', null);
        }

        // Simulation
        const s = data.simulation;
        if (s) {
            html += renderSection('Simulation', {
                'Success': s.success ? '<span class="tag tag-approved">PASS</span>' : '<span class="tag tag-rejected">FAIL</span>',
                'Revert Reason': s.revert_reason || 'n/a',
                'Expected Output': num(s.expected_output, 6),
                'Expected Net Profit': num(s.expected_net_profit, 6),
                'Simulated At': toEST(s.created_at),
            });
        } else {
            html += renderSection('Simulation', null);
        }

        const e = data.execution_attempt;
        const tr = data.trade_result;
        if (e || tr) {
            let execRows = {};
            if (e) {
                execRows['Submission Type'] = e.submission_type || 'n/a';
                execRows['TX Hash'] = e.tx_hash || 'n/a';
                execRows['Bundle ID'] = e.bundle_id || 'n/a';
                execRows['Target Block'] = e.target_block || 'n/a';
                execRows['Submitted At'] = toEST(e.submitted_at);
            }
            if (tr) {
                execRows['Included'] = tr.included ? '<span class="tag tag-approved">YES</span>' : '<span class="tag tag-detected">NO</span>';
                execRows['Reverted'] = tr.reverted ? '<span class="tag tag-rejected">YES</span>' : 'NO';
                execRows['Gas Used'] = tr.gas_used;
                execRows['Realized Quote Profit'] = `${num(tr.realized_profit_quote, 6)} ${tr.profit_currency || ''}`.trim();
                execRows['Gas Cost (Base)'] = `${num(tr.gas_cost_base, 6)} ETH`;
                execRows['Net Profit (Base)'] = `${num(tr.actual_net_profit, 6)} ETH`;
                execRows['Finalized At'] = toEST(tr.finalized_at);
            }
            html += renderSection('Execution', execRows);
        } else {
            html += renderSection('Execution', null);
        }

        document.getElementById('content').innerHTML = html;
    }
    load();
    </script>
</body>
</html>"""


OPS_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Ops &amp; Diagnostics</title>
    <meta http-equiv="refresh" content="30">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
               background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 4px; }
        h2 { color: #8b949e; margin: 24px 0 10px; font-size: 14px; text-transform: uppercase; }
        a { color: #58a6ff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card-title { font-size: 12px; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; }
        .card-value { font-size: 28px; font-weight: bold; color: #f0f6fc; }
        .card-sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
        .status-ok { color: #3fb950; }
        .status-warn { color: #d29922; }
        .status-bad { color: #f85149; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 8px; border-bottom: 2px solid #30363d; color: #8b949e;
             font-size: 12px; text-transform: uppercase; cursor: pointer; }
        th:hover { color: #58a6ff; }
        td { padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
        tr:hover { background: #1c2128; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; }
        .tag-ok { background: #23863533; color: #3fb950; }
        .tag-warn { background: #9e6a0333; color: #d29922; }
        .tag-bad { background: #da362933; color: #f85149; }
        .trend-bar { display: inline-block; height: 14px; border-radius: 3px; vertical-align: middle; }
        .mini-chart { display: flex; align-items: flex-end; gap: 2px; height: 40px; }
        .mini-bar { flex: 1; min-width: 3px; max-width: 8px; border-radius: 2px 2px 0 0; }
    </style>
</head>
<body>
    <a href="dashboard">&larr; Back to Dashboard</a>
    <h1>Operations &amp; Diagnostics</h1>

    <!-- Infrastructure -->
    <h2>Infrastructure</h2>
    <div class="grid" id="infra-grid"></div>

    <!-- RPC Health -->
    <h2>RPC Endpoints</h2>
    <div class="grid" id="rpc-grid"></div>

    <!-- DEX Health Table -->
    <h2>DEX Health (per pair)</h2>
    <table id="dex-table">
        <thead><tr>
            <th>DEX</th><th>Chain</th><th>Pair</th>
            <th>Success Rate</th><th>Quotes</th>
            <th>Avg Latency</th><th>Last Outcome</th><th>Last Error</th>
        </tr></thead>
        <tbody></tbody>
    </table>

    <!-- Metrics Trend -->
    <h2>Scan Metrics</h2>
    <div class="grid" id="metrics-grid"></div>

    <!-- Risk Policy -->
    <h2>Risk Policy</h2>
    <div class="grid" id="risk-grid"></div>

    <script>
    const API_BASE = window.location.pathname.split('/ops')[0];

    async function fetchJSON(url) {
        const r = await fetch(API_BASE + url);
        return r.json();
    }

    async function loadInfra() {
        const [ops, pnl] = await Promise.all([fetchJSON('/operations'), fetchJSON('/pnl')]);
        const blockers = (ops.launch_blockers || []).slice(0, 2).join(', ') || 'none';
        document.getElementById('infra-grid').innerHTML = `
            <div class="card">
                <div class="card-title">Live Stack</div>
                <div class="card-value ${ops.live_stack_ready ? 'status-ok' : 'status-warn'}">${ops.live_stack_ready ? 'READY' : 'SIMULATION ONLY'}</div>
                <div class="card-sub">Target: ${ops.live_rollout_target || 'none'}</div>
            </div>
            <div class="card">
                <div class="card-title">Launch Ready</div>
                <div class="card-value ${ops.launch_ready ? 'status-ok' : 'status-warn'}">${ops.launch_ready ? 'YES' : 'NOT YET'}</div>
                <div class="card-sub">${ops.launch_chain || 'none'} | ${blockers}</div>
            </div>
            <div class="card">
                <div class="card-title">DB Backend</div>
                <div class="card-value">${ops.db_backend || 'unknown'}</div>
            </div>
            <div class="card">
                <div class="card-title">Discovered Pairs</div>
                <div class="card-value">${ops.discovered_pairs_count || 0}</div>
                <div class="card-sub">Snapshot: ${ops.last_discovery_pair_count || 0} pairs</div>
            </div>
            <div class="card">
                <div class="card-title">Discovery Source</div>
                <div class="card-value">${ops.discovery_snapshot_source || 'none'}</div>
            </div>
            <div class="card">
                <div class="card-title">Enabled Pools</div>
                <div class="card-value">${ops.enabled_pools_total || 0}</div>
                <div class="card-sub">Last sync: ${ops.last_monitored_pools_synced || 0} inserted</div>
            </div>
            <div class="card">
                <div class="card-title">Executable Chains</div>
                <div class="card-value">${(ops.live_executable_chains || []).length}</div>
                <div class="card-sub">${(ops.live_executable_chains || []).join(', ') || 'none'}</div>
            </div>
            <div class="card">
                <div class="card-title">Executable Venues</div>
                <div class="card-value">${(ops.live_executable_dexes || []).length}</div>
                <div class="card-sub">${(ops.live_executable_dexes || []).slice(0,3).join(', ') || 'none'}</div>
            </div>
            <div class="card">
                <div class="card-title">Executor Config</div>
                <div class="card-value">${ops.executor_contract_configured && ops.executor_key_configured ? 'SET' : 'MISSING'}</div>
                <div class="card-sub">key=${ops.executor_key_configured ? 'yes' : 'no'} contract=${ops.executor_contract_configured ? 'yes' : 'no'} rpc=${ops.rpc_configured ? 'yes' : 'no'}</div>
            </div>
            <div class="card">
                <div class="card-title">Realized Quote Profit</div>
                <div class="card-value">${Number(pnl.total_realized_profit_quote || 0).toFixed(6)}</div>
                <div class="card-sub">Raw quote-token payout before base conversion</div>
            </div>
            <div class="card">
                <div class="card-title">Gas Cost (Base)</div>
                <div class="card-value">${Number(pnl.total_gas_cost_base || 0).toFixed(6)}</div>
                <div class="card-sub">Stored separately from realized quote profit</div>
            </div>
            <div class="card">
                <div class="card-title">Net Profit (Base)</div>
                <div class="card-value">${Number(pnl.total_profit || 0).toFixed(6)}</div>
                <div class="card-sub">Only populated when base conversion is safe</div>
            </div>
        `;
    }

    async function loadDexHealth() {
        try {
            const diag = await fetchJSON('/diagnostics/quotes');
            const dexes = diag.dexes || {};

            // RPC summary cards — group by chain
            const chainHealth = {};
            for (const [dex, entries] of Object.entries(dexes)) {
                for (const e of entries) {
                    const parts = e.key.split(':');
                    const chain = parts[1] || '?';
                    if (!chainHealth[chain]) chainHealth[chain] = { ok: 0, fail: 0, total: 0, latencies: [] };
                    chainHealth[chain].total += e.total_quotes;
                    chainHealth[chain].ok += e.success_count;
                    chainHealth[chain].fail += e.total_quotes - e.success_count;
                    if (e.avg_latency_ms > 0) chainHealth[chain].latencies.push(e.avg_latency_ms);
                }
            }
            const rpcGrid = document.getElementById('rpc-grid');
            rpcGrid.innerHTML = Object.entries(chainHealth).sort((a,b) => b[1].total - a[1].total).map(([chain, h]) => {
                const rate = h.total > 0 ? h.ok / h.total : 0;
                const avgLat = h.latencies.length > 0 ? h.latencies.reduce((a,b)=>a+b,0)/h.latencies.length : 0;
                const cls = rate >= 0.8 ? 'tag-ok' : rate >= 0.3 ? 'tag-warn' : 'tag-bad';
                const barW = Math.round(rate * 100);
                const barColor = rate >= 0.8 ? '#3fb950' : rate >= 0.3 ? '#d29922' : '#f85149';
                return `<div class="card">
                    <div class="card-title">${chain}</div>
                    <div class="card-value"><span class="tag ${cls}">${(rate*100).toFixed(0)}%</span></div>
                    <div style="margin:8px 0;background:#21262d;border-radius:4px;height:14px">
                        <div class="trend-bar" style="width:${barW}%;background:${barColor}"></div>
                    </div>
                    <div class="card-sub">${h.ok}/${h.total} quotes OK | avg ${avgLat.toFixed(0)}ms</div>
                </div>`;
            }).join('');

            // Detailed DEX table
            const rows = [];
            for (const [dex, entries] of Object.entries(dexes)) {
                for (const e of entries) {
                    const parts = e.key.split(':');
                    rows.push({ dex, chain: parts[1]||'?', pair: parts[2]||'?', ...e });
                }
            }
            rows.sort((a,b) => a.success_rate - b.success_rate);

            const tbody = document.querySelector('#dex-table tbody');
            tbody.innerHTML = rows.map(r => {
                const cls = r.success_rate >= 0.8 ? 'tag-ok' : r.success_rate >= 0.3 ? 'tag-warn' : 'tag-bad';
                const outCls = r.last_outcome === 'success' ? 'status-ok' :
                               r.last_outcome === 'cached_skip' ? 'status-warn' : 'status-bad';
                return `<tr>
                    <td><b>${r.dex}</b></td>
                    <td>${r.chain}</td>
                    <td>${r.pair}</td>
                    <td><span class="tag ${cls}">${(r.success_rate*100).toFixed(0)}%</span></td>
                    <td>${r.success_count}/${r.total_quotes}</td>
                    <td>${r.avg_latency_ms > 0 ? r.avg_latency_ms.toFixed(0)+'ms' : '-'}</td>
                    <td class="${outCls}">${r.last_outcome || '-'}</td>
                    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;color:#8b949e;font-size:11px">${r.last_error || '-'}</td>
                </tr>`;
            }).join('');

        } catch(e) { console.warn('DEX health load failed:', e); }
    }

    async function loadMetrics() {
        const [m, pnl] = await Promise.all([fetchJSON('/metrics'), fetchJSON('/pnl')]);
        const grid = document.getElementById('metrics-grid');
        const upH = (m.uptime_seconds / 3600).toFixed(1);
        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Uptime</div>
                <div class="card-value">${upH}h</div>
                <div class="card-sub">${Math.round(m.uptime_seconds)}s</div>
            </div>
            <div class="card">
                <div class="card-title">Opportunities / min</div>
                <div class="card-value">${m.opportunities_per_minute}</div>
                <div class="card-sub">${m.opportunities_detected} total detected</div>
            </div>
            <div class="card">
                <div class="card-title">Rejected</div>
                <div class="card-value">${m.opportunities_rejected}</div>
                <div class="card-sub">${Object.entries(m.rejection_reasons || {}).map(([k,v]) => k+': '+v).join(', ') || 'none'}</div>
            </div>
            <div class="card">
                <div class="card-title">Avg Pipeline Latency</div>
                <div class="card-value">${m.avg_latency_ms}ms</div>
                <div class="card-sub">P95: ${m.p95_latency_ms}ms</div>
            </div>
            <div class="card">
                <div class="card-title">Executions</div>
                <div class="card-value">${m.executions_submitted}</div>
                <div class="card-sub">Included: ${m.executions_included} | Reverted: ${m.executions_reverted}</div>
            </div>
            <div class="card">
                <div class="card-title">Execution PnL</div>
                <div class="card-value ${Number(pnl.total_profit || 0) > 0 ? 'status-ok' : Number(pnl.total_profit || 0) < 0 ? 'status-bad' : ''}">${Number(pnl.total_profit || 0).toFixed(6)} ETH</div>
                <div class="card-sub">${pnl.successful || 0} included / ${pnl.reverted || 0} reverted / ${pnl.not_included || 0} not included</div>
            </div>
            <div class="card">
                <div class="card-title">Total Expected Profit</div>
                <div class="card-value ${m.total_expected_profit > 0 ? 'status-ok' : ''}">${Number(m.total_expected_profit).toFixed(6)} ETH</div>
                <div class="card-sub">~$${(m.total_expected_profit * 2300).toFixed(2)}</div>
            </div>
        `;
    }

    async function loadRiskPolicy() {
        const r = await fetchJSON('/risk/policy');
        const grid = document.getElementById('risk-grid');
        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Min Net Profit</div>
                <div class="card-value">${Number(r.min_net_profit).toFixed(4)} ETH</div>
                <div class="card-sub">~$${(Number(r.min_net_profit) * 2300).toFixed(2)}</div>
            </div>
            <div class="card">
                <div class="card-title">Min Spread</div>
                <div class="card-value">${r.min_spread_pct}%</div>
            </div>
            <div class="card">
                <div class="card-title">Max Slippage</div>
                <div class="card-value">${r.max_slippage_bps} bps</div>
            </div>
            <div class="card">
                <div class="card-title">Min Liquidity</div>
                <div class="card-value">$${Number(r.min_liquidity_usd).toLocaleString()}</div>
            </div>
            <div class="card">
                <div class="card-title">Max Quote Age</div>
                <div class="card-value">${r.max_quote_age_seconds}s</div>
            </div>
            <div class="card">
                <div class="card-title">Execution</div>
                <div class="card-value ${r.execution_enabled ? 'status-ok' : 'status-warn'}">${r.execution_enabled ? 'LIVE' : 'SIMULATION'}</div>
            </div>
        `;
    }

    async function init() {
        await Promise.all([loadInfra(), loadDexHealth(), loadMetrics(), loadRiskPolicy()]);
    }
    init();
    </script>
</body>
</html>"""


ANALYTICS_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>PnL Analytics</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
               background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 4px; }
        h2 { color: #8b949e; margin: 24px 0 10px; font-size: 14px; text-transform: uppercase; }
        a { color: #58a6ff; text-decoration: none; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card-title { font-size: 12px; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; }
        .card-value { font-size: 28px; font-weight: bold; color: #f0f6fc; }
        .card-sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
        .status-ok { color: #3fb950; }
        .status-bad { color: #f85149; }
        .status-warn { color: #d29922; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 8px; border-bottom: 2px solid #30363d; color: #8b949e;
             font-size: 12px; text-transform: uppercase; }
        td { padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
        tr:hover { background: #1c2128; }
        .filters { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
        .filters label { color: #8b949e; font-size: 13px; }
        select, input[type=date] { background: #161b22; color: #c9d1d9; border: 1px solid #30363d;
                 border-radius: 6px; padding: 6px 12px; font-size: 13px; }
        .btn { padding: 6px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
               border: 1px solid #30363d; background: #1f6feb; color: #fff; font-weight: bold; }
        .btn:hover { background: #388bfd; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { padding: 6px 16px; border-radius: 6px; background: #21262d; color: #8b949e;
               cursor: pointer; font-size: 13px; border: 1px solid #30363d; text-decoration: none; }
        .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
        .bar-chart { display: flex; align-items: flex-end; gap: 3px; height: 140px;
                     background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                     padding: 12px; margin-top: 8px; overflow-x: auto; }
        .bar-group { display: flex; flex-direction: column; align-items: center; min-width: 20px; }
        .bar-pos { background: #3fb950; border-radius: 2px 2px 0 0; min-width: 14px; }
        .bar-neg { background: #f85149; border-radius: 0 0 2px 2px; min-width: 14px; }
        .bar-label { font-size: 9px; color: #484f58; margin-top: 2px; white-space: nowrap; }
        .mono { font-family: monospace; }
    </style>
</head>
<body>
    <a href="dashboard">&larr; Back to Dashboard</a>
    <h1>PnL Analytics</h1>

    <!-- Filters -->
    <div class="filters">
        <label>Chain:</label>
        <select id="f-chain"><option value="">All</option></select>
        <label>Window:</label>
        <select id="f-window">
            <option value="">All Time</option>
            <option value="1h">1h</option><option value="4h">4h</option>
            <option value="8h">8h</option><option value="24h" selected>24h</option>
            <option value="3d">3d</option><option value="1w">1w</option><option value="1m">1m</option>
        </select>
        <label>From:</label><input type="date" id="f-since">
        <label>To:</label><input type="date" id="f-until">
        <button class="btn" onclick="loadAll()">Apply</button>
    </div>

    <!-- Summary Cards -->
    <h2>Summary</h2>
    <div class="grid" id="summary-grid"></div>

    <!-- Hourly PnL -->
    <h2>Hourly PnL</h2>
    <div id="hourly-chart"></div>

    <!-- Per Pair -->
    <h2>Profit by Pair</h2>
    <table id="pair-table">
        <thead><tr><th>Pair</th><th>Chain</th><th>Trades</th><th>Wins</th><th>Reverts</th><th>Net Profit</th><th>Gas Cost</th><th>Avg Profit</th></tr></thead>
        <tbody></tbody>
    </table>

    <!-- Per Venue -->
    <h2>Profit by Venue Route</h2>
    <table id="venue-table">
        <thead><tr><th>Buy DEX</th><th>Sell DEX</th><th>Chain</th><th>Trades</th><th>Win Rate</th><th>Net Profit</th><th>Avg Profit</th></tr></thead>
        <tbody></tbody>
    </table>

    <!-- Expected vs Realized -->
    <h2>Expected vs Realized (Included Trades)</h2>
    <table id="evr-table">
        <thead><tr><th>Time</th><th>Pair</th><th>Buy</th><th>Sell</th><th>Expected</th><th>Realized</th><th>Capture</th><th>Gas</th><th>TX</th></tr></thead>
        <tbody></tbody>
    </table>

    <!-- Gas Efficiency -->
    <h2>Gas Efficiency by Chain</h2>
    <table id="gas-table">
        <thead><tr><th>Chain</th><th>Trades</th><th>Avg Gas Used</th><th>Avg Est Gas</th><th>Avg Gas Cost ETH</th></tr></thead>
        <tbody></tbody>
    </table>

    <!-- Rejection Analysis -->
    <h2>Rejection Reasons</h2>
    <table id="reject-table">
        <thead><tr><th>Reason</th><th>Chain</th><th>Count</th><th>Avg Expected Profit</th></tr></thead>
        <tbody></tbody>
    </table>

    <script>
    const API_BASE = window.location.pathname.split('/analytics')[0];

    function getFilters() {
        const chain = document.getElementById('f-chain').value;
        const window = document.getElementById('f-window').value;
        const since = document.getElementById('f-since').value;
        const until = document.getElementById('f-until').value;
        let qs = [];
        if (chain) qs.push('chain=' + chain);
        if (window) qs.push('window=' + window);
        if (since) qs.push('since=' + since + 'T00:00:00Z');
        if (until) qs.push('until=' + until + 'T23:59:59Z');
        return qs.length ? '?' + qs.join('&') : '';
    }

    async function loadAll() {
        const qs = getFilters();
        const data = await (await fetch(API_BASE + '/pnl/analytics' + qs)).json();
        renderSummary(data);
        renderHourly(data.hourly_pnl);
        renderPairs(data.per_pair);
        renderVenues(data.per_venue);
        renderEVR(data.expected_vs_realized);
        renderGas(data.gas_efficiency);
        renderRejects(data.rejection_reasons);
    }

    function pnlColor(v) { return v > 0 ? '#3fb950' : v < 0 ? '#f85149' : '#8b949e'; }
    function ethUsd(v) { return '$' + (v * 2300).toFixed(2); }
    function pct(a, b) { return b > 0 ? (a / b * 100).toFixed(1) + '%' : '-'; }

    function renderSummary(d) {
        const totalTrades = d.per_pair.reduce((s,r) => s + r.trades, 0);
        const totalWins = d.per_pair.reduce((s,r) => s + r.wins, 0);
        const totalReverts = d.per_pair.reduce((s,r) => s + r.reverts, 0);
        const totalProfit = d.per_pair.reduce((s,r) => s + r.net_profit, 0);
        const totalGas = d.per_pair.reduce((s,r) => s + r.gas_cost, 0);
        const winRate = totalTrades > 0 ? (totalWins / totalTrades * 100).toFixed(1) : '0';
        document.getElementById('summary-grid').innerHTML = `
            <div class="card"><div class="card-title">Total Trades</div>
                <div class="card-value">${totalTrades}</div>
                <div class="card-sub">${totalWins} wins / ${totalReverts} reverts</div></div>
            <div class="card"><div class="card-title">Win Rate</div>
                <div class="card-value ${Number(winRate) >= 50 ? 'status-ok' : 'status-bad'}">${winRate}%</div></div>
            <div class="card"><div class="card-title">Net Profit</div>
                <div class="card-value" style="color:${pnlColor(totalProfit)}">${totalProfit.toFixed(6)} ETH</div>
                <div class="card-sub">${ethUsd(totalProfit)}</div></div>
            <div class="card"><div class="card-title">Total Gas Cost</div>
                <div class="card-value">${totalGas.toFixed(6)} ETH</div>
                <div class="card-sub">${ethUsd(totalGas)}</div></div>
            <div class="card"><div class="card-title">Spread Capture</div>
                <div class="card-value">${d.spread_capture_rate_pct}%</div>
                <div class="card-sub">Expected: ${d.total_expected.toFixed(6)} | Realized: ${d.total_realized.toFixed(6)}</div></div>
            <div class="card"><div class="card-title">Profit / Trade</div>
                <div class="card-value">${totalTrades > 0 ? (totalProfit / totalTrades).toFixed(6) : '0'}</div>
                <div class="card-sub">${totalTrades > 0 ? ethUsd(totalProfit / totalTrades) : '$0'} avg</div></div>
        `;
    }

    function renderHourly(rows) {
        const container = document.getElementById('hourly-chart');
        if (!rows || rows.length === 0) { container.innerHTML = '<div class="bar-chart" style="justify-content:center;align-items:center;color:#484f58">No trade data</div>'; return; }
        const reversed = [...rows].reverse();
        const maxVal = Math.max(0.000001, ...reversed.map(r => Math.abs(r.net_profit)));
        container.innerHTML = '<div class="bar-chart">' + reversed.map(r => {
            const h = Math.max(2, Math.abs(r.net_profit) / maxVal * 100);
            const cls = r.net_profit >= 0 ? 'bar-pos' : 'bar-neg';
            const label = r.hour.slice(11, 13) + 'h';
            return `<div class="bar-group"><div class="${cls}" style="height:${h}px" title="${r.net_profit.toFixed(6)} ETH | ${r.trades} trades"></div><div class="bar-label">${label}</div></div>`;
        }).join('') + '</div>';
    }

    function renderPairs(rows) {
        document.querySelector('#pair-table tbody').innerHTML = rows.map(r => `<tr>
            <td><b>${r.pair}</b></td><td>${r.chain}</td><td>${r.trades}</td>
            <td style="color:#3fb950">${r.wins}</td><td style="color:#f85149">${r.reverts}</td>
            <td class="mono" style="color:${pnlColor(r.net_profit)};font-weight:bold">${r.net_profit.toFixed(6)}</td>
            <td class="mono">${r.gas_cost.toFixed(6)}</td>
            <td class="mono">${r.avg_profit.toFixed(6)}</td>
        </tr>`).join('') || '<tr><td colspan="8" style="color:#484f58">No trade data</td></tr>';
    }

    function renderVenues(rows) {
        document.querySelector('#venue-table tbody').innerHTML = rows.map(r => {
            const wr = r.trades > 0 ? (r.wins / r.trades * 100).toFixed(0) : '0';
            const wrColor = Number(wr) >= 50 ? '#3fb950' : '#f85149';
            return `<tr>
                <td>${r.buy_dex}</td><td>${r.sell_dex}</td><td>${r.chain}</td><td>${r.trades}</td>
                <td style="color:${wrColor};font-weight:bold">${wr}%</td>
                <td class="mono" style="color:${pnlColor(r.net_profit)}">${r.net_profit.toFixed(6)}</td>
                <td class="mono">${r.avg_profit.toFixed(6)}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="7" style="color:#484f58">No trade data</td></tr>';
    }

    function renderEVR(rows) {
        document.querySelector('#evr-table tbody').innerHTML = rows.map(r => {
            const capture = r.expected && r.expected !== 0 ? (r.realized / r.expected * 100).toFixed(1) + '%' : '-';
            const captureColor = r.realized >= r.expected ? '#3fb950' : '#f85149';
            const txShort = r.tx_hash ? r.tx_hash.slice(0, 10) + '...' : '-';
            const chain = r.chain || 'arbitrum';
            const explorer = chain === 'optimism' ? 'optimistic.etherscan.io' : chain === 'base' ? 'basescan.org' : chain === 'arbitrum' ? 'arbiscan.io' : 'etherscan.io';
            return `<tr>
                <td>${r.detected_at ? r.detected_at.slice(5,16) : '-'}</td>
                <td>${r.pair}</td><td>${r.buy_dex}</td><td>${r.sell_dex}</td>
                <td class="mono">${r.expected ? r.expected.toFixed(6) : '-'}</td>
                <td class="mono" style="color:${pnlColor(r.realized)}">${r.realized ? r.realized.toFixed(6) : '-'}</td>
                <td style="color:${captureColor}">${capture}</td>
                <td class="mono">${r.gas_cost ? r.gas_cost.toFixed(6) : '-'}</td>
                <td>${r.tx_hash ? '<a href="https://' + explorer + '/tx/' + r.tx_hash + '" target="_blank">' + txShort + '</a>' : '-'}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="9" style="color:#484f58">No included trades yet</td></tr>';
    }

    function renderGas(rows) {
        document.querySelector('#gas-table tbody').innerHTML = rows.map(r => `<tr>
            <td><b>${r.chain}</b></td><td>${r.trades}</td>
            <td class="mono">${Math.round(r.avg_gas_used).toLocaleString()}</td>
            <td class="mono">${r.avg_estimated_gas ? Number(r.avg_estimated_gas).toFixed(6) : '-'}</td>
            <td class="mono">${r.avg_gas_cost_eth ? r.avg_gas_cost_eth.toFixed(8) : '-'}</td>
        </tr>`).join('') || '<tr><td colspan="5" style="color:#484f58">No data</td></tr>';
    }

    function renderRejects(rows) {
        document.querySelector('#reject-table tbody').innerHTML = rows.map(r => `<tr>
            <td>${r.reason_code}</td><td>${r.chain}</td>
            <td>${r.cnt}</td>
            <td class="mono">${r.avg_expected_profit ? Number(r.avg_expected_profit).toFixed(6) : '0'}</td>
        </tr>`).join('') || '<tr><td colspan="4" style="color:#484f58">No rejections</td></tr>';
    }

    async function loadChains() {
        const chains = await (await fetch(API_BASE + '/dashboard/distinct-chains')).json();
        const sel = document.getElementById('f-chain');
        chains.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
    }

    async function init() {
        await loadChains();
        await loadAll();
    }
    init();
    </script>
</body>
</html>"""
