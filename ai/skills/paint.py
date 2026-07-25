import os
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

SKILL_NAME = "AI画笔"
SKILL_DESCRIPTION = "通过代码控制画笔作画，支持画线、圆、矩形、多边形、写文字等，保存为PNG"
SKILL_SYSTEM_PROMPT = (
    "当用户要求你画画、作图、绘制图形、写字作画时，使用画笔工具作画。"
    "作画流程：1) 先调用 create_canvas 创建画布；2) 调用各种 draw_xxx 工具绘制；3) 调用 save_canvas 保存。"
    "画布坐标系：左上角为(0,0)，x向右增加，y向下增加。"
    "颜色格式：使用十六进制如 '#ff0000' 表示红色，'#0000ff' 蓝色，'#ffffff' 白色，'#000000' 黑色。"
    "写文字使用 draw_text，font_size 控制大小，可选 font_name: arial.ttf / arialbd.ttf(粗体) / times.ttf / cour.ttf。"
    "画图时按用户描述的布局精确计算坐标。完成后告诉用户文件名。"
    f"保存目录: {WORKSPACE}"
)
SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_canvas",
            "description": "创建一张新画布（白色背景或指定颜色）。作画前必须先调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "description": "画布宽度(像素)，默认800"},
                    "height": {"type": "integer", "description": "画布高度(像素)，默认600"},
                    "bg_color": {"type": "string", "description": "背景色，十六进制如 #ffffff，默认白色"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_line",
            "description": "在画布上画一条直线",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                    "color": {"type": "string", "description": "线条颜色，如 #000000"},
                    "width": {"type": "integer", "description": "线条粗细(像素)，默认1"}
                },
                "required": ["x1", "y1", "x2", "y2", "color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_rectangle",
            "description": "画矩形，可填充",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "左上角x"},
                    "y1": {"type": "integer", "description": "左上角y"},
                    "x2": {"type": "integer", "description": "右下角x"},
                    "y2": {"type": "integer", "description": "右下角y"},
                    "color": {"type": "string", "description": "边框颜色"},
                    "fill": {"type": "string", "description": "填充颜色，不填则不填充"},
                    "width": {"type": "integer", "description": "边框粗细，默认1"}
                },
                "required": ["x1", "y1", "x2", "y2", "color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_circle",
            "description": "画圆，可填充",
            "parameters": {
                "type": "object",
                "properties": {
                    "cx": {"type": "integer", "description": "圆心x"},
                    "cy": {"type": "integer", "description": "圆心y"},
                    "r": {"type": "integer", "description": "半径"},
                    "color": {"type": "string", "description": "边框颜色"},
                    "fill": {"type": "string", "description": "填充颜色"},
                    "width": {"type": "integer", "description": "边框粗细，默认1"}
                },
                "required": ["cx", "cy", "r", "color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_ellipse",
            "description": "画椭圆，可填充",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "外接矩形左上x"},
                    "y1": {"type": "integer", "description": "外接矩形左上y"},
                    "x2": {"type": "integer", "description": "外接矩形右下x"},
                    "y2": {"type": "integer", "description": "外接矩形右下y"},
                    "color": {"type": "string"},
                    "fill": {"type": "string"},
                    "width": {"type": "integer", "description": "边框粗细，默认1"}
                },
                "required": ["x1", "y1", "x2", "y2", "color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_polygon",
            "description": "画多边形，可填充。自动闭合",
            "parameters": {
                "type": "object",
                "properties": {
                    "points": {"type": "string", "description": "顶点列表，格式 'x1,y1 x2,y2 x3,y3'，空格分隔点，逗号分隔xy"},
                    "color": {"type": "string", "description": "边框颜色"},
                    "fill": {"type": "string", "description": "填充颜色"}
                },
                "required": ["points", "color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "draw_text",
            "description": "在画布上写文字",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "文字左上角x"},
                    "y": {"type": "integer", "description": "文字左上角y"},
                    "text": {"type": "string", "description": "要写的文字内容"},
                    "color": {"type": "string", "description": "文字颜色，如 #000000", "default": "#000000"},
                    "font_size": {"type": "integer", "description": "字体大小，默认20"},
                    "font_name": {"type": "string", "description": "字体文件名: arial.ttf / arialbd.ttf(粗体) / times.ttf / cour.ttf / simhei.ttf(中文黑体) / msyh.ttf(中文微软雅黑)", "default": "arial.ttf"}
                },
                "required": ["x", "y", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fill_area",
            "description": "用指定颜色填充整张画布",
            "parameters": {
                "type": "object",
                "properties": {
                    "color": {"type": "string", "description": "填充颜色"}
                },
                "required": ["color"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_canvas",
            "description": "保存画布为PNG文件。作画完成后必须调用此工具保存",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "保存文件名，如 'painting.png'。不提供则自动生成"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_info",
            "description": "获取当前画布尺寸和状态",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]
ENABLED_BY_DEFAULT = False


# 按 session_id 保存画布的会话状态
_canvases = {}


def _get_canvas():
    """获取当前会话的画布和绘图对象"""
    sid = _get_session_id()
    if sid not in _canvases:
        return None, None, None
    img, draw = _canvases[sid]
    return img, draw, sid


def _get_session_id():
    """从调用栈推断 session_id（通过 SKILL 工作目录间接定位）"""
    # 简化：所有调用共享一个全局画布
    # 如需多会话隔离，可在 handle_xxx 函数签名加 session_id 参数
    return "default"


def _parse_color(color):
    """颜色解析，支持 #RRGGBB 或颜色名"""
    if not color:
        return (0, 0, 0)
    color = color.strip()
    if color.startswith('#'):
        h = color[1:]
        if len(h) == 6:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        if len(h) == 3:
            return tuple(int(h[i]*2, 16) for i in range(3))
    named = {
        'black': (0, 0, 0), 'white': (255, 255, 255),
        'red': (255, 0, 0), 'green': (0, 128, 0), 'blue': (0, 0, 255),
        'yellow': (255, 255, 0), 'cyan': (0, 255, 255),
        'magenta': (255, 0, 255), 'gray': (128, 128, 128),
        'orange': (255, 165, 0), 'pink': (255, 192, 203),
        'purple': (128, 0, 128), 'brown': (165, 42, 42),
    }
    return named.get(color.lower(), (0, 0, 0))


def _font_path(font_name):
    """Windows 字体路径"""
    fonts_dir = r"C:\Windows\Fonts"
    candidates = [
        os.path.join(fonts_dir, font_name),
        os.path.join(fonts_dir, font_name.lower()),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    # 回退到默认字体
    default = os.path.join(fonts_dir, "arial.ttf")
    return default if os.path.isfile(default) else None


def handle_create_canvas(width=800, height=600, bg_color="#ffffff"):
    if Image is None:
        return {"error": "Pillow 未安装，无法作画"}
    try:
        w = int(width) if width else 800
        h = int(height) if height else 600
        if w <= 0 or h <= 0 or w > 8000 or h > 8000:
            return {"error": f"画布尺寸不合法: {w}x{h}（应在 1-8000 之间）"}
        bg = _parse_color(bg_color)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        _canvases["default"] = (img, draw)
        return {"output": f"画布已创建: {w}x{h}, 背景色 {bg_color}"}
    except Exception as e:
        return {"error": f"创建画布失败: {str(e)}"}


def handle_draw_line(x1, y1, x2, y2, color="#000000", width=1):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建，请先调用 create_canvas"}
    try:
        draw.line(
            [(int(x1), int(y1)), (int(x2), int(y2))],
            fill=_parse_color(color),
            width=max(1, int(width)) if width else 1
        )
        return {"output": f"已画线: ({x1},{y1}) -> ({x2},{y2}) 颜色{color} 粗细{width}"}
    except Exception as e:
        return {"error": f"画线失败: {str(e)}"}


def handle_draw_rectangle(x1, y1, x2, y2, color="#000000", fill=None, width=1):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建"}
    try:
        fill_color = _parse_color(fill) if fill else None
        draw.rectangle(
            [(int(x1), int(y1)), (int(x2), int(y2))],
            outline=_parse_color(color),
            fill=fill_color,
            width=max(1, int(width)) if width else 1
        )
        return {"output": f"已画矩形: ({x1},{y1})-({x2},{y2}) 边框{color} 填充{fill}"}
    except Exception as e:
        return {"error": f"画矩形失败: {str(e)}"}


def handle_draw_circle(cx, cy, r, color="#000000", fill=None, width=1):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建"}
    try:
        cx, cy, r = int(cx), int(cy), int(r)
        fill_color = _parse_color(fill) if fill else None
        draw.ellipse(
            [(cx - r, cy - r), (cx + r, cy + r)],
            outline=_parse_color(color),
            fill=fill_color,
            width=max(1, int(width)) if width else 1
        )
        return {"output": f"已画圆: 圆心({cx},{cy}) 半径{r} 颜色{color}"}
    except Exception as e:
        return {"error": f"画圆失败: {str(e)}"}


def handle_draw_ellipse(x1, y1, x2, y2, color="#000000", fill=None, width=1):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建"}
    try:
        fill_color = _parse_color(fill) if fill else None
        draw.ellipse(
            [(int(x1), int(y1)), (int(x2), int(y2))],
            outline=_parse_color(color),
            fill=fill_color,
            width=max(1, int(width)) if width else 1
        )
        return {"output": f"已画椭圆: ({x1},{y1})-({x2},{y2})"}
    except Exception as e:
        return {"error": f"画椭圆失败: {str(e)}"}


def handle_draw_polygon(points, color="#000000", fill=None):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建"}
    try:
        pts = []
        for p in points.strip().split():
            x_str, y_str = p.split(',')
            pts.append((int(x_str), int(y_str)))
        if len(pts) < 3:
            return {"error": "多边形至少需要 3 个顶点"}
        fill_color = _parse_color(fill) if fill else None
        draw.polygon(pts, outline=_parse_color(color), fill=fill_color)
        return {"output": f"已画多边形: {len(pts)} 个顶点"}
    except Exception as e:
        return {"error": f"画多边形失败: {str(e)}"}


def handle_draw_text(x, y, text, color="#000000", font_size=20, font_name="arial.ttf"):
    img, draw, _ = _get_canvas()
    if draw is None:
        return {"error": "画布未创建"}
    try:
        font_path = _font_path(font_name)
        font = None
        if font_path:
            try:
                font = ImageFont.truetype(font_path, int(font_size))
            except Exception:
                font = None
        if font is None:
            font = ImageFont.load_default()

        draw.text(
            (int(x), int(y)),
            str(text),
            fill=_parse_color(color),
            font=font
        )
        return {"output": f"已写文字 '{text}' 于({x},{y}) 大小{font_size} 颜色{color}"}
    except Exception as e:
        return {"error": f"写文字失败: {str(e)}"}


def handle_fill_area(color):
    img, draw, _ = _get_canvas()
    if img is None:
        return {"error": "画布未创建"}
    try:
        w, h = img.size
        new_img = Image.new("RGB", (w, h), _parse_color(color))
        _canvases["default"] = (new_img, ImageDraw.Draw(new_img))
        return {"output": f"已填充画布: {color}"}
    except Exception as e:
        return {"error": f"填充失败: {str(e)}"}


def handle_save_canvas(filename=None):
    img, draw, _ = _get_canvas()
    if img is None:
        return {"error": "画布未创建，无内容可保存"}
    try:
        if not filename:
            ts = datetime.now().strftime("%m%d_%H%M%S")
            filename = f"painting_{ts}.png"
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            filename += '.png'
        save_path = os.path.join(WORKSPACE, filename)
        img.save(save_path)
        return {
            "output": f"画作已保存: {filename}\n路径: {save_path}\n尺寸: {img.size[0]}x{img.size[1]}",
            "saved_file": save_path,
            "filename": filename
        }
    except Exception as e:
        return {"error": f"保存失败: {str(e)}"}


def handle_get_canvas_info():
    img, draw, _ = _get_canvas()
    if img is None:
        return {"output": "当前无画布，请先调用 create_canvas 创建"}
    w, h = img.size
    return {"output": f"画布尺寸: {w}x{h}"}
