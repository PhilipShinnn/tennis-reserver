# -*- coding: utf-8 -*-
"""
세종시 테니스장 자동예약 — 실제 Chrome 자동 실행 버전

playwright의 chromium 대신 실제 Chrome을 사용해 봇 감지 회피
"""

import sys
import asyncio
import getpass
import re
import random
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ playwright가 없습니다.\n  pip3 install playwright")
    sys.exit(1)

# macOS Chrome 경로
CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
CDP_PORT    = 9222
CDP_URL     = f'http://localhost:{CDP_PORT}'

TENNIS_LIST_URL = 'https://onestop.sejong.go.kr/Usr/resve/instList.do?fcltClCode=FC_TENNIS'
LOGIN_URL       = 'https://www.sejong.go.kr/kor/login.do'

COURT_OPTIONS = ['중앙공원', '수질복원센터A', '수질복원센터B', '금남']
COURT_NUMBERS = {
    '중앙공원':    ['6', '7', '8', '9', '10'],
    '수질복원센터A': ['7', '9'],
    '수질복원센터B': [],
    '금남':       ['3'],
}
TIME_OPTIONS = [('06','06:00~08:00'),('08','08:00~10:00'),('10','10:00~12:00'),
                ('12','12:00~14:00'),('14','14:00~16:00'),('16','16:00~18:00'),
                ('18','18:00~20:00'),('20','20:00~22:00')]
TIME_FALLBACK = {
    '06': ['06', '20', '18', '08', '10', '12', '14', '16'],
    '08': ['08', '06', '20', '18', '10', '12', '14', '16'],
    '10': ['10', '08', '06', '12', '14', '16', '18', '20'],
    '12': ['12', '10', '08', '06', '14', '16', '18', '20'],
    '14': ['14', '12', '10', '08', '06', '16', '18', '20'],
    '16': ['16', '14', '12', '10', '08', '06', '18', '20'],
    '18': ['18', '20', '06', '16', '08', '10', '12', '14'],
    '20': ['20', '18', '06', '16', '08', '10', '12', '14'],
}


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    print(f'[{ts}] {msg}')


def launch_chrome():
    """실제 Chrome을 디버그 모드로 실행"""
    if not Path(CHROME_PATH).exists():
        print(f'❌ Chrome을 찾을 수 없습니다: {CHROME_PATH}')
        sys.exit(1)

    user_data = '/tmp/chrome_tennis_debug'
    cmd = [
        CHROME_PATH,
        f'--remote-debugging-port={CDP_PORT}',
        f'--user-data-dir={user_data}',
        '--no-first-run',
        '--no-default-browser-check',
    ]
    log(f'Chrome 실행 중...')
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # Chrome 시작 대기
    return proc


