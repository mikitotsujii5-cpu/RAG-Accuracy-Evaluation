# RAG精度評価アプリ — Databricks Apps実装

このディレクトリは、任意分野のPDF、AI Search Index、チャット履歴、正解付き評価質問、Phase 1〜5の評価結果をProject単位で扱う単一のFastAPIアプリです。画面の順番は「1. データ準備」「2. PDFカタログ」「3. RAGチャット」「4. RAG精度評価」で固定しています。トヨタ車種関連PDFと固定評価seedは後方互換を確認する同梱シナリオであり、アプリの必須入力ではありません。

## 必要な環境変数

| 変数 | 内容 |
|---|---|
| `DATABRICKS_WAREHOUSE_ID` | Project、文書、履歴、評価TableへアクセスするSQL Warehouse |
| `UC_CATALOG` | アプリ用Unity Catalog名。完全修飾Volume／Index bindingがあれば省略可 |
| `UC_SCHEMA` | アプリ用Schema名。完全修飾Volume／Index bindingがあれば省略可 |
| `UC_VOLUME` | Volume名、または`/Volumes/catalog/schema/volume`形式のパス |
| `VECTOR_SEARCH_ENDPOINT` | STANDARD AI Search endpoint名 |
| `DEFAULT_INDEX_NAME` | 初期Indexの3階層名。Project Variantが未登録の間だけ使用 |
| `DEFAULT_LLM_ENDPOINT` | model catalogの準備中にも使える既定FMAPI endpoint |
| `MLFLOW_EXPERIMENT_ID` | AppのTraceを書き込むMLflow Experiment |
| `MLFLOW_TRACKING_URI` | Databricks Appsでは`databricks`を指定し、WorkspaceのMLflow Trackingを使用 |
| `MLFLOW_TRACING_SQL_WAREHOUSE_ID` | MLflow 3 Tracingが使用するSQL Warehouse |
| `DATABRICKS_WORKSPACE_UI_HOST` | ブラウザで開くWorkspace URL。`databricks.yml`の`${workspace.host}`から設定し、未設定時はDatabricksが提供する`DATABRICKS_HOST`へfallback |

次の2つは、対応する非同期Jobを使う場合に必要です。

| 変数 | 内容 |
|---|---|
| `PREP_JOB_ID` | チャンク、Delta Table、Index作成・同期を行うJob |
| `EVAL_JOB_ID` | 固定DatasetでPhase 1〜5を評価するJob |

`PREP_JOB_ID`と`EVAL_JOB_ID`は、先頭ゼロや空白を含まない正の整数でなければなりません。Appは起動時に検証し、不正な値で評価画面を待機させ続けず設定エラーとして停止します。

CatalogとSchemaは完全修飾されたVolume／Index bindingから安全に判定するため、`app.yaml`へ利用者名を含むCatalog名を固定しません。

`app.yaml`では、Workspaceリソースを`valueFrom`で`app-warehouse`、`toyota-volume`、`baseline-index`、`default-llm`、`prep-job`、`eval-job`、`mlflow-experiment`から解決します。Databricks App service principalには、Warehouseの`CAN USE`、必要なTableの`SELECT`／`MODIFY`、Volumeの`READ VOLUME`／`WRITE VOLUME`、Indexの`SELECT`、LLM endpointの`CAN QUERY`、Jobの`CAN MANAGE RUN`、Experimentの`CAN EDIT`を用途に応じて付与します。資格情報を環境変数から直接読み取ったり、ログへ出力したりするコードはありません。`WorkspaceClient()`がAppsのOAuth認証を使用します。

既存Index専用モードでは、`GET /api/projects/{project_id}/variants`は`RAG_INDEX_PROFILES_JSON`、またはbaseline fallbackのsource Table／Indexと完全一致する行だけを返します。Projectに旧方式のVariantが残っていても一覧全体を失敗させず、その行だけを除外します。チャット／評価でVariantを解決するときは同じ許可リストを再確認し、許可外の物理Indexへはfail-closedで接続しません。

AI SearchのIndex GET応答は、全source列を同期するIndexで`columns_to_sync`を省略する場合があります。この省略だけでは失敗にせず、source schemaと検索manifestで必要列を検証します。GET応答に`columns_to_sync`または`columns_to_index`が明示される場合は、必要列との完全一致を要求し、不足列や重複列を許可しません。

## ローカル起動

