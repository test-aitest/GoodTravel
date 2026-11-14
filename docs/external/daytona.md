# Daytona（顧客ごとのPC）の呼び方と返る値

確かめた日：2026-11-14
SDK：PyPI `daytona` 0.231.1（`uv add --directory agent daytona` で入った最新の安定版。同じ版の `daytona-api-client`・`daytona-toolbox-api-client` が一緒に入る）
API キー：まだ無い。本物の応答とはまだ突き合わせていない（下の「キーが届いたらやること」）。

## 何を正にしたか
1. 入れた SDK のソース（`agent/.venv/lib/python3.13/site-packages/daytona/` と `daytona_api_client/`・`daytona_toolbox_api_client/`）。メソッド名・引数名・戻り値の型・例外はここが正。
2. 公式ドキュメント（意味・既定値・上限）。
   - Sandboxes：https://www.daytona.io/docs/en/sandboxes
   - Network limits：https://www.daytona.io/docs/en/network-limits
   - Preview：https://www.daytona.io/docs/en/preview
   - Limits：https://www.daytona.io/docs/en/limits
   - Process and code execution：https://www.daytona.io/docs/en/process-code-execution
   - File system operations：https://www.daytona.io/docs/en/file-system-operations
   - Python SDK の Sandbox：https://www.daytona.io/docs/en/python-sdk/sync/sandbox
   - Computer Use（Linux の要素操作）：https://www.daytona.io/docs/en/computer-use ・ https://www.daytona.io/docs/en/computer-use-linux

確かめられなかったことには「推定」と書いた。

## ClientOps が使う呼び出し（一覧）

| したいこと | 呼び方 | REST | 返るもの |
|---|---|---|---|
| 顧客のPCを作る | `Daytona.create(CreateSandboxFromSnapshotParams(...), timeout=60)` | `POST /sandbox` | `Sandbox`（STARTED になるまで待ってから返る） |
| 顧客のPCを探す | `Daytona.list(ListSandboxesQuery(labels={...}))` | `GET /sandbox?labels=<JSON>` | `Iterator[Sandbox]`（ページは SDK がたどる） |
| ID か名前で取る | `Daytona.get(sandbox_id_or_name)` | `GET /sandbox/{idOrName}` | `Sandbox` |
| 動かす・止める | `sandbox.start(timeout=60)`・`sandbox.stop(timeout=60, force=False)`（`Daytona.start(sandbox)`・`Daytona.stop(sandbox)` も同じ） | `POST /sandbox/{id}/start`・`/stop` | `None`（終わるまで待つ。`sandbox.state` が更新される） |
| 今の状態を読み直す | `sandbox.refresh_data()` | `GET /sandbox/{id}` | `None`（属性が更新される） |
| コマンドを実行 | `sandbox.process.exec(command, cwd=None, env=None, timeout=None)` | toolbox `POST /process/execute` | `daytona.ExecuteResponse` |
| ファイルを置く | `sandbox.fs.upload_file(src, dst)`（src は bytes かローカルのパス） | toolbox `POST /files/bulk-upload` | `None` |
| 画面に映すURL | `sandbox.create_signed_preview_url(port, expires_in_seconds=None)` | `GET /sandbox/{id}/ports/{port}/signed-preview-url` | `SignedPortPreviewUrl` |
| 映すURLを無効にする | `sandbox.expire_signed_preview_url(port, token)` | `POST /sandbox/{id}/ports/{port}/signed-preview-url/{token}/expire` | `None` |
| スナップショットを作る | `Daytona.snapshot.create(CreateSnapshotParams(name, image=Image..., resources=...), on_logs=None, timeout=0)` | `POST /snapshots` | `Snapshot`（active・error・build_failed になるまで待つ） |
| スナップショットを取る | `Daytona.snapshot.get(name_or_id)` | `GET /snapshots/{id}`（名前でも引ける、SDK の例） | `Snapshot` |
| 眠ったスナップショットを起こす | `Daytona.snapshot.activate(snapshot_or_name)` | `POST /snapshots/{id}/activate` | `Snapshot` |
| デスクトップを動かす（024） | `sandbox.computer_use.start(request_timeout=None)` | toolbox `POST /computeruse/start` | `ComputerUseStartResponse` |
| 要素の木を読む（024） | `sandbox.computer_use.accessibility.get_tree(scope=None, pid=None, max_depth=None)` | toolbox `GET /computeruse/a11y/tree?scope=&pid=&maxDepth=` | `AccessibilityTreeResponse` |
| 要素を押す（024） | `sandbox.computer_use.accessibility.invoke_node(node_id, action=None)` | toolbox `POST /computeruse/a11y/node/invoke` | `None` |
| 要素に値を入れる（024） | `sandbox.computer_use.accessibility.set_node_value(node_id, value)` | toolbox `POST /computeruse/a11y/node/value` | `None` |

