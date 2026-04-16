"""Main dashboard HTML — status cards, time windows, opportunities table, charts."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Arbitrage Trader Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
               background: #191c1f; color: #f4f4f4; padding: 24px; letter-spacing: 0.16px; }
        h1 { color: #ffffff; margin-bottom: 24px; font-weight: 600; font-size: 28px; letter-spacing: -0.4px; }
        h2 { color: #8d969e; margin: 24px 0 12px; font-size: 13px; text-transform: uppercase;
             font-weight: 600; letter-spacing: 0.24px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
        .card { background: #242729; border-radius: 20px; padding: 20px; }
        .card-title { font-size: 12px; color: #8d969e; text-transform: uppercase; margin-bottom: 8px;
                      font-weight: 600; letter-spacing: 0.24px; }
        .card-value { font-size: 28px; font-weight: 600; color: #ffffff; letter-spacing: -0.32px; }
        .card-sub { font-size: 12px; color: #8d969e; margin-top: 6px; }
        .status-ok { color: #00a87e; }
        .status-warn { color: #ec7e00; }
        .status-bad { color: #e23b4a; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 10px 8px; border-bottom: 2px solid #2e3236; color: #8d969e;
             font-size: 11px; text-transform: uppercase; cursor: pointer; font-weight: 600;
             letter-spacing: 0.24px; }
        th:hover { color: #494fdf; }
        td { padding: 10px 8px; border-bottom: 1px solid #2a2d31; font-size: 13px; }
        tr:hover { background: #2a2d31; }
        .tag { display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
        .tag-detected { background: rgba(73,79,223,0.15); color: #494fdf; }
        .tag-approved { background: rgba(0,168,126,0.15); color: #00a87e; }
        .tag-rejected { background: rgba(226,59,74,0.15); color: #e23b4a; }
        .tag-submitted { background: rgba(236,126,0,0.15); color: #ec7e00; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .tab { padding: 8px 20px; border-radius: 9999px; background: #242729; color: #8d969e;
               cursor: pointer; font-size: 13px; border: none; text-decoration: none; font-weight: 500; }
        .tab:hover { background: #2e3236; color: #f4f4f4; }
        .tab.active { background: #494fdf; color: #fff; }
        .filter-row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
        .filter-row label { color: #8d969e; font-size: 13px; font-weight: 500; }
        .btn { padding: 10px 24px; border-radius: 9999px; font-size: 13px; cursor: pointer;
               border: none; font-weight: 600; letter-spacing: 0.16px; }
        .btn-green { background: #00a87e; color: #fff; }
        .btn-green:hover { opacity: 0.85; }
        .btn-red { background: #e23b4a; color: #fff; }
        .btn-red:hover { opacity: 0.85; }
        .btn-gray { background: #2e3236; color: #8d969e; }
        .btn-gray:hover { opacity: 0.85; }
        .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
        select { background: #242729; color: #f4f4f4; border: 2px solid #2e3236; border-radius: 9999px;
                 padding: 8px 16px; font-size: 13px; font-family: 'Inter', sans-serif; }
        input[type="datetime-local"] { background: #242729; color: #f4f4f4; border: 2px solid #2e3236;
                 border-radius: 9999px; padding: 6px 14px; font-size: 12px; font-family: 'Inter', sans-serif; }
        .bar-chart { display: flex; align-items: flex-end; gap: 4px; height: 120px;
                     background: #242729; border-radius: 20px;
                     padding: 16px; margin-top: 8px; }
        .bar-group { display: flex; flex-direction: column; align-items: center; flex: 1; }
        .bar-stack { display: flex; flex-direction: column-reverse; width: 100%; max-width: 40px; }
        .bar-win { background: #00a87e; min-height: 2px; border-radius: 2px 2px 0 0; }
        .bar-loss { background: #e23b4a; min-height: 0; border-radius: 0 0 2px 2px; }
        .bar-label { font-size: 10px; color: #8d969e; margin-top: 4px; text-align: center; }
        .chart-legend { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; }
        .legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
        .sort-arrow { font-size: 10px; color: #505a63; }
        .sort-arrow.active { color: #494fdf; }
        th.sorted { color: #494fdf; }
        tr.exec-row { background: #2a2d31; cursor: pointer; }
        tr.exec-row:hover { background: #2e3236; }
        tr.exec-row td:first-child { border-left: 3px solid #ec7e00; padding-left: 5px; }
        tr.exec-detail { display: none; }
        tr.exec-detail.open { display: table-row; }
        tr.exec-detail td { background: #242729; padding: 12px 16px; border-left: 3px solid #2e3236; }
        .exec-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
        .exec-item { font-size: 12px; }
        .exec-label { color: #8d969e; text-transform: uppercase; font-size: 10px; font-weight: 600; letter-spacing: 0.24px; }
        .exec-value { color: #ffffff; font-family: 'Inter', monospace; }
        .notification-bar {
            display: none;
            position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
            background: #494fdf; color: #fff; text-align: center;
            padding: 10px 16px; font-size: 14px; font-weight: 600;
            cursor: pointer; letter-spacing: 0.16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        .notification-bar:hover { background: #3b40b8; }
        .notification-bar.visible { display: block; }
        @media (max-width: 720px) {
            body { padding: 12px; }
            .grid { grid-template-columns: 1fr; }
            .card-value { font-size: 22px; }
            h1 { font-size: 22px; }
        }
    </style>
</head>
<body>
    <div id="new-tx-banner" class="notification-bar" onclick="refreshAll()">
        New transactions detected — click to refresh
    </div>
    <h1>Arbitrage Trader Dashboard</h1>

    <!-- Scanner Controls -->
    <div class="controls" id="scanner-controls">
        <span style="color:#8d969e;font-size:13px">Scanner:</span>
        <span id="scanner-status" class="tag tag-detected">loading...</span>
        <button class="btn btn-green" onclick="controlScanner('start')">Start</button>
        <button class="btn btn-red" onclick="controlScanner('stop')">Stop</button>
        <span style="color:#2e3236">|</span>
        <span style="color:#8d969e;font-size:13px">Execution:</span>
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
    <h2>Performance <span style="font-size:11px;color:#8d969e;font-weight:normal;text-transform:none">(times in EST)</span></h2>
    <div class="filter-row">
        <label>Chain:</label>
        <select id="chain-filter" onchange="onChainFilter()">
            <option value="">All Chains</option>
        </select>
        <span style="color:#2e3236">|</span>
        <label>From:</label>
        <input type="datetime-local" id="range-start" style="background:#242729;color:#f4f4f4;border:2px solid #2e3236;border-radius:9999px;padding:6px 14px;font-size:12px;font-family:Inter,sans-serif">
        <label>To:</label>
        <input type="datetime-local" id="range-end" style="background:#242729;color:#f4f4f4;border:2px solid #2e3236;border-radius:9999px;padding:6px 14px;font-size:12px;font-family:Inter,sans-serif">
        <button class="btn btn-green" style="padding:4px 12px;font-size:12px" onclick="applyRange()">Apply</button>
        <button class="btn btn-gray" style="padding:4px 12px;font-size:12px" onclick="clearRange()">Clear</button>
    </div>
    <div class="tabs" id="window-tabs"></div>
    <div class="grid" id="window-grid"></div>

    <!-- Hourly Bar Chart -->
    <h2>Hourly Win/Loss (24h)</h2>
    <div class="chart-legend">
        <span><span class="legend-dot" style="background:#00a87e"></span>Approved/Included</span>
        <span><span class="legend-dot" style="background:#e23b4a"></span>Rejected/Reverted</span>
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

    <!-- Status Filter -->
    <div class="filter-row">
        <label>Status:</label>
        <select id="status-filter" onchange="onStatusFilter()">
            <option value="">All</option>
            <option value="simulation_approved">Simulation Approved</option>
            <option value="approved">Approved</option>
            <option value="submitted">Submitted</option>
            <option value="included">Included</option>
            <option value="reverted">Reverted</option>
            <option value="rejected">Rejected</option>
            <option value="dry_run">Dry Run</option>
            <option value="simulation_failed">Sim Failed</option>
        </select>
        <label>Pair:</label>
        <select id="pair-filter" onchange="onPairFilter()">
            <option value="">All Pairs</option>
        </select>
    </div>

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
    let currentWindow = localStorage.getItem('arb_window') || '5m';
    let selectedChain = '';
    let selectedStatus = '';
    let selectedPair = '';
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
        currentWindow = '5m';
        localStorage.setItem('arb_window', '5m');
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

        // Compute execution status from per-chain modes
        const execChains = exec.chains || {};
        const liveList = Object.entries(execChains).filter(function(e) { return e[1].mode === 'live'; }).map(function(e) { return e[0]; });
        const simCount = Object.keys(execChains).length - liveList.length;
        let execHtml;
        if (liveList.length > 0) {
            execHtml = '<div class="card-value status-ok">' + liveList.map(function(c) { return c.toUpperCase(); }).join(', ') + ' LIVE</div>'
                + '<div class="card-sub">' + simCount + ' chains simulated</div>';
        } else {
            execHtml = '<div class="card-value status-warn">ALL SIMULATED</div>';
        }

        const grid = document.getElementById('status-grid');
        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Execution</div>
                ` + execHtml + `
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
            rows += '<td style="color:#00a87e">' + approved + '</td>';
            rows += '<td style="color:#e23b4a">' + rejected + '</td>';
            rows += '</tr>';
        }
        tbody.innerHTML = rows;
    }

    function sortChains(field) { chainSortField = field; renderChains(); }

    function onStatusFilter() {
        selectedStatus = document.getElementById('status-filter').value;
        loadOpportunities();
    }
    function onPairFilter() {
        selectedPair = document.getElementById('pair-filter').value;
        loadOpportunities();
    }

    async function loadOpportunities() {
        let url = '/opportunities?limit=50';
        if (customStart) {
            url += '&start=' + encodeURIComponent(customStart);
            if (customEnd) url += '&end=' + encodeURIComponent(customEnd);
        } else {
            url += '&window=' + currentWindow;
        }
        if (selectedChain) url += '&chain=' + selectedChain;
        if (selectedStatus) url += '&status=' + selectedStatus;
        if (selectedPair) url += '&pair=' + selectedPair;
        oppData = await fetchJSON(url);
        // Populate pair filter from data
        const pairs = [...new Set(oppData.map(function(o) { return o.pair; }))].sort();
        const pairSel = document.getElementById('pair-filter');
        const curPair = pairSel.value;
        pairSel.innerHTML = '<option value="">All Pairs</option>' +
            pairs.map(function(p) { return '<option value="' + p + '"' + (p === curPair ? ' selected' : '') + '>' + p + '</option>'; }).join('');
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
            const profitColor = profitWeth > 0 ? '#00a87e' : '#e23b4a';
            const showExpected = isApproved(o.status);
            const hasExec = hasExecution(o);
            const rowClass = hasExec ? 'exec-row' : '';
            const rowClick = hasExec ? `onclick="toggleExecDetail('${o.opportunity_id}')"` : '';

            // Realized PnL column
            let realizedHtml = '<span style="color:#505a63">-</span>';
            if (o.actual_net_profit !== null && o.actual_net_profit !== undefined) {
                const rp = Number(o.actual_net_profit);
                const rpUsd = (rp * 2300).toFixed(2);
                const rpColor = rp > 0 ? '#00a87e' : rp < 0 ? '#e23b4a' : '#8d969e';
                realizedHtml = `<span style="color:${rpColor};font-weight:bold">$${rpUsd}</span>`;
            }

            rows += `<tr class="${rowClass}" ${rowClick}>
                <td><a href="${API_BASE}/opportunity/${o.opportunity_id}" style="color:#494fdf" onclick="event.stopPropagation()">${o.opportunity_id.slice(4,16)}</a></td>
                <td>${o.pair}</td>
                <td>${o.chain}</td>
                <td>${o.buy_dex}</td>
                <td>${o.sell_dex}</td>
                <td>${(Number(o.spread_bps)/100).toFixed(2)}%</td>
                <td>${showExpected ? `<span style="color:${profitColor};font-weight:bold">$${profitUsd}</span>` : '<span style="color:#505a63">-</span>'}</td>
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
                                <div class="exec-value">${txHash !== 'n/a' ? `<a href="https://arbiscan.io/tx/${txHash}" target="_blank" style="color:#494fdf">${txShort}</a>` : 'n/a'}</div></div>
                            <div class="exec-item"><div class="exec-label">Included</div>
                                <div class="exec-value" style="color:${o.exec_included ? '#00a87e' : '#e23b4a'}">${incl}</div></div>
                            <div class="exec-item"><div class="exec-label">Reverted</div>
                                <div class="exec-value" style="color:${o.exec_reverted ? '#e23b4a' : '#00a87e'}">${rev}</div></div>
                            <div class="exec-item"><div class="exec-label">Gas Used</div>
                                <div class="exec-value">${gasUsed.toLocaleString()}</div></div>
                            <div class="exec-item"><div class="exec-label">Gas Cost</div>
                                <div class="exec-value">${gasCost} ETH</div></div>
                            <div class="exec-item"><div class="exec-label">Realized Profit ${profCur}</div>
                                <div class="exec-value">${realProfit}</div></div>
                            <div class="exec-item"><div class="exec-label">Net PnL (base)</div>
                                <div class="exec-value" style="color:${Number(netProfit) >= 0 ? '#00a87e' : '#e23b4a'}">${netProfit} ETH</div></div>
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
            container.innerHTML = '<div class="bar-chart" style="justify-content:center;align-items:center;color:#505a63">No data for selected filter</div>';
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
                <div class="bar-label" style="color:#00a87e">${v.wins}</div>
                <div class="bar-label" style="color:#e23b4a">${v.losses}</div>
            </div>`;
        }).join('') + '</div>';
    }

    function setWindow(w) {
        customStart = ''; customEnd = '';
        document.getElementById('range-start').value = '';
        document.getElementById('range-end').value = '';
        currentWindow = w;
        localStorage.setItem('arb_window', w);
        loadWindows(); loadOpportunities(); loadBarChart();
    }

    // --- Scanner control functions ---

    async function loadScannerStatus() {
        const [scanData, execData] = await Promise.all([fetchJSON('/scanner'), fetchJSON('/execution')]);
        const el = document.getElementById('scanner-status');
        if (scanData.running) {
            el.textContent = 'RUNNING';
            el.className = 'tag tag-approved';
        } else {
            el.textContent = 'STOPPED';
            el.className = 'tag tag-rejected';
        }

        // Determine real execution state from per-chain modes
        const chains = execData.chains || {};
        const liveChains = Object.entries(chains).filter(([,v]) => v.mode === 'live').map(([k]) => k);
        const anyLive = liveChains.length > 0 || execData.execution_enabled;

        const execBtn = document.getElementById('exec-toggle');
        if (anyLive) {
            const label = liveChains.length > 0 ? liveChains.join(', ').toUpperCase() + ' LIVE' : 'ALL LIVE';
            execBtn.textContent = label + ' — Click to Pause All';
            execBtn.className = 'btn btn-red';
        } else {
            execBtn.textContent = 'ALL SIMULATED — Use chain toggles below';
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
            const routers = info.has_routers ? '<span style="color:#00a87e">Yes</span>' : '<span style="color:#e23b4a">No</span>';
            const aave = info.has_aave ? '<span style="color:#00a87e">Yes</span>' : '<span style="color:#e23b4a">No</span>';

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
                actions = '<span style="color:#505a63;font-size:11px">Not executable</span>';
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
        const chains = current.chains || {};
        const liveChains = Object.entries(chains).filter(([,v]) => v.mode === 'live').map(([k]) => k);
        if (liveChains.length > 0) {
            // Pause all live chains
            for (const ch of liveChains) {
                await fetch(API_BASE + '/execution', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({chain: ch, mode: 'simulated'}),
                });
            }
        } else {
            // No live chains — toggle global (backward compat)
            await fetch(API_BASE + '/execution', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: !current.execution_enabled}),
            });
        }
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
                <div class="card-value" style="font-size:16px"><a href="https://arbiscan.io/address/${addr}" target="_blank" style="color:#494fdf">${short}</a></div>
            </div>`;
            let totalEth = 0;
            for (const [chain, bal] of Object.entries(data.balances)) {
                if (bal === null) continue;
                totalEth += bal;
                const usd = (bal * 2300).toFixed(2);
                const color = bal > 0.001 ? '#00a87e' : '#e23b4a';
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

    // --- New transaction detection ---
    let knownOppIds = new Set();
    let initialLoadDone = false;

    function trackOppIds(opps) {
        const newIds = opps.map(o => o.opportunity_id);
        if (!initialLoadDone) {
            // First load — just record, don't notify
            knownOppIds = new Set(newIds);
            initialLoadDone = true;
            return;
        }
        let hasNew = false;
        for (const id of newIds) {
            if (!knownOppIds.has(id)) { hasNew = true; break; }
        }
        if (hasNew) {
            document.getElementById('new-tx-banner').classList.add('visible');
        }
    }

    async function checkForNewOpps() {
        try {
            let url = '/opportunities?limit=50';
            if (customStart) {
                url += '&start=' + encodeURIComponent(customStart);
                if (customEnd) url += '&end=' + encodeURIComponent(customEnd);
            } else {
                url += '&window=' + currentWindow;
            }
            if (selectedChain) url += '&chain=' + selectedChain;
            if (selectedStatus) url += '&status=' + selectedStatus;
            if (selectedPair) url += '&pair=' + selectedPair;
            const data = await fetchJSON(url);
            trackOppIds(data);
        } catch(e) { /* ignore polling errors */ }
    }

    function refreshAll() {
        // Hide notification banner and reload all data
        document.getElementById('new-tx-banner').classList.remove('visible');
        knownOppIds.clear();
        initialLoadDone = false;
        Promise.all([loadStatus(), loadWindows(), loadChains(), loadOpportunities(), loadBarChart(), loadScannerStatus(), loadChainExecStatus()]).then(() => {
            loadWalletBalance();
        });
    }

    // Patch loadOpportunities to track IDs on user-initiated loads
    const _origLoadOpps = loadOpportunities;
    loadOpportunities = async function() {
        await _origLoadOpps();
        // After a user-initiated reload, update known set and hide banner
        knownOppIds = new Set(oppData.map(o => o.opportunity_id));
        initialLoadDone = true;
        document.getElementById('new-tx-banner').classList.remove('visible');
    };

    async function init() {
        await loadChainFilter();
        await Promise.all([loadStatus(), loadWindows(), loadChains(), loadOpportunities(), loadBarChart(), loadScannerStatus(), loadChainExecStatus()]);
        // Load wallet balance async (non-blocking, may be slow due to RPC calls)
        loadWalletBalance();
    }
    init();

    // Background polling — preserves user's selected time window
    setInterval(loadScannerStatus, 10000);
    setInterval(loadChainExecStatus, 10000);
    setInterval(checkForNewOpps, 15000);
    // Refresh status cards and charts every 30s (replaces old meta-refresh)
    setInterval(() => {
        loadStatus();
        loadBarChart();
        loadChains();
    }, 30000);
    </script>
</body>
</html>"""


