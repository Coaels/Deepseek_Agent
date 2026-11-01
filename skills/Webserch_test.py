import re
import urllib.parse
import html
import base64
import json
import random
import time
import os
import atexit
import hashlib
import winreg
import shlex
import threading

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError as e:
    SELENIUM_AVAILABLE = False
    print(f"导入失败: {e}")
    print("请安装: pip install selenium webdriver-manager")

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
SKILL_NAME = "联网搜索-测试"
SKILL_DESCRIPTION = "使用selenium联网bing搜索-测试"
ENABLED_BY_DEFAULT = True

ENABLE_SOUGOU = False
DEBUG_TOOLS = True

SKILL_SYSTEM_PROMPT = """
搜索规则：
1. query必须一字不差包含用户原话，禁止拆分、改写、添加空格或任何词
2. 只有在你完全不知道答案时才搜索，最多搜2次，搜不到直接说"不知道"
3. 禁止连搜多次碰运气
4. 下载图片必须使用curl，不要下缩略图
5. 配合上下文理解思考
6. 如果第一次搜索不准确，直接换搜索引擎
7. 用户原话若已含空格，不要主动去掉空格
8. 中文查询使用国内版Bing，英文查询使用国际版Bing
9. 搜索默认返回5条结果，你可以根据需要通过max_results参数调整
"""

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://cn.bing.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web_cn",
            "description": "国内版必应网页搜索，中文查询",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5，最大10", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_en",
            "description": "国际版必应网页搜索，英文查询",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5，最大10", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": "使用浏览器抓取网页正文内容，支持指定读取的字符区间",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页URL"},
                    "read_begin": {"type": "integer", "description": "开始读取的字符位置（从0开始计数，包含该位置），默认从头开始"},
                    "read_end": {"type": "integer", "description": "结束读取的字符位置（不包含该位置），默认到末尾"},
                    "max_chars": {"type": "integer", "description": "如果不指定区间，则截取全文的前max_chars个字符，默认4000", "default": 4000}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_images_cn",
            "description": "国内版Bing图片搜索",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "图片搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5，最大10", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_images_en",
            "description": "国际版Bing图片搜索",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "图片搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5，最大10", "default": 5}
                },
                "required": ["query"]
            }
        }
    }
]

