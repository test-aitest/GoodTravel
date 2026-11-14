# 動きの参考: Osmo「Free Resources」コレクション（2026-11-14 調査）

- コレクション: https://codepen.io/collection/LYPEvO （作者 Osmo / @osmosupply、2025-01-24 作成、2026-06-15 更新）
- 読み方: WebFetch は 403 で弾かれたので、Chrome で一覧（リスト表示）を開いて読みました。読むだけで、操作は表示の切り替えとページ送りのみです。コードはプロジェクトに一切写していません。
- 一覧で見えたのは 19 本です。ページ送りのボタンはありますが、2 ページ目に別のペンは見えませんでした。
- 「効果」の欄は、ペンを動かして確かめたものではありません。題名から推測しています。
- 技法の欄は、題名に書かれている GSAP プラグイン名です。GSAP は 3.13 以降、SplitText・MorphSVG・DrawSVG・Inertia などのプラグインも無料になっています。
- ライセンス: 各ペンの詳細を開いてライセンス表示を確かめてはいません。CodePen では公開ペンの既定が MIT ですが、PRO の作者（Osmo は PRO）は変更できます。動きは参考にするだけで、コードは自分で書きます。

| # | 題名 | 作者 | URL | 効果（題名からの推測） | 技法 |
|---|---|---|---|---|---|
| 1 | Fixed Underlay Navigation - Osmo | Osmo | https://codepen.io/editor/osmosupply/pen/019ecab1-0de0-75fc-901d-80af78d2bc08 | 固定された下地の上をナビが覆う／めくれるメニュー | GSAP と推測（CodePen 2.0 エディタのペン） |
| 2 | Crisp Loading Animation - Osmo | Osmo | https://codepen.io/osmosupply/pen/RNaeYqp | 読み込み中の演出 | GSAP と推測 |
| 3 | Willem Loading Animation - Osmo | Osmo | https://codepen.io/osmosupply/pen/wBGYEMd | 読み込み演出（Willem 風） | GSAP と推測 |
| 4 | Falling Text with Gravity (Physics2D) | Osmo | https://codepen.io/osmosupply/pen/ByyVYMG | 文字が重力で落ちて積もる | GSAP Physics2D |
| 5 | Masked Text Reveal (GSAP SplitText) | Osmo | https://codepen.io/osmosupply/pen/pvvKezw | 行や単語がマスクの下からせり上がって現れる | GSAP SplitText |
| 6 | Draw Random Underline (DrawSVG) | Osmo | https://codepen.io/osmosupply/pen/qEEKRrx | ホバーで手書き風の下線が描かれる | GSAP DrawSVG（SVG） |
| 7 | Glowing Interactive Dots Grid (GSAP Inertia) | Osmo | https://codepen.io/osmosupply/pen/azzKmwX | 点の格子がカーソルに反応して光り、慣性で揺れる | GSAP InertiaPlugin |
| 8 | Morphing Play/Pause (MorphSVG) | Osmo | https://codepen.io/osmosupply/pen/yyyzMee | 再生と一時停止のアイコンが形を変えて切り替わる | GSAP MorphSVG（SVG） |
| 9 | Pixelated Image Reveal - Osmo | Osmo | https://codepen.io/osmosupply/pen/OJKjWNz | 画像がモザイクから鮮明に現れる | GSAP と推測 |
| 10 | Looping words with GSAP - Osmo | Osmo | https://codepen.io/osmosupply/pen/QWeQQrW | 単語が縦に回り続ける見出し | GSAP |
| 11 | Apple Dock Navigation Bar - Osmo | Osmo | https://codepen.io/osmosupply/pen/BaXPYNQ | macOS の Dock のような拡大するナビ | GSAP と推測 |
| 12 | Parallax Image Layers - Osmo | Osmo | https://codepen.io/osmosupply/pen/NWQevrB | スクロールで画像の層がずれて奥行きが出る（いちばん人気、約 7.3 万回の閲覧） | GSAP ScrollTrigger と推測 |
| 13 | Dynamic Custom Cursor with GSAP — Osmo | Osmo | https://codepen.io/osmosupply/pen/Jjgqjvp | 対象に合わせて形や文字が変わるカーソル | GSAP |
| 14 | Side Navigation with Wipe Effect - Osmo | Osmo | https://codepen.io/osmosupply/pen/JoPPOXj | 横から拭うように開くメニュー | GSAP と推測 |
| 15 | Draggable Infinite Slider (GSAP) - Osmo | Osmo | https://codepen.io/osmosupply/pen/NPKqByd | ドラッグで無限に回るカードのスライダー | GSAP Draggable と推測 |
| 16 | Osmo Scaling System (Starterkit) | Osmo | https://codepen.io/osmosupply/pen/dPbXyem | 画面幅に合わせて全体の大きさを揃える仕組み（動きではない） | CSS |
| 17 | Gallery to Overlay Transition — Osmo | Osmo | https://codepen.io/osmosupply/pen/yyBXYZv | 一覧の画像が、その位置から全画面の詳細へつながって広がる | GSAP Flip と推測 |
| 18 | Interactive Tab System with GSAP — Osmo | Osmo | https://codepen.io/osmosupply/pen/vEBeKJz | タブの中身が切り替わるときの演出 | GSAP |
| 19 | Dynamic Current Year | Osmo | https://codepen.io/osmosupply/pen/azoEZap | フッターの年を自動で今年にする（動きではない） | JS |

## 旅行予約の場面に合う 3 つ

場面: 全画面の旅行予約。重ねて表示する会話、写真・動画のカード、座席表、長押しで確定するチケット。

1. **Gallery to Overlay Transition（#17）→ 写真・動画カードから詳細への移行**
   カードを押すと、そのカードの位置から全画面の詳細（行き先の写真や動画）へ途切れずに広がります。「どのカードを開いたか」を見失わないので、会話の流れの中で候補を開く場面に最も合います。作るなら GSAP Flip（または View Transitions API）で、自分で書きます。
2. **Masked Text Reveal（#5）→ 重ねた会話の文が出てくるとき**
   エージェントの返答が行ごとにマスクの下からせり上がると、全画面の写真の上でも文字が落ち着いて読めます。チャットのような 1 文字ずつのタイプ表示より上品で、映像の上に重ねるのに向いています。SplitText で行に分けて、短い時間差で出します。
3. **Glowing Interactive Dots Grid（#7）→ 座席表**
   点の格子が指やカーソルに反応して光る動きは、そのまま座席表に置き換えられます（点＝座席、近くの空席が光る、選んだ席は色が変わる）。慣性の揺れは控えめにして、空席と満席の区別がはっきり見えることを優先します。

長押しで確定するチケットに当たるペンは、このコレクションにはありません。押している間だけ進む GSAP のタイムライン（離すと戻る）を自分で書きます。進み具合の輪は Draw Random Underline（#6）と同じ DrawSVG の考え方で描けます。
