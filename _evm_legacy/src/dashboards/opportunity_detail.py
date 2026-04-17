"""Opportunity detail page — full lifecycle view for a single opportunity."""

OPPORTUNITY_DETAIL_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Opportunity Detail</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
               background: #191c1f; color: #f4f4f4; padding: 24px; letter-spacing: 0.16px; }
        h1 { color: #ffffff; margin-bottom: 8px; font-weight: 600; font-size: 24px; letter-spacing: -0.32px; }
        h2 { color: #8d969e; margin: 24px 0 10px; font-size: 13px; text-transform: uppercase;
             font-weight: 600; letter-spacing: 0.24px; }
        .back { color: #494fdf; text-decoration: none; margin-bottom: 16px; display: inline-block; font-weight: 500; }
        .section { background: #242729; border-radius: 20px;
                   padding: 20px; margin-bottom: 16px; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 8px 12px; color: #8d969e; font-size: 12px;
             text-transform: uppercase; width: 200px; }
        td { padding: 8px 12px; font-size: 14px; }
        .tag { display: inline-block; padding: 3px 12px; border-radius: 9999px; font-size: 12px; font-weight: 600; }
        .tag-approved { background: rgba(0,168,126,0.15); color: #00a87e; }
        .tag-rejected { background: rgba(226,59,74,0.15); color: #e23b4a; }
        .tag-detected { background: rgba(73,79,223,0.15); color: #494fdf; }
        .mono { font-family: 'Inter', monospace; color: #ffffff; }
        .empty { color: #505a63; font-style: italic; }
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
            const netColor = net > 0 ? '#00a87e' : '#e23b4a';

            html += `<h2>Cost Breakdown</h2><div class="section">
            <table>
                <tr><th>Buy Cost (input)</th><td class="mono">$${inp.toFixed(2)}</td></tr>
                <tr><th>Sell Proceeds (output)</th><td class="mono">$${out.toFixed(2)}</td></tr>
                <tr style="border-top:1px solid #2e3236">
                    <th>Gross Spread</th>
                    <td class="mono">$${(out - inp).toFixed(2)}</td></tr>
                <tr><th style="padding-left:30px">- DEX Fees</th>
                    <td class="mono" style="color:#e23b4a">-$${fee.toFixed(4)}</td></tr>
                <tr><th style="padding-left:30px">- Slippage</th>
                    <td class="mono" style="color:#e23b4a">-$${slip.toFixed(4)}</td></tr>
                <tr><th style="padding-left:30px">- Gas</th>
                    <td class="mono" style="color:#e23b4a">-${gas.toFixed(6)} ETH</td></tr>
                <tr style="border-top:2px solid #494fdf">
                    <th style="font-size:14px;color:#ffffff">Net Profit</th>
                    <td class="mono" style="font-size:16px;font-weight:bold;color:${netColor}">
                        ${net.toFixed(6)} ETH (~$${netUsd})
                    </td></tr>
            </table>
            <div style="margin-top:8px;color:#505a63;font-size:11px">Priced at ${toEST(p.created_at)}</div>
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

            riskHtml += `<tr style="border-top:1px solid #2e3236"><th colspan="2" style="color:#494fdf;padding-top:12px">Analysis</th></tr>`;

            if (details.net_profit !== undefined)
                riskHtml += `<tr><th>Net Profit</th><td class="mono">${num(details.net_profit, 6)} ETH</td></tr>`;
            if (details.gross_spread_pct !== undefined)
                riskHtml += `<tr><th>Gross Spread</th><td class="mono">${num(details.gross_spread_pct, 4)}%</td></tr>`;

            // Fee breakdown
            const hasFees = details.dex_fees || details.flash_loan_fee || details.slippage_cost || details.gas_cost;
            if (hasFees) {
                riskHtml += `<tr style="border-top:1px solid #2e3236"><th colspan="2" style="color:#494fdf;padding-top:12px">Fee Components</th></tr>`;
                if (details.dex_fees && details.dex_fees !== '0')
                    riskHtml += `<tr><th>DEX Fees</th><td class="mono">$${num(details.dex_fees, 4)}</td></tr>`;
                if (details.fee_included !== undefined)
                    riskHtml += `<tr><th>Fee Pre-Included</th><td class="mono">${details.fee_included ? '<span style="color:#00a87e">Yes</span> (on-chain quoter)' : 'No (calculated)'}</td></tr>`;
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
                riskHtml += `<tr style="border-top:1px solid #2e3236"><th colspan="2" style="color:#494fdf;padding-top:12px">Risk Signals</th></tr>`;
                if (details.liquidity_score !== undefined)
                    riskHtml += `<tr><th>Liquidity Score</th><td class="mono">${details.liquidity_score}</td></tr>`;
                if (details.warning_flags && details.warning_flags.length > 0)
                    riskHtml += `<tr><th>Warning Flags</th><td class="mono" style="color:#e23b4a">${details.warning_flags.join(', ')}</td></tr>`;
            }

            if (details.simulation)
                riskHtml += `<tr><th>Mode</th><td class="mono"><span class="tag tag-approved">SIMULATION</span> — would execute if live</td></tr>`;

            riskHtml += `<tr style="border-top:1px solid #2e3236"><th>Decided At</th><td class="mono">${toEST(r.created_at)}</td></tr>`;
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


