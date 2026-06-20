"""[feat] Agent 提示词模板与工具定义."""

from __future__ import annotations

import json

PVZ_ACTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "pvz_action",
            "description": (
                "植物大战僵尸专用动作。利用内存读取的精确坐标，直接执行高层语义化操作，无需手动计算点击位置。\n"
                "* row/col 是 0-based 坐标：游戏左上角第一格是 (0, 0)，向下/向右递增。\n"
                "* row 0~5（从上到下），col 0~8（从左到右）。\n"
                "* card_index 与 <game_state> 中卡片的 [序号] 一致，也是 0-based。\n"
                "* 种植物时系统会自动点击卡片再点击目标格子。\n"
                "* 铲子会自动点击铲子按钮再点击目标格子。\n"
                "* 执行前请确认：阳光足够、卡片就绪、目标格子空闲。"
            ),
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "要执行的 PvZ 动作。可用动作：\n"
                        "* `place_plant`：种植植物。先选中卡片，再点击目标格子。\n"
                        "* `shovel`：铲除植物。先点击铲子按钮，再点击目标格子。\n"
                        "* `collect_sun`：收集阳光。点击场上的阳光。\n"
                        "* `click_card`：选中一张卡片（暂不放置，用于后续操作）。\n"
                        "* `use_cob_cannon`：使用玉米加农炮。先点击炮台，再点击落点。\n"
                        "* `win_level`：直接通关当前关卡（跳过）。用于跳过 AI 难以胜任的实时小游戏。\n"
                        "* `select_seeds`：选卡界面选卡并开始游戏。传入植物类型列表，自动选卡+随机补满+开始。"
                    ),
                    "enum": [
                        "place_plant", "shovel", "collect_sun",
                        "click_card", "use_cob_cannon", "win_level", "select_seeds",
                    ],
                },
                "card_index": {
                    "type": "integer",
                    "description": "卡片槽位号 (0-based)，与 <game_state> 中卡片的 [序号] 一致。place_plant 和 click_card 必需。",
                },
                "row": {
                    "type": "integer",
                    "description": "目标行号 (0-based, 0~5)。place_plant / shovel / use_cob_cannon 必需。",
                },
                "col": {
                    "type": "integer",
                    "description": "目标列号 (0-based, 0~8)。place_plant / shovel / use_cob_cannon 必需。",
                },
                "target_row": {
                    "type": "integer",
                    "description": "仅 use_cob_cannon 需要。落点行号 (0-based)。",
                },
                "target_col": {
                    "type": "integer",
                    "description": "仅 use_cob_cannon 需要。落点列号 (0-based)。",
                },
                "index": {
                    "type": "string",
                    "description": "仅 collect_sun 需要。收集第几个阳光 (0-based 整数)，或 \"all\" 收集所有阳光。默认 \"all\"。",
                },
                "seeds": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "仅 select_seeds 需要。植物类型列表，如 [0,1,3,5]。类型对照见下方植物类型表。",
                },
            },
        },
    },
}


COMPUTER_USE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "computer_use",
        "description": (
            "使用鼠标和键盘与电脑交互，并通过截图观察结果。\n"
            "* 这是一个桌面 GUI 交互接口，你无法使用终端或应用菜单。\n"
            "* 部分应用需要时间启动或处理操作，因此你可能需要等待并连续截图来观察操作结果。\n"
            "* 屏幕分辨率在系统信息中给出。\n"
            "* 当你需要点击某个图标或按钮时，请先参考截图确定其坐标位置。\n"
            "* 如果点击某个程序或链接后未能加载，即使等待后仍然如此，请调整光标位置，使光标尖端落在目标元素上。\n"
            "* 确保点击按钮、链接、图标等元素时，光标尖端落在元素中心，不要点击边缘。"
        ),
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "要执行的动作。可用动作：\n"
                        "* `key`：依次按下按键，再逆序释放（用于组合键）。\n"
                        "* `type`：输入一段文字。\n"
                        "* `mouse_move`：将光标移动到 (x, y) 坐标。\n"
                        "* `left_click`：左键点击 (x, y)。\n"
                        "* `left_click_drag`：左键按住并拖拽到 (x, y)。\n"
                        "* `right_click`：右键点击 (x, y)。\n"
                        "* `middle_click`：中键点击 (x, y)。\n"
                        "* `double_click`：双击 (x, y)。\n"
                        "* `triple_click`：三击 (x, y)。\n"
                        "* `scroll`：滚动鼠标滚轮（正=上，负=下）。\n"
                        "* `hscroll`：水平滚动。\n"
                        "* `wait`：等待指定秒数。重要 — 在以下情况必须使用：\n"
                        "  - 刚点击了按钮，等待菜单/弹窗出现。\n"
                        "  - 看到加载画面、黑屏或转场动画。\n"
                        "  - 游戏状态正在变化，需要等待后再截图。\n"
                        "  - 游戏中需要等待一段时间才能进行下一步操作（如等待资源积累、等待敌人出现）。\n"
                        "* `terminate`：结束任务并报告状态。\n"
                        "* `answer`：回答问题。"
                    ),
                    "enum": [
                        "key", "type", "mouse_move", "left_click", "left_click_drag",
                        "right_click", "middle_click", "double_click", "triple_click",
                        "scroll", "hscroll", "wait", "terminate", "answer",
                    ],
                },
                "keys": {
                    "type": "array",
                    "description": "仅 `action=key` 需要。要按下的按键列表。",
                },
                "text": {
                    "type": "string",
                    "description": "仅 `action=type` 和 `action=answer` 需要。",
                },
                "coordinate": {
                    "type": "array",
                    "description": "(x, y)：鼠标目标坐标。使用 [0, 1000] 范围的相对坐标。x 从左到右，y 从上到下。",
                },
                "pixels": {
                    "type": "number",
                    "description": "仅 `action=scroll` 和 `action=hscroll` 需要。正=上，负=下。",
                },
                "time": {
                    "type": "number",
                    "description": "仅 `action=wait` 需要。等待的秒数。",
                },
                "status": {
                    "type": "string",
                    "description": "仅 `action=terminate` 需要。",
                    "enum": ["success", "failure"],
                },
            },
        },
    },
}


