# Claude（Anthropic）の呼び方と返る値

確かめた日：2026-11-14
版：PyPI `agentfield` 0.1.145・`litellm` 1.109.1（どちらも 11/14 時点の PyPI の最新。LiteLLM は agentfield が入れたもの）、Python 3.13
モデル：Claude Opus 5.5（`claude-opus-5-5`）だけ。effort は medium。
API キー：まだ無い。本物の Anthropic とは突き合わせていない。代わりに「本物の `app.ai()` → 本物の LiteLLM → 手元の偽 Anthropic サーバ」をつなぎ、実際に出ていく HTTP と `app.ai()` の戻り値を記録した（`agent/tests/test_anthropic_contract.py`）。

## 何を正にしたか
1. 入れたソース（呼び方・戻り値・例外はここが正）
   - agentfield：`agent.py`（`Agent.ai`）、`agent_ai.py`（`AgentAI.ai` の本体・再試行）、`types.py`（`AIConfig`）、`multimodal_response.py`（戻り値・使用量・費用）、`cost_tracker.py`、`rate_limiter.py`
   - LiteLLM：`llms/anthropic/chat/transformation.py`（要求と応答の変換）、`llms/anthropic/common_utils.py`（鍵と送り先）、`litellm_core_utils/core_helpers.py`（stop_reason → finish_reason）、`litellm_core_utils/exception_mapping_utils.py`（HTTP エラー → 例外）、`model_prices_and_context_window_backup.json`（料金表）
2. 公式ドキュメント（2026-11-14 に取得）
   - Create a Message（応答の欄）：https://platform.claude.com/docs/en/api/messages/create
   - Structured outputs：https://platform.claude.com/docs/en/build-with-claude/structured-outputs
   - Refusals and fallback：https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback
   - Errors：https://platform.claude.com/docs/en/api/errors
   - Pricing：https://platform.claude.com/docs/en/about-claude/pricing
   - Claude Code 同梱の claude-api スキル（2.1.323、モデル表は 2026-11-03 時点）の Claude Opus 5.5 の項：考える（thinking）は止められない、effort の既定は medium、`tool_choice` の any / tool は 400
3. 手元の実験（キー不要）：偽 Anthropic サーバ `agent/tests/anthropic_stub.py`

確かめられなかったことには「推定」と書いた。

## 結論
`app.ai()` は、そのまま（`schema=` で）呼ぶと Opus 5.5 の構造化出力を正しく扱えない。effort は渡せる。根拠は次の3つで、どれもテストで再現している。

| 問題 | 何が起きるか | 根拠 |
|---|---|---|
| `schema=` は古い書き方を送る | LiteLLM が `output_format`（deprecated）を送り、ベータヘッダー `structured-outputs-2025-11-13` を付ける。公式：「`output_format` は deprecated。使うならこのヘッダーが要る。無いと 400」。決まりごとの「deprecated を使わない」に反する | `test_evidence_app_ai_with_schema_sends_the_deprecated_output_format_and_hides_refusals`、公式 Structured outputs の「Migrating from the beta」 |
| `schema=` は止まった理由を消す | 戻り値が pydantic のオブジェクトだけになり、stop_reason・拒否の理由・使用量が読めない。拒否や max_tokens 切れは「JSON が読めない」扱いになり、`app.ai()` が同じ依頼を計3回送ってから `ValueError("Could not parse structured response: ")` を投げる（3回分の使用量が記録される） | 同上のテスト。`agent_ai.py` の `max_parse_retries = 2` |
| 入力上限を知らない | `AIConfig` に `max_input_tokens` が無いと、`app.ai()` は Opus 5.5 の入力上限を 10,192 トークンと見なし、それを超えるプロンプトを LiteLLM の `trim_messages` で黙って切る。6,000 行の要素ツリーが半分以下になって送られた | `test_evidence_without_max_input_tokens_app_ai_silently_trims_a_long_tree`、`agent_ai.py` の「Strategy 4: Conservative fallback」 |

