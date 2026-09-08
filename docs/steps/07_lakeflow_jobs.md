# Step 7: Lakeflow Jobsを配置して実行する

## 目的

時間のかかるデータ準備、Phase評価、Index同期をAppのHTTP requestから切り離し、非同期Jobとして実行します。

## 構築

最初に、[Jobs README](../../deployment/jobs/README.md)の4本のimport commandを実行します。3本の実行Notebookが`%run ./job_common`を使うため、同じWorkspaceディレクトリへ置きます。

新規環境でだけJobを作ります。

```bash
databricks jobs create \
  --json @deployment/jobs/data_preparation_job.json \
  --profile <DATABRICKS_CLI_PROFILE>
databricks jobs create \
  --json @deployment/jobs/evaluation_job.json \
  --profile <DATABRICKS_CLI_PROFILE>
databricks jobs create \
  --json @deployment/jobs/index_sync_job.json \
  --profile <DATABRICKS_CLI_PROFILE>
```

既存Jobの設定を更新するときは、新しいJobを重複作成しません。`job_id`と`new_settings`を持つreset bodyを作り、同じIDを保ちます。例:

```bash
jq --argjson job_id <DATA_PREPARATION_JOB_ID> \
  '{job_id: $job_id, new_settings: .}' \
  deployment/jobs/data_preparation_job.json \
  > /tmp/toyota_data_preparation_serverless_reset.json

databricks jobs reset \
  --json @/tmp/toyota_data_preparation_serverless_reset.json \
  --profile <DATABRICKS_CLI_PROFILE>
```

`jobs reset`へsettings JSONだけを直接渡さず、必ず上の`job_id`／`new_settings` wrapper形式を使います。Serverless互換性の問題が確認された場合は、実行中のData Preparationが終端状態になったことを確認してから、同じJob IDをClassic fallbackへ戻します。

```bash
jq --argjson job_id <DATA_PREPARATION_JOB_ID> \
  '{job_id: $job_id, new_settings: .}' \
  deployment/jobs/data_preparation_job_classic_fallback.json \
  > /tmp/toyota_data_preparation_classic_rollback.json

databricks jobs reset \
  --json @/tmp/toyota_data_preparation_classic_rollback.json \
  --profile <DATABRICKS_CLI_PROFILE>

databricks jobs get <DATA_PREPARATION_JOB_ID> --profile <DATABRICKS_CLI_PROFILE> -o json
```

同じIDを保つため、Appの`PREP_JOB_ID`は変更しません。rollback後は新しい隔離smokeを実行し、prep `SUCCEEDED`、sourceとIndexの行数一致、Index `READY`を確認します。失敗したVariantや`SUPERSEDED` Variantを手動でactiveへ戻しません。

## 確認

Job定義:

```bash
databricks jobs get <DATA_PREPARATION_JOB_ID> --profile <DATABRICKS_CLI_PROFILE> -o json
databricks jobs get <EVALUATION_JOB_ID> --profile <DATABRICKS_CLI_PROFILE> -o json
databricks jobs get <INDEX_SYNC_JOB_ID> --profile <DATABRICKS_CLI_PROFILE> -o json
```

Index Sync smoke:

```bash
databricks jobs run-now <INDEX_SYNC_JOB_ID> \
  --profile <DATABRICKS_CLI_PROFILE> --no-wait
python3 scripts/wait_for_job_run.py \
  --profile <DATABRICKS_CLI_PROFILE> --run-id <返されたrun_id>
```

Data Preparation／Evaluationは、AppがProjectを認可し、run管理Tableへhash付きの`QUEUED`行を保存してから起動します。物理Table名やIndex名をJob parameterへ直接渡しません。

本番Data Preparation Jobは、チャンク作成開始までの待ち時間を短くするため、次のPerformance-optimized Serverless構成にします。

| 設定 | 値 | 初心者向けの意味 |
|---|---|---|
| Compute | Lakeflow Jobs Serverless | JobごとにDriver／WorkerのVMサイズを管理しない実行方式 |
| Performance target | `PERFORMANCE_OPTIMIZED` | Standard Serverlessより起動時間を優先する設定 |
| Environment | key `toyota_rag_serverless_v5`、version `5` | Notebook taskが使うServerless実行環境 |
| 同時実行数 | `max_concurrent_runs=2` | 独立したVariant再構築を最大2件まで並行実行 |
| Environment dependency | `databricks-sdk==0.135.0` | HYBRID Index作成に必要な`IndexSubtype`を含む検証済み版を固定する |
| tag | `compute_profile=serverless-performance-optimized-v5` | 利用量画面で本番構成を識別するための目印 |

