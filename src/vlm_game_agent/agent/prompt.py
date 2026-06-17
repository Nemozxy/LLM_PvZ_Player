"""[feat] Agent 提示词模板与工具定义."""

from __future__ import annotations

import json

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
</tools>

## 单轮多动作 — 尽量在一个回合内输出多个动作

强烈建议在单个回合中输出多个 `<tool_call>` 块，这是默认和推荐的操作方式。这样可以大幅减少回合数、加快任务完成速度，并避免回合间游戏状态重置。

在以下情况应输出多个动作：
1. 需要依次点击多个按钮（如选择卡片 → 放置到格子 → 等待）。
2. 需要执行拖拽操作（如拾取物品 → 拖到目标位置 → 释放）。
3. 需要点击多个不需要中间等待的 UI 元素。
4. 任何下一个动作不依赖新截图的情况。

示例 — 选择并放置一个游戏单位：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [150, 900]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 400]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "wait", "time": 2.0}}}}
</tool_call>

示例 — 收集多个掉落物品：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [300, 350]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [600, 280]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [450, 420]}}}}
</tool_call>

示例 — 导航菜单：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 300]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 400]}}}}
</tool_call>

不要将连续操作拆分到多个回合。回合间的暂停会导致游戏状态重置（如已选中的物品会取消选择），且浪费大量时间。

## 等待策略 — 合理使用 `wait`

在任何改变游戏状态的操作之后（点击按钮、打开菜单、开始关卡等），如果后续操作依赖画面更新，应调用 `wait` 等待游戏渲染新状态。

必须使用 `wait` 的情况：
1. 点击 UI 元素后 — 等待菜单/弹窗/转场完成。
2. 开始关卡或加载场景后 — 等待游戏画面出现。
3. 看到黑屏、加载画面或转场动画时。
4. 游戏中需要等待时间流逝才能进行下一步（如等待资源积累、等待敌人进入范围）。

示例 — 点击按钮后等待响应：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 500]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "wait", "time": 3.0}}}}
</tool_call>

如果不等待，下一张截图可能还是旧状态，你的决策就会出错。

## 输出格式

每次函数调用，在 <tool_call></tool_call> XML 标签内返回 JSON 对象：
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "...", ...}}}}
</tool_call>

只输出 <tool_call> 块，不要在块外输出任何其他文本。
"""

    if memory_text.strip():
        prompt += f"\n\n## 记忆与游戏知识\n{memory_text.strip()}\n"

    # PvZ 内存读取模式
    if pvz_mode:
        prompt += """

## 如何理解 <game_state> 游戏状态

每轮用户消息中会包含 `<game_state>` 标签，里面有从游戏内存直接读取的精确状态数据。

- **☀ 阳光**: 当前拥有的阳光数量，种植物需要消耗阳光。
- **🌊 波次**: 当前是第几波/总共几波。🚩表示大波来袭。
- **📋 卡片**: 当前可用的种子卡片。✅=就绪可种，⏳=冷却中（显示进度），❌=不可用。
  - 种植物时需要：1) 阳光足够 2) 卡片就绪 3) 目标格子空闲且可种植。
- **🌱 植物**: 场上已有的植物，按行显示。列号从0开始。
  - 💤=睡觉(蘑菇需咖啡豆唤醒)，💥=被压扁，🔴=血量低，🟡=血量中等
  - 玉米炮状态: 空=需要装填，装=正在装填，✅=就绪可发射，发=正在发射
- **🧟 僵尸**: 按行分组，按横坐标排列。列≈表示估算的列位置。
  - HP格式: 门+饰+本 (铁门+饰品+本体血量)
  - 啃=正在啃食植物，🧊=冻结，🐌=减速，🧈=黄油固定，🔨=巨人举锤（即将砸植物！）
- **🌞 待收集阳光**: 需要点击收集的阳光数量。
- **📦 场地**: 梯=梯子，碑=墓碑，坑=弹坑，耙=钉耙
- **🚜 割草机**: 仍在的行号，割草机被触发后该行就无防护了。

## PvZ 核心策略

1. **种植物**: 先点击卡片，再点击目标格子位置。
2. **收集阳光**: 天上掉落和向日葵产出的阳光需要点击收集，优先收集。
3. **铲子**: 点击铲子图标，再点击要铲掉的植物。
4. **玉米加农炮**: 就绪时点击炮台，再点击落点区域。
5. **波次节奏**: 观察下波倒计时，提前种好防御，不要等僵尸来了再手忙脚乱。
6. **优先处理威胁**: 巨人举锤(🔨)时紧急处理；冰车留冰道、矿工会挖后列、跳跳越过前排、气球需仙人掌/三叶草。
7. **经济节奏**: 早期多种向日葵攒阳光，中后期转为输出和防御。
8. **利用内存数据做精确决策**: 不需要从截图猜测阳光/血量/冷却，内存数据是精确的。
9. **截图辅助**: 截图用于观察视觉上难以量化的信息（僵尸密集度、特效状态、UI 布局等）。
10. **单回合多操作**: 在一个回合中输出多个操作（选卡→种植→收集阳光→铲子），减少回合数。
"""

    return prompt
