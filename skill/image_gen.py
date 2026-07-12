import os
import urllib.request
import urllib.parse
from datetime import datetime

SKILL_NAME = "图像生成"
SKILL_DESCRIPTION = "根据文本描述生成图片，支持自定义尺寸和文件名"
SKILL_SYSTEM_PROMPT = (
    "当用户要求画图、生成图片、画一张图、画个xxx时，调用 generate_image 工具生成图片。"+
    "默认尺寸 1024x1024，如果用户没指定尺寸不要擅自改。"+
    "生成完成后告诉用户图片已保存到工作目录，并说明文件名。"+
    "如果用户指定了图片生成要求，严格按照用户生成要求"+
    f"图片保存目录: {WORKSPACE}"
)
SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "根据文本描述生成一张图片并保存到工作目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "图片的详细描述，用英文或中文都可以，建议尽量详细"
                    },
                    "width": {
                        "type": "integer",
                        "default": 1024,
                        "description": "图片宽度，默认1024"
                    },
                    "height": {
                        "type": "integer",
                        "default": 1024,
                        "description": "图片高度，默认1024"
                    },
                    "filename": {
                        "type": "string",
                        "description": "保存的文件名，如 'cat.png'。如果不提供则自动生成"
                    }
                },
                "required": ["prompt"]
            }
        }
    }
]
ENABLED_BY_DEFAULT = True


def handle_generate_image(prompt, width=1024, height=1024, filename=None):

    if not filename:
        ts = datetime.now().strftime("%m%d_%H%M%S")
        safe_prompt = "".join(c if c.isalnum() or c in "-_" else "_" for c in prompt)[:30]
        filename = f"{safe_prompt}_{ts}.png"

    if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
        filename += '.png'

    save_path = os.path.join(WORKSPACE, filename)


    encoded_prompt = urllib.parse.quote(prompt)


    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}&height={height}&nologo=true&seed={int(datetime.now().timestamp())}"
    )

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
            }
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())

        return {
            "output": f"图片已生成并保存: {filename}\n路径: {save_path}\n尺寸: {width}x{height}",
            "saved_file": save_path,
            "filename": filename
        }

    except Exception as e:
        return {"error": f"图片生成失败: {str(e)}"}