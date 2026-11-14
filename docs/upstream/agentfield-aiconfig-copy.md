# AgentField への報告の下書き（未送信・送るかはユーザーが決める）

## Title
`AIConfig.copy()` uses pydantic's deprecated `BaseModel.copy`, so every `app.ai()` call emits PydanticDeprecatedSince20

## Body
Version: agentfield 0.1.145 (PyPI), pydantic 2.x, Python 3.13.

`agentfield/agent_ai.py` calls `self.agent.ai_config.copy(deep=True)` on every `app.ai()` call, and `AIConfig.copy` in `agentfield/types.py` forwards to `super().copy(...)`, which pydantic 2 marks as deprecated:

```
PydanticDeprecatedSince20: The `copy` method is deprecated; use `model_copy` instead.
```

Projects that run their tests with `-W error::DeprecationWarning` have to ignore this warning to call `app.ai()`.

Suggested fix: implement `AIConfig.copy` with `self.model_copy(update=update, deep=deep)` (and handle `include`/`exclude` via `model_dump` if they are still needed), or call `model_copy(deep=True)` directly in `agent_ai.py`.

## ClientOps 側の扱い（11/14 ユーザー決定）
- `agent/tests/test_anthropic_contract.py` だけで、この警告 1 件を無視している。ほかの警告は今までどおりエラー
- AgentField で直ったら、無視の指定を外す
