import os
import sys
import subprocess
import json
import time
import traceback
import threading
import ctypes
import ctypes.wintypes
import importlib.util
import inspect
import re
import base64
import hashlib
import platform
import getpass
import shutil
import tempfile
import webbrowser
import copy
import uuid
from datetime import datetime, timedelta, timezone


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.join(SCRIPT_DIR, 'workspace')
os.makedirs(WORKSPACE, exist_ok=True)
LOG_FILE = os.path.join(WORKSPACE, 'coain.log')
LOG_MAX_BYTES = 5 * 1024 * 1024


def rotate_log():
    try:
        if not os.path.exists(LOG_FILE):
            return
        if os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        backup = LOG_FILE + '.1'
        if os.path.exists(backup):
            os.remove(backup)
        os.replace(LOG_FILE, backup)
    except Exception:
        pass


rotate_log()

try:
    _LOG_STREAM = open(LOG_FILE, 'a', encoding='utf-8', buffering=1)
except Exception:
    _LOG_STREAM = open(os.devnull, 'w', encoding='utf-8')

if sys.stdout is None:
    sys.stdout = _LOG_STREAM
if sys.stderr is None:
    sys.stderr = _LOG_STREAM

PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
CORE_DEPS = [("flask", "flask"), ("flask_cors", "flask-cors"),
             ("werkzeug", "werkzeug"), ("requests", "requests")]
SHELL_DEPS = [("webview", "pywebview"), ("clr_loader", "clr-loader"), ("pythonnet", "pythonnet")]
SKILL_DEPS = [("selenium", "selenium"), ("webdriver_manager", "webdriver-manager"),
              ("PIL", "pillow"), ("pytesseract", "pytesseract"), ("pptx", "python-pptx")]
DEPS_MARKER = os.path.join(WORKSPACE, '.deps_failed')


def log_line(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[" + datetime.now().strftime("%H:%M:%S") + "] " + text + "\n")
    except Exception:
        pass


def missing_deps(pairs):
    missing = []
    for module, spec in pairs:
        try:
            found = importlib.util.find_spec(module) is not None
        except Exception:
            found = False
        if not found:
            missing.append(spec)
    return missing


def console_flags():
    if sys.stdout is not None and sys.stdout.isatty():
        return 0
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def pip_install(specs, extra=None):
    cmd = [sys.executable, "-m", "pip", "install", "--no-input",
           "--disable-pip-version-check", "--retries", "2", "-i", PIP_INDEX]
    cmd += list(extra or []) + list(specs)
    log_line("执行: " + " ".join(cmd))
    try:
        return subprocess.call(cmd, creationflags=console_flags())
    except Exception as e:
        log_line("pip 启动失败: " + str(e))
        return -1


def ensure_pip():
    try:
        if importlib.util.find_spec("pip") is not None:
            return True
    except Exception:
        pass
    log_line("未检测到 pip，尝试 ensurepip")
    try:
        subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"],
                        creationflags=console_flags())
    except Exception as e:
        log_line("ensurepip 失败: " + str(e))
    try:
        return importlib.util.find_spec("pip") is not None
    except Exception:
        return False


def read_failed():
    try:
        with open(DEPS_MARKER, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(str(item) for item in data)
    except Exception:
        pass
    return set()


def write_failed(specs):
    try:
        with open(DEPS_MARKER, "w", encoding="utf-8") as f:
            json.dump(sorted(specs), f, ensure_ascii=False)
    except Exception:
        pass


def install_group(label, group, failed_before, allow_skip):
    missing = missing_deps(group)
    if not missing:
        return set()
    targets = [spec for spec in missing if not (allow_skip and spec in failed_before)]
    skipped = [spec for spec in missing if spec not in targets]
    if skipped:
        log_line(label + "依赖上次安装失败，本次跳过: " + ", ".join(skipped))
    still = []
    if targets:
        log_line(label + "依赖缺失，开始安装: " + ", ".join(targets))
        if pip_install(targets) != 0 or missing_deps(group):
            pip_install(targets, ["--user"])
        still = [spec for spec in missing_deps(group) if spec in targets]
        if still:
            log_line(label + "依赖仍缺失: " + ", ".join(still))
        else:
            log_line(label + "依赖安装完成")
    return set(skipped) | set(still)


def ensure_dependencies():
    if not ensure_pip():
        log_line("pip 不可用，跳过依赖安装")
        return
    failed_before = read_failed()
    failed_now = set()
    failed_now |= install_group("核心", CORE_DEPS, failed_before, False)
    failed_now |= install_group("窗口", SHELL_DEPS, failed_before, True)
    failed_now |= install_group("技能", SKILL_DEPS, failed_before, True)
    write_failed(failed_now)
    still = missing_deps(CORE_DEPS)
    if still:
        msg = "核心依赖安装失败，请手动执行：\n" + sys.executable + " -m pip install -i " + PIP_INDEX + " " + " ".join(still)
        log_line(msg)
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, "Coain 依赖安装失败", 0)
        except Exception:
            pass
        sys.exit(1)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def arg_value(flag):
    if flag in sys.argv:
        index = sys.argv.index(flag)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def window_visible_titles():
    titles = []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(enum_proc(collect), 0)
    return titles


def find_edge():
    for path in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(path):
            return path
    return None


def port_in_use(port):
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/api/skills' % port, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def await_port_file(path, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                value = f.read().strip()
            if value.isdigit():
                return int(value)
        except Exception:
            pass
        time.sleep(0.2)
    return None


def shutdown_with_parent(server, parent_pid, port_file):
    try:
        parent_pid = int(parent_pid)
    except (TypeError, ValueError):
        parent_pid = 0
    log_line("服务进程 PID=%d，监视启动器 PID=%d" % (os.getpid(), parent_pid))
    handle = 0
    if parent_pid:
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, parent_pid)
        except Exception:
            handle = 0
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    else:
        log_line("无法监视启动器进程")
    log_line("启动器已退出，关闭服务")
    try:
        if port_file and os.path.exists(port_file):
            os.remove(port_file)
    except Exception:
        pass
    try:
        server.shutdown()
        server.server_close()
        log_line("监听端口已关闭")
    except Exception as e:
        log_line("关闭端口失败: " + str(e))
    os._exit(0)


EDGE_FLAGS = [
    '--no-first-run',
    '--no-default-browser-check',
    '--no-service-autorun',
    '--disable-sync',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-features=Translate,TranslateUI,msEdgeSidebar,msEdgeIdentityFeature,'
    'msEdgeSync,msEdgeWallet,msEdgeCollections,msEdgeShoppingAssistant,'
    'EdgeCollections,EdgeShoppingAssistant,msEdgeWebView2Delegation',
    '--disable-extensions',
    '--disable-breakpad',
    '--noerrdialogs',
    '--disable-session-crashed-bubble',
    '--hide-crash-restore-bubble',
    '--disable-notifications',
]


def write_edge_profile(profile):
    try:
        default_dir = os.path.join(profile, 'Default')
        os.makedirs(default_dir, exist_ok=True)
        prefs = {
            "profile": {
                "exit_type": "Normal",
                "exited_cleanly": True,
                "default_content_setting_values": {"notifications": 2},
            },
            "sync": {"requested": False, "has_setup_completed": True},
            "signin": {"allowed": False, "allowed_on_next_startup": False},
            "credentials_enable_service": False,
            "browser": {
                "has_seen_welcome_page": True,
                "check_default_browser": False,
                "show_home_button": False,
            },
            "edge": {"firstrun_completed": True},
            "distribution": {
                "import_bookmarks": False,
                "import_history": False,
                "import_search_engine": False,
                "make_chrome_default": False,
                "skip_first_run_ui": True,
            },
        }
        with open(os.path.join(default_dir, 'Preferences'), 'w', encoding='utf-8') as handle:
            json.dump(prefs, handle)
        first_run = os.path.join(profile, 'First Run')
        open(first_run, 'w', encoding='utf-8').close()
    except Exception as e:
        log_line("写入 Edge 配置文件失败: " + str(e))


