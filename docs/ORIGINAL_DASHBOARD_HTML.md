# Original Dashboard HTML — Before Carbon Redesign

## CSS Color Palette (`:root` variables)

```css
:root {
  --bg-0: #06080d;
  --bg-1: #0c1017;
  --bg-2: #111820;
  --bg-3: #19212d;
  --border: #1e2a3a;
  --border-accent: #2a3a52;
  --text-0: #e8edf5;
  --text-1: #a4b1c7;
  --text-2: #6b7a94;
  --text-3: #3f4f66;
  --accent: #3b82f6;
  --accent-dim: #1e3a5f;
  --green: #10b981;
  --green-dim: #064e3b;
  --red: #ef4444;
  --red-dim: #4c1414;
  --yellow: #f59e0b;
  --yellow-dim: #4a3508;
  --cyan: #06b6d4;
  --purple: #8b5cf6;
  --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif;
}
```

## Top Bar CSS

```css
/* --- TOP BAR --- */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 20px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  z-index: 100;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.logo {
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 2px;
}
.logo span { color: var(--text-2); font-weight: 400; }
.mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 1px;
}
.mode-badge.live { background: var(--green-dim); color: var(--green); border: 1px solid var(--green); }
.mode-badge.disconnected { background: var(--red-dim); color: var(--red); border: 1px solid var(--red); }
.mode-badge.halted { background: var(--red-dim); color: var(--red); border: 1px solid var(--red); }
.mode-badge.paused { background: var(--yellow-dim); color: var(--yellow); border: 1px solid var(--yellow); }
.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  display: inline-block;
}
.status-dot.ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
.status-dot.warn { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
.status-dot.err { background: var(--red); box-shadow: 0 0 6px var(--red); animation: pulse 1.5s infinite; }
@keyframes pulse {
  0%,100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}
.topbar-right .tick { color: var(--text-1); }
```

## KPI Strip CSS

```css
/* --- KPI STRIP --- */
.kpi-strip {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 1px;
  background: var(--border);
}
.kpi {
  background: var(--bg-1);
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kpi-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-2);
}
.kpi-value {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 700;
  color: var(--text-0);
  line-height: 1.2;
}
.kpi-sub {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}
.kpi-value.positive { color: var(--green); }
.kpi-value.negative { color: var(--red); }
.kpi-value.warn { color: var(--yellow); }
.kpi-value.accent { color: var(--accent); }
```

## Banners CSS

```css
/* --- BANNERS --- */
.halt-banner {
  display: none;
  background: var(--red-dim);
  border-bottom: 1px solid var(--red);
  padding: 10px 16px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--red);
  letter-spacing: 1px;
  flex-shrink: 0;
}
.halt-banner.visible { display: block; }
.disconnected-banner {
  display: none;
  background: var(--red-dim);
  border-bottom: 1px solid var(--red);
  padding: 8px 16px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--red);
  flex-shrink: 0;
}
.disconnected-banner.visible { display: block; }
```

## Main Grid CSS

```css
/* --- MAIN GRID --- */
.dashboard {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  grid-template-rows: auto 1fr 1fr auto;
  gap: 1px;
  padding: 1px;
  background: var(--border);
  flex: 1;
  overflow: auto;
}
```

## Responsive CSS

```css
@media (max-width: 1200px) {
  .kpi-strip { grid-template-columns: repeat(4, 1fr); }
  .dashboard { grid-template-columns: 1fr 1fr; }
  .positions-panel { grid-column: 1 / -1; }
  .system-panel { grid-column: 1 / -1; }
  .pairs-panel { grid-column: 1 / -1; }
  .risk-panel { grid-column: 1 / -1; }
  .alerts-panel { grid-column: 1 / -1; }
}
@media (max-width: 768px) {
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .topbar { flex-direction: column; gap: 8px; }
}
```

---

## BODY HTML

### Top Bar (Header)

```html
<!-- TOP BAR -->
<div class="topbar">
  <div class="topbar-left">
    <div class="logo">MULTI_ASSETS <span>| SPOT MONITOR</span></div>
    <div class="mode-badge live" id="mode-badge">
      <span class="status-dot ok" id="status-dot"></span>
      <span id="mode-text">LIVE</span>
    </div>
  </div>
  <div class="topbar-right">
    <span>CYCLE <span class="tick" id="loop-counter">--</span></span>
    <span>|</span>
    <span id="bot-status">--</span>
    <span>|</span>
    <span id="clock">--:--:--</span>
  </div>
</div>
```

### Banners

```html
<!-- BANNERS (outside grid for clean layout) -->
<div class="disconnected-banner" id="disconnect-banner">
  BOT DISCONNECTED -- No heartbeat detected. Check PM2 process or start the bot.
</div>
<div class="halt-banner" id="halt-banner">
  EMERGENCY HALT ACTIVE
</div>
```

### KPI Strip (Balance, PnL, Positions etc.)