```bash
cd app
uv sync --dev
npm ci
LOCAL_DEV_PRINCIPAL=local-user uv run python main.py
```

`http://localhost:8000`を開きます。ローカルPrincipalは`DATABRICKS_HOST`がない場合だけ使用されます。Databricks Apps上では、proxyが渡す`x-forwarded-access-token`をCurrent User APIで検証したuser IDを使用します。

```bash
uv run pytest
```

リポジトリルートから、Pythonの全test、Chat UI test、JavaScript構文checkを実行します。

```bash
app/.venv/bin/python -m pytest app/tests jobs/tests -q
cd app && npm run test:chat-ui && cd ..
node --check app/static/app.js
```

## Databricks Appsへのデプロイ

Databricks Appsのdeploymentは、pipが`requirements.txt`を読む方式です。Workspaceへ同期するsourceには`requirements.txt`を含め、ローカル開発・UIテスト用の`tests/`、`.venv`、`package.json`、`package-lock.json`、`pyproject.toml`、`uv.lock`、cacheを含めません。`package.json`と`package-lock.json`はChat UIのjsdomテスト専用であり、このPython＋静的asset構成のDatabricks Apps buildには不要です。`app.yaml`は`python main.py`を実行します。

active deployment IDと実環境の検証結果は、Git管理外のprivate operations logへ記録します。このREADMEへIDを複製しません。再デプロイ後はApp／compute／health、登録済み評価質問の選択run、孤児Chat run回復を伴うPDF単体削除E2Eを確認します。

既存環境を更新するときは、先に[`sql/07_migrate_generic_document_metadata.sql`](../sql/07_migrate_generic_document_metadata.sql)と[`sql/09_document_logical_deletion.sql`](../sql/09_document_logical_deletion.sql)をこの順に実行します。最初のmigrationは`missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`がすべて`0`、PDF削除migrationは`documents_without_lifecycle`、`variants_without_lifecycle`がともに`0`であることを確認した後に同期・デプロイします。デプロイ元に上記ローカル専用ファイルが残っていないことも確認します。デプロイ後はdeployment `SUCCEEDED`、App `RUNNING`、compute `ACTIVE`、health `ok`、`databricks_ready=true`に加え、PDFだけの登録、汎用メタデータ付き登録、非車両PDFのVariant作成・チャット・引用、Project固有の評価質問登録とPhase評価を確認します。さらに使い捨てProjectでPDF単体削除を行い、後継Variant／Index、引用保持、冪等再送、最後のPDFの`EMPTY`を確認してresource stateを更新します。

PDF削除の初回remote E2Eでは、App service principalに`toyota_index_variants`の`MODIFY`がなくHTTP 503となりました。`SELECT`／`MODIFY`を付与・確認してから再実行し、後継Variant更新と最後のPDF削除まで成功しています。別環境へ配置するときも、registryだけでなくVariant管理Tableの書き込み権限を必ず確認します。

Appの説明またはuser scopeを更新するときは、説明、`model-serving`、7件のresourceをすべて含むfull-safe payloadを使います。checked-in JSONの`<...>`を実値へ置換したGit管理外のlocal copyを作り、下記コマンドのpayload pathへ指定してください。

```bash
export APP_RESOURCES_UPDATE_JSON="<RENDERED_APP_RESOURCES_UPDATE_JSON>"
export APP_USER_SCOPES_UPDATE_JSON="<RENDERED_APP_USER_SCOPES_UPDATE_JSON>"

databricks apps update "$DATABRICKS_APP_NAME" \
  --json @"$APP_RESOURCES_UPDATE_JSON" \
  --profile "$DATABRICKS_CONFIG_PROFILE"

# user scopeも同時に更新する場合
databricks apps update "$DATABRICKS_APP_NAME" \
  --json @"$APP_USER_SCOPES_UPDATE_JSON" \
  --profile "$DATABRICKS_CONFIG_PROFILE"
```

`databricks apps update --description ...`だけを実行すると、既存resource bindingが外れる事象をこの環境で検出しました。更新直後に`databricks apps get`でresourceが7件あることを確認してからdeployします。個別fieldだけの更新を前提にしたpayloadへ戻しません。

Remote Chat E2Eの対象deployment、session、request、Trace、期待回答一致、引用数、PDF応答と、cancel E2E／SSO済みブラウザ手操作の状態はprivate operations logだけに記録します。