## 1. PCを作る：`Daytona.create`

```python
from daytona import CreateSandboxFromSnapshotParams
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(
        snapshot="clientops-computer-1",
        labels={"clientops-client": "kumo"},
        domain_allow_list="portal.maruyama-supply.example,admin.kumo-coffee.example",
        auto_stop_interval=15,
    ),
    timeout=60,
)
```

渡す引数（`CreateSandboxFromSnapshotParams`、pydantic のモデル）

| 引数 | 型 | ClientOps で入れる値 | 確認 |
|---|---|---|---|
| `snapshot` | `str \| None` | 作り置きのスナップショット名（Lightpanda と 002 のサイト入り）。名前は 003 で決める | SDK |
| `labels` | `dict[str, str] \| None` | 顧客ID。キー名 `clientops-client` は ClientOps の決め事。SDK が `code-toolbox-language: python` を自動で足す（渡した dict もその場で書き換わる） | SDK |
| `domain_allow_list` | `str \| None` | その顧客のサイトのホスト名をカンマ区切り | SDK・ドキュメント |
| `network_allow_list` | `str \| None` | 使わない（IPv4 の CIDR だけ） | SDK・ドキュメント |
| `network_block_all` | `bool \| None` | 使わない（許可リストと同時に指定すると 400） | ドキュメント |
| `auto_stop_interval` | `int \| None`（分） | 必ず指定する。未指定は 15 分、0 は自動停止なし（使わない） | SDK・ドキュメント |
| `env_vars` | `dict[str, str] \| None` | 渡さない。秘密を作成時の環境変数に入れない（決まりごと） | — |
| `secrets` | `dict[str, str] \| None` | 渡さない（下の「秘密について」） | SDK |
| `name` | `str \| None` | 渡さない。未指定なら ID が名前になる | SDK（API の説明文） |
| `language` | 省略 | 省略すると SDK が `python` を入れる | SDK |
| `timeout`（create の引数） | `float` 秒 | 60（作成〜起動の待ち。0 は無制限） | SDK |

返るもの：`daytona.Sandbox`（`daytona_api_client.Sandbox` を継承した pydantic モデル）。ClientOps が読む属性：

| 属性 | 型 | 意味 |
|---|---|---|
| `id` | `str` | PCの ID。UUID の形（推定。形はドキュメントに書かれていない） |
| `name` | `str` | 未指定なら `id` と同じ |
| `labels` | `dict[str, str]` | 作成時のラベル＋`code-toolbox-language` |
| `state` | `SandboxState \| None` | 下の「状態」 |
| `snapshot` | `str \| None` | 作成元のスナップショット名 |
| `auto_stop_interval` | `int \| float \| None` | 分 |
| `domain_allow_list` | `str \| None` | 作成時の値 |
| `network_allow_list` | `str \| None` | 作成時の値 |
| `network_block_all` | `bool \| None` | |
| `env` | `dict[str, str] \| None` | 一覧（list）の結果では `None`。`refresh_data()` で埋まる |
| `error_reason`・`recoverable` | `str \| None`・`bool \| None` | `error` 状態のときの理由と、`recover()` で戻せるか |

