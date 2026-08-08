import json, sys
d = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
for i, s in enumerate(d['steps']):
    t = s.get('text', s.get('key', ''))
    print(f"[{i}] {s['action']} t={s['t']} text={t!r}")
