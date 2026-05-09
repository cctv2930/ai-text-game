import os
import json
import re
import time
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

def get_available_icons():
    icons_dir = "../frontend/assets/UI_label"
    if os.path.exists(icons_dir):
        files = [f for f in os.listdir(icons_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.svg'))]
        return files if files else ["default.png"]
    return ["default.png"]

def extract_json(text: str) -> str:
    """从文本中提取第一个完整的 JSON 对象"""
    start = text.find('{')
    if start == -1:
        raise ValueError("未找到 JSON 起始符")
    stack = []
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            stack.append(i)
        elif ch == '}':
            stack.pop()
            if not stack:
                return text[start:i+1]
    raise ValueError("JSON 不完整")

def repair_json(content: str) -> str:
    content = extract_json(content)
    content = re.sub(r',\s*}', '}', content)
    content = re.sub(r',\s*]', ']', content)
    return content

def generate_next_story(
    user_choice: str,
    history_summary: str,
    resources: dict,
    retry: int = 2,
    expected_effect=None
) -> dict:
    """
    调用 DeepSeek 生成下一段剧情、选项和资源变化
    返回字典: {"description": str, "options": list, "resource_changes": dict}
    """
    # 获取可用图标列表
    available_icons = get_available_icons()
    icons_str = ", ".join(available_icons)

    
    system_prompt = f"""
你是一个互动文字冒险游戏大师。当前玩家状态：
- 属性（stats）：{resources.get("stats", {})}
- 物品栏（inventory）：{resources.get("inventory", [])}
历史剧情摘要：{history_summary}
玩家刚才选择了：「{user_choice}」

【重要规则】
- 玩家所选的选项是触发后续变化的唯一原因。
- **所有属性变化（stats_changes）和物品获得/消耗（new_items / used_items）都必须直接来源于玩家选择的选项。**
- 剧情描述中可以提及物品，但请不要因为这些提及就往 `new_items` 中添加物品，除非选项本身明确表示“获得物品”。
- 每个选项的 `effect` 字段必须标明会得到或失去哪些物品，并且 `new_items` / `used_items` 必须与 `effect` 中的描述一一对应。
**消耗物品示例**：
- 选项效果：“将羽毛抛入井中，消耗羽毛” → 必须在 `used_items` 中包含 `{{"name": "黑色羽毛"}}`。
- 选项效果：“收好羽毛，无消耗” → `used_items` 为空数组 `[]`。

**重复物品规则**：
- 如果物品栏已经拥有某物品（如“黑色羽毛”），不要因为选项“获得”而再次添加。
- 只有当选项明确表示“获得第二个”或“得到新的”时才允许添加重复物品。
- **数值合理**：确保 `stats_changes` 不会导致属性变成负数（如果可以变负，请说明理由，否则限制为最低0）。

请根据这个选择，生成接下来的游戏发展。输出必须是严格的 JSON，格式如下：
{{
  "description": "一段精彩的剧情描述（80-150字）",
  "options": [
    {{"text": "选项A", "effect": "简短说明后果，并且在后面补充说明资源的变化情况（如生命值变化、获得/消耗物品）"}},
    {{"text": "选项B", "effect": "简短说明后果，并且在后面补充说明资源的变化情况（如生命值变化、获得/消耗物品）"}},
    {{"text": "选项C", "effect": "简短说明后果，并且在后面补充说明资源的变化情况（如生命值变化、获得/消耗物品）"}}
  ],
  "stats_changes": {{
    "生命值": -10,
    "魔法值": 5
  }},
  "new_items": [
    {{"name": "宝石", "icon": "gem.png"}},
    {{"name": "钥匙", "icon": "key.png"}}
  ],
  "used_items": [
    {{"name": "钥匙"}}
  ]
}}
关键要求：
- 包含 3 个选项，每个选项有 text 和 effect。
- 每个选项的 effect 必须清晰地说明该选择会对玩家状态产生什么影响（例如“生命值-10”、“获得物品”等等），必须**准确描述**该选项会带来的 `stats_changes` 和 `new_items`。
- `stats_changes` 和 `new_items` 必须与对应选项的 `effect` 描述**完全一致**。
- 玩家刚才选择了：「{user_choice}」
- **期望的变化提示（供参考，必须遵循）：** {expected_effect if expected_effect else '无'}
- 请严格按照上述期望变化来设置 stats_changes 和 new_items，如果期望变化为空则自行设计。
- 当前生成的选项必须与玩家刚才的选择（{user_choice}）逻辑连贯。
- 剧情合理有趣，根据选择产生分支。
- stats_changes 中的数值变化是整数增量（正数增加，负数减少），只修改已存在的属性。
- new_items 用于添加新物品，每个物品的 icon 必须从以下可用图标中选择：{icons_str}。如果没有合适图标，可以留空或使用默认图标。
- `new_items` 中的物品必须与所选选项的 `effect` 中明确提到的“获得”一致。
- 如果选项没有提到获得物品（例如“继续前进”），则 `new_items` 必须为空数组 `[]`。
- 如果剧情描述中自然出现了物品，但选项本身并未授予该物品，绝对不能放入 `new_items`。
- `used_items` 数组中每个对象包含 `"name"`，表示消耗的物品名称。如果消耗多个物品，就放多个对象。
- 消耗的物品名称必须与物品栏中已有的物品名称**完全匹配**。
- 如果你无法判断消耗什么物品，可以忽略该字段或使用空数组 `[]`。
- 如果游戏可能走向结局，可以只提供一个 "重新开始" 选项。
- 只输出 JSON，不要有任何额外解释。
"""
    for attempt in range(retry):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请继续游戏"}
                ],
                temperature=0.8,
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
            raw = response.choices[0].message.content
            fixed = repair_json(raw)
            data = json.loads(fixed)
            # 基本验证
            if "description" not in data or "options" not in data:
                raise ValueError("缺少必要字段")
            return data
        except Exception as e:
            if attempt == retry - 1:
                raise Exception(f"生成失败：{str(e)}")
            time.sleep(1)
    raise Exception("多次重试后仍失败")

def generate_opening(initial_prompt: str) -> dict:
    """根据用户的一句话描述生成开场"""
    
    system_prompt = f"""
你是一个互动小说游戏设计师。根据用户的一句话描述，生成一个完整的文字冒险游戏开场。
输出格式必须是严格的 JSON，结构如下：
{{
  "description": "开场剧情（100-150字）",
  "options": [
    {{"text": "选项1", "effect": "..."}},
    {{"text": "选项2", "effect": "..."}},
    {{"text": "选项3", "effect": "..."}}
  ],
  "initial_resources": {{"生命值": 100/100}}
}}
只输出 JSON。
用户描述：{initial_prompt}
"""
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": system_prompt}],
        temperature=0.7,
        max_tokens=1500,
        response_format={"type": "json_object"}
    )
    raw = response.choices[0].message.content
    fixed = repair_json(raw)
    return json.loads(fixed)
