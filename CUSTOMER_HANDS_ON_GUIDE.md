# 顧客向けハンズオン：RAG精度評価アプリ

この手順書は、ハンズオンで実行する操作だけを記載しています。`<...>`は自分の環境の値へ置き換えてください。

## 0. 作成するリソース

| Step 11までに手動で作成・設定 | Step 12でGenie Codeが自動作成・設定 |
|---|---|
| SQL Warehouse | Databricks App |
| Catalog／Schema／Volume／Delta Tables | App専用サービスプリンシパル |
| AI Search Endpoint／Index | GitHub `main/app`の接続とAppの起動 |
| Lakeflow Jobs 3件 | Resource Binding 7件 |
| MLflow Experiment | Appサービスプリンシパルへの権限付与 |
| Index Profile用Embedding／回答・評価用LLMの選択 | デプロイとヘルスチェック |
| Git folderとGitHub認証 | |

## 1. 設定値を記録する

作成した値を次の表へ記録します。

| 項目 | 値 |
|---|---|
| Workspace URL | `<WORKSPACE_URL>` |
| GitHub repository URL | `https://github.com/mikitotsujii5-cpu/RAG-Accuracy-Evaluation.git` |
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
| App技術名 | `<APP_NAME>`（小文字英数字と`-`のみ） |
| App表示名 | `<APP_DISPLAY_NAME>` |

例：技術名を`rag-accuracy-final-test`、表示名を`RAG精度評価_最終test版`にします。

## 2. GitHubリポジトリを開く

1. Databricksの左メニューから`Workspace`を開きます。
2. `Create > Git folder`を押します。
3. 次を入力します。

| 項目 | 入力値 |
|---|---|
| Git repository URL | `https://github.com/mikitotsujii5-cpu/RAG-Accuracy-Evaluation.git` |
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

| Job | Notebook | Compute | Environment dependency | Parameter |
|---|---|---|---|---|
| Data Preparation | `data_preparation_job.py` | Serverless／Performance optimized／Environment 5 | `databricks-sdk==0.135.0` | `prep_run_id` |
| Evaluation | `evaluation_job.py` | Serverless／Performance optimized／Environment 5 | `databricks-sdk==0.135.0`、`databricks-ai-search==0.78` | `eval_run_id` |
| Index Sync | `index_sync_job.py` | Serverless／Performance optimized／Environment 5 | `databricks-sdk==0.135.0` | なし |

4. EvaluationのNotebook parameterへ、Step 1のMLflow Experiment path／IDとSQL Warehouse IDを設定します。
5. Data Preparationの`Max concurrent runs`を`2`、ほかの2件を`1`にします。
6. 3件すべてでQueueを有効にします。
7. 各Job IDをStep 1へ記録します。

確認：3件のJobが保存され、Notebook path、Serverless Environment、parameterが正しい。

スクショ：各JobのTask、Serverless、Environment 5、Parameterが見える画面。

## 12. Genie CodeでAppを自動作成する

Databricks Appの技術名には、小文字英数字と`-`だけを使えます。日本語名は`App表示名`として説明欄へ設定します。

1. Step 2のGit folderを開き、Genie Codeを開きます。
2. 次の`<...>`をStep 1の値へ置き換え、プロンプトを1回だけ送ります。

```text
次のGitHubコードと手動作成済みリソースを使い、Databricks Appを全自動で作成・デプロイしてください。

GitHub: https://github.com/mikitotsujii5-cpu/RAG-Accuracy-Evaluation.git
Branch: main
Source: app
App技術名: <APP_NAME>
App表示名: <APP_DISPLAY_NAME>
SQL Warehouse ID: <SQL_WAREHOUSE_ID>
Catalog: <CATALOG>
Schema: <SCHEMA>
Volume: <CATALOG>.<SCHEMA>.<VOLUME>
AI Search Endpoint: <AI_SEARCH_ENDPOINT>
baseline Index: <BASELINE_INDEX>
Embedding endpoint: <EMBEDDING_ENDPOINT>
回答用LLM endpoint: <DEFAULT_LLM_ENDPOINT>
評価用LLM endpoint: <JUDGE_LLM_ENDPOINT>
Data Preparation Job ID: <PREP_JOB_ID>
Evaluation Job ID: <EVAL_JOB_ID>
Index Sync Job ID: <INDEX_SYNC_JOB_ID>
MLflow Experiment path: <EXPERIMENT_PATH>
MLflow Experiment ID: <EXPERIMENT_ID>

実行内容:
1. 手動作成済みリソースは作成・削除・置換しない。
2. databricks.ymlを使い、App本体、App専用サービスプリンシパル、7つのResource Binding（Warehouse、Volume、baseline Index、回答用LLM、Data Preparation Job、Evaluation Job、MLflow Experiment）を作成する。
3. App専用サービスプリンシパルへ、Catalog／SchemaのUSE、必要TableのSELECT・MODIFY、VolumeのREAD・WRITE、各Bindingの権限を付与する。
4. GitHubのmain/appをApp sourceへ設定し、bundle validate、deploy、runを順に実行する。
5. AppがRUNNING、computeがACTIVE、deploymentがSUCCEEDED、Bindingが7件、/api/healthがokになるまで確認・修正する。
6. 実ID、メール、Workspace ID、tokenをコードへ保存・commit・pushしない。基盤リソースもGitHub Actionsも作成しない。

不足値がある場合だけ質問し、完了時にApp URLと確認結果を返してください。
```

3. Genie Codeが返したApp URLを開きます。App作成、権限、Binding、Git接続、デプロイ、起動、ヘルスチェックについて、追加の手動設定は不要です。

確認：Genie Codeの結果に`RUNNING`、`ACTIVE`、`SUCCEEDED`、`7 bindings`、`health: ok`が表示される。

スクショ：Genie Codeの完了結果とAppのトップ画面。メール、token、実リソースIDは写さないでください。