Serverless notebook taskではtask-levelの`libraries`を使用できません。このため、SDKは`tasks[].libraries`ではなく`environments[].spec.dependencies`へ指定します。このJobが担当するのは`BUILD_VARIANT`のチャンク化、source Delta Table作成、AI Search Index作成・同期の依頼です。アップロード直後の`ai_parse_document`は移行対象ではありません。引き続き`READ_FILES(..., format => 'file')`の`FILE`値を使い、X-Large Serverless SQL Warehouseで実行します。AI SearchのIndex作成・同期そのものはmanaged service側の処理です。

Performance optimizedは起動時間を優先し、Standard performance modeは通常4〜6分の起動待ちを許容する代わりにDBU使用量を抑えます。本番では実測latencyと`system.billing.usage`を一緒に確認します。

障害時に切り戻せるよう、移行前のDBR `18.x-scala2.13`、Driver `Standard_D16s_v5`、Worker `Standard_D8s_v5`×2の定義を[`data_preparation_job_classic_fallback.json`](../../deployment/jobs/data_preparation_job_classic_fallback.json)として保持します。これはfallbackであり、本番の通常経路ではありません。

Evaluation作成APIから質問IDが指定された場合は、1〜1000件、空・重複なし、同じProject・評価データ版・用途への所属を検証し、`evaluation_case_ids`を`config_json`と`config_hash`へ固定します。Evaluation Jobはその固定済みIDだけを再取得し、全IDが一致することを確認してからPhase比較を実行します。`evaluation_case_ids`を持たない既存runは、後方互換のため同じProject・版・用途の全質問を処理します。

Data PreparationがVariantとIndexを正常に作成した後、そのProjectに`starter-v1`のサンプル質問3件を冪等に登録します。これらは期待回答と正解ページを空にした試用データで、正解ラベルを捏造しません。

PDF単体の論理削除も、同じData Preparation Jobで後継Variantを作ります。Appは、削除PDFを含む既存Variantの保存済み設定と`source_document_ids`をサーバー側で検証し、削除PDFを除いた文書IDだけの`BUILD_VARIANT` runを作ります。旧Variantは上書きせず`SUPERSEDED`とします。元のactive Variantの後継だけが`activate_on_success=true`、その他の後継は`false`となり、複数Jobの完了順でactive pointerが変わらないようにします。残るPDFがないVariantに空の後継Indexは作りません。

Data Preparationの重複防止と回復契約:

- 同じProject・同じ設定hashのactive Buildがあれば既存runを返し、新しい制御行やJobを増やしません。INSERTが競合した場合も最古のrunを採用します。
- Jobs APIへ渡すidempotency tokenは、永続化済みの`prep_run_id`から作ります。Appへの応答だけを失った場合でも、同じtokenの再試行で同じJob runを回収します。
- `job_run_id`がNULLのまま残ったactive `BUILD_VARIANT`は、個別run GETまたはactive-run GETで自己回復します。`PARSE_ONLY`や終端済みrunはこの経路から起動しません。
- Job権限エラーなどが続く場合は30、60、120、240、最大300秒の指数バックオフを使います。3秒間隔の画面pollごとに`run-now`を呼びません。
- ViewerのGETが行うのは、EditorのPOSTが事前検証して保存した既存`BUILD_VARIANT`の回復だけです。GETから設定、物理Job ID、Table名、Index名を指定できず、Job側でもProjectと設定hashを再検証します。

合格条件:

- 3 Jobが指定IDで存在し、Notebook pathとparameterがdeployment JSONに一致する。
- Data Preparationは`PERFORMANCE_OPTIMIZED` Serverless、Environment version `5`、`max_concurrent_runs=2`、Environment dependency `databricks-sdk==0.135.0`、tag `compute_profile=serverless-performance-optimized-v5`である。task-levelの`libraries`を持たない。
- Data PreparationはProject外のdocumentを拒否し、作成IndexだけをApp SPへ`SELECT`付与する。
- 同じ`prep_run_id`を再確認してもJob runが重複せず、NULLの`job_run_id`を自己回復できる。
- Evaluationは同じProject、Dataset、Variant、モデル、設定hashだけを処理する。
- `evaluation_case_ids`があるEvaluation runは指定IDだけを処理し、別Project・別version・別splitへの逸脱を拒否する。fieldのない旧runは全件評価を維持する。
- Data Preparation成功後にProject内だけへスターター質問3件があり、別ProjectのPDF URIが混入しない。
- PDF削除で作られた後継runは、元Variantの設定を保持し、削除PDFの`document_id`を含まない。非active Variantの後継が先に完了してもProjectのactive pointerを上書きしない。
- 後継IndexがREADYとなり、sourceとIndexのどちらにも削除PDFのchunkが0件である。
- runは`TERMINATED`かつ`result_state=SUCCESS`になる。

## 失敗時の修正

