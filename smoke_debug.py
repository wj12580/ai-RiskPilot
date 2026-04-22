"""Debug API test"""
import urllib.request, json, sys
sys.stdout.reconfigure(encoding='utf-8')

def test(url):
    try:
        r = urllib.request.urlopen(url)
        print('OK:', url, r.read()[:200].decode())
    except urllib.error.HTTPError as e:
        print('HTTP', e.code, ':', e.read()[:500].decode())
    except Exception as e:
        print('ERR:', url, e)

test('http://127.0.0.1:5000/api/records/stats')
test('http://127.0.0.1:5000/api/records/channels')
test('http://127.0.0.1:5000/api/reviews')
test('http://127.0.0.1:5000/api/knowledge/topics')
