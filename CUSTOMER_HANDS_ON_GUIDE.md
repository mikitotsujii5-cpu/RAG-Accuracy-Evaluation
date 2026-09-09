# 顧客向けハンズオン：RAG精度評価アプリ

この手順書は、ハンズオンで実行する操作だけを記載しています。`<...>`は自分の環境の値へ置き換えてください。

## 0. 作成するリソース

| 手動で作成・設定 | GitHubから作成・設定 |
|---|---|
| SQL Warehouse | Databricks App |
| Catalog／Schema／Volume／Delta Tables | App専用サービスプリンシパル |
| AI Search Endpoint／Index | GitHub接続とAppの起動 |
| Lakeflow Jobs 3件 | Resource Binding 7件 |
| MLflow Experiment | ヘルスチェック |
| Index Profile用Embedding／回答・評価用LLMの設定 | |
| Git認証と権限 | |

## 1. 設定値を記録する

作成した値を次の表へ記録します。

| 項目 | 値 |
|---|---|
| Workspace URL | `<WORKSPACE_URL>` |
| GitHub repository URL | `<GITHUB_REPOSITORY_URL>` |
| Branch | `main` |
| App source path | `app` |
| SQL Warehouse名／ID | `<SQL_WAREHOUSE_NAME>`／`<SQL_WAREHOUSE_ID>` |
| Catalog | `<CATALOG>` |
| Schema | `<SCHEMA>` |
| Volume完全名 | `<CATALOG>.<SCHEMA>.<VOLUME>` |
| AI Search Endpoint | `<AI_SEARCH_ENDPOINT>` |
| baseline source Table | `<BASELINE_TABLE>` |
| baseline Index | `<BASELINE_INDEX>` |
| Embedding endpoint | `<EMBEDDING_ENDPOINT>` |
| 回答用LLM endpoint | `<DEFAULT_LLM_ENDPOINT>` |
| 評価用LLM endpoint | `<JUDGE_LLM_ENDPOINT>` |
| Data Preparation Job ID | `<PREP_JOB_ID>` |
| Evaluation Job ID | `<EVAL_JOB_ID>` |
| Index Sync Job ID | `<INDEX_SYNC_JOB_ID>` |
| MLflow Experiment path／ID | `<EXPERIMENT_PATH>`／`<EXPERIMENT_ID>` |
| App名 | `<APP_NAME>` |
| App専用SP client ID | `<APP_SP_CLIENT_ID>` |
| App URL | `<APP_URL>` |

## 2. GitHubリポジトリを開く

1. Databricksの左メニューから`Workspace`を開きます。
2. `Create > Git folder`を押します。
3. 次を入力します。

| 項目 | 入力値 |
|---|---|
| Git repository URL | `<GITHUB_REPOSITORY_URL>` |
| Git provider | `GitHub` |
| Folder name | `rag-accuracy-evaluation` |

4. `Create`を押します。
5. Branchを`main`にします。
6. `app/`、`jobs/`、`sql/`が表示されることを確認します。

確認：GitHub認証を求められた場合は、`Settings > Linked accounts > Add Git credential`からGitHubを接続します。

スクショ：Git folder、`main`、`app/`、`jobs/`、`sql/`が見える画面。

## 3. GitHubコードを確認する

1. Step 2のGit folderを開きます。
2. `app/`、`jobs/`、`sql/`、`databricks.yml`、`.github/workflows/`があることを確認します。
3. `main`の最新状態へ更新します。

確認：未解決のGitエラーが表示されていない。

## 4. SQL Warehouseを作成する

1. 左メニューから`SQL Warehouses`を開きます。
2. `Create SQL warehouse`を押します。
3. 次を設定します。

| 項目 | 設定 |
|---|---|
| Name | `<SQL_WAREHOUSE_NAME>` |
| Type | `Serverless` |
| Cluster size | `X-Large` |
| Auto stop | `10～15分` |

4. `Create`を押します。
5. Statusが`Running`になったら、Warehouse IDをStep 1へ記録します。
6. SQL Editorで次を実行します。

```sql
SELECT 1 AS health_check;
```

確認：結果が`1`。

スクショ：Warehouse名、Serverless、X-Large、Runningが見える画面。

## 5. Catalog、Schema、Volumeを作成する

### 5.1 Catalog

1. 左メニューから`Catalog`を開きます。
2. `Create catalog`を押します。
3. `<CATALOG>`を入力し、`Standard catalog`を選びます。
4. `Create`を押します。

### 5.2 Schema

1. 作成したCatalogを開きます。
2. `Create schema`を押します。
3. `<SCHEMA>`を入力して`Create`を押します。

### 5.3 Volume

