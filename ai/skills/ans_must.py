import os
import subprocess
import shutil
import shlex
import re
SKILL_NAME = "严格回复"
SKILL_DESCRIPTION = "AI将使用严格回复模式，不允许乱答"
ENABLED_BY_DEFAULT = True

SKILL_SYSTEM_PROMPT = f"""
强制规则:
1.只回答有明确训练数据依据的确定事实,否则只回复“无法确认”
2.禁止补全、推断、使用“可能”“大概”等模糊词
3.若问题涉及时效性，比较传入的当前日期与你的知识截止日期，差值超过10天则不回答，回复“时间差过大，无有效回答信息”。
4.不确定直接答“无法确认”，不附加解释。
"""