CACHE_DIR = os.path.join(WORKSPACE, ".web_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _cleanup_web_cache():
    try:
        import shutil
        if os.path.exists(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
    except Exception:
        pass

atexit.register(_cleanup_web_cache)

def _get_cache_path(url):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{url_hash}.json")

def _get_encoding(resp):
    content_type = resp.headers.get("Content-Type", "")
    m = re.search(r"charset=([a-zA-Z0-9._-]+)", content_type, re.IGNORECASE)
    if m:
        return m.group(1).strip(chr(39) + chr(34))
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.content[:4096], "html.parser")
        meta = soup.find("meta", charset=True)
        if meta:
            return meta["charset"]
        meta2 = soup.find("meta", attrs={"http-equiv": lambda v: v and "content-type" in v.lower()})
        if meta2 and meta2.get("content"):
            m2 = re.search(r"charset=([a-zA-Z0-9._-]+)", meta2["content"], re.IGNORECASE)
            if m2:
                return m2.group(1).strip(chr(39) + chr(34))
    except Exception:
        pass
    return "utf-8"

def _extract_query_terms(query):
    terms = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(chunk) >= 2:
            for i in range(len(chunk) - 1):
                terms.add(chunk[i:i + 2].lower())
    for m in re.findall(r"[A-Za-z0-9]{3,}", query):
        terms.add(m.lower())
    return list(terms)

def _extract_query_segments(query):
    segments = []
    for part in query.split():
        terms = set()
        for chunk in re.findall(r"[\u4e00-\u9fff]+", part):
            if len(chunk) >= 2:
                for i in range(len(chunk) - 1):
                    terms.add(chunk[i:i + 2].lower())
        for m in re.findall(r"[A-Za-z0-9]{3,}", part):
            terms.add(m.lower())
        if terms:
            segments.append(terms)
    return segments

def _results_look_relevant(query, results):
    q_compact = re.sub(r"\s+", " ", query.lower()).strip()
    use_phrase_shortcut = len(q_compact) >= 2
    for r in results:
        if "error" in r:
            continue
        blob = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        if use_phrase_shortcut and q_compact in blob:
            return True

    segments = _extract_query_segments(query)
    if segments:
        for r in results:
            if "error" in r:
                continue
            blob = (r.get("title", "") + " " + r.get("snippet", "")).lower()
            for seg_terms in segments:
                n = len(seg_terms)
                threshold = n if n <= 2 else max(2, (n + 1) // 2)
                matched = sum(1 for t in seg_terms if t in blob)
                if matched >= threshold:
                    return True
        return False

    terms = _extract_query_terms(query)
    if not terms:
        return True
    required = min(2, len(terms))
    for r in results:
        if "error" in r:
            continue
        blob = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        matched = sum(1 for t in terms if t in blob)
        if matched >= required:
            return True
    return False

def get_default_browser_windows():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as key:
            progid = winreg.QueryValueEx(key, "Progid")[0]

        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{progid}\\shell\\open\\command") as key:
                cmd = winreg.QueryValueEx(key, "")[0]
                parts = shlex.split(cmd)
                if parts:
                    exe_path = parts[0].strip('"')
                    exe_lower = exe_path.lower()
                    if "chrome" in exe_lower or "google" in exe_lower:
                        return "chrome"
                    elif "edge" in exe_lower or "msedge" in exe_lower:
                        return "edge"
                    elif "firefox" in exe_lower:
                        return "firefox"
                    else:
                        return "edge"
        except Exception:
            pass
    except Exception:
        pass

    common_paths = [
        ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "chrome"),
        ("C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe", "chrome"),
        ("C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe", "edge"),
        ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", "edge"),
        ("C:\\Program Files\\Mozilla Firefox\\firefox.exe", "firefox"),
        ("C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe", "firefox"),
    ]

    for path, browser in common_paths:
        if os.path.exists(path):
            return browser

    return "edge"

def _find_browser_exe(browser):
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if browser == "edge":
        paths = [
            os.path.join(localappdata, r"Microsoft\Edge SxS\Application\msedge.exe"),
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    elif browser == "chrome":
        paths = [
            os.path.join(localappdata, r"Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        return None
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _create_driver():
    import tempfile

    browser = get_default_browser_windows()

    user_data_dir = tempfile.mkdtemp(prefix="selenium_ud_")

    def _make_edge_options(headless=True):
        opts = EdgeOptions()
        if headless:
            opts.add_argument("--headless")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-software-rasterizer")
        opts.add_argument(f"--user-data-dir={user_data_dir}")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-plugins")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-backgrounding-occluded-windows")
        opts.add_argument("--disable-breakpad")
        opts.add_argument("--disable-client-side-phishing-detection")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-features=IsolateOrigins,site-per-process,TranslateUI")
        opts.add_argument("--disable-hang-monitor")
        opts.add_argument("--disable-ipc-flooding-protection")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-prompt-on-repost")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--force-color-profile=srgb")
        opts.add_argument("--metrics-recording-only")
        opts.add_argument("--no-first-run")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        exe = _find_browser_exe("edge")
        if exe:
            opts.binary_location = exe
        return opts

    def _make_chrome_options(headless=True):
        opts = ChromeOptions()
        if headless:
            opts.add_argument("--headless")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-software-rasterizer")
        opts.add_argument(f"--user-data-dir={user_data_dir}")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-plugins")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-backgrounding-occluded-windows")
        opts.add_argument("--disable-breakpad")
        opts.add_argument("--disable-client-side-phishing-detection")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-features=IsolateOrigins,site-per-process,TranslateUI")
        opts.add_argument("--disable-hang-monitor")
        opts.add_argument("--disable-ipc-flooding-protection")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-prompt-on-repost")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--force-color-profile=srgb")
        opts.add_argument("--metrics-recording-only")
        opts.add_argument("--no-first-run")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        exe = _find_browser_exe("chrome")
        if exe:
            opts.binary_location = exe
        return opts

    def _try_edge(headless=True):
        opts = _make_edge_options(headless=headless)
        try:
            return webdriver.Edge(options=opts)
        except Exception as e1:
            try:
                svc = EdgeService(EdgeChromiumDriverManager().install())
                return webdriver.Edge(service=svc, options=opts)
            except Exception as e2:
                try:
                    svc = EdgeService()
                    return webdriver.Edge(service=svc, options=opts)
                except Exception as e3:
                    if headless:
                        return _try_edge(headless=False)
                    raise RuntimeError(
                        f"Edge 驱动启动失败。已尝试4种方式(含非headless):\n"
                        f"  1. Selenium自动管理(headless): {e1}\n"
                        f"  2. webdriver-manager(headless): {e2}\n"
                        f"  3. 系统PATH(headless): {e3}\n"
                        f"  4. 非headless模式也失败\n"
                        f"请确保 Edge 浏览器已安装且能正常打开。"
                    )

    def _try_chrome(headless=True):
        opts = _make_chrome_options(headless=headless)
        try:
            return webdriver.Chrome(options=opts)
        except Exception as e1:
            try:
                svc = ChromeService(ChromeDriverManager().install())
                return webdriver.Chrome(service=svc, options=opts)
            except Exception as e2:
                try:
                    svc = ChromeService()
                    return webdriver.Chrome(service=svc, options=opts)
                except Exception as e3:
                    if headless:
                        return _try_chrome(headless=False)
                    raise RuntimeError(
                        f"Chrome 驱动启动失败。已尝试4种方式(含非headless):\n"
                        f"  1. Selenium自动管理(headless): {e1}\n"
                        f"  2. webdriver-manager(headless): {e2}\n"
                        f"  3. 系统PATH(headless): {e3}\n"
                        f"  4. 非headless模式也失败\n"
                        f"请确保 Chrome 浏览器已安装且能正常打开。"
                    )

    if browser == "edge":
        return _try_edge(headless=not DEBUG_TOOLS)
    elif browser == "chrome":
        return _try_chrome(headless=not DEBUG_TOOLS)
    else:
        return _try_edge(headless=not DEBUG_TOOLS)

