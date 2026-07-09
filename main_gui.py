# -*- coding: utf-8 -*-
"""
시설 자동예약 — Windows GUI 버전
"""
import sys, os, threading, asyncio, random, subprocess, time, json, traceback
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ── 상수 ──────────────────────────────────────────────────────────
CHROME_PATHS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
]

CDP_PORT        = 9222
CDP_URL         = f'http://localhost:{CDP_PORT}'
TENNIS_LIST_URL = 'https://onestop.sejong.go.kr/Usr/resve/instList.do?fcltClCode=FC_TENNIS'
SETTINGS_FILE   = Path.home() / '.tennis_reserve_gui.json'

APP_VERSION  = '1.0.0'
GITHUB_REPO  = 'PhilipShinnn/tennis-reserver'

COURT_OPTIONS = ['중앙공원', '수질복원센터A', '수질복원센터B', '금남']
COURT_NUMBERS = {
    '중앙공원':     ['6', '7', '8', '9', '10'],
    '수질복원센터A': ['7', '9'],
    '수질복원센터B': [],
    '금남':         ['3'],
}
TIME_OPTIONS = [
    ('06', '06:00~08:00'), ('08', '08:00~10:00'),
    ('10', '10:00~12:00'), ('12', '12:00~14:00'),
    ('14', '14:00~16:00'), ('16', '16:00~18:00'),
    ('18', '18:00~20:00'), ('20', '20:00~22:00'),
]
TIME_FALLBACK = {
    '06': ['06','20','18','08','10','12','14','16'],
    '08': ['08','06','20','18','10','12','14','16'],
    '10': ['10','08','06','12','14','16','18','20'],
    '12': ['12','10','08','06','14','16','18','20'],
    '14': ['14','12','10','08','06','16','18','20'],
    '16': ['16','14','12','10','08','06','18','20'],
    '18': ['18','20','06','16','08','10','12','14'],
    '20': ['20','18','06','16','08','10','12','14'],
}

# ── 예약 엔진 ─────────────────────────────────────────────────────

