# Lightpanda（顧客のPCの中のブラウザ）の呼び方と返る値

確かめた日：2026-11-14
版：1.0.4（2026-11-13 公開の最新の安定版。prerelease の 1.0.4-rc1 は除いた）
手元：macOS arm64 に公式の実行ファイルを入れ、002 のデモ用サイト（`bun sandbox/sites/server.ts`）相手に本物で動かした。
見本：`agent/tests/contract/external/lightpanda/`（すべて手元の本物の応答。削った所は各ファイルの `trimmed` に書いた）

## 何を正にしたか
1. 手元で動かした本物の応答（見本のファイル）。形と値はここが正。
2. 動いている serve の `GET http://127.0.0.1:9222/json/protocol`（1.0.4 から LP ドメインの説明が載る）。使う分を `protocol-lp.json` に写した。
3. リリースに付く公式ドキュメント `lightpanda-docs.tar.gz`（`pandascript-builtins.mdx`・`browser-tools.mdx`・`mcp-tools.mdx`・`agent-commands.mdx`、sha256 `1cff5c5d9b00cff20077609bdb719c198c95446c4d2a7af00cc65aeaf4009dac` を確かめて読んだ。プロジェクトには置いていない）。
4. `lightpanda mcp` が出す資源 `mcp://skill/pandascript`（PandaScript の書き方。公式の説明文）。
5. `lightpanda help run`・`lightpanda help serve`。
6. リリースノート：https://github.com/lightpanda-io/browser/releases/tag/1.0.4

見ていないことには「推定」と書いた。

## 1. 入れ方

| 項目 | 値 |
|---|---|
| 版 | 1.0.4（`./vendor/lightpanda/lightpanda version` → `1.0.4`） |
| 置き場所 | `vendor/lightpanda/lightpanda`（実行権 755。`vendor/` は写し `snapshots/` に入れない） |
| アセット名 | `lightpanda-aarch64-macos` |
| URL | https://github.com/lightpanda-io/browser/releases/download/1.0.4/lightpanda-aarch64-macos |
| 大きさ | 91,609,208 バイト |
| sha256 | `83b0ba5ae31086b675fb3ba50644bda075a13bebabe46dc5f7543952bc41ae46`（GitHub API の `digest` と、落とした中身の sha256 が一致） |

Daytona のイメージ用（003。今回は落としていない）：

| 項目 | 値 |
|---|---|
| アセット名 | `lightpanda-x86_64-linux` |
| URL | https://github.com/lightpanda-io/browser/releases/download/1.0.4/lightpanda-x86_64-linux |
| 大きさ | 77,649,224 バイト |
| sha256 | `7d22651129901203abdb95c64f49069c49e88f5211f4d8c4c6828c123c4adab4` |
| ほかに | `lightpanda_1.0.4_amd64.deb`（20,104,424 バイト、sha256 `978da22bcc97dceca7bc831f49b4d459a1f96d85cb43360c75d35c8a85a325bb`） |

- 版とアセットは `GET https://api.github.com/repos/lightpanda-io/browser/releases/latest` で選んだ。`curl` は使えないので、bun の `fetch` で落とし、sha256 が合ったときだけ保存した。

```ts
const [url, target, expected] = process.argv.slice(2);
const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer());
const actual = new Bun.CryptoHasher("sha256").update(bytes).digest("hex");
if (actual !== expected) throw new Error("sha256 mismatch, not saved");
await Bun.write(target, bytes);
await (await import("node:fs/promises")).chmod(target, 0o755);
```

- macOS 版は macOS 15 以上で動く（リリースノート）。Linux 版は glibc が要る（調査メモより。今回は未確認＝推定）。

## 2. 起動（serve）

```console
$ bun run lightpanda
$ ./vendor/lightpanda/lightpanda serve --host 127.0.0.1 --port 9222
$time=… $scope=note $level=note $msg="server running" address=127.0.0.1:9222 max_connections=16120 max_browser_connections=16
```

