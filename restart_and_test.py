"""Start server and run smoke test"""
import subprocess, sys, time, os, json, urllib.request

PYTHON = r'F:\2022gk\ai赛2\RiskPilot\venv\Scripts\python.exe'

# Kill any existing process on port 5000
try:
    import socket
    s = socket.socket()
    s.settimeout(1)
    s.connect(('127.0.0.1', 5000))
    s.close()
    print('Port 5000 already in use, killing...')
    subprocess.run([PYTHON, '-c', 'import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1",5000)); s.close()'], stderr=subprocess.DEVNULL)
    time.sleep(2)
except:
    pass

# Kill existing RiskPilot processes
try:
    subprocess.run(['taskkill', '/F', '/FI', 'WINDOWTITLE eq *RiskPilot*'], stderr=subprocess.DEVNULL, timeout=3)
    time.sleep(1)
except:
    pass

# Start server
os.chdir(r'F:\2022gk\ai赛2\RiskPilot')
p = subprocess.Popen(
    [PYTHON, 'app.py'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
print('Server started, PID:', p.pid)
time.sleep(6)

# Smoke test
try:
    r1 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/stats')
    print('STATS:', json.loads(r1.read()))

    r2 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/channels')
    print('CHANNELS:', json.loads(r2.read()))

    r3 = urllib.request.urlopen('http://127.0.0.1:5000/api/reviews')
    print('REVIEWS OK, count:', len(json.loads(r3.read()).get('reviews', [])))

    print('\nALL OK')
except Exception as e:
    err = p.stderr.read().decode('gbk', errors='replace')
    print('API ERROR:', e)
    print('STDERR:', err[:800])

print('\nServer still running at http://127.0.0.1:5000')
print('PID:', p.pid)