# 浏览器常驻复用 -------------------------------------------------
_shared_driver = None
_driver_lock = threading.Lock()
_driver_initialized = False

def _get_shared_driver():
    global _shared_driver, _driver_initialized
    with _driver_lock:
        if not _driver_initialized or _shared_driver is None:
            try:
                if _shared_driver:
                    try:
                        _shared_driver.quit()
                    except:
                        pass
                _shared_driver = _create_driver()
                _driver_initialized = True
                print("[Skill] 共享浏览器已启动")
            except Exception as e:
                _driver_initialized = False
                raise RuntimeError(f"无法启动共享浏览器: {e}")
        # 健康检查
        try:
            _shared_driver.current_url
        except Exception:
            # 驱动已断开，重建
            try:
                if _shared_driver:
                    try:
                        _shared_driver.quit()
                    except:
                        pass
                _shared_driver = _create_driver()
                print("[Skill] 共享浏览器因连接丢失已重建")
            except Exception as e:
                _driver_initialized = False
                raise RuntimeError(f"共享浏览器重建失败: {e}")
        return _shared_driver

def _quit_shared_driver():
    global _shared_driver, _driver_initialized
    with _driver_lock:
        if _shared_driver:
            try:
                _shared_driver.quit()
            except:
                pass
            _shared_driver = None
        _driver_initialized = False

atexit.register(_quit_shared_driver)
# --------------------------------------------------------------


def _search_bing_once(query, max_results, use_phrase=True, international=False):
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("Selenium 未安装，请运行: pip install selenium webdriver-manager")

    if use_phrase and '"' not in query:
        phrased = f'"{query}"'
    else:
        phrased = query
    encoded_query = urllib.parse.quote(phrased, safe='')

    if international:
        domain = "https://www.bing.com"
    else:
        domain = "https://cn.bing.com"

    search_url = f"{domain}/search?q={encoded_query}&form=QBLH"

    driver = _get_shared_driver()

    try:
        driver.get(domain)
        time.sleep(random.uniform(0.2, 0.8))
        
        driver.get(search_url)
        time.sleep(random.uniform(0.8, 1.5))
        
        if driver.find_elements(By.CSS_SELECTOR, ".b_no"):
            return []
        
        results = []
        items = driver.find_elements(By.CSS_SELECTOR, "ol#b_results > li.b_algo")
        
        for item in items[:max_results]:
            try:
                a = item.find_element(By.CSS_SELECTOR, "h2 a")
                title = a.text.strip()
                href = a.get_attribute("href") or ""
                
                real_url = href
                if href.startswith("/"):
                    m = re.search(r"[?&]u=([^&]+)", href)
                    if m:
                        try:
                            real_url = base64.b64decode(m.group(1)).decode("utf-8")
                        except Exception:
                            real_url = domain + href
                    else:
                        real_url = domain + href
                
                snippet = ""
                try:
                    cap = item.find_element(By.CSS_SELECTOR, ".b_caption p")
                    snippet = cap.text.strip()
                except:
                    snippet_elems = item.find_elements(By.CSS_SELECTOR, ".b_snippet, .b_snippetText")
                    if snippet_elems:
                        snippet = snippet_elems[0].text.strip()
                
                results.append({
                    "title": title,
                    "url": real_url,
                    "snippet": snippet
                })
            except:
                continue
        
        return results
    except Exception as e:
        return [{"error": f"必应搜索失败: {str(e)}"}]
    # 不关闭 driver，保持复用

