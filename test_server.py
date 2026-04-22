"""Server startup + smoke test"""
import subprocess, sys, time, os

# Start server
os.chdir(r'F:\2022gk\ai赛2\RiskPilot')
p = subprocess.Popen(
    [sys.executable, 'app.py'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=r'F:\2022gk\ai赛2\RiskPilot'
)
print('Server PID:', p.pid)

# Wait for startup
time.sleep(6)

# Test APIs
try:
    import urllib.request, json
    r1 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/stats')
    data1 = json.loads(r1.read())
    print('STATS OK:', data1)

    r2 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/channels')
    data2 = json.loads(r2.read())
    print('CHANNELS OK:', data2)

    r3 = urllib.request.urlopen('http://127.0.0.1:5000/api/reviews')
    data3 = json.loads(r3.read())
    print('REVIEWS OK, count:', len(data3.get('reviews', [])))

    r4 = urllib.request.urlopen('http://127.0.0.1:5000/api/knowledge/topics')
    print('TOPICS OK:', r4.status)

    print('\nALL APIS OK - server running at http://127.0.0.1:5000')

except Exception as e:
    err = p.stderr.read().decode('gbk', errors='replace')
    print('API ERROR:', e)
    print('STDERR:', err[:500])

# Keep server running
print('Keeping server alive...')
p.wait()