1. `<CATALOG>.<SCHEMA>`を開きます。
2. `Create > Volume`を押します。
3. `<VOLUME>`を入力し、`Managed`を選びます。
4. `Create`を押します。

確認：`/Volumes/<CATALOG>/<SCHEMA>/<VOLUME>/`のFiles画面を開ける。

スクショ：Catalog、Schema、Managed Volumeの各作成済み画面。

## 6. Delta Tablesを作成する

1. SQL Editorを開き、Step 4のWarehouseを選びます。
2. 実行するSQL内のCatalog／SchemaをStep 1の値へ置き換えます。
3. 次の順番で実行します。

```text
sql/01_foundation.sql
sql/02_seed_core.sql
sql/07_migrate_generic_document_metadata.sql
sql/09_document_logical_deletion.sql
```

4. Tableを確認します。

```sql
SHOW TABLES IN <CATALOG>.<SCHEMA>;
```

5. baseline TableのChange Data Feedを確認します。

```sql
SHOW TBLPROPERTIES <BASELINE_TABLE>;
```

確認：

- 19個のTableが表示される
- migration SQLの検査値がすべて`0`
- `delta.enableChangeDataFeed`が`true`

スクショ：Table一覧と`delta.enableChangeDataFeed=true`。

## 7. `FILE`型でPDFを解析する

1. Catalog ExplorerでStep 5のVolumeを開きます。
2. `Upload`からサンプルPDFを1件アップロードします。
3. PDFのVolume pathをコピーします。
4. SQL Editorで次を実行します。

```sql
WITH pdf_input AS (
  SELECT file AS source_file
  FROM READ_FILES(
    '<PDF_PATH>',
    format => 'file'
  )
)
SELECT ai_parse_document(
  source_file,
  map('version', '2.0')
) AS parsed
FROM pdf_input;
```

確認：`parsed`列に解析結果が返る。

スクショ：`format => 'file'`、`ai_parse_document`、解析結果が見える画面。

## 8. Index Profile用EmbeddingとLLMを設定する

1. 左メニューから`Serving`を開きます。
2. 利用可能なFMAPI endpointを確認します。
3. 次の3件をStep 1へ記録します。

| 用途 | 選択条件 |
|---|---|
| Index Profile用Embedding | 管理者がbaseline Indexで固定するモデルendpoint |
| 回答用LLM | Tool Calling対応モデル |
| 評価用LLM | 評価に利用できるモデル |

4. `Playground`で回答用LLMを選び、短い質問を送ります。

確認：エラーなく回答が返る。

Embeddingは管理者がAI Search Indexの作成時に固定します。そのIndexと完全に一致するIndex ProfileをAppとLakeflow Jobへ登録してください。Appの利用者はEmbeddingを個別に選択できず、登録済みProfile以外の値は表示されません。固定したEmbeddingを利用できない場合も別Embeddingへ自動で切り替わりません。

スクショ：選択したendpoint名とPlaygroundの応答。

## 9. AI Search EndpointとIndexを作成する

### 9.1 Endpoint

1. 左メニューから`Compute > AI Search`を開きます。
2. `Create endpoint`を押します。
3. `<AI_SEARCH_ENDPOINT>`を入力し、`Standard`を選びます。
4. `Confirm`を押します。
5. Statusが`Online`になるまで待ちます。

### 9.2 baseline Index

1. Catalog Explorerで`<BASELINE_TABLE>`を開きます。
2. `Create > Vector search index`を押します。
3. 次を設定します。

| 項目 | 設定 |
|---|---|
| Index name | `<BASELINE_INDEX>` |
| Index type | `Hybrid` |
| Primary key | `chunk_id` |
| Embedding source | `Compute embeddings` |
| Embedding source column | `chunk_to_embed` |
| Embedding model | `<EMBEDDING_ENDPOINT>` |
| AI Search endpoint | `<AI_SEARCH_ENDPOINT>` |
| Sync mode | `Triggered` |

4. `Advanced settings > Columns to index`で次を選びます。

```text
project_id, document_id, chunk_to_retrieve, parent_chunk_id,
parent_chunk_to_retrieve, doc_uri, page_number, page_numbers,
parent_page_numbers, title, model, model_year, document_type,
vehicle_category, section_title, keywords, variant_id
```

5. `Create`を押します。
6. `Index status = Online`、`Update status = Idle`になるまで待ちます。

スクショ：Index設定画面と、Online／Idleの結果画面。

## 10. MLflow Experimentを作成する

1. 左メニューから`Experiments`を開きます。
2. `Create experiment`を押します。
3. `<EXPERIMENT_PATH>`を入力して作成します。
4. Experiment IDをStep 1へ記録します。

確認：Experimentの画面を開ける。

スクショ：Experiment名とID。不要な利用者情報は写さないでください。