def shell_window(url, port):
    edge = find_edge()
    if not edge:
        log_line("未找到 Edge，改用默认浏览器")
        webbrowser.open(url)
        threading.Event().wait()
        return

    profile = tempfile.mkdtemp(prefix='coain-')
    write_edge_profile(profile)
    subprocess.Popen(
        [edge, '--app=' + url,
         '--user-data-dir=' + profile,
         '--window-size=1120,780'] + EDGE_FLAGS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    marker = '127.0.0.1:' + str(port)
    deadline = time.time() + (10 if is_admin() else 30)
    seen = False
    missing = 0
    while True:
        time.sleep(1)
        present = any('Coain' in t or marker in t for t in window_visible_titles())
        if present:
            seen = True
            missing = 0
        elif seen:
            missing += 1
            if missing >= 2:
                break
        elif time.time() > deadline:
            log_line("窗口未出现，改用默认浏览器")
            webbrowser.open(url)
            threading.Event().wait()

    shutil.rmtree(profile, ignore_errors=True)
    log_line("窗口已关闭，退出")


def prepare_dotnet():
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    roots = [r"C:\Program Files\dotnet", r"C:\Program Files (x86)\dotnet"]
    found = None
    for root in roots:
        desktop = os.path.join(root, "shared", "Microsoft.WindowsDesktop.App")
        if os.path.isdir(desktop):
            versions = sorted(os.listdir(desktop), reverse=True)
            found = (root, versions)
            break
    if found:
        root, versions = found
        log_line(".NET 检测: 桌面运行时 %s，Python %d 位" % (",".join(versions[:2]), bits))
        if not os.environ.get("DOTNET_ROOT"):
            os.environ["DOTNET_ROOT"] = root
            log_line(".NET 检测: 已设置 DOTNET_ROOT=" + root)
        return True
    log_line(".NET 检测: 未找到 Microsoft.WindowsDesktop.App（Python %d 位）" % bits)
    for root in roots:
        core = os.path.join(root, "shared", "Microsoft.NETCore.App")
        if os.path.isdir(core):
            log_line(".NET 检测: 只找到 Microsoft.NETCore.App（" + root + "），缺少桌面运行时，"
                     "pywebview 需要 .NET Desktop Runtime")
            break
    return False


def shell_preference():
    value = str(app_config.get("shell") or "auto").strip().lower()
    return value if value in ("auto", "edge", "webview") else "auto"


def pywebview_status():
    preference = shell_preference()
    if preference == "edge":
        return False, "配置中指定使用 Edge 窗口"
    if sys.version_info >= (3, 14) and preference != "webview":
        return False, ("Python %d.%d 下 clr-loader 无法加载 hostfxr（cffi 兼容问题），"
                       "如需内嵌窗口请改用 Python 3.12 / 3.13" % sys.version_info[:2])
    try:
        import webview  # noqa: F401
    except Exception as e:
        return False, "未安装 pywebview(" + str(e) + ")"
    return True, ""


def open_shell(url, port):
    usable, reason = pywebview_status()
    if not usable:
        log_line("跳过 pywebview: " + reason + "；使用 Edge 应用窗口")
        shell_window(url, port)
        return

    prepare_dotnet()
    try:
        import webview
    except Exception as e:
        log_line("pywebview 不可用(" + str(e) + ")，回退到 Edge 应用窗口")
        shell_window(url, port)
        return

    log_line("使用 pywebview 打开窗口: " + url)
    try:
        webview.create_window('Coain', url, width=1120, height=780, min_size=(800, 560))
        webview.start(lambda: log_line("pywebview 窗口已就绪"))
        log_line("窗口已关闭，退出")
    except Exception as e:
        log_line("pywebview 启动失败(" + str(e) + ")，回退到 Edge 应用窗口")
        shell_window(url, port)


def launcher():
    port_file = os.path.join(tempfile.gettempdir(), 'coain-port-%d.txt' % os.getpid())
    log_line("启动器进程 PID=%d" % os.getpid())
    try:
        if os.path.exists(port_file):
            os.remove(port_file)
    except Exception:
        pass

    exe = sys.executable
    if exe.lower().endswith('python.exe'):
        candidate = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.isfile(candidate):
            exe = candidate
    if not os.path.isfile(exe):
        exe = sys.executable
    script_path = os.path.abspath(sys.argv[0])
    params = '"%s" --child --parent %d --port-file "%s"' % (script_path, os.getpid(), port_file)
    log_line("请求管理员权限，启动后台服务: " + exe)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if result <= 32:
        log_line("提权失败，返回码 " + str(result))
        reason = {
            5: "系统拒绝访问（提权被阻止）",
            31: "找不到目标程序",
            32: "不是有效的 Win32 程序",
            1223: "用户取消了提权（点了“否”）",
        }.get(result, "提权失败，返回码 " + str(result))
        text = reason + "\n\n如果反复出现，请手动右键 run.bat，选择“以管理员身份运行”。"
        try:
            ctypes.windll.user32.MessageBoxW(0, text, "Coain", 0)
        except Exception:
            pass
        return 1

    port = await_port_file(port_file, 180)
    if not port:
        log_line("后台服务未就绪")
        try:
            ctypes.windll.user32.MessageBoxW(0, "后台服务启动失败，请查看 workspace\\coain.log", "Coain", 0)
        except Exception:
            pass
        return 1

    url = 'http://127.0.0.1:%d/' % port
    log_line("服务已就绪: " + url)
    open_shell(url, port)
    return 0


if __name__ == '__main__' and '--child' not in sys.argv and not is_admin():
    sys.exit(launcher())


ensure_dependencies()


from flask import Flask, request, jsonify, send_from_directory, render_template, Response, stream_with_context
from flask_cors import CORS


app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
CORS(app)


PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_field": "deepseek_api_key"
    }
}

MODEL_MAP = {
    "flash": {"model": "deepseek-flash", "provider": "deepseek", "effort": "high"},
    "pro": {"model": "deepseek-v4-pro", "provider": "deepseek", "effort": "high"}
}
DEFAULT_MODEL = "flash"

PROVIDER_LABELS = {"deepseek": "DeepSeek"}
provider_thinking = {"deepseek": True}

MODEL_CONTEXT = {
    "flash": 1000000,
    "pro": 1000000
}

MODEL_PRICES = {
    "flash": {"cache_hit": 0.04, "cache_miss": 2.0, "output": 8.0},
    "pro": {"cache_hit": 0.30, "cache_miss": 9.0, "output": 27.0}
}

EFFORT_LEVELS = ("off", "low", "high", "max")
DEFAULT_EFFORT = "high"


def normalize_effort(value):
    text = str(value or "").strip().lower()
    return text if text in EFFORT_LEVELS else DEFAULT_EFFORT


def resolve_effort(data):
    if isinstance(data, dict) and "effort" in data:
        return normalize_effort(data.get("effort"))
    if isinstance(data, dict) and data.get("deep_thinking") is not None:
        return DEFAULT_EFFORT if data.get("deep_thinking") else "off"
    return DEFAULT_EFFORT


def provider_supports_thinking(name):
    return bool(provider_thinking.get(name, False))

SKILLS_DIR = os.path.join(SCRIPT_DIR, 'skills')

os.makedirs(SKILLS_DIR, exist_ok=True)

LEARN_DIR = os.path.join(SCRIPT_DIR, 'Learns')
os.makedirs(LEARN_DIR, exist_ok=True)

CHAT_DIR = os.path.join(SCRIPT_DIR, 'chat')
os.makedirs(CHAT_DIR, exist_ok=True)

session_stop = {}
session_skills = {}
session_knowledge = {}
conversations = {}
conversation_locks = {}
conversation_index = {}
SESSION_CACHE_LIMIT = 200
CONVERSATION_CACHE_LIMIT = 12
CONV_INDEX_FILE = os.path.join(CHAT_DIR, 'index.json')

CONFIG_FILE = os.path.join(SCRIPT_DIR, '.config.enc')

DEFAULT_CONFIG = {
    "deepseek_api_key": "",
    "max_messages": 100,
    "max_total_tokens": 100000,
    "max_content_len": 8000,
    "sidebar_collapsed": 0,
    "update_url": "http://192.168.0.146:1980",
    "model": "flash",
    "fast_mode": 0,
    "effort": "high",
    "enabled_skills": None,
    "enabled_knowledge": None
}

app_config = dict(DEFAULT_CONFIG)

def _machine_key_variants():
    base = platform.node() + getpass.getuser()
    candidates = [base]
    exe_dir = os.path.dirname(sys.executable)
    for name in ('python.exe', 'pythonw.exe'):
        candidates.append(base + os.path.join(exe_dir, name))
    candidates.append(base + sys.executable)
    seen = []
    for item in candidates:
        key = hashlib.sha256(item.encode()).digest()
        if key not in seen:
            seen.append(key)
    return seen


def _get_machine_key():
    return _machine_key_variants()[0]


def _xor_with(key, data_bytes):
    out = bytearray()
    for i, b in enumerate(data_bytes):
        out.append(b ^ key[i % len(key)])
    return bytes(out)


def _encrypt_data(data):
    return base64.b64encode(_xor_with(_get_machine_key(), data.encode('utf-8'))).decode()


def _decrypt_with(key, encrypted_str):
    return _xor_with(key, base64.b64decode(encrypted_str.encode())).decode('utf-8')


def load_config():
    global app_config
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            encrypted = f.read()
    except Exception as e:
        print("配置读取失败: " + str(e))
        return
    for key in _machine_key_variants():
        try:
            loaded = json.loads(_decrypt_with(key, encrypted))
        except Exception:
            continue
        if isinstance(loaded, dict):
            for k, v in loaded.items():
                if k in app_config:
                    app_config[k] = v
            return
    print("配置解密失败：所有密钥变体都不匹配")

def save_config(data):
    global app_config
    for k, v in data.items():
        if k in app_config:
            app_config[k] = v
    try:
        json_str = json.dumps(app_config, ensure_ascii=False)
        encrypted = _encrypt_data(json_str)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write(encrypted)
        return True
    except Exception as e:
        print("配置保存失败: " + str(e))
        return False

load_config()




def estimate_tokens(text):
    if not text:
        return 0
    cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - cn_chars
    return cn_chars + other_chars // 4 + 1

def get_message_tokens(msg):
    tokens = 4
    content = msg.get('content') or ''
    tokens += estimate_tokens(content)
    if msg.get('tool_calls'):
        for tc in msg['tool_calls']:
            tokens += estimate_tokens(tc.get('function', {}).get('name', ''))
            tokens += estimate_tokens(tc.get('function', {}).get('arguments', ''))
    if msg.get('tool_call_id'):
        tokens += estimate_tokens(msg.get('tool_call_id', ''))
    return tokens

def _cleanup_orphan_tools(messages):
    call_ids = set()
    reply_ids = set()
    for m in messages:
        role = m.get('role')
        if role == 'assistant':
            for tc in m.get('tool_calls') or []:
                if tc.get('id'):
                    call_ids.add(tc['id'])
        elif role == 'tool':
            reply_ids.add(m.get('tool_call_id'))

    result = []
    for m in messages:
        role = m.get('role')
        if role == 'tool':
            if m.get('tool_call_id') in call_ids:
                result.append(m)
            continue
        if role == 'assistant':
            calls = m.get('tool_calls') or []
            kept = [tc for tc in calls if tc.get('id') in reply_ids]
            if len(kept) != len(calls):
                if kept:
                    m['tool_calls'] = kept
                else:
                    m.pop('tool_calls', None)
            if not m.get('content') and not m.get('tool_calls'):
                continue
        result.append(m)
    return result


def _shrink_tool_outputs(rounds, keep_recent=1, limit=400):
    cut = max(0, len(rounds) - keep_recent)
    for round_msgs in rounds[:cut]:
        for msg in round_msgs:
            if msg.get('role') != 'tool':
                continue
            content = msg.get('content') or ''
            if len(content) > limit:
                msg['content'] = content[:limit] + "\n...（较早的工具输出已省略，原长度 " + str(len(content)) + " 字符）"


