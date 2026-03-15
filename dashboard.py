"""Dashboard HTML template — professional trading dashboard."""

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BTC Scalper Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        :root {
            --bg: #0b0e14; --bg2: #111827; --bg3: #1a2332; --bg4: #0d1b2a;
            --border: #1e2d3d; --border2: #2a3a4d;
            --text: #e0e6ed; --text2: #8899aa; --text3: #556677;
            --green: #00c853; --green2: #00e676; --green-bg: rgba(0,200,83,0.12);
            --red: #ff1744; --red2: #ff5252; --red-bg: rgba(255,23,68,0.12);
            --yellow: #f0b90b; --yellow2: #ffd54f; --yellow-bg: rgba(240,185,11,0.10);
            --blue: #2196f3; --blue-bg: rgba(33,150,243,0.10);
            --purple: #bb86fc;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; font-size: 13px; overflow-x: hidden; }

        /* Header */
        .header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--bg2); position: sticky; top: 0; z-index: 100; }
        .header-left { display: flex; align-items: center; gap: 10px; }
        .logo { font-size: 1.1em; font-weight: 700; color: var(--yellow); letter-spacing: 1px; }
        .mode-badge { font-size: 0.65em; padding: 2px 8px; border-radius: 3px; font-weight: 600; letter-spacing: 1px; }
        .mode-live { background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }
        .mode-testnet { background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }
        .mode-dry { background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue); }
        .header-right { display: flex; align-items: center; gap: 12px; }
        .live-indicator { display: flex; align-items: center; gap: 5px; font-size: 0.8em; color: var(--text2); }
        .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
        .live-dot.off { background: var(--red); }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .live-dot.on { animation: pulse 2s infinite; }
        .uptime { font-size: 0.75em; color: var(--text3); font-family: monospace; }

        /* Status bar */
        .status-bar { padding: 8px 16px; background: var(--bg3); text-align: center; font-size: 0.85em; color: var(--yellow); border-bottom: 1px solid var(--border); }

        /* Main layout */
        .main { padding: 12px; max-width: 1200px; margin: 0 auto; }

        /* Cards grid */
        .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
        .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
        .card-label { font-size: 0.7em; color: var(--text3); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 4px; }
        .card-value { font-size: 1.4em; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
        .card-sub { font-size: 0.7em; color: var(--text3); margin-top: 2px; }

        /* Two column layout */
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }

        /* Sections */
        .section { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px; }
        .section-title { font-size: 0.7em; color: var(--text3); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
        .section-title .dot { width: 6px; height: 6px; border-radius: 50%; }

        /* Position card */
        .position-card { background: var(--bg4); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px; }
        .pos-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .side-badge { padding: 3px 12px; border-radius: 4px; font-weight: 700; font-size: 0.85em; letter-spacing: 0.5px; }
        .side-long { background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }
        .side-short { background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }
        .pos-pnl { font-family: monospace; font-weight: 700; font-size: 1.1em; }
        .pos-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 0.85em; }
        .pos-item { display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .pos-item-label { color: var(--text3); }
        .pos-item-value { font-family: monospace; }

        /* Price bar visualization */
        .price-bar-container { margin: 10px 0 6px; }
        .price-bar { height: 6px; background: var(--bg3); border-radius: 3px; position: relative; overflow: visible; }
        .price-bar-fill { height: 100%; border-radius: 3px; }
        .price-marker { position: absolute; top: -4px; width: 2px; height: 14px; border-radius: 1px; }
        .price-bar-labels { display: flex; justify-content: space-between; font-size: 0.65em; color: var(--text3); margin-top: 2px; }

        /* Score section */
        .score-row { margin-bottom: 8px; }
        .score-header { display: flex; justify-content: space-between; align-items: center; font-size: 0.85em; margin-bottom: 3px; }
        .score-bar { height: 6px; background: var(--bg3); border-radius: 3px; position: relative; }
        .score-fill { height: 100%; border-radius: 3px; transition: width 0.4s ease; }
        .score-threshold { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--yellow); border-radius: 1px; }
        .breakdown-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; margin-top: 8px; font-size: 0.75em; }
        .breakdown-item { display: flex; justify-content: space-between; padding: 2px 6px; border-radius: 3px; }
        .breakdown-item.active { background: rgba(255,255,255,0.03); }

        /* Indicators */
        .ind-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
        .ind-item { padding: 8px; background: var(--bg3); border-radius: 6px; text-align: center; }
        .ind-name { font-size: 0.65em; color: var(--text3); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px; }
        .ind-val { font-family: monospace; font-weight: 600; font-size: 1em; }
        .ind-sub { font-size: 0.65em; color: var(--text3); margin-top: 1px; }

        /* Risk panel */
        .risk-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
        .risk-item { text-align: center; padding: 8px; background: var(--bg3); border-radius: 6px; }
        .risk-label { font-size: 0.65em; color: var(--text3); text-transform: uppercase; margin-bottom: 3px; }
        .risk-value { font-family: monospace; font-weight: 600; }
        .risk-bar { height: 4px; background: var(--bg); border-radius: 2px; margin-top: 4px; }
        .risk-bar-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }

        /* Trade history */
        .trade-table { width: 100%; border-collapse: collapse; font-size: 0.8em; }
        .trade-table th { text-align: left; padding: 6px 8px; color: var(--text3); font-weight: 500; text-transform: uppercase; font-size: 0.85em; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
        .trade-table td { padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.02); font-family: monospace; }
        .trade-table tr:hover { background: rgba(255,255,255,0.02); }
        .no-trades { text-align: center; padding: 20px; color: var(--text3); font-size: 0.85em; }

        /* Equity chart */
        .chart-container { position: relative; height: 120px; }
        .chart-canvas { width: 100%; height: 100%; }
        .chart-labels { position: absolute; top: 4px; right: 8px; font-size: 0.7em; color: var(--text3); font-family: monospace; }

        /* Colors */
        .green { color: var(--green); } .red { color: var(--red); } .yellow { color: var(--yellow); } .blue { color: var(--blue); } .purple { color: var(--purple); }

        /* Responsive */
        @media (max-width: 768px) {
            .cards { grid-template-columns: repeat(2, 1fr); }
            .two-col { grid-template-columns: 1fr; }
            .ind-grid { grid-template-columns: 1fr 1fr; }
            .risk-grid { grid-template-columns: repeat(2, 1fr); }
            .card-value { font-size: 1.15em; }
        }
        @media (max-width: 480px) {
            .cards { grid-template-columns: 1fr 1fr; gap: 6px; }
            .card { padding: 10px; }
            .card-value { font-size: 1em; }
        }
    </style>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="header-left">
            <span class="logo">BTC SCALPER</span>
            <span class="mode-badge" id="modeBadge">LIVE</span>
        </div>
        <div class="header-right">
            <span class="uptime" id="uptime">00:00:00</span>
            <div class="live-indicator">
                <span class="live-dot on" id="liveDot"></span>
                <span id="liveLabel">LIVE</span>
            </div>
        </div>
    </div>

    <!-- Status -->
    <div class="status-bar" id="statusBar">Conectando al bot...</div>

    <div class="main">
        <!-- Top Cards -->
        <div class="cards">
            <div class="card">
                <div class="card-label">Equity</div>
                <div class="card-value" id="equity">$0.00</div>
                <div class="card-sub" id="balanceSub">Free: $0.00</div>
            </div>
            <div class="card">
                <div class="card-label">BTC/USDT</div>
                <div class="card-value yellow" id="price">$0</div>
                <div class="card-sub" id="priceSub">-</div>
            </div>
            <div class="card">
                <div class="card-label">P&L Diario</div>
                <div class="card-value" id="dailyPnl">$0.00</div>
                <div class="card-sub" id="dailyPnlSub">Restante: $0.00</div>
            </div>
            <div class="card">
                <div class="card-label">P&L Total</div>
                <div class="card-value" id="totalPnl">$0.00</div>
                <div class="card-sub" id="totalPnlSub">0 trades</div>
            </div>
        </div>

        <!-- Position (dynamic) -->
        <div id="positionSection"></div>

        <!-- Two Column: Scoring + Equity Chart -->
        <div class="two-col">
            <!-- Scoring -->
            <div class="section">
                <div class="section-title"><span class="dot" style="background:var(--yellow)"></span> Scoring de Senales</div>
                <div class="score-row">
                    <div class="score-header">
                        <span class="green">LONG</span>
                        <span class="green" id="longScore" style="font-family:monospace;font-weight:700;">0.0</span>
                    </div>
                    <div class="score-bar">
                        <div class="score-fill" id="longBar" style="width:0%;background:var(--green);"></div>
                        <div class="score-threshold" id="longThreshold" style="left:25%;"></div>
                    </div>
                </div>
                <div class="score-row">
                    <div class="score-header">
                        <span class="red">SHORT</span>
                        <span class="red" id="shortScore" style="font-family:monospace;font-weight:700;">0.0</span>
                    </div>
                    <div class="score-bar">
                        <div class="score-fill" id="shortBar" style="width:0%;background:var(--red);"></div>
                        <div class="score-threshold" id="shortThreshold" style="left:25%;"></div>
                    </div>
                </div>
                <div class="breakdown-grid" id="scoreBreakdown"></div>
            </div>

            <!-- Equity Chart -->
            <div class="section">
                <div class="section-title"><span class="dot" style="background:var(--green)"></span> Equity</div>
                <div class="chart-container">
                    <canvas id="equityChart" class="chart-canvas"></canvas>
                    <div class="chart-labels" id="chartLabels">-</div>
                </div>
            </div>
        </div>

        <!-- Two Column: Indicators + Risk -->
        <div class="two-col">
            <!-- Indicators -->
            <div class="section">
                <div class="section-title"><span class="dot" style="background:var(--blue)"></span> Indicadores</div>
                <div class="ind-grid" id="indGrid">
                    <div class="ind-item"><div class="ind-name">EMA</div><div class="ind-val" id="indEma">-</div><div class="ind-sub" id="indEmaSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">RSI</div><div class="ind-val" id="indRsi">-</div><div class="ind-sub" id="indRsiSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">MACD</div><div class="ind-val" id="indMacd">-</div><div class="ind-sub" id="indMacdSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">ATR</div><div class="ind-val" id="indAtr">-</div><div class="ind-sub" id="indAtrSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">Volume</div><div class="ind-val" id="indVol">-</div><div class="ind-sub" id="indVolSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">Orderbook</div><div class="ind-val" id="indOb">-</div><div class="ind-sub" id="indObSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">BB</div><div class="ind-val" id="indBb">-</div><div class="ind-sub" id="indBbSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">VWAP</div><div class="ind-val" id="indVwap">-</div><div class="ind-sub" id="indVwapSub">-</div></div>
                    <div class="ind-item"><div class="ind-name">HTF Trend</div><div class="ind-val" id="indHtf">-</div><div class="ind-sub" id="indHtfSub">-</div></div>
                </div>
            </div>

            <!-- Risk Management -->
            <div class="section">
                <div class="section-title"><span class="dot" style="background:var(--red)"></span> Risk Management</div>
                <div class="risk-grid">
                    <div class="risk-item">
                        <div class="risk-label">Win Rate</div>
                        <div class="risk-value green" id="winRate">0%</div>
                        <div class="risk-bar"><div class="risk-bar-fill" id="wrBar" style="width:0%;background:var(--green);"></div></div>
                    </div>
                    <div class="risk-item">
                        <div class="risk-label">W / L</div>
                        <div class="risk-value" id="winLoss">0 / 0</div>
                    </div>
                    <div class="risk-item">
                        <div class="risk-label">Leverage</div>
                        <div class="risk-value yellow" id="leverage">15x</div>
                        <div class="risk-bar"><div class="risk-bar-fill" id="levBar" style="width:33%;background:var(--yellow);"></div></div>
                    </div>
                    <div class="risk-item">
                        <div class="risk-label">Consec. Losses</div>
                        <div class="risk-value" id="consecLosses">0 / 4</div>
                        <div class="risk-bar"><div class="risk-bar-fill" id="clBar" style="width:0%;background:var(--red);"></div></div>
                    </div>
                    <div class="risk-item">
                        <div class="risk-label">Daily Loss Left</div>
                        <div class="risk-value" id="dailyLossLeft">$0.00</div>
                        <div class="risk-bar"><div class="risk-bar-fill" id="dlBar" style="width:100%;background:var(--green);"></div></div>
                    </div>
                    <div class="risk-item">
                        <div class="risk-label">Cooldown</div>
                        <div class="risk-value" id="cooldown">-</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Trade History -->
        <div class="section">
            <div class="section-title"><span class="dot" style="background:var(--purple)"></span> Trade History</div>
            <div style="overflow-x:auto;">
                <table class="trade-table" id="tradeTable">
                    <thead>
                        <tr>
                            <th>Hora</th>
                            <th>Side</th>
                            <th>Entry</th>
                            <th>Exit</th>
                            <th>Lev</th>
                            <th>PnL</th>
                            <th>%</th>
                            <th>Razon</th>
                            <th>Dur</th>
                        </tr>
                    </thead>
                    <tbody id="tradeBody">
                        <tr><td colspan="9" class="no-trades">Sin trades aun...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
    const socket = io();
    let lastState = null;

    // ─── Connection ───
    socket.on('connect', () => {
        document.getElementById('liveDot').className = 'live-dot on';
        document.getElementById('liveLabel').textContent = 'LIVE';
    });
    socket.on('disconnect', () => {
        document.getElementById('liveDot').className = 'live-dot off';
        document.getElementById('liveLabel').textContent = 'OFF';
        document.getElementById('statusBar').textContent = 'Desconectado del bot...';
        document.getElementById('statusBar').style.background = 'rgba(255,23,68,0.15)';
    });

    // ─── Helpers ───
    function fmt$(v, dec=4) { return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(dec); }
    function fmtPrice(v) { return '$' + v.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}); }
    function fmtTime(ts) {
        let d = new Date(ts * 1000);
        return d.toLocaleTimeString('es', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
    }
    function fmtDuration(sec) {
        if (sec < 60) return Math.floor(sec) + 's';
        if (sec < 3600) return Math.floor(sec/60) + 'm ' + Math.floor(sec%60) + 's';
        return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm';
    }
    function fmtUptime(sec) {
        let h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = Math.floor(sec%60);
        return String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
    }
    function clr(v) { return v >= 0 ? 'green' : 'red'; }

    // ─── State Update ───
    socket.on('state_update', (s) => {
        lastState = s;

        // Status bar
        let sb = document.getElementById('statusBar');
        sb.textContent = s.status || 'Esperando senal...';
        sb.style.background = s.in_cooldown ? 'rgba(255,23,68,0.12)' : 'var(--bg3)';

        // Mode badge
        let badge = document.getElementById('modeBadge');
        if (s.dry_run) { badge.textContent = 'DRY-RUN'; badge.className = 'mode-badge mode-dry'; }
        else if (s.testnet) { badge.textContent = 'TESTNET'; badge.className = 'mode-badge mode-testnet'; }
        else { badge.textContent = 'LIVE'; badge.className = 'mode-badge mode-live'; }

        // Uptime
        document.getElementById('uptime').textContent = fmtUptime(s.uptime || 0);

        // Cards
        let eqEl = document.getElementById('equity');
        eqEl.textContent = '$' + (s.equity || 0).toFixed(4);
        eqEl.className = 'card-value ' + clr((s.equity || 0) - 10);
        document.getElementById('balanceSub').textContent = 'Free: $' + (s.balance || 0).toFixed(4);

        document.getElementById('price').textContent = fmtPrice(s.price || 0);

        let dp = s.daily_pnl || 0;
        let dpEl = document.getElementById('dailyPnl');
        dpEl.textContent = fmt$(dp);
        dpEl.className = 'card-value ' + clr(dp);
        document.getElementById('dailyPnlSub').textContent = 'Restante: $' + (s.daily_loss_remaining || 0).toFixed(2);

        let tp = s.total_pnl || 0;
        let tpEl = document.getElementById('totalPnl');
        tpEl.textContent = fmt$(tp);
        tpEl.className = 'card-value ' + clr(tp);
        document.getElementById('totalPnlSub').textContent = (s.total_trades || 0) + ' trades';

        // ─── Position ───
        let posSection = document.getElementById('positionSection');
        if (s.position) {
            let p = s.position;
            let pnl = p.pnl_unrealized || 0;
            let margin = p.margin || 0;
            let pnlPct = margin > 0 ? (pnl/margin*100) : 0;
            let isLong = p.side === 'LONG';
            let sideClass = isLong ? 'side-long' : 'side-short';

            // Price bar: SL to TP range with current price marker
            let sl = p.stop_loss || 0, tp2 = p.take_profit || 0, entry = p.entry_price || 0;
            let cur = s.price || entry;
            let range = tp2 - sl;
            let curPct = range > 0 ? Math.max(0, Math.min(100, ((cur - sl) / range) * 100)) : 50;
            let entryPct = range > 0 ? Math.max(0, Math.min(100, ((entry - sl) / range) * 100)) : 50;
            let barColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
            let fillWidth = isLong ? curPct : (100 - curPct);

            let trailHtml = '';
            if (p.trailing_active && p.trailing_price) {
                let trailPct = range > 0 ? Math.max(0, Math.min(100, ((p.trailing_price - sl) / range) * 100)) : 50;
                trailHtml = '<div class="price-marker" style="left:' + trailPct + '%;background:var(--purple);"></div>';
            }

            posSection.innerHTML = '<div class="position-card">' +
                '<div class="pos-header">' +
                    '<div style="display:flex;align-items:center;gap:8px;">' +
                        '<span class="side-badge ' + sideClass + '">' + p.side + ' ' + (p.leverage || s.leverage) + 'x</span>' +
                        (p.trailing_active ? '<span style="font-size:0.7em;color:var(--purple);">TRAIL ON</span>' : '') +
                    '</div>' +
                    '<span class="pos-pnl ' + clr(pnl) + '">' + fmt$(pnl) + ' (' + pnlPct.toFixed(2) + '%)</span>' +
                '</div>' +
                '<div class="price-bar-container">' +
                    '<div class="price-bar">' +
                        '<div class="price-bar-fill" style="width:' + fillWidth + '%;background:' + barColor + ';opacity:0.3;"></div>' +
                        '<div class="price-marker" style="left:' + entryPct + '%;background:var(--yellow);width:2px;"></div>' +
                        '<div class="price-marker" style="left:' + curPct + '%;background:' + barColor + ';width:3px;"></div>' +
                        trailHtml +
                    '</div>' +
                    '<div class="price-bar-labels">' +
                        '<span style="color:var(--red);">SL ' + fmtPrice(sl) + '</span>' +
                        '<span>Entry ' + fmtPrice(entry) + '</span>' +
                        '<span style="color:var(--green);">TP ' + fmtPrice(tp2) + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="pos-grid">' +
                    '<div class="pos-item"><span class="pos-item-label">Precio Actual</span><span class="pos-item-value ' + clr(pnl) + '">' + fmtPrice(cur) + '</span></div>' +
                    '<div class="pos-item"><span class="pos-item-label">Cantidad</span><span class="pos-item-value">' + (p.quantity||0).toFixed(6) + ' BTC</span></div>' +
                    '<div class="pos-item"><span class="pos-item-label">Margen</span><span class="pos-item-value">$' + margin.toFixed(2) + '</span></div>' +
                    '<div class="pos-item"><span class="pos-item-label">Duracion</span><span class="pos-item-value">' + fmtDuration(p.duration||0) + '</span></div>' +
                '</div>' +
            '</div>';
        } else {
            posSection.innerHTML = '<div style="text-align:center;padding:12px;color:var(--text3);font-size:0.85em;background:var(--bg2);border:1px dashed var(--border);border-radius:8px;margin-bottom:12px;">Sin posicion abierta &mdash; Analizando mercado...</div>';
        }

        // ─── Scores ───
        let ls = s.long_score || 0, ss = s.short_score || 0;
        let thresh = s.score_threshold || 3.0;
        let maxS = 12;
        document.getElementById('longScore').textContent = ls.toFixed(1);
        document.getElementById('shortScore').textContent = ss.toFixed(1);
        document.getElementById('longBar').style.width = Math.min(ls/maxS*100, 100) + '%';
        document.getElementById('shortBar').style.width = Math.min(ss/maxS*100, 100) + '%';
        document.getElementById('longThreshold').style.left = (thresh/maxS*100) + '%';
        document.getElementById('shortThreshold').style.left = (thresh/maxS*100) + '%';

        // Score breakdown
        let bd = s.score_breakdown || {};
        let bdHtml = '';
        let bdKeys = ['ema', 'rsi', 'macd', 'volume', 'bollinger', 'vwap', 'orderbook', 'htf', 'rsi_div'];
        let bdLabels = ['EMA', 'RSI', 'MACD', 'Volume', 'BB', 'VWAP', 'OB', 'HTF', 'RSI Div'];
        for (let i = 0; i < bdKeys.length; i++) {
            let lv = bd['long_' + bdKeys[i]] || 0;
            let sv = bd['short_' + bdKeys[i]] || 0;
            let active = lv !== 0 || sv !== 0;
            bdHtml += '<div class="breakdown-item' + (active ? ' active' : '') + '"><span style="color:var(--text3)">' + bdLabels[i] + '</span>' +
                '<span><span class="green">' + (lv > 0 ? '+' : '') + lv.toFixed(1) + '</span> / <span class="red">' + (sv > 0 ? '+' : '') + sv.toFixed(1) + '</span></span></div>';
        }
        document.getElementById('scoreBreakdown').innerHTML = bdHtml;

        // ─── Indicators ───
        let ind = s.indicators || {};
        let emaF = ind.ema_fast || 0, emaS = ind.ema_slow || 0;
        let emaBullish = emaF > emaS;
        document.getElementById('indEma').textContent = emaBullish ? 'BULL' : 'BEAR';
        document.getElementById('indEma').className = 'ind-val ' + (emaBullish ? 'green' : 'red');
        document.getElementById('indEmaSub').textContent = emaF.toFixed(0) + ' / ' + emaS.toFixed(0);

        let rsi = ind.rsi || 50;
        document.getElementById('indRsi').textContent = rsi.toFixed(0);
        document.getElementById('indRsi').className = 'ind-val ' + (rsi < 30 ? 'green' : rsi > 70 ? 'red' : 'yellow');
        document.getElementById('indRsiSub').textContent = rsi < 30 ? 'Oversold' : rsi > 70 ? 'Overbought' : 'Neutral';

        let macdH = ind.macd_histogram || 0;
        document.getElementById('indMacd').textContent = (macdH >= 0 ? '+' : '') + macdH.toFixed(1);
        document.getElementById('indMacd').className = 'ind-val ' + clr(macdH);
        document.getElementById('indMacdSub').textContent = 'MACD: ' + (ind.macd || 0).toFixed(1);

        let atrPct = (ind.atr_pct || 0) * 100;
        document.getElementById('indAtr').textContent = atrPct.toFixed(2) + '%';
        document.getElementById('indAtr').className = 'ind-val ' + (atrPct > 0.3 ? 'yellow' : '');
        document.getElementById('indAtrSub').textContent = '$' + (ind.atr || 0).toFixed(1);

        let volR = ind.volume_ratio || 0;
        document.getElementById('indVol').textContent = volR.toFixed(1) + 'x';
        document.getElementById('indVol').className = 'ind-val ' + (volR > 1.5 ? 'green' : volR < 0.5 ? 'red' : '');
        let vdelta = ind.volume_delta || 0;
        document.getElementById('indVolSub').textContent = 'Delta: ' + (vdelta >= 0 ? '+' : '') + (vdelta * 100).toFixed(0) + '%';

        let imb = (ind.imbalance || 0) * 100;
        document.getElementById('indOb').textContent = (imb >= 0 ? '+' : '') + imb.toFixed(0) + '%';
        document.getElementById('indOb').className = 'ind-val ' + (imb > 15 ? 'green' : imb < -15 ? 'red' : '');
        document.getElementById('indObSub').textContent = imb > 0 ? 'Bid heavy' : imb < 0 ? 'Ask heavy' : 'Balanced';

        let bbPos = (ind.bb_position || 0.5) * 100;
        document.getElementById('indBb').textContent = bbPos.toFixed(0) + '%';
        document.getElementById('indBb').className = 'ind-val ' + (bbPos < 20 ? 'green' : bbPos > 80 ? 'red' : '');
        let bbw = (ind.bb_width || 0) * 100;
        document.getElementById('indBbSub').textContent = 'Width: ' + bbw.toFixed(2) + '%' + (bbw < 0.2 ? ' SQUEEZE' : '');

        let vwap = ind.vwap || 0;
        let aboveVwap = (s.price || 0) > vwap;
        document.getElementById('indVwap').textContent = aboveVwap ? 'Above' : 'Below';
        document.getElementById('indVwap').className = 'ind-val ' + (aboveVwap ? 'green' : 'red');
        document.getElementById('indVwapSub').textContent = fmtPrice(vwap);

        let htfF = ind.htf_ema_fast || 0, htfS = ind.htf_ema_slow || 0;
        let htfBull = htfF > htfS;
        document.getElementById('indHtf').textContent = htfBull ? 'BULL' : 'BEAR';
        document.getElementById('indHtf').className = 'ind-val ' + (htfBull ? 'green' : 'red');
        document.getElementById('indHtfSub').textContent = 'EMA25/65';

        // ─── Risk ───
        let wr = s.win_rate || 0;
        document.getElementById('winRate').textContent = wr.toFixed(1) + '%';
        document.getElementById('wrBar').style.width = wr + '%';

        document.getElementById('winLoss').textContent = (s.winning_trades || 0) + ' / ' + (s.losing_trades || 0);

        let lev = s.position ? (s.position.leverage || s.leverage) : s.leverage;
        let maxLev = s.max_leverage || 45;
        document.getElementById('leverage').textContent = lev + 'x';
        document.getElementById('levBar').style.width = (lev / maxLev * 100) + '%';
        document.getElementById('levBar').style.background = lev > 30 ? 'var(--red)' : lev > 20 ? 'var(--yellow)' : 'var(--green)';

        let cl = s.consecutive_losses || 0;
        document.getElementById('consecLosses').textContent = cl + ' / 4';
        document.getElementById('clBar').style.width = (cl / 4 * 100) + '%';

        let dlr = s.daily_loss_remaining || 0;
        let mdl = s.max_daily_loss || 3;
        document.getElementById('dailyLossLeft').textContent = '$' + dlr.toFixed(2);
        document.getElementById('dlBar').style.width = (mdl > 0 ? dlr / mdl * 100 : 100) + '%';
        document.getElementById('dlBar').style.background = dlr < mdl * 0.3 ? 'var(--red)' : dlr < mdl * 0.6 ? 'var(--yellow)' : 'var(--green)';

        let cd = s.cooldown_remaining || 0;
        document.getElementById('cooldown').textContent = s.in_cooldown ? fmtDuration(cd) : 'OFF';
        document.getElementById('cooldown').className = 'risk-value ' + (s.in_cooldown ? 'red' : 'green');

        // ─── Trade History ───
        let trades = s.trade_history || [];
        let tbody = document.getElementById('tradeBody');
        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="no-trades">Sin trades aun...</td></tr>';
        } else {
            let rows = '';
            for (let i = trades.length - 1; i >= 0; i--) {
                let t = trades[i];
                let pnlC = t.pnl >= 0 ? 'green' : 'red';
                let sideC = t.side === 'LONG' ? 'green' : 'red';
                let reasonMap = {tp:'TP', sl:'SL', trailing:'TRAIL', shutdown:'SHUT', daily_limit:'DLMT', manual:'MAN'};
                rows += '<tr>' +
                    '<td>' + fmtTime(t.time) + '</td>' +
                    '<td class="' + sideC + '">' + t.side + '</td>' +
                    '<td>' + fmtPrice(t.entry) + '</td>' +
                    '<td>' + fmtPrice(t.exit) + '</td>' +
                    '<td class="yellow">' + t.lev + 'x</td>' +
                    '<td class="' + pnlC + '">' + fmt$(t.pnl) + '</td>' +
                    '<td class="' + pnlC + '">' + (t.pnl_pct >= 0 ? '+' : '') + t.pnl_pct.toFixed(1) + '%</td>' +
                    '<td>' + (reasonMap[t.reason] || t.reason) + '</td>' +
                    '<td>' + fmtDuration(t.duration) + '</td>' +
                '</tr>';
            }
            tbody.innerHTML = rows;
        }

        // ─── Equity Chart ───
        drawEquityChart(s.equity_history || []);
    });

    // ─── Equity Chart Drawing ───
    function drawEquityChart(data) {
        let canvas = document.getElementById('equityChart');
        let ctx = canvas.getContext('2d');
        let dpr = window.devicePixelRatio || 1;
        let rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);
        let W = rect.width, H = rect.height;

        ctx.clearRect(0, 0, W, H);

        if (!data || data.length < 2) {
            ctx.fillStyle = '#556677';
            ctx.font = '12px Inter';
            ctx.textAlign = 'center';
            ctx.fillText('Esperando datos...', W/2, H/2);
            return;
        }

        let vals = data.map(d => d.eq);
        let minV = Math.min(...vals), maxV = Math.max(...vals);
        let range = maxV - minV || 1;
        let pad = range * 0.1;
        minV -= pad; maxV += pad; range = maxV - minV;

        // Grid lines
        ctx.strokeStyle = 'rgba(255,255,255,0.03)';
        ctx.lineWidth = 1;
        for (let i = 0; i < 4; i++) {
            let y = H * (i / 3);
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        }

        // Starting equity line
        let startY = H - ((vals[0] - minV) / range) * H;
        ctx.strokeStyle = 'rgba(240,185,11,0.2)';
        ctx.setLineDash([4,4]);
        ctx.beginPath(); ctx.moveTo(0, startY); ctx.lineTo(W, startY); ctx.stroke();
        ctx.setLineDash([]);

        // Equity line
        let lastVal = vals[vals.length - 1];
        let lineColor = lastVal >= vals[0] ? '#00c853' : '#ff1744';
        ctx.strokeStyle = lineColor;
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.beginPath();
        for (let i = 0; i < vals.length; i++) {
            let x = (i / (vals.length - 1)) * W;
            let y = H - ((vals[i] - minV) / range) * H;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Gradient fill
        let grad = ctx.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, lineColor + '20');
        grad.addColorStop(1, lineColor + '02');
        ctx.lineTo(W, H);
        ctx.lineTo(0, H);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        // Labels
        document.getElementById('chartLabels').innerHTML = '$' + lastVal.toFixed(4);
    }

    // ─── Auto resize chart ───
    window.addEventListener('resize', () => {
        if (lastState) drawEquityChart(lastState.equity_history || []);
    });
    </script>
</body>
</html>
"""
