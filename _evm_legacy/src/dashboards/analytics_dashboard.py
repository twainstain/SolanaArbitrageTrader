"""Analytics dashboard — PnL breakdown, rejection analysis, near-miss analysis."""

ANALYTICS_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>PnL Analytics</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
               background: #191c1f; color: #f4f4f4; padding: 20px; }
        h1 { color: #494fdf; margin-bottom: 4px; }
        h2 { color: #8d969e; margin: 24px 0 10px; font-size: 14px; text-transform: uppercase; }
        a { color: #494fdf; text-decoration: none; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
        .card { background: #242729; border: 1px solid #2e3236; border-radius: 8px; padding: 16px; }
        .card-title { font-size: 12px; color: #8d969e; text-transform: uppercase; margin-bottom: 8px; }
        .card-value { font-size: 28px; font-weight: bold; color: #ffffff; }
        .card-sub { font-size: 12px; color: #8d969e; margin-top: 4px; }
        .status-ok { color: #00a87e; }
        .status-bad { color: #e23b4a; }
        .status-warn { color: #ec7e00; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 8px; border-bottom: 2px solid #2e3236; color: #8d969e;
             font-size: 12px; text-transform: uppercase; }
        td { padding: 8px; border-bottom: 1px solid #2a2d31; font-size: 13px; }
        tr:hover { background: #2a2d31; }
        .filters { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
        .filters label { color: #8d969e; font-size: 13px; }
        select, input[type=date] { background: #242729; color: #f4f4f4; border: 1px solid #2e3236;
                 border-radius: 6px; padding: 6px 12px; font-size: 13px; }
        .btn { padding: 6px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
               border: 1px solid #2e3236; background: #1f6feb; color: #fff; font-weight: bold; }
        .btn:hover { background: #388bfd; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { padding: 6px 16px; border-radius: 6px; background: #2a2d31; color: #8d969e;
               cursor: pointer; font-size: 13px; border: 1px solid #2e3236; text-decoration: none; }
        .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
        .bar-chart { display: flex; align-items: flex-end; gap: 3px; height: 140px;
                     background: #242729; border: 1px solid #2e3236; border-radius: 8px;
                     padding: 12px; margin-top: 8px; overflow-x: auto; }
        .bar-group { display: flex; flex-direction: column; align-items: center; min-width: 20px; }
        .bar-pos { background: #00a87e; border-radius: 2px 2px 0 0; min-width: 14px; }
        .bar-neg { background: #e23b4a; border-radius: 0 0 2px 2px; min-width: 14px; }
        .bar-label { font-size: 9px; color: #505a63; margin-top: 2px; white-space: nowrap; }
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

    <!-- Scan History -->
    <h2>Scan History — Filter Breakdown</h2>
    <table id="scan-filter-table">
        <thead><tr><th>Reason</th><th>Chain</th><th>Count</th><th>Avg Spread</th><th>Avg Net Profit</th><th>Best Net Profit</th></tr></thead>
        <tbody></tbody>
    </table>

    <h2>Spread Distribution by Pair</h2>
    <table id="scan-spread-table">
        <thead><tr><th>Chain</th><th>Pair</th><th>Samples</th><th>Avg Spread %</th><th>Max Spread %</th><th>Min Spread %</th></tr></thead>
        <tbody></tbody>
    </table>

    <h2>Near Misses (Almost Profitable)</h2>
    <table id="scan-nearmiss-table">
        <thead><tr><th>Time</th><th>Pair</th><th>Chain</th><th>Buy</th><th>Sell</th><th>Spread %</th><th>Net Profit</th><th>Gas</th></tr></thead>
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
        const [data, scanData] = await Promise.all([
            (await fetch(API_BASE + '/pnl/analytics' + qs)).json(),
            (await fetch(API_BASE + '/scan-history/summary' + qs)).json().catch(function() { return {}; }),
        ]);
        renderSummary(data);
        renderHourly(data.hourly_pnl);
        renderPairs(data.per_pair);
        renderVenues(data.per_venue);
        renderEVR(data.expected_vs_realized);
        renderGas(data.gas_efficiency);
        renderRejects(data.rejection_reasons);
        renderScanFilters(scanData.filter_breakdown || []);
        renderScanSpreads(scanData.spread_distribution || []);
        renderNearMisses(scanData.near_misses || []);
    }

    function renderScanFilters(rows) {
        document.querySelector('#scan-filter-table tbody').innerHTML = rows.map(function(r) {
            return '<tr><td>' + r.filter_reason + '</td><td>' + r.chain + '</td><td>' + r.cnt +
                '</td><td class="mono">' + (r.avg_spread ? Number(r.avg_spread).toFixed(4) : '0') + '%</td>' +
                '<td class="mono">' + (r.avg_net_profit ? Number(r.avg_net_profit).toFixed(6) : '0') + '</td>' +
                '<td class="mono" style="color:' + pnlColor(r.max_net_profit || 0) + '">' +
                (r.max_net_profit ? Number(r.max_net_profit).toFixed(6) : '0') + '</td></tr>';
        }).join('') || '<tr><td colspan="6" style="color:#505a63">No scan data yet</td></tr>';
    }

    function renderScanSpreads(rows) {
        document.querySelector('#scan-spread-table tbody').innerHTML = rows.map(function(r) {
            return '<tr><td>' + r.chain + '</td><td><b>' + r.pair + '</b></td><td>' + r.samples +
                '</td><td class="mono">' + Number(r.avg_spread).toFixed(4) + '%</td>' +
                '<td class="mono" style="color:#00a87e">' + Number(r.max_spread).toFixed(4) + '%</td>' +
                '<td class="mono">' + Number(r.min_spread).toFixed(4) + '%</td></tr>';
        }).join('') || '<tr><td colspan="6" style="color:#505a63">No scan data yet</td></tr>';
    }

    function renderNearMisses(rows) {
        document.querySelector('#scan-nearmiss-table tbody').innerHTML = rows.map(function(r) {
            var ts = r.scan_ts ? r.scan_ts.slice(5, 16) : '-';
            return '<tr><td>' + ts + '</td><td>' + r.pair + '</td><td>' + r.chain +
                '</td><td>' + r.buy_dex + '</td><td>' + r.sell_dex +
                '</td><td class="mono">' + Number(r.spread).toFixed(4) + '%</td>' +
                '<td class="mono" style="color:#ec7e00">' + Number(r.net_profit).toFixed(6) + '</td>' +
                '<td class="mono">' + Number(r.gas_cost).toFixed(6) + '</td></tr>';
        }).join('') || '<tr><td colspan="8" style="color:#505a63">No near misses yet</td></tr>';
    }

    function pnlColor(v) { return v > 0 ? '#00a87e' : v < 0 ? '#e23b4a' : '#8d969e'; }
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
        if (!rows || rows.length === 0) { container.innerHTML = '<div class="bar-chart" style="justify-content:center;align-items:center;color:#505a63">No trade data</div>'; return; }
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
            <td style="color:#00a87e">${r.wins}</td><td style="color:#e23b4a">${r.reverts}</td>
            <td class="mono" style="color:${pnlColor(r.net_profit)};font-weight:bold">${r.net_profit.toFixed(6)}</td>
            <td class="mono">${r.gas_cost.toFixed(6)}</td>
            <td class="mono">${r.avg_profit.toFixed(6)}</td>
        </tr>`).join('') || '<tr><td colspan="8" style="color:#505a63">No trade data</td></tr>';
    }

    function renderVenues(rows) {
        document.querySelector('#venue-table tbody').innerHTML = rows.map(r => {
            const wr = r.trades > 0 ? (r.wins / r.trades * 100).toFixed(0) : '0';
            const wrColor = Number(wr) >= 50 ? '#00a87e' : '#e23b4a';
            return `<tr>
                <td>${r.buy_dex}</td><td>${r.sell_dex}</td><td>${r.chain}</td><td>${r.trades}</td>
                <td style="color:${wrColor};font-weight:bold">${wr}%</td>
                <td class="mono" style="color:${pnlColor(r.net_profit)}">${r.net_profit.toFixed(6)}</td>
                <td class="mono">${r.avg_profit.toFixed(6)}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="7" style="color:#505a63">No trade data</td></tr>';
    }

    function renderEVR(rows) {
        document.querySelector('#evr-table tbody').innerHTML = rows.map(r => {
            const capture = r.expected && r.expected !== 0 ? (r.realized / r.expected * 100).toFixed(1) + '%' : '-';
            const captureColor = r.realized >= r.expected ? '#00a87e' : '#e23b4a';
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
        }).join('') || '<tr><td colspan="9" style="color:#505a63">No included trades yet</td></tr>';
    }

    function renderGas(rows) {
        document.querySelector('#gas-table tbody').innerHTML = rows.map(r => `<tr>
            <td><b>${r.chain}</b></td><td>${r.trades}</td>
            <td class="mono">${Math.round(r.avg_gas_used).toLocaleString()}</td>
            <td class="mono">${r.avg_estimated_gas ? Number(r.avg_estimated_gas).toFixed(6) : '-'}</td>
            <td class="mono">${r.avg_gas_cost_eth ? r.avg_gas_cost_eth.toFixed(8) : '-'}</td>
        </tr>`).join('') || '<tr><td colspan="5" style="color:#505a63">No data</td></tr>';
    }

    function renderRejects(rows) {
        document.querySelector('#reject-table tbody').innerHTML = rows.map(r => `<tr>
            <td>${r.reason_code}</td><td>${r.chain}</td>
            <td>${r.cnt}</td>
            <td class="mono">${r.avg_expected_profit ? Number(r.avg_expected_profit).toFixed(6) : '0'}</td>
        </tr>`).join('') || '<tr><td colspan="4" style="color:#505a63">No rejections</td></tr>';
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