最小の直し（プロバイダも SDK も変えない）。`agent/clientops/claude_call.py` に置いた。
1. `schema=` と `reasoning_effort=` は使わない。`output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ...}}` を `app.ai()` の追加の引数で渡す。LiteLLM はこれを本文の `output_config` にそのまま入れる（実験で確認）。`app.ai()` は `MultimodalResponse` を返すので、本文・止まった理由・拒否の理由・使用量・費用が全部読める。JSON の形の検査は 012 で自前で行う。
2. エージェントは `Agent(..., ai_config=claude_ai_config())` で作る。中身は `AIConfig(model="anthropic/claude-opus-5-5", max_input_tokens=1_000_000)`。`fallback_models` は空のまま（別のモデルへ逃げない）。
3. エージェントを起動するときに環境変数 `LITELLM_LOCAL_MODEL_COST_MAP=True` を入れる。LiteLLM は import のたびに GitHub から料金表を取りに行く（`get_model_cost_map`、5 秒待つ）。これを止め、入れた版の料金表（Opus 5.5 は公式と同じ値。下の 6）を使う。Anthropic 以外へ通信しない。

## 1. 送る中身（`app.ai()` 経由）

```python
from clientops.claude_call import claude_ai_config, element_choice_call

app = Agent(node_id="clientops", ..., ai_config=claude_ai_config())
response = await app.ai(**element_choice_call(system=instructions, user=masked_tree_and_goal))
```

`element_choice_call(system, user)` が返す引数

| 引数 | 値 | 理由 |
|---|---|---|
| `system` | 指示文（固定） | |
| `user` | 伏せた要素ツリーと目的 | 値やログイン情報は入れない（決まったこと 10） |
| `model` | `"anthropic/claude-opus-5-5"` | 「プロバイダ/モデル」で書かないと `app.ai()` が `ValueError("Invalid model spec ...")` |
| `max_tokens` | `16000` | 考えた分も `max_tokens` に入る。ストリーミング無しで時間切れにならない目安（claude-api スキル） |
| `output_config` | `{"effort": "medium", "format": {"type": "json_schema", "schema": ELEMENT_CHOICE_SCHEMA}}` | 公式の正式な書き方 |

送らないもの：`temperature`・`top_p`・`top_k`（Opus 5.5 は 400。`AIConfig` の既定は None で送られないことを確認）、`thinking`（送らなくても adaptive で考える。`disabled` と `budget_tokens` は 400）、`tools`・`tool_choice`（any / tool は 400）、`schema`・`response_format`・`stream`、`fallbacks`（下の 7）。

返させる JSON（`ELEMENT_CHOICE_SCHEMA`）

```json
{
  "type": "object",
  "properties": {
    "element_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "action": {"type": "string", "enum": ["click", "fill", "none"]},
    "confident": {"type": "boolean"}
  },
  "required": ["element_id", "action", "confident"],
  "additionalProperties": false
}
```

- 11/14 に「要素がこのページに無い」と答えられる形を足した（本セッションの決定）。`element_id: null` と `action: "none"` の組だけが「無い」。使ってよいのは、手順が「無いこともある」と宣言した要素だけ（今は表を読むときの「次のページ」リンク。あとでクッキーの案内など）。確信ありの none なら「1 ページだけ／することなし」として続ける。必要な要素への none は使えない答え（1 回だけ聞き直し、だめなら人へ）。確信なしの none は今までどおり人へ。null と none の組み合わせが崩れた答え（null で click、ID ありで none など）は形違いとして弾く（スキーマでは表さず、012 のコードで検査）
- none を勧めるのは手順のコードが「無いこともある」と判断したときだけ。表を読む手順では、ページが続く兆し（「Showing 1–20 of 42」のような数え方や 1 ページの件数ちょうどの行数）があれば none を勧めず、none が返っても使えない答えにする（012）
- null は `anyOf` で書いた。構造化出力の対応一覧（claude-api スキルの Structured Outputs、公式の JSON Schema の対応）に `anyOf` と `null` 型がある。`"type": ["string", "null"]` の書き方は一覧に無いので使わない
- URL・送り先・入力する値・考えの欄は無い。`additionalProperties: false` なので、これ以外の鍵は返せない（公式）。
- 考えた中身を書かせる欄（reasoning など）を足すと `reasoning_extraction` で拒否されることがある（公式 Refusals）。足さない。
- 任意の欄は 0 個（上限 24）、union は `element_id` の 1 個（上限 16）。
- enum の大文字・小文字は保証されない（公式「Enum value casing」）。012 では小文字にそろえて比べる。
- 実際に出ていく本文（見本 `request-element-choice.json`）は、本物の LiteLLM がこのスキーマをそのまま `output_config.format.schema` に入れることを偽サーバで確かめて記録し直した（11/14、`anyOf` もそのまま。ヘッダーとほかの欄は前と同じ）
- 構造化出力を使うと、API が書式の説明をシステムプロンプトに自動で足す。その分入力トークンが少し増える（公式）。スキーマは使った最後から 24 時間キャッシュされ、初回だけコンパイルで遅くなる（公式）。