def build_compaction_note(dropped, max_chars=1200):
    entries = []
    first_user = ""
    for msg in dropped:
        role = msg.get('role')
        content = (msg.get('content') or '').strip()
        if role == 'user' and content:
            if not first_user:
                first_user = content
            entries.append("用户：" + content[:80])
        elif role == 'assistant' and content:
            entries.append("AI：" + content[:80])
    if not entries and not first_user:
        return ""
    lines = ["【早期对话摘要（系统自动压缩，用于保持上下文连续，不是新任务）】"]
    if first_user:
        lines.append("最初的任务：" + first_user[:120])
    picked = []
    used = 0
    for text in reversed(entries):
        if used + len(text) > max_chars:
            break
        picked.append(text)
        used += len(text) + 1
    if picked:
        picked.reverse()
        lines.append("此前的对话回顾：")
        lines.extend("- " + text for text in picked)
    lines.append("请延续上述上下文继续回答，不要重新自我介绍，也不要反问“需要我帮你什么”。")
    return "\n".join(lines)


def trim_history(messages, max_msgs=None, max_tokens=None, return_dropped=False):
    max_msgs = max_msgs or app_config.get("max_messages", 100)
    max_tokens = max_tokens or app_config.get("max_total_tokens", 100000)
    if len(messages) <= 3:
        return (messages, []) if return_dropped else messages

    system_msgs = [m for m in messages if m.get('role') == 'system']
    other_msgs = [m for m in messages if m.get('role') != 'system']

    total_tokens = sum(get_message_tokens(m) for m in messages)
    if len(other_msgs) <= max_msgs and total_tokens <= max_tokens:
        return (messages, []) if return_dropped else messages

    rounds = []
    current_round = []
    for msg in other_msgs:
        if msg.get('role') == 'user':
            if current_round:
                rounds.append(current_round)
            current_round = [msg]
        else:
            current_round.append(msg)
    if current_round:
        rounds.append(current_round)

    _shrink_tool_outputs(rounds)

    kept_rounds = []
    kept_tokens = sum(get_message_tokens(m) for m in system_msgs)
    kept_count = len(system_msgs)

    for round_msgs in reversed(rounds):
        round_tokens = sum(get_message_tokens(m) for m in round_msgs)
        round_count = len(round_msgs)

        if kept_count + round_count > max_msgs + len(system_msgs):
            break

        if kept_tokens + round_tokens > max_tokens:
            has_tool_related = any(
                (m.get('role') == 'assistant' and m.get('tool_calls')) or m.get('role') == 'tool'
                for m in round_msgs
            )
            if not has_tool_related:
                user_msgs = [m for m in round_msgs if m.get('role') == 'user']
                if user_msgs:
                    user_tokens = sum(get_message_tokens(m) for m in user_msgs)
                    if kept_tokens + user_tokens <= max_tokens:
                        kept_rounds.insert(0, user_msgs)
                        kept_tokens += user_tokens
                        kept_count += len(user_msgs)
            break

        kept_rounds.insert(0, round_msgs)
        kept_tokens += round_tokens
        kept_count += round_count

    kept_ids = set()
    for round_msgs in kept_rounds:
        for m in round_msgs:
            kept_ids.add(id(m))

    if rounds:
        latest_users = [m for m in rounds[-1] if m.get('role') == 'user']
        if latest_users and not any(id(m) in kept_ids for m in latest_users):
            kept_rounds.append(latest_users)

    result = list(system_msgs)
    for r in kept_rounds:
        result.extend(r)

    if len(result) < len(messages):
        result = _cleanup_orphan_tools(result)

    if not return_dropped:
        return result
    kept_ids = set(id(m) for m in result)
    dropped = [m for m in other_msgs if id(m) not in kept_ids]
    return result, dropped

def sanitize_history(messages):
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get('role') == 'assistant' and messages[i].get('tool_calls'):
            tc_ids = {tc.get('id') for tc in messages[i]['tool_calls']}
            found_ids = set()
            for j in range(i + 1, len(messages)):
                if messages[j].get('role') == 'tool':
                    found_ids.add(messages[j].get('tool_call_id'))
            if tc_ids.issubset(found_ids):
                break
            new_messages = []
            for j, msg in enumerate(messages):
                if j == i:
                    continue
                if msg.get('role') == 'tool' and msg.get('tool_call_id') in tc_ids:
                    continue
                new_messages.append(msg)
            messages[:] = new_messages
            break
    return messages


class SkillManager:
    def __init__(self, skills_dir):
        self.skills_dir = skills_dir
        self.skills = {}
        self._default_skills = None
        self.load_all()

    def load_all(self):
        self.skills = {}
        self._default_skills = None
        if not os.path.exists(self.skills_dir):
            return
        for filename in sorted(os.listdir(self.skills_dir)):
            if not filename.endswith('.py') or filename.startswith('_'):
                continue
            skill_path = os.path.join(self.skills_dir, filename)
            skill_name = filename[:-3]
            try:
                skill = self._load_skill_file(skill_name, skill_path)
                if skill:
                    self.skills[skill_name] = skill
                    print(" Skill 加载成功: " + skill_name)
            except Exception as e:
                print(" Skill 加载失败 " + skill_name + ": " + str(e))

    def _load_skill_file(self, name, path):
        spec = importlib.util.spec_from_file_location("skill_" + name, path)
        module = importlib.util.module_from_spec(spec)
        module.__dict__['WORKSPACE'] = WORKSPACE
        module.__dict__['APP_CONFIG'] = app_config
        spec.loader.exec_module(module)

        return {
            'name': name,
            'module': module,
            'display_name': getattr(module, 'SKILL_NAME', name),
            'description': getattr(module, 'SKILL_DESCRIPTION', ''),
            'system_prompt': getattr(module, 'SKILL_SYSTEM_PROMPT', '').replace('{WORKSPACE}', WORKSPACE),
            'tools': getattr(module, 'SKILL_TOOLS', []),
            'handlers': {
                attr_name[7:]: getattr(module, attr_name)
                for attr_name in dir(module)
                if attr_name.startswith("handle_")
            },
            'enabled_by_default': getattr(module, 'ENABLED_BY_DEFAULT', False),
        }

    def get_all_skills(self):
        return [
            {
                'name': name,
                'display_name': info['display_name'],
                'description': info['description'],
                'enabled_by_default': info['enabled_by_default'],
                'tools_count': len(info['tools']),
            }
            for name, info in self.skills.items()
        ]

    def get_enabled_skills(self, enabled_names):
        tools = []
        prompts = []
        handlers = {}
        for name in enabled_names:
            if name not in self.skills:
                continue
            skill = self.skills[name]
            tools.extend(skill["tools"])
            if skill["system_prompt"]:
                prompts.append(skill["system_prompt"])
            handlers.update(skill["handlers"])
        return {
            'tools': tools,
            'system_prompt': '\n\n'.join(prompts),
            'handlers': handlers,
        }

    def get_default_skills(self):
        if self._default_skills is None:
            self._default_skills = [
                n for n, i in self.skills.items()
                if i["enabled_by_default"]
            ]
        return self._default_skills

    def reload(self):
        self.load_all()
        return self.get_all_skills()


skill_manager = SkillManager(SKILLS_DIR)

if isinstance(app_config.get("enabled_skills"), list):
    session_skills["default"] = [
        name for name in app_config["enabled_skills"] if name in skill_manager.skills
    ]
if isinstance(app_config.get("enabled_knowledge"), list):
    session_knowledge["default"] = [
        str(name) for name in app_config["enabled_knowledge"]
    ]


def conversation_path(cid):
    return os.path.join(CHAT_DIR, cid + '.json')


def safe_cid(cid):
    return bool(cid) and re.fullmatch(r'[A-Za-z0-9_-]{4,60}', cid) is not None


def _trim_cache(mapping, limit):
    while len(mapping) > limit:
        try:
            mapping.pop(next(iter(mapping)))
        except Exception:
            break


def _evict_conversations(keep_cid=None):
    if len(conversations) <= CONVERSATION_CACHE_LIMIT:
        return
    ordered = sorted(conversations.items(), key=lambda kv: kv[1].get("updated_at") or "")
    for cid, _ in ordered:
        if len(conversations) <= CONVERSATION_CACHE_LIMIT:
            break
        if cid == keep_cid:
            continue
        conversations.pop(cid, None)


def conversation_lock(cid):
    lock = conversation_locks.get(cid)
    if lock is None:
        lock = threading.Lock()
        conversation_locks[cid] = lock
        _trim_cache(conversation_locks, SESSION_CACHE_LIMIT)
    return lock


def _index_entry(conv):
    return {
        "id": conv.get("id"),
        "title": conv.get("title") or "新会话",
        "updated_at": conv.get("updated_at") or "",
        "message_count": sum(1 for b in conv.get("bubbles") or [] if b.get("role") == "user"),
    }


def save_index():
    try:
        with open(CONV_INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(conversation_index.values()), f, ensure_ascii=False)
    except Exception:
        pass


