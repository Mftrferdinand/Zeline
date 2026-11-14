"""Read-only live acceptance for the runtime header and agent summary fix."""
import hashlib
import json
from pathlib import Path
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

home = Path.home()
private = json.loads((home / '.zeline/run/runtime-access.json').read_text())
assets = home / '.local/share/zeline/venv/lib/python3.13/site-packages/zeline/runtime_assets'
for name, route in [('index.html', '/'), ('runtime.js', '/runtime.js')]:
    response = requests.get('http://127.0.0.1:8765' + route, timeout=10)
    response.raise_for_status()
    assert response.content == (assets / name).read_bytes(), name + ' served/install mismatch'
options = Options()
options.binary_location = '/data/data/com.termux/files/usr/bin/chromium-browser'
for arg in ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--window-size=390,844']:
    options.add_argument(arg)
options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
driver = webdriver.Chrome(service=Service('/data/data/com.termux/files/usr/bin/chromedriver'), options=options)
try:
    driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
    driver.get(private['owner_url'])
    WebDriverWait(driver, 25).until(lambda d: d.find_element('id', 'connection-label').text == 'Connected')
    WebDriverWait(driver, 15).until(lambda d: d.find_element('id', 'agent-main-count').text != 'Unknown')
    values = driver.execute_script('''return {
      title:document.title,
      heading:document.querySelector('.terminal-window-title').textContent,
      topMode:!!document.querySelector('.terminal-titlebar .terminal-window-mode'),
      footer:document.querySelector('.monitor-footer').textContent,
      main:document.getElementById('agent-main-count').textContent,
      subagent:document.getElementById('agent-subagent-count').textContent,
      mainDetail:document.getElementById('agent-main-detail').textContent,
      subagentDetail:document.getElementById('agent-subagent-detail').textContent,
      page:document.body.dataset.page,
      agentListVisible:document.getElementById('agent-list').getClientRects().length>0
    };''')
    assert values['title'] == 'Zeline Runtime'
    assert values['heading'] == 'Zeline Runtime'
    assert not values['topMode']
    assert 'read-only' in values['footer']
    assert values['main'] == '1', values['main']
    assert values['subagent'] == '0', values['subagent']
    assert not values['agentListVisible'], 'Must prove summary updates without opening Agents panel'
    errors = [entry for entry in driver.get_log('browser') if entry['level'] == 'SEVERE']
    assert not errors, 'Browser console errors: ' + str(len(errors))
    folder = home / '.cache/zeline-runtime-header-fix'
    driver.save_screenshot(str(folder / 'runtime-verified.png'))
    (folder / 'browser-result.json').write_text(json.dumps(values, indent=2))
    print(json.dumps({'verified':values,'browser_errors':0,'served_matches_install':True}, ensure_ascii=False))
finally:
    driver.quit()
manifest = json.loads((home / '.cache/zeline-runtime-header-fix/manifest.json').read_text())
base = assets.parent
for name, expected in manifest['files'].items():
    if not name.startswith('runtime_assets/'):
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == expected, name + ' changed'
print('PASS: engine, memory/session code, and staged Telegram UI patch unchanged; gateway was not restarted')