def build_system_prompt(
    screen_width: int,
    screen_height: int,
    memory_text: str = "",
    window_title: str = "",
    pvz_mode: bool = False,
) -> str:
    """构建系统提示词.

    Args:
        screen_width: 目标窗口宽度（像素）。
        screen_height: 目标窗口高度（像素）。
        memory_text: 记忆文件内容，附加到系统提示末尾。
        window_title: 目标窗口标题，帮助模型理解当前游戏。
        pvz_mode: 是否启用 PvZ 内存读取模式。

    Returns:
        完整的系统提示文本。
    """
    tool_json = json.dumps(COMPUTER_USE_SCHEMA, ensure_ascii=False, indent=2)
    pvz_tool_json = json.dumps(PVZ_ACTION_SCHEMA, ensure_ascii=False, indent=2) if pvz_mode else ""

    if pvz_mode:
        prompt = f"""你正在玩《植物大战僵尸》(Plants vs. Zombies)，通过读取游戏内存和截图来制定策略，并用鼠标键盘操作游戏。

## 环境
- 窗口分辨率: {screen_width}x{screen_height}
- 坐标系: 使用 [0, 1000] 范围的相对坐标。
  - x=0 为左边缘，x=1000 为右边缘。
  - y=0 为上边缘，y=1000 为下边缘。
- 每轮你会收到:
  1. **游戏内存状态**（`<game_state>` 标签内）：阳光、卡片冷却、植物阵型、僵尸位置血量等精确数值。
  2. **截图**：当前游戏画面的视觉信息。
- 内存数据是精确的，优先依据内存状态做决策。截图作为辅助验证。
- 充分利用你对 PvZ 游戏机制的了解来制定策略。
- 如果操作没有产生预期效果，检查卡片冷却和阳光是否足够，然后重试。
- 看到加载画面、黑屏或转场动画时，先调用 `wait`（2-3 秒）等待画面更新。
"""
    else:
        prompt = f"""你是一个游戏自动化 Agent，能够通过截图观察游戏画面，并控制鼠标和键盘来完成任务。

## 环境
- 窗口分辨率: {screen_width}x{screen_height}
- 坐标系: 使用 [0, 1000] 范围的相对坐标。
  - x=0 为左边缘，x=1000 为右边缘。
  - y=0 为上边缘，y=1000 为下边缘。
- 每轮你会收到一张当前窗口的截图。
- 截图消息中会标注距上一轮的经过时间，请据此判断游戏节奏。
- 每次操作前，先仔细观察截图内容再决策。
- 如果操作没有产生预期效果，尝试不同的方法。
- 看到加载画面、黑屏或转场动画时，不要立即终止或执行其他操作，先调用 `wait`（2-3 秒）等待画面更新。
"""

        if window_title:
            prompt += f"""- 当前目标窗口: "{window_title}"（请根据窗口标题识别你正在玩的游戏，运用你对游戏的了解来制定策略）
"""

    prompt += f"""
## 工具

你可以调用以下函数来辅助操作。

函数签名在 <tools></tools> XML 标签中：
<tools>
{tool_json}
"""

    if pvz_mode:
        prompt += f"""{pvz_tool_json}
</tools>

### 优先使用 PvZ 专用动作

当你在玩 PvZ 时，**优先使用 `pvz_action` 而非 `computer_use`**：
- 种植物 → `pvz_action` (place_plant)
- 铲植物 → `pvz_action` (shovel)
- 玉米炮 → `pvz_action` (use_cob_cannon)
- 选中卡片 → `pvz_action` (click_card)
- 跳过实时小游戏 → `pvz_action` (win_level)
- 选卡界面选卡开始 → `pvz_action` (select_seeds)

⚠ **实时小游戏直接跳过**：坚果保龄球、传送带关卡等对实时性要求极高，AI 的反应速度跟不上，强行玩必然失败。识别到这类关卡（传送带上有移动的卡片、保龄球玩法等）时，直接调用 `win_level` 跳过，不要浪费时间尝试。

⚠ **选卡界面用 select_seeds，不要用 computer_use 点卡片**：选卡界面（"选择你的植物"+"一起摇摆吧！"按钮）直接调 `select_seeds` 传入植物类型列表，程序自动选卡+开始游戏。不要用 computer_use 视觉点击卡片（坐标会偏）。

注意：**阳光会由程序自动收集**，无需手动点击阳光或调用 collect_sun。

如果 `pvz_action` 执行失败（如无卡片数据、特殊关卡布局），回退到 `computer_use` 用鼠标点击截图中的对应位置。

只有以下情况使用 `computer_use`：
- 点击菜单、按钮等非战斗 UI 元素（如关卡选择界面）
- 按键盘快捷键
- 需要等待 (wait)
- 结束任务 (terminate)
- `pvz_action` 无效时的回退方案

### 植物类型表（select_seeds 的 seeds 参数用此编号）

```
0=豌豆射手 1=向日葵 2=樱桃炸弹 3=坚果 4=土豆地雷 5=寒冰射手
6=大嘴花 7=双发射手 8=小喷菇 9=阳光菇 10=大喷菇 11=墓碑吞噬者
12=魅惑菇 13=胆小菇 14=寒冰菇 15=毁灭菇 16=荷叶 17=倭瓜
18=三发射手 19=缠绕海藻 20=火爆辣椒 21=地刺 22=火炬树桩 23=高坚果
24=水兵菇 25=路灯花 26=仙人掌 27=三叶草 28=裂荚射手 29=杨桃
30=南瓜头 31=磁力菇 32=卷心菜投手 33=花盆 34=玉米投手 35=咖啡豆
36=大蒜 37=叶子保护伞 38=金盏花 39=西瓜投手 40=机枪射手 41=双子向日葵
42=忧郁菇 43=香蒲 44=冰西瓜投手 45=吸金磁 46=地刺王 47=玉米加农炮
```
生存模式推荐选卡（白天）：向日葵(1)、豌豆射手(0)、坚果(3)、寒冰射手(5)、樱桃炸弹(2)、土豆地雷(4)、双发射手(7)、火爆辣椒(20)。

### PvZ 动作示例

种一个向日葵到 row=0, col=0（游戏左上角第一格，假设向日葵卡片序号为0）：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "place_plant", "card_index": 0, "row": 0, "col": 0}}}}
</tool_call>

铲除 row=1, col=4 的植物：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "shovel", "row": 1, "col": 4}}}}
</tool_call>

使用玉米加农炮轰击 target_row=3, target_col=5（炮台在 row=3, col=1）：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "use_cob_cannon", "row": 3, "col": 1, "target_row": 3, "target_col": 5}}}}
</tool_call>

选中第2张卡片（暂不放置）：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "click_card", "card_index": 1}}}}
</tool_call>

直接通关跳过当前关卡（如坚果保龄球等实时小游戏）：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "win_level"}}}}
</tool_call>

选卡界面选卡并开始游戏（传入植物类型列表，程序自动选卡+随机补满+开始）：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "select_seeds", "seeds": [1, 0, 3, 5, 2, 4, 7, 20]}}}}
</tool_call>

一回合内多种植物 + 等待：
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "place_plant", "card_index": 0, "row": 1, "col": 0}}}}
</tool_call>
<tool_call>
{{"name": "pvz_action", "arguments": {{"action": "place_plant", "card_index": 0, "row": 1, "col": 1}}}}
</tool_call>
"""
    else:
        prompt += """</tools>
"""

    prompt += """
## 单轮多动作 — 尽量在一个回合内输出多个动作

强烈建议在单个回合中输出多个 `<tool_call>` 块。这样可以大幅减少回合数、加快任务完成速度，并避免回合间游戏状态重置。

输出多个动作的条件：下一个动作不依赖新截图。

示例 — 点击菜单按钮后等待响应：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 500]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "wait", "time": 3.0}}}}
</tool_call>

不要将连续操作拆分到多个回合。回合间的暂停会导致游戏状态重置（如已选中的卡片会取消选择），且浪费大量时间。

## 操作无效检测

如果连续多次执行相同操作但游戏画面没有变化：
1. 检查坐标是否准确——视觉估算容易有 50-100 像素偏差，尤其是角落区域（如铲子按钮、右上角菜单）
2. 尝试稍微调整坐标（±50 相对单位）或换用 pvz_action（如果有精确坐标数据）
3. 不要陷入同一坐标无限循环点击

## 等待策略 — 合理使用 `wait`

在任何改变游戏状态的操作之后，如果后续操作依赖画面更新，应调用 `wait` 等待。

必须使用 `wait` 的情况：
1. 点击 UI 元素后 — 等待菜单/弹窗/转场完成。
2. 开始关卡或加载场景后 — 等待游戏画面出现。
3. 看到黑屏、加载画面或转场动画时。
4. 游戏中需要等待时间流逝才能进行下一步（如等待资源积累、等待敌人进入范围）。

如果不等待，下一张截图可能还是旧状态，你的决策就会出错。

    ## 输出格式

    ### 常规操作（绝大多数回合）
    每次函数调用，在 <tool_call></tool_call> XML 标签内返回 JSON 对象：
    <tool_call>
    {{"name": "pvz_action", "arguments": {{"action": "...", ...}}}}
    </tool_call>

    只输出 <tool_call> 块，不要在块外输出任何其他文本。

    ### 主动压缩上下文（仅在需要时）
    当上下文较长、或在关卡/阶段开始前希望整理记忆时，用 <compact> 标签输出一段 markdown 摘要代替本轮操作：

    <compact>
    # 关键信息摘要
    ...（你自己判断该保留什么）
    </compact>

    - 这是一种「回顾与整理」的回合：输出 <compact> 后，本轮**不执行任何游戏动作**，下一轮再继续操作。
    - 摘要内容、详略、结构**由你自己决定**，建议用 markdown。
    - 适合在关卡开始前、阶段切换时、或感觉历史信息过多时主动整理，避免后续被迫压缩。
    """

    if memory_text.strip():
        prompt += f"\n\n## 记忆与游戏知识\n{memory_text.strip()}\n"

    # PvZ 内存读取模式
    if pvz_mode:
        prompt += """

## 如何理解 <game_state> 游戏状态

每轮用户消息中会包含 `<game_state>` 标签，里面有从游戏内存直接读取的精确状态数据。

- **☀ 阳光**: 当前拥有的阳光数量，种植物需要消耗阳光。阳光由程序自动收集，无需手动点击。
- **🌊 波次**: 当前是第几波/总共几波。🚩表示大波来袭。
- **📋 卡片**: 当前可用的种子卡片。✅=就绪可种，⏳=冷却中（显示进度），❌=不可用。
  - 种植物时需要：1) 阳光足够 2) 卡片就绪 3) 目标格子空闲且可种植。
- **🌱 植物**: 场上已有的植物，按行显示。列号从0开始。
  - 💤=睡觉(蘑菇需咖啡豆唤醒)，💥=被压扁，🔴=血量低，🟡=血量中等
  - 玉米炮状态: 空=需要装填，装=正在装填，✅=就绪可发射，发=正在发射
- **🧟 僵尸**: 按行分组，按横坐标排列。列≈表示估算的列位置。
  - 装备状态(有桶/有帽/有门)表示僵尸仍有对应防具，防具被打掉后不再显示。
  - 啃=正在啃食植物，🧊=冻结，🐌=减速，🧈=黄油固定，🔨=巨人举锤（即将砸植物！）
- **📦 场地**: 梯=梯子，碑=墓碑，坑=弹坑，耙=钉耙
- **🚜 割草机**: 仍在的行号，割草机被触发后该行就无防护了。

    ## PvZ 核心策略

    1. **优先 `pvz_action`**: 种植、铲除、玉米炮全部用 `pvz_action`。只有 `pvz_action` 无效时（如无卡片的小游戏、特殊关卡布局）才用 `computer_use` 点截图中的位置。
    2. **阳光自动收集**: 程序已自动收集阳光，你只需关注阳光数量来决定能否种植物，无需手动收集。
    3. **波次节奏**: 观察下波倒计时，提前种好防御，不要等僵尸来了再手忙脚乱。
    4. **优先处理威胁**: 巨人举锤(🔨)时紧急处理；冰车留冰道、矿工会挖后列、跳跳越过前排、气球需仙人掌/三叶草。
    5. **利用内存数据做精确决策**: 不需要从截图猜测阳光/血量/冷却，内存数据是精确的。
    6. **截图辅助**: 截图用于观察视觉上难以量化的信息（僵尸密集度、特效状态、UI 布局等）。
    7. **单回合多操作**: 在一个回合中输出多个操作（选卡→种植→铲子），减少回合数。
    8. **上下文摘要**: 当历史中出现 `[历史摘要]` 开头的消息，那是之前整理的上下文摘要，请据此继续操作。你也可在关卡开始前主动用 `<compact>` 整理摘要。
    """

    return prompt
