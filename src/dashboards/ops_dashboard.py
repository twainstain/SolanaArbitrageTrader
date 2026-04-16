"""Operations & diagnostics dashboard — infra, RPC health, DEX health, risk policy."""

OPS_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Ops &amp; Diagnostics</title>
    <meta http-equiv="refresh" content="30">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
               background: #191c1f; color: #f4f4f4; padding: 24px; letter-spacing: 0.16px; }
        h1 { color: #ffffff; margin-bottom: 4px; font-weight: 600; font-size: 28px; letter-spacing: -0.4px; }
        h2 { color: #8d969e; margin: 24px 0 10px; font-size: 14px; text-transform: uppercase; }
        a { color: #494fdf; text-decoration: none; }
        a:hover { text-decoration: underline; }
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
             font-size: 11px; text-transform: uppercase; cursor: pointer; font-weight: 600; letter-spacing: 0.24px; }
        th:hover { color: #494fdf; }
        td { padding: 10px 8px; border-bottom: 1px solid #2a2d31; font-size: 13px; }
        tr:hover { background: #2a2d31; }
        .tag { display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
        .tag-ok { background: rgba(0,168,126,0.15); color: #00a87e; }
        .tag-warn { background: rgba(236,126,0,0.15); color: #ec7e00; }
        .tag-bad { background: rgba(226,59,74,0.15); color: #e23b4a; }
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
                const barColor = rate >= 0.8 ? '#00a87e' : rate >= 0.3 ? '#ec7e00' : '#e23b4a';
                return `<div class="card">
                    <div class="card-title">${chain}</div>
                    <div class="card-value"><span class="tag ${cls}">${(rate*100).toFixed(0)}%</span></div>
                    <div style="margin:8px 0;background:#2a2d31;border-radius:4px;height:14px">
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
                    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;color:#8d969e;font-size:11px">${r.last_error || '-'}</td>
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
                <div class="card-value">${Number(r.min_net_profit_default).toFixed(4)} ETH</div>
                <div class="card-sub">~$${(Number(r.min_net_profit_default) * 2300).toFixed(2)}</div>
            </div>
            <div class="card">
                <div class="card-title">Min Spread</div>
                <div class="card-value">${r.min_spread_pct_default}%</div>
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

