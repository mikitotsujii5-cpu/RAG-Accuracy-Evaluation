# RAG精度評価アプリ

任意分野のPDFをDatabricksへ登録し、RAGの検索精度、回答品質、レイテンシを比較するアプリです。画面タイトルは「RAG精度評価アプリ」です。PDFだけで登録でき、必要に応じてカテゴリ、タグ、文書日付、ソース、任意の追加メタデータを付けられます。

PDF、検索Index、チャット履歴、評価Dataset、評価結果はすべてProject単位で分離します。各ProjectのPDFに合う正解付き評価質問を画面から登録し、同じ評価データ版・用途の質問一覧から今回使う質問だけを選んで、Phase 1から5のVector Search、Hybrid Search、Metadata Filtering、Reranking、Query Optimizationの効果を順番に確認できます。

現行UIでは、任意の文書情報を閉じたパネルにまとめ、Qwen3 Embedding 0.6Bを利用可能時の既定値にしています。PDF解析後はFMAPIで20〜30字の概要を作り、回答の根拠はチャンク本文ではなくProject認可済みPDF原文リンクで示します。ログインユーザーのメール、使用中のDatabricks機能、Project／PDF／会話の削除、評価用サンプル質問3件、登録済み質問の個別・一括選択、正解情報、過去の精度評価結果も画面から確認できます。PDF単体の削除では、登録と監査履歴を残したまま検索対象から外し、影響するVariantを残存PDFだけで再構築します。解析に失敗したPDFは、同じファイルを再アップロードせずカタログから再解析できます。

> [!CAUTION]
> `output/pdf`のトヨタ車種関連PDFは、汎用RAGを比較するために同梱した評価シナリオです。すべて非公式・架空であり、実車の操作、整備、救助、購入判断には使用できません。トヨタ自動車株式会社とは関係ありません。

トヨタ評価シナリオはアプリの必須用途ではありません。非車両文書の動作確認には、同梱の[`generic_information_security_policy_demo.pdf`](output/pdf/generic_information_security_policy_demo.pdf)を使用できます。

## 最初に読む文書

- [顧客向けハンズオン手順書](CUSTOMER_HANDS_ON_GUIDE.md): Genie Code、最新Databricks UI、非公開GitHub、手動リソース作成、App自動デプロイをスクショポイント付きで説明
- [構築・評価手順書](DATABRICKS_TOYOTA_RAG_BUILD_GUIDE.md): Databricksの機能、設計、SQL、評価方法の詳しい説明
- [Step別構築手順](docs/steps/README.md): 各Stepの「構築→確認→失敗時の修正」と現在の状態
- [Workspace検証記録（匿名化版）](docs/verification/2026-09-06_field-eng-east.md): 具体的なIDを除いた実測値と未完了項目
- 実IDを含むresource stateはGitへ追加せず、アクセス制御された運用台帳で管理
- [アプリ操作ガイド](APP_USER_GUIDE.md): Project作成、PDF登録、評価質問登録、Phase比較の画面操作
- [アプリ実装README](app/README.md): Databricks Appsの環境変数、権限、ローカルテスト
- [Lakeflow Jobs README](deployment/jobs/README.md): NotebookのimportとJob作成・更新
- [PDFコーパスREADME](output/pdf/README.md): 汎用確認用1冊とトヨタ評価用9冊の役割、登録メタデータ、質問例

## 対象ワークスペース

このリポジトリの配置先は次の1ワークスペースに固定されています。CLI操作の前に、選択中のprofileがこのhostを指すことを必ず確認してください。

| 項目 | 設定値 |
|---|---|
| Workspace | `field-eng-east` |
| Workspace ID | `<WORKSPACE_ID>` |
| Cloud | Azure |
| Host | `https://<DATABRICKS_WORKSPACE_HOST>` |
| Databricks CLI profile | `<DATABRICKS_CLI_PROFILE>` |
| SQL Warehouse ID | `<SQL_WAREHOUSE_ID>` |
| Unity Catalog | `<UC_CATALOG>` |
| Schema | `rag_accuracy` |
| Volume | `documents` |
| AI Search endpoint | `toyota-rag-search` |
| App name | `<APP_NAME>` |

公開用の設定例は [config/field-eng-east.json](config/field-eng-east.json) です。Job ID、deployment ID、App URL、App service principal IDなど、作成後に決まる実値はGitへ追加せず、アクセス制御された運用台帳で管理します。[検証記録](docs/verification/2026-09-06_field-eng-east.md)には匿名化した結果だけを記載します。

## 現在の構築状態

2026-09-08に現行source asset `1.4.5`を新規Databricks AppへGitHubの`main/app`からデプロイし、Deployment、health、既存Index Variant一覧、実RAGチャット、MLflow Trace、PDFリンク引用を確認しました。既存Index専用モードでは、管理者の許可リストに一致するVariantだけを画面へ表示し、過去に作成された許可外Variantは一覧から除外します。実検索時の許可リスト検証はfail-closedのままです。公開用Markdownには実測IDを含めません。`PENDING`は実測していない項目であり、成功扱いにはしません。