見本：`agent/tests/contract/external/daytona/sandbox.json`（API が返す JSON の形。キーは camelCase）。SDK のモデルが必須にしている項目（`organizationId`・`user`・`public`・`kvm`・`target`・`cpu`・`gpu`・`memory`・`disk`・`toolboxProxyUrl`）は、SDK がその見本を読めるように入れてある。ClientOps は読まない。

作成時の例外（SDK のソースで確認）：

| 場面 | 例外 | `status_code` |
|---|---|---|
| `auto_stop_interval` が負 | `DaytonaBadRequestError`（`DaytonaValidationError` は同じクラスの古い別名） | `None`（送る前に SDK が止める） |
| `network_block_all` と許可リストを同時に指定 | `DaytonaBadRequestError` | 400（ドキュメント。文言は推定） |
| 同じ `name` がある | `DaytonaConflictError` | 409（SDK の例。文言は推定） |
| 鍵が無い・違う | `DaytonaAuthenticationError` | 401（鍵が無いときは `Daytona()` を作る時点で出る） |
| 時間切れ | `DaytonaTimeoutError`。PCができてから切れたときは `sandbox_id` 属性に ID が入る | — |
| 上限・混雑 | `DaytonaRateLimitError`（429）、`DaytonaQueueTimeoutError`（割り当て待ちの時間切れ。API 側で消される） | |

すべて `DaytonaError` の子。`message`・`status_code`・`code`・`source` を持つ。メッセージの頭に SDK が `Failed to create sandbox: ` を付ける。

## 2. 顧客のPCを探す：`Daytona.list` と `Daytona.get`

```python
from daytona import ListSandboxesQuery
found = list(daytona.list(ListSandboxesQuery(labels={"clientops-client": "kumo"})))
```

- `labels` は SDK が JSON 文字列にして `GET /sandbox?labels=...` で送る（SDK）。複数のラベルを渡したときに「全部一致」かどうかはドキュメントに無い（推定：全部一致）
- `states=[SandboxState.STOPPED]` で状態でも絞れる（SDK）
- 一覧の結果は `SandboxListItem` から作られるので、`env`・`volumes`・`build_info` などは `None`。必要なら `refresh_data()`（SDK）
- 見本：`list_sandboxes.json`（`{"items": [...], "nextCursor": null}`。`nextCursor` があれば SDK が次のページを取る）
- `get("")` は `DaytonaBadRequestError("Failed to get sandbox: sandbox_id_or_name is required")`（SDK）。無い ID は `DaytonaNotFoundError`（404。文言は推定）

## 3. 状態（`SandboxState`、`daytona_api_client` の列挙型）

値：`creating`・`restoring`・`destroyed`・`destroying`・`started`・`stopped`・`starting`・`stopping`・`error`・`build_failed`・`pending_build`・`building_snapshot`・`unknown`・`pulling_snapshot`・`archived`・`archiving`・`resizing`・`snapshotting`・`forking`・`pausing`・`paused`・`resuming`（SDK）。知らない値は `unknown_default_open_api` になる。

ClientOps で見るのは次の4つ：
- `started`：コマンドを流せる
- `stopped`：自動停止か `stop()` のあと。`start()` で戻る
- `archived`：止まったまま既定 7 日たつと移る。`start()` で戻るが時間がかかる（ドキュメント）
- `error`：`error_reason` を記録し、`recoverable` が真なら `recover()`

ドキュメントの表では消すときの名前が Deleting・Deleted だが、SDK の値は `destroying`・`destroyed`。SDK の値を正にする。

## 4. 自動停止（費用は動いている間だけ）

- 単位は分。未指定は 15 分。0 は止まらない（ドキュメント・SDK）
- 止まったあと：自動アーカイブは既定 7 日（0 は最大の 30 日）。自動削除は既定でなし（ドキュメント）
- 待ち時間が戻る（＝止まらない）もの（Sandboxes のページ）：状態の変化、プレビュー経由の通信、SSH、toolbox の API（`exec`・`upload_file` など）
- 戻らないもの：toolbox 以外の SDK 呼び出し、投げっぱなしのバックグラウンド処理（`lightpanda serve` を裏で動かしているだけでは止まる）
- 食い違い：Python SDK の `set_autostop_interval` の説明には「Sandbox Previews のやり取りは含まない」とある。プレビューを開いているだけで止まらないかは本物で確かめる（推定：Sandboxes のページの方が新しい）