実際に出ていく HTTP（確認済み：偽サーバで記録。見本 `request-element-choice.json`）

```http
POST https://api.anthropic.com/v1/messages
x-api-key: $ANTHROPIC_API_KEY
anthropic-version: 2023-06-01
anthropic-beta: structured-outputs-2025-11-13
content-type: application/json

{"model": "claude-opus-5-5",
 "system": [{"type": "text", "text": "<system>"}],
 "messages": [{"role": "user", "content": [{"type": "text", "text": "<user>"}]}],
 "max_tokens": 16000,
 "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": {...}}}}
```

- 鍵と送り先は LiteLLM が環境変数から読む：`ANTHROPIC_API_KEY`（`x-api-key` で送る）、送り先を変えるときだけ `ANTHROPIC_BASE_URL`（または `ANTHROPIC_API_BASE`）。`ANTHROPIC_API_KEY` が無くて `ANTHROPIC_AUTH_TOKEN` があると Bearer で送るので、`ANTHROPIC_AUTH_TOKEN` は置かない（`common_utils.py`）。
- `anthropic-beta: structured-outputs-2025-11-13` は、LiteLLM が `output_config.format` を見て自動で付ける。正式な `output_config.format` にはヘッダーは要らない。このベータ値は `output_format` 用としてまだ有効と公式にあるので、付いていても 400 にはならないと推定。
- `anthropic-version: 2023-06-01` は LiteLLM の既定。公式の例は `2026-10-21`。2023-06-01 のままで `output_config` が通るのは推定（エラー表に 2023-06-01 の挙動が今も書かれている）。
- `thinking` を送らないので、Opus 5.5 は adaptive で考え、thinking ブロックの中身は空で返る（display の既定は omitted）。考えた分も出力トークンとして課金される。

`app.ai()` の中の再試行と時間切れ（確認済み：ソース）
- 時間切れ：1回 120 秒（`async_config.llm_call_timeout`）。その2倍で asyncio の安全網が切り、接続を作り直して最大2回やり直す（`AGENTFIELD_AI_TIMEOUT_RETRIES`、既定 2）。
- 429・5xx・接続エラー：同じ仕組みで最大2回（2 秒・4 秒待つ）。さらに 429 は agentfield の `StatelessRateLimiter` が最大5回（0.5 秒〜30 秒、±25%）。連続5回で 30 秒止まる。
- 400・401 などはやり直さない。
- だから 004 の「Claude を呼ぶのは1回だけ」は、429・5xx のときは Anthropic に届く依頼が1回とは限らない。

## 2. 送る中身（SDK で直接。004 の予備）
004 の `scripts/check.ts` は TypeScript なので `@anthropic-ai/sdk` を使う（まだ入れていない。入れるときは最新版）。本文は上と同じで、ベータヘッダーは付かない。

```ts
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic();
const message = await client.messages.create({
  model: "claude-opus-5-5",
  max_tokens: 16000,
  system: "<system>",
  messages: [{ role: "user", content: "<user>" }],
  output_config: { effort: "medium", format: { type: "json_schema", schema: ELEMENT_CHOICE_SCHEMA } },
});
```

