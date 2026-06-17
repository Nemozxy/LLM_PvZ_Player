"""模拟 trim_history 行为"""
from vlm_game_agent.agent.core import GameAgent

agent = GameAgent.__new__(GameAgent)
agent.max_history_turns = 6
agent._history = [{'role': 'system', 'content': 'fake system prompt'}]

# 模拟 9 轮: 每轮 1 assistant + 1 user 执行反馈
for turn in range(1, 10):
    agent._history.append({'role': 'assistant', 'content': f'<tool_call>round_{turn}</tool_call>'})
    agent._trim_history()
    agent._history.append({'role': 'user', 'content': f'[执行结果] round_{turn}'})
    agent._trim_history()

print(f'max_history_turns={agent.max_history_turns}, max_msgs={agent.max_history_turns * 3}')
print(f'总消息: {len(agent._history)}')
for msg in agent._history:
    role = msg['role']
    content = str(msg['content'])[:60]
    print(f'  [{role}] {content}')
