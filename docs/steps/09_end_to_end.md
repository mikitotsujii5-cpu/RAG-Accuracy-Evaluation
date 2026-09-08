# Step 9: アプリをend-to-endで確認する

## 目的

個別リソースが存在するだけでなく、利用者がProjectを選び、PDF、検索、回答、引用、評価結果まで一続きに操作できることを確認します。

## 構築

[アプリ操作ガイド](../../APP_USER_GUIDE.md)の「最短デモ手順」を使います。最初に新しい汎用Projectへ非車両PDFを登録し、PDF準備、チャット、評価質問登録、Phase比較を一続きで確認します。既存の`Toyota RAG Baseline` Projectとbaseline Variantは、同梱シナリオの後方互換を再確認するときだけ使用します。

## 確認

### 1. 共通Project

- 画面タイトルが「RAG精度評価アプリ」である。
- サイドバーが「データ準備、PDFカタログ、RAGチャット、RAG精度評価」の順である。
- Projectを変えると、PDF、Variant、会話、評価結果も切り替わる。
- 右上にDatabricks Appsでログインしているユーザーのメールアドレスが表示される。
- 各画面に、その処理で使うDatabricks機能名が表示される。
- URLのProject IDを書き換えても、権限のないProjectを閲覧できない。
- OWNERは確認画面からProjectを削除できる。実行中処理がある場合は削除できず、削除後のProjectは通常の一覧とAPIから見えない。

### 2. データ準備とカタログ

- PDF upload後、`FILE`型のDocument Parsingが完了する。
- 必須入力はPDFだけで、「文書情報を追加」は初期状態で閉じている。開閉でき、タイトル、カテゴリ、タグ、文書日付、ソース、追加メタデータを毎回入力しなくてもよい。
- タイトル未入力時はファイル名から補完され、概要未入力時は解析後にFMAPIで20〜30字の概要、または同じ長さのfallback概要が入る。
- 256／512／1024とStandard／Semantic／Parent-childを選べる。
- 「02 検索する文書のチャンク化・ベクトル化」と表示される。
- READYなFMAPI Embedding候補だけを選べ、利用可能ならQwen3 Embedding 0.6Bが既定で「推奨・日本語対応」と表示される。利用不可時の別fallbackへ「日本語対応」を誤表示しない。
- 新しいVariantは別Table／Indexとして残り、既存Variantを上書きしない。
- カタログにタイトル、概要、リンクが表示され、認可済みPDFを開ける。
- 「PDFを開く」をhoverまたはfocusした時点でprefetchし、content APIが`ETag`、byte `Range`、private cacheに対応する。同じProject・PDF・pageを閉じて再表示すると読み込み済みiframeを再利用し、Project切替時は破棄する。
- 状態が`ERROR`のPDFだけに「再解析」が表示され、同じ登録ファイルを`FILE`型で再処理する。連打しても再解析要求は1件だけである。
- PDFカードと表の両方に「削除」があり、OWNER／EDITORだけが確認後に実行できる。処理中はspinnerが表示され、連打で同じrequestを増やさない。

#### PDF単体削除の安全なE2E

本番利用中のProjectではなく、削除検証専用の使い捨てProjectで行います。

次のhelperは、2件のPDFを持つ隔離Projectの作成、初期Variant、削除前引用、1件の論理削除、後継Variant／Delta Table／AI Search、過去引用、冪等DELETEを一続きで検証します。`--cleanup-project`は成功後に検証Projectを論理削除する場合だけ付けます。初回は証跡を目視で確認できるよう、付けずに実行します。

```bash
app/.venv/bin/python scripts/smoke_test_document_deletion.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --app-url https://<APP_HOST> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --pdf-to-delete output/pdf/04_prius_2024_safety_operation_demo.pdf \
  --pdf-to-keep output/pdf/07_crown_sport_2024_owners_guide_demo.pdf \
  --timeout-seconds 3600
```

helperが出力するProject、document、旧／後継Variant、prep／Job runのIDはGit外の運用台帳へ記録し、[公開用の検証記録](../verification/2026-09-06_field-eng-east.md)には行数と匿名化した結果だけを記載します。token、authorization header、cookieはどちらにも記録しません。