- 返るのは下の 3 の `Message` そのもの。`message.stop_reason` を見てから `content` を読む。
- 失敗は `Anthropic.BadRequestError`（400）・`AuthenticationError`（401）・`RateLimitError`（429）・`InternalServerError`（5xx、529 も）・`APIConnectionError` の順に分けて受ける。SDK は 408・409・429・5xx・接続エラーを既定で2回やり直す（公式 Errors）。
- request-id は `message._request_id`。

## 3. 返る値：Messages API の応答（確認済み：公式 Create a Message の Returns）

| 欄 | 型 | ClientOps での使い方 |
|---|---|---|
| `id` | string（`msg_...`） | 記録だけ |
| `type` | `"message"` | |
| `role` | `"assistant"` | |
| `model` | string（`"claude-opus-5-5"`） | 固定されているかの確認 |
| `content` | ブロックの配列 | 下の表 |
| `stop_reason` | 下の7つのどれか。ストリーミング無しなら必ず値がある | 先にこれを見る |
| `stop_sequence` | string または null | 使わない |
| `stop_details` | 拒否のときだけ object、ほかは null | 拒否の分類 |
| `usage` | object | 費用 |
| `container`・`diagnostics` | object または null | 使わない。見本では省いた（本物で null として入ってくるかは推定） |

`content` のブロック（このプロジェクトで来るもの）
- `{"type": "thinking", "thinking": "", "signature": "..."}`：考えた跡。display が omitted なので中身は空。signature はそのまま扱う（会話を続けないので返送はしない）。
- `{"type": "redacted_thinking", "data": "..."}`：考えの一部が安全のため伏せられたとき。
- `{"type": "text", "text": "<JSON>", "citations": null}`：答えの JSON。構造化出力では text ブロックに入る（公式）。`citations` は見本では省いた。

`stop_reason` の値：`end_turn`（ふつうに終わった）、`max_tokens`（`max_tokens` に達した。JSON は途中で切れていて形が合わない）、`stop_sequence`、`tool_use`、`pause_turn`、`refusal`（拒否）、`model_context_window_exceeded`。ClientOps で来るのは end_turn・max_tokens・refusal の3つ（推定）。

`stop_details`（拒否のときだけ）

| 欄 | 値 |
|---|---|
| `type` | `"refusal"` |
| `category` | `"cyber"`・`"bio"`・`"frontier_llm"`・`"reasoning_extraction"`・`"general_harms"`・null（名前のある分類に当たらないとき。null は正常な値） |
| `explanation` | 人向けの説明。文面は変わるので読み取りに使わない。null もある |
| `recommended_model` | `fallbacks` を送ったときだけ。ClientOps は送らない |

拒否は HTTP 200 で返る。出力の前に拒否されると `content` は空、途中で拒否されると途中までの出力が付くので捨てる。拒否の判定は `stop_reason` で行い、`stop_details` は参考にとどめる（公式）。

`usage`

| 欄 | 型 | 意味 |
|---|---|---|
| `input_tokens` | number | キャッシュを使わなかった入力 |
| `cache_creation_input_tokens` | number または null | キャッシュに書いた入力 |
| `cache_read_input_tokens` | number または null | キャッシュから読んだ入力 |
| `cache_creation` | `{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}` または null | 書いたキャッシュの内訳（5 分・1 時間） |
| `output_tokens` | number | 出力。考えた分も入る。課金の正 |
| `output_tokens_details` | `{thinking_tokens}` または null | 出力のうち考えた分（内訳。`output_tokens` 以下） |
| `server_tool_use` | object または null | 使わない |
| `service_tier` | `"standard"`・`"priority"`・`"batch"` または null | |
| `inference_geo` | string または null | 推論した地域。ClientOps は指定しない（`global`） |
| `speed` | `"standard"`・`"fast"` または null | fast mode は使わない |