- `%run ./job_common`が失敗したら4 Notebookの相対配置とimport形式`SOURCE`／`PYTHON`を確認します。
- Data Preparationでdependency解決が失敗する場合は、Environment versionと`environments[].spec.dependencies`を確認します。Serverless notebook taskの`tasks[].libraries`へ移して回避しません。Evaluation／Index SyncのClassic task libraryとは分けて確認します。
- Job起動は成功して処理が失敗する場合、JobのRun as identityにVolume、Table、AI Search、model endpointの権限があるか確認します。
- `QUEUED`が長い場合は、Appに表示されるqueue理由とJobリンクを確認します。先行run待ちとServerless compute準備を分けて確認します。
- チャンク作成が速くなってもIndex同期で待つ場合は、AI Search pipeline statusを確認します。managed service側の同期時間はServerless performance targetでは短縮されません。
- Serverless固有の非互換が確認できた場合だけ、上記wrapperでClassic fallbackへrollbackします。互換性を確認せずに別Jobを新規作成したり、Appの`PREP_JOB_ID`を付け替えたりしません。
- 正常な`BUILD_VARIANT`行で`job_run_id`だけがNULLなら、新しいrunを手作業で増やさず、Appの個別run GET／active-run GETによる自己回復を待ちます。
- server-side run rowが改変されている、Project／run type／設定hashが不一致、または終端済みなら同じIDを再利用しません。原因を直してAppから新しいrunを作ります。
- 選択評価で件数が合わない場合は、Eval runの`config_json`／`config_hash`、`evaluation_case_ids`、case TableのProject・version・splitを確認します。Job parameterへcase IDや物理Table名を追加して回避しません。
- PDF削除の後継runが失敗した場合は、旧`SUPERSEDED` Variantを手動で検索可能に戻しません。対象`prep_run_id`とJob logで原因を修正し、削除PDFを含まない同じ構成の新しいVariantを作成します。

## この環境の実測結果