def rebuild_index():
    conversation_index.clear()
    try:
        names = os.listdir(CHAT_DIR)
    except Exception:
        names = []
    for name in names:
        if not name.endswith('.json') or name == 'index.json':
            continue
        cid = name[:-5]
        if not safe_cid(cid):
            continue
        try:
            with open(os.path.join(CHAT_DIR, name), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not isinstance(data.get("messages"), list) or not isinstance(data.get("bubbles"), list):
            continue
        data.setdefault("id", cid)
        conversation_index[cid] = _index_entry(data)
    save_index()


def load_index():
    try:
        with open(CONV_INDEX_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and safe_cid(item.get("id")):
                    conversation_index[item["id"]] = item
    except Exception:
        pass
    if not conversation_index:
        rebuild_index()


def save_conversation(conv):
    if not conv or not safe_cid(conv.get("id")):
        return False
    conv["updated_at"] = datetime.now().isoformat(timespec='seconds')
    path = conversation_path(conv["id"])
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(conv, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        log_line("会话保存失败: " + str(e))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False
    conversation_index[conv["id"]] = _index_entry(conv)
    save_index()
    _evict_conversations(conv["id"])
    return True


def load_conversation(cid):
    if not safe_cid(cid):
        return None
    if cid in conversations:
        return conversations[cid]
    path = conversation_path(cid)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            conv = json.load(f)
    except Exception:
        return None
    if not isinstance(conv, dict):
        return None
    if not isinstance(conv.get("messages"), list) or not isinstance(conv.get("bubbles"), list):
        return None
    conv.setdefault("id", cid)
    conv.setdefault("title", "新会话")
    conv.setdefault("created_at", "")
    conv.setdefault("updated_at", "")
    conversations[cid] = conv
    conversation_index[cid] = _index_entry(conv)
    _evict_conversations(cid)
    return conv


def new_conversation():
    now = datetime.now()
    cid = now.strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex[:6]
    conv = {
        "id": cid,
        "title": "新会话",
        "title_manual": False,
        "model": DEFAULT_MODEL,
        "created_at": now.isoformat(timespec='seconds'),
        "updated_at": now.isoformat(timespec='seconds'),
        "messages": [],
        "bubbles": [],
        "usage": empty_usage(),
    }
    conversations[cid] = conv
    if "default" in session_skills:
        session_skills[cid] = list(session_skills["default"])
    if "default" in session_knowledge:
        session_knowledge[cid] = list(session_knowledge["default"])
    save_conversation(conv)
    return conv


def list_conversations():
    if not conversation_index:
        load_index()
    items = list(conversation_index.values())
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items


def delete_conversation(cid):
    conversations.pop(cid, None)
    conversation_index.pop(cid, None)
    save_index()
    try:
        if os.path.isfile(conversation_path(cid)):
            os.remove(conversation_path(cid))
        return True
    except Exception:
        try:
            with open(conversation_path(cid), 'w', encoding='utf-8') as f:
                f.write('{"deleted": true}')
        except Exception:
            pass
        return False


class TurnRecorder:
    def __init__(self, segments):
        self.segments = segments
        self.thinking = ""

    def _add(self, kind, content):
        if self.segments and self.segments[-1].get("type") == kind:
            self.segments[-1]["content"] = self.segments[-1].get("content", "") + content
        else:
            self.segments.append({"type": kind, "content": content})

    def feed(self, event):
        kind = event.get("type")
        content = event.get("content") or ""
        if kind == "thinking":
            self.thinking += content
            return
        if kind in ("text", "interim"):
            if self.thinking:
                self._add("thinking_block", self.thinking)
                self.thinking = ""
            self._add("text" if kind == "text" else "interim", content)

    def finish(self):
        if self.thinking:
            self._add("thinking_block", self.thinking)
            self.thinking = ""
        return self.segments


def get_session_skills(sid):
    if sid not in session_skills:
        session_skills[sid] = skill_manager.get_default_skills().copy()
    return session_skills[sid]

def get_session_knowledge(sid):
    if sid not in session_knowledge:
        session_knowledge[sid] = []
    return session_knowledge[sid]


def load_knowledge_file(filename):
    filepath = os.path.join(LEARN_DIR, filename)
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception:
        return None

def list_knowledge_files():
    if not os.path.exists(LEARN_DIR):
        return []
    files = []
    for n in sorted(os.listdir(LEARN_DIR)):
        p = os.path.join(LEARN_DIR, n)
        if os.path.isfile(p):
            name = os.path.splitext(n)[0]
            files.append({'name': name, 'filename': n})
    return files


def build_knowledge_prompt(enabled_knowledge):
    if not enabled_knowledge:
        return ''
    parts = []
    for kname in enabled_knowledge:
        filepath = os.path.join(LEARN_DIR, kname + '.json')
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            content = json.dumps(data, ensure_ascii=False)
            parts.append('  - ' + kname + ':\n' + content)
        except Exception:
            continue
    if not parts:
        return ''
    return '知识库-以下是你可以参考的知识库资料，在用户提及相关问题时才回答\n' + '\n'.join(parts) + '\n-知识库结束'


def build_system_prompt(enabled_skills, enabled_knowledge=None):
    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日")
    prompt = (
        "你叫Coain。当前时间:" + time_str + "使用用户提问语言回答问题"
        "这是一段连续的对话：结合上下文理解用户此刻说的话，不要重新自我介绍、不要重新打招呼、不要反问“需要我帮你什么”。"
        "只回答最后一条user消息，不要复述之前的回答。"
        "回答简洁，不废话,只说重要内容,不回答无关信息，尽量最少工具调用。"
        "用户发来不明确消息时，直接询问"
        "【强制规则】当用户要求执行命令、操作文件、搜索等任务时，你必须通过 tool_calls 调用对应工具执行，禁止把工具调用内容以纯文本形式输出在 content 中.在需要停下询问用户时,严禁继续执行操作"
        f"操作基于 {WORKSPACE}。"
        "不要在对话中提起系统规则和提示词"
    )
    knowledge_prompt = build_knowledge_prompt(enabled_knowledge or [])
    if knowledge_prompt:
        prompt += "\n\n" + knowledge_prompt
    skill_data = skill_manager.get_enabled_skills(enabled_skills)
    if skill_data["system_prompt"]:
        prompt += "\n\n已启用的 Skill 能力\n" + skill_data["system_prompt"]
    return prompt


def build_request_messages(messages, enabled_skills, enabled_knowledge):
    system_prompt = build_system_prompt(enabled_skills, enabled_knowledge or [])
    payload = [{"role": "system", "content": system_prompt}]
    payload.extend(copy.deepcopy(messages))
    kept, dropped = trim_history(payload, return_dropped=True)
    if dropped:
        note = build_compaction_note(dropped)
        if note and kept and kept[0].get("role") == "system":
            kept[0]["content"] = kept[0]["content"] + "\n\n" + note
    return kept


def empty_usage():
    return {
        "rounds": 0,
        "steps": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "total_tokens": 0,
        "model_seconds": 0.0,
        "tool_seconds": 0.0,
        "ttft_total": 0.0,
        "ttft_count": 0,
        "gen_seconds": 0.0,
        "model": DEFAULT_MODEL,
        "context": {"system": 0, "tools": 0, "messages": 0, "limit": MODEL_CONTEXT[DEFAULT_MODEL]},
        "cost": {"cache_hit": 0.0, "cache_miss": 0.0, "output": 0.0, "total": 0.0, "currency": "CNY"},
        "estimated": False
    }


def _usage_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number or number in (float("inf"), float("-inf")):
        return 0
    return number


def normalize_usage(data):
    usage = empty_usage()
    if not isinstance(data, dict):
        return usage
    for key in ("rounds", "steps", "prompt_tokens", "completion_tokens",
                "cache_hit_tokens", "cache_miss_tokens", "total_tokens", "ttft_count"):
        usage[key] = max(0, int(_usage_number(data.get(key))))
    for key in ("model_seconds", "tool_seconds", "ttft_total", "gen_seconds"):
        usage[key] = max(0.0, _usage_number(data.get(key)))
    model = str(data.get("model") or DEFAULT_MODEL)
    usage["model"] = model if model in MODEL_MAP else DEFAULT_MODEL
    context = data.get("context")
    if isinstance(context, dict):
        for key in ("system", "tools", "messages"):
            usage["context"][key] = max(0, int(_usage_number(context.get(key))))
        limit = int(_usage_number(context.get("limit")))
        usage["context"]["limit"] = limit if limit > 0 else MODEL_CONTEXT[usage["model"]]
    else:
        usage["context"]["limit"] = MODEL_CONTEXT[usage["model"]]
    cost = data.get("cost")
    if isinstance(cost, dict):
        for key in ("cache_hit", "cache_miss", "output", "total"):
            usage["cost"][key] = max(0.0, _usage_number(cost.get(key)))
    usage["estimated"] = bool(data.get("estimated"))
    if not usage["prompt_tokens"]:
        usage["prompt_tokens"] = usage["cache_hit_tokens"] + usage["cache_miss_tokens"]
    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage


def is_peak_pricing():
    current = datetime.now(timezone.utc) + timedelta(hours=8)
    if current.weekday() >= 5:
        return False
    minutes = current.hour * 60 + current.minute
    return (9 * 60 <= minutes < 12 * 60) or (14 * 60 <= minutes < 18 * 60)


def compute_cost(model_alias, cache_hit, cache_miss, output, peak=None):
    if peak is None:
        peak = is_peak_pricing()
    factor = 1.0 if peak else 0.5
    prices = MODEL_PRICES.get(model_alias, MODEL_PRICES[DEFAULT_MODEL])
    hit_cost = float(cache_hit) / 1000000 * prices["cache_hit"] * factor
    miss_cost = float(cache_miss) / 1000000 * prices["cache_miss"] * factor
    output_cost = float(output) / 1000000 * prices["output"] * factor
    return {
        "cache_hit": hit_cost,
        "cache_miss": miss_cost,
        "output": output_cost,
        "total": hit_cost + miss_cost + output_cost,
        "currency": "CNY"
    }


def estimate_conversation_usage(conv, enabled_skills=None, enabled_knowledge=None):
    usage = empty_usage()
    messages = conv.get("messages") if isinstance(conv, dict) else None
    if not isinstance(messages, list):
        messages = []
    model = conv.get("model") if isinstance(conv, dict) else None
    if model not in MODEL_MAP:
        model = DEFAULT_MODEL
    usage["model"] = model
    skills = enabled_skills if enabled_skills is not None else skill_manager.get_default_skills()
    system_tokens = estimate_tokens(build_system_prompt(skills, enabled_knowledge or []))
    tools = skill_manager.get_enabled_skills(skills)["tools"]
    tool_tokens = estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0
    history = []
    prompt_tokens = 0
    output_tokens = 0
    steps = 0
    rounds = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            rounds += 1
        if role == "assistant":
            steps += 1
            call_prompt = system_tokens + tool_tokens + sum(get_message_tokens(item) for item in history)
            call_output = estimate_tokens(msg.get("content") or "") + estimate_tokens(msg.get("reasoning_content") or "")
            for tc in msg.get("tool_calls") or []:
                call_output += estimate_tokens(tc.get("function", {}).get("name", ""))
                call_output += estimate_tokens(tc.get("function", {}).get("arguments", ""))
            prompt_tokens += call_prompt
            output_tokens += call_output
            call_cost = compute_cost(model, 0, call_prompt, call_output)
            usage["cost"]["cache_hit"] += call_cost["cache_hit"]
            usage["cost"]["cache_miss"] += call_cost["cache_miss"]
            usage["cost"]["output"] += call_cost["output"]
            usage["cost"]["total"] += call_cost["total"]
        history.append(msg)
    usage["rounds"] = rounds
    usage["steps"] = steps
    usage["cache_hit_tokens"] = 0
    usage["cache_miss_tokens"] = prompt_tokens
    usage["prompt_tokens"] = prompt_tokens
    usage["completion_tokens"] = output_tokens
    usage["total_tokens"] = prompt_tokens + output_tokens
    usage["context"]["system"] = system_tokens
    usage["context"]["tools"] = tool_tokens
    usage["context"]["messages"] = sum(get_message_tokens(msg) for msg in messages if isinstance(msg, dict))
    usage["context"]["limit"] = MODEL_CONTEXT.get(model, MODEL_CONTEXT[DEFAULT_MODEL])
    usage["estimated"] = bool(messages)
    return usage


def conversation_usage(conv):
    if not isinstance(conv, dict):
        return empty_usage()
    stored = conv.get("usage")
    if isinstance(stored, dict) and (stored.get("steps") or stored.get("prompt_tokens") or stored.get("completion_tokens")):
        return normalize_usage(stored)
    return estimate_conversation_usage(conv)


def truncate_text(text, max_len=None):
    max_len = max_len or app_config.get("max_content_len", 8000)
    if len(text) > max_len:
        return text[:max_len] + "\n... (内容已截断，共" + str(len(text)) + "字符)"
    return text


def handle_tool(tool_name, args, handlers):
    handler = handlers.get(tool_name)
    if handler is None:
        return {"error": f"未知工具: {tool_name}"}
    try:
        inspect.signature(handler).bind(**args)
    except TypeError as e:
        return {"error": f"工具参数错误: {str(e)}"}
    try:
        result = handler(**args)
        return result if isinstance(result, dict) else {"output": str(result)}
    except Exception as e:
        return {"error": f"Skill 工具执行失败: {str(e)}"}


import requests as std_requests

def api_request_with_retry(messages, tools=None, max_retries=2, effort="high", model_alias=DEFAULT_MODEL):
    model_info = MODEL_MAP.get(model_alias, MODEL_MAP[DEFAULT_MODEL])
    provider_name = model_info["provider"]
    provider = PROVIDERS[provider_name]
    api_key = app_config.get(provider["api_key_field"], "")
    if not api_key:
        raise Exception(provider_name + " API 密钥未配置，请在设置中配置")
    url = provider["base_url"] + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    model_name = model_info["model"]

    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True
    }
    if provider_supports_thinking(provider_name):
        if effort == "off":
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = effort
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    label = PROVIDER_LABELS.get(provider_name, provider_name)
    rate_limit_left = 3
    server_error_left = max_retries
    network_left = max_retries
    thinking_fallback = False

    while True:
        try:
            resp = std_requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
            resp.raise_for_status()
            return resp
        except std_requests.exceptions.HTTPError as e:
            status = e.response.status_code
            try:
                body = (e.response.text or "")[:300]
            except Exception:
                body = ""

            if (status == 400 and "thinking" in payload and not thinking_fallback
                    and payload["thinking"].get("type") != "disabled"):
                thinking_fallback = True
                payload["thinking"] = {"type": "disabled"}
                payload.pop("reasoning_effort", None)
                log_line(label + " 请求返回 400，本次已关闭思考参数并重试")
                continue

            if status == 429:
                if rate_limit_left <= 0:
                    raise Exception(
                        label + " 接口限流(429)，已重试 3 次仍失败。"
                        "请稍等几十秒再发，或检查账号的并发/QPS 额度"
                    )
                delay = 0
                try:
                    retry_after = e.response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = int(retry_after)
                except Exception:
                    delay = 0
                if delay <= 0:
                    delay = min(3 * (4 - rate_limit_left), 12)
                rate_limit_left -= 1
                log_line("%s 限流(429)，%d 秒后重试" % (label, delay))
                time.sleep(delay)
                continue

            if status in (401, 403):
                raise Exception(label + " 鉴权失败(" + str(status) + ")：API 密钥无效或没有该模型权限，请到设置里检查密钥")

            if status == 402:
                raise Exception(label + " 余额不足(402)：请充值或改用其他模型")

            if status == 400:
                raise Exception(label + " 请求格式错误(400)：" + (body or "无返回内容"))

            if status in (500, 502, 503, 504):
                if server_error_left <= 0:
                    raise Exception(label + " 服务异常(" + str(status) + ")：" + body[:120])
                server_error_left -= 1
                time.sleep(min(3 * (max_retries - server_error_left + 1), 9))
                continue

            raise Exception(label + " 接口错误(" + str(status) + ")：" + (body or "无返回内容"))
        except Exception:
            if network_left <= 0:
                raise Exception(label + " 网络请求失败，请检查网络或代理设置")
            network_left -= 1
            time.sleep(min(3 * (max_retries - network_left + 1), 9))


def extract_key_info(tool_name, tool_args, result):
    info = {}
    if tool_name == "write_file":
        info['saved_file'] = tool_args.get('filepath', '')
    elif tool_name == "read_file":
        info['read_file'] = tool_args.get('filepath', '')
    elif tool_name == "execute_command":
        cmd = tool_args.get('command', '')
        info['command'] = cmd
        if 'start ' in cmd.lower() or 'explorer ' in cmd.lower():
            parts = cmd.split()
            for i, p in enumerate(parts):
                if p.lower() in ['start', 'explorer'] and i + 1 < len(parts):
                    info["target_file"] = parts[i + 1].strip('"')
                    break
    elif tool_name == "list_files":
        info['path'] = tool_args.get('path', '.')
    elif tool_name in ["search_images", "search_web"]:
        info['query'] = tool_args.get('query', '')
        if tool_name == "search_images" and result.get("results"):
            info['first_result'] = result['results'][0].get('image_url', '')
    return info


def make_tool_summary(tool_calls_results):
    summaries = []
    for tc_name, tc_args, result in tool_calls_results:
        info = extract_key_info(tc_name, tc_args, result)
        if info.get('saved_file'):
            summaries.append("已保存文件: " + info["saved_file"])
        elif info.get('read_file'):
            summaries.append("已读取文件: " + info["read_file"])
        elif info.get('target_file'):
            summaries.append("操作目标: " + info["target_file"])
        elif info.get('command'):
            summaries.append("执行命令: " + info["command"][:50])
        elif info.get('query'):
            summaries.append("搜索: " + info["query"])
    if summaries:
        return "；".join(summaries)
    return ""


def stream_chat(messages, sid, enabled_skills=None, effort="high", model_alias=DEFAULT_MODEL,
                enabled_knowledge=None, fast_mode=True, usage=None):
    max_turns = 50
    if isinstance(usage, dict):
        normalized = normalize_usage(usage)
        usage.clear()
        usage.update(normalized)
    else:
        usage = empty_usage()
    user_count = sum(1 for m in messages if m.get("role") == "user")
    usage["rounds"] = max(int(usage.get("rounds") or 0), user_count)
    skill_data = skill_manager.get_enabled_skills(enabled_skills or [])
    tools = list(skill_data["tools"])
    handlers = skill_data["handlers"]
    tool_display = {}
    for st in tools:
        fn = st.get("function", {})
        tool_display[fn.get("name", "")] = " " + fn.get("name", "")

    for turn in range(max_turns):
        if session_stop.get(sid, False):
            yield {"type": "text", "content": "\n\n[已停止]"}
            yield {"type": "done"}
            return

        if turn:
            time.sleep(0.4)

        try:
            request_messages = build_request_messages(messages, enabled_skills or [], enabled_knowledge or [])
            if tools:
                for msg in request_messages:
                    if msg.get("role") == "assistant" and not isinstance(msg.get("reasoning_content"), str):
                        msg["reasoning_content"] = ""
            call_start = time.time()
            resp = api_request_with_retry(request_messages, tools=tools, effort=effort, model_alias=model_alias)
        except Exception as e:
            yield {"type": "text", "content": "\n\n[API错误] " + str(e)}
            yield {"type": "done"}
            return
        usage["steps"] = int(usage.get("steps") or 0) + 1

        tool_calls = {}
        full_text = ""
        reasoning_text = ""
        has_any_output = False
        last_yield_time = time.time()
        first_token_at = None
        call_usage = None
        stopped = False
        stream_error = None
        artificial_delay = 0.0

        try:
            for line in resp.iter_lines():
                if session_stop.get(sid, False):
                    stopped = True
                    break

                if time.time() - last_yield_time > 4:
                    yield {"type": "heartbeat"}
                    last_yield_time = time.time()

                if not line:
                    continue
                line = line.decode("utf-8")
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    chunk_usage = chunk.get("usage")
                    if isinstance(chunk_usage, dict):
                        call_usage = chunk_usage
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    thinking_content = delta.get("reasoning_content") or delta.get("thinking_content")
                    if thinking_content:
                        has_any_output = True
                        if first_token_at is None:
                            first_token_at = time.time()
                        reasoning_text += thinking_content
                        yield {"type": "thinking", "content": thinking_content}
                        last_yield_time = time.time()

                    if "content" in delta and delta["content"]:
                        has_any_output = True
                        if first_token_at is None:
                            first_token_at = time.time()
                        full_text += delta["content"]
                        yield {"type": "text", "content": delta["content"]}
                        if not fast_mode:
                            pause = 0.04 * len(delta["content"])
                            time.sleep(pause)
                            artificial_delay += pause
                        last_yield_time = time.time()

                    if "tool_calls" in delta:
                        has_any_output = True
                        if first_token_at is None:
                            first_token_at = time.time()
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls:
                                tool_calls[idx] = {"id": tc.get("id", ""), "name": "", "args": ""}
                            if "function" in tc:
                                if "name" in tc["function"]:
                                    tool_calls[idx]["name"] = tc["function"]["name"]
                                if "arguments" in tc["function"]:
                                    tool_calls[idx]["args"] += tc["function"]["arguments"]
                except Exception:
                    continue
        except Exception as e:
            stream_error = e
        finally:
            resp.close()

        call_end = time.time()
        active_seconds = max(0.0, call_end - call_start - artificial_delay)
        usage["model_seconds"] = float(usage.get("model_seconds") or 0) + active_seconds
        if first_token_at is not None:
            usage["ttft_total"] = float(usage.get("ttft_total") or 0) + max(0.0, first_token_at - call_start)
            usage["ttft_count"] = int(usage.get("ttft_count") or 0) + 1
            usage["gen_seconds"] = float(usage.get("gen_seconds") or 0) + max(
                0.0, call_end - first_token_at - artificial_delay)

        if isinstance(call_usage, dict):
            prompt_tokens = int(_usage_number(call_usage.get("prompt_tokens")))
            completion_tokens = int(_usage_number(call_usage.get("completion_tokens")))
            cache_hit_tokens = int(_usage_number(call_usage.get("prompt_cache_hit_tokens")))
            cache_miss_tokens = int(_usage_number(call_usage.get("prompt_cache_miss_tokens")))
            if not cache_hit_tokens:
                details = call_usage.get("prompt_tokens_details")
                if isinstance(details, dict):
                    cache_hit_tokens = int(_usage_number(details.get("cached_tokens")))
            if prompt_tokens and not (cache_hit_tokens or cache_miss_tokens):
                cache_miss_tokens = prompt_tokens
            elif prompt_tokens and cache_hit_tokens + cache_miss_tokens != prompt_tokens:
                cache_hit_tokens = min(cache_hit_tokens, prompt_tokens)
                cache_miss_tokens = prompt_tokens - cache_hit_tokens
        else:
            prompt_tokens = sum(get_message_tokens(msg) for msg in request_messages)
            completion_tokens = estimate_tokens(full_text) + estimate_tokens(reasoning_text)
            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                completion_tokens += estimate_tokens(tc.get("name", "")) + estimate_tokens(tc.get("args", ""))
            cache_hit_tokens = 0
            cache_miss_tokens = prompt_tokens
            usage["estimated"] = True

        call_cost = compute_cost(model_alias, cache_hit_tokens, cache_miss_tokens, completion_tokens)
        usage["cache_hit_tokens"] = int(usage.get("cache_hit_tokens") or 0) + cache_hit_tokens
        usage["cache_miss_tokens"] = int(usage.get("cache_miss_tokens") or 0) + cache_miss_tokens
        usage["prompt_tokens"] = int(usage.get("prompt_tokens") or 0) + cache_hit_tokens + cache_miss_tokens
        usage["completion_tokens"] = int(usage.get("completion_tokens") or 0) + completion_tokens
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        cost = usage.get("cost")
        if not isinstance(cost, dict):
            cost = {}
            usage["cost"] = cost
        for key in ("cache_hit", "cache_miss", "output", "total"):
            cost[key] = float(cost.get(key) or 0) + call_cost[key]
        cost["currency"] = "CNY"
        usage["model"] = model_alias if model_alias in MODEL_MAP else DEFAULT_MODEL

        system_tokens = 0
        conversation_tokens = 0
        for index, msg in enumerate(request_messages):
            if index == 0 and msg.get("role") == "system":
                system_tokens = estimate_tokens(msg.get("content") or "")
            else:
                conversation_tokens += get_message_tokens(msg)
        usage["context"]["system"] = system_tokens
        usage["context"]["tools"] = estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0
        usage["context"]["messages"] = conversation_tokens
        usage["context"]["limit"] = MODEL_CONTEXT.get(model_alias, MODEL_CONTEXT[DEFAULT_MODEL])

        yield {"type": "usage", "usage": copy.deepcopy(usage)}

        if stopped:
            yield {"type": "text", "content": "\n\n[已停止]"}
            yield {"type": "done"}
            return

        if stream_error is not None:
            yield {"type": "text", "content": "\n\n[错误] 读取响应出错: " + str(stream_error)}
            yield {"type": "done"}
            return

        if not has_any_output and not tool_calls:
            yield {"type": "text", "content": "(AI 没有返回内容)"}
            yield {"type": "done"}
            return

        if not tool_calls:
            if full_text or reasoning_text:
                final_msg = {"role": "assistant", "content": full_text or ""}
                if reasoning_text:
                    final_msg["reasoning_content"] = reasoning_text
                messages.append(final_msg)
            yield {"type": "done"}
            return

        yield {"type": "interim", "content": "\n"}
        assistant_tool_calls = []
        for idx in sorted(tool_calls.keys()):
            tc = tool_calls[idx]
            assistant_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["args"]}
            })

        tool_calls_results = []

        assistant_msg = {
            "role": "assistant",
            "content": full_text or "",
            "tool_calls": assistant_tool_calls
        }
        if reasoning_text:
            assistant_msg["reasoning_content"] = reasoning_text
        messages.append(assistant_msg)

        for idx in sorted(tool_calls.keys()):
            tc = tool_calls[idx]
            tool_name = tc["name"]
            tool_call_id = tc["id"]

            if session_stop.get(sid, False):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({"error": "用户已停止执行"}, ensure_ascii=False)
                })
                continue

            try:
                tool_args = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                yield {"type": "text", "content": "\n 工具参数解析错误\n"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({"error": "工具参数解析错误"}, ensure_ascii=False)
                })
                continue

            yield {"type": "interim", "content": "\n" + "="*50 + "\n"}
            last_yield_time = time.time()

            yield {"type": "interim", "content": "[工具] " + tool_display.get(tool_name, tool_name) + "\n"}

            if tool_name == "execute_command":
                yield {"type": "interim", "content": "> 命令: " + tool_args.get("command", "") + "\n"}
            elif tool_name in ["read_file", "write_file"]:
                yield {"type": "interim", "content": "[文件] " + tool_args.get("filepath", "") + "\n"}
            elif tool_name == "list_files":
                yield {"type": "interim", "content": "[目录] " + tool_args.get("path", ".") + "\n"}
            else:
                args_str = json.dumps(tool_args, ensure_ascii=False, indent=2)
                yield {"type": "interim", "content": "[参数] " + args_str + "\n"}

            yield {"type": "interim", "content": "-"*50 + "\n"}
            yield {"type": "heartbeat"}


            tool_start = time.time()
            try:
                if tool_name == "execute_command":
                    cmd_result = [None]

                    def cmd_worker():
                        cmd_result[0] = handle_tool(tool_name, tool_args, handlers)

                    thread = threading.Thread(target=cmd_worker, daemon=True)
                    thread.start()
                    while thread.is_alive():
                        if session_stop.get(sid, False):
                            break
                        yield {"type": "heartbeat"}
                        thread.join(3)
                    if thread.is_alive():
                        usage["tool_seconds"] = float(usage.get("tool_seconds") or 0) + max(0.0, time.time() - tool_start)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps({"error": "用户已停止执行"}, ensure_ascii=False)
                        })
                        yield {"type": "text", "content": "\n\n[已停止]"}
                        yield {"type": "done"}
                        return
                    result = cmd_result[0]
                else:
                    result = handle_tool(tool_name, tool_args, handlers)
            except Exception as e:
                result = {"error": "工具执行异常: " + str(e)}
            usage["tool_seconds"] = float(usage.get("tool_seconds") or 0) + max(0.0, time.time() - tool_start)

            if result is None:
                result = {"error": "工具执行返回空结果"}

            tool_calls_results.append((tool_name, tool_args, result))

            output_text = _format_tool_output(tool_name, result)

            yield {"type": "interim", "content": output_text + "\n"}
            yield {"type": "interim", "content": "="*50 + "\n"}
            last_yield_time = time.time()

            try:
                tool_content_for_ai = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                tool_content_for_ai = str(result)
            tool_content_for_ai = truncate_text(tool_content_for_ai, app_config.get('max_content_len', 8000))

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_content_for_ai
            })

        if session_stop.get(sid, False):
            yield {"type": "text", "content": "\n\n[已停止]"}
            yield {"type": "done"}
            return

        summary = make_tool_summary(tool_calls_results)
        if summary:
            current_content = assistant_msg.get("content") or ""
            if not any(kw in current_content for kw in ['文件', '保存', '下载', '路径', '已']):
                assistant_msg["content"] = (current_content + "\n" + summary).strip() if current_content else summary

        yield {"type": "text", "content": "\n"}

    yield {"type": "text", "content": "\n(已达到最大执行轮数)"}
    yield {"type": "done"}


