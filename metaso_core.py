import json, time, uuid, sys, os, gc
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://metaso.cn"
CHAT_EP  = f"{BASE_URL}/api/search/chat"

BUCKETS = [
    {
        "name":   "h5-iphone",
        "header": ("metaso-h5", "h5"),
        "ua":     "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    },
    {
        "name":   "app-android",
        "header": ("metaso-app", "app"),
        "ua":     "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Mobile Safari/537.36",
    },
    {
        "name":   "aliapp-android",
        "header": ("metaso-aliapp", "aliapp"),
        "ua":     "Mozilla/5.0 (Linux; Android 14; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Mobile Safari/537.36",
    },
]

DEFAULT_TOKEN = "wr8+pHu3KYryzz0O2MaBSNUZbVLjLUYC1FR4sKqSW0oY+a9+FzLxNoG/EHc90mkqveCPM/FO45hW1wOhZ5oq8SDWLTEiGwtyL1JBDOZsmzxO0atH2CcxFrt0cEK3Z3KI8IiP35R836G8XBk+ksquBw=="
DEFAULT_CONV_ID = "2105719632196911105"
DEFAULT_PARENT_ID = "2105762809210978304"

def grab_token(headless: bool = True, timeout: int = 30) -> str:
    print("[*] Launching stealth headless browser to harvest fresh token...")
    captured = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--single-process',
                '--no-zygote',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--js-flags=--max-old-space-size=128',
                '--window-size=800,600',
            ]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 800, "height": 600}
        )
        ctx.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
        page = ctx.new_page()

        # Block heavy images, media, and fonts to save RAM on Render 512MB limit (allow stylesheets for React layout)
        def block_assets(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()
        page.route("**/*", block_assets)

        def on_req(req):
            if "/api/search/chat" in req.url and req.method == "POST":
                try:
                    body = json.loads(req.post_data or "{}")
                    tok = body.get("token", "")
                    if tok and "token" not in captured:
                        captured["token"] = tok
                        captured["conv_id"] = body.get("conversationId", "")
                        msgs = body.get("messages", [])
                        if msgs:
                            captured["parent_id"] = msgs[0].get("parentId", "")
                        print(f"[+] Fresh Token & Conversation Intercepted!")
                except Exception:
                    pass

        page.on("request", on_req)
        try:
            page.goto(BASE_URL, wait_until="commit", timeout=20000)
        except Exception:
            pass
        time.sleep(3)

        try:
            ta = page.locator("textarea").first
            ta.click(timeout=10000)
            time.sleep(0.5)
            ta.fill("test init query")
            time.sleep(0.5)

            # Try Enter key
            page.keyboard.press("Enter")
            time.sleep(0.5)

            # Also try button click if not yet captured
            if "token" not in captured:
                btn = page.locator("button:has(svg)").last
                if btn.count() > 0:
                    try:
                        btn.click(timeout=10000)
                    except Exception:
                        page.mouse.click(1074, 474)
                else:
                    page.mouse.click(1074, 474)
        except Exception as e:
            print(f"[!] Error interacting with page: {e}")

        for _ in range(timeout * 2):
            if "token" in captured:
                break
            time.sleep(0.5)
        browser.close()
        gc.collect()

    if "token" not in captured:
        raise RuntimeError("Failed to intercept token automatically.")
    return (
        captured["token"],
        captured.get("conv_id") or DEFAULT_CONV_ID,
        captured.get("parent_id") or DEFAULT_PARENT_ID
    )

def make_session(token: str, bucket: dict) -> requests.Session:
    s = requests.Session()
    hk, hv = bucket["header"]
    s.headers.update({
        "User-Agent":      bucket["ua"],
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          BASE_URL,
        "Referer":         BASE_URL + "/",
        "token":           token,
        hk:                hv,
    })
    return s

class MetasoCore:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.token = None
        self.sessions = []
        self.exhausted = []
        self.current = 0
        self.conv_ids = []
        self.parent_ids = []
        self._init_client()

    def _init_client(self, force_refresh: bool = False):
        token = "" if force_refresh else (os.getenv("METASO_TOKEN", "").strip() or DEFAULT_TOKEN)
        cache_path = os.path.join(os.environ.get("TEMP", "/tmp"), "metaso_token.txt")
        conv_id = DEFAULT_CONV_ID
        parent_id = DEFAULT_PARENT_ID
        
        if not token and not force_refresh and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = f.read().strip()
                    if cached:
                        token = cached
                        print("[*] Reusing cached Metaso token.")
            except Exception:
                pass

        if not token:
            token, conv_id, parent_id = grab_token(headless=self.headless)
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(token)
            except Exception:
                pass

        self.token = token
        self.sessions = [make_session(self.token, b) for b in BUCKETS]
        self.exhausted = [False] * len(BUCKETS)
        self.current = 0
        self.conv_ids = [conv_id] * len(BUCKETS)
        self.parent_ids = [parent_id] * len(BUCKETS)

    def _rotate(self):
        for i in range(len(BUCKETS)):
            nxt = (self.current + 1 + i) % len(BUCKETS)
            if not self.exhausted[nxt]:
                return nxt
        return -1

    def stream_chat(self, prompt: str):
        attempts = 0
        while attempts < len(BUCKETS) * 2:
            b = BUCKETS[self.current]
            ses = self.sessions[self.current]
            cid = self.conv_ids[self.current]
            pid = self.parent_ids[self.current]

            payload = {
                "model": "fast_thinking", "stream": True,
                "messages": [{
                    "id": f"temp-{uuid.uuid4()}", "key": f"temp-{uuid.uuid4()}",
                    "conversationId": cid, "role": "user",
                    "content": prompt, "markdownContent": prompt,
                    "engineType": "", "filter": "all", "contentType": 0,
                    "outputHtml": False, "mode": "detail",
                    "model": "fast_thinking", "outputStyle": "normal",
                    "parentId": pid,
                }],
                "engineType": "", "mode": "detail", "filter": "all",
                "outputHtml": False, "outputStyle": "normal",
                "darkMode": False, "outputLanguage": "English",
                "htmlNoDisplayEnable": True, "displayContent": prompt,
                "conversationId": cid, "parentMessageId": pid,
                b["header"][0]: b["header"][1],
                "token": self.token,
            }

            try:
                r = ses.post(
                    CHAT_EP,
                    headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
                    json=payload,
                    stream=True,
                    timeout=90
                )
            except Exception as e:
                print(f"[!] Network error: {e}")
                attempts += 1
                continue

            if r.status_code != 200:
                attempts += 1
                continue

            hit_429 = False
            new_rid = None
            
            # Generator for streaming back to FastAPI
            def event_generator():
                nonlocal hit_429, new_rid
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw: continue
                    ds = raw[5:].strip() if raw.startswith("data:") else raw.strip()
                    if ds == "[DONE]": break
                    try:
                        evt = json.loads(ds)
                    except: continue
                    
                    t = evt.get("type", "")
                    if t == "response_message_init":
                        new_rid = evt.get("data", {}).get("id")
                    elif t == "error":
                        if evt.get("code") == 429:
                            hit_429 = True
                        break
                    elif t in ("response_message", "message", "text", "chat"):
                        c = evt.get("data", {}).get("content", "")
                        if c: yield ("content", c)
                    elif "choices" in evt:
                        for ch in evt["choices"]:
                            delta = ch.get("delta", {})
                            rc = delta.get("reasoning_content", "")
                            c = delta.get("content", "")
                            if rc: yield ("reasoning", rc)
                            if c:  yield ("content", c)
            
            # We must buffer the first chunk or peek to see if it's 429
            # Since requests stream is blocking, we can yield from generator
            # But wait, if we yield, we can't easily retry from the parent loop if 429 happens mid-stream.
            # Usually 429 happens instantly. Let's process the generator.
            
            stream_started = False
            for event_type, chunk in event_generator():
                stream_started = True
                yield (event_type, chunk)

            if hit_429:
                self.exhausted[self.current] = True
                nxt = self._rotate()
                if nxt == -1:
                    print("[*] All buckets dry. Refreshing token...")
                    try:
                        self._init_client(force_refresh=True)
                        attempts = 0
                        continue
                    except:
                        yield ("error", "Rate limited and auto-refresh failed.")
                        return
                self.current = nxt
                if stream_started:
                    # Can't transparently retry if we already started sending chunks to user
                    yield ("error", "\n[System: 429 mid-stream, please retry]")
                    return
                attempts += 1
                continue

            if new_rid:
                self.parent_ids[self.current] = new_rid
            return # success, exit loop

        yield ("error", "Max retries exceeded.")