`GET http://127.0.0.1:9222/json/version`（本物）：

```json
{"Browser": "Lightpanda/1.0", "Protocol-Version": "1.3", "User-Agent": "Lightpanda/1.0", "Lightpanda-Version": "1.0.4", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/"}
```

- `--host` の既定も `127.0.0.1`。外から触れないよう、必ず 127.0.0.1 で起動する。
- `Origin` ヘッダの付いた WebSocket の接続は既定で断られる（`--remote-allow-origins`、help に記載。試していない）。
- `/metrics` が既定で開く（help に記載。試していない）。止めるなら `--disable-metrics`。
- `Browser.getVersion` は運転ソフトとの互換のため Chrome 124 を名乗る。Lightpanda の版は `LP.version`（`{"version": "1.0.4"}`）で読む。

## 3. CDP のつなぎ方（本物）

1. WebSocket で `ws://127.0.0.1:9222/` につなぐ
2. `Target.createBrowserContext` → `{"browserContextId": "BID-1"}`
3. `Target.createTarget {"url": "about:blank", "browserContextId": "BID-1"}` → `{"targetId": "FID-0000000001"}`
4. `Target.attachToTarget {"targetId": "FID-0000000001", "flatten": true}` → `{"sessionId": "SID-1"}`
5. ここから先はメッセージに `"sessionId": "SID-1"` を付ける。`Page.enable` → `{}`
6. `Page.navigate {"url": "http://127.0.0.1:3002/login"}` → `{"frameId": "FID-0000000001", "loaderId": "LID-0000000001"}`
7. `LP.waitUntil {"until": "load"}` → `{}`
8. 終わったら `Target.closeTarget {"targetId": …}` → `{"success": true}`、`Target.disposeBrowserContext {"browserContextId": …}` → `{}`

やり取りの実物（`supplier-stock.json` の `wire`）：

```json
{"id": 14, "method": "LP.getSemanticTree", "params": {"format": "text", "interactiveOnly": true}, "sessionId": "SID-1"}
{"id": 14, "result": {"semanticTree": "5 RootWebArea 'Stock list - Maruyama Supply'\n 16 [i] button 'Sign out'\n …"}}
```

- 今のURLは `Target.getTargetInfo {"targetId": …}`（ブラウザ側、sessionId なし）の `targetInfo.url` で読む。保存のあとは `http://127.0.0.1:3002/inventory?saved=1` だった。
- `targetId` などは接続ごとに `BID-1`・`FID-0000000001`・`SID-1` から数え直した。

## 4. 使う呼び出し（本物の入出力）

### LP.getSemanticTree
引数（`/json/protocol`）：`format: "text"`（なしなら JSON）、`prune`（既定 true）、`interactiveOnly`（既定 false。prune より優先）、`backendNodeId`（その要素から下だけ）、`maxDepth`。
返る値：`{"semanticTree": <文字列 か JSON の木>}`。

text の1行の形（本物）：`<backendNodeId> [i] <role> '<name>' value='<value>'`。字下げ1つが深さ1。`[i]` は操作できる要素。文字だけの行は `<id> '<文字>'`。

```text
5 RootWebArea 'Inventory - Kumo Coffee - Store Admin'
 12 navigation 'Store'
 22 [i] button 'Sign out'
 24 heading 'Inventory'
 30 columnheader 'SKU'
 43 [i] spinbutton 'Available for Ethiopia Guji 200g' value='18'
 …
 332 [i] link 'Discard'
 333 [i] button 'Save changes'
```

JSON の1要素（`interactiveOnly: true`、本物）：

```json
{"nodeId": "50", "backendDOMNodeId": 50, "nodeName": "input",
 "xpath": "/html[1]/body[1]/div[1]/main[1]/form[1]/table[1]/tbody[1]/tr[2]/td[3]/input[1]",
 "nodeType": 1, "isInteractive": true, "role": "spinbutton", "name": "Available for Ethiopia Guji 500g", "value": "12",
 "attributes": {"class": "qty", "type": "number", "min": "0", "step": "1", "name": "available-KC-ETH-500", "value": "12", "aria-label": "Available for Ethiopia Guji 500g"},
 "children": []}
```