## 5. コマンドを実行：`sandbox.process.exec`

```python
response = sandbox.process.exec("lightpanda run job.js", cwd="/home/daytona/jobs", timeout=120)
response.exit_code, response.result
```

- 引数：`command: str`、`cwd: str | None`、`env: dict[str, str] | None`、`timeout: int | None`（秒）（SDK）
- **`timeout` を渡さないと 10 秒で切れる**（ドキュメントと API の説明文）。PandaScript の再実行は必ず `timeout` を渡す
- 戻り値 `daytona.ExecuteResponse`：`exit_code: int`、`result: str`（出力）、`artifacts: ExecutionArtifacts(stdout=result, charts=[])`。終了コードが 0 以外でも例外にならず、そのまま返る（SDK）
- `result` に標準エラーも混ざるか：ドキュメントに明記なし（推定：混ざる。エラー処理の例が `result` を「エラー出力」として表示している）。末尾の改行が残るかも推定
- 時間切れのとき：`DaytonaProcessExecutionTimeoutError`（`DaytonaTimeoutError` の子。デーモンの `PROCESS_EXECUTION_TIMEOUT` に対応）（推定：SDK の対応表から。実際に出るかは未確認）
- 実行するシェルは zsh・bash・sh のうち最初にあるもの（SDK の説明）
- 秘密を `env` で渡すと Daytona の API を通る。ログインの値は PC の中で埋める決まりなので、ここで渡さない
- 見本：`execute_response_echo.json`（`echo 'hello from the client computer'` の結果）と `execute_response_exit_3.json`（`exit 3` の結果）。中身は API の JSON（`exitCode`・`result`）

## 6. ファイルを置く：`sandbox.fs.upload_file`

```python
sandbox.fs.upload_file(script_bytes, "/home/daytona/jobs/job.js")
```

- 実際の引数名は `src`・`dst`・`timeout`（秒、既定 30 分）。ドキュメントの型ヒントは `file`/`local_path`・`remote_path` と書いてあるが、動く関数の名前は `src`・`dst`。**位置引数で渡す**（SDK）
- `src` が bytes ならその中身、str ならローカルのファイルのパス。同じパスがあれば上書き。戻り値は `None`
- 相対パスはユーザーのホーム（`/home/<user>`）から（ドキュメント）。ユーザー名は見本では `daytona`（推定）
- 大きさの上限はドキュメントに無い

## 7. 画面に映すURL：`create_signed_preview_url`

```python
preview = sandbox.create_signed_preview_url(3002, expires_in_seconds=3600)
preview.url
```

- 戻り値 `SignedPortPreviewUrl`：`sandbox_id: str`、`port: int`、`token: str`、`url: str`（SDK）
- URL の形：`https://{port}-{token}.{proxyDomain}`。ヘッダなしでブラウザや iframe で開ける（ドキュメント）。見本の `proxy.daytona.work` はドキュメントの例の値（本物のドメインは推定）
- token の長さ・文字の種類はドキュメントに無い（見本の 32 文字は推定）
- 有効期限：既定 60 秒、最短 1 秒、最長 86,400 秒（24 時間）、ドキュメントのおすすめは 3,600 秒。PCを止めて動かし直しても期限までは使える。`expire_signed_preview_url(port, token)` で先に無効にできる（ドキュメント）
- 範囲外の秒数のときの例外：推定 `DaytonaBadRequestError`（400）
- ポートは 1〜65535 のどれでもよい（ドキュメント）。PCの中のサイトが `127.0.0.1` だけで待っていても映るかは書かれていない（推定：`0.0.0.0` で待つ必要があるかもしれない）
- **ブラウザで初めて開くと Daytona の警告ページが出る**（ドキュメント）。消し方は `X-Daytona-Skip-Preview-Warning: true` ヘッダ、Tier 3 以上、自前のプレビュープロキシのどれか。iframe からはヘッダを付けられないので、Activity 欄に映す方法は 003 で決める
- ヘッダで開くふつうのプレビュー（`get_preview_link`、`x-daytona-preview-token` ヘッダ）は止めると無効になり取り消せないので使わない