両helperのSQL待機設定は、Databricks SDKのenum `ExecuteStatementRequestOnWaitTimeout.CONTINUE`を使います。`on_wait_timeout="CONTINUE"`のような文字列は、SDK内部で`AttributeError: 'str' object has no attribute 'value'`となるため使いません。現行sourceはenumへ修正済みで、契約testとfresh helperのexit 0を確認しています。

1件目の削除と後継Indexの確認が成功したら、helperの最終JSONにある値を作業用変数へ入れます。最初にread-only preflightを実行し、対象が`PDF単体削除E2E-`で始まる使い捨てProject、表示中PDFが1件、READY Variantが1件、実行中runが0件であることを確認します。

```bash
DELETE_E2E_PROJECT_ID="<1件目helperのproject_id>"
DELETE_E2E_FINAL_DOCUMENT_ID="<1件目helperのretained_document_id>"
DELETE_E2E_PREVIOUS_DOCUMENT_ID="<1件目helperのdeleted_document_id>"
DELETE_E2E_ACTIVE_VARIANT_ID="<1件目helperのreplacement_variant_id>"

app/.venv/bin/python scripts/smoke_test_last_document_deletion.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --app-url https://<APP_HOST> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --project-id "$DELETE_E2E_PROJECT_ID" \
  --final-document-id "$DELETE_E2E_FINAL_DOCUMENT_ID" \
  --previously-deleted-document-id "$DELETE_E2E_PREVIOUS_DOCUMENT_ID" \
  --expected-active-variant-id "$DELETE_E2E_ACTIVE_VARIANT_ID"
```

preflightが`PREFLIGHT_SUCCEEDED`を返した場合だけ、Project IDを同じ値でもう一度指定して最後のPDFを削除します。`--execute-delete`と`--confirm-project-id`の片方だけでは変更しません。

```bash
app/.venv/bin/python scripts/smoke_test_last_document_deletion.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --app-url https://<APP_HOST> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --project-id "$DELETE_E2E_PROJECT_ID" \
  --final-document-id "$DELETE_E2E_FINAL_DOCUMENT_ID" \
  --previously-deleted-document-id "$DELETE_E2E_PREVIOUS_DOCUMENT_ID" \
  --expected-active-variant-id "$DELETE_E2E_ACTIVE_VARIANT_ID" \
  --execute-delete \
  --confirm-project-id "$DELETE_E2E_PROJECT_ID"
```

1. PDF AとPDF Bの2件を登録・解析し、両方を含むVariantをREADYにする。
2. PDF Aを引用する回答を1件保存し、PDF引用リンクを開けることを確認する。必要な場合は削除前の評価runも作る。
3. PDF Aの「削除」を押し、確認画面を取り消したときは何も変わらないことを確認する。再度開き、「PDFを削除」で確定する。
4. API応答が`202 Accepted`、PDF Aがカタログとデータ準備の対象から消えること、同じDELETEを再送しても新しい後継runが増えないことを確認する。
5. 元Variantが`SUPERSEDED`となり、チャット／評価の選択肢から外れることを確認する。
6. 後継Data Preparation runが`SUCCEEDED`、AI Search IndexがREADYとなるまで待つ。後継Variantの`source_document_ids`はPDF Bだけで、source TableとIndexにPDF Aのchunkが0件、PDF Bのchunkが1件以上であることを確認する。
7. 後継VariantでPDF Aの内容が検索結果へ出ないこと、PDF Bは検索できることを確認する。
8. 削除前の回答、評価結果、PDF Aの引用リンクが残り、ProjectのVIEWER以上でPDF原本を開けることを確認する。registryの状態は`DELETED`で、Volume原本と解析行は保持する。
9. 30分以内の実行中Chat、Data Preparation、Evaluation、別mutationがあるテストでは、PDF削除が409となり、PDF、Variant、Project mutation lockが安全な状態へ戻ることを確認する。
10. App再起動などで開始から30分を超えた`QUEUED`／`STREAMING`／`CANCEL_REQUESTED` Chat runを用意したテストでは、削除前にrunが`ERROR`、対応する`STREAMING` assistant messageも`ERROR`へ収束し、PDF削除が続行できることを確認する。同時に完了したrunの終端状態は上書きしない。
11. PDF Bも削除し、Projectが`EMPTY`、`active_variant_id=NULL`、カタログが空、利用可能Variantが0件となることを確認する。空の後継Indexは作らない。