def _search_bing(query, max_results, international=False):
    results = _search_bing_once(query, max_results, use_phrase=True, international=international)
    if results and not (len(results) == 1 and "error" in results[0]):
        return results
    return _search_bing_once(query, max_results, use_phrase=False, international=international)

def _search_sogou(query, max_results):
    try:
        try:
            from curl_cffi import requests as curl_requests
            session = curl_requests.Session(impersonate="chrome")
        except ImportError:
            import requests as std_requests
            session = std_requests.Session()
            session.headers.update(SEARCH_HEADERS)

        url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}&page=1"
        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://www.sogou.com/"

        resp = session.get(url, headers=h, timeout=20)
        resp.raise_for_status()
        resp.encoding = _get_encoding(resp)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        results = []

        for item in soup.select(".vrwrap, .rb"):
            a = item.select_one("h3 a") or item.select_one(".vr-title a")
            if not a:
                continue

            title = a.get_text(strip=True)
            href = a.get("href", "")
            real_url = href

            if href.startswith("/"):
                m = re.search(r"url=([^&]+)", href)
                if m:
                    real_url = urllib.parse.unquote(m.group(1))
                else:
                    real_url = "https://www.sogou.com" + href

            snippet = ""
            st = item.select_one(".str-text") or item.select_one(".fb")
            if st:
                snippet = st.get_text(strip=True)
            else:
                for p in item.find_all(["p", "div"]):
                    t = p.get_text(strip=True)
                    if t and len(t) > 20 and t != title:
                        snippet = t
                        break

            results.append({"title": title, "url": real_url, "snippet": snippet})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        return [{"error": f"搜狗失败: {str(e)}"}]

def _search_images_bing(query, max_results, international=False):
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("Selenium 未安装")

    driver = _get_shared_driver()
    if international:
        domain = "https://www.bing.com"
    else:
        domain = "https://cn.bing.com"

    try:
        url = f"{domain}/images/search?q={urllib.parse.quote(query)}&form=QBIR&first=1"
        driver.get(url)
        time.sleep(random.uniform(1.5, 3.0))

        results = []
        seen = set()

        items = driver.find_elements(By.CSS_SELECTOR, "a.iusc")
        for a in items[:max_results * 2]:
            try:
                m_data = a.get_attribute("m")
                if not m_data:
                    continue
                data = json.loads(m_data)

                title = data.get("t", "").strip()
                desc = data.get("desc", "").strip()
                width = data.get("w", "")
                height = data.get("h", "")
                purl = data.get("purl", "").strip()

                image_url = ""
                href = a.get_attribute("href") or ""
                if href:
                    if href.startswith("/"):
                        href = domain + href
                    m = re.search(r"[?&]mediaurl=([^&]+)", href)
                    if m:
                        try:
                            image_url = urllib.parse.unquote(m.group(1))
                        except Exception:
                            pass

                if not image_url:
                    image_url = data.get("murl", "").strip()
                if not image_url:
                    image_url = data.get("turl", "").strip()

                if not image_url or not image_url.startswith("http"):
                    continue
                if image_url in seen:
                    continue
                seen.add(image_url)

                thumbnail = data.get("turl", "").strip() or image_url
                results.append({
                    "title": html.unescape(title) if title else query,
                    "image_url": image_url,
                    "thumbnail_url": thumbnail,
                    "source_url": purl,
                    "description": html.unescape(desc) if desc else "",
                    "width": width,
                    "height": height
                })

                if len(results) >= max_results:
                    break
            except:
                continue

        if not results:
            imgs = driver.find_elements(By.CSS_SELECTOR, "img.mimg")
            for img in imgs[:max_results * 2]:
                try:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if not src or src in seen or src.startswith("data:"):
                        continue
                    seen.add(src)

                    image_url = src
                    try:
                        parent_a = img.find_element(By.XPATH, "..")
                        if parent_a.tag_name == "a":
                            href = parent_a.get_attribute("href") or ""
                            if href.startswith("/"):
                                href = domain + href
                            m = re.search(r"[?&]mediaurl=([^&]+)", href)
                            if m:
                                try:
                                    image_url = urllib.parse.unquote(m.group(1))
                                except Exception:
                                    pass
                    except:
                        pass

                    results.append({
                        "title": query,
                        "image_url": image_url,
                        "thumbnail_url": src,
                        "source_url": "",
                        "description": "",
                        "width": "",
                        "height": ""
                    })

                    if len(results) >= max_results:
                        break
                except:
                    continue

        return results
    except Exception as e:
        return [{"error": f"图片搜索失败: {str(e)}"}]
    # 不关闭 driver