PDF単体削除E2EのProject、document、旧／後継Variant、prep／Job run、Index、最後のPDF削除結果もprivate operations logへ記録します。削除後はsource／AI Searchの削除PDFが0行／hit 0、保持PDFだけが検索されることを確認します。最後のPDFではProject `EMPTY`、READY Variant 0、原本・解析結果・過去引用保持を確認します。

認証tokenを表示せずremote E2Eを再確認する例:

```bash
python3 scripts/smoke_test_app.py \
  --profile "$DATABRICKS_CONFIG_PROFILE" \
  --app-url "$DATABRICKS_APP_URL" \
  --project-id "$RAG_PROJECT_ID"
```

同一`client_request_id`の再送と永続停止は、[`scripts/smoke_test_chat_controls.py`](../scripts/smoke_test_chat_controls.py)で確認します。既存の成功requestを再利用する場合は`--replay-session-id`と`--replay-request-id`を両方指定します。

## 実装上の重要点

- PDFは拡張子、MIME type、PDF header、100 MB上限、SHA-256重複を検査してからVolumeへ保存します。
- アップロードで必須なのはPDFだけです。タイトル、カテゴリ、タグ、文書日付、ソース、追加メタデータは初期状態で閉じた`details`にまとめています。タイトル未入力時は、拡張子を除き、連続するunderscoreと空白を整えたファイル名から最大300文字で補完します。
- PDF登録直後はタイトル／ファイル名から20〜30字のfallback概要を入れ、`ai_parse_document`完了後のbackground taskでTool Calling対応FMAPI LLMから日本語20〜30字の概要を生成します。Chat Completions、Responsesの`output_text`／`output`、`predictions`形式を正規化して読み取ります。手入力概要は上書きせず、LLM失敗だけでupload／parseを失敗扱いにしません。生成元、model key、prompt version、状態をregistryへ保存します。
- そのまま20〜30字契約を満たすLLM概要は`AI_GENERATED`、安全な決定論的整形を行ったLLM概要は`AI_GENERATED_NORMALIZED`として区別します。registryが20件だった時点の実環境監査は空欄0件、長さ違反0件で、内訳は`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、`USER=1`です。その後追加された削除E2E用2件を含む最終22件全体へ、この内訳を外挿しません。
- 汎用メタデータは`category`、`tags`、`document_date`、`source`、`custom_metadata`です。タイトル300文字、カテゴリ100文字、タグ20件・各80文字、ソース500文字、追加メタデータ20件・key 80文字・value 1000文字をサーバーでも検証します。日付は`YYYY-MM-DD`です。
- `model`、`model_year`、`document_type`、`vehicle_category`は既存クライアントとトヨタ評価seedの後方互換として受け付けます。新しい汎用UIでは必須にしません。
- `metadata_json`は`schema_version=1.0`、`common`、`custom`、`legacy`を分けて保存します。共通項目名とsystem項目名を`custom_metadata`のkeyに使わせず、大文字・小文字違いの重複も拒否します。`model`など旧トヨタ項目と同じ名前の汎用keyは`custom`で使用でき、互換用トップレベル値は`legacy`へ分離します。
- Document Parsingは`READ_FILES(path, format => 'file')`が返す`FILE`型を`ai_parse_document`へ直接渡します。BINARY列は使用しません。
- `ai_parse_document`はAppのbackground taskからX-Large Serverless SQL Warehouseへ送信します。`READ_FILES(..., format => 'file')`が返す`FILE`型を使う経路は変更していません。後続の`BUILD_VARIANT`だけを、Performance-optimized Serverless Lakeflow Jobへ移行し、チャンク化、source Delta Table作成、AI Search Index作成・同期の依頼を行います。
- Data Preparation Jobは`performance_target=PERFORMANCE_OPTIMIZED`、Serverless Environment version `5`、`max_concurrent_runs=2`、tag `compute_profile=serverless-performance-optimized-v5`です。Serverless notebook taskではtask-levelの`libraries`を使わず、Environmentの`dependencies`へ`databricks-sdk==0.135.0`を固定します。これはHYBRID Index同期に必要な`IndexSubtype`を含む検証済み版です。
- Standard／512／Qwen3のcanaryと複数PDF canaryで、source／Indexの行数一致とIndex `READY`を確認します。具体的なrun／task／prep／Variant IDと計測値はprivate operations logへ保存します。
- 移行前のClassic増強構成（Driver `Standard_D16s_v5`、Worker `Standard_D8s_v5`×2）は[`data_preparation_job_classic_fallback.json`](../deployment/jobs/data_preparation_job_classic_fallback.json)に保持します。障害時は同じJob IDへresetできます。reset wrapperとrollback手順は[`deployment/jobs/README.md`](../deployment/jobs/README.md)を参照してください。
- Project membershipをAPIごとに照合します。ブラウザからTable名、Index名、Volume URI、物理モデル名は受け取りません。
- Embedding／Chat／Judge候補は`toyota_rag_model_catalog`から動的に取得し、ブラウザにはopaqueな`model_key`だけを返します。
- Embeddingの既定値はendpoint discoveryと分けた`toyota_rag_model_defaults`で管理します。Qwen3 Embedding 0.6Bが`READY`、region利用可、選択可なら`is_default`／`is_recommended`として返し、利用不可ならREADY候補へ決定的にfallbackします。UIの「日本語対応」はQwen3 Embedding 0.6Bだけに付け、別の推奨fallbackへ誤表示しません。
- Phase presetでは検索設定をサーバー側で解決します。正式比較では同じVariant、Dataset、回答LLM、Judge LLMを固定します。
- 評価質問はProject、評価データ版、`development`／`holdout`用途で分離します。作成APIは`EDITOR`以上に限定し、正解PDFが同じProjectの`PARSED`／`READY`文書であること、1始まりの正解ページがPDFの範囲内であること、同じ版・用途に同じ質問がないことをサーバー側で検証します。画面は対象の登録済み質問を初期全選択し、個別／一括選択、正解状態、期待回答、正解PDF／ページ、選択件数と最大試行数を表示します。追加フォームは初期状態で閉じます。
- Evaluation作成APIの`evaluation_case_ids`は1〜1000件、空文字・重複なしです。サーバーは全IDが指定Project・評価データ版・用途に属することを検証し、選択IDを含むcanonicalな`config_json`／`config_hash`をPhase行へ固定します。画面は0件選択時に開始を無効化し、Jobは固定済みIDだけを評価します。`evaluation_case_ids`のない既存runは同じProject・版・用途の全件を処理する後方互換を維持します。
- 精度評価のtrial既定値は1です。開始ボタンは質問、Phase、既存Indexに対応するVariant、回答LLM、採点LLMがそろった場合だけ有効です。Phase 1〜5は全幅の横並びカードで、狭い画面はPhase領域を横スクロールし、設定、工程、指標群は1列へ切り替えます。
- 評価開始要求中からspinnerと進捗カードを表示し、`expected_trials`／`completed_trials`を全体とPhaseごとに更新します。画面は受付、Job起動、Phase評価、指標集計、改善提案、進捗割合、経過時間を示します。Projectを再表示した場合は最近のactive runを復元します。履歴からrunを開く最初のstatus GETは1回25秒のtimeoutと最大3回の再接続を持ち、通常のstatus pollも一時的失敗後に同じrunを監視します。
- Evaluation作成要求は、同じpayloadの再送でブラウザが同じ`Idempotency-Key`を最大3回再利用します。サーバーはProject、利用者、keyから同じ`eval_run_id`を決定し、Phase行をDelta `MERGE`で作成します。Lakeflow Jobsのidempotency tokenにも同じ`eval_run_id`を使うため、Appの応答消失や再起動後も同じJobを回復し、二重起動しません。
- status APIはPhase行と一つのLakeflow runの対応を検証し、queue、compute起動、environment準備、実行、terminal状態を利用者向け文言へ変換します。Job受付結果が不明な場合は30〜300秒のbackoffと`retry_after_ms`を返し、画面はその間隔を尊重します。Jobs APIの状態を一時取得できない`UNKNOWN`はspinner付きで再確認し、Bad Request、Not Found、Permission Denied、Unauthenticatedなど確定的なJob拒否だけを`FAILED`へ収束します。JobがNotebook開始前に失敗／取消になっても、Jobs APIを基準に評価行を終端状態へ収束します。providerの生メッセージは返しません。Job linkは設定済みWorkspaceと一致する安全なURLだけを返します。
- Deltaが同じPhaseの同一制御行を複数保持しても、API、評価履歴、Evaluation JobはPhaseごとに1件へ集約し、進捗と試行数を水増ししません。設定が異なる重複行はJob契約違反として拒否します。
- 評価停止は、まずDelta Tableへ`CANCEL_REQUESTED`を永続化し、対応するLakeflow Job runへも取消を要求します。画面は停止専用の通信経路で最大3回再確認し、クリック直後にもstatusを取得します。Job登録と停止が競合した場合も、同じ`eval_run_id`からJobを回復して取消します。Jobs APIが一時失敗してもJob側のcheckpointは永続フラグを確認し、その後のstatus pollでも取消を再試行します。status pollが先にterminal状態を確認した場合は、待機中の停止POSTをabortし、terminal状態と取得済み結果を維持します。完了が先に確定した場合は409を返し、完了済みPhaseを停止へ上書きしません。
- terminal状態になった後の結果APIは、1回45秒のtimeoutと最大3回の再試行を使います。取得中はspinner、`aria-busy=true`、「結果取得中」、試行番号を表示し、停止ボタンは隠します。全試行が失敗しても評価runを消さず、評価履歴を更新して同じrunを選ぶと保存済み指標と改善提案を再取得できます。
- Metadata Filteringは、現在のProjectで`PARSED`／`READY`の文書registryだけを照合します。`category`、`tags`、`document_date`、`source`、custom metadataを検証し、customは質問にkeyとvalueの両方がある場合だけ採用します。任意JSON pathをAI Search filterへ直接渡さず、検証済み`document_id IN (...)`へ変換します。矛盾、0件、全件一致、100件超では安全のためフィルタなしへ戻します。
- 回答内の引用IDを検索結果と照合し、最終回答で実際に参照した引用だけを保存します。live `citation.added`には検証済み`document_id`と物理ページを含め、ブラウザはその値から認可付きPDF content routeを再構築します。`retrieval.completed` SSEにはexcerpt／チャンク本文を含めず、画面にも表示しません。
- PDF content routeはProjectと文書所属を再認可し、`ETag`、`If-None-Match`、単一byte `Range`、`HEAD`、`Cache-Control: private`を扱います。画面はPDF操作のhover／focus／pointerdownでprefetchし、同じProjectで読み込み済みの同じPDF・ページはiframeを保持します。Project切替時は必ず破棄します。
- `GET /api/me`はDatabricks Apps ingressの認証済みメールを優先し、Current User APIで検証したidentityへfallbackします。forwarded access tokenは保存しません。
- Project削除はOWNERだけが実行できる論理削除です。`DELETING`で新規処理をfenceし、active runへ取消要求を付けて`ARCHIVED`にします。共有Delta Table、Volume、Indexの物理データは監査・復旧用に保持します。
- PDF単体削除はOWNER／EDITORだけが実行できる論理削除です。Project単位のmutation tokenでupload、Build、Chat、Evaluationとの競合をfenceします。競合判定の直前に、開始から30分を超えた`QUEUED`／`STREAMING`／`CANCEL_REQUESTED`のChat runだけを条件付きUPDATEで`ERROR`へ収束し、そのrunに対応する`STREAMING` assistant messageも`ERROR`へ整合します。終端状態を上書きしないguardを持ち、30分以内のrun、active Prep／Evaluation、別mutationには409を返します。registryは`ACTIVE → DELETING → DELETED`で管理し、カタログと新規検索は`ACTIVE`だけを対象にします。
- 削除PDFを含むREADY Variantは`SUPERSEDED`にし、そのVariantに残るPDFがあれば、保存済み設定とsource lineageからimmutableな後継Variantを作成します。削除前のactive Variantの後継だけがJob成功時にactiveになり、その他の後継は`activate_on_success=false`でactive pointerを競合させません。最後のPDF削除では空のIndexを作らずProjectを`EMPTY`、`active_variant_id=NULL`にします。
- PDF原本、Document Parsing結果、削除前の会話・引用・評価は監査用に保持します。削除済みPDFは一覧と新規検索からは除外しますが、過去の検証済み引用のcontent routeはProject権限内で開ける設計です。同じDELETEの再送は新しいJobを増やさず現在状態を返します。
- 会話削除はsession ownerだけが実行できます。active runがあれば取消要求後に409を返し、停止完了後はmessage、run、sessionをchild-firstで削除します。
- Data Preparation Job成功時、各Projectへ`starter-v1`のサンプル質問3件を冪等に作ります。期待回答とページlabelは空のため、正式評価では人が正解付きケースを追加します。
- `ERROR`の文書だけを`POST /documents/{document_id}:parse`で再解析できます。古いactive parse runを閉じ、新しい`PARSE_ONLY` runを作り、同じVolume上のPDFを`FILE`型で再処理します。正常文書の再解析と画面の多重送信は拒否します。
- Projectごとの最近の評価runを一覧取得し、選択した過去runのPhase状態、集計指標、改善提案を保存済みTableから再表示します。新規runでは選択した評価case IDも再現条件の一部として固定します。
- 期待回答・期待事実がないケースは`answer_correctness=NULL`にし、SQLの平均から除外します。0点へ変換しません。Phase advisorには検索指標に加え、Answer Correctness、Groundedness、Citation Correctnessと各judge rationaleを渡します。
- 精度評価の主表示は検索再現率（Recall@10）、回答正解率、回答時間です。詳細表と比較chartでPrecision@10、nDCG@10、Groundedness、Citation Correctness、TTFT、E2E p50／p95、token、cost、error rateも確認します。
- 各ページのfeature stripに、Catalog Explorer、Unity Catalog Volume、`ai_parse_document`、Lakeflow Jobs、Delta Table、FMAPI、AI Search、MLflow 3、Index Profileなど、裏側で使うDatabricks機能名を表示します。現在のWorkspaceに属する安全なURLを生成できた項目は、対応するコンソールを新しいタブで開けます。
- 空のチャットには用途の異なる質問例を9件表示します。精度評価画面のPhase選択見出しは「比較条件を選択」です。
- AI SearchのRerankingは`databricks-ai-search`の`DatabricksReranker`を使用します。
- SQL Statement Executionは公開Databricks SDKを使い、複数result chunkを回収し、timeout時はstatementを取り消します。
- 削除E2E helperの`on_wait_timeout`は文字列ではなく、SDKの`ExecuteStatementRequestOnWaitTimeout.CONTINUE`を渡します。文字列を渡すとSDK内部で`AttributeError: 'str' object has no attribute 'value'`になる版があるためです。
- Data PreparationはProjectごとに単一のbrowser pollerで監視します。実行中は二重送信を防ぎ、画面移動、Project切替、再読込み後も保存済みrun IDまたはactive-run APIから監視を復元します。固定回数や固定時間による監視打ち切りはありません。
- 同じProject・同じ設定のactive Buildはサーバー側で再利用し、`prep_run_id`から作るDatabricks Jobsのidempotency tokenで重複Jobを防ぎます。`job_run_id`未保存の制御行は個別GET／active GETで自己回復します。
- Job受付が不明な場合は行を即`FAILED`にせず、30〜300秒の指数バックオフで同じtokenを再確認します。Jobs APIから取得したqueue、コンピュート起動、task開始状態と、同じWorkspaceに属する検証済みJob URLだけをブラウザへ返します。
- 各チャット要求をMLflow 3の`AGENT` spanとして記録し、その配下に`final_retrieval`（`RETRIEVER`）と回答生成（`CHAT_MODEL`）を記録します。Trace IDは回答履歴にも保存し、画面にはMLflow Trace linkを表示します。Traceには`project_id`、`phase_id`、`variant_id`をタグとして付けるため、同じ評価データで条件別に比較できます。
- `responses_agent.py`は同じ検索・回答生成処理を`mlflow.pyfunc.ResponsesAgent`から呼ぶための薄い互換層です。物理Index名とendpoint名はサーバー側のconstructorで注入し、このモジュール自身はModel Servingへの登録やデプロイを行いません。

## 停止ボタンの範囲

現在の回答LLM呼び出しは公開Databricks SDKの非ストリーミングAPIです。画面はSSEで段階表示し、停止時はブラウザ側のstreamをすぐ閉じて入力を復帰させます。停止要求はrun Tableへ`CANCEL_REQUESTED`として永続化し、処理中instanceの高速経路と各処理段階のTable確認を併用するため、別App replicaでも認識できます。すでにFMAPIへ渡した単一の非ストリーミング推論をtoken途中で止める保証はありませんが、次のcheckpointでrunとassistant messageを`CANCELED`／`CANCELLED`へ確定します。接続切断、完了との競合、同一`client_request_id`再送は回帰testと本番smokeの対象です。

## 未準備時の動作

必須環境変数、Table、Index、model catalog、Jobのいずれかが未準備でもHTMLは表示されます。APIは`RESOURCE_NOT_READY`と初心者向けメッセージを返し、画面上部の案内とtoastへ表示します。stack trace、Volume path、endpoint名はブラウザへ返しません。