### 3. チャット

- Phase 1〜5とCustomを切り替えられる。
- Vector／Hybrid、Metadata Filtering、Reranking、Query Optimizationがpresetどおりに動く。
- 回答にはチャンク本文を表示せず、検索済みチャンクとサーバー側で照合した認可済みPDF原文リンク／物理ページが付く。live citationは検証済み`document_id`からURLを作り、retrieval SSEにexcerptを含めない。
- 会話履歴が利用者とProject単位で保存される。
- 最近の会話は先読みされ、切替時はキャッシュを即時表示する。会話ごとの未送信下書きを保持する。
- 過去の会話を確認付きで削除できる。回答中の会話は停止完了前に削除されない。
- 回答中はspinnerと「停止」だけを表示し、Enter連打やHTTP再送でも同じ`client_request_id`のmessageを重複保存しない。
- 「停止」はSSEをすぐ閉じて入力を復帰させ、run Tableの停止要求を経由して履歴を`CANCELLED`へ確定する。すでに送信した非ストリーミングFMAPI推論がtoken途中で停止する保証はない。

### 4. 精度評価とMLflow

- 現在のProjectに合う質問、期待回答、正解PDF、1始まりの正解ページ、評価データ版、用途を画面から登録できる。
- Data Preparation済みProjectには`starter-v1`のサンプル質問3件が表示される。期待回答と正解ページは空であり、正式な精度比較には人が正解付きケースを追加する。
- 登録した評価質問が別Projectへ表示されず、正解PDFが同じProjectの解析済み文書へ解決される。
- Project／評価データ版／用途に登録済みの質問が初回全選択され、個別選択、すべて選択、選択解除が動く。正解状態、期待回答、正解PDF／ページ、選択件数、最大試行数を確認でき、0件では開始できない。
- 開始APIへ選択した`evaluation_case_ids`だけが渡り、同じProject・version・splitへの所属検証後に`config_json`／`config_hash`へ固定される。Evaluation JobはそのIDだけを各Phaseで評価する。
- 同じ質問集合、Dataset、Variant、回答LLM、Judge LLM、final kでPhase 1〜5を実行する。
- Recall@10、Precision@10、nDCG@10、Correctness、Groundedness、Citation Correctness、TTFT、E2E、error rate、tokenを表示する。
- 各Phaseに、失敗ケースを根拠にしたLLM改善提案を表示する。
- 評価履歴から過去runを選択し、保存済みPhase状態、指標、改善提案を再表示できる。
- 期待回答・期待事実がないケースのAnswer Correctnessは`NULL`／`—`で、平均から除外される。
- Phase advisorの根拠にAnswer Correctness、Groundedness、Citation Correctnessと各judge rationaleが含まれる。
- Trace IDからMLflowで`AGENT`の配下に`RETRIEVER`と`CHAT_MODEL`を確認できる。
- 画面からProject、評価Dataset版、Index Variant、run状態、Trace IDを確認でき、比較条件を後から再現できる。
- TTFTは非ストリーミングE2E時間から推測せず、最初のtext deltaを受け取る性能runでserver／client値を記録する。

## 失敗時の修正