def handle_search_web_cn(query, max_results=5):
    bing_results = _search_bing(query, max_results, international=False)
    valid_bing = [r for r in bing_results if "error" not in r]
    if valid_bing:
        return {
            "query": query,
            "engine": "bing (国内版)",
            "results_count": len(valid_bing),
            "results": valid_bing
        }
    if ENABLE_SOUGOU:
        sogou_results = _search_sogou(query, max_results)
        valid_sogou = [r for r in sogou_results if "error" not in r]
        if valid_sogou:
            return {
                "query": query,
                "engine": "sogou",
                "results_count": len(valid_sogou),
                "results": valid_sogou
            }
    return {"query": query, "error": "搜索失败"}

def handle_search_web_en(query, max_results=5):
    bing_results = _search_bing(query, max_results, international=True)
    valid_bing = [r for r in bing_results if "error" not in r]
    if valid_bing:
        return {
            "query": query,
            "engine": "bing (国际版)",
            "results_count": len(valid_bing),
            "results": valid_bing
        }
    return {"query": query, "error": "搜索失败"}

def handle_search_images_cn(query, max_results=5):
    try:
        results = _search_images_bing(query, max_results, international=False)
        if results and "error" in results[0]:
            return {"error": results[0]["error"]}
        return {
            "query": query,
            "results_count": len(results),
            "results": results
        }
    except Exception as e:
        return {"error": f"图片搜索失败: {str(e)}"}

def handle_search_images_en(query, max_results=5):
    try:
        results = _search_images_bing(query, max_results, international=True)
        if results and "error" in results[0]:
            return {"error": results[0]["error"]}
        return {
            "query": query,
            "results_count": len(results),
            "results": results
        }
    except Exception as e:
        return {"error": f"图片搜索失败: {str(e)}"}