| 項目 | 状態 | 実測 |
|---|---|---|
| Unity Catalog／PDF／Document Parsing | `SUCCESS` | 非ARCHIVED Project 11、PDF registry 22。PDFは`ACTIVE` 18件、`DELETING` 0件、論理削除済み4件。Statement `<STATEMENT_ID>` |
| 評価Dataset／baseline chunks | `SUCCESS` | 評価ケース44件。starter内訳は過去snapshotで8 Project×3件。baseline 40 chunks、CDF `true` |
| Model catalog | `SUCCESS` | 同期後49件（Chat 46、Embedding 3）、READYでない選択可能モデル0件 |
| AI Search | `SUCCESS` | endpoint ONLINE、baseline Index ready 40行、4種smoke成功 |
| Index Sync Job | `SUCCESS` | Job `<INDEX_SYNC_JOB_ID>`、run `<DATABRICKS_RESOURCE_ID>` |
| Data Preparation Job | `SUCCESS` | 非車両PDFのprep run `<RESOURCE_ID>`、Job run `<DATABRICKS_RESOURCE_ID>`、Semantic／512 Variant `<RESOURCE_ID>`、Index READY |
| Phase 1〜5 Evaluation Job | `SUCCESS` | Job `<EVALUATION_JOB_ID>`、run `<DATABRICKS_RESOURCE_ID>`、60結果、エラー0、Trace 60、LLM改善提案5 |
| 未ラベルstarter評価 | `SUCCESS` | eval `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`。Phase 1×3 trial、error 0、Answer Correctness `NULL`、提案1件 |
| MLflow Trace | `SUCCESS` | 全60 Traceで`AGENT`配下の`RETRIEVER`／`CHAT_MODEL`／`EVALUATOR`を確認 |
| Databricks App | `SUCCESS` | deployment `<DEPLOYMENT_ID>`、`SUCCEEDED`／`RUNNING`／`ACTIVE`、resource binding 7件 |
| App live read APIs | `SUCCESS` | health HTTP 200、`/api/me`でログインメールを取得、Project別PDF／Variant／評価データ／評価履歴を取得 |
| デプロイ済みasset | `SUCCESS` | GitHub `main/app`、health HTTP 200、asset version `1.4.5` |
| PDF概要監査（直近snapshot） | `SUCCESS` | 20件時点で空欄0件、20〜30字違反0件。`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、`USER=1`。その後追加された削除E2E用2件へは外挿しない |
| App SP権限 | `SUCCESS` | 既存権限checkに加え、`toyota_index_variants`の`SELECT`／`MODIFY`を確認 |
| Local Browser UI | `SUCCESS` | Enter 5連打で質問1件、思考中表示、停止直後の入力復帰、履歴往復277／284 ms、下書き保持、390 px表示、console error 0 |
| Baselineの使用中Variant | `SUCCESS` | `baseline-standard-512-v1`へ復元し、UPDATE／検証SELECTで確認 |
| 非車両PDFのRemote Chat E2E | `SUCCESS` | 旧asset `1.4.1`の履歴。期待回答`30分`と一致、PDF引用1件、Trace `<TRACE_ID>`、PDF／Trace link成功 |
| 非車両ProjectのPhase 1〜5評価 | `SUCCESS` | 旧asset `1.4.1`の履歴。Dataset `security-policy-v1`、5結果、エラー0、Correctness／Groundedness／Citation各5、Trace 5、改善提案5 |
| トヨタ互換回帰 | `SUCCESS` | 旧asset `1.4.1`の履歴。期待回答`60`と一致、引用2件、Trace `<TRACE_ID>` |
| RAG検索データ作成E2E | `SUCCESS` | Semantic／512のsource 6行、Index 6行、別Project行0、Index READY |
| 汎用RAG migration／再デプロイ／非車両PDF E2E | `SUCCESS` | migration、汎用化source、登録・解析・Variant・チャット・引用・評価を実環境で確認 |
| Chat多重送信・停止 | `SUCCESS` | 旧asset `1.4.1`のremote履歴。同一request再送後も保存message 2件、停止API `CANCEL_REQUESTED`、terminal `run.cancelled`、履歴 `CANCELLED` |
| 現行sourceの回帰テスト | `SUCCESS` | asset `1.4.5`、Python 306件、Chat UI 27件、差分check成功 |
| 評価質問の選択 | `SUCCESS` | Project／評価データ版／用途内の登録済み質問を初期全選択。個別選択、すべて選択、選択解除、正解状態・期待回答・正解PDF／ページ、選択件数・最大試行数を表示し、0件では開始不可。API／Jobは選択IDだけを凍結・評価 |
| PDF viewer | `SUCCESS` | 旧assetのremote content APIでPDF 200、Range 206、ETag 304、private cacheを確認。現行assetは削除前後のPDF 200を確認。ローカル再表示296 ms、Project切替時は保持iframeを破棄 |
| PDF単体の論理削除 | `SUCCESS` | asset `1.4.4`でProject `<RESOURCE_ID>`のPDF `<RESOURCE_ID>`をDELETE 202。孤児Chat run 2件とassistant messageを`ERROR`へ整合後、影響旧Variant 4件から後継Variant 2件をREADY化。両Indexで削除PDF hit 0、保持PDFだけを検索 |
| Data Preparation Serverless本番移行 | `SUCCESS` | Job `<DATA_PREPARATION_JOB_ID>`をPerformance optimized／Environment v5へ移行。production run `<DATABRICKS_RESOURCE_ID>`でStandard／512／Qwen3、3 chunks、Index READY。Classic同入力比でSetup 98.95%、Job実処理8.88%、合計71.18%短縮 |
| Data Preparation Classic増強履歴 | `SUCCESS_AFTER_REMEDIATION` | D16 Driver／D8 Worker×2のJob `<DATABRICKS_RESOURCE_ID>`が成功、Standard／512／Qwen3、3 chunks、Index READY。増強前比でJob実処理18.75%、合計6.5%短縮したが、Setup 382秒は変わらなかった |
| TTFT／残り7 chunk profile／SSO済みブラウザE2E | `PENDING` | 品質runからTTFTを推測しない。Standard／256とSemantic／512以外、デプロイ画面の手操作は未確認 |

### RAG検索データ作成の監視と自己回復

- Projectごとにブラウザ側の監視処理を1つだけ動かし、実行中は二重送信を防ぎます。同じProject・同じ設定の有効なrunはサーバー側でも再利用します。
- 9分などの固定打ち切りは設けません。画面移動、Project切替、再読込みの後も、保存したrun IDまたはサーバー側のactive runから監視を復元します。
- Lakeflow Jobsの`QUEUED`、先行Job待ち、コンピュート起動中などを区別して表示し、検証済みWorkspaceのJobリンクだけを表示します。
- Job受付後に応答または`job_run_id`保存だけが失敗しても、同じ`prep_run_id`由来の冪等tokenで自己回復します。権限エラー等では30〜300秒の指数バックオフを使い、3秒ごとの再投入を防ぎます。
- Data Preparation Job `<DATA_PREPARATION_JOB_ID>`はPerformance-optimized Serverless、Standard Environment v5、`max_concurrent_runs=2`です。Notebook taskではtask libraryを使わず、Job Environmentのdependencyとして検証済み`databricks-sdk==0.135.0`を固定します。構成識別用tagは`compute_profile=serverless-performance-optimized-v5`です。
- Performance optimizedは起動時間を優先するため、Standard performance modeよりDBU使用量が増える場合があります。速度だけでなく`system.billing.usage`の実績も継続して比較します。
- PDF解析の`ai_parse_document`は引き続きX-Large Serverless SQL Warehouseで、`READ_FILES(..., format => 'file')`が返す`FILE`値を入力にします。Serverless Jobsへ移したのは後続のチャンク化とsource Delta Table作成、AI Search Index作成・同期依頼です。Index作成・同期自体はAI Search managed serviceが担当します。
- production run `<DATABRICKS_RESOURCE_ID>`（task `<DATABRICKS_RESOURCE_ID>`、prep `<RESOURCE_ID>`、Variant `<RESOURCE_ID>`）はSetup 4秒、Job実処理154秒、合計159.146秒、App E2E 182.4秒で、3 chunksとREADY Indexを確認しました。同入力のClassic run `<DATABRICKS_RESOURCE_ID>`は382秒／169秒／552.328秒であり、合計を71.18%短縮しました。
- 隔離検証では1 PDFのrun `<DATABRICKS_RESOURCE_ID>`が157.875秒、8 PDFのrun `<DATABRICKS_RESOURCE_ID>`が158.368秒で完了しました。後者は8文書、31 chunks、Index READYで、`activate_on_success=false`によりProjectのactive Variantを変更していません。
- Classic増強構成は障害時に同じJob IDへ戻せるよう、[`deployment/jobs/data_preparation_job_classic_fallback.json`](deployment/jobs/data_preparation_job_classic_fallback.json)へ保持しています。Instance Poolはidle instanceを常時warmにするAzure VM費用が発生するため採用していません。
- Serverless JobsはUnity CatalogとStandard access modeを前提とし、Instance Pool、init script、compute-scoped library、Spark UI、compute event logを使いません。Standard memoryは16 GB、High memory 32 GBはPreviewのため、想定最大PDF数でもメモリを確認します。
- Serverlessの実行要件と制約は、Azure Databricks公式の[Serverless Jobs](https://learn.microsoft.com/azure/databricks/jobs/run-serverless-jobs)、[Environment](https://learn.microsoft.com/azure/databricks/compute/serverless/dependencies)、[制約](https://learn.microsoft.com/azure/databricks/compute/serverless/limitations)に合わせています。

### Phase 1〜5の実測比較

同じdevelopment 12問、同じbaseline Standard／512 Variant、同じ回答LLM／Judge LLM、Trial 1で比較したsmoke値です。値の高低だけで採用を決めず、失敗ケースとTraceも確認してください。

| Phase | Recall@10 | Precision@10 | nDCG@10 | Correctness | Groundedness | Citation | E2E p50 | E2E p95 | Total tokens |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.8958 | 0.1833 | 0.7644 | 1.0000 | 1.0000 | 1.0000 | 4,498.8 ms | 9,038.6 ms | 75,010 |
| 2 | 1.0000 | 0.2083 | 0.8519 | 1.0000 | 0.9167 | 0.9167 | 4,038.2 ms | 9,916.4 ms | 71,575 |
| 3 | 0.8681 | 0.1750 | 0.8596 | 1.0000 | 1.0000 | 0.9167 | 4,011.0 ms | 8,861.2 ms | 57,535 |
| 4 | 0.8681 | 0.1750 | 0.8159 | 1.0000 | 1.0000 | 1.0000 | 4,611.0 ms | 8,651.0 ms | 57,511 |
| 5 | 0.8681 | 0.1750 | 0.8393 | 1.0000 | 1.0000 | 1.0000 | 7,983.1 ms | 14,474.9 ms | 59,687 |

このrunは非ストリーミング品質評価です。`server_ttft_ms`／`client_ttft_ms`は全件NULLであり、TTFTの性能runは`PENDING`です。

## リポジトリ構成

| パス | 内容 |
|---|---|
| `sql/` | Unity Catalog、Project、文書、評価Dataset、baseline chunkを作るSQL |
| `jobs/` | データ準備、Phase評価、Index同期のNotebook sourceとunit test |
| `deployment/` | AI SearchとLakeflow Jobsの作成用JSON |
| `app/` | FastAPIバックエンドと4画面のDatabricks App |
| `output/pdf/` | 汎用確認用PDF 1冊、トヨタ評価用PDF 9冊、manifest、固定評価seed |
| `scripts/` | SQL実行、PDF解析、モデル同期、待機、smoke test用helper |
| `config/` | 対象ワークスペースと文書IDの固定値 |

## GitHubからAppだけをデプロイする

配布元は [mikitotsujii5-cpu/RAG-Accuracy-Evaluation](https://github.com/mikitotsujii5-cpu/RAG-Accuracy-Evaluation) の `main` branchです。[`databricks.yml`](databricks.yml)はDatabricks Appだけを宣言し、SQL Warehouse、Unity Catalog、AI Search、Lakeflow Jobs、MLflow Experimentは作成・削除しません。既存リソースのID／名前を変数で受け取り、7件のApp Resource Bindingとして参照します。

GitHub Actionsの定義は [`.github/workflows/deploy-databricks-app.yml`](.github/workflows/deploy-databricks-app.yml) です。OIDCでDatabricksへ接続し、Bundle検証、App作成／更新、GitHub `app/`からの起動、`RUNNING`／`ACTIVE`／health確認を行います。顧客利用時は公開Repositoryをそのまま使わず、組織管理下の非公開Repositoryへforkまたはmirrorし、動作確認済みcommitを固定してください。

## Stepごとの構築と確認

先のStepが合格してから次へ進みます。失敗時は「修正」にある対象だけを直して同じ確認を再実行し、確認結果を残します。エラーを無視して次へ進めません。

### Step 0: 接続先を固定する

構築:

```bash
databricks auth describe --profile <DATABRICKS_CLI_PROFILE>
databricks current-user me --profile <DATABRICKS_CLI_PROFILE>
```

確認:

- hostが `https://<DATABRICKS_WORKSPACE_HOST>` である。
- current userが意図した構築担当者である。
- SQL Warehouse `<SQL_WAREHOUSE_ID>` が利用可能である。

