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
    </style>
</head>
<body>
    <h1>Arbitrage Trader Dashboard</h1>

    <!-- Status Cards -->
    <h2>System Status</h2>
    <div class="grid" id="status-grid"></div>

    <!-- Chain Filter + Time Window Tabs -->
    <h2>Performance</h2>
    <div class="filter-row">
        <label>Chain:</label>
        <select id="chain-filter" onchange="onChainFilter()">
            <option value="">All Chains</option>
        </select>
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

    <!-- Recent Opportunities (sorted by spread, winners first) -->
    <h2>Recent Opportunities</h2>
    <table id="opp-table">
        <thead><tr>
            <th>ID</th><th>Pair</th><th>Chain</th><th>Buy</th><th>Sell</th>
            <th onclick="sortOpps('spread')">Spread</th>
            <th onclick="sortOpps('status')">Status</th>
            <th onclick="sortOpps('time')">Time</th>
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
    let oppSortField = 'spread';
    let oppSortDesc = true;
    let chainSortField = 'total';
    let chainData = [];
    let oppData = [];

    async function fetchJSON(url) { const r = await fetch(API_BASE + url); return r.json(); }

    function statusClass(val, goodIf) {
        if (goodIf === 'true') return val ? 'status-ok' : 'status-bad';
        if (goodIf === 'false') return val ? 'status-bad' : 'status-ok';
        return '';
    }
    function tagClass(s) {
        if (['approved','included','simulated','dry_run'].includes(s)) return 'tag-approved';
        if (['rejected','reverted','simulation_failed'].includes(s)) return 'tag-rejected';
        if (['submitted'].includes(s)) return 'tag-submitted';
        return 'tag-detected';
    }
    function isWin(s) { return ['approved','included','simulated','dry_run','submitted'].includes(s); }

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
                <div class="card-title">Total PnL</div>
                <div class="card-value">${Number(pnl.total_profit || 0).toFixed(6)}</div>
                <div class="card-sub">${pnl.successful || 0} successful / ${pnl.reverted || 0} reverted</div>
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
        tabs.innerHTML = WINDOWS.map(w =>
            `<a class="tab ${w===currentWindow?'active':''}" onclick="setWindow('${w}')">${w}</a>`
        ).join('');
        const chainParam = selectedChain ? `?chain=${selectedChain}` : '';
        const data = await fetchJSON(`/dashboard/window/${currentWindow}${chainParam}`);
        const grid = document.getElementById('window-grid');
        const o = data.opportunities || {};
        const t = data.trades || {};
        grid.innerHTML = `
            <div class="card">
                <div class="card-title">Opportunities (${currentWindow})</div>
                <div class="card-value">${o.total || 0}</div>
            </div>
            <div class="card">
                <div class="card-title">Trades (${currentWindow})</div>
                <div class="card-value">${t.total_trades || 0}</div>
                <div class="card-sub">Profit: ${Number(t.total_profit || 0).toFixed(6)}</div>
            </div>
            <div class="card">
                <div class="card-title">Successful (${currentWindow})</div>
                <div class="card-value">${t.successful || 0}</div>
            </div>
            <div class="card">
                <div class="card-title">Reverted (${currentWindow})</div>
                <div class="card-value">${t.reverted || 0}</div>
            </div>
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
        oppData = await fetchJSON('/opportunities?limit=50');
        if (selectedChain) oppData = oppData.filter(o => o.chain === selectedChain);
        renderOpps();
    }

    function renderOpps() {
        let data = [...oppData];
        // Default: winners first (approved/included before rejected), then by spread desc
        if (oppSortField === 'spread') {
            data.sort((a,b) => {
                const aw = isWin(a.status) ? 1 : 0;
                const bw = isWin(b.status) ? 1 : 0;
                if (aw !== bw) return bw - aw;
                return Number(b.spread_bps) - Number(a.spread_bps);
            });
        } else if (oppSortField === 'status') {
            data.sort((a,b) => {
                const aw = isWin(a.status) ? 1 : 0;
                const bw = isWin(b.status) ? 1 : 0;
                return bw - aw;
            });
        } else if (oppSortField === 'time') {
            data.sort((a,b) => b.detected_at.localeCompare(a.detected_at));
        }

        const tbody = document.querySelector('#opp-table tbody');
        tbody.innerHTML = data.slice(0, 30).map(o => `
            <tr>
                <td><a href="${API_BASE}/opportunity/${o.opportunity_id}" style="color:#58a6ff">${o.opportunity_id.slice(4,16)}</a></td>
                <td>${o.pair}</td>
                <td>${o.chain}</td>
                <td>${o.buy_dex}</td>
                <td>${o.sell_dex}</td>
                <td>${Number(o.spread_bps).toFixed(2)}%</td>
                <td><span class="tag ${tagClass(o.status)}">${o.status}</span></td>
                <td>${o.detected_at.slice(11,19)}</td>
            </tr>
        `).join('');
    }

    function sortOpps(field) { oppSortField = field; renderOpps(); }

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

    function setWindow(w) { currentWindow = w; loadWindows(); }

    async function init() {
        await loadChainFilter();
        await Promise.all([loadStatus(), loadWindows(), loadChains(), loadOpportunities(), loadBarChart()]);
    }
    init();
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
            'Spread': num(o.spread_bps, 4) + '%',
            'Status': `<span class="tag tag-${['approved','included','dry_run'].includes(o.status)?'approved':o.status==='rejected'?'rejected':'detected'}">${o.status}</span>`,
            'Detected At': o.detected_at,
            'Updated At': o.updated_at,
        });

        // Pricing
        const p = data.pricing;
        if (p) {
            html += renderSection('Pricing', {
                'Input Amount': '$' + num(p.input_amount, 2),
                'Estimated Output': '$' + num(p.estimated_output, 2),
                'DEX Fee Cost': '$' + num(p.fee_cost, 4),
                'Slippage Cost': '$' + num(p.slippage_cost, 4),
                'Gas Estimate': num(p.gas_estimate, 6),
                'Expected Net Profit': num(p.expected_net_profit, 6),
                'Priced At': p.created_at,
            });
        } else {
            html += renderSection('Pricing', null);
        }

        // Risk Decision
        const r = data.risk_decision;
        if (r) {
            html += renderSection('Risk Decision', {
                'Approved': r.approved ? '<span class="tag tag-approved">YES</span>' : '<span class="tag tag-rejected">NO</span>',
                'Reason': r.reason_code,
                'Thresholds': r.threshold_snapshot,
                'Decided At': r.created_at,
            });
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
                'Simulated At': s.created_at,
            });
        } else {
            html += renderSection('Simulation', null);
        }

        document.getElementById('content').innerHTML = html;
    }
    load();
    </script>
</body>
</html>"""