def _format_tool_output(tool_name, result):
    if tool_name in ("search_web_cn", "search_web_en", "search_web") and "results" in result:
        engine = result.get("engine", "未知")
        query = result.get("query", "")
        lines = ["[搜索] [" + engine.upper() + "] 查询词: " + query + "  找到 " + str(result.get("results_count", 0)) + " 条结果:\n"]
        for i, r in enumerate(result["results"][:5], 1):
            if 'error' in r:
                lines.append("   " + r["error"])
            else:
                lines.append("  " + str(i) + ". " + r.get("title", "无标题"))
                lines.append("     " + r.get("url", ""))
                snippet = r.get("snippet", "")[:200]
                lines.append("      " + snippet + "...")
                lines.append("")
        return "\n".join(lines)

    if tool_name in ("search_images_cn", "search_images_en", "search_images") and "results" in result:
        lines = ["[图片] 找到 " + str(result.get("results_count", 0)) + " 张图片:\n"]
        for i, r in enumerate(result["results"][:5], 1):
            if 'error' in r:
                lines.append("   " + r["error"])
            else:
                lines.append("  " + str(i) + ". " + r.get("title", "无标题"))
                lines.append("      " + r.get("image_url", "")[:80] + "...")
                lines.append("")
        return "\n".join(lines)

    if tool_name == "read_webpage":
        if 'error' in result:
            return "[错误] 抓取失败: " + result["error"]
        content = result.get("content", "")
        if len(content) > 3000:
            return " " + result.get("title", "无标题") + "\n\n" + content[:3000] + "\n\n... (共 " + str(len(content)) + " 字符，已截断)"
        return " " + result.get("title", "无标题") + "\n\n" + content

    if tool_name == "api_fetch_page":
        if 'error' in result:
            return "[错误] 抓取失败: " + result["error"]
        title = result.get("title") or "无标题"
        url = result.get("url", "")
        content = result.get("content", "")
        prefix = " " + title + "\n" + url + "\n\n"
        if result.get("truncated") or len(content) > 3000:
            total = result.get("content_length", len(content))
            return prefix + content[:3000] + "\n\n... (共 " + str(total) + " 字符，已截断)"
        return prefix + content

    if isinstance(result, dict) and result.get('files'):
        file_list = "\n".join(["  " + ("[文件夹]" if f["is_dir"] else "[文件]") + " " + f["name"] for f in result["files"]])
        return "[目录] 共 " + str(len(result["files"])) + " 个文件/目录:\n" + file_list

    if isinstance(result, dict) and 'output' in result:
        output = str(result["output"])
        if len(output) > 3000:
            return output[:3000] + "\n\n... (共 " + str(len(output)) + " 字符，已截断)"
        return output

    if isinstance(result, dict):
        for key in ('text', 'content', 'message', 'result'):
            if key in result:
                return str(result[key])[:3000]
        try:
            json_str = json.dumps(result, ensure_ascii=False, indent=2)
            if len(json_str) > 3000:
                return json_str[:3000] + "\n... (JSON 已截断)"
            return json_str
        except Exception:
            return str(result)[:3000]

    return str(result) if result else '(完成)'


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/skills', methods=['GET'])
def get_skills():
    return jsonify({"skills": skill_manager.get_all_skills()})