- まずブラウザに出たerror code、Project ID、画面名、操作時刻、Job run IDを記録します。tokenやauthorization headerは記録しません。
- `RESOURCE_NOT_READY`はhealthの`missing`とApp resource／権限を照合します。
- 別Projectの検索結果が1件でも出た場合は、その評価結果を採用せず、Variant registryとIndex sourceを修正します。
- 引用リンクが検索結果にない場合は、LLM出力をそのままリンク化せず、citation検証処理を修正します。
- 引用欄へチャンク本文が表示された場合は正式結果として扱わず、PDF content URL生成と画面renderを修正します。
- `retrieval.completed`にexcerptが含まれる、またはlive citationに検証済み`document_id`がない場合は、そのrunを正式結果に使わずSSE payloadを修正します。
- PDF再解析を開始できない場合は文書が`ERROR`か、同じProjectで別のデータ準備が実行中でないかを確認します。正常文書の再解析を許可しません。
- PDF削除が403の場合はProject role、409の場合は実行中のData Preparation／Chat／Evaluation／別mutationを確認します。5xxまたは後継Job失敗では、PDF、旧Variant、mutation token、`prep_run_id`の状態を確認し、旧Variantを手動でactiveに戻さず削除PDFを含まないVariantで回復します。
- 30分超のChat runが残っているのに削除が409のままなら、`started_at`、run状態、対応assistant message、回復UPDATEのguardを確認します。時刻や状態を手動変更しません。30分以内のrunに対する409は正常です。
- Phase比較の途中で固定条件が変わった場合は、そのrunを正式比較へ使わず新しいrunを作ります。
- 評価質問を登録できない場合は、正解PDFの解析状態、ページ範囲、同じ版・用途での質問重複を確認します。

## この環境の実測結果

確認済み項目は`SUCCESS`です。