修正:

- hostが違う場合は、そこで中断し `databricks auth login --host https://<DATABRICKS_WORKSPACE_HOST> --profile <DATABRICKS_CLI_PROFILE>` でprofileを直す。
- 認証、Warehouse利用権限、Unity Catalog権限を直した後、同じ確認をやり直す。

### Step 1: ローカル成果物を検証する

構築前に、アプリとJobの契約テストを実行します。

```bash
app/.venv/bin/python -m pytest app/tests jobs/tests -q
cd app && npm run test:chat-ui && cd ..
node --check app/static/app.js
```

確認:

- App／JobのPython testとChat UI testがすべてpassする。件数ではなく終了コードを合格条件にする。
- JavaScriptの構文checkが成功する。
- `git diff --check` が空で終了する。

修正:

- 失敗したテスト名を確認し、該当するsourceまたはdeployment JSONだけを修正する。
- Job sourceを直した場合は、後続Stepで4本すべてを再importする。

### Step 2: Unity Catalogの土台を作る

構築:

```bash
python3 scripts/execute_sql_file.py sql/01_foundation.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>

python3 scripts/execute_sql_file.py sql/07_migrate_generic_document_metadata.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>

python3 scripts/execute_sql_file.py sql/09_document_logical_deletion.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

`07_migrate_generic_document_metadata.sql`は、既存の文書registryへ汎用メタデータ列を追加し、空のタイトルをファイル名から補います。`09_document_logical_deletion.sql`は、Project更新ロック、PDF削除状態、Variant lineage／後継関係の列を既存Tableへ冪等に追加します。`CREATE TABLE IF NOT EXISTS`だけでは既存Tableへ列が追加されないため、どちらのmigrationも省略できません。新規環境でも実行し、schemaを同じ状態へそろえます。

確認:

- Catalog、Schema、VolumeをCatalog Explorerで確認する。
- `sql/01_foundation.sql`に定義されたTableが存在する。
- Source Delta TableがAI Search用のChange Data Feedを有効にできる構成である。
- migration末尾の`missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`がすべて`0`である。
- PDF削除migration末尾の`documents_without_lifecycle`、`variants_without_lifecycle`がともに`0`である。Project、registry、Variantに新しい状態／lineage列がある。

修正:

- 権限エラーは作成者の`USE CATALOG`、`USE SCHEMA`、`CREATE TABLE`、`CREATE VOLUME`を確認する。
- SQLは冪等なので、不足を直した後に同じファイルを再実行する。
- migrationの検証値が1つでも`0`でなければ次のStepへ進まず、該当行のタイトル、タグ配列、`metadata_json.schema_version`を修正してmigrationを再実行する。
- PDF削除migrationの両検証値が`0`でない場合はデプロイせず、対象行の`lifecycle_status`を確認して同じmigrationを再実行する。

### Step 3: Project、model catalog、PDF registryを準備する

構築:

```bash
python3 scripts/execute_sql_file.py sql/02_seed_core.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
python3 scripts/sync_model_catalog.py \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
python3 scripts/execute_sql_file.py sql/02b_fix_model_catalog.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
python3 scripts/execute_sql_file.py sql/03_register_documents.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
```

`sql/02_seed_core.sql`と`sql/03_register_documents.sql`は、同梱のトヨタ評価シナリオを再現するためのseedです。モデル同期の2コマンドは汎用利用でも必要です。汎用RAGの利用者は、画面から任意のProjectとPDFを登録できます。PDFは [output/pdf/README.md](output/pdf/README.md) のルールに従い、Project別のVolume pathへ登録します。トヨタ評価ではD01〜D08をbaseline Project、D09をscan比較Projectに分け、D05とD09を同じIndexへ入れません。

確認:

- baseline ProjectにD01〜D08の8件、scan ProjectにD09の1件がある。
- registryの`sha256`と実ファイルが一致する。
- model catalogには、サーバー側で確認できたFMAPIモデルだけが入り、`READY`かつ`selectable=true`の候補だけをUIで選べる。
- Qwen3 Embedding 0.6Bが利用可能なら別管理の既定値ポリシーにより選択され、利用不可ならREADY候補へfallbackする。fallback先には「推奨」を表示しても、Qwen3以外へ「日本語対応」を誤表示しない。
- 任意PDFの必須入力はPDF本体だけで、文書情報パネルは初期状態で閉じている。タイトル未入力時は安全なファイル名から補完され、解析後は20〜30字のAI概要またはfallback概要を保持する。汎用メタデータを入力した場合はregistryへ保持される。

修正:

- PDFの欠落やhash不一致は、その1ファイルだけを正しいProject pathへ再登録する。
- モデル候補を手入力で推測せず、`sync_model_catalog.py`を再実行する。

### Step 4: `FILE`型でDocument Parsingを確認する

最初にD01だけをsmoke testします。

```bash
python3 scripts/execute_sql_file.py sql/04_parse_d01_smoke.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
```

このSQLは次の経路を使います。

```sql
READ_FILES('<PDF path>', format => 'file').file
  -> ai_parse_document(FILE, map('version', '2.0', ...))