```html
<!-- KPI STRIP -->
<div class="kpi-strip">
  <div class="kpi">
    <div class="kpi-label">USDC Balance</div>
    <div class="kpi-value accent" id="kpi-balance">--</div>
    <div class="kpi-sub" id="kpi-balance-sub">&nbsp;</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Daily P&amp;L</div>
    <div class="kpi-value" id="kpi-daily-pnl">--</div>
    <div class="kpi-sub" id="kpi-daily-pct">&nbsp;</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Cumulative P&amp;L</div>
    <div class="kpi-value" id="kpi-cumul-pnl">--</div>
    <div class="kpi-sub" id="kpi-cumul-trades">&nbsp;</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Positions</div>
    <div class="kpi-value accent" id="kpi-positions">--</div>
    <div class="kpi-sub" id="kpi-pairs-count">&nbsp;</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Daily Loss Limit</div>
    <div class="kpi-value" id="kpi-daily-loss">--</div>
    <div class="kpi-sub" id="kpi-daily-loss-sub">&nbsp;</div>
    <div class="progress-track"><div class="progress-fill" id="loss-progress" style="width:0%;background:var(--green)"></div></div>
  </div>
  <div class="kpi">
    <div class="kpi-label">OOS Blocked</div>
    <div class="kpi-value" id="kpi-oos">--</div>
    <div class="kpi-sub" id="kpi-oos-sub">&nbsp;</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Circuit Breaker</div>
    <div class="kpi-value" id="kpi-circuit">--</div>
    <div class="kpi-sub" id="kpi-errors">&nbsp;</div>
  </div>
</div>
```

### Panel Layout (Positions, System, Pairs, Risk, Activity Log)

```html
<!-- OPEN POSITIONS TABLE -->
<div class="panel positions-panel">
  <div class="panel-header">
    <div class="panel-title">Open Positions</div>
    <div class="panel-badge" id="pos-count">0</div>
  </div>
  <div id="positions-body">
    <div class="empty-state">No open positions</div>
  </div>
</div>

<!-- SYSTEM STATUS -->
<div class="panel system-panel">
  <div class="panel-header">
    <div class="panel-title">System</div>
    <div class="panel-badge" id="sys-version">--</div>
  </div>
  <div id="system-rows">
    <div class="sys-row"><span class="sys-label">PID</span><span class="sys-value" id="sys-pid">--</span></div>
    <div class="sys-row"><span class="sys-label">Heartbeat</span><span class="sys-value" id="sys-heartbeat">--</span></div>
    <div class="sys-row"><span class="sys-label">Circuit Mode</span><span class="sys-value" id="sys-circuit">--</span></div>
    <div class="sys-row"><span class="sys-label">Error Count</span><span class="sys-value" id="sys-errors">--</span></div>
    <div class="sys-row"><span class="sys-label">Loop Counter</span><span class="sys-value" id="sys-loop">--</span></div>
    <div class="sys-row"><span class="sys-label">Taker Fee</span><span class="sys-value" id="sys-taker">--</span></div>
    <div class="sys-row"><span class="sys-label">Maker Fee</span><span class="sys-value" id="sys-maker">--</span></div>
    <div class="sys-row"><span class="sys-label">API Latency</span><span class="sys-value" id="sys-latency">--</span></div>
    <div class="sys-row"><span class="sys-label">Metrics Update</span><span class="sys-value" id="sys-metrics-ts">--</span></div>
    <div class="sys-row"><span class="sys-label">Last Refresh</span><span class="sys-value" id="sys-refresh">--</span></div>
  </div>
</div>

<!-- PAIRS OVERVIEW -->
<div class="panel pairs-panel">
  <div class="panel-header">
    <div class="panel-title">Pairs Overview</div>
    <div class="panel-badge" id="pairs-total">0</div>
  </div>
  <div id="pairs-body">
    <div class="empty-state">No pairs configured</div>
  </div>
</div>

<!-- RISK & PERFORMANCE -->
<div class="panel risk-panel">
  <div class="panel-header">
    <div class="panel-title">Risk &amp; Performance</div>
    <div class="panel-badge">live</div>
  </div>
  <div class="risk-grid" id="risk-grid">
    <div class="risk-item">
      <div class="risk-label">Cumul P&amp;L</div>
      <div class="risk-value" id="r-cumul">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Closed Trades</div>
      <div class="risk-value" id="r-trades">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Daily P&amp;L %</div>
      <div class="risk-value" id="r-daily-pct">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Starting Equity</div>
      <div class="risk-value" id="r-equity">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Emergency Halt</div>
      <div class="risk-value" id="r-halt">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">OOS Blocked</div>
      <div class="risk-value" id="r-oos">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Taker Fee</div>
      <div class="risk-value" id="r-taker">--</div>
    </div>
    <div class="risk-item">
      <div class="risk-label">Unrealized P&amp;L</div>
      <div class="risk-value" id="r-unrealized">--</div>
    </div>
  </div>
</div>

<!-- ACTIVITY LOG -->
<div class="panel alerts-panel">
  <div class="panel-header">
    <div class="panel-title">Activity Log</div>
    <div class="panel-badge" id="log-count">0</div>
  </div>
  <div id="log-body">
    <div class="empty-state">Waiting for data...</div>
  </div>
</div>
```

## Grid Column Layout Summary

| Panel | CSS class | grid-column |
|-------|-----------|-------------|
| KPI Strip | `.kpi-strip` | `1 / -1` (full width) |
| Positions | `.positions-panel` | `1 / 4` (3 cols) |
| System | `.system-panel` | `4 / 5` (1 col) |
| Pairs | `.pairs-panel` | `1 / 3` (2 cols) |
| Risk | `.risk-panel` | `3 / 5` (2 cols) |
| Activity Log | `.alerts-panel` | `1 / -1` (full width) |
