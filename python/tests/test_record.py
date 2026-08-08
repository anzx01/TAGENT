"""测试录制引擎：模拟点击+键盘输入，验证钩子是否捕获事件"""
import time, json, ctypes, urllib.request

def post(url):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'},
                                 data=b'{}')
    return json.loads(urllib.request.urlopen(req).read())

# 1. 开始录制
r = post('http://127.0.0.1:18899/record/start')
print('start:', r)
time.sleep(1)

# 2. 模拟鼠标点击
ctypes.windll.user32.SetCursorPos(500, 500)
ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
print('clicked at 500,500')
time.sleep(0.5)

# 3. 模拟键盘输入 AB
for vk in [0x41, 0x42]:  # A, B
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.1)
print('typed AB')
time.sleep(1)

# 4. 停止录制
r = post('http://127.0.0.1:18899/record/stop')
print('stop:', r)

# 5. 检查录制内容
path = r.get('events_path', '')
if path:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    steps = data.get('steps', [])
    print(f'\n=== 捕获到 {len(steps)} 步 ===')
    for i, s in enumerate(steps):
        action = s.get('action', '?')
        x = s.get('screen_x', '')
        y = s.get('screen_y', '')
        extra = s.get('text', s.get('key', ''))
        window = s.get('window', '')[:30]
        shot = s.get('screenshot', '')
        print(f'  [{i}] {action} ({x},{y}) "{extra}" win={window} shot={shot}')