## 11. Lakeflow Jobsを作成する

1. Git folder内の次の4ファイルを、同じWorkspace folderへImportします。

```text
jobs/job_common.py
jobs/data_preparation_job.py
jobs/evaluation_job.py
jobs/index_sync_job.py
```

2. 左メニューから`Jobs & Pipelines`を開きます。
3. `Create > Job`から次の3件を作成します。

| Job | Notebook | Compute | Parameter |
|---|---|---|---|
| Data Preparation | `data_preparation_job.py` | Serverless、Performance optimized、Environment version 5、`databricks-sdk==0.135.0` | `prep_run_id` |
| Evaluation | `evaluation_job.py` | DBR 18.x、User isolation、`Standard_D4s_v5`×1 | `eval_run_id` |
| Index Sync | `index_sync_job.py` | DBR 18.x、User isolation、`Standard_D4s_v5`×1 | なし |

4. Evaluationへ次のLibrariesを追加します。

```text
databricks-sdk==0.135.0
databricks-ai-search==0.78
```

5. Index Syncへ`databricks-sdk==0.135.0`を追加します。
6. 3件すべてでQueueを有効にします。
7. 各Job IDをStep 1へ記録します。

確認：3件のJobが保存され、Notebook pathとparameterが正しい。

スクショ：各JobのTask、Compute、Parameterが見える画面。

## 12. Genie Codeで環境値を反映する

1. Step 2のGit folderを開きます。
2. Genie Codeを開き、次を送ります。

```text
このRAG精度評価アプリを、手動作成済みのDatabricksリソースへ接続してください。

- App sourceはmain branchのapp/
- Warehouse、Catalog、Schema、Volume、Delta Tables、AI Search、Lakeflow Jobs、MLflow Experiment、FMAPI endpointは新規作成しない
- AI Search Indexは既存Indexの同期と検索だけに使う
- databricks.ymlはAppと7つのResource Bindingだけを定義する
- 秘密情報、個人名、メールアドレス、Workspace IDをコードへ書かない
- commitとpushは行わない

必要なリソース名とIDを1項目ずつ質問してください。
```

3. Step 1の値を順番に回答します。
4. Changed filesを確認します。
5. 秘密情報や個人情報が追加されていないことを確認します。

スクショ：Changed filesの一覧。設定値は写さないでください。

## 13. GitHub ActionsのOIDCを設定する

### 13.1 Databricks

1. デプロイ用サービスプリンシパルを作成し、Workspaceへ追加します。
2. Account Consoleでサービスプリンシパルを開きます。
3. `Credentials & secrets > Federation policies`を開きます。
4. GitHub Environment `prod`用のpolicyを作成します。

```text
repo:<organization>/<repository>:environment:prod
```

### 13.2 GitHub

1. Repositoryの`Settings > Environments`を開きます。
2. `prod`を作成します。
3. Environment variablesへ次を登録します。

```text
DATABRICKS_HOST
DATABRICKS_CLIENT_ID
APP_NAME
GIT_REPOSITORY_URL
GIT_BRANCH
APP_SOURCE_PATH
UC_CATALOG
UC_SCHEMA
UC_VOLUME_FULL_NAME
SQL_WAREHOUSE_ID
DATABRICKS_WORKSPACE_UI_HOST
VECTOR_SEARCH_ENDPOINT
BASELINE_INDEX_FULL_NAME
DEFAULT_LLM_ENDPOINT
MLFLOW_EXPERIMENT_ID
MLFLOW_TRACING_SQL_WAREHOUSE_ID
PREP_JOB_ID
EVAL_JOB_ID
```

4. `GIT_BRANCH`は`main`、`APP_SOURCE_PATH`は`app`にします。

スクショ：Federation policyとGitHub Environmentの変数名。値は写さないでください。

## 14. Appを作成する

1. Genie CodeのChanged filesを確認します。
2. 秘密情報や個人情報がないことを確認します。
3. `Commit & Push`を実行します。
4. GitHubの`Actions > Deploy Databricks App`を開きます。
5. `Run workflow`を押します。
6. `bootstrap_only = true`、Branch `main`で実行します。
7. `Validate App bundle`と`Create or update App and seven bindings`が成功したことを確認します。
8. Databricksの`Apps`から`<APP_NAME>`を開きます。
9. App専用サービスプリンシパルのclient IDをStep 1へ記録します。

スクショ：GitHub Actionsの成功画面とDatabricks AppのOverview。

## 15. AppのGit認証と権限を設定する

1. AppのOverviewで`Configure Git credential`を押します。
2. `GitHub`を選び、対象Repositoryへの読み取り権限を設定します。
3. Git credentialが`Configured`になったことを確認します。
4. `deployment/app_uc_grants.sql`を開きます。
5. Catalog、Schema、App専用SP client IDを今回の値へ置き換えます。
6. SQL Editorで実行します。

