# web_search.py
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

try:
    from curl_cffi import requests as curl_requests
    _session = curl_requests.Session(impersonate="chrome")
    USE_CURL = True
except ImportError:
    import requests as std_requests
    _session = std_requests.Session()
    USE_CURL = False

SKILL_NAME = "联网搜索"
SKILL_DESCRIPTION = "必应/搜狗网页搜索、Bing图片搜索、网页内容抓取"
ENABLED_BY_DEFAULT = True

SKILL_SYSTEM_PROMPT = """
搜索规则（违反任意一条即错误）：
1. query必须一字不差包含用户原话，禁止拆分、改写、添加空格或任何词
2. 只有在你完全不知道答案时才搜索，最多搜2次，搜不到直接说"不知道"
3. 禁止连搜多次碰运气
4. 下载图片必须使用curl，不要下缩略图
5. 配合上下文理解思考
6. 如果第一次搜索不准确，直接换搜索引擎
7. 用户原话若已含空格（如"Python 教程""csgo 饰品"），不要主动去掉空格；
   搜索后端会启用精确短语模式，结果即使少也是精准的，不要再改成无空格版本重搜
分析示例：
- "停雨"→先搜天气→看预报判断何时停，不是直接搜"停雨"
- "北京热吗"→搜"北京天气"
- "Python教程"→直接搜"Python教程"，不要加"入门""菜鸟"等
- "南阳 骨科"→直接搜"南阳 骨科"（带空格原样传），系统会自动精确短语匹配

警告：搜索禁止拆分、加空格、添加用户没说的词。最多2次，搜不到就说不知道。
"""

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.bing.com/",
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
            "name": "search_web",
            "description": "必应/搜狗网页搜索，返回标题、链接、摘要",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": "抓取网页正文内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页URL"},
                    "max_chars": {"type": "integer", "default": 4000}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": "Bing 图片搜索",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "图片搜索关键词"},
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    }
]