## 8. 通信の許可リストと契約の段（Tier）

- `domain_allow_list`：カンマ区切りのホスト名。プロトコル・パス・ポートは書かない。`*.example.com` で配下も含む。最大 100 件（ドキュメント）
- `network_allow_list`：カンマ区切りの IPv4 CIDR（`/0`〜`/32` 必須）。最大 10 件。ホスト名・IPv6 は不可（ドキュメント）
- `network_block_all` と許可リストは作成時に同時指定できない（400）（ドキュメント）
- HTTPS はホスト名で判定し、許可したホストへの TLS は Daytona の出口のプロキシで終端される（ドキュメント）。PCの中の `127.0.0.1` への通信が影響を受けるかは書かれていない（推定：受けない）
- **Tier 1・2 では組織の制限が優先され、PCごとの許可リストで上書きできない**。作成後の変更（`update_network_settings`）はエラーになる。作成時に指定した場合に拒否されるのか無視されるのかは書かれていない（推定：無視。Network limits のページに「作成時に `networkAllowList`・`domainAllowList` を指定しても組織の制限はそのまま効く」とあり、拒否するとは書いていない）。Tier 1・2 でも必須のサービス（パッケージ置き場・GitHub・Anthropic などの AI 事業者・`app.daytona.io` ほか）には届く
- Tier 3・4 は既定で全部に出られ、許可リストを付けるとそれだけになる。必須サービスも許可リストに無ければ届かない
- **11/14 実測：組織全体の制限がある組織では、作成時に `domain_allow_list` を付けると 400（`DaytonaBadRequestError`「Network access is restricted and cannot be overridden at the sandbox level. Remove domainAllowList from the request.」）で拒否される**（上の「推定：無視」は誤り）。そのため ClientOps は `DAYTONA_SANDBOX_ALLOW_LIST=on` のときだけ許可リストを付けて作り、見つけた PC の許可リスト違いも確かめる。未設定やほかの値では付けず、違いも咎めない
- Tier の上がり方：1＝メール確認、2＝カード登録と $25、3＝$500、4＝30 日ごとに $2,000（Limits のページ）

## 8.5 スナップショット（作り置きのイメージ）

確かめた日：2026-11-14（SDK のソース `daytona/_sync/snapshot.py`・`daytona/common/image.py`、ドキュメント https://www.daytona.io/docs/en/declarative-builder ・ https://www.daytona.io/docs/en/snapshots ）

- 宣言的な作り方：`Image.base("<image>")` から `.env({...})`・`.run_commands(...)`・`.add_local_file(local, remote)`・`.dockerfile_commands([...])`・`.workdir(...)`・`.entrypoint([...])` をつなぎ、`daytona.snapshot.create(CreateSnapshotParams(name=..., image=image, resources=Resources(cpu, memory, disk)), on_logs=print)` で登録する（ドキュメント・SDK）
- `add_local_file` はファイルを Daytona のオブジェクト置き場に上げ、Dockerfile に `COPY ["<置き場の中のパス>", "<PCの中のパス>"]` を書く。置き場の中のパスは、渡したパスの先頭の `/` を取ったもの。絶対パスを渡すと手元のフォルダ名（ユーザー名を含む）が Dockerfile に残るので、ClientOps はプロジェクトの根からの相対パスで渡す（SDK）
- 上げるのは `snapshot.create` の中。ファイルが無いと `DaytonaError("Failed to create snapshot: Path does not exist: <path>")`（SDK）
- 作り終わるまで 1 秒ごとに状態を読む。`error`・`build_failed` なら `DaytonaError("Failed to create snapshot: Failed to create snapshot <name>, reason: <error_reason>")`（SDK）。`timeout` の既定は 0（待ち続ける）
- `CreateSnapshotParams.entrypoint` を渡すと SDK が Dockerfile の末尾に `ENTRYPOINT` を足す。渡した `Image` もその場で書き換わる（SDK）
- 状態：pending・building・pulling・snapshotting・active・inactive・error・build_failed・removing。**2 週間使わないと inactive になり、`activate` で起こすまで使えない**（ドキュメント）
- 元のイメージにはタグか digest が要る。`latest`・`lts`・`stable` は不可。AMD64 で作る（ドキュメント）
- 宣言的に作ったイメージは 24 時間キャッシュされ、同じ内容なら作り直さない（ドキュメント）
- ドキュメントに無いこと：スナップショットの大きさの上限、PC の起動にかかる時間、イメージの中の既定のユーザー
- 同じ名前のスナップショットを作ったときの例外は推定（`DaytonaConflictError`、409）。無い名前の `get` は推定 `DaytonaNotFoundError`（404）