応答 JSON の文字数（本物、1回ずつ）：

| ページ | text | text・interactiveOnly | JSON | JSON・interactiveOnly | JSON・prune なし |
|---|---|---|---|---|---|
| 仕入先の在庫（1ページ目、20行） | 2,500 | 340 | 23,119 | 2,452 | 55,120 |
| 店の在庫フォーム（42行） | 5,550 | 3,181 | 42,349 | 21,823 | — |

分かったこと：
- `interactiveOnly` では表のセルの文字（在庫の数）が消える。仕入先の在庫を読むのは `format: "text"`（既定の prune あり）で、1行目の `columnheader` のあと SKU・名前・包装・数・状態の順に文字の行が並ぶ。仕入先は 20 行ずつ3ページ（`Showing 1–20 of 42`）。
- 店のフォームは `interactiveOnly` の text だけで 42 個の値が読める（`spinbutton … value='18'`）。
- 保存のあとの `role="status"`（`Saved. 1 product updated.`）は `interactiveOnly` に出ない。`LP.waitForSelector` で id をもらい、`{"format": "text", "backendNodeId": 338}` で読む → `"338 status\n 339 'Saved. 1 product updated.'\n"`。
- ページを移ると `backendNodeId` は振り直される（保存の前 43 → 後 379 など）。

### LP.getInteractiveElements
引数：`nodeId`（任意）。返る値：`{"elements": [...]}`（本物の1件）：

```json
{"backendNodeId": 43, "tagName": "input", "role": "spinbutton", "name": "Available for Ethiopia Guji 200g", "type": "native", "tabIndex": 0, "class": "qty", "inputType": "number", "value": "18", "elementName": "available-KC-ETH-200"}
```

- `elementName` が HTML の `name` 属性、`name` が見える名前。ボタン名の変更（スイッチ renameButton）では `name` が `Update inventory` に変わり、`elementName: "action"`・`class` は同じ（`store-renamed-button.json`）。
- 要素の `backendNodeId` は、ツリーか一覧を一度読んだあとにだけ使える。読む前に推測の id で `LP.fillNode` を呼ぶと `InvalidNodeId` だった。

### LP.detectForms
返る値（本物、fields は2件に削った）：

```json
{"forms": [{"backendNodeId": 4, "action": "/inventory", "method": "post", "fields": [
  {"backendNodeId": 43, "tagName": "input", "name": "available-KC-ETH-200", "inputType": "number", "required": false, "disabled": false, "value": "18"}]}]}
```

「どのフォームの中か」の手がかりはここ（フォームの `backendNodeId`・`action`）か、JSON ツリーの `xpath` で取る。ボタンは fields に入らない。

### LP.getNodeDetails
引数：`backendNodeId`。返る値（本物）：

```json
{"nodeDetails": {"backendNodeId": 43, "tagName": "input", "role": "spinbutton", "name": "Available for Ethiopia Guji 200g", "isInteractive": true, "value": "18", "inputType": "number", "class": "qty", "selector": "input[name=\"available-KC-ETH-200\"]"}}
```

- `selector` は確かめた要素から PandaScript を組み立てるのに使える。ただし「querySelector で最初に当たる一番短いセレクタ」で、1つに決まる保証はない。保存ボタンは `button[name="action"]` で、紛らわしいボタン（decoyButton）が出ると2つに当たる。台本では `[value="save"]` まで足し、要素が1つかを確かめる。
- パスワード欄の値は `*****` と出る。

### LP.fillNode
引数：`backendNodeId`、`text`。返る値は `[]`（空の配列。`{}` ではない）。入れたあと `LP.getNodeDetails` の `value` が `"6"` になった。

