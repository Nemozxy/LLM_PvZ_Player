"""[feat] Agent 提示词模板与工具定义."""

from __future__ import annotations

import json

COMPUTER_USE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "computer_use",
        "description": (
            "Use a mouse and keyboard to interact with a computer, and take screenshots.\n"
            "* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu.\n"
            "* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.\n"
            "* The screen's resolution is described in the system info.\n"
            "* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.\n"
            "* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.\n"
            "* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges."
        ),
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The action to perform. Available actions:\n"
                        "* `key`: Press keys in order, then release in reverse order.\n"
                        "* `type`: Type a string of text.\n"
                        "* `mouse_move`: Move cursor to (x, y) coordinate.\n"
                        "* `left_click`: Left-click at (x, y).\n"
                        "* `left_click_drag`: Click and drag to (x, y).\n"
                        "* `right_click`: Right-click at (x, y).\n"
                        "* `middle_click`: Middle-click at (x, y).\n"
                        "* `double_click`: Double-click at (x, y).\n"
                        "* `triple_click`: Triple-click at (x, y).\n"
                        "* `scroll`: Scroll mouse wheel (positive=up, negative=down).\n"
                        "* `hscroll`: Horizontal scroll.\n"
                        "* `wait`: Wait specified seconds. IMPORTANT — use this when:\n"
                "  - You just clicked a button and expect a menu/popup to appear.\n"
                "  - You see a loading screen, black screen, or transition animation.\n"
                "  - The game state is changing and you need to wait before the next screenshot.\n"
                "  - After terminating, wait a moment to ensure the game finishes processing.\n"
                        "* `terminate`: End task with status.\n"
                        "* `answer`: Answer a question."
                    ),
                    "enum": [
                        "key", "type", "mouse_move", "left_click", "left_click_drag",
                        "right_click", "middle_click", "double_click", "triple_click",
                        "scroll", "hscroll", "wait", "terminate", "answer",
                    ],
                },
                "keys": {
                    "type": "array",
                    "description": "Required only by `action=key`. List of keys to press.",
                },
                "text": {
                    "type": "string",
                    "description": "Required only by `action=type` and `action=answer`.",
                },
                "coordinate": {
                    "type": "array",
                    "description": "(x, y): Coordinates to move the mouse to. Use relative coordinates in range [0, 1000]. x is pixels from left, y is pixels from top.",
                },
                "pixels": {
                    "type": "number",
                    "description": "Required only by `action=scroll` and `action=hscroll`. Positive=up, negative=down.",
                },
                "time": {
                    "type": "number",
                    "description": "Required only by `action=wait`. Seconds to wait.",
                },
                "status": {
                    "type": "string",
                    "description": "Required only by `action=terminate`.",
                    "enum": ["success", "failure"],
                },
            },
        },
    },
}


def build_system_prompt(screen_width: int, screen_height: int, memory_text: str = "") -> str:
    """构建系统提示词.

    Args:
        screen_width: 目标窗口宽度（像素）。
        screen_height: 目标窗口高度（像素）。
        memory_text: 记忆文件内容，附加到系统提示末尾。

    Returns:
        完整的系统提示文本。
    """
    tool_json = json.dumps(COMPUTER_USE_SCHEMA, ensure_ascii=False, indent=2)

    prompt = f"""You are a GUI automation agent. You can see the screen and control the mouse and keyboard to complete tasks.

## Environment
- Screen resolution: {screen_width}x{screen_height}
- Coordinate system: When specifying coordinates, use relative values in the range [0, 1000].
  - x=0 is the left edge, x=1000 is the right edge.
  - y=0 is the top edge, y=1000 is the bottom edge.
- You will be provided with screenshots of the target window.
- Before each action, pause and think about what you see in the screenshot.
- If an action does not produce the expected result, try a different approach.
- When you see a loading screen, black screen, or the game is clearly in transition, DO NOT terminate or take another action immediately. Call `wait` first (e.g. 2-3 seconds) and let the next screenshot show the updated state.

## Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{tool_json}
</tools>

## Multi-Action in One Turn — OUTPUT MULTIPLE ACTIONS TO MAXIMIZE EFFICIENCY

You are STRONGLY ENCOURAGED to output MULTIPLE `<tool_call>` blocks in a SINGLE turn whenever possible. This is the DEFAULT and PREFERRED mode of operation. Doing so dramatically reduces the number of rounds, speeds up task completion, and prevents game state resets between turns.

Output multiple actions in ONE turn when:
1. You need to click several buttons in sequence (e.g. open menu → click option → confirm).
2. You need to perform a drag-and-drop (e.g. pick up a card → drag to grid → release).
3. You need to click multiple independent UI elements that don't require waiting between them.
4. Any situation where the next action does NOT depend on a new screenshot.

Example — navigating a menu:
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 300]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 400]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [600, 500]}}}}
</tool_call>

Example — planting a unit:
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [200, 800]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 400]}}}}
</tool_call>

DO NOT split sequences across multiple turns. The pause between turns will reset the game state (e.g. a held item will be lost) and waste time.

## Waiting Strategy — USE `wait` WHENEVER NEEDED

After ANY action that changes the game state (clicking a button, opening a menu, starting a level, etc.), you MUST call `wait` BEFORE requesting the next screenshot or taking further action. The game needs time to render the new state.

You MUST use `wait` in these situations:
1. After clicking any UI element — wait for the menu/popup/transition to finish.
2. After starting a level or loading a scene — wait for the gameplay screen to appear.
3. When you see a black screen, loading screen, or transition animation.
4. After terminating — wait briefly to ensure the game finishes processing.

Example — starting a level:
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [500, 500]}}}}
</tool_call>
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "wait", "time": 3.0}}}}
</tool_call>

If you do NOT wait, the next screenshot will show the OLD state and your plan will be wrong.

## Output Format

For each function call, return a JSON object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "...", ...}}}}
</tool_call>

You must ONLY output the <tool_call> block(s). Do not include any other text outside the block.
"""

    if memory_text.strip():
        prompt += f"\n\n## Memory\n{memory_text.strip()}\n"

    return prompt