# ========== 网页缓存系统 ==========
CACHE_DIR = os.path.join(WORKSPACE, ".web_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _cleanup_web_cache():
    """脚本退出时自动清空网页缓存"""
    try:
        import shutil
        if os.path.exists(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
            print("🧹 网页缓存已清理")
    except Exception as e:
        print(f"清理缓存失败: {e}")

# 注册退出钩子
atexit.register(_cleanup_web_cache)

def _get_cache_path(url):
    """根据 URL 生成缓存文件路径"""
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
    """从查询词中提取有意义的检索项：
    - 中文：2字滑动窗口（bigram），如"卤素灯泡"→"卤素""素灯""灯泡"
    - 字母数字：3字符及以上的串（如 35W、csgo、CS2）
    bigram 比整段中文更宽容——好结果可能只含部分词，整段匹配会漏判。
    """
    terms = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(chunk) >= 2:
            for i in range(len(chunk) - 1):
                terms.add(chunk[i:i + 2].lower())
    for m in re.findall(r"[A-Za-z0-9]{3,}", query):
        terms.add(m.lower())
    return list(terms)


def _extract_query_segments(query):
    """按空格把 query 分成多个词段，每段提取检索项。
    返回 List[Set[str]]，每个 Set 是该词段的 bigram/3+字符串集合。
    用于"按词段集中命中"校验，避免分散命中蒙混过关。
    例如 "日本熊本县 7.1级地震 2026" →
      [{"日本","本熊","熊本","本县"}, {"级地","地震"}, {"2026"}]
    """
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
    """检查搜索结果是否与查询相关。
    Bing 反爬时会返回标题正确但内容随机的"假搜索页"，
    用查询词是否出现在结果标题/摘要中来判断。

    核心规则：按词段集中命中——把 query 按空格分成多个词段，
    要求至少一条结果集中命中某一个词段的多数 bigram。
    避免分散命中（"日本"来自A段+"地震"来自B段）蒙混过关。
    """
    # 整段命中捷径：把 query 整段（含空格，whitespace 归一化）作为子串直接匹配。
    # 对于"Bing 精确短语模式"返回的结果，标题/摘要里出现完整 query 是强相关信号，
    # 避免被 bigram 规则误杀。query 太短（<4 字符）时不开此捷径，防止过宽。
    q_compact = re.sub(r"\s+", " ", query.lower()).strip()
    use_phrase_shortcut = len(q_compact) >= 4
    for r in results:
        if "error" in r:
            continue
        blob = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        if use_phrase_shortcut and q_compact in blob:
            return True

    # 按词段集中命中：要求至少一条结果命中某一个词段的多数 bigram
    # 阈值规则：≤2 个 bigram 的词段要求全中（避免"地震"单字蒙混过关），
    #          >2 个 bigram 的词段要求 ceil(n/2) 但至少 2 个
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
        return False  # 所有词段都没集中命中 → 不相关

    # fallback：无法分词段时（纯单字/纯符号），默认认为相关
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


def _search_bing_once(query, max_results, use_phrase=True):
    """单次 Bing 搜索。
    use_phrase=True（默认）：把 query 用双引号包裹，强制 Bing 做精确短语匹配，
        避免把带空格的词（如 "Python 教程"）拆成多个独立关键词。
    use_phrase=False：宽匹配（旧行为），作为精确无结果时的兜底。
    返回：结果列表（可能为空）或包含 error 的列表。
    """
    import time
    import random
    import urllib.parse
    from bs4 import BeautifulSoup

    # 1. 构造真实 URL
    # 精确短语模式：用双引号包裹 query（query 本身已含引号则不再加，避免双引号嵌套）
    if use_phrase and '"' not in query:
        phrased = f'"{query}"'
    else:
        phrased = query
    encoded_query = urllib.parse.quote(phrased, safe='')
    search_url = f"https://cn.bing.com/search?q={encoded_query}&form=QBLH"

    # 2. 请求头：使用 curl_cffi 模拟时不覆盖 UA / Sec-Ch-Ua（避免与 TLS 指纹不匹配），
    #    只在回退到普通 requests 时手动设 Chrome UA。
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if not USE_CURL:
        # 未使用 curl_cffi 时手动设 Chrome UA（与 impersonate="chrome" 保持一致）
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        headers["Sec-Ch-Ua"] = '"Not/A)Brand";v="99", "Chromium";v="115"'
        headers["Sec-Ch-Ua-Mobile"] = "?0"
        headers["Sec-Ch-Ua-Platform"] = '"Windows"'

    # 3. 预热：获取首页 Cookie（确保 MUID、SRCHD 等）
    try:
        if not _session.cookies.get("MUID"):
            _session.get("https://cn.bing.com/", headers=headers, timeout=10)
            time.sleep(random.uniform(0.3, 0.8))
    except Exception:
        pass  # 预热失败不影响主请求

    # 4. 带重试的主请求
    retries = 2
    for attempt in range(retries):
        try:
            if attempt > 0:
                time.sleep(random.uniform(1.0, 3.0))  # 重试前等待
            resp = _session.get(search_url, headers=headers, timeout=20)
            resp.raise_for_status()
            resp.encoding = _get_encoding(resp)

            # 5. 解析
            soup = BeautifulSoup(resp.text, "lxml")
            results = []

            # 只在 ol#b_results 下取 li.b_algo（标准结构）；
            # 不再 fallback 到全局 .b_algo——避免在反爬假页/首页上误取无关条目
            result_container = soup.select_one("ol#b_results")
            if not result_container:
                continue
            items = result_container.select("li.b_algo")

            # Bing "无结果"页会带 .b_no，直接跳过重试
            if soup.select_one(".b_no"):
                continue

            for item in items:
                a = item.select_one("h2 a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                # 处理相对链接和重定向
                real_url = href
                if href.startswith("/"):
                    # 尝试解析重定向参数
                    m = re.search(r"[?&]u=([^&]+)", href)
                    if m:
                        try:
                            real_url = base64.b64decode(m.group(1)).decode("utf-8")
                        except Exception:
                            real_url = "https://cn.bing.com" + href
                    else:
                        real_url = "https://cn.bing.com" + href

                # 摘要提取
                snippet = ""
                cap = item.select_one(".b_caption p")
                if cap:
                    snippet = cap.get_text(strip=True)
                else:
                    # 尝试其他摘要容器
                    for p in item.find_all(["p", "div"], class_=lambda x: x and "snippet" in x):
                        snippet = p.get_text(strip=True)
                        if snippet:
                            break

                results.append({
                    "title": title,
                    "url": real_url,
                    "snippet": snippet
                })
                if len(results) >= max_results:
                    break

            if results:
                return results
            else:
                # 无结果，视为失败，继续重试
                continue

        except Exception as e:
            if attempt == retries - 1:
                return [{"error": f"必应失败: {str(e)}"}]
            continue

    return []  # 无结果：返回空列表（区别于 error），让上层决定是否走宽匹配 fallback


def _search_bing(query, max_results):
    """Bing 搜索入口：先精确短语匹配，0 结果时降级到宽匹配。

    精确短语模式解决了"Python 教程"这类带空格查询词被 Bing 拆成两个独立关键词
    （隐式 AND）后结果不准的问题；宽匹配兜底则保证稀有 query 不会完全没结果。
    """
    results = _search_bing_once(query, max_results, use_phrase=True)
    # 精确模式有结果（正常结果或网络错误）→ 直接返回；让上层决定 fallback 到搜狗
    if results:
        return results
    # 精确模式 0 结果 → 降级到宽匹配
    return _search_bing_once(query, max_results, use_phrase=False)


def _search_sogou(query, max_results):
    try:
        url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}&page=1"
        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://www.sogou.com/"
        resp = _session.get(url, headers=h, timeout=20)
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


def handle_search_web(query, max_results=5):
    """
    必应/搜狗网页搜索。
    先尝试必应，若必应返回错误或结果与查询无关（反爬假页）则回退到搜狗。
    """
    bing_results = _search_bing(query, max_results)
    valid_bing = [r for r in bing_results if "error" not in r]
    # 关键：校验 Bing 结果是否真的和查询相关。
    # Bing 反爬时会返回标题正确但内容随机的"假搜索页"，必须过滤掉。
    if valid_bing and _results_look_relevant(query, valid_bing):
        return {
            "query": query,
            "engine": "bing",
            "results_count": len(valid_bing),
            "results": valid_bing
        }

    # Bing 不可用或返回垃圾结果，回退到搜狗
    sogou_results = _search_sogou(query, max_results)
    valid_sogou = [r for r in sogou_results if "error" not in r]
    if valid_sogou:
        return {
            "query": query,
            "engine": "sogou",
            "results_count": len(valid_sogou),
            "results": valid_sogou
        }

    # 搜狗也没结果：若 Bing 至少有结果（即便不相关）也比空着强，兜底返回
    if valid_bing:
        return {
            "query": query,
            "engine": "bing",
            "results_count": len(valid_bing),
            "results": valid_bing
        }

    errors = []
    if bing_results and "error" in bing_results[0]:
        errors.append(bing_results[0]["error"])
    if sogou_results and "error" in sogou_results[0]:
        errors.append(sogou_results[0]["error"])
    return {
        "query": query,
        "error": "; ".join(errors) if errors else "搜索失败"
    }


def handle_search_images(query, max_results=5):
    try:
        # 去掉之前残留的固定参数（pq、cvid 等），用干净的 URL
        url = f"https://cn.bing.com/images/search?q={urllib.parse.quote(query)}&form=QBIR&first=1"

        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://cn.bing.com/images/search"

        resp = _session.get(url, headers=h, timeout=20)
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        results = []
        seen = set()

        # 方法1：精确提取 a.iusc（Bing 图片结果的标准容器）
        # 每个 a.iusc 的 m 属性是 JSON 元数据，href 是详情页链接（含 mediaurl）
        for a in soup.select("a.iusc"):
            m_data = a.get("m", "")
            if not m_data:
                continue

            try:
                data = json.loads(m_data)
            except (json.JSONDecodeError, ValueError):
                continue

            # 从 m 属性获取元数据
            title   = data.get("t", "").strip()
            desc    = data.get("desc", "").strip()
            width   = data.get("w", "")
            height  = data.get("h", "")
            purl    = data.get("purl", "").strip()

            # 优先从 href 提取 mediaurl（真正的高清原图）
            image_url = ""
            href = a.get("href", "")
            if href:
                if href.startswith("/"):
                    href = "https://cn.bing.com" + href
                m = re.search(r"[?&]mediaurl=([^&]+)", href)
                if m:
                    try:
                        image_url = urllib.parse.unquote(m.group(1))
                    except Exception:
                        pass

            # Fallback 1: murl（原图地址，通常与 mediaurl 相同）
            if not image_url:
                image_url = data.get("murl", "").strip()

            # Fallback 2: turl（缩略图，最后手段）
            if not image_url:
                image_url = data.get("turl", "").strip()

            # 过滤无效/垃圾数据
            if not image_url or not image_url.startswith("http"):
                continue

            # 去重
            if image_url in seen:
                continue
            seen.add(image_url)

            # 缩略图地址
            thumbnail_url = data.get("turl", "").strip() or image_url

            results.append({
                "title": html.unescape(title) if title else query,
                "image_url": image_url,
                "thumbnail_url": thumbnail_url,
                "source_url": purl,
                "description": html.unescape(desc) if desc else "",
                "width": width,
                "height": height
            })

            if len(results) >= max_results:
                break

        # 方法2：如果 iusc 方法失败，fallback 到 img.mimg（这些是缩略图，质量最低）
        if not results:
            for img in soup.select("img.mimg"):
                src = img.get("src") or img.get("data-src")
                if not src or src in seen or src.startswith("data:"):
                    continue
                seen.add(src)

                # 尝试从父级 a 标签的 href 找 mediaurl
                image_url = src
                parent_a = img.find_parent("a")
                if parent_a:
                    href = parent_a.get("href", "")
                    if href.startswith("/"):
                        href = "https://cn.bing.com" + href
                    m = re.search(r"[?&]mediaurl=([^&]+)", href)
                    if m:
                        try:
                            image_url = urllib.parse.unquote(m.group(1))
                        except Exception:
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

        return {
            "query": query,
            "results_count": len(results),
            "results": results
        }
    except Exception as e:
        return {"error": f"图片搜索失败: {str(e)}"}


def handle_read_webpage(url, max_chars=4000):
    cache_path = _get_cache_path(url)

    # 检查缓存（1小时内有效）
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
                pass  # 缓存损坏，继续抓取

    try:
        h = SEARCH_HEADERS.copy()
        h["Referer"] = "https://www.google.com/"
        resp = _session.get(url, headers=h, timeout=20)
        resp.raise_for_status()
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

        # 保存完整内容到缓存
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

        # 返回截断后的内容
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