@app.route('/api/skills/reload', methods=['POST'])
def reload_skills():
    return jsonify({"status": "ok", "skills": skill_manager.reload()})

@app.route('/api/skills/session', methods=['GET'])
def get_session_skills_api():
    sid = request.args.get('conversation_id') or request.args.get('session_id', 'default')
    return jsonify({"session_id": sid, "enabled_skills": get_session_skills(sid)})

@app.route('/api/skills/session', methods=['POST'])
def set_session_skills_api():
    data = request.get_json(silent=True) or {}
    sid = data.get('conversation_id') or data.get('session_id', 'default')
    enabled = [s for s in data.get("enabled_skills", []) if s in skill_manager.skills]
    session_skills[sid] = enabled
    if sid == "default":
        save_config({"enabled_skills": enabled})
    return jsonify({"status": "ok", "session_id": sid, "enabled_skills": enabled})

@app.route('/api/knowledge', methods=['GET'])
def get_knowledge():
    return jsonify({"knowledge": list_knowledge_files()})

@app.route('/api/knowledge/session', methods=['GET'])
def get_session_knowledge_api():
    sid = request.args.get('conversation_id') or request.args.get('session_id', 'default')
    return jsonify({"session_id": sid, "enabled_knowledge": get_session_knowledge(sid)})