class ChromeReserver:
    def __init__(self, cfg):
        self.cfg = cfg

    async def _delay(self):
        base = self.cfg.get('slow_mo', 120)
        ms = random.randint(base - 40, base + 40)
        await asyncio.sleep(ms / 1000)

    # ────────────────────────────────────────────────
    async def run(self):
        chrome_proc = launch_chrome()

        async with async_playwright() as p:
            # CDP로 실제 Chrome에 연결
            for attempt in range(5):
                try:
                    browser = await p.chromium.connect_over_cdp(CDP_URL)
                    break
                except Exception:
                    log(f'Chrome 연결 대기 중... ({attempt+1}/5)')
                    await asyncio.sleep(1)
            else:
                print('❌ Chrome 연결 실패')
                chrome_proc.terminate()
                sys.exit(1)

            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            log('✅ Chrome 연결 완료')

            try:
                await self._login(page)
                await self._pre_position(page)
                await self._wait_until_scheduled(page)
                await self._fast_reserve(page)
            except Exception as e:
                log(f'❌ 오류: {e}')
                import traceback; traceback.print_exc()
            finally:
                input('\n[Enter] 누르면 종료합니다...')
                chrome_proc.terminate()

    # ────────────────────────────────────────────────
    async def _login(self, page):
        log('onestop 메인 이동')
        await page.goto('https://onestop.sejong.go.kr/Usr/main/main.do')
        await page.wait_for_load_state('networkidle')
        await self._close_popup(page)

        login_link = page.locator('a[href*="login"], a[title*="로그인"]').first
        if await login_link.count():
            await login_link.click()
        else:
            await page.goto(LOGIN_URL)
        await page.wait_for_load_state('networkidle')

        log(f'로그인 페이지: {page.url}')
        await self._delay()
        await page.fill('#id', self.cfg['user_id'])
        await self._delay()
        await page.fill('#password', self.cfg['user_pw'])
        await self._delay()

        submit = page.locator('input[type="submit"], button[type="submit"]').first
        if await submit.count():
            await submit.click()
        else:
            await page.evaluate('document.querySelector("form").submit()')
        await page.wait_for_load_state('networkidle')

        if 'onestop.sejong.go.kr' not in page.url:
            await page.goto('https://onestop.sejong.go.kr/Usr/main/main.do')
            await page.wait_for_load_state('networkidle')

        await self._close_popup(page)
        body = await page.inner_text('body')
        if '로그아웃' in body or '마이페이지' in body:
            log('✅ 로그인 성공')
        else:
            log(f'⚠️ 로그인 상태 불명확 ({page.url})')

    # ────────────────────────────────────────────────
    async def _close_popup(self, page):
        await page.wait_for_timeout(600)
        for text in ['닫기', '확인', '닫 기', '24시간 열지 않기']:
            try:
                el = page.get_by_text(text, exact=True).first
                if await el.is_visible(timeout=400):
                    await el.click()
                    await page.wait_for_timeout(300)
            except Exception:
                pass

    # ────────────────────────────────────────────────
    async def _pre_position(self, page):
        days        = self.cfg['days_ahead']
        target_date = datetime.now() + timedelta(days=days)
        ty = int(target_date.year)
        tm = int(target_date.month)

        log(f'사전 위치: {ty}-{tm:02d} 달력으로 이동 중...')
        await page.goto(TENNIS_LIST_URL)
        await page.wait_for_load_state('networkidle')

        keyword    = self.cfg['court']
        court_link = page.locator(f'a[title*="{keyword}"][title*="예약"]').first
        await court_link.wait_for(timeout=10000)
        await self._delay()
        await court_link.click()
        await page.wait_for_load_state('networkidle')
        log(f'코트 선택: {keyword}')

        # 목표 월까지 다음달 클릭
        now = datetime.now()
        months_ahead = (ty * 12 + tm) - (now.year * 12 + now.month)
        log(f'다음달 클릭 횟수: {months_ahead}')
        for _ in range(months_ahead):
            await page.wait_for_timeout(500)
            idx = await page.evaluate("""
                () => {
                    var els = Array.from(document.querySelectorAll('a, button, input[type=button]'));
                    return els.findIndex(el => {
                        var t = (el.innerText || el.value || el.title || '').trim();
                        return t === '다음달' || t === '다음 달';
                    });
                }
            """)
            if idx >= 0:
                log('다음달 클릭')
                await page.locator('a, button, input[type=button]').nth(idx).click()
                await page.wait_for_load_state('domcontentloaded')
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(500)
            else:
                log('다음달 버튼 못 찾음')
                break

        # 서버 시간 읽기
        offset = await self._read_server_offset(page)
        self.cfg['server_offset'] = offset
        log(f'✅ {ty}-{tm:02d} 달력 대기 중 (오프셋: {offset:+.0f}초)')

    # ────────────────────────────────────────────────
    async def _read_server_offset(self, page):
        result = None
        for _ in range(3):
            try:
                await page.wait_for_load_state('domcontentloaded')
                result = await page.evaluate("""
                    () => {
                        if (!document.body) return null;
                        var body = document.body.innerText || document.body.textContent || '';
                        var m = body.match(/(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일\\s*(\\d{2}):(\\d{2}):(\\d{2})/);
                        if (m) return {y:+m[1], mo:+m[2], d:+m[3], h:+m[4], mi:+m[5], s:+m[6]};
                        return null;
                    }
                """)
                break
            except Exception:
                await asyncio.sleep(0.5)
        if not result:
            log('서버 시간 파싱 실패 — 오프셋 0')
            return 0
        server_dt = datetime(result['y'], result['mo'], result['d'],
                             result['h'], result['mi'], result['s'])
        local_dt  = datetime.now().replace(microsecond=0)
        offset    = (server_dt - local_dt).total_seconds()
        log(f'서버: {server_dt.strftime("%H:%M:%S")}  로컬: {local_dt.strftime("%H:%M:%S")}  오프셋: {offset:+.0f}초')
        return offset

    # ────────────────────────────────────────────────
    async def _wait_until_scheduled(self, page=None):
        offset = self.cfg.get('server_offset', 0)
        now    = datetime.now()
        target_server = now.replace(hour=self.cfg['hour'], minute=self.cfg['minute'],
                                    second=0, microsecond=500000)  # 9:00:00.5
        target_local  = target_server - timedelta(seconds=offset)

        if now >= target_local:
            log('설정 시각 이미 지남 → 바로 실행')
            return
        log(f'⏳ 로컬 {target_local.strftime("%H:%M:%S")} 까지 대기...')
        while True:
            remaining = (target_local - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            m, s = divmod(int(remaining), 60)
            print(f'\r  남은 시간: {m}분 {s}초  ', end='', flush=True)
            if remaining <= 5 and page is not None:
                print()
                try:
                    log('⏱ 서버 시간 재확인...')
                    new_offset = await self._read_server_offset(page)
                    target_local = target_server - timedelta(seconds=new_offset)
                    self.cfg['server_offset'] = new_offset
                except Exception as e:
                    log(f'서버 시간 재확인 실패 (무시): {e}')
                page = None
            await asyncio.sleep(min(1, remaining))
        print()
        log(f'⏰ {datetime.now().strftime("%H:%M:%S.%f")[:-3]} 실행 시작!')

    # ────────────────────────────────────────────────
    async def _fast_reserve(self, page):
        days        = self.cfg['days_ahead']
        target_date = datetime.now() + timedelta(days=days)
        t_year  = str(target_date.year)
        t_month = str(target_date.month).zfill(2)
        t_day   = str(target_date.day)

        log(f'🚀 예약 시작: {t_year}-{t_month}-{t_day.zfill(2)}')

        attempt = 0
        while True:
            attempt += 1
            log(f'--- 시도 #{attempt} ---')
            success = await self._try_book(page, t_year, t_month, t_day)
            if success:
                break
            log(f'슬롯 미오픈 — 재시도')
            cur = page.url
            if 'instDetail.do' in cur:
                log('달력 — 즉시 재시도')
            else:
                log('step3 → 새로고침 → 달력')
                await page.reload()
                await page.wait_for_load_state('networkidle')
                if 'instDetail.do' not in page.url:
                    log('달력 복귀 실패')
                    break

    # ────────────────────────────────────────────────
    async def _try_book(self, page, t_year, t_month, t_day):
        t_month_int = str(int(t_month))

        date_onclick = await page.evaluate(f"""
            () => {{
                var links = Array.from(document.querySelectorAll('a[onclick*="fn_selectCalDate"]'));
                for (var a of links) {{
                    var oc = a.getAttribute('onclick') || '';
                    var hasMonth = oc.includes("'{t_month}'") || oc.includes("'{t_month_int}'");
                    var hasDay   = oc.includes("'{t_day}'") || oc.includes("'{t_day.zfill(2)}'");
                    if (hasMonth && hasDay) return oc;
                }}
                return null;
            }}
        """)

        if not date_onclick:
            log(f'날짜({t_year}-{t_month}-{t_day.zfill(2)}) 못 찾음')
            return False

        log(f'날짜 클릭 ({datetime.now().strftime("%H:%M:%S.%f")[:-3]})')
        await page.evaluate(f'() => {{ {date_onclick} }}')
        await page.wait_for_load_state('networkidle')

        # "다음" 버튼 (visible)
        all_btns = page.locator('a, button')
        for i in range(await all_btns.count()):
            btn = all_btns.nth(i)
            try:
                if not await btn.is_visible(timeout=100):
                    continue
                text = (await btn.inner_text(timeout=200)).strip()
                onclick = await btn.get_attribute('onclick') or ''
                if text == '다음' and 'fn_showResveCheck' not in onclick:
                    log('다음 버튼 클릭')
                    await btn.click(timeout=5000)
                    await page.wait_for_load_state('networkidle')
                    break
            except Exception:
                continue

        # 슬롯 선택
        avail = await page.locator('li.select_o').count()
        log(f'예약가능: {avail}개')
        if avail == 0:
            return False

        preferred_courts = COURT_NUMBERS.get(self.cfg['court'], [])
        court_slot_map = await page.evaluate("""
            () => {
                var allSlots = Array.from(document.querySelectorAll('li.select_o > a'));
                var result = [];
                var currentCourt = null;
                Array.from(document.querySelectorAll('*')).forEach(el => {
                    if (el.children.length === 0) {
                        var m = el.textContent.trim().match(/테니스장\\s*(\\d+)/);
                        if (m) currentCourt = m[1];
                    }
                    if (el.tagName === 'A' && el.closest('li.select_o') && currentCourt) {
                        var idx = allSlots.indexOf(el);
                        if (idx !== -1)
                            result.push({court: currentCourt, idx: idx,
                                         title: el.title || el.textContent.trim()});
                    }
                });
                return result;
            }
        """)
        log(f'코트별 슬롯: { {d["court"]: d["title"][:20] for d in court_slot_map[:6]} }')

        slots_all = page.locator('li.select_o > a')
        slot_clicked = False

        async def click_slot(idx, label):
            nonlocal slot_clicked
            await slots_all.nth(idx).click()
            await page.wait_for_timeout(200)
            log(f'✅ 선택: {label}')
            slot_clicked = True

        ordered_times = []
        for h in self.cfg['preferred_times']:
            for t in TIME_FALLBACK.get(h, [h]):
                if t not in ordered_times:
                    ordered_times.append(t)
        for code, _ in TIME_OPTIONS:
            if code not in ordered_times:
                ordered_times.append(code)
        log(f'시간 우선순위: {ordered_times}')

        for hour in ordered_times:
            for d in court_slot_map:
                if d['court'] in preferred_courts and f' {hour}:00' in d['title']:
                    await click_slot(d['idx'], f"코트{d['court']} {d['title']}")
                    break
            if slot_clicked: break
            for d in court_slot_map:
                if f' {hour}:00' in d['title']:
                    await click_slot(d['idx'], f"코트{d['court']} {d['title']} (코트무관)")
                    break
            if slot_clicked: break

        if not slot_clicked:
            if court_slot_map:
                d = court_slot_map[0]
                await click_slot(d['idx'], f"코트{d['court']} {d['title']} (첫번째)")
            elif await slots_all.count():
                await slots_all.first.click()
                await page.wait_for_timeout(200)
                slot_clicked = True

        if not slot_clicked:
            return False

        return await self._confirm(page)

    # ────────────────────────────────────────────────
    async def _handle_captcha(self, page):
        # reCAPTCHA iframe 또는 "로봇이 아닙니다" 텍스트만 감지 (일반 인증절차 페이지 제외)
        async def detected():
            try:
                for sel in ['iframe[src*="recaptcha"]', 'iframe[src*="captcha"]']:
                    if await page.locator(sel).count(): return True
                for frame in page.frames:
                    if 'recaptcha' in (frame.url or '').lower(): return True
                body = await page.inner_text('body')
                if '로봇이 아닙니다' in body: return True
            except Exception:
                pass
            return False

        if not await detected():
            return True

        log('🚨 캡차 감지 — reCAPTCHA 체크박스 자동 클릭 시도...')

        # reCAPTCHA iframe 안의 체크박스 클릭 시도
        try:
            for frame in page.frames:
                if 'recaptcha' in (frame.url or '').lower():
                    cb = frame.locator('#recaptcha-anchor, .recaptcha-checkbox').first
                    if await cb.count():
                        await cb.click(timeout=3000)
                        log('reCAPTCHA 체크박스 클릭!')
                        await page.wait_for_timeout(2000)
                        break
        except Exception as e:
            log(f'자동 클릭 실패: {e}')

        # 체크 후 예약하기 버튼 자동 클릭 시도
        try:
            resve_btn = page.locator('a:has-text("예약하기"), button:has-text("예약하기")').first
            if await resve_btn.count() and await resve_btn.is_visible(timeout=1000):
                await resve_btn.click()
                log('예약하기 클릭!')
                await page.wait_for_load_state('networkidle')
                return True
        except Exception as e:
            log(f'예약하기 자동 클릭 실패: {e}')

        # 자동 실패 시 사용자에게 넘김
        print()
        print('=' * 50)
        print('🚨 캡차 직접 처리 필요!')
        print('   1. "로봇이 아닙니다" 체크')
        print('   2. 예약하기 버튼 클릭')
        print('=' * 50)
        print('\a\a\a', flush=True)

        cur_url = page.url
        for i in range(120):
            await asyncio.sleep(0.5)
            try:
                if page.url != cur_url:
                    log('✅ 캡차 해결 — 페이지 이동 감지')
                    await page.wait_for_load_state('networkidle')
                    return True
                if not await detected():
                    log(f'✅ 캡차 해결 ({i}초)')
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
        log('⚠️ 캡차 120초 초과')
        return False

    # ────────────────────────────────────────────────
    async def _confirm(self, page):
        log('확인 단계 시작')

        resve_check_btn = None
        for sel in ['a[onclick*="fn_showResveCheck"]', 'button[onclick*="fn_showResveCheck"]']:
            c = page.locator(sel).first
            if await c.count():
                resve_check_btn = c
                break
        if resve_check_btn:
            await self._delay()
            await resve_check_btn.click()
        else:
            try: await page.evaluate('() => fn_showResveCheck()')
            except Exception as e: log(f'fn_showResveCheck 실패: {e}')
        await page.wait_for_timeout(400)
        if not await self._handle_captcha(page): return False

        # 캡차 후 이미 완료된 경우 체크
        try:
            body = await page.inner_text('body')
            if '완료' in body or '신청완료' in body:
                log('🎉🎉🎉 캡차 후 예약 완료!')
                return True
        except Exception:
            pass

        if 'login' in page.url.lower():
            log('로그인 풀림')
            return False

        await self._delay()
        cbs = page.locator('input[type="checkbox"]')
        for i in range(await cbs.count()):
            try:
                await self._delay()
                await cbs.nth(i).check(force=True)
            except Exception: pass
        await self._delay()

        resve = None
        for sel in ['a[title*="예약신청"]', 'button[title*="예약신청"]',
                    'a:has-text("예약신청")', 'button:has-text("예약신청")']:
            c = page.locator(sel).first
            if await c.count():
                resve = c; break
        if not resve:
            log('예약신청 버튼 못 찾음'); return False
        log('🚀 예약신청 클릭!')
        await self._delay()
        await resve.click()
        await page.wait_for_timeout(400)
        if not await self._handle_captcha(page): return False

        # 캡차 후 이미 완료된 경우 체크
        try:
            body = await page.inner_text('body')
            if '완료' in body or '신청완료' in body:
                log('🎉🎉🎉 예약 완료!')
                return True
        except Exception:
            pass

        await self._delay()
        popup_done = False
        for frame in page.frames:
            try:
                btn = frame.get_by_text('동의 및 확인')
                if await btn.count():
                    cbs_f = frame.locator('input[type=checkbox]')
                    for i in range(await cbs_f.count()):
                        try:
                            await self._delay()
                            await cbs_f.nth(i).check(force=True)
                        except Exception: pass
                    await self._delay()
                    await btn.first.click(force=True)
                    log('동의 및 확인 클릭!')
                    popup_done = True
                    await page.wait_for_timeout(400)
                    break
            except Exception: pass
        if not popup_done:
            btn = page.get_by_role('button', name='동의 및 확인')
            if await btn.count():
                await self._delay()
                await btn.click(force=True)
                await page.wait_for_timeout(400)

        await self._delay()
        log(f'폼 페이지: {page.url}')

        try:
            people = page.locator('#people_count, input[name="people_count"]').first
            current = int(await people.input_value() or '1')
            plus = page.locator('button.plus, button:has-text("+"), .btn_plus').first
            if await plus.count():
                for _ in range(max(0, 4 - current)):
                    await self._delay(); await plus.click()
                minus = page.locator('button.minus, button:has-text("-"), .btn_minus').first
                for _ in range(max(0, current - 4)):
                    await self._delay(); await minus.click()
            else:
                await people.fill('4')
            log('참여인원: 4')
        except Exception as e:
            log(f'참여인원 실패: {e}')

        try:
            all_opts = await page.evaluate("""
                () => {
                    var sel = document.querySelector('select#discount_select, select[name="discount_select"]')
                           || Array.from(document.querySelectorAll('select')).find(s =>
                               Array.from(s.options).some(o => o.text.includes('할인')));
                    if (!sel) return null;
                    return {id: sel.id, name: sel.name,
                            options: Array.from(sel.options).map(o => ({value: o.value, text: o.text.trim()}))}
                }
            """)
            if all_opts:
                target_val = next((o['value'] for o in all_opts['options']
                                   if '등록단체' in o['text'] or '단체할인' in o['text']), None)
                if target_val is not None:
                    sid = all_opts.get('id') or all_opts.get('name')
                    sel_css = f'select#{sid}' if all_opts.get('id') else f'select[name="{sid}"]'
                    await self._delay()
                    await page.select_option(sel_css, value=target_val)
                    log('할인선택 완료')
                    await page.wait_for_timeout(400)
        except Exception as e:
            log(f'할인선택 실패: {e}')

        await self._delay()
        for frame in page.frames:
            try:
                for btn in await frame.locator('button, a').all():
                    txt = (await btn.inner_text(timeout=200)).strip()
                    if txt in ('예', '확인', '동의'):
                        cbs_p = frame.locator('input[type=checkbox]:not(:checked)')
                        for i in range(await cbs_p.count()):
                            try:
                                await self._delay()
                                await cbs_p.nth(i).click(force=True)
                            except Exception: pass
                        await self._delay()
                        await btn.click(force=True)
                        log('할인 동의 팝업 처리')
                        await self._delay()
                        break
            except Exception: pass

        try:
            inputs = await page.evaluate("""
                () => Array.from(document.querySelectorAll('input[type=text], textarea'))
                     .map(el => ({id: el.id, name: el.name}))
            """)
            for inp in inputs:
                fid = inp.get('id', '') or inp.get('name', '')
                sel = f'#{fid}' if inp.get('id') else f'[name="{fid}"]'
                if any(k in fid for k in ('단체', 'group', 'Group')):
                    await self._delay(); await page.fill(sel, '엠테니스')
                    log(f'단체명: {fid}')
                if any(k in fid for k in ('Resn', 'resn', 'reason', 'reson')):
                    await self._delay(); await page.fill(sel, '운동')
                    log(f'예약사유: {fid}')
        except Exception as e:
            log(f'입력 실패: {e}')

        current_url = page.url
        try:
            info_btn = page.get_by_text('시설 상세 이용안내').first
            if await info_btn.count():
                await self._delay()
                await info_btn.click()
                log('시설 상세 이용안내 클릭')
                await page.wait_for_timeout(300)
                if page.url != current_url:
                    log('페이지 이동 → 복귀')
                    await page.go_back()
                    await page.wait_for_load_state('networkidle')
        except Exception as e:
            log(f'이용안내 실패: {e}')

        await self._delay()
        clicked_final = False
        all_btns = page.locator('a, button')
        for i in range(await all_btns.count()):
            btn = all_btns.nth(i)
            txt = (await btn.inner_text()).strip()
            if txt in ('예약', '예약신청', '예약 >'):
                log(f'🎉 예약 버튼 클릭: "{txt}"')
                await self._delay()
                await btn.click()
                clicked_final = True
                await page.wait_for_timeout(500)
                break
        if not clicked_final:
            log('예약 버튼 못 찾음')

        await self._delay()
        done2 = False
        for frame in page.frames:
            try:
                for btn in await frame.locator('a, button, input[type=button]').all():
                    txt = (await btn.inner_text(timeout=200)).strip()
                    if txt in ('예', '예(Y)'):
                        await self._delay()
                        await btn.click(force=True)
                        log('최종 확인 "예" 클릭')
                        done2 = True
                        await page.wait_for_timeout(400)
                        break
                if done2: break
            except Exception: pass

        await page.wait_for_timeout(500)
        body = await page.inner_text('body')
        log(f'최종 URL: {page.url}')
        if '완료' in body or '완료' in page.url or '신청완료' in body:
            log('🎉🎉🎉 예약 완료!')
            return True
        log('결과 확인 필요')
        return False


# ────────────────────────────────────────────────────
def ask_config():
    print()
    print('=' * 50)
    print('  세종시 테니스장 자동예약 — 실제 Chrome 버전')
    print('=' * 50)
    print()

    user_id = input('아이디: ').strip()
    user_pw = getpass.getpass('비밀번호: ')

    print('\n코트 선택:')
    for i, c in enumerate(COURT_OPTIONS, 1):
        print(f'  {i}. {c}')
    c_input = input('번호 [1]: ').strip() or '1'
    court = COURT_OPTIONS[int(c_input) - 1] if c_input.isdigit() and 1 <= int(c_input) <= 4 else COURT_OPTIONS[0]

    days_input = input('\n앞당김 일수 (세종시민=20 / 일반=7) [20]: ').strip() or '20'
    days = int(days_input)

    print('\n선호 시간대 (번호, 쉼표 구분):')
    for i, (code, label) in enumerate(TIME_OPTIONS, 1):
        print(f'  {i}. {label}')
    t_input = input('번호 [1,2]: ').strip() or '1,2'
    selected_times = []
    for n in t_input.split(','):
        n = n.strip()
        if n.isdigit() and 1 <= int(n) <= len(TIME_OPTIONS):
            selected_times.append(TIME_OPTIONS[int(n) - 1][0])
    if not selected_times:
        selected_times = ['06', '08']

    h_input = input('\n실행 시각 — 시 [9]: ').strip() or '9'
    m_input = input('실행 시각 — 분 [0]: ').strip() or '0'
    slow_mo_input = input('딜레이 기준 ms [120]: ').strip() or '120'

    target_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    print(f'\n─────────────────────────────')
    print(f'코트    : {court}')
    print(f'예약 날짜: {target_date} (+{days}일)')
    print(f'선호 시간: {", ".join(selected_times)}시')
    print(f'실행 시각: {int(h_input):02d}:{int(m_input):02d}  / 딜레이: {slow_mo_input}±40ms')
    print(f'─────────────────────────────')
    ok = input('\n시작할까요? (y/n) [y]: ').strip().lower() or 'y'
    if ok != 'y':
        print('취소됨')
        sys.exit(0)

    return {
        'user_id': user_id,
        'user_pw': user_pw,
        'court': court,
        'days_ahead': days,
        'preferred_times': selected_times,
        'hour': int(h_input),
        'minute': int(m_input),
        'slow_mo': int(slow_mo_input),
    }


if __name__ == '__main__':
    cfg = ask_config()
    asyncio.run(ChromeReserver(cfg).run())