入力の合計 = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`（公式）。

## 4. `app.ai()` が返すもの（確認済み：実験。見本 `app-ai-returns.json`）

`agentfield.multimodal_response.MultimodalResponse`

| 読み方 | 中身 |
|---|---|
| `response.text` | text ブロックの文字列。拒否なら `""`、max_tokens 切れなら途中までの JSON |
| `response.usage` | `{"prompt_tokens", "completion_tokens", "total_tokens", "cache_read_tokens", "cache_creation_tokens"}`。`prompt_tokens` はキャッシュ分も含む入力の合計、`completion_tokens` は `output_tokens`（考えた分込み） |
| `response.cost_usd`・`response.cost_source` | LiteLLM の料金表で計算した費用（float）と `"litellm"` |
| `response.raw_response` | `litellm.ModelResponse` |
| `raw_response.model` | `"claude-opus-5-5"` |
| `raw_response.choices[0].finish_reason` | `end_turn`・`stop_sequence` → `"stop"`、`max_tokens`・`pause_turn`・`model_context_window_exceeded` → `"length"`、`refusal` → `"content_filter"`、`tool_use` → `"tool_calls"` |
| `raw_response.choices[0].message.provider_specific_fields` | `{"citations": None, "thinking_blocks": [...]}`。拒否のときは `"stop_reason": "refusal"` と `"stop_details": {...}` も入る |
| `raw_response.choices[0].message.refusal` | 拒否の `explanation`（無ければ None） |
| `raw_response.usage.completion_tokens_details.reasoning_tokens` | `thinking_tokens` |
| `raw_response.usage.prompt_tokens_details.cache_creation_token_details` | 5 分・1 時間の内訳 |
| `raw_response._hidden_params["additional_headers"]["llm_provider-request-id"]` | request-id（問い合わせ用） |

使用量の記録：reasoner の中で呼ぶと、`app.ai()` はその実行の `CostTracker` に1件足す（`agentfield.cost_tracker.get_current_cost_tracker()`）。中身は `{"source": "llm", "provider": "anthropic", "model": "claude-opus-5-5", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "total_tokens", "cost_usd", "cost_source": "litellm", "routing_provider": "anthropic", ...}`。実行の結果に `__agentfield_usage__` として載ってコントロールプレーンへ届く（`cost_tracker.py`）。020 はこれを使える。

012 での読み方（案）：まず `finish_reason` を見る。`"content_filter"` は拒否、`"length"` は切れ、`"stop"` のときだけ `json.loads(response.text)` して形と「今のツリーにある要素か」を検査する。

## 5. エラー（確認済み：公式 Errors とソース。見本 `errors.json`・`app-ai-raises.json`）

エラーの本文の形は全部同じ。

```json
{"type": "error", "error": {"type": "rate_limit_error", "message": "..."}, "request_id": "req_..."}
```

| HTTP | `error.type` | `app.ai()` が投げる例外 | `app.ai()` のやり直し | ClientOps の扱い（案） |
|---|---|---|---|---|
| 400 | `invalid_request_error` | `litellm.BadRequestError`（status_code 400） | しない | こちらの不具合か使用上限。止めて記録、人へ |
| 401 | `authentication_error` | `litellm.AuthenticationError`（401） | しない | 鍵の問題。設定を直すまで段2を止める |
| 403 | `permission_error` | `litellm.PermissionDeniedError`（ソースから。実験はしていない） | しない | 同上 |
| 404 | `not_found_error` | `litellm.NotFoundError`（ソースから） | しない | モデル名の誤り |
| 413 | `request_too_large` | `litellm.BadRequestError`（ソースから） | しない | ツリーを絞る |
| 429 | `rate_limit_error` | 再試行のあと `agentfield.rate_limiter.RateLimitError`（status_code 無し、文面に `rate_limit_error`） | する（上の 1） | 少し待って1回だけ再実行、だめなら人へ |
| 500 | `api_error` | `litellm.InternalServerError`（ソースから） | する | 同上 |
| 529 | `overloaded_error` | `litellm.InternalServerError`（status_code は 500 になる。文面に `overloaded_error`） | する | 同上 |
| 504 | `timeout_error` | `litellm.Timeout`（ソースから） | する | 同上 |

- 400・401・429・529 は偽サーバに見本の本文を返させて確かめた。message の文面（400 以外）は推定。400 の文面は公式の「Forced tool use not supported」のもの。
- 拒否は 200 なので例外にならない（上の 3・4）。
- 429 には `retry-after` ヘッダーが付く。月の使用上限に達した 429 には付かず、待っても通らない（公式）。

## 6. 費用（確認済み：公式 Pricing。LiteLLM の料金表も同じ値をテストで確認）

Claude Opus 5.5（100 万トークンあたり）

| 区分 | 単価 |
|---|---|
| 入力 | $4 |
| キャッシュ書き込み 5 分 | $5 |
| キャッシュ書き込み 1 時間 | $8 |
| キャッシュ読み込み | $0.20 |
| 出力（考えた分を含む） | $20 |

- 1M トークンの文脈全体で同じ単価（長文の割増なし）。`inference_geo: "us"` を付けると 1.1 倍（付けない）。fast mode は $8 / $40（使わない）。Batch は半額（使わない）。

計算式（Messages API の `usage` から）

```
費用 = ( input_tokens × 4
       + cache_creation.ephemeral_5m_input_tokens × 5
       + cache_creation.ephemeral_1h_input_tokens × 8
       + cache_read_input_tokens × 0.20
       + output_tokens × 20 ) ÷ 1,000,000
