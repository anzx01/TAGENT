import json, os
base = r'd:\aiapp\TAGENT\recordings'
dirs = sorted([x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x))])
latest = dirs[-1]
p = os.path.join(base, latest, 'events.json')
data = json.load(open(p, 'r', encoding='utf-8'))
print(f'dir={latest} count={data["count"]}')
for i, s in enumerate(data['steps']):
    t = s.get('text', s.get('key', ''))
    print(f'  [{i}] {s["action"]} text={t!r}')
