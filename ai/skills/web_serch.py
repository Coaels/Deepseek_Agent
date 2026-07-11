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
分析示例：
- "停雨"→先搜天气→看预报判断何时停，不是直接搜"停雨"
- "北京热吗"→搜"北京天气"
- "Python教程"→直接搜"Python教程"，不要加"入门""菜鸟"等

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


def _search_bing(query, max_results):
    try:
        time.sleep(random.uniform(0.5, 10.0))
        
        encoded_query = urllib.parse.quote(query)
        # 模拟 Edge 浏览器新标签页搜索，获取更丰富的结果
        url = f"https://cn.bing.com/search?PC=U523&q={encoded_query}&adppc=EDGEINJP"
        
        # ... 请求头不变 ...

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Sec-Ch-Ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Ch-Ua-Arch": '"x86"',
            "Sec-Ch-Ua-Bitness": '"64"',
            "Sec-Ch-Ua-Full-Version": '"150.0.4078.65"',
            "Sec-Ch-Ua-Full-Version-List": '"Not;A=Brand";v="8.0.0.0", "Chromium";v="150.0.7871.115", "Microsoft Edge";v="150.0.4078.65"',
            "Sec-Ch-Ua-Model": '""',
            "Sec-Ch-Ua-Platform-Version": '"10.0.0"',
            "Sec-Ch-Prefers-Color-Scheme": "light",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Ect": "3g",
            "Priority": "u=0, i",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }

        if not _session.cookies.get("MUID"):
            _session.get("https://cn.bing.com/", headers={"User-Agent": headers["User-Agent"]}, timeout=10)
            time.sleep(0.5)

        resp = _session.get(url, headers=headers, timeout=20)

        resp.raise_for_status()
        resp.encoding = _get_encoding(resp)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            real_url = href
            if href.startswith("/"):
                m = re.search(r"[?&]u=([^&]+)", href)
                if m:
                    try:
                        real_url = base64.b64decode(m.group(1)).decode("utf-8")
                    except Exception:
                        real_url = "https://cn.bing.com" + href
                else:
                    real_url = "https://cn.bing.com" + href

            snippet = ""
            cap = li.select_one(".b_caption p")
            if cap:
                snippet = cap.get_text(strip=True)
            else:
                for p in li.find_all("p"):
                    t = p.get_text(strip=True)
                    if t and t != title:
                        snippet = t
                        break

            results.append({"title": title, "url": real_url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        return [{"error": f"必应失败: {str(e)}"}]


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
    先尝试必应，失败则回退到搜狗。
    """
    bing_results = _search_bing(query, max_results)
    valid_bing = [r for r in bing_results if "error" not in r]
    if valid_bing:
        return {
            "query": query,
            "engine": "bing",
            "results_count": len(valid_bing),
            "results": valid_bing
        }

    sogou_results = _search_sogou(query, max_results)
    valid_sogou = [r for r in sogou_results if "error" not in r]
    if valid_sogou:
        return {
            "query": query,
            "engine": "sogou",
            "results_count": len(valid_sogou),
            "results": valid_sogou
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