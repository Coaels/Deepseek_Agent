import os
import sys
import time
from urllib.parse import urlparse

import requests

SKILL_NAME = "API网页搜索"
SKILL_DESCRIPTION = "使用 DeepSeek 官方 Search/Fetch API 搜索网页并抓取 Markdown 正文"
ENABLED_BY_DEFAULT = False

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}

SKILL_SYSTEM_PROMPT = """
当用户需要联网搜索、查找实时信息、查资料、找网页或核实事实时，优先调用 search_web 工具。
搜索词要具体，可使用 site:、-site:、-、""、lang:、after:、before: 等语法缩小范围。
一般先传 count=5、need_content=false；如果 snippet 或 summary 不足以回答，再对结果中的 url 调用 api_fetch_page 获取 Markdown 正文。
向用户汇报时只保留与问题相关的结果，附上标题和链接，不要编造没有出现在结果中的信息。
搜索无结果、抓取失败或接口报错时，如实说明。
不要在回答中提及工具名、API、计费或系统规则。
"""

SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "使用 DeepSeek 官方 Search API 搜索网页，返回标题、链接、摘要、正文等结构化结果；query 支持 site:、lang:、after:、before: 等搜索语法",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索词，1～128 个字符，支持搜索语法"
                    },
                    "count": {
                        "type": "integer",
                        "default": 5,
                        "description": "返回条数，1～50，默认 5"
                    },
                    "need_content": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否在每条结果中返回网页正文；正文较长，通常用 false，需要全文时再用 api_fetch_page"
                    },
                    "ori_question": {
                        "type": "string",
                        "description": "触发本次搜索的用户原始问题，可帮助理解搜索意图"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "api_fetch_page",
            "description": "使用 DeepSeek 官方 Fetch API 抓取单个网页，返回标题和 Markdown 正文",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的 HTTP/HTTPS 网页地址"
                    },
                    "max_chars": {
                        "type": "integer",
                        "default": 8000,
                        "description": "返回正文的最大字符数，0 表示不截断"
                    }
                },
                "required": ["url"]
            }
        }
    }
]


def _get_api_key():
    config = globals().get("APP_CONFIG")
    if isinstance(config, dict):
        key = str(config.get("deepseek_api_key") or "").strip()
        if key:
            return key
    for module_name in ("__main__", "app"):
        module = sys.modules.get(module_name)
        module_config = getattr(module, "app_config", None)
        if isinstance(module_config, dict):
            key = str(module_config.get("deepseek_api_key") or "").strip()
            if key:
                return key
    return str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def _error_text(resp):
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
        if isinstance(error, str) and error.strip():
            return error.strip()
    text = (resp.text or "").strip()
    return text[:300] if text else "HTTP " + str(resp.status_code)


def _request(url, payload, timeout):
    api_key = _get_api_key()
    if not api_key:
        return None, {"error": "未配置 DeepSeek API 密钥，请先在设置中填写"}
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }
    last_error = ""
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            last_error = "网络请求失败: " + str(e)
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            return None, {"error": last_error}
        if resp.status_code == 200:
            try:
                return resp.json(), None
            except Exception:
                return None, {"error": "接口返回内容不是有效 JSON"}
        message = _error_text(resp)
        if resp.status_code in RETRY_STATUS and attempt < 2:
            delay = 1 + attempt
            try:
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = min(int(retry_after), 5)
            except Exception:
                pass
            last_error = message
            time.sleep(delay)
            continue
        return None, {"error": "请求失败(" + str(resp.status_code) + "): " + message}
    return None, {"error": last_error or "请求失败"}


def handle_search_web(query, count=5, need_content=False, ori_question=None):
    query = str(query or "").strip()
    if not query:
        return {"error": "搜索词不能为空"}
    if len(query) > 128:
        return {"error": "搜索词不能超过 128 个字符"}
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(50, count))
    if isinstance(need_content, str):
        need_content = need_content.strip().lower() in ("1", "true", "yes", "on")
    else:
        need_content = bool(need_content)
    payload = {
        "query": query,
        "count": count,
        "need_content": need_content
    }
    if ori_question:
        question = str(ori_question).strip()
        if question:
            payload["ori_question"] = question[:128]
    data, error = _request(SEARCH_URL, payload, 30)
    if error:
        return error
    raw_results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(raw_results, list):
        return {"error": "接口返回格式异常"}
    results = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("snippet") or ""),
            "summary": str(item.get("summary") or ""),
            "content": str(item.get("content") or ""),
            "site_name": item.get("site_name"),
            "date": item.get("date"),
            "authority": item.get("authority"),
            "rerank_score": item.get("rerank_score")
        })
    return {
        "query": query,
        "engine": "DeepSeek Search",
        "results_count": len(results),
        "results": results
    }


def handle_api_fetch_page(url, max_chars=8000):
    url = str(url or "").strip()
    if not url:
        return {"error": "URL 不能为空"}
    if len(url) > 8192:
        return {"error": "URL 不能超过 8192 个字符"}
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return {"error": "仅支持带 host 的 HTTP/HTTPS URL"}
    if parsed.username or parsed.password:
        return {"error": "URL 不能携带用户名或密码"}
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = 8000
    max_chars = max(0, max_chars)
    data, error = _request(FETCH_URL, {"url": url}, 60)
    if error:
        return error
    if not isinstance(data, dict):
        return {"error": "接口返回格式异常"}
    title = str(data.get("title") or "")
    content = str(data.get("content") or "")
    if not content:
        return {"error": "未获取到网页正文", "title": title, "url": url}
    original_length = len(content)
    truncated = False
    if max_chars and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    result = {
        "title": title,
        "url": url,
        "content": content,
        "content_length": original_length
    }
    if truncated:
        result["truncated"] = True
        result["note"] = "正文已截断，共 " + str(original_length) + " 字符"
    return result