```

`app.ai()` の `response.usage` から（1 時間キャッシュは使わない前提）

```
費用 = ( (prompt_tokens − cache_read_tokens − cache_creation_tokens) × 4
       + cache_creation_tokens × 5
       + cache_read_tokens × 0.20
       + completion_tokens × 20 ) ÷ 1,000,000
```

見本での値（トークン数は推定の例）
- ふつうの選択（入力 5,200・出力 640）：$0.0336。COMPACT-NOTES の「初回の手順づくり 約 $0.42」は、この大きさの呼び出しが十数回の計算と合う（推定）。
- max_tokens 切れ（入力 5,200・出力 16,000）：$0.3408。1回で初回づくりほどかかる。
- 拒否（入力 5,200・出力 0）：LiteLLM と agentfield は $0.0208 と記録する。しかし公式では、出力の前の拒否は `bio`・`frontier_llm`・`reasoning_extraction` だけ課金され、`cyber`・`general_harms`・null は課金されない。020 では「出力前の拒否でこの3つ以外なら $0」に直して記録する。

## 7. 拒否・切れ・確信なしの扱い
- Anthropic の `fallbacks`（拒否されたら別のモデルでやり直す）は使わない。claude-api スキルは既定で付けるよう勧めるが、行き先は Claude Opus 5 / Opus 4.8 で、決まったこと 8・10（AI は Opus 5.5 だけ）に反する。拒否されたら段3（人）へ。
- max_tokens 切れ：JSON が切れている。012 の「外れたら1回だけ聞き直し、だめなら人へ」に入れる。
- `confident: false`：段3（人）へ（none でも同じ）。
- 確信ありの none：無いこともある要素なら「無い」として続ける。必要な要素なら使えない答え（1 回だけ聞き直し、だめなら人へ）。
- 今のツリーに無い `element_id`：012 の「1回だけ聞き直し」。

## 8. 偽物（モック）：`agent/clientops/externals/fake_ai.py`

`FakeClaude` は `app.ai` と同じ引数で呼べる（`inspect.signature` でテスト）。`monkeypatch.setattr(app, "ai", fake)` で差し替えられる。

台本の積み方（積んだ順に1回ずつ使う。足りないと `UnscriptedCall`）

| メソッド | 返るもの |
|---|---|
| `reply_choice(element_id, action, confident=True, *, input_tokens=5200, output_tokens=640, ...)` | 正しい選択の JSON（ツリーに無い要素や確信なしもこれで作る） |
| `reply_none(confident=True, *, input_tokens=5200, output_tokens=640, ...)` | 「このページに無い」の JSON（`{"element_id": null, "action": "none", "confident": …}`） |
| `reply_text(text)` | JSON でない文 |
| `reply_refusal(category="cyber")` | 拒否（`content` 空、`finish_reason` は `content_filter`） |
| `reply_cut_off(partial_text)` | max_tokens 切れ（`finish_reason` は `length`） |
| `reply_message(wire)` | 任意の Messages API の応答（見本をそのまま流す） |
| `fail_rate_limited()`・`fail_overloaded()`・`fail_invalid_request(message)`・`fail_unauthorized()`・`fail_with_status(status)` | 上の 5 と同じ例外 |

作り方：台本 → Messages API の応答 JSON → LiteLLM 自身の Anthropic 変換（`AnthropicConfig.transform_parsed_response`）→ agentfield の `detect_multimodal_response`。本物の `app.ai()` と同じ部品を通すので、戻り値の型と中身が同じになる。テスト `test_app_ai_returns_what_the_fake_returns_and_what_the_sample_records` で、偽サーバ越しの本物の `app.ai()` と見比べている。使用量は本物と同じく今の `CostTracker` に記録する。

記録：`fake.received` に呼ばれた順の `ReceivedCall(args, system, user, model, max_tokens, output_config, extra)`。`fake.everything_sent()` は渡されたもの全部（位置引数・system・user・output_config・その他の引数）をつないだ文字列で、ログイン情報や表の値が入っていないことを確かめるのに使う。

本物と違う返り方をする呼び方は断る（`UnsupportedCall`）：`schema=`・`stream=True`・`response_format=`・`tools=`・Anthropic 以外のモデル。プロバイダの無いモデル名は本物と同じ `ValueError`。

## 9. 見本（`agent/tests/contract/external/anthropic/`）

| ファイル | 中身 | 確認 |
|---|---|---|
| `messages-choice.json` | 選択の JSON を返したふつうの応答（thinking + text、使用量） | 欄は公式。値（トークン数・ID）は推定の例 |
| `messages-none.json` | 「このページに無い」を返したふつうの応答（11/14 追加） | 欄は公式。値は推定の例 |
| `messages-refusal.json` | 出力前の拒否（`content` 空、`stop_details`） | 欄は公式（公式の例は `usage` が2欄だけ。全欄そろうかは推定） |
| `messages-max-tokens.json` | 16,000 で切れた応答 | 欄は公式。値は推定 |
| `errors.json` | 400・401・429・529 のエラー本文 | 形は公式。400 以外の文面は推定 |
| `request-element-choice.json` | 実際に出ていく HTTP（ヘッダーと本文） | 実験（本物の app.ai と LiteLLM） |
| `app-ai-returns.json` | 上の4つの応答に対して `app.ai()` が返したものと CostTracker の記録 | 実験（none は 11/14 に同じ方法で追加） |
| `app-ai-raises.json` | エラーごとに `app.ai()` が投げる例外 | 実験 |

## 10. agentfield 0.1.145 の既知の問題
- `app.ai()` の中で `AIConfig.copy()` が pydantic の deprecated な `copy` を呼び、`PydanticDeprecatedSince20` の警告が出る。テストは `-W error::DeprecationWarning` で走るので、本物の `app.ai()` を通す `test_anthropic_contract.py` だけ、この1件の警告を無視している（`pytestmark`）。ClientOps のコードは `copy` を使っていない。agentfield への報告候補。
- `app.ai()` の入力の切り詰め（上の結論）。

## 推定のまま残っていること（キーが届いたら確かめる）
1. `anthropic-version: 2023-06-01` とベータヘッダー付きで、`output_config`（effort と format）が 200 で通る。
2. 本物の応答に `container`・`diagnostics`・`citations` が null で入ってくるか。拒否のときに `usage` の全欄がそろうか。
3. 401・429・529 の message の文面。
4. 1回の選択の実際のトークン数と、16,000 の `max_tokens` で足りるか（medium で考える量）。
5. 出力前の `cyber` 拒否が本当に $0 になるか（コンソールの請求と照合）。
6. `element_id` の `anyOf`（string か null）と enum の `none` を含むスキーマが 200 で通り、Opus 5.5 が無い要素に null と none を返すこと（11/14 の追加分）

## キーが届いたらやること
- 004 の `bun run check` で1回だけ呼び、応答の JSON を保存する。保存したものに `test_the_message_samples_carry_exactly_the_documented_fields` と同じ形の比較を当てる。
- 1回分の費用をコンソールの使用量と照合する。