```

`content`などの`BINARY`列は入力に使いません。

確認:

- `parsed:version`が`2.0`である。
- parser errorが空である。
- page情報と本文要素が取得できる。
- `source_file.uri`がregistryの`doc_uri`と一致する。

合格後、残りを解析します。

```bash
python3 scripts/parse_documents.py \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
```

修正:

- `FILE`型またはPreviewのエラーは、FILE type Previewと対応computeを確認する。
- `FILE`型を使うPDF解析はserverless notebook computeへ移さず、本リポジトリで指定したX-Large Serverless SQL Warehouseを使う。後続のData Preparation JobがServerlessであることとは分けて確認する。
- 100 MB超過、暗号化、破損PDFは対象文書だけを修正する。登録済みPDFが`ERROR`の場合はカタログの「再解析」を使い、同じVolume上のPDFを`FILE`型で再処理する。多重クリックでも再解析要求は1件だけ送る。

### Step 5: 評価Datasetとbaseline chunkを作る

構築:

```bash
python3 scripts/execute_sql_file.py sql/05_seed_evaluation.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
python3 scripts/execute_sql_file.py sql/08_seed_starter_evaluation.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
python3 scripts/execute_sql_file.py sql/06_build_baseline_chunks.sql \
  --profile <DATABRICKS_CLI_PROFILE> --warehouse-id <SQL_WAREHOUSE_ID>