- CDP の `LP.fillNode` は `$LP_*` を置き換えない。serve を `LP_STORE_USERNAME` 付きで起動しても、欄には文字どおり `$LP_STORE_USERNAME` が入った（`errors-and-placeholders.json`）。CDP でログインするなら、PCの中の実行器が値を入れる。

### LP.clickNode
引数：`backendNodeId`。返る値は `[]`。画面の移動を待たずに返る（説明文どおり）。押したあとは `LP.waitForSelector` で次のページの要素を待つ（移動中なら新しいページで待つ。本物で確かめた）。

### LP.waitForSelector / LP.waitUntil
- `LP.waitForSelector {"selector": "[role=\"status\"]"}` → `{"backendNodeId": 338}`。
- `LP.waitUntil {"until": "load"}` → `{}`。
- 2026-11-14（015、手元の 1.0.4 で本物を確かめた。見本のファイルは足していない）:
  - `LP.waitForSelector` の `timeout` を省くと、押した直後に呼んでも 5000 ms で `InternalError` になった（説明文は「読み込み中は 15000」。押した直後はまだ前のページが読み込み済みと数えられる）。サインインに失敗して同じログインのページに戻ったときも、5 秒待ってから失敗になる（015 の段 1 の遅さの原因）
  - `LP.clickNode` でフォームを送った直後の `LP.waitUntil {"until": "domcontentloaded", "timeout": 15000}` は、押して始まった移動の先のページ（応答が 2 秒遅いときも）が読み込まれるまで待って `{}` を返した
  - `LP.getSemanticTree {"maxDepth": 0}` → `{"semanticTree": {"nodeId": "1", "backendDOMNodeId": 1, "nodeName": "root", "xpath": "", "nodeType": 9, "children": []}}`。ページを移ると根の `backendDOMNodeId` が変わる（1 → 16）。押して新しいページになったかをこれで見る
  - `LP.waitUntil {"until": "settled", "timeout": 1}` は、落ち着いたページではすぐ `{}`。`LP.waitForSelector {"selector": "table", "timeout": 100}` は無ければ約 100 ms で `InternalError`
  - 実行器の click（`waitFor` つき）はこれを使い、押して新しいページになり、そのページが落ち着いても要素が無ければすぐ wait-failed にする。新しいページにならないときは今まで通り待つ

### エラー（本物）
- 知らない id：`{"code": -31998, "message": "InvalidNodeId"}`
- id なしの `LP.clickNode {}`：`{"code": -31998, "message": "InvalidParam"}`

## 5. PandaScript

書き方（公式の説明 `mcp://skill/pandascript` と本物で確かめた）：
- グローバルは `Page` だけ。`const page = new Page(); await page.goto(url);` のあと、ほかはすべて page の同期メソッド（`page.fill(sel, value)`・`page.click(sel)`・`page.extract(schema)`・`page.evaluate(js)`・`page.waitForSelector(sel)`・`page.waitForState(state)`・`page.press(sel, key)`・`page.selectOption`・`page.setChecked`・`page.login`・`page.screenshot`・`page.download` など）。
- セレクタは CSS だけ。`backendNodeId` は使えない。
- `return <値>` が stdout に JSON で出る。`console.log` は stdout、`console.warn`・`console.error` は stderr。
- `tree`・`findElement`・`nodeDetails`・`interactiveElements`・`markdown`・`detectForms` は台本の中に無い（本物で `typeof undefined`。`pandascript-probes.json` の probe-tree）。決まりごと 7 のとおり、要素ツリーは CDP 側で使う。
- `page.click` は移動が終わってから返り、文字列を返す：`Clicked element (selector: …). Page url: http://127.0.0.1:3002/inventory, HTTP 200 OK, title: Inventory - Kumo Coffee - Store Admin`。`page.fill` も `Filled element (selector: …) with "6". Page url: …` を返す。
- `page.extract({x: {selector, attr: "value"}})` は HTML の属性を返す。入力のあとも `"18"` のまま。今の値は `page.evaluate("document.querySelector(…).value")` で読む（`"6"`）。
- `page.extract({matches: [sel]})` は1つも当たらないと `[]` を返す（例外にならない）。要素が1つかの確認に使う。
- `page.login({username, password, usernameSelector, passwordSelector, submitSelector})` はセレクタを全部渡すとモデルを呼ばない（`calls: 0`）。ただし本物ではログインできたのに `loggedIn: false` が返った。使うなら結果は信じず、URL で確かめる。今の台本は `fill` と `click` にした。

