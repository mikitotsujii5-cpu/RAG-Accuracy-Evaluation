# Step 1: ローカル成果物を検証する

## 目的

Workspaceへ配置する前に、Python、画面、API契約、Job設定の基本的な誤りを見つけます。ローカルテスト合格はクラウド上の稼働を保証しませんが、単純な不具合を先に除けます。

## 構築

アプリのローカル依存をまだ準備していない場合は、`app`ディレクトリで開発用環境を作ります。

```bash
cd app
uv sync --dev
npm ci
cd ..
```

Databricks Appsへのデプロイでは`app/requirements.txt`を使います。ローカルの`tests/`、`.venv`、`package.json`、`package-lock.json`、`pyproject.toml`、`uv.lock`はデプロイ元へ含めません。`package.json`と`package-lock.json`はChat UIのjsdomテスト専用です。

## 確認

```bash
app/.venv/bin/python -m pytest app/tests jobs/tests -q
cd app && npm run test:chat-ui && cd ..
node --check app/static/app.js
git diff --check
```

さらに、Workspaceへ送らないローカル専用ファイルを確認します。

```bash
find app -maxdepth 2 \( -name '.venv' -o -name '__pycache__' -o -name '.pytest_cache' -o -name 'package.json' -o -name 'package-lock.json' -o -name 'pyproject.toml' -o -name 'uv.lock' \) -print
```

この`find`はローカルに存在する開発用ディレクトリを表示する確認です。表示されたディレクトリをWorkspaceへ同期しないでください。

合格条件:

- App／JobのPython testとChat UI testがすべてpassする。件数ではなく終了コードを合格条件にする。
- JavaScript構文checkが成功する。
- `git diff --check`が出力なしで終了する。
- デプロイ対象に`tests/`、`.venv`、cache、`package.json`、`package-lock.json`、`pyproject.toml`、`uv.lock`、ローカル資格情報が含まれない。
- PDFだけのupload、タイトル自動補完、汎用メタデータ、制約超過の拒否、トヨタ互換payload、Project限定Metadata Filteringの契約testが含まれる。
- 任意の文書情報が初期状態で閉じていること、Qwen3 Embedding 0.6Bが利用可能な場合に既定選択されること、`/api/me`のメール表示を画面testで確認する。
- Project／PDF／会話削除の確認、権限、実行中処理との競合、二重送信防止をAPIと画面の両方で確認する。
- PDF単体削除はOWNER／EDITORだけに許可し、VIEWERを403で拒否する。カタログのカード／表の両方に確認付き削除があり、処理中spinner、連打防止、403／409／5xxの日本語表示を確認する。
- backendでは`DELETING → DELETED`、影響Variantの`SUPERSEDED`、後継Buildの`activate_on_success`、最後のPDFの`EMPTY`、冪等DELETE、Project mutation lock解除を確認する。削除済みPDFが新規一覧と検索から外れる一方、過去の引用のPDF content routeは開けることもtestする。
- PDF引用がチャンク本文を表示せず、認可済みPDF URLと物理ページを指すことを確認する。
- PDF取得の`ETag`、`Range`、private cache、リンク操作時のprefetch、同一Project・PDF・pageのiframe再利用、Project切替時の破棄を確認する。
- `ERROR` PDFの再解析は1回だけ送信され、正常なPDFには再解析操作が出ないことを確認する。
- `retrieval.completed` SSEにexcerptがなく、live citationが検証済み`document_id`からPDF linkを作ることを確認する。
- 過去の評価runを選ぶと保存済み指標を再表示し、未ラベルのAnswer Correctnessは`NULL`表示になることを確認する。
- Phase advisorへ回答品質3指標とjudge rationaleが渡り、Qwen以外のfallbackモデルへ「日本語対応」を誤表示しないことを確認する。
- 評価画面で版・用途ごとの質問が初期全選択され、個別選択、すべて選択、選択解除、正解状態／詳細、選択件数／最大試行数が動くことを確認する。0件では開始できず、開始APIへ選択した`evaluation_case_ids`だけを送る。
- 評価作成APIは`evaluation_case_ids`を1〜1000件で検証し、別Project・別version・別split、空ID、重複IDを拒否する。Jobは保存済みの選択IDだけを評価し、IDのない旧runは全件評価を維持する。
- 精度評価のtrial既定値が1で、質問、Phase、Variant、回答LLM、採点LLMのすべてがそろうまで開始ボタンが無効であることを確認する。1問 × 5 Phase × 1回では最大5試行と表示する。
- 開始APIの応答前からspinnerと進捗カードが表示され、工程、全体／Phase別の`completed_trials`、割合、経過時間が更新されることを確認する。Phaseカードの横スクロールと、狭い画面で設定・工程・指標が1列になることも確認する。
- 画面再読込、ページ移動、Project再表示でactiveな評価runを復元し、poll timeout／一時失敗の後も監視を継続する。実行中に別の履歴runへ切り替えて結果を混ぜない。
- 評価停止はDeltaの`CANCEL_REQUESTED`とLakeflow Jobs APIのcancelを両方試行し、terminal状態まで監視する。JobがNotebook開始前に失敗／取消になった場合も、評価行を`FAILED`／`CANCELED`へ収束して無限待ちにしない。
- providerの生メッセージをブラウザへ返さず、Job linkは設定済みWorkspaceと一致するHTTPS URLだけを表示する。検索再現率、回答正解率、回答時間を主要3指標として表示する。
- PDF削除の競合確認では、開始から30分超の`QUEUED`／`STREAMING`／`CANCEL_REQUESTED` Chat runと対応する途中のassistant messageだけをguard付きで`ERROR`へ収束する。30分以内のrunは409を維持し、同時完了した終端状態を上書きしない。