```

`sql/05_seed_evaluation.sql`は同梱トヨタ評価シナリオの互換seedです。`sql/08_seed_starter_evaluation.sql`は、解析済みPDFがある既存Projectへ、概要、重要点3つ、手順／条件のサンプル質問を`starter-v1`として3件登録します。期待回答と正解ページは捏造せず空のままです。新しいProjectではData Preparation Job成功時にも同じ3件を作ります。正式評価では、アプリの「RAG精度評価」画面から、そのProjectのPDFに合う質問、期待回答、正解PDF、1始まりの正解ページ、評価データ版、用途を登録します。質問追加フォームは初期状態で閉じており、登録済み質問の一覧を見ながら必要な場合だけ開きます。質問と正解を変更した場合は新しい評価データ版を作り、過去runを再現できるようにします。

確認:

- 評価Datasetの`dataset_version`、Project、文書URI、物理ページ番号がseedと一致する。
- 任意分野のProjectでは、画面から登録した評価質問が同じProject、選択した評価データ版・用途にだけ表示され、正解PDFがそのProjectの`PARSED`／`READY`文書を指す。
- 登録済み質問は初回にすべて選択され、個別選択、「すべて選択」、「選択を解除」が動く。各質問に正解の登録状態、期待回答、正解PDF／ページが表示され、0件選択では評価を開始できない。
- baseline TableにD01〜D08以外の行がない。
- `chunk_id`が一意で、本文、URI、ページ、タイトル、汎用メタデータ、後方互換メタデータ、Variant情報を保持できる。
- Change Data Feedが有効である。

修正:

- qrelsのURI不一致はregistryを先に直してから`05_seed_evaluation.sql`を再実行する。
- chunkの欠落は解析結果を直してから`06_build_baseline_chunks.sql`を再実行する。

### Step 6: AI Search endpointとbaseline Indexを作る

作成用JSONは [deployment/ai_search_endpoint.json](deployment/ai_search_endpoint.json) と [deployment/ai_search_index.json](deployment/ai_search_index.json) です。既存の同名リソースがある場合は重複作成せず、実際の設定とJSONを比較します。

確認:

- endpoint typeが`STANDARD`である。
- Indexは`DELTA_SYNC`、`HYBRID`、`TRIGGERED`である。
- Embedding sourceが`chunk_to_embed`である。
- Indexが`ONLINE`／readyになり、indexed row countがsource Tableと一致する。

確認用helper:

```bash
python3 scripts/wait_for_vector_index.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --index-name <UC_CATALOG>.rag_accuracy.toyota_chunks_standard_512_v1_index \
  --expected-rows <source-tableの確認済み行数>