@app.route('/api/knowledge/session', methods=['POST'])
def set_session_knowledge_api():
    data = request.get_json(silent=True) or {}
    sid = data.get('conversation_id') or data.get('session_id', 'default')
    enabled = data.get("enabled_knowledge", [])
    valid_names = [f['name'] for f in list_knowledge_files()]
    enabled = [k for k in enabled if k in valid_names]
    session_knowledge[sid] = enabled
    if sid == "default":
        save_config({"enabled_knowledge": enabled})
    return jsonify({"status": "ok", "session_id": sid, "enabled_knowledge": enabled})

def resolve_conversation(data):
    cid = data.get('conversation_id') or data.get('session_id') or ''
    conv = load_conversation(cid) if cid else None
    if conv is None:
        conv = new_conversation()
    return conv


def find_user_msg_position(history, user_index):
    count = 0
    for i, msg in enumerate(history):
        if msg.get('role') == 'user':
            count += 1
            if count == user_index:
                return i
    return -1


def find_user_bubble_cut(bubbles, user_index, keep_user):
    count = 0
    for i, bubble in enumerate(bubbles):
        if bubble.get('role') == 'user':
            count += 1
            if count == user_index:
                return i + 1 if keep_user else i
    return len(bubbles)


def clip_for_edit(conv, user_index):
    idx = find_user_msg_position(conv["messages"], user_index)
    if idx < 0:
        return -1
    del conv["messages"][idx:]
    del conv["bubbles"][find_user_bubble_cut(conv["bubbles"], user_index, False):]
    return idx


def clip_for_regenerate(conv, user_index):
    idx = find_user_msg_position(conv["messages"], user_index)
    if idx < 0:
        return -1
    del conv["messages"][idx + 1:]
    del conv["bubbles"][find_user_bubble_cut(conv["bubbles"], user_index, True):]
    return idx


def run_turn(conv, user_text, effort, model_alias, mode, fast_mode=True):
    cid = conv["id"]
    lock = conversation_lock(cid)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "该会话正在处理中，请稍后再试"}), 409

    try:
        messages = conv["messages"]
        bubbles = conv["bubbles"]

        session_stop[cid] = False
        enabled_skills = get_session_skills(cid)
        enabled_knowledge = get_session_knowledge(cid)

        stored_usage = conv.get("usage")
        if isinstance(stored_usage, dict) and (
                stored_usage.get("steps") or stored_usage.get("prompt_tokens") or stored_usage.get("completion_tokens")):
            usage = normalize_usage(stored_usage)
        else:
            usage = estimate_conversation_usage(conv, enabled_skills, enabled_knowledge)
        usage["model"] = model_alias if model_alias in MODEL_MAP else DEFAULT_MODEL
        conv["usage"] = usage
        conv["model"] = usage["model"]

        if mode != "regenerate":
            messages.append({"role": "user", "content": user_text})
            bubbles.append({"role": "user", "content": user_text})
            if mode == "stream" and not conv.get("title_manual") and conv.get("title") in ("", "新会话"):
                conv["title"] = (user_text.strip().replace("\n", " ")[:30] or "新会话")

        bubble = {"role": "assistant", "segments": []}
        bubbles.append(bubble)
        recorder = TurnRecorder(bubble["segments"])

        def generate():
            try:
                for event in stream_chat(messages, cid, enabled_skills, effort, model_alias, enabled_knowledge, fast_mode, usage):
                    recorder.feed(event)
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception as e:
                print("生成异常: " + str(e))
                print(traceback.format_exc())
                yield json.dumps({"type": "text", "content": "\n\n[错误] 服务器内部错误: " + str(e)}, ensure_ascii=False) + "\n"
                yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
            finally:
                recorder.finish()
                save_conversation(conv)
                lock.release()

        def header():
            meta = json.dumps({"type": "meta", "conversation_id": cid, "title": conv.get("title", "新会话"),
                               "usage": normalize_usage(usage)}, ensure_ascii=False)
            return meta + "\n"

        def pipeline():
            yield header()
            for chunk in generate():
                yield chunk

        return Response(stream_with_context(pipeline()), mimetype="text/event-stream")
    except Exception:
        lock.release()
        raise


@app.route('/api/conversations', methods=['GET'])
def api_conversations():
    return jsonify({"conversations": list_conversations()})


@app.route('/api/conversations', methods=['POST'])
def api_conversation_create():
    conv = new_conversation()
    return jsonify({"id": conv["id"], "title": conv["title"]})


@app.route('/api/conversations/<cid>', methods=['GET'])
def api_conversation_get(cid):
    conv = load_conversation(cid)
    if conv is None:
        return jsonify({"error": "会话不存在"}), 404
    return jsonify({
        "id": conv["id"],
        "title": conv.get("title", "新会话"),
        "bubbles": conv.get("bubbles", []),
        "usage": conversation_usage(conv),
    })


@app.route('/api/conversations/<cid>', methods=['DELETE'])
def api_conversation_delete(cid):
    if not safe_cid(cid):
        return jsonify({"error": "非法会话"}), 400
    delete_conversation(cid)
    return jsonify({"status": "ok"})


@app.route('/api/conversations/<cid>/rename', methods=['POST'])
def api_conversation_rename(cid):
    conv = load_conversation(cid)
    if conv is None:
        return jsonify({"error": "会话不存在"}), 404
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    conv["title"] = title[:60]
    conv["title_manual"] = True
    save_conversation(conv)
    return jsonify({"status": "ok", "id": cid, "title": conv["title"]})


@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    data = request.get_json(silent=True) or {}
    user_msg = data.get('message', '')
    if not user_msg:
        return jsonify({"error": "请输入消息"}), 400
    model_alias = data.get('model', DEFAULT_MODEL)
    if model_alias not in MODEL_MAP:
        model_alias = DEFAULT_MODEL
    conv = resolve_conversation(data)
    sanitize_history(conv["messages"])
    return run_turn(conv, user_msg, resolve_effort(data), model_alias, "stream",
                    bool(data.get('fast_mode', False)))