| 対象 | ID／run | 状態 |
|---|---|---|
| Data Preparation Job（本番Serverless） | `<DATA_PREPARATION_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | `SUCCESS` |
| Data Preparation Serverless 1 PDF canary | run `<DATABRICKS_RESOURCE_ID>` | `SUCCESS` |
| Data Preparation Serverless 8 PDF canary | run `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>` | `SUCCESS` |
| Data Preparation Classic増強履歴 | `<DATA_PREPARATION_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | `SUCCESS_AFTER_REMEDIATION` |
| Index Sync Job | `<INDEX_SYNC_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | `SUCCESS` |
| Phase Evaluation Job（トヨタbaseline履歴） | `<EVALUATION_JOB_ID>`／run `<DATABRICKS_RESOURCE_ID>` | `SUCCESS` |

最新のData Preparation smokeでは、非車両Projectからprep run `<RESOURCE_ID>`、Semantic／512 Variant `<RESOURCE_ID>`を作成しました。Job runは`<DATABRICKS_RESOURCE_ID>`、動的Indexは`<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`です。4/4完了、Index `READY`、source 6行、Index 6行、別Project行0、`job_run_url`は同一Workspaceを指すことを確認しました。

本番Job `<DATA_PREPARATION_JOB_ID>`はPerformance-optimized Serverless Environment version `5`へ同じIDのままreset済みです。本番確認run `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>`は、prep `<RESOURCE_ID>`、Variant `<RESOURCE_ID>`、Standard／512／Qwen3で成功しました。Setup `4,000 ms`、Job実処理 `154,000 ms`、合計 `159,146 ms`、App E2E `182.4秒`、source／Index各3 chunks、Index `READY`です。

同じ入力のClassic増強run `<DATABRICKS_RESOURCE_ID>`はSetup `382,000 ms`、Job実処理 `169,000 ms`、合計 `552,328 ms`でした。本番Serverlessでは合計が約71.2%短縮されています。この比較は各1回の実測であり、負荷試験の分布ではありません。

Serverless移行前に、1 PDF canary run `<DATABRICKS_RESOURCE_ID>`を実行し、合計 `157,875 ms`で成功しました。続いて8 PDFの隔離canary run `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>`、prep `<RESOURCE_ID>`、Variant `<RESOURCE_ID>`を実行しました。Setup `5,000 ms`、実処理 `152,000 ms`、合計 `158,368 ms`、8文書31 chunks、Index `READY`です。`activate_on_success=false`を使い、baseline active Variantが変わっていないことも確認しました。

過去のClassic増強履歴も保持します。同じ1 PDF、Standard／512、Qwen3のJob `<DATABRICKS_RESOURCE_ID>`は`TERMINATED`／`SUCCESS`、prep `<RESOURCE_ID>`、Variant `<RESOURCE_ID>`、source／Index各3行、Index READYでした。増強前run `<DATABRICKS_RESOURCE_ID>`との比較はSetup 382秒→382秒、Job実処理208秒→169秒、合計590.740秒→552.328秒です。最初のsmoke `<DATABRICKS_RESOURCE_ID>`ではDBR 18同梱SDKに`IndexSubtype`がないことを検出し、Classicでは`databricks-sdk==0.135.0`をtask libraryへ戻して成功しました。Serverlessでは同じ依存をEnvironmentへ指定します。

PDF削除専用の後継Build／Syncは`SUCCESS`です。最新の`job_common.py`と`data_preparation_job.py`をWorkspaceへ再importし、既存Job `<DATA_PREPARATION_JOB_ID>`をresetした後、新しい使い捨てProject `<RESOURCE_ID>`で2件→1件を最初から確認しました。初期runはprep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`です。1件削除後は後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`が`SUCCEEDED`／`READY`となりました。後継sourceとIndexは各6行、削除PDF行／検索hit 0、保持PDF行6／検索hit 1です。

過去のscan ProjectではStandard／256のVariant `<RESOURCE_ID>`も作成し、source 8行、Index 8行、`ready=true`、別Project行0、App SPへの個別`SELECT`を確認済みです。実Workspaceで確認済みのprofileはStandard／256とSemantic／512の2組で、残り7組は`PENDING`です。

重複防止、冪等再試行、単一poller、無期限監視、queue表示、Project切替後の復元、`job_run_id`自己回復、選択した評価caseだけの実行、PDF削除後の後継Buildを含む現行App／Job sourceは、Python 264件とChat UI 21件（合計285件）の自動テストですべて成功しています。Data Preparation Job `<DATA_PREPARATION_JOB_ID>`とEvaluation Job `<EVALUATION_JOB_ID>`は最新Notebookを再importして同じJob IDへreset済みです。現行asset `1.4.4`はdeployment `<DEPLOYMENT_ID>`へ配置済みです。選択評価run `<RESOURCE_ID>`はcase `figure-001`だけを処理し、Job `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>`が`TERMINATED`／`SUCCESS`、結果1行、選択外0行、error 0で完了しました。MLflow runは`<RESOURCE_ID>`です。

同じassetのPDF削除では、後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`がREADYとなりsource 8行、削除PDF 0行、保持PDF 8行でした。もう一つの後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`もREADYとなりsource 4行、削除PDF 0行、保持PDF 4行でした。両AI Search実検索は保持document `<RESOURCE_ID>`だけを返し、削除documentを返しませんでした。

未ラベルstarter質問をAnswer Correctnessの0点として数えず`NULL`のまま集計する最終実環境確認も、eval run `<RESOURCE_ID>`／Job run `<DATABRICKS_RESOURCE_ID>`で`TERMINATED`／`SUCCESS`になりました。Phase 1、trial 3、error rate 0、Answer Correctness `NULL`、Groundedness `0.66667`、Citation Correctness `1.0`、LLM改善提案1件を確認しました。

Evaluationの初回run `<DATABRICKS_RESOURCE_ID>`は`FAILED`でした。Luna endpointが`temperature=0.1`を`unsupported_value`として拒否したことを最小queryで再現し、optional sampling値を送らないように修正しました。回帰テスト、Notebook再import、Job reset、Luna最小queryを合格させた後、Phase 1の機能確認run `<DATABRICKS_RESOURCE_ID>`とTrace修正run `<DATABRICKS_RESOURCE_ID>`が成功しました。

最終的に、同じdevelopment 12問、baseline Standard／512 Variant、回答Luna、Judge Terra、Trial 1を固定したPhase 1〜5 run `<DATABRICKS_RESOURCE_ID>`が`TERMINATED`／`SUCCESS`になりました。60結果はすべて一意、エラー0、Trace ID 60件すべてnon-nullかつ一意です。各PhaseにLLM生成の改善提案を1件、合計5件保存しました。MLflow run IDは`<RESOURCE_ID>`です。

非車両Projectの評価run `<RESOURCE_ID>`も`SUCCESS`です。Dataset `security-policy-v1`の1問をPhase 1〜5で評価し、5結果、エラー0、Correctness／Groundedness／Citation各5件、Trace 5件、改善提案5件を確認しました。

品質runは非ストリーミングなので、`server_ttft_ms`と`client_ttft_ms`は全60件NULLです。TTFTを0とせず、別のストリーミング性能runを`PENDING`として残します。

## 公式ドキュメント

- [Azure Databricks: Serverless computeでLakeflow Jobsを実行する](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
- [Azure Databricks: Serverless Environmentと依存関係](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/dependencies)
- [Azure Databricks: Serverless computeの制限](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/limitations)
- [Azure Databricks: JobをServerlessへ移行する](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/migration)
- [Databricks CLIからWorkspaceへimportする](https://docs.databricks.com/aws/en/dev-tools/cli/reference/workspace-commands)
- [Databricks CLIのJobs commands](https://docs.databricks.com/aws/en/dev-tools/cli/reference/jobs-commands)