確認：すべての`GRANT`が成功する。

スクショ：Git credentialのConfigured表示とGRANTの成功結果。tokenやclient IDは写さないでください。

## 16. Resource Bindingを確認してデプロイする

1. Appの`Resources`を開きます。
2. 次の7件を確認します。

| Binding key | 接続先 |
|---|---|
| `app-warehouse` | SQL Warehouse |
| `toyota-volume` | Volume |
| `baseline-index` | AI Search Index |
| `default-llm` | 回答用LLM endpoint |
| `prep-job` | Data Preparation Job |
| `eval-job` | Evaluation Job |
| `mlflow-experiment` | MLflow Experiment |

3. GitHubの`Actions > Deploy Databricks App`を開きます。
4. `Run workflow`を押します。
5. `bootstrap_only = false`、Branch `main`で実行します。
6. すべてのStepが成功するまで待ちます。
7. Databricks AppsでStatusが`Running`であることを確認します。
8. App URLをStep 1へ記録して開きます。

確認：`/api/health`の確認を含むGitHub Actionsが成功する。

スクショ：7件のResources、GitHub Actions成功、AppのRunning表示。

## 17. RAGアプリを操作する

### 17.1 ProjectとPDF

1. App名が`RAG精度評価アプリ`であることを確認します。
2. `新しいプロジェクト`からProjectを作成します。
3. `RAG検索データを作成・同期`を開き、PDFをアップロードします。
4. 「利用できる検索設定」に、AppとLakeflow Jobへ登録したIndex Profileだけが表示されることを確認します。Profileが1件ならチャンク方式、チャンクサイズ、Embeddingが固定表示され、選択欄は表示されません。複数件なら登録済みProfileだけを選択でき、未登録の値は表示されません。

5. 対象PDFを選び、`RAG検索データを作成・同期`を押します。
6. 状態が`READY`になるまで進捗表示を確認します。
7. `PDFカタログ`でタイトル、概要、PDFリンクを確認します。
8. `PDFを開く`を押し、全画面で全ページをスクロールできることを確認します。
9. `別タブで開く`、`ダウンロード`、Escで閉じる操作を確認します。

### 17.2 RAGチャット

1. `RAGチャット`を開きます。
2. テスト質問から質問を選びます。
3. `Vector Search`で送信します。
4. `Hybrid Search`でも同じ質問を送信します。
5. 回答、PDF根拠リンク、ページ番号を確認します。
6. 生成中にspinnerと停止ボタンが表示されることを確認します。
7. 会話履歴の作成、切り替え、削除を確認します。

### 17.3 精度評価

1. `RAG精度評価`を開きます。
2. 正解付きの評価質問を3件登録します。

| 質問 | 正解欄に登録する内容 |
|---|---|
| 2024年式プリウスのE-Fourの型式は？ | PDFに記載された型式 |
| 2024年式プリウスGグレードのSEA設定は？ | PDFに記載された設定 |
| ZVW60救援作業の高電圧遮断手順は？ | PDFに記載された手順 |

3. `比較条件を選択`でPhase 1～5を選びます。
4. `RAG精度を比較`を押します。
5. spinnerと進捗表示を確認します。
6. 各Phaseの次の結果を比較します。

```text
Recall@k、Precision@k、nDCG@k
Answer Correctness、Groundedness、Citation Correctness
TTFT、End-to-end latency、トークン使用量
精度改善の提案
```

7. MLflow Traceへのリンクを開きます。
8. 画面内の`ai_parse_document`、AI Search、Catalog、Volumeなどの`↗`リンクを開きます。

スクショ：Project、PDFのREADY、チャット回答とPDFリンク、Phase 1～5の比較結果、MLflow Trace。

## 18. 最終確認

- [ ] SQL Warehouseが`Running`
- [ ] Catalog、Schema、Volume、19個のDelta Tableが存在する
- [ ] `FILE`型を使った`ai_parse_document`が成功する
- [ ] Index Profileに固定したEmbedding、回答用LLM、評価用LLMが利用できる
- [ ] AI Search Endpointが`Online`
- [ ] baseline Indexが`Online／Idle`
- [ ] Lakeflow Jobs 3件が存在する
- [ ] MLflow Experimentが存在する
- [ ] Resource Binding 7件が正しい
- [ ] GitHub Actionsが成功する
- [ ] Appが`Running`
- [ ] PDFのVariantが`READY`
- [ ] RAG回答にPDF根拠リンクが表示される
- [ ] Phase 1～5の評価結果と進捗が表示される

スクショには、メールアドレス、token、client secret、実リソースIDを写さないでください。