## 8.6 デスクトップの要素操作：`sandbox.computer_use`（024）

確かめた日：2026-11-14（SDK のソース `daytona/_sync/computer_use.py`・`daytona/common/errors.py`・`daytona_toolbox_api_client/api/computer_use_api.py` とモデル、ドキュメントの Computer Use のページ。デーモンのソースは公開リポジトリから消えていて読めなかった）

- 要素の操作は Linux の AT-SPI を薄く包んだもの。**先に `computer_use.start()`**（Xvfb・xfce4・x11vnc・novnc を動かす）。始める前に読むと `DaytonaA11yUnavailableError`（503、`code` は `A11Y_UNAVAILABLE`、`source` は `DAYTONA_DAEMON`。SDK の対応表）
- `get_tree` の `scope` は `focused`（既定）・`pid`・`all`。`pid` は `scope="pid"` のときだけ。`max_depth` は -1 で全部・0 で根だけ（既定 -1）。ClientOps は `all` か `pid` で、深さは指定しない
- 返る木：`{"root": 節, "truncated": bool}`。節は `id`・`role`・`name`・`description`・`states`・`actions`・`bounds`・`children`（どれも省略可。SDK のモデル `ComputerUseAccessibilityNode`）。`role` は AT-SPI の役割名（`push button`・`entry`・`dialog`・`frame` など）。ClientOps は roles.py の表で Web と同じ役割名に直す
- `invoke_node` は `action` を省くと主の操作を行う（SDK）。`set_node_value` は「値を変えられる節に」文字か値を書く（ドキュメント）。**値を読む呼び出しは無い**（`get_tree` の節にも値の項目が無い）
- 失敗の HTTP は 400・404・500・503（toolbox の定義）。SDK が `code` と状態から `DaytonaBadRequestError`・`DaytonaNotFoundError`・`DaytonaA11yUnavailableError` などに変える
- 推定：節の `id` の形（`<バス名>:<オブジェクトのパス>`、例 `:1.31:/org/a11y/atspi/accessible/6`）、消えた節を押したときの 404（`NOT_FOUND`）、値を変えられない節に書いたときの 400（`BAD_REQUEST`）、`truncated` が true になる条件、`scope="pid"` の根がそのアプリの `application` の節になること、知らない pid の 404

## 9. 上限（Limits のページ、組織ごと）

| Tier | vCPU | メモリ | ディスク | PC作成/分 | 状態変更/分 |
|---|---|---|---|---|---|
| 1 | 10 | 10 GiB（別の表では 20 GiB） | 30 GiB | 300 | 10,000 |
| 2 | 100 | 200 GiB | 300 GiB | 400 | 20,000 |
| 3 | 250 | 500 GiB | 2,000 GiB | 500 | 40,000 |

PC1台の既定は 1 vCPU・1 GiB・3 GiB、1台の上限は 4 vCPU・8 GiB・10 GiB（Sandboxes のページ）。

