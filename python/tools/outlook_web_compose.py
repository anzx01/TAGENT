"""Outlook Web DOM automation with persistent browser profile and verification."""
from __future__ import annotations
import argparse, json, sys, time, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import BASE_DIR

URL = "https://outlook.live.com/mail/"

def main():
    p=argparse.ArgumentParser(); p.add_argument('--to',required=True); p.add_argument('--body',default='\u60a8\u597d'); p.add_argument('--subject',default='TAGENT-WPS-web'); p.add_argument('--attach',type=Path); p.add_argument('--send',action='store_true'); p.add_argument('--profile',type=Path,default=BASE_DIR/'runs'/'edge-profile'); a=p.parse_args()
    if a.attach: a.attach=a.attach.resolve(strict=True)
    from playwright.sync_api import sync_playwright
    a.profile.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        context=pw.chromium.launch_persistent_context(str(a.profile), channel='msedge', headless=False)
        page=next((p for p in context.pages if p.url.startswith('https://outlook.live.com')), None) or (context.pages[0] if context.pages else context.new_page())
        if not page.url.startswith('https://outlook.live.com'):
            page.goto(URL,wait_until='domcontentloaded')
        page.wait_for_timeout(5000)
        if page.get_by_text('Sign in',exact=True).count() or page.get_by_text('\u767b\u5f55',exact=True).count():
            print('Please sign in in the opened Outlook Web window; waiting up to 120 seconds...', flush=True)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                page.wait_for_timeout(2000)
                if not (page.get_by_text('Sign in',exact=True).count() or page.get_by_text('\u767b\u5f55',exact=True).count()):
                    break
            else:
                context.close(); raise SystemExit('Outlook Web login was not completed within 120 seconds.')
        new_mail = page.get_by_role('button', name=re.compile(r'New mail|\u65b0\u90ae\u4ef6', re.I))
        if not new_mail.count():
            new_mail = page.get_by_text('\u65b0\u90ae\u4ef6', exact=True)
        new_mail.first.click()
        page.wait_for_timeout(1000)
        to=page.locator('input[aria-label*="To"],input[aria-label*="\u6536\u4ef6"],input[placeholder*="To"]')
        if not to.count(): raise SystemExit('Recipient input not found in Outlook Web DOM')
        to.first.fill(a.to); page.keyboard.press('Enter')
        subject=page.locator('input[aria-label*="Subject"],input[aria-label*="\u4e3b\u9898"],input[placeholder*="Subject"]')
        if not subject.count(): raise SystemExit('Subject input not found in Outlook Web DOM')
        subject.first.fill(a.subject)
        body=page.locator('[contenteditable="true"]').last
        body.fill(a.body)
        if a.attach:
            chooser=None
            with page.expect_file_chooser() as event:
                page.get_by_role('button',name=lambda n: n and ('Attach' in n or '\u9644\u52a0' in n)).click()
            chooser=event.value; chooser.set_files(str(a.attach))
        page.screenshot(path=str(BASE_DIR/'runs'/'outlook_web_ready.png'),full_page=True)
        if not a.send:
            print('Draft prepared; not sent'); context.close(); return
        page.get_by_role('button',name=lambda n: n and (n.lower()=='send' or '\u53d1送' in n)).click(); page.wait_for_timeout(3000)
        page.get_by_text('Sent',exact=True).click() if page.get_by_text('Sent',exact=True).count() else page.get_by_text('\u5df2发送邮件',exact=True).click()
        page.wait_for_timeout(2000)
        if not page.get_by_text(a.subject,exact=True).count(): raise SystemExit('Sent message subject not found')
        print('Sent record verified'); context.close()

if __name__=='__main__': main()
