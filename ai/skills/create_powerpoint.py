import os

SKILL_NAME = "PPT制作"
SKILL_DESCRIPTION = "PPT制作优化"
ENABLED_BY_DEFAULT = False

# 启用时才注入，精简且专注
SKILL_SYSTEM_PROMPT = """
用户要求做PPT时：
1. 先分析需求，不清楚的地方直接问
2. 使用 python-pptx 库制作
3. 保存到 {WORKSPACE}
4. 配合上下文理解思考
5. 排版精美，制作精美，内容生动活泼。
6. 如果用户提到，去img文件夹使用图片当作背景等 
"""