## 秘密について
- 作成時の `env_vars` には秘密を入れない（決まりごと）。`Sandbox.env` は API から読み返せるので、入れると外に出る
- SDK 0.231.1 には `secrets`（組織の Secret を名前で付ける。PCの中の環境変数には中身ではなく置き換え用の印が入り、許可したホストへの通信のときだけ本物に差し替わる）がある。ClientOps のログインはPCの中のフォームに打ち込むので、この仕組みでは埋められない（推定）。使わない
- SDK は作業フォルダの `.env.local`・`.env` から `DAYTONA_*` を読む。エージェントは `agent/` で動くので、根の `.env.local` は SDK からは見えない。鍵はプロセスの環境変数で渡す（`bun run` が根の `.env.local` を読み込んで子のプロセスに渡す、は推定。004 で確かめる）

## SDK を使うときの注意（ソースで確認）
- `DaytonaConfig(api_key=...)` だけで `Daytona(...)` を作ると、SDK が古い `server_url` を読みに行き `DeprecationWarning` が出る（pytest は `-W error::DeprecationWarning` なので失敗する）。`api_url` も必ず渡す。`clientops.externals.daytona_client()` はそうしている
- `Daytona()` を作ると裏でイベント用の WebSocket に接続しに行く（`use_deprecated_polling` は deprecated なので使わない）
- `daytona_sdk` という古いパッケージ名は deprecated。`daytona` を使う

## モック（`agent/clientops/externals/fake_daytona.py`）

切り替えは環境変数だけ。黙って切り替えない。

```python
from clientops.externals import daytona_client
daytona = daytona_client()
```

| `CLIENTOPS_EXTERNALS` | `DAYTONA_API_KEY` | 返るもの |
|---|---|---|
| `fake` | どちらでも | 1プロセスで共有の `FakeDaytona` |
| 未設定か `real` | あり | 本物の `Daytona`（`api_url` は `DAYTONA_API_URL` か `https://app.daytona.io/api`） |
| 未設定か `real` | なし・空 | `MissingExternalKey`（どの鍵が無いかと `CLIENTOPS_EXTERNALS=fake` の案内） |
| それ以外 | — | `ValueError` |

`FakeDaytona` がまねするもの：上の一覧の呼び出しだけ。引数の名前・順番・既定値は本物と同じ（テストで `inspect.signature` を突き合わせている）。返す `FakeSandbox` は本物の `Sandbox` と同じ `daytona_api_client.Sandbox` を継承し、属性の名前と型も同じ。中身は見本の JSON から SDK 自身のモデル（`from_dict`）を通して作る。`exec` は本物と同じ `daytona.ExecuteResponse`、プレビューは本物と同じ `SignedPortPreviewUrl` を返す。

テスト用に足したもの：
- `fake.calls`：成功した呼び出しの記録（`FakeCall(method, sandbox_id, arguments)`）。`create` は渡した params オブジェクトそのもの、`exec` は `command`・`cwd`・`env`・`timeout`、`upload_file` は `dst` と中身の bytes
- `fake.script_command(command, response)`：そのコマンドに返す API の JSON（見本をそのまま渡せる）。登録していないコマンドは `exitCode 0`・空の出力（モックだけの決め事）
- ID とトークンは呼んだ順で決まる（同じ順に呼べば毎回同じ）
- `fake.snapshot`（003 で足した `FakeSnapshotService`）：`get`・`create`・`activate` を本物と同じ引数でまねる。`create` は `fake.calls` に `snapshot.create` として params・Dockerfile の中身・上げるはずのローカルのパスを残す（何も上げない）。返す `Snapshot` は見本 `snapshot.json` から SDK のモデルで作る。`fake.snapshot.fail_next_build(reason)` で次の作成を build_failed にでき、`fake.snapshot._set_state(name, state)` で inactive などにできる

