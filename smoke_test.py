"""Quick smoke test"""
import urllib.request, json, sys
sys.stdout.reconfigure(encoding='utf-8')

try:
    r1 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/stats')
    print('STATS:', json.loads(r1.read()))
except Exception as e:
    print('STATS_FAIL:', e)

try:
    r2 = urllib.request.urlopen('http://127.0.0.1:5000/api/records/channels')
    print('CHANNELS:', json.loads(r2.read()))
except Exception as e:
    print('CHANNELS_FAIL:', e)

try:
    r3 = urllib.request.urlopen('http://127.0.0.1:5000/api/reviews')
    data3 = json.loads(r3.read())
    print('REVIEWS count:', len(data3.get('reviews', [])))
except Exception as e:
    print('REVIEWS_FAIL:', e)