@app.route('/api/stop', methods=['POST'])
def stop_chat():
    data = request.get_json(silent=True) or {}
    cid = data.get('conversation_id') or data.get('session_id') or ''
    session_stop[cid] = True
    return jsonify({"status": "ok"})


@app.route('/api/chat/edit', methods=['POST'])
def chat_edit():
    data = request.get_json(silent=True) or {}
    new_message = data.get('message', '')
    if not new_message:
        return jsonify({"error": "请输入消息"}), 400
    model_alias = data.get('model', DEFAULT_MODEL)
    if model_alias not in MODEL_MAP:
        model_alias = DEFAULT_MODEL
    conv = resolve_conversation(data)
    user_index = int(data.get('user_msg_index', 1))
    if clip_for_edit(conv, user_index) < 0:
        return jsonify({"error": "消息未找到"}), 404
    sanitize_history(conv["messages"])
    return run_turn(conv, new_message, resolve_effort(data), model_alias, "edit",
                    bool(data.get('fast_mode', False)))


@app.route('/api/chat/regenerate', methods=['POST'])
def chat_regenerate():
    data = request.get_json(silent=True) or {}
    model_alias = data.get('model', DEFAULT_MODEL)
    if model_alias not in MODEL_MAP:
        model_alias = DEFAULT_MODEL
    conv = resolve_conversation(data)
    user_index = int(data.get('user_msg_index', 1))
    if clip_for_regenerate(conv, user_index) < 0:
        return jsonify({"error": "消息未找到"}), 404
    sanitize_history(conv["messages"])
    return run_turn(conv, '', resolve_effort(data), model_alias, "regenerate",
                    bool(data.get('fast_mode', False)))


@app.route('/api/clear', methods=['POST'])
def clear():
    data = request.get_json(silent=True) or {}
    cid = data.get('conversation_id') or data.get('session_id') or ''
    conv = load_conversation(cid) if cid else None
    if conv is not None:
        conv["messages"] = []
        conv["bubbles"] = []
        conv["usage"] = empty_usage()
        if not conv.get("title_manual"):
            conv["title"] = "新会话"
        save_conversation(conv)
    session_stop[cid] = False
    return jsonify({"status": "ok"})

UPDATE_VERSION_FILE = os.path.join(SCRIPT_DIR, 'update.txt')
UPDATE_TASK_FILE = os.path.join(SCRIPT_DIR, 'update_task.json')
UPDATE_STAGING = os.path.join(SCRIPT_DIR, 'updatefile')


def version_tuple(text):
    parts = []
    for piece in str(text or "").strip().split("."):
        digits = re.sub(r"\D", "", piece)
        parts.append(int(digits) if digits else 0)
    if not parts:
        parts = [0]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest, current):
    a = version_tuple(latest)
    b = version_tuple(current)
    size = max(len(a), len(b))
    a = a + (0,) * (size - len(a))
    b = b + (0,) * (size - len(b))
    return a > b


def read_local_version():
    try:
        with open(UPDATE_VERSION_FILE, 'r', encoding='utf-8-sig') as f:
            value = f.read().strip()
        return value or "0.0.0"
    except Exception:
        return "0.0.0"


def update_base_url():
    value = (app_config.get("update_url") or "").strip()
    return (value or "http://192.168.0.146:1980").rstrip("/")


def fetch_update_manifest(timeout=3):
    url = update_base_url() + "/update.json"
    resp = std_requests.get(url, timeout=timeout, headers={"Cache-Control": "no-cache"})
    resp.raise_for_status()
    raw = (resp.content or b"").decode("utf-8-sig", errors="replace")
    data = json.loads(raw.lstrip("\ufeff"))
    if not isinstance(data, dict):
        raise ValueError("update.json 不是对象")
    version = str(data.get("version") or "").strip()
    raw_files = data.get("files")
    if not version or not isinstance(raw_files, list):
        raise ValueError("update.json 缺少 version 或 files")
    files = []
    for item in raw_files:
        path = str(item).replace("\\", "/").strip().lstrip("/")
        if not path or path.startswith("~"):
            continue
        if any(part in ("..", "") for part in path.split("/")):
            continue
        files.append(path)
    if not files:
        raise ValueError("更新清单为空")
    return {
        "version": version,
        "updatelog": str(data.get("updatelog") or ""),
        "files": files,
    }


def console_python():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return exe


def process_image_name(pid):
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value).lower() if ok else ""
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def terminate_pid(pid, expected_names=("python.exe", "pythonw.exe")):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    name = process_image_name(pid)
    if name and expected_names and name not in expected_names:
        log_line("跳过终止 PID %d（进程名 %s 不匹配）" % (pid, name))
        return False
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def wait_ready_file(path, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.3)
    return False


def prepare_update_task(manifest):
    parent_pid = arg_value('--parent')
    ready_file = os.path.join(tempfile.gettempdir(), 'coain-updater-ready-%d.txt' % os.getpid())
    try:
        if os.path.exists(ready_file):
            os.remove(ready_file)
    except Exception:
        pass
    task = {
        "server": update_base_url(),
        "version": manifest["version"],
        "old_version": read_local_version(),
        "updatelog": manifest["updatelog"],
        "files": manifest["files"],
        "app_dir": SCRIPT_DIR,
        "self_pid": os.getpid(),
        "parent_pid": int(parent_pid) if (parent_pid or "").isdigit() else 0,
        "ready_file": ready_file,
    }
    with open(UPDATE_TASK_FILE, 'w', encoding='utf-8') as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
    return task


def launch_updater():
    updater = os.path.join(SCRIPT_DIR, 'updater.py')
    if not os.path.isfile(updater):
        raise RuntimeError("找不到 updater.py")
    subprocess.Popen(
        [console_python(), updater, UPDATE_TASK_FILE],
        cwd=SCRIPT_DIR,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )


def shutdown_for_update(task):
    ready = task.get("ready_file") or ""
    if ready and not wait_ready_file(ready, 20):
        log_line("更新程序未能在 20 秒内启动，已取消更新")
        try:
            if os.path.exists(UPDATE_TASK_FILE):
                os.remove(UPDATE_TASK_FILE)
        except Exception:
            pass
        return
    time.sleep(0.8)
    log_line("开始更新到 " + task["version"] + "，关闭应用")
    terminate_pid(task.get("parent_pid"))
    os._exit(0)


@app.route('/api/update/check', methods=['GET'])
def api_update_check():
    current = read_local_version()
    try:
        manifest = fetch_update_manifest()
    except Exception as e:
        return jsonify({"has_update": False, "current": current, "error": str(e)})
    return jsonify({
        "has_update": is_newer(manifest["version"], current),
        "current": current,
        "latest": manifest["version"],
        "updatelog": manifest["updatelog"],
        "file_count": len(manifest["files"]),
    })


@app.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    current = read_local_version()
    try:
        manifest = fetch_update_manifest()
    except Exception as e:
        return jsonify({"status": "error", "message": "获取更新信息失败: " + str(e)}), 502
    if not is_newer(manifest["version"], current):
        return jsonify({"status": "error", "message": "当前已是最新版本"}), 400
    try:
        task = prepare_update_task(manifest)
        launch_updater()
    except Exception as e:
        log_line("启动更新失败: " + str(e))
        return jsonify({"status": "error", "message": "启动更新失败: " + str(e)}), 500
    threading.Thread(target=shutdown_for_update, args=(task,), daemon=True).start()
    return jsonify({"status": "ok", "version": manifest["version"]})


@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(app_config)

@app.route('/api/config', methods=['POST'])
def set_config():
    data = request.get_json(silent=True) or {}
    allowed_keys = ['deepseek_api_key', 'max_messages', 'max_total_tokens',
                    'max_content_len', 'sidebar_collapsed', 'update_url', 'model', 'fast_mode', 'effort']
    update = {}
    for k in allowed_keys:
        if k in data:
            if k in ['max_messages', 'max_total_tokens', 'max_content_len']:
                try:
                    update[k] = max(1, int(data[k]))
                except (TypeError, ValueError):
                    pass
            elif k in ['sidebar_collapsed', 'fast_mode']:
                try:
                    update[k] = 1 if int(data[k]) else 0
                except (TypeError, ValueError):
                    pass
            elif k == 'model':
                value = str(data[k])
                if value in MODEL_MAP:
                    update[k] = value
            elif k == 'effort':
                update[k] = normalize_effort(data[k])
            else:
                update[k] = str(data[k])
    if save_config(update):
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "保存失败"}), 500

if __name__ == '__main__':
    log_line("=" * 40)
    log_line("程序启动")
    log_line("服务进程 PID=%d" % os.getpid())

    parent_pid = arg_value('--parent')
    parent_pid = int(parent_pid) if parent_pid and parent_pid.isdigit() else None
    port_file = arg_value('--port-file')

    try:
        from werkzeug.serving import make_server
        if port_in_use(5000):
            log_line("5000 端口已有服务在响应，改用随机端口")
            server = make_server('0.0.0.0', 0, app, threaded=True)
        else:
            try:
                server = make_server('0.0.0.0', 5000, app, threaded=True)
            except OSError:
                server = make_server('0.0.0.0', 0, app, threaded=True)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = 'http://127.0.0.1:' + str(port) + '/'
        log_line("服务已启动: " + url)

        if port_file:
            try:
                with open(port_file, 'w', encoding='utf-8') as f:
                    f.write(str(port))
            except Exception:
                log_line("端口文件写入失败: " + str(port_file))

        if parent_pid:
            shutdown_with_parent(server, parent_pid, port_file)

        open_shell(url, port)
        try:
            server.shutdown()
        except Exception:
            pass
        os._exit(0)
    except Exception as e:
        err_msg = "[错误] 程序异常退出: " + str(e) + "\n" + traceback.format_exc()
        log_line(err_msg)
        print(err_msg)
        try:
            ctypes.windll.user32.MessageBoxW(0, err_msg[:1000], "Coain 错误", 0)
        except Exception:
            pass
        os._exit(1)