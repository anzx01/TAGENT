"""查看录制内容"""
import json
d = json.load(open('recordings/20260807_184546/events.json', 'r', encoding='utf-8'))
for i, s in enumerate(d['steps']):
    action = s['action']
    text = s.get('text', s.get('key', ''))
    print(f"[{i}] {action} text='{text}'")
