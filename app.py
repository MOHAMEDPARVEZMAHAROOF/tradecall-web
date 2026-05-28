from datetime import datetime
from functools import wraps
import json
import random
import uuid

from flask import Flask, request, redirect, url_for, session, jsonify, render_template_string

app = Flask(__name__)
app.secret_key = "tradecall-web-secret-key"

# In-memory user database
USER_DB = {
    "demo": "demo123"
}

# Mock watchlist
WATCHLIST = [
    "RELIANCE",
    "TATAMOTORS",
    "HDFCBANK",
    "INFY",
    "TCS",
    "NIFTY",
    "BANKNIFTY",
    "ICICIBANK",
    "SBIN",
    "ONGC"
]

# Application state
CALL_LOG = []
JOURNAL_ENTRIES = []
SESSION_STATE = {
    "session_id": None,
    "user": None,
    "started_at": None,
    "last_activity": None,
    "phase": "PHASE_1_CALL_ENGINE",
    "analyses_count": 0,
    "calls_count": 0,
    "executions_count": 0,
    "status": "idle"
}

SYSTEM_STATE = {
    "mode": "SAFE_MODE",
    "kill_switch": False,
    "last_updated": None,
    "notes": "System starts in SAFE_MODE until mode updates are applied."
}

SYSTEM_MODES = {
    "ANALYSIS_ONLY": "ANALYSIS_ONLY",
    "CALL_ONLY": "CALL_ONLY",
    "CONFIRMATION_MODE": "CONFIRMATION_MODE",
    "AUTO_EXECUTION_MODE": "AUTO_EXECUTION_MODE",
    "SAFE_MODE": "SAFE_MODE"
}

HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>TradeCall Web</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; padding: 24px; background: #f4f7fb; color: #1f2937; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
    .card { background: white; border-radius: 10px; padding: 18px; margin-bottom: 18px; box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05); }
    button { cursor: pointer; border: none; border-radius: 8px; background: #2563eb; color: white; padding: 10px 14px; margin-right: 10px; }
    button.secondary { background: #6b7280; }
    pre { background: #f8fafc; padding: 14px; border-radius: 8px; overflow-x: auto; }
    label { display: block; margin-top: 10px; margin-bottom: 4px; font-weight: 600; }
    select, input { width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #d1d5db; border-radius: 8px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>TradeCall Web Dashboard</h1>
    <p>Phase: <strong id="sessionPhase">loading...</strong> · Mode: <strong id="systemMode">loading...</strong> · Kill switch: <strong id="killSwitchState">loading...</strong></p>
  </div>
  <div>
    <button onclick="navigate('/logout')">Logout</button>
  </div>
</header>

<div class="grid">
  <div class="card">
    <h2>Watchlist</h2>
    <div id="watchlist"></div>
  </div>
  <div class="card">
    <h2>System Control</h2>
    <label for="modeSelect">Mode</label>
    <select id="modeSelect"></select>
    <label for="killSwitch">Kill switch</label>
    <select id="killSwitch"></select>
    <button onclick="saveSystem()">Update System</button>
  </div>
  <div class="card">
    <h2>Session Status</h2>
    <pre id="sessionInfo">loading...</pre>
  </div>
</div>

<div class="card">
  <h2>Analysis & Execution</h2>
  <label for="symbolInput">Symbol</label>
  <input id="symbolInput" list="symbols" placeholder="Search symbol...">
  <datalist id="symbols"></datalist>
  <button onclick="runAnalysis()">Analyze</button>
  <button onclick="runCallEngine()">Generate Calls</button>
  <button onclick="runAutoExecute()">Auto Execute</button>
  <div id="analysisResult"></div>
</div>

<div class="grid">
  <div class="card">
    <h2>Call Log</h2>
    <pre id="callLog">loading...</pre>
  </div>
  <div class="card">
    <h2>Journal</h2>
    <pre id="journalLog">loading...</pre>
  </div>
</div>

<script>
  async function api(path, opts) {
    const res = await fetch(path, opts);
    return await res.json();
  }

  function navigate(path) {
    window.location.href = path;
  }

  async function loadDashboard() {
    const session = await api('/api/session');
    const system = await api('/api/system');
    const calls = await api('/api/calls');
    const journal = await api('/api/journal');
    document.getElementById('sessionPhase').innerText = session.phase;
    document.getElementById('systemMode').innerText = system.mode;
    document.getElementById('killSwitchState').innerText = system.kill_switch ? 'ENABLED' : 'DISABLED';
    document.getElementById('sessionInfo').innerText = JSON.stringify(session, null, 2);
    document.getElementById('callLog').innerText = calls.length ? JSON.stringify(calls.slice(-10).reverse(), null, 2) : 'No calls logged yet.';
    document.getElementById('journalLog').innerText = journal.length ? JSON.stringify(journal.slice(-10).reverse(), null, 2) : 'No journal entries yet.';

    window.WATCHLIST = {{ watchlist | tojson }};
    const symbolList = document.getElementById('symbols');
    symbolList.innerHTML = '';
    window.WATCHLIST.forEach(symbol => {
      const opt = document.createElement('option');
      opt.value = symbol;
      symbolList.appendChild(opt);
    });
    const list = document.getElementById('watchlist');
    list.innerHTML = '<ul>' + WATCHLIST.map(s => `<li>${s}</li>`).join('') + '</ul>';

    const modeSelect = document.getElementById('modeSelect');
    modeSelect.innerHTML = '';
    ['SAFE_MODE','ANALYSIS_ONLY','CALL_ONLY','CONFIRMATION_MODE','AUTO_EXECUTION_MODE'].forEach(mode => {
      const opt = document.createElement('option');
      opt.value = mode;
      opt.text = mode;
      if (system.mode === mode) opt.selected = true;
      modeSelect.appendChild(opt);
    });
    const killSwitch = document.getElementById('killSwitch');
    killSwitch.innerHTML = '';
    [['false','OFF'], ['true','ON']].forEach(pair => {
      const opt = document.createElement('option');
      opt.value = pair[0]; opt.text = pair[1];
      if (String(system.kill_switch) === pair[0]) opt.selected = true;
      killSwitch.appendChild(opt);
    });
  }

  async function saveSystem() {
    const mode = document.getElementById('modeSelect').value;
    const kill_switch = document.getElementById('killSwitch').value === 'true';
    const res = await api('/api/system', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, kill_switch })
    });
    document.getElementById('analysisResult').innerText = JSON.stringify(res, null, 2);
    await loadDashboard();
  }

  async function runAnalysis() {
    const symbol = document.getElementById('symbolInput').value || '';
    const body = symbol ? { symbols: [symbol] } : { symbols: window.WATCHLIST };
    const res = await api('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    document.getElementById('analysisResult').innerText = JSON.stringify(res, null, 2);
    await loadDashboard();
  }

  async function runCallEngine() {
    const body = { symbols: window.WATCHLIST };
    const res = await api('/api/calls', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    document.getElementById('analysisResult').innerText = JSON.stringify(res, null, 2);
    await loadDashboard();
  }

  async function runAutoExecute() {
    const res = await api('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto: true })
    });
    document.getElementById('analysisResult').innerText = JSON.stringify(res, null, 2);
    await loadDashboard();
  }

  window.onload = loadDashboard;
</script>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>TradeCall Web Login</title>
  <style>
    body { font-family: Arial, sans-serif; background: #f4f7fb; color: #111827; display: flex; height: 100vh; align-items: center; justify-content: center; margin: 0; }
    .login-card { width: 360px; background: white; border-radius: 14px; padding: 30px; box-shadow: 0 20px 40px rgba(15,23,42,0.12); }
    h1 { margin-top: 0; }
    label { display: block; margin: 14px 0 6px; font-weight: 600; }
    input { width: 100%; padding: 12px; border: 1px solid #d1d5db; border-radius: 10px; }
    button { width: 100%; padding: 12px; background: #2563eb; border: none; color: white; font-weight: 700; border-radius: 10px; margin-top: 18px; }
    .hint { margin-top: 12px; font-size: 0.95rem; color: #6b7280; }
    .error { margin-top: 12px; color: #b91c1c; }
  </style>
</head>
<body>
<div class="login-card">
  <h1>TradeCall Web</h1>
  <form method="post" action="/login">
    <label for="username">Username</label>
    <input id="username" name="username" type="text" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required>
    <button type="submit">Login</button>
    <div class="hint">Use demo / demo123</div>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </form>
</div>
</body>
</html>
"""

WELCOME_TEMPLATE = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Redirecting...</title></head>
<body><script>window.location.replace('/dashboard');</script></body>
</html>
"""

# Helpers

def require_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('username'):
            return redirect(url_for('index'))
        return func(*args, **kwargs)
    return wrapper


def login_user(username):
    now = datetime.utcnow().isoformat() + 'Z'
    session['username'] = username
    session['session_id'] = str(uuid.uuid4())
    SESSION_STATE.update({
        'session_id': session['session_id'],
        'user': username,
        'started_at': now,
        'last_activity': now,
        'phase': 'PHASE_1_CALL_ENGINE',
        'analyses_count': 0,
        'calls_count': 0,
        'executions_count': 0,
        'status': 'active'
    })
    log_journal('Login', f'User {username} logged in.')


def logout_user():
    username = session.get('username')
    session.clear()
    SESSION_STATE.update({
        'session_id': None,
        'user': None,
        'started_at': None,
        'last_activity': None,
        'phase': 'PHASE_1_CALL_ENGINE',
        'analyses_count': 0,
        'calls_count': 0,
        'executions_count': 0,
        'status': 'idle'
    })
    log_journal('Logout', f'User {username} logged out.')


def update_session_activity(action=None):
    now = datetime.utcnow().isoformat() + 'Z'
    SESSION_STATE['last_activity'] = now
    if action:
        SESSION_STATE['status'] = action


def log_journal(event, description):
    entry = {
        'id': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'event': event,
        'description': description
    }
    JOURNAL_ENTRIES.append(entry)
    return entry


def log_call(symbol, signal, confidence, risk_score, note=''):
    call = {
        'id': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'symbol': symbol,
        'signal': signal,
        'confidence': confidence,
        'risk_score': risk_score,
        'note': note,
        'mode': SYSTEM_STATE['mode']
    }
    CALL_LOG.append(call)
    SESSION_STATE['calls_count'] += 1
    log_journal('Call Generated', f"{symbol} {signal} signal created with risk {risk_score}.")
    return call


def create_execution(symbol, signal, risk_score, executed):
    entry = {
        'id': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'symbol': symbol,
        'signal': signal,
        'risk_score': risk_score,
        'executed': executed,
        'mode': SYSTEM_STATE['mode'],
        'kill_switch': SYSTEM_STATE['kill_switch']
    }
    JOURNAL_ENTRIES.append(entry)
    SESSION_STATE['executions_count'] += 1
    return entry


def normalize_symbol(symbol):
    return symbol.strip().upper()


def get_market_price(symbol):
    base = 1000 if symbol in ['NIFTY', 'BANKNIFTY'] else 200
    return round(base + random.uniform(-0.8, 1.2) * random.randint(10, 50), 2)


def detect_patterns(symbol, price, momentum):
    patterns = []
    if 'BANK' in symbol and momentum > 0.5:
        patterns.append('Banking Momentum')
    if momentum < -0.5:
        patterns.append('Mean Reversion')
    if price % 2 == 0:
        patterns.append('Round Number Support')
    if len(symbol) <= 5 and momentum > 0.4:
        patterns.append('Momentum Breakout')
    return patterns or ['No clear pattern']


def calculate_risk(signal, momentum, volatility):
    base = 50
    modifier = abs(momentum) * 20 + volatility * 15
    if signal == 'SELL':
        modifier += 8
    if signal == 'BUY' and momentum > 0.5:
        modifier -= 12
    risk = max(10, min(90, int(base + modifier)))
    return risk


def build_analysis(symbol):
    price = get_market_price(symbol)
    momentum = round(random.uniform(-1.0, 1.0), 2)
    volatility = round(random.uniform(0.1, 1.5), 2)
    signal = 'BUY' if momentum > 0.2 else 'SELL' if momentum < -0.2 else 'HOLD'
    patterns = detect_patterns(symbol, price, momentum)
    risk_score = calculate_risk(signal, momentum, volatility)
    recommendation = 'monitor' if signal == 'HOLD' else 'watch closely' if risk_score > 60 else 'consider trade'
    return {
        'symbol': symbol,
        'price': price,
        'momentum': momentum,
        'volatility': volatility,
        'signal': signal,
        'patterns': patterns,
        'risk_score': risk_score,
        'recommendation': recommendation,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }


def generate_analysis(symbols):
    analyses = []
    now = datetime.utcnow().isoformat() + 'Z'
    for symbol in symbols:
        symbol = normalize_symbol(symbol)
        if not symbol:
            continue
        analyses.append(build_analysis(symbol))
    SESSION_STATE['analyses_count'] += len(analyses)
    SESSION_STATE['phase'] = 'PHASE_1_CALL_ENGINE'
    SESSION_STATE['last_activity'] = now
    log_journal('Analysis Generated', f'Generated analysis for {len(analyses)} symbol(s).')
    return analyses


def evaluate_auto_execution(analysis):
    symbol = analysis['symbol']
    signal = analysis['signal']
    risk_score = analysis['risk_score']
    executed = False

    if SYSTEM_STATE['kill_switch']:
        create_execution(symbol, signal, risk_score, executed=False)
        return {'symbol': symbol, 'status': 'blocked_by_kill_switch', 'risk_score': risk_score}

    if SYSTEM_STATE['mode'] == SYSTEM_MODES['AUTO_EXECUTION_MODE']:
        if risk_score <= 55 and signal in ['BUY', 'SELL']:
            executed = True
            create_execution(symbol, signal, risk_score, executed=True)
            log_journal('Auto Execution', f'Auto-executed {signal} for {symbol} at risk {risk_score}.')
        else:
            create_execution(symbol, signal, risk_score, executed=False)
            log_journal('Auto Execution Blocked', f'{symbol} had risk {risk_score} and was not executed.')
    else:
        create_execution(symbol, signal, risk_score, executed=False)

    return {
        'symbol': symbol,
        'signal': signal,
        'executed': executed,
        'risk_score': risk_score,
        'mode': SYSTEM_STATE['mode'],
        'kill_switch': SYSTEM_STATE['kill_switch']
    }


def generate_call_engine(symbols):
    calls = []
    if SYSTEM_STATE['mode'] == SYSTEM_MODES['SAFE_MODE']:
        update_session_activity('safe_mode_blocked')
        return []

    for symbol in symbols:
        analysis = build_analysis(symbol)
        if SYSTEM_STATE['mode'] == SYSTEM_MODES['ANALYSIS_ONLY']:
            calls.append({'symbol': symbol, 'signal': analysis['signal'], 'reason': 'Analysis-only mode', 'risk_score': analysis['risk_score']})
        elif SYSTEM_STATE['mode'] == SYSTEM_MODES['CALL_ONLY']:
            if analysis['signal'] != 'HOLD':
                calls.append(log_call(symbol, analysis['signal'], analysis['risk_score'], note='Call-only signal'))
        elif SYSTEM_STATE['mode'] == SYSTEM_MODES['CONFIRMATION_MODE']:
            if analysis['signal'] != 'HOLD' and analysis['risk_score'] <= 70:
                calls.append(log_call(symbol, analysis['signal'], analysis['risk_score'], note='Confirmation recommended'))
        else:
            if analysis['signal'] != 'HOLD' and analysis['risk_score'] <= 70:
                calls.append(log_call(symbol, analysis['signal'], analysis['risk_score'], note='Generated by call engine'))
    update_session_activity('call_engine_run')
    return calls

# Routes
@app.route('/', methods=['GET'])
def index():
    if session.get('username'):
        return render_template_string(WELCOME_TEMPLATE)
    return render_template_string(LOGIN_TEMPLATE, error=None)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template_string(LOGIN_TEMPLATE, error=None)

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if USER_DB.get(username) == password:
        login_user(username)
        return redirect(url_for('dashboard'))

    return render_template_string(LOGIN_TEMPLATE, error='Invalid credentials. Use demo/demo123')

@app.route('/logout', methods=['GET'])
@require_login
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard', methods=['GET'])
@require_login
def dashboard():
    return render_template_string(HTML_TEMPLATE, watchlist=WATCHLIST)

@app.route('/api/search', methods=['GET'])
@require_login
def api_search():
    query = normalize_symbol(request.args.get('q', ''))
    results = [symbol for symbol in WATCHLIST if query in symbol] if query else WATCHLIST
    update_session_activity('search')
    return jsonify({'query': query, 'results': results})

@app.route('/api/analyze', methods=['POST'])
@require_login
def api_analyze():
    payload = request.get_json(silent=True) or {}
    symbols = payload.get('symbols') or WATCHLIST
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)]
    analyses = generate_analysis(symbols)
    if SYSTEM_STATE['mode'] in [SYSTEM_MODES['CALL_ONLY'], SYSTEM_MODES['ANALYSIS_ONLY'], SYSTEM_MODES['CONFIRMATION_MODE'], SYSTEM_MODES['AUTO_EXECUTION_MODE']]:
        generate_call_engine(symbols)
    if SYSTEM_STATE['mode'] == SYSTEM_MODES['AUTO_EXECUTION_MODE']:
        auto_results = [evaluate_auto_execution(analysis) for analysis in analyses]
    else:
        auto_results = []
    return jsonify({'analyses': analyses, 'auto_execution': auto_results})

@app.route('/api/execute', methods=['POST'])
@require_login
def api_execute():
    payload = request.get_json(silent=True) or {}
    auto = payload.get('auto', False)
    symbol = normalize_symbol(payload.get('symbol', ''))
    action = payload.get('action', '').upper()
    target_symbols = [symbol] if symbol else WATCHLIST
    executed = []
    blocked = []

    for symbol in target_symbols:
        analysis = build_analysis(symbol)
        if auto or SYSTEM_STATE['mode'] == SYSTEM_MODES['AUTO_EXECUTION_MODE']:
            result = evaluate_auto_execution(analysis)
            if result['executed']:
                executed.append(result)
            else:
                blocked.append(result)
        else:
            executed.append(create_execution(symbol, action or analysis['signal'], analysis['risk_score'], executed=False))

    update_session_activity('execute')
    return jsonify({'executed': executed, 'blocked': blocked, 'mode': SYSTEM_STATE['mode'], 'kill_switch': SYSTEM_STATE['kill_switch']})

@app.route('/api/calls', methods=['GET', 'POST'])
@require_login
def api_calls():
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        symbols = payload.get('symbols') or WATCHLIST
        if isinstance(symbols, str):
            symbols = [symbols]
        symbols = [normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)]
        calls = generate_call_engine(symbols)
        update_session_activity('generate_calls')
        return jsonify({'calls_generated': len(calls), 'calls': calls})
    update_session_activity('fetch_calls')
    return jsonify(CALL_LOG[-50:])

@app.route('/api/journal', methods=['GET'])
@require_login
def api_journal():
    update_session_activity('fetch_journal')
    return jsonify(JOURNAL_ENTRIES[-50:])

@app.route('/api/session', methods=['GET'])
@require_login
def api_session():
    return jsonify(SESSION_STATE)

@app.route('/api/system', methods=['GET', 'POST'])
@require_login
def api_system():
    if request.method == 'GET':
        return jsonify(SYSTEM_STATE)

    payload = request.get_json(silent=True) or {}
    mode = payload.get('mode')
    kill_switch = payload.get('kill_switch')
    if mode in SYSTEM_MODES:
        SYSTEM_STATE['mode'] = SYSTEM_MODES[mode]
    if isinstance(kill_switch, bool):
        SYSTEM_STATE['kill_switch'] = kill_switch
    SYSTEM_STATE['last_updated'] = datetime.utcnow().isoformat() + 'Z'
    SYSTEM_STATE['notes'] = f"Mode updated to {SYSTEM_STATE['mode']}, kill switch={'ON' if SYSTEM_STATE['kill_switch'] else 'OFF'}."
    log_journal('System Update', SYSTEM_STATE['notes'])
    return jsonify(SYSTEM_STATE)

@app.errorhandler(404)
def handle_not_found(error):
    return jsonify({'error': 'Endpoint not found', 'path': request.path}), 404

@app.errorhandler(500)
def handle_server_error(error):
    return jsonify({'error': 'Server error', 'message': str(error)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