def handle_read_webpage(url, read_begin=None, read_end=None, max_chars=4000):
    cache_path = _get_cache_path(url)

    if os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < 3600:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                content = cached.get("content", "")
                # 应用区间或截断
                if read_begin is not None or read_end is not None:
                    begin = read_begin if read_begin is not None else 0
                    end = read_end if read_end is not None else len(content)
                    content = content[begin:end]
                    info = f" (已截取 [{begin}:{end}]，共{len(content)}字符)"
                else:
                    if len(content) > max_chars:
                        content = content[:max_chars] + f"\n... (已截断，共{len(content)}字符)"
                    else:
                        info = ""
                return {
                    "url": url,
                    "title": cached.get("title", "无标题"),
                    "content": content + info if info else content,
                    "cached": True
                }
            except Exception:
                pass

    driver = _get_shared_driver()
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        time.sleep(random.uniform(1.5, 3.0))
        page_html = driver.page_source

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_html, "lxml")

        title = soup.title.get_text(strip=True) if soup.title else "无标题"

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
            tag.decompose()

        main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            divs = soup.find_all("div")
            longest = ""
            for div in divs:
                txt = div.get_text(strip=True)
                if len(txt) > len(longest) and len(txt) > 200:
                    longest = txt
            text = longest or soup.get_text(separator="\n", strip=True)

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        text = "\n".join(lines)

        full_result = {
            "url": url,
            "title": title,
            "content": text
        }

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(full_result, f, ensure_ascii=False)
        except Exception:
            pass

        # 应用区间或截断
        if read_begin is not None or read_end is not None:
            begin = read_begin if read_begin is not None else 0
            end = read_end if read_end is not None else len(text)
            text = text[begin:end]
            info = f" (已截取 [{begin}:{end}]，共{len(text)}字符)"
        else:
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... (已截断，共{len(text)}字符)"
                info = ""
            else:
                info = ""

        return {
            "url": url,
            "title": title,
            "content": text + info if info else text,
            "cached": False
        }
    except Exception as e:
        return {"url": url, "error": f"浏览器加载失败: {str(e)}"}
    cache_path = _get_cache_path(url)

    if os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < 3600:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                content = cached.get("content", "")
                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n... (已截断，共{len(content)}字符)"
                return {
                    "url": url,
                    "title": cached.get("title", "无标题"),
                    "content": content,
                    "cached": True
                }
            except Exception:
                pass

    driver = _get_shared_driver()
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        time.sleep(random.uniform(1.5, 3.0))
        page_html = driver.page_source

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_html, "lxml")

        title = soup.title.get_text(strip=True) if soup.title else "无标题"

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
            tag.decompose()

        main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            divs = soup.find_all("div")
            longest = ""
            for div in divs:
                txt = div.get_text(strip=True)
                if len(txt) > len(longest) and len(txt) > 200:
                    longest = txt
            text = longest or soup.get_text(separator="\n", strip=True)

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        text = "\n".join(lines)

        full_result = {
            "url": url,
            "title": title,
            "content": text
        }

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(full_result, f, ensure_ascii=False)
        except Exception:
            pass

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (已截断，共{len(text)}字符)"

        return {
            "url": url,
            "title": title,
            "content": text,
            "cached": False
        }
    except Exception as e:
        return {"url": url, "error": f"浏览器加载失败: {str(e)}"}
    cache_path = _get_cache_path(url)

    if os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < 3600:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                content = cached.get("content", "")
                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n... (已截断，共{len(content)}字符)"
                return {
                    "url": url,
                    "title": cached.get("title", "无标题"),
                    "content": content,
                    "cached": True
                }
            except Exception:
                pass

    def _fetch_with_retry(fetch_func, retries=3):
        last_err = None
        for i in range(retries):
            try:
                return fetch_func()
            except Exception as e:
                last_err = e
                if i < retries - 1:
                    time.sleep(1.5 * (i + 1))
        raise last_err

    def _fetch_curl():
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome")
        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://cn.bing.com/"
        resp = session.get(url, headers=h, timeout=60)
        resp.raise_for_status()
        return resp

    def _fetch_std():
        import requests as std_requests
        session = std_requests.Session()
        session.headers.update(SEARCH_HEADERS)
        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://cn.bing.com/"
        resp = session.get(url, headers=h, timeout=60)
        resp.raise_for_status()
        return resp

    try:
        try:
            resp = _fetch_with_retry(_fetch_curl, retries=3)
        except ImportError:
            resp = _fetch_with_retry(_fetch_std, retries=3)
        except Exception:
            resp = _fetch_with_retry(_fetch_std, retries=3)

        resp.encoding = _get_encoding(resp)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
            tag.decompose()

        main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            divs = soup.find_all("div")
            longest = ""
            for div in divs:
                txt = div.get_text(strip=True)
                if len(txt) > len(longest) and len(txt) > 200:
                    longest = txt
            text = longest or soup.get_text(separator="\n", strip=True)

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        text = "\n".join(lines)

        full_result = {
            "url": url,
            "title": soup.title.get_text(strip=True) if soup.title else "无标题",
            "content": text
        }

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(full_result, f, ensure_ascii=False)
        except Exception:
            pass

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (已截断，共{len(text)}字符)"

        return {
            "url": url,
            "title": full_result["title"],
            "content": text,
            "cached": False
        }
    except Exception as e:
        return {"url": url, "error": str(e)}