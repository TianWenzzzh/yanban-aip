import json, time, urllib.request
tid = json.load(open('webui/fixtures/task_post_response.json', encoding='utf-8'))['task_id']
t0 = time.time()
while time.time() - t0 < 900:
    try:
        d = json.loads(urllib.request.urlopen(f'http://127.0.0.1:8041/task/{tid}', timeout=10).read())
    except Exception as e:
        print('poll error', e); time.sleep(5); continue
    if d['status'] != 'running':
        json.dump(d, open('webui/fixtures/task_get_completed.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('DONE', d['status'], 'elapsed_ms', d['elapsed_ms'], 'subresults', [(s['partner'], s['state']) for s in d['subresults']])
        break
    time.sleep(5)
else:
    print('TIMEOUT after 900s')