使う台本 `agent/tests/contract/external/lightpanda/store-set-available.js`（店の管理画面にログインし、KC-ETH-200 の Available を 6 にして保存する）：
- ログイン欄・パスワード欄・ボタンがそれぞれ1つだけかを確かめてから、`$LP_STORE_USERNAME`・`$LP_STORE_PASSWORD` を入れる（値は台本に書かない）
- ログイン後のパスが `/inventory` か
- 入力欄と保存ボタンが1つだけか、保存ボタンの名前が `Save changes` のままか（要素の手がかりの確認）
- 入れたあとの値が 6 か
- 保存後に `role="status"` が1つあり `Saved.` で始まるか、パスが `/inventory` か、読み直した値が 6 か
- 確認に通らなければ `throw new Error("check failed: …")` で止まる

### lightpanda run の出力と終了コード（本物、`pandascript-runs.json`）

```console
LP_STORE_USERNAME=… LP_STORE_PASSWORD=… ./vendor/lightpanda/lightpanda run agent/tests/contract/external/lightpanda/store-set-available.js
```

| 場面 | 終了コード | stdout | stderr |
|---|---|---|---|
| 成功（スイッチなし） | 0 | `{"sku":"KC-ETH-200","before":"18","after":"6","message":"Saved. 1 product updated."}` | なし |
| ボタン名の変更（renameButton） | 1 | なし | `Error: check failed: form[action="/inventory"] button[type="submit"][name="action"][value="save"] says "Update inventory", expected "Save changes"` |
| 見た目と name 属性の変更と紛らわしいボタン（restyle・decoyButton） | 1 | なし | `Error: check failed: input[name="username"] matched 0 elements, expected 1` |
| ログインを env に入れない | 1 | なし | `Error: check failed: after signing in the page is /login, expected /inventory` |

- restyle の行は 2026-11-14 に録り直した（015 の決定で restyle が店のサインインの欄と在庫フォームのボタンの name 属性も変えるようになったため。前は終了コード 0 で成功と同じだった）。この手書きの台本は name 属性で欄を選ぶので、サインインの欄で止まる。段 1（要素ツリーで探し直す）はこの場面のためにある

ほかの終わり方（`pandascript-probes.json`）：
- 手順の失敗（当たらないセレクタを click）：1、`Error: click failed: NodeNotFound: the selector or backendNodeId matched nothing on the current page. …`
- 文法の誤り：1、`Error: SyntaxError: Unexpected token ';'`
- 台本のファイルが無い：1、`Error: Failed to read script 'does-not-exist.js': FileNotFound`
- stderr には色の制御文字（`\x1b[1m\x1b[31m … \x1b[0m`）が付く。`NO_COLOR=1` でも付いた。画面に出す前に取り除く。
- 確認の失敗も手順の失敗も終了コードは同じ 1。どちらかは stderr の `check failed:` で見分ける。

## 6. プレースホルダ `$LP_*`
- `lightpanda run` の台本の文字列の中の `$LP_<名前>` は、Lightpanda の中で環境変数の値に置き換わる（本物でログインできた）。
- 結果の文字列には置き換え前の `$LP_STORE_USERNAME` が出て、値は出ない（本物）。
- 環境変数が無い `$LP_*` はそのままの文字として入力され、エラーにならない（本物：`$LP_NOT_SET` が欄に入り、終了コード 0）。実行器は台本の全プレースホルダが env にあるかを、走らせる前に確かめる。
- CDP の `LP.fillNode` では置き換わらない（上の 4）。
- `page.evaluate` に渡す JS の文字列の中でも置き換わる（本物、2026-11-14・014 で確認：`page.evaluate('"$LP_SUPPLIER_USERNAME".length')` が値の長さ 13 を返した。台本そのものの文字列（21）と `console.log` では置き換わらない）。台本は自前のコードで組み立て、`$LP_*` を evaluate に渡さない（014 の `agent/clientops/pandascript.js` は入力済みかを `$` と `LP_` を分けた比較で確かめる）。
- `LP_` 以外の名前は読まれない（公式の説明。試していない＝推定）。