## 失敗時の修正

- 最初に失敗したテスト名を確認し、該当sourceだけを修正します。
- Job sourceを変更した場合は、Step 7で4本のNotebookをすべて再importします。
- App sourceを変更した場合は、Step 8で同期、deploy、health確認をすべてやり直します。
- testを削除したりskipしたりして合格扱いにしません。

## この環境の実測結果

`SUCCESS`。2026-09-08に現行source asset `1.4.7`の`app/tests`と`jobs/tests`を統合実行し、Python 334件すべてが合格しました。加えてUIのjsdom回帰テスト37件が合格し、自動テストは合計371件です。差分checkも合格しました。ローカル画面では評価開始直後のspinner、進捗、完了、停止、結果取得中のspinner、Phase横並びを確認しました。既存のChat実測ではEnter連打、spinner、停止、履歴切替、下書き、390 px表示、console error 0件、PDF viewer初回3,057 ms、同じProject・PDF・pageの再表示296 msを確認しています。

テストには、PDFだけの登録、タイトル自動補完、汎用メタデータ、Project限定Metadata Filteringに加え、FMAPIモデルによって拒否されるoptional sampling値を送らないこと、最終回答で参照した引用だけを保存すること、同一`client_request_id`を冪等化すること、停止と完了の競合を正しく閉じること、Chat Trace IDを履歴と画面へ渡すこと、MLflow Trace階層の契約が含まれます。さらに現行sourceでは、20〜30字の概要生成、Qwen3の既定値とfallback表示、ログインユーザー取得、Project／会話削除、PDFの`Range`／`ETag`、PDF原文リンク、prefetch、`ERROR` PDF再解析、評価履歴再表示、未ラベルCorrectness除外、advisorの回答品質根拠、評価質問の個別／一括選択と固定、評価runの復元／停止／終端収束、同一`Idempotency-Key`による評価開始再送、`eval_run_id`によるJob冪等化、`retry_after_ms`、一時的な`UNKNOWN`状態、確定的Job拒否の`FAILED`収束、停止APIの再試行、重複Phase集約、結果取得中spinner、結果APIの45秒timeout／最大3回再試行、評価履歴からの結果再取得、Job IDの正整数検証、AI Search GETで同期列が省略される応答、PDF単体削除の権限／競合／冪等性／後継Variant／最後のPDF、30分超の孤児Chat run回復、既存Index許可外の旧Variantを一覧から除外する回帰も対象です。直前のremote実測asset `1.4.5`はStep 8〜9で確認済みで、asset `1.4.7`のremote実測は`PENDING`です。

現行sourceは、上記334件のPython自動テストと37件のUIテスト、合計371件を確認済みです。直前のdeployment `<DEPLOYMENT_ID>`によるremote E2Eはasset `1.4.5`の別証跡です。旧asset `1.4.1`の240件成功とasset `1.4.3`の277件成功は履歴であり、現行機能の合格件数へ混ぜません。

削除E2E helperがSQL Statement Execution APIを呼ぶときは、両helperで`ExecuteStatementRequestOnWaitTimeout`をimportし、`on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE`を渡します。文字列`"CONTINUE"`ではSDK内部で`AttributeError: 'str' object has no attribute 'value'`となることを実環境で検出し、enumへ修正後にfresh helperがexit 0となりました。`jobs/tests/test_deployment_contract.py`の2件の契約testで、このenum指定を固定しています。

## 公式ドキュメント

- [Databricks Appsの依存関係](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/dependencies)
- [Databricks Appsのデプロイ](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