024 で足したもの（`agent/clientops/externals/fake_daytona_desktop.py`）：
- `sandbox.computer_use`（本物と同じくプロパティ）の `start()` と、`accessibility` の `get_tree`・`invoke_node`・`set_node_value`。引数は SDK と同じ（`tests/test_daytona_desktop_contract.py` が `inspect.signature` で突き合わせる）。返すのは見本 `accessibility/` の JSON を SDK のモデル（`from_dict`）に通したもの。失敗は SDK と同じ `create_daytona_error` で作る（`start` 前の読み取りは見本 `error_a11y_unavailable.json` から）
- `fake.desktop(sandbox_id)`：PC ごとのデスクトップ。`tree`（根の節。差し替えられる。既定は見本 `tree_all.json`）、`truncate`（true で `truncated: true`）、`pids`（pid → アプリ名。`scope="pid"` はその名前の `application` の節を根にして返す）、`values`（書いた値）、`node(id)`（節を探す）
- `fake.calls` に `computer_use.start`・`accessibility.get_tree`（`scope`・`pid`・`max_depth`）・`accessibility.invoke_node`（`node_id`・`action`）・`accessibility.set_node_value`（`node_id`・`value`）
- モックだけの決め事：読めるのは `scope` が `all` か `pid` で深さ指定なしだけ（ほかは ValueError）。値を書けるのは `states` に `editable` がある節だけ（ほかは 400）。押せるのは `actions` がある節だけ（ほかは 400）。押しても木は変わらない。PC を止めても `start()` 済みの印は消えない

モックだけの決め事（本物で確かめる）：止まっているPCへの `exec`・`upload_file` は `DaytonaError`。ラベルでの絞り込みは全部一致。止まっているPCへの `stop()`・動いているPCへの `start()` は何も起きない。スナップショットの作成はすぐ active になる（失敗を頼んだときだけ build_failed）。PCの作成はスナップショットがあるかを確かめない。

## 見本のファイル（`agent/tests/contract/external/daytona/`）

| ファイル | SDK のモデル | 取り方（キーが届いたら） |
|---|---|---|
| `sandbox.json` | `daytona_api_client.Sandbox` | 上の作成例で作り、`GET /sandbox/{id}` の JSON |
| `list_sandboxes.json` | `daytona_api_client.ListSandboxesResponse` | `stop()` のあと `GET /sandbox?labels={"clientops-client":"kumo"}` |
| `signed_port_preview_url.json` | `daytona_api_client.SignedPortPreviewUrl` | `create_signed_preview_url(3002, expires_in_seconds=3600)` |
| `execute_response_echo.json` | `daytona_toolbox_api_client.ExecuteResponse` | `exec("echo 'hello from the client computer'")` |
| `execute_response_exit_3.json` | 同上 | `exec("exit 3")` |
| `accessibility/tree_all.json` | `daytona_toolbox_api_client.AccessibilityTreeResponse` | デスクトップで zenity の入力欄を出し、`get_tree(scope="all")`（値の `id`・`bounds`・`states` は推定） |
| `accessibility/computer_use_start.json` | `daytona_toolbox_api_client.ComputerUseStartResponse` | `computer_use.start()`（pid は推定） |
| `accessibility/error_a11y_unavailable.json` | `daytona_toolbox_api_client.ErrorResponse` | `start()` の前に `get_tree` を呼んだときの本文（文言は推定） |
| `snapshot.json` | `daytona_api_client.SnapshotDto` | `bun run computer:snapshot` のあと `GET /snapshots/clientops-computer-1` の JSON（`size`・`imageName`・`buildInfo.snapshotRef`・`contextHashes` の形は推定） |

値のうち、ID・組織ID・トークン・スナップショット名・ラベルのキー・許可リストのホスト名・`target`（`us`）・`toolboxProxyUrl`・`user`（`daytona`）は形だけ合わせた作り物（推定）。キーの名前と型は SDK のモデルで確かめた（`agent/tests/test_daytona_contract.py` が、SDK のモデルが見本を知らない項目なしで読めて、同じ値・同じ型で書き戻せることを確かめる）。

## キーが届いたらやること
1. 004 の `bun run check` で、上の表の取り方どおりに本物の応答を取り、見本とキー・型が一致するかを確かめる
2. 推定と書いた点を確かめる：Tier と許可リストの扱い（作成時に拒否か無視か）、`127.0.0.1` で待つサイトがプレビューに映るか、警告ページが iframe でどう見えるか、プレビューを開いているだけで自動停止が延びるか、`result` に標準エラーと末尾の改行が入るか、止まったPCへの `exec` の例外、`exec` の時間切れの例外、ID とトークンの形
3. 違っていたら見本とモックを直し、このファイルの「推定」を消す