python3 scripts/smoke_test_ai_search.py --profile <DATABRICKS_CLI_PROFILE>
```

修正:

- row数不一致はsource Table、CDF、pipeline sync statusを確認して同期を再実行する。
- Embedding endpointやdimensionを変えた場合は既存Indexを上書きせず、新しいimmutable VariantとIndexを作る。

### Step 7: Lakeflow Jobsを配置する

[deployment/jobs/README.md](deployment/jobs/README.md) に従い、`jobs/`の4本を同じWorkspace directoryへimportします。3つの実行Notebookが `%run ./job_common` を使うため、相対配置を崩しません。

確認:

- data preparation、evaluation、index syncの3 Jobが存在する。
- Data Preparation Jobが`performance_target=PERFORMANCE_OPTIMIZED`、Standard Environment v5、`max_concurrent_runs=2`である。
- Data Preparation taskの`environment_key`がJob Environmentを参照し、dependencyが`databricks-sdk==0.135.0`、tagが`compute_profile=serverless-performance-optimized-v5`である。taskの`libraries`、`job_cluster_key`、Jobの`job_clusters`を使っていないことも確認する。Evaluation／Index Syncはそれぞれのdeployment JSONに記載したClassic構成と照合する。
- [`deployment/jobs/data_preparation_job_classic_fallback.json`](deployment/jobs/data_preparation_job_classic_fallback.json)がD16 Driver／D8 Worker×2の検証済みClassic構成を保持し、障害時もJob ID `<DATA_PREPARATION_JOB_ID>`を変えずにresetできる。
- run-as user、Notebook path、parameter名がdeployment JSONと一致する。
- 既存Jobを更新した場合は`jobs reset`を使い、Appが参照するJob IDを変えていない。
- Evaluation Jobは`config_json`／`config_hash`に固定した`evaluation_case_ids`だけを処理する。`evaluation_case_ids`のない既存runは従来どおり同じProject・version・splitの全件を処理する。

修正:

- Notebook変更時は4本すべてを再importし、Jobをresetする。
- smoke runはまず`STANDARD / 256`、次に`SEMANTIC / 512`、最後に`PARENT_CHILD / 512`の順で1件ずつ実行し、各run完了後にVariant Table、Index、registryを確認する。

### Step 8: MLflowとDatabricks Appを配置する

MLflow Experimentはsourceに固定された次の値を使います。

| 項目 | 値 |
|---|---|
| Experiment path | `/Users/<DATABRICKS_USER_EMAIL>/<APP_NAME>/mlflow-experiment` |
| Experiment ID | `<MLFLOW_EXPERIMENT_ID>` |

Appは [app/app.yaml](app/app.yaml) のresource keyを使います。作成後に取得したJob ID、Experiment ID、Index、Warehouse、Volume、LLM endpointをDatabricks Apps resourceへ結び付けます。

Appの説明やuser scopeを更新するときも、7件のresourceを含むfull-safe payload [deployment/app_resources_update.json](deployment/app_resources_update.json) または [deployment/app_user_scopes_update.json](deployment/app_user_scopes_update.json) を使います。`--description`だけの更新で既存resourceが外れる事象を実測したため、更新後にresourceが7件あることを確認してからdeployします。

確認:

- Appの表示名が「RAG精度評価アプリ」である。
- `/api/health`が起動状態を返し、画面に未設定resourceの警告がない。
- App service principalに、必要なWarehouse、Table、Volume、Index、LLM endpoint、Job、Experimentの最小権限がある。
- 説明／scope更新後もApp resourceが7件あり、effective scopeが`iam.access-control:read`、`iam.current-user:read`、`model-serving`である。
- サイドバーが「データ準備、PDFカタログ、RAGチャット、RAG精度評価」の順である。
- 右上にDatabricks Appsログインユーザーのメールが表示され、各ページに利用中のDatabricks機能名が表示される。
- アップロード画面の必須項目はPDFだけで、任意項目を閉じられ、車種固有項目を入力しなくても登録できる。
- 「02 検索する文書のチャンク化・ベクトル化」と表示され、利用可能ならQwen3 Embedding 0.6Bが既定値になる。
- カタログに20〜30字の概要があり、PDF content APIのprefetch、`ETag`、`Range`、同一PDF viewer再利用が動く。
- Projectと会話を確認付きで削除でき、進行中処理との競合を安全に拒否する。
- PDF単体をOWNER／EDITORが確認付きで論理削除できる。削除中は連打を防ぎ、影響するVariantだけを検索不可にして、残存PDFで後継Variantを構築・AI Search同期する。原本、解析結果、過去の会話・引用・評価は保持する。
- RAG精度評価画面で、現在のProjectに属する解析済みPDFを正解として評価質問を登録し、評価データ版・用途を選択できる。登録済み質問の個別／一括選択、正解状態と詳細、選択件数と最大試行数が表示され、質問0件では開始できない。

修正:

- `RESOURCE_NOT_READY`の`missing`項目を見て、対応するApp resourceまたは権限だけを直す。
- App secretやtokenをsource、Table、logへ保存しない。
- 共有Variant registry `toyota_index_variants`にはApp service principalの`SELECT`／`MODIFY`を付与し、動的に作る各AI Search Indexには`SELECT`を付与する。
- `toyota_rag_eval_cases`への評価質問登録で権限エラーになる場合は、App service principalの`SELECT`／`MODIFY`を確認する。

### Step 9: アプリからend-to-endで確認する

[APP_USER_GUIDE.md](APP_USER_GUIDE.md) の「最短デモ手順」を実行します。

合格条件:

- PDF登録後、`FILE`型のDocument Parsingが完了する。
- 3手法 × 3サイズのVariantを少なくとも1つずつ作成できる。
- Vector／Hybrid、Metadata Filtering、Reranking、Query OptimizationをPhaseで切り替えられる。
- 回答にProject認可済みのPDF・ページリンクが付き、そのリンクを開ける。
- 回答画面にチャンク本文を表示せず、PDF原文リンクだけを根拠として示す。
- live citationはサーバーが検証した`document_id`と物理ページからPDF URLを組み立て、retrieval SSEにはexcerpt／チャンク本文を含めない。
- 会話履歴がProject内に保存され、別Projectへ混ざらず、確認付きで削除できる。
- ProjectをOWNERが確認付きで削除でき、削除後は通常の一覧から見えない。
- PDF単体をOWNER／EDITORが確認付きで削除でき、カタログと新規検索から直ちに外れる。削除中の再送は冪等で、進行中のデータ準備・チャット・評価・別更新との競合は409になる。
- PDF削除前に、開始から30分を超えた`QUEUED`／`STREAMING`／`CANCEL_REQUESTED`の孤児Chat runだけをguard付きで`ERROR`へ収束し、対応するassistant messageも`ERROR`へそろえる。30分以内のChat runは実行中として409を維持する。
- 削除PDFを含む旧Variantは`SUPERSEDED`となり、残存PDFがあれば同設定の後継VariantがREADYになる。AI Searchの後継Indexに削除PDFのchunkがない。
- 最後のPDFを削除するとProjectは`EMPTY`、`active_variant_id` はNULLになる。一方、削除前の回答のPDF引用はProject権限内で開け、過去の評価結果も残る。
- Data Preparation済みProjectに、正解ラベルを捏造しないサンプル質問3件がある。
- 任意分野のPDFに合う正解付き評価質問を画面から登録し、同じProject、評価データ版、用途に保存できる。
- 同じProject、評価データ版、用途の質問を個別または一括で選び、選択した質問だけをPhase 1〜5へ同じDataset、Variant、回答LLM、Judge LLMで評価できる。
- Retrieval、回答品質、レイテンシ、エラー率と、Phase別のLLM改善提案を表示できる。
- 過去の評価runを選び、保存済みのPhase進捗、指標、改善提案を再表示できる。
- 期待回答・期待事実がないサンプル質問はAnswer Correctnessを`NULL`として平均から除外し、0点として扱わない。
- 改善advisorには検索指標だけでなくAnswer Correctness、Groundedness、Citation Correctnessと各judgeのrationaleを渡す。
- 非車両PDFを別Projectへ登録し、タイトル自動補完、汎用メタデータ、チャット回答、認可済み引用リンクを確認できる。

## 現行sourceの検証結果

2026-09-08にローカルで次を確認しました。

- `app/.venv/bin/python -m pytest app/tests jobs/tests -q`: Python 264 tests pass
- `cd app && npm run test:chat-ui`: Chat UI 21 tests pass
- JavaScript構文2ファイル、Python compile 51ファイル、JSON 19ファイル: pass
- 4画面とPhase 1〜5のUI確認: browser console error 0

現行source asset `1.4.5`はGitHubの`main/app`から新規Appへ配置し、deployment `SUCCEEDED`、App `RUNNING`、compute `ACTIVE`、health HTTP 200、binding 7件を確認しました。許可済み既存Index Variant 1件だけが一覧に表示され、AI Search 10件取得、回答、Trace、PDFリンク引用、`run.completed`まで成功しています。評価質問選択とPDF削除の詳細はasset `1.4.4`の検証履歴として保持します。source testだけを本番稼働の根拠にはしていません。

## デプロイ記録

次の値は実環境で確認したものです。未完了の確認は`PENDING`のまま残します。

| リソース | 匿名化した参照 | 確認日時 | 確認内容 |
|---|---|---|---|
| Data preparation Job（Serverless本番） | `<DATA_PREPARATION_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | 2026-09-08 | Performance optimized／Environment v5。task `<DATABRICKS_RESOURCE_ID>`、prep `<RESOURCE_ID>`、Variant `<RESOURCE_ID>`、3 chunks、Index READY。Setup 4秒、実処理154秒、合計159.146秒、App E2E 182.4秒 |
| Data preparation Serverless隔離検証 | run `<DATABRICKS_RESOURCE_ID>`／`<DATABRICKS_RESOURCE_ID>` | 2026-09-08 | 1 PDFは157.875秒。8 PDFは158.368秒、8文書、31 chunks、Index READY。いずれもactive pointer不変 |
| Data preparation Classic増強履歴 | run `<DATABRICKS_RESOURCE_ID>` | 2026-09-08 | D16 Driver／D8 Worker×2、Standard／512／Qwen3、3 chunks、Index READY。Setup 382秒、実処理169秒、合計552.328秒。fallback JSONとして保持 |
| Evaluation Job | `<EVALUATION_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | 2026-09-06 | `SUCCESS`、Phase 1〜5／development 60結果・エラー0・Trace 60・提案5件 |
| Index sync Job | `<INDEX_SYNC_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | 2026-09-06 | `SUCCESS`、triggered sync |
| Databricks App | `<APP_URL>`／deployment `<DEPLOYMENT_ID>` | 2026-09-08 | GitHub `main/app`、`SUCCEEDED`／`RUNNING`／`ACTIVE`、health HTTP 200、asset `1.4.5`、binding 7件 |
| 選択評価smoke | eval run `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>` | 2026-09-08 | `TERMINATED`／`SUCCESS`。`figure-001`だけ、結果1行、選択外0、error 0。MLflow run `<RESOURCE_ID>` |
| 孤児Chat run回復付きPDF削除 | Project `<RESOURCE_ID>`／document `<RESOURCE_ID>` | 2026-09-08 | DELETE 202、deletion request `<RESOURCE_ID>`。後継prep `<RESOURCE_ID>`／`<RESOURCE_ID>`は両方READY、削除PDF行／hit 0 |
| PDF単体削除E2E（2件→1件） | Project `<RESOURCE_ID>` | 2026-09-08 | helper exit 0。初期Variant `<RESOURCE_ID>`を`SUPERSEDED`にし、後継 `<RESOURCE_ID>`／Index 6行、削除PDF hit 0、保持PDF hit 1を確認 |
| 最後のPDF削除E2E（1件→0件） | Project `<RESOURCE_ID>` | 2026-09-08 | DELETE 202、`EMPTY`、active Variantなし、READY Variant 0、prep 4→4、原本・解析2件・過去引用を保持 |
| 非車両Project | `<RESOURCE_ID>` | 2026-09-07 | PDF登録・3ページ解析・汎用metadata・Semantic／512 Index・Chat・引用・`security-policy-v1`のPhase 1〜5評価を確認 |
| トヨタ互換回帰 | session `<RESOURCE_ID>` | 2026-09-07 | 期待回答`60`と一致、引用2件、Trace link成功 |
| App service principal | client `<APP_SERVICE_PRINCIPAL_ID>`／numeric `<APP_SERVICE_PRINCIPAL_NUMERIC_ID>` | 2026-09-08 | 7 App resources、個別UC grants、動的Index `SELECT`、Variant registry `SELECT`／`MODIFY` |
