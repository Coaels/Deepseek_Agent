import os
import subprocess
import shutil
import shlex
import re
SKILL_NAME = "Windows 操作"
SKILL_DESCRIPTION = "Windows 命令执行、文件读写、目录列表"
ENABLED_BY_DEFAULT = True

SKILL_SYSTEM_PROMPT = f"""
工作目录: {WORKSPACE}，所有文件操作都在这里，除非用户要求其他位置。
所有删除修改操作先备份在 {WORKSPACE}\\backups。
执行命令完全遵守用户，不多弄,一步到位执行到底。
不允许执行用户没让你干的命令。
修改文件时最好修改单独行而不是重写全部
"""

SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "执行 Windows 命令行命令",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {"filepath": {"type": "string"}},
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或创建文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录下的文件和文件夹",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": []
            }
        }
    }
]


def _truncate_text(text, max_len=8000):
    if len(text) > max_len:
        return text[:max_len] + f"\n... (内容已截断，共{len(text)}字符)"
    return text


def _needs_temp_file(command):
    """判断命令是否需要写临时文件执行"""
    # 1. 命令包含换行符
    if "\n" in command or "\r" in command:
        return True, command

    # 2. python -c 命令：一律写临时文件
    # Windows cmd 下 python -c 的引号处理太容易出问题
    if "python" in command.lower() and "-c" in command:
        code = None
        patterns = [
            r'-c\s+"(.+)"',
            r"-c\s+'(.+)'",
            r'-c\s+(.+)$',
        ]
        for pattern in patterns:
            match = re.search(pattern, command, re.DOTALL)
            if match:
                code = match.group(1)
                break
        return True, code

    # 3. 命令本身包含未配对的引号
    single_quotes = command.count("'") - command.count("\\'")
    double_quotes = command.count('"') - command.count('\\"')
    if single_quotes % 2 != 0 or double_quotes % 2 != 0:
        return True, command

    # 4. 命令太长
    if len(command) > 500:
        return True, command

    return False, None


def handle_execute_command(command="", timeout=60):
    """
    执行系统命令，自动处理 Windows 引号问题。
    如果命令包含复杂引号嵌套，自动写入临时 .py/.bat 文件执行。
    """
    if not command or not command.strip():
        return {"error": "命令不能为空"}

    command = command.strip()
    original_command = command

    # 检测是否需要写临时文件
    needs_temp, extracted = _needs_temp_file(command)

    if needs_temp:
        # 判断是 Python 代码还是普通命令
        if "python" in command.lower() and "-c" in command:
            code = extracted
            if code:
                # 处理转义的换行符
                code = code.replace("\\n", "\n")
                # 写入临时 .py 文件
                temp_path = os.path.join(WORKSPACE, "_temp_cmd.py")
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(code)
                    # 用 python 执行临时文件
                    command = f'python "{temp_path}"'
                except Exception as e:
                    return {"error": f"写入临时文件失败: {str(e)}"}
            else:
                return {"error": "无法提取 python -c 后面的代码"}
        else:
            # 普通命令，写入 .bat
            temp_path = os.path.join(WORKSPACE, "_temp_cmd.bat")
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write("@echo off\n")
                    f.write(command)
                    f.write("\n")
                command = f'"{temp_path}"'
            except Exception as e:
                return {"error": f"写入临时文件失败: {str(e)}"}

    # 执行命令
    try:
        if command.startswith("python "):
            # 尝试用列表形式执行，避免 shell 引号问题
            try:
                args = shlex.split(command, posix=False)
                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=WORKSPACE,
                    encoding="utf-8",
                    errors="replace"
                )
            except Exception:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=WORKSPACE,
                    encoding="utf-8",
                    errors="replace"
                )
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=WORKSPACE,
                encoding="utf-8",
                errors="replace"
            )

        output = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        # 合并输出
        full_output = output
        if stderr and result.returncode != 0:
            full_output += f"\n[STDERR] {stderr}" if full_output else f"[STDERR] {stderr}"
        elif stderr:
            full_output += f"\n[警告] {stderr}" if full_output else f"[警告] {stderr}"

        return {
            "output": full_output or "(命令执行成功，无输出)",
            "returncode": result.returncode,
            "command": original_command,
            "executed_as": command if command != original_command else original_command
        }

    except subprocess.TimeoutExpired:
        return {"error": f"命令执行超时（{timeout}秒）", "command": original_command}
    except Exception as e:
        return {"error": f"执行失败: {str(e)}", "command": original_command}


def handle_read_file(filepath):
    path = os.path.join(WORKSPACE, filepath)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content, "truncated_for_ai": _truncate_text(content)}
    except Exception as e:
        return {"error": str(e)}


def handle_write_file(filepath, content):
    path = os.path.join(WORKSPACE, filepath)
    try:
        # 备份逻辑
        if os.path.exists(path):
            backup_dir = os.path.join(WORKSPACE, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, os.path.basename(filepath) + '.bak')
            shutil.copy2(path, backup_path)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"message": f"已保存: {filepath}", "filepath": filepath}
    except Exception as e:
        return {"error": str(e)}


def handle_list_files(path="."):
    full_path = os.path.join(WORKSPACE, path)
    try:
        items = [{"name": n, "is_dir": os.path.isdir(os.path.join(full_path, n))} for n in os.listdir(full_path)]
        return {"files": items}
    except Exception as e:
        return {"error": str(e)}