## 7. 見本とモック
- 見本：`supplier-stock.json`（仕入先の在庫 3 ページ）、`store-save.json`（ログイン→フォーム→入力→保存→読み直し）、`store-renamed-button.json`、`errors-and-placeholders.json`、`pandascript-runs.json`、`pandascript-probes.json`、`protocol-lp.json`、台本 `store-set-available.js`。ログインの `LP.fillNode` の `text` は `null` にして `redacted: ["text"]` を付けた（応答は本物）。
- モック：`agent/clientops/externals/fake_lightpanda.py` の `FakeLightpanda(recording, script_run="saved")`。
  - `send(method, params)`：録った呼び出しのうち、まだ答えていない同じ method・params のものを順に返す（redacted の引数は何でもよい）。録ったエラーは `CdpError(code, message)`、録っていない呼び出しは `UnrecordedCall`。
  - `run_script(script, env)`：録った `lightpanda run` の結果（`ScriptRun(exit_code, stdout, stderr)`）を返す。台本の `$LP_*` が env に足りなければ、本物と同じく「ログインを env に入れない」の結果を返す。env は名前だけ控え、値は残さない。
- 単体テスト：`agent/tests/test_fake_lightpanda.py`。見本が `/json/protocol` の返す値の名前だけでできているかも確かめる。
- 結合テスト：`agent/tests/test_lightpanda_integration.py`。サイト（`bun sandbox/sites/server.ts`）と `bun run lightpanda` を動かし、`LP_STORE_USERNAME`・`LP_STORE_PASSWORD` に店の管理画面のデモ用ログイン（`sandbox/sites/catalog.ts` の accounts）を入れて `bun run test:agent` を走らせると、本物の応答と終了コード・出力が見本と同じかを比べる。どれかが無ければ理由を書いて skip。テストの前後でサイトを reset し、スイッチを全部切る（`/__control/reset` はスイッチを戻さない）。

## 8. 限界・注意
- `lightpanda run` 1回は 0.23〜0.28 秒だった（各場面1回ずつの観測。比べたり外に出したりする数字には使わない）。
- `LP.fillNode`・`LP.clickNode` の結果は `[]`、`LP.waitUntil`・`Page.enable` は `{}`。
- `backendNodeId` は読んだあとにだけ有効で、ページを移ると振り直される。
- 台本の `page.evaluate` はページの中で任意の JS を動かす。台本は確かめた手順から組み立て、AI に書かせない（決まりごと 7）。

## 出典（2026-11-14 に確認）
- リリース：https://github.com/lightpanda-io/browser/releases/tag/1.0.4
- リリース一覧 API：https://api.github.com/repos/lightpanda-io/browser/releases/latest
- 公式ドキュメント（リリースの資産）：https://github.com/lightpanda-io/browser/releases/download/1.0.4/lightpanda-docs.tar.gz
- CDP の説明：手元の serve の `GET /json/protocol`
- PandaScript の書き方：`lightpanda mcp` の資源 `mcp://skill/pandascript`

## 推定のまま残したこと
- Linux 版に glibc が要ること（Daytona のイメージで確かめる、003）
- `--remote-allow-origins` と `/metrics` のふるまい（help の記載だけ）
- `LP_` 以外の環境変数が置き換わらないこと
- Linux（Daytona）でも同じ応答になること（003・004 で `test_lightpanda_integration.py` を PCの中で走らせて確かめる）