class ChromeReserver:
    def __init__(self, cfg, log_cb, stop_event):
        self.cfg        = cfg
        self._log_cb    = log_cb
        self.stop       = stop_event

    def log(self, msg):
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        self._log_cb(f'[{ts}] {msg}')

    async def _delay(self):
        base = self.cfg.get('slow_mo', 120)
        ms   = random.randint(base - 40, base + 40)
        await asyncio.sleep(ms / 1000)

    async def run(self):
        if not PLAR�P��OK:
            self.log('❌ playwright가 설치되지 않았습니다. pip install playwright')
            return

        chrome_path = None
        for p in CHROME_PATHS:
            if Path(p).exists():
                chrome_path = p
                break
        if not chrome_path:
            self.log('❌ Chrome을 찾을 수 없습니다. Chrome을 먼저 설치해 주세요.')
            return

        if sys.platform == 'win32':
            user_data = str(Path.home() / 'AppData' / 'Local' / 'Temp' / 'chrome_tennis_debug')
        else:
            user_data = '/tmp/chrome_tennis_debug'

        cmd = [
            chrome_path,
            f'--remote-debugging-port={CDP_PORT}',
            f'--user-data-dir={user_data}',
            '--no-first-run',
            '--no-default-browser-check',
        ]
        self.log('Chrome 실행 중...')
        chrome_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(2)

        try:
            async with async_playwright() as p:
                for attempt in range(5):
                    if self.stop.is_set():
                        return
                    try:
                        browser = await p.chromium.connect_over_cdp(CDP_URL)
                        break
                    except Exception:
                        self.log(f'Chrome 연결 대기 중... ({attempt+1}/5)')
                        await asyncio.sleep(1)
                else:
                    self.log('❌ Chrome 연결 실패')
                    chrome_proc.terminate()
                    return

                context = browser.contexts[0]
                page    = context.pages[0] if context.pages else await context.new_page()
                self.log('✅ Chrome 연결 완료')

                try:
                    await self._login(page)
                    if self.stop.is_set(): return
                    await self._pre_position(page)
                    if self.stop.is_set(): return
                    await self._wait_until_scheduled(page)
                    if self.stop.is_set(): return
                    await self._fast_reserve(page)
                except Exception as e:
                    self.log(f'❌ 오류: {e}')
                    self.log(traceback.format_exc())
        finally:
            self.log('Chrome 종료')
            try:
                chrome_proc.terminate()
            except Exception:
                pass

    # ────────────────────────────────────────────────
    async def _login(self, page):
        self.log('onestop 메인 이동')
        await page.goto('https://onestop.sejong.go.kr/Usr/main/main.do')
        await page.wait_for_load_state('networkidle')
        await self._close_popup(page)

        login_link = page.locator('a[href*="login"], a[title*="로그인"]').first
        if await login_link.count():
            await login_link.click()
        else:
            await page.goto('https://www.sejong.go.kr/kor/login.do')
        await page.wait_for_load_state('networkidle')

        self.log(f'로그인 페이지: {page.url}')
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
            self.log('✅ 로그인 성공')
        else:
            self.log(f'⚠️ 로그인 상태 불명확 ({page.url})')

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

        self.log(f'사전 위치: {ty}-{tm:02d} 달력으로 이동 중...')
        await page.goto(TENNIS_LIST_URL)
        await page.wait_for_load_state('networkidle')

        keyword    = self.cfg['court']
        court_link = page.locator(f'a[title*="{keyword}"][title*="예약"]').first
        await court_link.wait_for(timeout=10000)
        await self._delay()
        await court_link.click()
        await page.wait_for_load_state('networkidle')
        self.log(f'코트 선택: {keyword}')

        now = datetime.now()
        months_ahead = (ty * 12 + tm) - (now.year * 12 + now.month)
        self.log(f'다음달 클릭 횟수: {months_ahead}')
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
                self.log('다음달 클릭')
                await page.locator('a, button, input[type=button]').nth(idx).click()
                await page.wait_for_load_state('domcontentloaded')
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(500)
            else:
                self.log('다음달 버튼 못 찾음')
                break

        offset = await self._read_server_offset(page)
        self.cfg['server_offset'] = offset
        self.log(f'✅ {ty}-{tm:02d} 달력 대기 중 (오프셋: {offset:+.0f}초)')

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
            self.log('서버 시간 파싱 실패 — 오프셋 0')
            return 0
        server_dt = datetime(result['y'], result['mo'], result['d'],
                             result['h'], result['mi'], result['s'])
        local_dt  = datetime.now().replace(microsecond=0)
        offset    = (server_dt - local_dt).total_seconds()
        self.log(f'서버: {server_dt.strftime("%H:%M:%S")}  로컬: {local_dt.strftime("%H:%M:%S")}  오프셋: {offset:+.0f}초')
        return offset

    # ────────────────────────────────────────────────
    async def _wait_until_scheduled(self, page=None):
        offset = self.cfg.get('server_offset', 0)
        now    = datetime.now()
        target_server = now.replace(hour=self.cfg['hour'], minute=self.cfg['minute'],
                                    second=0, microsecond=500000)
        target_local  = target_server - timedelta(seconds=offset)

        if now >= target_local:
            self.log('설정 시각 이미 지남 → 바로 실행')
            return
        self.log(f'⏳ 로컬 {target_local.strftime("%H:%M:%S")} 까지 대기...')

        while True:
            if self.stop.is_set():
                return
            remaining = (target_local - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            m, s = divmod(int(remaining), 60)
            # 특수 메시지 — GUI 상태 표시줄에 카운트다운 업데이트
            self._log_cb(f'COUNTDOWN:{m}분 {s}초 남음')
            if remaining <= 5 and page is not None:
                try:
                    self.log('⏱ 서버 시간 재확인...')
                    new_offset   = await self._read_server_offset(page)
                    target_local = target_server - timedelta(seconds=new_offset)
                    self.cfg['server_offset'] = new_offset
                except Exception as e:
                    self.log(f'서버 시간 재확인 실패 (무시): {e}')
                page = None
            await asyncio.sleep(min(1, remaining))

        self.log(f'⏰ {datetime.now().strftime("%H:%M:%S.%f")[:-3]} 실행 시작!')

    # ────────────────────────────────────────────────
    async def _fast_reserve(self, page):
        days        = self.cfg['days_ahead']
        target_date = datetime.now() + timedelta(days=days)
        t_year  = str(target_date.year)
        t_month = str(target_date.month).zfill(2)
        t_day   = str(target_date.day)

        self.log(f'🚀 예약 시작: {t_year}-{t_month}-{t_day.zfill(2)}')
        attempt = 0
        while True:
            if self.stop.is_set():
                return
            attempt += 1
            self.log(f'--- 시도 #{attempt} ---')
            success = await self._try_book(page, t_year, t_month, t_day)
            if success:
                break
            self.log('슬롯 미오픈 — 재시도')
            cur = page.url
            if 'instDetail.do' in cur:
                self.log('달력 — 즉시 재시도')
            else:
                self.log('step3 → 새로고침 → 달력')
                await page.reload()
                await page.wait_for_load_state('networkidle')
                if 'instDetail.do' not in page.url:
                    self.log('달력 복귀 실패')
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
            self.log(f'날짜({t_year}-{t_month}-{t_day.zfill(2)}) 못 찾음')
            return False

        self.log(f'날짜 클릭 ({datetime.now().strftime("%H:%M:%S.%f")[:-3]})')
        await page.evaluate(f'() => {{ {date_onclick} }}')
        await page.wait_for_load_state('networkidle')

        # "다음" 버튼 클릭
        all_btns = page.locator('a, button')
        for i in range(await all_btns.count()):
            btn = all_btns.nth(i)
            try:
                if not await btn.is_visible(timeout=100):
                    continue
                text   = (await btn.inner_text(timeout=200)).strip()
                onclick = await btn.get_attribute('onclick') or ''
                if text == '다음' and 'fn_showResveCheck' not in onclick:
                    self.log('다음 버튼 클릭')
                    await btn.click(timeout=5000)
                    await page.wait_for_load_state('networkidle')
                    break
            except Exception:
                continue

        avail = await page.locator('li.select_o').count()
        self.log(f'예약가능: {avail}개')
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
        self.log(f'코트별 슬롯: { {d["court"]: d["title"][:20] for d in court_slot_map[:6]} }')

        slots_all   = page.locator('li.select_o > a')
        slot_clicked = False

        async def click_slot(idx, label):
            nonlocal slot_clicked
            await slots_all.nth(idx).click()
            await page.wait_for_timeout(200)
            self.log(f'✅ 선택: {label}')
            slot_clicked = True

        ordered_times = []
        for h in self.cfg['preferred_times']:
            for t in TIME_FALLBACK.get(h, [h]):
                if t not in ordered_times:
                    ordered_times.append(t)
        for code, _ in TIME_OPTIONS:
            if code not in ordered_times:
                ordered_times.append(code)
        self.log(f'시간 우선순위: {ordered_times}')

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

        if self.cfg.get('test_mode'):
            self.log('🧪 테스트 모드 — 슬롯 선택 완료, 결제창 직전에서 중단')
            return True

        return await self._confirm(page)

    # ────────────────────────────────────────────────
    async def _handle_captcha(self, page):
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

        self.log('🚨 캡차 감지 — reCAPTCHA 체크박스 자동 클릭 시도...')

        try:
            for frame in page.frames:
                if 'recaptcha' in (frame.url or '').lower():
                    cb = frame.locator('#recaptcha-anchor, .recaptcha-checkbox').first
                    if await cb.count():
                        await cb.click(timeout=3000)
                        self.log('reCAPTCHA 체크박스 클릭!')
                        await page.wait_for_timeout(2000)
                        break
        except Exception as e:
            self.log(f'자동 클릭 실패: {e}')

        try:
            resve_btn = page.locator('a:has-text("예약하기"), button:has-text("예약하기")').first
            if await resve_btn.count() and await resve_btn.is_visible(timeout=1000):
                await resve_btn.click()
                self.log('예약하기 클릭!')
                await page.wait_for_load_state('networkidle')
                return True
        except Exception as e:
            self.log(f'예약하기 자동 클릭 실패: {e}')

        self.log('🚨 캡차 직접 처리 필요! 브라우저에서 처리해 주세요.')
        # GUI 알림
        self._log_cb('ALERT:캡차 처리 필요!\n\n브라우저에서:\n1. "로봇이 아닙니다" 체크\n2. 예약하기 버튼 클릭')

        cur_url = page.url
        for i in range(120):
            await asyncio.sleep(0.5)
            if self.stop.is_set():
                return False
            try:
                if page.url != cur_url:
                    self.log('✅ 캡차 해결 — 페이지 이동 감지')
                    await page.wait_for_load_state('networkidle')
                    return True
                if not await detected():
                    self.log('✅ 캡차 해결')
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
        self.log('⚠️ 캡차 120초 초과')
        return False

    # ────────────────────────────────────────────────
    async def _confirm(self, page):
        self.log('확인 단계 시작')

        resve_check_btn = None
        for sel in ['a[onclick*="fn_showResveCheck"]', 'button[onclick*="fn_showResveCheck"]']:
            c = page.locator(sel).first
            if await c.count():
                resve_check_btn = c; break
        if resve_check_btn:
            await self._delay()
            await resve_check_btn.click()
        else:
            try:
                await page.evaluate('() => fn_showResveCheck()')
            except Exception as e:
                self.log(f'fn_showResveCheck 실패: {e}')
        await page.wait_for_timeout(400)
        if not await self._handle_captcha(page): return False

        try:
            body = await page.inner_text('body')
            if '완료' in body or '신청완료' in body:
                self.log('🎉🎉🎉 캡차 후 예약 완료!')
                return True
        except Exception:
            pass

        if 'login' in page.url.lower():
            self.log('로그인 풀림'); return False

        await self._delay()
        cbs = page.locator('input[type="checkbox"]')
        for i in range(await cbs.count()):
            try:
                await self._delay()
                await cbs.nth(i).check(force=True)
            except Exception:
                pass
        await self._delay()

        resve = None
        for sel in ['a[title*="예약신청"]', 'button[title*="예약신청"]',
                    'a:has-text("예약신청")', 'button:has-text("예약신청")']:
            c = page.locator(sel).first
            if await c.count():
                resve = c; break
        if not resve:
            self.log('예약신청 버튼 못 찾음'); return False
        self.log('🚀 예약신청 클릭!')
        await self._delay()
        await resve.click()
        await page.wait_for_timeout(400)
        if not await self._handle_captcha(page): return False

        try:
            body = await page.inner_text('body')
            if '완료' in body or '신청완료' in body:
                self.log('🎉🎉🎉 예약 완료!'); return True
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
                        except Exception:
                            pass
                    await self._delay()
                    await btn.first.click(force=True)
                    self.log('동의 및 확인 클릭!')
                    popup_done = True
                    await page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        if not popup_done:
            btn = page.get_by_role('button', name='동의 및 확인')
            if await btn.count():
                await self._delay()
                await btn.click(force=True)
                await page.wait_for_timeout(400)

        await self._delay()
        self.log(f'폼 페이지: {page.url}')

        try:
            people  = page.locator('#people_count, input[name="people_count"]').first
            current = int(await people.input_value() or '1')
            plus    = page.locator('button.plus, button:has-text("+"), .btn_plus').first
            if await plus.count():
                for _ in range(max(0, 4 - current)):
                    await self._delay(); await plus.click()
                minus = page.locator('button.minus, button:has-text("-"), .btn_minus').first
                for _ in range(max(0, current - 4)):
                    await self._delay(); await minus.click()
            else:
                await people.fill('4')
            self.log('참여인원: 4')
        except Exception as e:
            self.log(f'참여인원 실패: {e}')

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
                    sid     = all_opts.get('id') or all_opts.get('name')
                    sel_css = f'select#{sid}' if all_opts.get('id') else f'select[name="{sid}"]'
                    await self._delay()
                    await page.select_option(sel_css, value=target_val)
                    self.log('할인선택 완료')
                    await page.wait_for_timeout(400)
        except Exception as e:
            self.log(f'할인선택 실패: {e}')

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
                            except Exception:
                                pass
                        await self._delay()
                        await btn.click(force=True)
                        self.log('할인 동의 팝업 처리')
                        await self._delay()
                        break
            except Exception:
                pass

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
                    self.log(f'단체명: {fid}')
                if any(k in fid for k in ('Resn', 'resn', 'reason', 'reson')):
                    await self._delay(); await page.fill(sel, '운동')
                    self.log(f'예약사유: {fid}')
        except Exception as e:
            self.log(f'입력 실패: {e}')

        current_url = page.url
        try:
            info_btn = page.get_by_text('시설 상세 이용안내').first
            if await info_btn.count():
                await self._delay()
                await info_btn.click()
                self.log('시설 상세 이용안내 클릭')
                await page.wait_for_timeout(300)
                if page.url != current_url:
                    self.log('페이지 이동 → 복귀')
                    await page.go_back()
                    await page.wait_for_load_state('networkidle')
        except Exception as e:
            self.log(f'이용안내 실패: {e}')

        await self._delay()
        clicked_final = False
        all_btns = page.locator('a, button')
        for i in range(await all_btns.count()):
            btn = all_btns.nth(i)
            txt = (await btn.inner_text()).strip()
            if txt in ('예약', '예약신청', '예약 >'):
                self.log(f'🎉 예약 버튼 클릭: "{txt}"')
                await self._delay()
                await btn.click()
                clicked_final = True
                await page.wait_for_timeout(500)
                break
        if not clicked_final:
            self.log('예약 버튼 못 찾음')

        await self._delay()
        done2 = False
        for frame in page.frames:
            try:
                for btn in await frame.locator('a, button, input[type=button]').all():
                    txt = (await btn.inner_text(timeout=200)).strip()
                    if txt in ('예', '예(Y)'):
                        await self._delay()
                        await btn.click(force=True)
                        self.log('최종 확인 "예" 클릭')
                        done2 = True
                        await page.wait_for_timeout(400)
                        break
                if done2: break
            except Exception:
                pass

        await page.wait_for_timeout(500)
        body = await page.inner_text('body')
        self.log(f'최종 URL: {page.url}')
        if '완료' in body or '완료' in page.url or '신청완료' in body:
            self.log('🎉🎉🎉 예약 완료!')
            return True
        self.log('결과 확인 필요')
        return False


# ── GUI 앱 ────────────────────────────────────────────────────────

class App(tk.Tk):
    # 색상 팔레트
    C_BG      = '#F0F2F5'
    C_WHITE   = '#FFFFFF'
    C_GREEN   = '#1B5E20'
    C_GREEN2  = '#2E7D32'
    C_GREEN3  = '#4CAF50'
    C_RED     = '#C62828'
    C_RED2    = '#E53935'
    C_TEXT    = '#212121'
    C_SUB     = '#757575'
    C_BORDER  = '#E0E0E0'
    C_LOG_BG  = '#0D1117'
    C_LOG_FG  = '#C9D1D9'

    def __init__(self):
        super().__init__()
        self.title(f'시설 자동예약  v{APP_VERSION}')
        self.configure(bg=self.C_BG)
        self.geometry('560x800')
        self.resizable(False, False)
        self._running    = False
        self._stop_event = threading.Event()
        self._thread     = None
        self._build_ui()
        self._load_settings()
        # 업데이트 체크 (백그라운드)
        threading.Thread(target=self._check_update, daemon=True).start()

    # ── UI 구성 ───────────────────────────────────────────────────
    def _build_ui(self):
        # ── 헤더 ──
        hdr = tk.Frame(self, bg=self.C_GREEN)
        hdr.pack(fill='x')
        tk.Label(hdr, text='  시설 자동예약',
                 bg=self.C_GREEN, fg='white',
                 font=('Malgun Gothic', 14, 'bold'),
                 pady=14, anchor='w').pack(side='left', padx=6)
        self.status_lbl = tk.Label(hdr, text='대기 중',
                                   bg=self.C_GREEN, fg='#A5D6A7',
                                   font=('Malgun Gothic', 9))
        self.status_lbl.pack(side='right', padx=14)

        # ── 로그인 ──
        login_card = self._make_card('로그인 정보', pady_top=12)
        self.id_var = tk.StringVar()
        self.pw_var = tk.StringVar()
        self._field_row(login_card, '아이디',   self.id_var)
        self._field_row(login_card, '비밀번호', self.pw_var, show='●')

        # ── 예약 설정 ──
        cfg_card = self._make_card('예약 설정')

        # 코트
        row = self._label_row(cfg_card, '코트 선택')
        self.court_var = tk.StringVar(value=COURT_OPTIONS[0])
        court_combo = ttk.Combobox(row, textvariable=self.court_var,
                                   values=COURT_OPTIONS, state='readonly', width=16)
        court_combo.pack(side='left')

        # 사전 예약일
        row2 = self._label_row(cfg_card, '사전 예약일')
        self.days_var = tk.StringVar(value='20')
        tk.Spinbox(row2, from_=1, to=90, textvariable=self.days_var,
                   width=3, font=('Malgun Gothic', 9)).pack(side='left')
        tk.Label(row2, text=' 일   (세종시민 20 / 일반 7)',
                 bg=self.C_WHITE, fg=self.C_SUB,
                 font=('Malgun Gothic', 9)).pack(side='left')

        # 시간대 체크박스
        tk.Label(cfg_card, text='선호 시간대', bg=self.C_WHITE, fg=self.C_TEXT,
                 font=('Malgun Gothic', 10), anchor='w').pack(fill='x', pady=(8, 2))
        time_grid = tk.Frame(cfg_card, bg=self.C_WHITE)
        time_grid.pack(fill='x', pady=(0, 4))
        self.time_vars = []
        for i, (code, label) in enumerate(TIME_OPTIONS):
            var = tk.BooleanVar(value=(code in ['06', '08']))
            self.time_vars.append((code, var))
            cb = tk.Checkbutton(time_grid, text=label, variable=var,
                                bg=self.C_WHITE, fg=self.C_TEXT,
                                font=('Malgun Gothic', 9), selectcolor=self.C_WHITE,
                                activebackground=self.C_WHITE,
                                highlightthickness=0, bd=0)
            cb.grid(row=i // 4, column=i % 4, sticky='w', padx=2, pady=1)

        # 실행 시각 + 딜레이
        row3 = self._label_row(cfg_card, '실행 시각')
        self.hour_var = tk.StringVar(value='9')
        self.min_var  = tk.StringVar(value='0')
        tk.Spinbox(row3, from_=0, to=23, textvariable=self.hour_var,
                   width=3, font=('Malgun Gothic', 9)).pack(side='left')
        tk.Label(row3, text='시 ', bg=self.C_WHITE, fg=self.C_SUB,
                 font=('Malgun Gothic', 9)).pack(side='left')
        tk.Spinbox(row3, from_=0, to=59, textvariable=self.min_var,
                   width=3, font=('Malgun Gothic', 9)).pack(side='left')
        tk.Label(row3, text='분    딜레이 ', bg=self.C_WHITE, fg=self.C_SUB,
                 font=('Malgun Gothic', 9)).pack(side='left')
        self.delay_var = tk.StringVar(value='120')
        tk.Spinbox(row3, from_=50, to=500, textvariable=self.delay_var,
                   width=4, font=('Malgun Gothic', 9)).pack(side='left')
        tk.Label(row3, text=' ms', bg=self.C_WHITE, fg=self.C_SUB,
                 font=('Malgun Gothic', 9)).pack(side='left')

        # 테스트 모드
        test_row = tk.Frame(cfg_card, bg=self.C_WHITE)
        test_row.pack(fill='x', pady=(8, 0))
        self.test_var = tk.BooleanVar(value=False)
        tk.Checkbutton(test_row, text='테스트 모드 (결제창 직전까지만)',
                       variable=self.test_var,
                       bg=self.C_WHITE, fg='#E65100',
                       font=('Malgun Gothic', 9, 'bold'),
                       selectcolor=self.C_WHITE,
                       activebackground=self.C_WHITE,
                       highlightthickness=0, bd=0).pack(side='left')

        # ── 버튼 ──
        btn_frame = tk.Frame(self, bg=self.C_BG)
        btn_frame.pack(fill='x', padx=16, pady=12)

        self.start_btn = tk.Button(
            btn_frame, text='실행 시작',
            bg=self.C_GREEN2, fg='white',
            font=('Malgun Gothic', 12, 'bold'),
            relief='flat', bd=0, cursor='hand2',
            padx=0, pady=10, command=self._start)
        self.start_btn.pack(side='left', fill='x', expand=True, padx=(0, 8))

        self.stop_btn = tk.Button(
            btn_frame, text='  중지  ',
            bg='#BDBDBD', fg='white',
            font=('Malgun Gothic', 12, 'bold'),
            relief='flat', bd=0, state='disabled',
            padx=12, pady=10, command=self._stop)
        self.stop_btn.pack(side='right')

        # ── 로그 ──
        log_wrap = tk.Frame(self, bg=self.C_BG)
        log_wrap.pack(fill='both', expand=True, padx=16, pady=(0, 12))

        log_hdr = tk.Frame(log_wrap, bg=self.C_BG)
        log_hdr.pack(fill='x')
        tk.Label(log_hdr, text='실행 로그', bg=self.C_BG, fg=self.C_SUB,
                 font=('Malgun Gothic', 9), anchor='w').pack(side='left')
        self.copy_btn = tk.Button(log_hdr, text='로그 복사',
                                   bg=self.C_BG, fg=self.C_SUB,
                                   font=('Malgun Gothic', 8),
                                   relief='flat', bd=0, cursor='hand2',
                                   command=self._copy_log)
        self.copy_btn.pack(side='right')

        log_container = tk.Frame(log_wrap, bg=self.C_LOG_BG,
                                 highlightbackground='#30363D', highlightthickness=1)
        log_container.pack(fill='both', expand=True)

        self.log_text = tk.Text(
            log_container,
            bg=self.C_LOG_BG, fg=self.C_LOG_FG,
            font=('Consolas', 9),
            relief='flat', bd=0, state='disabled',
            wrap='word', pady=8, padx=10,
            insertbackground=self.C_LOG_FG,
            selectbackground='#264F78')
        sb = tk.Scrollbar(log_container, bg=self.C_LOG_BG,
                          troughcolor=self.C_LOG_BG, bd=0,
                          command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log_text.pack(fill='both', expand=True)

        # 로그 색상 태그
        self.log_text.tag_configure('success',   foreground='#3FB950')
        self.log_text.tag_configure('error',     foreground='#F85149')
        self.log_text.tag_configure('warn',      foreground='#D29922')
        self.log_text.tag_configure('countdown', foreground='#BC8CFF')
        self.log_text.tag_configure('normal',    foreground=self.C_LOG_FG)
        self.log_text.tag_configure('ts',        foreground='#8B949E')

    # ── 헬퍼 위젯 ────────────────────────────────────────────────
    def _make_card(self, title, pady_top=6):
        outer = tk.Frame(self, bg=self.C_BG)
        outer.pack(fill='x', padx=16, pady=(pady_top, 0))
        card = tk.Frame(outer, bg=self.C_WHITE,
                        highlightbackground=self.C_BORDER,
                        highlightthickness=1, bd=0)
        card.pack(fill='x')
        tk.Label(card, text=title, bg=self.C_WHITE, fg=self.C_GREEN2,
                 font=('Malgun Gothic', 10, 'bold'), anchor='w',
                 padx=14, pady=8).pack(fill='x')
        sep = tk.Frame(card, bg=self.C_BORDER, height=1)
        sep.pack(fill='x')
        inner = tk.Frame(card, bg=self.C_WHITE)
        inner.pack(fill='x', padx=14, pady=10)
        return inner

    def _field_row(self, parent, label, var, show=None):
        row = tk.Frame(parent, bg=self.C_WHITE)
        row.pack(fill='x', pady=4)
        tk.Label(row, text=label, bg=self.C_WHITE, fg=self.C_TEXT,
                 font=('Malgun Gothic', 10), width=7, anchor='w').pack(side='left')
        kw = dict(textvariable=var, font=('Malgun Gothic', 10), relief='solid',
                  bd=1, highlightthickness=0)
        if show:
            kw['show'] = show
        ent = tk.Entry(row, **kw)
        ent.pack(side='left', fill='x', expand=True, ipady=4)

    def _label_row(self, parent, label):
        row = tk.Frame(parent, bg=self.C_WHITE)
        row.pack(fill='x', pady=4)
        tk.Label(row, text=label, bg=self.C_WHITE, fg=self.C_TEXT,
                 font=('Malgun Gothic', 10), width=7, anchor='w').pack(side='left')
        return row

    # ── 실행 / 중지 ───────────────────────────────────────────────
    def _start(self):
        if self._running:
            return

        uid = self.id_var.get().strip()
        pw  = self.pw_var.get().strip()
        if not uid or not pw:
            messagebox.showerror('오류', '아이디와 비밀번호를 입력해 주세요.')
            return

        times = [code for code, var in self.time_vars if var.get()]
        if not times:
            messagebox.showerror('오류', '선호 시간대를 하나 이상 선택해 주세요.')
            return

        try:
            days   = int(self.days_var.get())
            hour   = int(self.hour_var.get())
            minute = int(self.min_var.get())
            delay  = int(self.delay_var.get())
        except ValueError:
            messagebox.showerror('오류', '숫자를 올바르게 입력해 주세요.')
            return

        cfg = {
            'user_id':         uid,
            'user_pw':         pw,
            'court':           self.court_var.get(),
            'days_ahead':      days,
            'preferred_times': times,
            'hour':            hour,
            'minute':          minute,
            'slow_mo':         delay,
            'test_mode':       self.test_var.get(),
        }

        # 로그 초기화
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

        self._running = True
        self._stop_event.clear()
        self.start_btn.config(state='disabled', bg='#78909C')
        self.stop_btn.config(state='normal', bg=self.C_RED, cursor='hand2')
        self._set_status('실행 중...')
        self._save_settings()

        self._thread = threading.Thread(
            target=self._run_async, args=(cfg,), daemon=True)
        self._thread.start()

    def _stop(self):
        self._stop_event.set()
        self._append_log('--- 중지 요청 ---', 'warn')
        self._set_status('중지 요청...')

    def _run_async(self, cfg):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            reserver = ChromeReserver(cfg, self._log_from_thread, self._stop_event)
            loop.run_until_complete(reserver.run())
        except Exception as e:
            self._log_from_thread(f'[CRITICAL] {e}')
        finally:
            loop.close()
            self.after(0, self._on_done)

    # ── 로그 처리 (thread-safe) ───────────────────────────────────
    def _log_from_thread(self, msg):
        self.after(0, lambda m=msg: self._process_log(m))

    def _process_log(self, msg):
        # 카운트다운 — 상태 표시줄에만
        if msg.startswith('COUNTDOWN:'):
            self.status_lbl.config(text=msg[len('COUNTDOWN:'):])
            return
        # 알림 팝업
        if msg.startswith('ALERT:'):
            messagebox.showwarning('주의', msg[len('ALERT:'):])
            return

        # 태그 결정
        if any(k in msg for k in ('✅', '🎉', '완료', '성공')):
            tag = 'success'
        elif any(k in msg for k in ('❌', 'ERROR', 'CRITICAL', '실패')):
            tag = 'error'
        elif any(k in msg for k in ('⚠️', '캡차', '🚨', '중지')):
            tag = 'warn'
        elif any(k in msg for k in ('⏳', '⏰', '대기', '카운트')):
            tag = 'countdown'
        else:
            tag = 'normal'

        self._append_log(msg, tag)

    def _append_log(self, msg, tag='normal'):
        self.log_text.config(state='normal')
        # 타임스탬프 부분 회색 처리
        if msg.startswith('[') and ']' in msg[:14]:
            end_ts = msg.index(']') + 1
            self.log_text.insert('end', msg[:end_ts], 'ts')
            self.log_text.insert('end', msg[end_ts:] + '\n', tag)
        else:
            self.log_text.insert('end', msg + '\n', tag)
        self.log_text.see('end')
        self.log_text.config(state='disabled')

    def _set_status(self, text):
        self.status_lbl.config(text=text)

    def _on_done(self):
        self._running = False
        self.start_btn.config(state='normal', bg=self.C_GREEN2)
        self.stop_btn.config(state='disabled', bg='#BDBDBD', cursor='arrow')
        self._set_status('완료')

    def _copy_log(self):
        content = self.log_text.get('1.0', 'end').strip()
        if not content:
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.copy_btn.config(text='복사됨 ✓', fg=self.C_GREEN2)
        self.after(2000, lambda: self.copy_btn.config(text='로그 복사', fg=self.C_SUB))

    # ── 자동 업데이트 ─────────────────────────────────────────────
    def _check_update(self):
        import urllib.request, json as _json
        try:
            url = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
            req = urllib.request.Request(url, headers={'User-Agent': 'tennis-reserver'})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = _json.loads(r.read().decode())
            latest = data['tag_name'].lstrip('v')
            current = APP_VERSION
            if (tuple(int(x) for x in latest.split('.')) >
                    tuple(int(x) for x in current.split('.'))):
                for asset in data.get('assets', []):
                    if asset['name'].endswith('.exe'):
                        dl_url = asset['browser_download_url']
                        self.after(0, lambda u=dl_url, v=latest: self._prompt_update(u, v))
                        break
        except Exception:
            pass  # 네트워크 없어도 조용히 무시

    def _prompt_update(self, download_url, new_version):
        if messagebox.askyesno('업데이트',
                               f'새 버전 v{new_version}이 있습니다.\n지금 업데이트하시겠습니까?',
                               icon='info'):
            threading.Thread(target=self._do_update, args=(download_url,), daemon=True).start()

    def _do_update(self, download_url):
        import urllib.request, tempfile, subprocess as sp
        if not getattr(sys, 'frozen', False):
            self.after(0, lambda: messagebox.showinfo(
                '안내', '개발 모드에서는 자동 업데이트가 지원되지 않습니다.'))
            return
        try:
            self.after(0, lambda: self._set_status('업데이트 다운로드 중...'))
            current_exe = sys.executable
            tmp_exe = os.path.join(tempfile.gettempdir(), 'TennisReserver_update.exe')
            urllib.request.urlretrieve(download_url, tmp_exe)

            bat = os.path.join(tempfile.gettempdir(), 'tennis_update.bat')
            with open(bat, 'w', encoding='cp949') as f:
                f.write('@echo off\n'
                        'timeout /t 2 /nobreak >nul\n'
                        f'move /y "{tmp_exe}" "{current_exe}"\n'
                        f'start "" "{current_exe}"\n'
                        'del "%~f0"\n')

            sp.Popen(['cmd', '/c', bat], creationflags=sp.CREATE_NO_WINDOW)
            self.after(0, self.destroy)
            sys.exit(0)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                '업데이트 실패',
                f'수동으로 다운로드해 주세요.\n\n'
                f'https://github.com/{GITHUB_REPO}/releases\n\n{e}'))

    # ── 설정 저장/불러오기 ────────────────────────────────────────
    def _save_settings(self):
        s = {
            'user_id':         self.id_var.get(),
            'court':           self.court_var.get(),
            'days_ahead':      self.days_var.get(),
            'preferred_times': [code for code, var in self.time_vars if var.get()],
            'hour':            self.hour_var.get(),
            'minute':          self.min_var.get(),
            'slow_mo':         self.delay_var.get(),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False))
        except Exception:
            pass

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            s = json.loads(SETTINGS_FILE.read_text())
            self.id_var.set(s.get('user_id', ''))
            self.court_var.set(s.get('court', COURT_OPTIONS[0]))
            self.days_var.set(str(s.get('days_ahead', 20)))
            saved_times = s.get('preferred_times', ['06', '08'])
            for code, var in self.time_vars:
                var.set(code in saved_times)
            self.hour_var.set(str(s.get('hour', 9)))
            self.min_var.set(str(s.get('minute', 0)))
            self.delay_var.set(str(s.get('slow_mo', 120)))
        except Exception:
            pass

    def destroy(self):
        self._save_settings()
        super().destroy()


# ── 진입점 ────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = App()
    app.mainloop()