- 現行source asset `1.4.4`はPython 264件、Chat UI 21件（合計285件）が成功しました。Python compile 51ファイル、JavaScript構文、JSON検証も成功しています。ローカルBrowserの既存実測ではEnter 5連打時の質問1件、思考中spinner、停止直後の入力復帰、会話履歴の往復277／284 ms、下書き保持、390 px表示、console error 0件を確認しました。
- 現行asset `1.4.4`はdeployment `<DEPLOYMENT_ID>`へ配置済みです。`SUCCEEDED`／`RUNNING`／`ACTIVE`、health HTTP 200、resource binding 7件を確認しました。
- baseline Projectの使用中Variantを`baseline-standard-512-v1`へ復元し、UPDATE Statement `<STATEMENT_ID>`と検証SELECT `<STATEMENT_ID>`で確認しました。
- 非車両Project `<RESOURCE_ID>`へPDFを登録し、タイトル自動補完、汎用metadata、旧車両値NULL、3ページの`FILE`型解析を確認しました。documentは`<RESOURCE_ID>`、parse runは`<RESOURCE_ID>`です。
- 「RAG検索データを作成」はprep run `<RESOURCE_ID>`、Job run `<DATABRICKS_RESOURCE_ID>`、Semantic／512 Variant `<RESOURCE_ID>`で`SUCCEEDED`です。sourceとIndexは各6行、別Project行0、Indexは`READY`です。
- 旧asset `1.4.1`の非車両PDF remote Chat E2Eは期待回答`30分`と一致しました。session `<RESOURCE_ID>`、request `<RESOURCE_ID>`、Trace `<TRACE_ID>`、引用1件、PDF／Trace linkを履歴として保持しています。
- 旧asset `1.4.1`では、同じrequestを再送しても保存messageはuser／assistantの2件だけでした。別session `<RESOURCE_ID>`では停止API `CANCEL_REQUESTED`、terminal `run.cancelled`、履歴 `CANCELLED`を確認しました。
- 過去評価runの再表示、`ERROR` PDF再解析の多重送信防止、live citationの`document_id`、retrieval SSEからのexcerpt除去、advisorへの回答品質／rationale入力、Qwen fallback表示を現行sourceで回帰確認しました。旧asset `1.4.1`で開始した未ラベルstarter質問だけのeval run `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`も`SUCCESS`で、Phase 1のAnswer Correctnessは`NULL`、error rate 0、改善提案1件です。未ラベル回答を0点として平均へ入れていません。
- PDF概要はregistry 20件のsnapshotで空欄0件、20〜30字違反0件です。内訳は`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、`USER=1`で、Statementは`<STATEMENT_ID>`です。fresh helper後の最新registry 22件全体の監査値ではありません。
- 旧asset `1.4.1`では、評価case `<RESOURCE_ID>`、Dataset `security-policy-v1`、run `<RESOURCE_ID>`でPhase 1〜5を完走しました。5結果、エラー0、Correctness／Groundedness／Citation Correctness各5件、Trace 5件、改善提案5件です。
- 旧asset `1.4.1`のトヨタ互換回帰でも期待回答`60`と一致し、session `<RESOURCE_ID>`、request `<RESOURCE_ID>`、Trace `<TRACE_ID>`、引用2件を確認しました。
- 直前のnpm失敗deployment `<DEPLOYMENT_ID>`は失敗履歴として現行deploymentと分けています。
- PDF単体の2件→1件削除E2Eは`SUCCESS`です。新しい使い捨てProject `<RESOURCE_ID>`でdocument `<RESOURCE_ID>`をDELETE `202`で論理削除しました。初期prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`、初期Variant `<RESOURCE_ID>`は`SUPERSEDED`となり、document `<RESOURCE_ID>`だけを持つ後継Variant `<RESOURCE_ID>`が`READY`になりました。後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`、source／Index各6行、削除PDF行／検索hit 0、保持PDF行6／検索hit 1を確認しました。
- 同じDELETEの再送は冪等でした。削除前のsession `<RESOURCE_ID>`／request `<RESOURCE_ID>`／Trace `<TRACE_ID>`の引用と、後継session `<RESOURCE_ID>`／request `<RESOURCE_ID>`／Trace `<TRACE_ID>`の回答・PDF引用を確認しました。
- 最後の1件→0件は別の既存検証Project `<RESOURCE_ID>`だけをEMPTY証跡に使いました。最後のdocument `<RESOURCE_ID>`をDELETE `202`で論理削除し、Project `EMPTY`、active Variantなし、READY Variant 0件、prep run数4→4、空の後継Indexなし、mutation lock解除、PDF原本・解析行・過去引用の保持を確認しました。
- asset `1.4.4`ではProject `<RESOURCE_ID>`で開始から30分を超えた孤児Chat run 2件と対応assistant messageを`ERROR`へ整合し、document `<RESOURCE_ID>`のDELETEがHTTP 202になることを確認しました。deletion requestは`<RESOURCE_ID>`、影響旧Variant 4件、残存documentは`<RESOURCE_ID>`です。SQL Warehouseによる回復UPDATEのno-op構文検証も成功しています。
- 後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`はREADY、source 8行、削除0／保持8行です。後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`もREADY、source 4行、削除0／保持4行です。両AI Search実検索は保持documentだけ、削除document 0件でした。
- 選択評価run `<RESOURCE_ID>`では、case `figure-001`だけを固定し、Job `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>`が`TERMINATED`／`SUCCESS`、結果1行、選択外0行、error 0で完了しました。MLflow runは`<RESOURCE_ID>`、確認Statementは`<STATEMENT_ID>`です。
- 最新aggregateはStatement `<STATEMENT_ID>`で、非ARCHIVED Project 11、documents 22（`ACTIVE=18`／`DELETING=0`／`DELETED=4`）、Variants 20（`READY=13`／`SUPERSEDED=7`）、評価ケース44、mutation lock 0です。
- Evaluation Job run `<DATABRICKS_RESOURCE_ID>`で、同一条件のPhase 1〜5を完走しました。各Phase 12結果、合計60結果、エラー0、Trace 60件、Phase別LLM改善提案5件です。
- 全60 Evaluation Traceで、`AGENT`配下の`RETRIEVER`／`CHAT_MODEL`／`EVALUATOR`を確認しました。
- App service principalへの30 GRANT文を成功させました。PDF削除で必要な`toyota_index_variants`の`MODIFY`はStatement `<STATEMENT_ID>`、`SELECT`＋`MODIFY`の確認は`<STATEMENT_ID>`です。

次の3領域は`PENDING`のままです。

- Microsoft Entra IDへサインイン済みのブラウザによる、デプロイ画面4ページの手操作。remote API E2EとローカルBrowser UIは成功していますが、これを代替証拠にはしません。
- ストリーミング性能runによるserver／client TTFT。非ストリーミング品質runのE2Eから推測しません。
- 全9 profileのうち未実施の7種類。実WorkspaceではStandard／256とSemantic／512をsmoke済みです。

## 公式ドキュメント

- [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
- [AI Searchの検索品質ガイド](https://docs.databricks.com/aws/en/ai-search/retrieval-quality)
- [MLflow GenAI Evaluation](https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/)
