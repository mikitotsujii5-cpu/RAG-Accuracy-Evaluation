# 顧客向けハンズオン：Genie CodeとGitHubで作るRAG精度評価アプリ

最終確認日：2026年9月8日
対象：Databricksを初めて操作する方
参加者の想定時間：3～4時間（リソースの起動・同期待ちを除く）
講師・管理者の事前準備：別途60～90分を見込み、開始前日までに終えてください。

## 1. このハンズオンで作るもの

このハンズオンでは、PDFを検索して回答し、RAGの検索精度と回答品質を比較できる「RAG精度評価アプリ」を作ります。

完成後は、次の操作ができます。

- ProjectごとにPDFを登録する
- PDFをDocument Parsingして検索用データを作る
- Vector SearchとHybrid Searchを比較する
- Metadata Filtering、Reranking、Query Optimizationを切り替える
- Phase 1～5を同じ評価質問で比較する
- 回答の根拠となるPDFとページを確認する
- MLflowでTrace、品質、レイテンシを確認する

```text
非公開GitHubリポジトリ
  ├─ app/                  Databricks Appのコード
  ├─ jobs/                 Lakeflow JobsのNotebook source
  ├─ sql/                  Delta Table作成用SQL
  ├─ databricks.yml        AppとResource Bindingの定義
  └─ .github/workflows/    Appの検証・デプロイ
             │
             ├── Genie Codeでコードを確認・修正
             │
             └── GitHub ActionsでAppを作成・デプロイ
                              │
                              ▼
                     Databricks Apps
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   Unity Catalog          AI Search          Lakeflow Jobs
          │                   │                   │
          └───────────────────┴───────────────────┘
                              │
                              ▼
                     MLflow 3
```

### ハンズオンの進み方

迷ったときは、次の表で現在地と完了条件を確認してください。各Stepは、直前のStepの「完了確認」が終わってから進みます。

| Step | 主な作業 | 区分 | そのStepの完了条件 |
|---:|---|---|---|
| 1 | Genie Codeを安全に開く | 手動設定 | `Ask first`で提案だけを表示 |
| 2 | 非公開GitHubへ接続 | 手動設定 | 正しいrepository／branch／commitを表示 |
| 3 | SQL Warehouseを作成 | 手動作成 | `Running`、`SELECT 1`成功 |
| 4 | Catalog／Schema／Volumeを作成 | 手動作成 | 3つの名前と保存先を確認 |
| 5 | Delta Tablesを作成 | 手動作成 | 19 Table、migration検査値0、CDF有効 |
| 6 | Document Parsingを確認 | 手動確認 | `FILE`型で`ai_parse_document`成功 |
| 7 | Embedding／LLMを選ぶ | 手動設定 | Playgroundで正常応答 |
| 8 | AI Searchを作成 | 手動作成 | Endpoint `Online`、Index `Online／Idle` |
| 9 | MLflow Experimentを作成 | 手動作成 | path／IDを記録 |
| 10 | Lakeflow Jobsを作成 | 手動作成 | 3 Jobの設定とIDを確認 |
| 11 | GitHub用設定を確認 | Genie Code支援 | App以外を作らない差分になっている |
| 12 | GitHub Actions OIDCを設定 | 手動設定 | Federation policyとEnvironmentが一致 |
| 13 | Appのひな型を作成 | 自動作成 | Appと専用SPが生成済み |
| 14 | App用Git認証を設定 | 手動設定 | private repositoryへ接続済み |
| 15 | 最小権限を設定 | 手動設定 | Binding外の必要権限だけ付与 |
| 16 | Appをデプロイ | 自動設定・実行 | Binding、Deployment、Appが正常 |
| 17 | ヘルスチェック | 自動確認 | `Running`かつ独自healthがHTTP 200 |
| 18 | RAGとPhase評価を操作 | App操作 | PDF、Chat、評価、Traceを確認 |

## 2. Genie Codeの役割を先に理解する

Genie Codeは、Databricks上でコードを作成・修正・説明・テストするための支援機能です。

このハンズオンでは、Genie Codeを次の目的で使います。

- Git folder内の`app/`、`app.yaml`、`databricks.yml`を確認する
- 顧客環境のCatalog名やEndpoint名へ変更する
- Resource Bindingの設定漏れを確認する
- デプロイエラーの原因を説明してもらう

Genie CodeがDatabricks Appそのものを直接作成するわけではありません。役割分担は次のとおりです。

| 作業 | 担当 |
|---|---|
| アプリコードの作成・修正支援 | Genie Code |
| SQL WarehouseやAI Searchなどの基盤作成 | 利用者がDatabricks UIで実施 |
| App本体とApp専用サービスプリンシパルの作成 | GitHub ActionsとDatabricks Apps |
| 既存リソースのResource Binding | GitHub Actions |
| GitHubからのデプロイ・起動・ヘルスチェック | GitHub Actions |

> **重要**
> Genie Codeに本番リソースの作成や削除を任せません。本ハンズオンでは、Genie Codeの承認設定を`Ask first`にし、提案された変更を人が確認してから実行します。

## 3. 手動作成と自動作成の区分

| 区分 | Databricksリソース | このハンズオンでの扱い |
|---|---|---|
| 手動作成 | SQL Warehouse | UIで作成し、IDを記録する |
| 手動作成 | Unity Catalog／Schema／Volume／Delta Tables | Catalog UIとSQL Editorで作成する |
| 手動作成 | AI Search Endpoint／Index | UIで演習に使うEndpointとIndexを作成する |
| 手動作成 | Lakeflow Jobs（データ準備・評価・同期） | UIで3つ作成する |
| 手動作成 | MLflow Experiment | UIで作成し、IDを記録する |
| 手動設定 | Embedding／LLM endpoint | 対象Workspaceで利用可能なものを選ぶ |
| 手動設定 | Appサービスプリンシパルへの権限 | 最小権限を設定する |
| 手動設定 | 非公開GitHubリポジトリへのGit認証 | 開発者用とApp専用SP用を別々に設定する |
| 手動設定 | GitHub ActionsのOIDC認証 | デプロイ専用SPへFederation policyを設定する |
| 自動作成 | Databricks App本体 | GitHub Actionsが作成する |
| 自動作成 | App専用サービスプリンシパル | App作成時にDatabricksが自動生成する |
| 自動設定 | GitHub URL、branch、`app/`パス | Bundle定義から設定する |
| 自動設定 | 既存リソースのResource Binding | GitHub Actionsが既存IDを参照して設定する |
| 自動実行 | GitHubからのデプロイ・起動・ヘルスチェック | GitHub Actionsが実行する |

`ai_parse_document`は作成するリソースではなくSQL関数です。SQL Warehouse、Volume、保存先Tableを手動で準備し、PDF登録後の関数呼び出しはアプリのバックグラウンド処理が行います。

> **AI Searchの作成方針**
> このハンズオンでは、AI Search EndpointとIndexを必ずDatabricks UIで先に作成します。アプリとLakeflow Jobが行うのは、手動作成済みのsource Delta Tableへのデータ書き込み、既存Indexの同期、既存Indexの検索だけです。Endpoint／Indexの新規作成、削除、作り直しは行いません。
>
> Phase 1～5は、同じ`Standard／512／Qwen3 Embedding 0.6B`のbaseline Indexを使い、検索方式や精度向上機能だけを切り替えて比較します。チャンク方式やEmbedding modelも比較する場合は、講師・管理者が比較用Table／Indexを別名で事前作成してから、アプリへ登録します。同じIndexを上書きしません。

## 4. 最新UIの表記について

本書のメニュー名は、2026年9月8日時点のAzure Databricks公式ドキュメントで確認しています。段階的なUI更新により表記が異なる場合は、本書末尾の公式リンクと各Stepの「完了確認」を優先してください。

- AI Searchは以前のVector Searchから名称変更されています。
- 現在のUIでも、Index作成メニューは`Vector search index`と表示されます。
- 左メニューが折りたたまれている場合は、左上のメニューアイコンを開いてください。
- WorkspaceのCloud、Region、段階的なUI展開により、表示順や日本語訳が少し異なる場合があります。

迷った場合は、メニュー名ではなく、本文に記載した「作成後の確認結果」を基準にしてください。

## 5. 事前準備

### 5.1 使用する環境

このハンズオンは、検証用Workspaceで実施してください。本番データや機密PDFは使用しません。

必要な機能は次のとおりです。

- Unity Catalog
- Serverless SQL Warehouse
- AI Search
- Databricks Apps
- Lakeflow Jobs
- MLflow 3
- Foundation Model APIs
- Genie Code
- `FILE`型と`ai_parse_document`を利用できるRegion／Preview設定

### 5.2 用語を先に確認する

| 用語 | この手順書での意味 |
|---|---|
| SP | 人ではなく、AppやGitHub ActionsがDatabricksへ接続するときのID（service principal） |
| OIDC | GitHub Actionsがclient secretを保存せず、短時間だけDatabricksへ接続する仕組み |
| Bundle | Appと既存リソースの関連付けをYAMLで再現する仕組み。正式名称はDeclarative Automation Bundles |
| Resource Binding | Appへ既存のWarehouse、Volume、Index、Jobなどを安全に渡す設定 |
| Variant | チャンクサイズ、チャンク方式、Embedding modelと、対応する手動作成済みTable／Indexの組み合わせを記録した論理設定 |
| CDF | Delta Tableの変更分をAI Searchへ伝えるChange Data Feed |
| Run as | Lakeflow Jobをどの利用者またはSPの権限で実行するかを示す設定 |
| `valueFrom` | `app.yaml`で物理IDを直書きせず、Resource Bindingのkeyから値を受け取る指定 |

### 5.3 必要な担当者

| 担当 | 主な作業 |
|---|---|
| ハンズオン参加者 | UIで各リソースを作成し、アプリを確認する |
| Databricks Workspace管理者 | Preview、権限、サービスプリンシパルを設定する |
| Databricks Account管理者 | GitHub Actions用のFederation policyを設定する |
| GitHub Organization管理者 | 非公開リポジトリと必要なGitHub Appアクセスを許可する |

1人が複数の役割を兼ねても構いません。

### 5.4 設定値記入シート

作成した値は、次の表に必ず記録してください。`<...>`は実際の値へ置き換えます。

| 項目 | 推奨例 | 実際の値 |
|---|---|---|
| Workspace URL | `https://adb-<workspace-id>.<region>.azuredatabricks.net` | `<WORKSPACE_URL>` |
| GitHub repository | `https://github.com/<organization>/<repository>` | `<GITHUB_REPOSITORY_URL>` |
| Git branch | `main` | `<GIT_BRANCH>` |
| 配布テンプレートのcommit SHA | 講師が動作確認した40桁の値 | `<TEMPLATE_COMMIT_SHA>` |
| App source path | `app` | `<APP_SOURCE_PATH>` |
| SQL Warehouse名 | `rag-handson-warehouse` | `<SQL_WAREHOUSE_NAME>` |
| SQL Warehouse ID | 作成後に記録 | `<SQL_WAREHOUSE_ID>` |
| Catalog | `rag_accuracy_handson` | `<CATALOG>` |
| Schema | `rag_eval` | `<SCHEMA>` |
| Volume | `documents` | `<VOLUME>` |
| Volume完全名 | `<CATALOG>.<SCHEMA>.<VOLUME>` | `<VOLUME_FULL_NAME>` |
| AI Search Endpoint | `rag-handson-search` | `<AI_SEARCH_ENDPOINT>` |
| baseline source Table | `<CATALOG>.<SCHEMA>.toyota_chunks_standard_512_v1` | `<BASELINE_TABLE>` |
| baseline Index | `<CATALOG>.<SCHEMA>.toyota_chunks_standard_512_v1_index` | `<BASELINE_INDEX>` |
| AI Search利用方針 | 既存Indexの同期・検索のみ | `既存のみ` |
| Embedding endpoint | `databricks-qwen3-embedding-0-6b`を優先 | `<EMBEDDING_ENDPOINT>` |
| 回答用LLM endpoint | Tool Calling対応モデル | `<DEFAULT_LLM_ENDPOINT>` |
| 評価用LLM endpoint | 対象Workspaceで利用可能なモデル | `<JUDGE_LLM_ENDPOINT>` |
| Data Preparation Job ID | 作成後に記録 | `<PREP_JOB_ID>` |
| Evaluation Job ID | 作成後に記録 | `<EVAL_JOB_ID>` |
| Index Sync Job ID | 作成後に記録 | `<INDEX_SYNC_JOB_ID>` |
| MLflow Experiment path | `/Shared/rag-accuracy-handson`など | `<EXPERIMENT_PATH>` |
| MLflow Experiment ID | 作成後に記録 | `<EXPERIMENT_ID>` |
| App名 | `rag-accuracy-evaluation` | `<APP_NAME>` |
| App service principal client ID | App作成後に記録 | `<APP_SP_CLIENT_ID>` |
| App URL | App作成後に記録 | `<APP_URL>` |

AI Searchの選択条件と、手動作成済みリソースの対応も記録します。本編で必須なのは1行目だけです。比較用Indexを増やす場合は、空欄行をコピーして記入します。

| Variant key | チャンク方式 | サイズ | Embedding model | source Table | 既存Index | Binding key |
|---|---|---:|---|---|---|---|
| `baseline-standard-512-v1` | Standard | 512 | `<EMBEDDING_ENDPOINT>` | `<BASELINE_TABLE>` | `<BASELINE_INDEX>` | `baseline-index` |
| `<OPTIONAL_VARIANT_KEY>` | `<Standard／Semantic／Parent-child>` | `<256／512／1024>` | `<EMBEDDING_ENDPOINT>` | `<OPTIONAL_SOURCE_TABLE>` | `<OPTIONAL_INDEX>` | `<OPTIONAL_BINDING_KEY>` |

> **このリポジトリを別Workspaceで使う場合**
> 公開テンプレートの`app/app.yaml`、`jobs/job_common.py`、`deployment/jobs/*.json`、`deployment/app_*.json`、`sql/*.sql`はプレースホルダーまたはサンプル値を使用します。Genie Codeで差分を確認し、顧客環境の値へ置き換えてから実行してください。実IDを含む`deployment/resource_state.json`はローカル運用専用とし、Gitへ追加したり新しい環境へコピーしたりしません。

### 5.5 講師が事前に確認すること

本手順の検証元テンプレートは [mikitotsujii5-cpu/RAG-Accuracy-Evaluation](https://github.com/mikitotsujii5-cpu/RAG-Accuracy-Evaluation) の `main` branchです。顧客ハンズオンでは、この公開元を直接デプロイ用正本にせず、組織管理下の非公開Repositoryへforkまたはmirrorしてから使用します。

顧客が開始する前に、講師は顧客配布用の非公開GitHubリポジトリを用意します。参加者が空のRepositoryをその場で組み立てる手順ではありません。

- GitHubの`Settings > General`で`Visibility = Private`になっている
- `main` branchが存在し、参加者にRepository URLが配布されている
- 記入シートの`<TEMPLATE_COMMIT_SHA>`が、講師の動作確認済み「既存Index専用」版と一致している
- GitHub Organization管理者がDatabricks GitHub Appを対象Repositoryへ許可している
- 開発者のGit credentialには必要な場合だけContents read/write、App専用SPにはContents read相当の最小権限を付けている
- `main`へ`app/`、`jobs/`、`sql/`、`databricks.yml`、workflowが配置済みである

- `app/`に起動可能なアプリコード、`app.yaml`、依存関係がある
- `app/`は配布用allowlistで作られ、`tests/`、`.venv/`、`node_modules/`、cache、秘密情報、大容量データを含まない
- Python Appとして配布する場合、ローカルUIテスト専用の`package.json`、`package-lock.json`、`pyproject.toml`、`uv.lock`を`app/`へ含めない
- `jobs/`に4つのNotebook sourceがある
- `sql/01_foundation.sql`が顧客用Catalog／Schemaへ変更可能になっている
- `databricks.yml`がAppだけを宣言し、基盤リソースを作成しない
- Data Preparation Jobが手動作成済みの`<BASELINE_TABLE>`へ書き込み、`<BASELINE_INDEX>`を同期する構成になっている
- App／JobにAI Search Endpoint／Indexの作成・削除処理と、動的Indexへの権限付与処理がない
- `.github/workflows/deploy-databricks-app.yml`に、Repository固有の`bootstrap_only`入力と通常デプロイがある
- 通常デプロイがBundle validate、deploy、run、Running確認を行う
- Workspace固有ID、個人メール、PAT、client secretがコミットされていない

`bootstrap_only`はDatabricksの標準オプションではなく、この顧客配布用Repositoryに実装する安全な初回作成ルールです。`true`のときはBundleのvalidateとdeployまで実行し、`bundle run`だけをスキップします。

講師はworkflowに少なくとも次の契約があることをレビューします。

```yaml
on:
  workflow_dispatch:
    inputs:
      bootstrap_only:
        type: boolean
        default: false

# validateとdeployは常に実行
# private Git credential設定前の初回だけ、起動をスキップ
- name: Start or restart app
  if: inputs.bootstrap_only != true
  run: databricks bundle run <APP_RESOURCE_KEY> --target prod
```

いずれかがない場合は、Step 1へ進まず講師へ連絡してください。特に、Data Preparation Job内に`create_index`、`create_delta_sync_index`、`delete_index`、または動的Indexへの`GRANT`が残っている版は、このハンズオンでは使いません。Genie Codeに不足ファイルを無条件で作らせず、講師がテストした「既存Index専用」テンプレートを使います。このWorkspace内にある開発用ファイル一式を、そのままGitHubへpushしないでください。

### 5.6 講師から受け取るPDF

Phase 1～5の比較には、顧客配布用Repositoryの`output/pdf/`にある次の8冊を使います。

| 文書 | ファイル |
|---|---|
| D01 | `01_prius_2024_owners_guide_demo.pdf` |
| D02 | `02_prius_2023_owners_guide_demo.pdf` |
| D03 | `03_prius_2024_grade_equipment_demo.pdf` |
| D04 | `04_prius_2024_safety_operation_demo.pdf` |
| D05 | `05_prius_2024_emergency_response_demo.pdf` |
| D06 | `06_prius_2023_2024_change_report_demo.pdf` |
| D07 | `07_crown_sport_2024_owners_guide_demo.pdf` |
| D08 | `08_toyota_demo_safety_glossary.pdf` |

これらはRAG評価用に作成した架空・非公式のPDFです。トヨタ自動車株式会社とは関係がなく、実車の操作、整備、救助、購入判断には使用できません。参加者は講師からPDF一式をローカルPCへ受け取り、GitHubへ再コミットしません。

## 6. Step 1：Genie Codeを安全に開く

### 目的

この後の作業をGenie Codeに説明してもらいながら進められる状態にします。

### UI操作

1. Databricks WorkspaceのHomeを開きます。
2. 画面中央の入力欄で`Code`を選びます。
3. 次の文章を入力して送信します。

```text
RAG精度評価アプリのハンズオンを開始します。
SQL Warehouse、Unity Catalog、AI Search、Lakeflow Jobs、MLflowは人がUIで作成します。
新しい基盤リソースを勝手に作成・変更・削除しないでください。
今は操作を実行せず、作業順と確認項目だけを説明してください。
```

4. Genie Code右上のメニューから`Settings`を開きます。
5. ツール承認を`Ask first`にします。

既存画面から開く場合は、画面右上のGenie Codeアイコンをクリックします。

### 完了確認

- Full page Genie Codeまたは右側のチャットpaneが開いている
- Genie Codeがリソースを作成せず、手順だけを回答している
- 承認設定が`Ask first`になっている

> **スクショポイント SS-01：Genie Codeの開始画面**
> 撮影箇所：Genie Codeのチャット、入力した指示、`Ask first`設定。
> 赤枠：作業対象と「勝手に作成・削除しない」の文。
> 合格表示：手順の提案だけが返っている。
> 写さないもの：別Project名、機密データ、個人メール、アクセストークン。

## 7. Step 2：非公開GitHubリポジトリへ接続する

### 目的

GitHubをアプリコードの正本にします。

### 3種類の認証を混同しない

| 認証 | 用途 | 設定場所 |
|---|---|---|
| 開発者のGit credential | Git folderのClone、Commit、Push | Databricks利用者設定 |
| App専用SPのGit credential | Appが非公開リポジトリを読み取る | App作成後のOverview |
| デプロイSPのOIDC | GitHub ActionsがDatabricksを操作する | Databricks AccountとGitHub Environment |

1つのPATやclient secretを3用途で使い回しません。GitHub ActionsはOIDCを使うため、client secretは登録しません。

この手順は「GitHub Actions＋BundleがAppを作成・更新し、App専用SPが非公開Gitからコードを取得する」組み合わせ方式です。Databricks Appsの`Auto deploy on push events`（Beta）は有効にしません。両方を有効にすると、同じpushでデプロイが二重に走るためです。

### 7.1 開発者用Git credentialを設定する

1. Databricks画面右上のユーザーアイコンをクリックします。
2. `Settings`を開きます。
3. `Linked accounts`を開きます。
4. `Add Git credential`をクリックします。
5. Git providerで`GitHub`を選びます。
6. 組織の手順に従い、GitHubアカウントをLinkします。

GitHub OrganizationでDatabricks GitHub Appの利用が制限されている場合は、Organization管理者に対象RepositoryへのInstall／許可を依頼します。PATを代替利用する場合も、参加者には必要なRepositoryだけの権限を与えます。

> **スクショポイント SS-02：開発者用Git認証**
> 撮影箇所：`Linked accounts`でGitHubが接続済みになった状態。
> 赤枠：Providerと接続状態。
> 写さないもの：PAT、OAuth code、client secret。

### 7.2 Git folderを作成する

1. 左メニューの`Workspace`を開きます。
2. 保存先Folderを選び、`Create > Git folder`をクリックします。
3. 次を入力します。

| 項目 | 入力値 |
|---|---|
| Git repository URL | `<GITHUB_REPOSITORY_URL>` |
| Git provider | `GitHub` |
| Git folder name | `rag-accuracy-evaluation` |
| Sparse checkout mode | 通常はOFF |

4. 作成後、branchが`main`であることを確認します。
5. `app/`、`jobs/`、`sql/`が表示されることを確認します。
6. Git dialogまたはcommit historyで先頭commitを開き、SHAが`<TEMPLATE_COMMIT_SHA>`と一致することを確認します。

`main`や`app/`が見つからない場合、またはcommit SHAが一致しない場合は、別branchへ変更したり新規ファイルを作ったりせず、講師から配布されたURL、branch、commitを確認します。

> **スクショポイント SS-03：Git folder**
> 撮影箇所：Git repository URL、provider、folder名、作成後のbranchと先頭commit SHA。
> 赤枠：repository、`main`、`<TEMPLATE_COMMIT_SHA>`。
> 合格表示：`app/`、`jobs/`、`sql/`が見える。
> 写さないもの：Git credentialの値。

## 8. Step 3：SQL Warehouseを作成する

### 目的

Delta Tableの作成、`FILE`型でのPDF解析、アプリからのSQL実行に使うWarehouseを準備します。

### UI操作

1. 左メニューの`SQL Warehouses`をクリックします。
2. `Create SQL warehouse`をクリックします。
3. `Name`へ`<SQL_WAREHOUSE_NAME>`を入力します。
4. `Type`で`Serverless`を選びます。表示されない場合は管理者へ利用可否を確認します。
5. PDF解析時間を短縮するハンズオンでは、`Cluster Size`を`X-Large`にします。
6. `Auto Stop`を設定します。ハンズオンでは10～15分を推奨します。
7. `Create`をクリックします。

### 完了確認

1. Statusが`Running`になるまで待ちます。
2. Warehouse detailの`Connection details`を開きます。`HTTP path`末尾の`/warehouses/<ID>`、または画面URLのWarehouse IDを記入シートへ保存します。
3. SQL EditorでWarehouseを選び、次を実行します。

```sql
SELECT 1 AS health_check;
```

結果が`1`なら合格です。

> **スクショポイント SS-04：SQL Warehouse**
> 撮影箇所：Warehouse一覧のName、Type、Size、Status。
> 赤枠：Serverless、X-Large、Running。
> 合格表示：StatusがRunning。
> 写さないもの：他部署のWarehouse、利用者メール。

## 9. Step 4：Unity Catalog、Schema、Volumeを作成する

### 9.1 Catalogを作成する

1. 左メニューの`Catalog`を開きます。
2. Quick accessの`Catalogs`を開きます。
3. `Create catalog`をクリックします。
4. `Catalog name`へ`<CATALOG>`を入力します。
5. `Type`は`Standard catalog`を選びます。
6. Metastore-level managed storageが設定済みなら、Storage locationは既定値を使えます。未設定の場合は管理者が許可したmanaged storage locationを選びます。
7. `Create`をクリックし、Enterprise向けの権限を確認するため`Configure catalog`を開きます。`View catalog`は全Workspace／全利用者向けの初期設定を受け入れる場合だけ使います。

### 完了確認

- 左のCatalog treeに`<CATALOG>`が表示される
- OverviewでType、Owner、Commentを確認できる

> **スクショポイント SS-05：Catalog**
> 撮影箇所：Catalog treeとOverview。
> 赤枠：Catalog名、Type、Owner。
> 合格表示：`default`と`information_schema`が表示される。

### 9.2 Schemaを作成する

1. Catalog treeで`<CATALOG>`を選びます。
2. `Create schema`をクリックします。
3. Schema名へ`<SCHEMA>`を入力します。
4. Commentへ`RAG accuracy evaluation hands-on`と入力します。
5. `Create`をクリックします。

### 完了確認

breadcrumbが`<CATALOG>.<SCHEMA>`になっていることを確認します。

> **スクショポイント SS-06：Schema**
> 撮影箇所：Catalog／Schemaのbreadcrumb、Schema名、Comment。
> 合格表示：`<CATALOG>.<SCHEMA>`が見える。

### 9.3 Managed Volumeを作成する

1. `<CATALOG>.<SCHEMA>`を開きます。
2. `Create > Volume`をクリックします。
3. Volume名へ`<VOLUME>`を入力します。
4. Typeは`Managed`を選びます。
5. `Create`をクリックします。

Volumeのパスは次の形式です。

```text
/Volumes/<CATALOG>/<SCHEMA>/<VOLUME>/
```

> **スクショポイント SS-07：Volume**
> 撮影箇所：Volume名、Type、Catalog／Schema breadcrumb、Files領域。
> 赤枠：ManagedとVolume完全名。
> 合格表示：Filesタブを開ける。

## 10. Step 5：Delta TablesをSQL Editorで作成する

### 目的

Project、PDF、チャット、評価、検索Variantを保存するTableを作ります。

### UI操作

1. 左メニューの`SQL Editor`を開きます。
2. Step 3で作成したWarehouseを選びます。
3. Git folderの`sql/01_foundation.sql`を開きます。
4. 検証環境のCatalog／Schema名を、記入シートの値へ置き換えます。
5. `DROP`、`TRUNCATE`、`REPLACE TABLE`が追加されていないことを確認します。
6. SQLをSQL Editorへ貼り付け、`Run`をクリックします。

このリポジトリでは、互換性のためTable名に`toyota_`が残っています。アプリコードを変更せず使う場合は、Table名を変更しないでください。

主なTableは次のとおりです。

| 用途 | Table |
|---|---|
| モデル | `toyota_rag_model_catalog`、`toyota_rag_model_defaults` |
| Project／PDF | `toyota_rag_projects`、`toyota_rag_project_members`、`toyota_document_registry` |
| 共通設定 | `toyota_vehicle_master`、`toyota_rag_user_preferences`、`toyota_rag_notifications` |
| Variant | `toyota_index_variants` |
| Chat | `toyota_rag_chat_sessions`、`toyota_rag_chat_messages`、`toyota_rag_chat_runs` |
| データ準備 | `toyota_rag_prep_runs`、`toyota_parsed_v2` |
| 評価 | `toyota_rag_eval_cases`、`toyota_rag_eval_runs`、`toyota_rag_eval_results`、`toyota_rag_eval_suggestions` |
| baseline検索 | `toyota_chunks_standard_512_v1` |

`toyota_index_variants`の1行は、チャンク条件と手動作成済みsource Table／Indexを結ぶ論理レコードです。このTableへ行を追加しても、物理的なDelta TableやAI Search Indexは作成されません。

合計19 Tableです。続けて、既存環境と新規環境の差をなくすため、次のmigrationを必ずこの順番で実行します。どちらも再実行できるように作られたSQLです。

1. `sql/07_migrate_generic_document_metadata.sql`
2. `sql/09_document_logical_deletion.sql`

最初のmigrationの最後に表示される`missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`がすべて`0`、次のmigrationの`documents_without_lifecycle`と`variants_without_lifecycle`がともに`0`であることを確認します。1つでも0以外なら、次のStepへ進みません。

Table数も確認します。

```sql
SHOW TABLES IN <CATALOG>.<SCHEMA>;
```

このハンズオン専用Schemaで、上の19 Tableがすべて表示されれば合格です。

### CDFを確認する

AI SearchのStandard endpointでは、source Delta TableのChange Data Feedが必要です。

```sql
SHOW TBLPROPERTIES <CATALOG>.<SCHEMA>.toyota_chunks_standard_512_v1;
```

`delta.enableChangeDataFeed`が`true`なら合格です。

> **スクショポイント SS-08A：DDLの実行結果**
> 撮影箇所：SQL EditorのWarehouse、Catalog／Schema、成功結果。
> 合格表示：すべてのstatementが成功。
> 写さないもの：別Catalogのデータ。

> **スクショポイント SS-08B：TableとCDF**
> 撮影箇所：Catalog ExplorerのTable一覧とbaseline TableのProperties。
> 赤枠：`delta.enableChangeDataFeed=true`。
> 合格表示：19 Tableが表示され、migrationの6つの検査値がすべて0。

## 11. Step 6：`FILE`型でDocument Parsingを確認する

### 目的

`ai_parse_document`へPDFのバイナリではなく、`FILE`型を渡せることを確認します。

### PDFをVolumeへ置く

1. Catalog Explorerで`<VOLUME>`を開きます。
2. Files領域でUploadを選びます。
3. ハンズオン用のPDFを1冊アップロードします。
4. 表示されたVolume pathを控えます。

機密情報や個人情報を含むPDFは使わないでください。

ここでアップロードするPDFは`ai_parse_document`の疎通確認用です。Step 18でAppからProjectへ登録するPDFとは別で、Document Registryや評価データへは自動登録されません。

### SQLを実行する

SQL Editorで次を実行します。`<PDF_PATH>`を実際のVolume pathへ置き換えます。

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

`READ_FILES(..., format => 'file')`が返す`file`列は`FILE`型です。`CAST`や`BINARY`への変換を入れず、そのまま`ai_parse_document`へ渡します。

### 完了確認

- SQLが成功する
- `parsed`に文書要素が返る
- エラーに`FILE type not enabled`や`function not supported`が出ない

> **スクショポイント SS-09：Document Parsing**
> 撮影箇所：`format => 'file'`、`ai_parse_document`、成功した`parsed`結果。
> 赤枠：`file AS source_file`と`format => 'file'`。
> 合格表示：解析結果が返る。
> 写さないもの：PDF本文の機密情報。

## 12. Step 7：EmbeddingとLLM endpointを選ぶ

### 目的

このWorkspaceで実際に利用できるモデルだけを選びます。

FMAPIのPay-per-token endpointはDatabricksから自動提供されるため、このハンズオンで新規作成しません。Workspaceに実際に表示されたendpoint名を記録します。

### UI操作

1. 左メニューの`Serving`を開きます。
2. Foundation Model APIsのEndpoint一覧を確認します。
3. Embeddingでは`databricks-qwen3-embedding-0-6b`が利用可能か確認します。このモデルは100以上の言語に対応するPublic Previewです。利用できない場合は、一覧にある日本語対応Embeddingを選びます。
4. 回答用LLMでは、Function calling（Tool Calling）対応モデルを選びます。
5. 評価用LLMも、利用可能なEndpointから選びます。
6. Endpoint名を記入シートへ正確にコピーします。

LLMの動作は次の手順で確認します。

1. 左メニューの`AI/ML`配下から`Playground`を開きます。
2. Model dropdownで回答用LLMを選びます。
3. 機密情報を含まない短い質問を送ります。
4. 正常に回答が返ることを確認します。

Function calling対応でもPlaygroundに表示されないモデルがあります。その場合は「利用不可」と判断せず、講師がResponses APIまたはServing endpoint queryで疎通確認したモデルだけをmodel catalogへ登録します。

「FMAPIで指定できる全モデル」を固定リストとしてコードへ書きません。Region、契約、利用規約、Workspace設定により利用可能なモデルが変わるためです。

### 選んだモデルをアプリのmodel catalogへ登録する

アプリのモデル選択欄は、`toyota_rag_model_catalog`のうち、現在利用可能と確認した行だけを表示します。SQL Editorで次を実行します。3つの`<..._ENDPOINT>`は、Serving画面で確認した実在名へ置き換えます。

```sql
MERGE INTO <CATALOG>.<SCHEMA>.toyota_rag_model_catalog AS target
USING (
  SELECT * FROM VALUES
    ('emb-default', '既定のEmbedding', 'FMAPI_ENDPOINT',
     '<EMBEDDING_ENDPOINT>', array('embedding')),
    ('llm-answer-default', '既定の回答LLM', 'FMAPI_ENDPOINT',
     '<DEFAULT_LLM_ENDPOINT>',
     array('chat', 'streaming', 'tool_calling', 'chat_tool_calling')),
    ('llm-judge-default', '既定の評価LLM', 'FMAPI_ENDPOINT',
     '<JUDGE_LLM_ENDPOINT>', array('chat', 'judge', 'advisor'))
  AS source(model_key, display_name, target_kind, target_name, capabilities)
) AS source
ON target.model_key = source.model_key
WHEN MATCHED THEN UPDATE SET
  target.display_name = source.display_name,
  target.target_kind = source.target_kind,
  target.target_name = source.target_name,
  target.capabilities = source.capabilities,
  target.endpoint_state = 'READY',
  target.region_available = true,
  target.selectable = true,
  target.unavailable_reason = NULL,
  target.verified_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  model_key, display_name, target_kind, target_name, capabilities,
  endpoint_state, embedding_dimension, max_context_tokens, max_output_tokens,
  region_available, selectable, unavailable_reason, verified_at
) VALUES (
  source.model_key, source.display_name, source.target_kind,
  source.target_name, source.capabilities, 'READY', NULL, NULL, NULL,
  true, true, NULL, current_timestamp()
);

MERGE INTO <CATALOG>.<SCHEMA>.toyota_rag_model_defaults AS target
USING (
  SELECT 'embedding' AS capability,
         'emb-default' AS preferred_model_key,
         'READY_SELECTABLE_DISPLAY_NAME_MODEL_KEY_ASC' AS fallback_policy,
         'Serving画面で利用可能と確認したEmbedding' AS rationale
) AS source
ON target.capability = source.capability
WHEN MATCHED THEN UPDATE SET
  target.preferred_model_key = source.preferred_model_key,
  target.fallback_policy = source.fallback_policy,
  target.rationale = source.rationale,
  target.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  capability, preferred_model_key, fallback_policy, rationale, updated_at
) VALUES (
  source.capability, source.preferred_model_key, source.fallback_policy,
  source.rationale, current_timestamp()
);
```

次の確認で、今回追加した3つのmodel keyが3行すべて返れば合格です。

```sql
SELECT model_key, display_name, target_name, capabilities
FROM <CATALOG>.<SCHEMA>.toyota_rag_model_catalog
WHERE model_key IN ('emb-default', 'llm-answer-default', 'llm-judge-default')
  AND selectable = true
  AND region_available = true
ORDER BY model_key;
```

> **スクショポイント SS-10A：Serving endpoints**
> 撮影箇所：選択したEmbedding／LLM endpoint名と利用可能状態。
> 赤枠：実際に選んだEndpoint。
> 写さないもの：他ProjectのEndpoint、利用者情報。

> **スクショポイント SS-10B：Playground**
> 撮影箇所：Model dropdownと正常な応答。
> 合格表示：エラーなく応答が返る。
> 写さないもの：機密プロンプト。

## 13. Step 8：AI Search Endpointとbaseline Indexを手動作成する

このStepで、ハンズオン中に使用する物理的なAI Searchリソースをすべて作ります。後続のApp／Jobは、このIndexを同期・検索するだけです。

### 13.1 AI Search Endpoint

1. 左メニューの`Compute`を開きます。
2. `AI Search`タブを開きます。
3. `Create endpoint`をクリックします。
4. Endpoint名へ`<AI_SEARCH_ENDPOINT>`を入力します。
5. `Type`は`Standard`を選びます。
6. `Confirm`をクリックします。
7. Statusが`Online`になるまで待ちます。

> **スクショポイント SS-11：AI Search Endpoint**
> 撮影箇所：AI SearchタブのEndpoint名、Type、Status。
> 赤枠：StandardとOnline。
> 合格表示：StatusがOnline。

### 13.2 baseline Index

1. 左メニューの`Catalog`を開きます。
2. `<BASELINE_TABLE>`を開きます。
3. 右上の`Create`をクリックします。
4. `Vector search index`を選びます。
5. `Create AI Search index`画面で次の値を設定します。

| UI項目 | 値 |
|---|---|
| NameのCatalog／Schema | `<CATALOG>`／`<SCHEMA>` |
| Nameのindex名 | `toyota_chunks_standard_512_v1_index` |
| Index type | `Hybrid` |
| Primary key | `chunk_id` |
| Embedding source | `Compute embeddings` |
| Embedding source column | `chunk_to_embed` |
| Embedding model | `<EMBEDDING_ENDPOINT>` |
| AI Search endpoint | `<AI_SEARCH_ENDPOINT>` |
| Sync mode | `Triggered` |
| Advanced settings > Columns to index | 下記17列 |

`Name`は画面上でCatalog、Schema、末尾名の入力欄に分かれています。末尾のName欄へ`<CATALOG>.<SCHEMA>...`という完全名を貼り付けません。作成後の完全名が`<BASELINE_INDEX>`と一致することを確認します。

`Advanced settings`は初期状態で閉じています。開いて、次の17列をすべて選びます。Primary keyの`chunk_id`とEmbedding列の`chunk_to_embed`は自動的に含まれます。

```text
project_id
document_id
chunk_to_retrieve
parent_chunk_id
parent_chunk_to_retrieve
doc_uri
page_number
page_numbers
parent_page_numbers
title
model
model_year
document_type
vehicle_category
section_title
keywords
variant_id
```

6. `Create`をクリックします。
7. Index detailの`Overview`で`Index status = Online`、`Data Ingest`の`Update status = Idle`になるまで待ちます。
8. 初回作成後にsource Tableを更新した場合だけ、`Data Ingest > Sync now`をクリックします。

source Tableが空の場合、検索可能行数が0でも初期構築としては異常ではありません。PDF登録と検索データ作成後に再確認します。

> **スクショポイント SS-12A：Index作成設定**
> 撮影箇所：Nameの3入力、Index type、Primary key、Embedding model、Endpoint、Sync mode、展開したAdvanced settings。
> 赤枠：Hybrid、`chunk_id`、Triggered、17列。
> Createを押す直前に撮影する。

> **スクショポイント SS-12B：Index同期結果**
> 撮影箇所：OverviewのData Ingest、Latest sync、source Table。
> 合格表示：Index statusがOnline、Update statusがIdle、最新Syncが成功。

### 13.3 比較用Indexを増やす場合（任意）

本編では`Standard／512／Qwen3 Embedding 0.6B`だけを使います。チャンクサイズ、チャンク方式、Embedding modelも比較する場合は、講師・管理者が次の順で追加します。

1. 比較条件ごとに別のsource Delta Tableを手動作成します。
2. Step 13.2と同じUIで、そのTableに対応する別名のIndexを手動作成します。
3. Indexが`Online`、Data Ingestが`Idle`になることを確認します。
4. GitHub側の許可済みIndex一覧とResource Bindingへ追加します。
5. Appを再デプロイし、App専用SPにそのIndexの`SELECT`が付いたことを確認します。

たとえば、`Semantic／256`と`Parent-child／1024`は同じIndexを共用しません。選択したEmbedding modelもIndexの作成設定と一致させます。対応する既存Indexが未登録の条件は、アプリで実行不可として表示します。App／Jobが不足Indexを自動作成する設計にはしません。Project Tableに過去の許可外Variantが残っていても、その行は画面の選択肢から除外されます。許可済みVariantまで表示されない場合は、物理Indexを作り直さず、source Table／Indexの完全修飾名が管理者の許可リストと一致するか確認します。追加Indexを1件Bindingすると、後述のResource Binding件数は`7件 + 追加Index件数`になります。本編では追加せず7件のまま進めます。

## 14. Step 9：MLflow Experimentを作成する

### 目的

Chat Trace、評価結果、レイテンシを同じExperimentに保存します。

### UI操作

最も迷いにくい手順は次のとおりです。

1. 左メニューの`Workspace`を開きます。
2. Experimentを保存するFolderを選びます。
3. Folderを右クリックし、`Create > MLflow experiment`を選びます。
4. Nameへ`rag-accuracy-handson`を入力します。
5. Artifact locationは、ハンズオンでは未入力でも構いません。
6. `Create`をクリックします。

左メニューの`AI/ML`配下から`Experiments`を開き、ページ上部の`Custom`を選んで作成することもできます。

### 完了確認

1. Experiment detailを開きます。
2. 名前の右側にあるInformationアイコンをクリックします。
3. Experiment pathとExperiment IDを記入シートへ保存します。

このハンズオンはWorkspace Experimentを使うため、App Resource Bindingの`Can edit`でTraceを書き込めます。Unity CatalogをMLflow Traceの保存先に選ぶ場合は別設定です。その場合は、生成される`*_otel_annotations`、`*_otel_logs`、`*_otel_metrics`、`*_otel_spans`の4 TableへApp／Jobの書き込みidentityに`MODIFY`を追加してください。

> **スクショポイント SS-13：MLflow Experiment**
> 撮影箇所：Experiment名とInformation popover。
> 赤枠：Experiment path、ID、Artifact location。
> 写さないもの：個人メールが含まれるpathはマスクする。

## 15. Step 10：Lakeflow Jobsを3つ作成する

### 目的

データ準備、Phase評価、baseline Index同期を、Appの画面を閉じても続くバックグラウンド処理として作ります。Appの初回作成でJob IDを参照するため、Jobsを先に作ります。このStepでは設定だけを保存し、Data PreparationとEvaluationはまだ実行しません。3つのJobはいずれも、手動作成済みのTable／Indexだけを使用します。

Git folderの次の4ファイルを同じ相対位置のまま使います。

```text
jobs/
├─ job_common.py
├─ data_preparation_job.py
├─ evaluation_job.py
└─ index_sync_job.py
```

3つの実行Notebookは`%run ./job_common`を使います。4ファイルを別々のFolderへ移動しないでください。

### 10.1 Jobを作る共通操作

1. 左メニューの`Jobs & Pipelines`を開きます。
2. `Create > Job`をクリックします。
3. `Notebook` tileをクリックします。見つからない場合は`Add another task type`からNotebookを選びます。
4. `Task name`を入力し、`Path`でGit folder内のNotebookを選びます。
5. Compute、parameter、Environment、retryを下表どおり設定します。
6. `Save task`をクリックします。
7. 画面上部の`New Job <日付> <時刻>`をクリックし、表のJob名へ変更します。
8. Job detailsへ戻り、Queue、同時実行数、Job parameter、`Run as`を設定します。

設定場所が分かれています。`Performance optimized`とJob parametersはJob details、Environment versionとdependencyはTaskのEnvironment side pane、同時実行数はJob detailsの`Advanced settings > Edit concurrent runs`です。

### 10.2 Data Preparation Job

| 設定場所 | 項目 | 値 |
|---|---|---|
| Job | Job名 | `rag-data-preparation` |
| Job | Queue | Enabled |
| Job | Max concurrent runs | `2` |
| Job | Performance target | `Performance optimized` |
| Job | Job parameter | `prep_run_id`、defaultは空文字 |
| Task | Task name | `prepare_existing_index_data` |
| Task | Path | `jobs/data_preparation_job.py` |
| Task | Base parameter | `prep_run_id={{job.parameters.prep_run_id}}` |
| Task | Compute | Serverless |
| Task Environment | Version | `5` |
| Task Environment | Dependency | `databricks-sdk==0.135.0` |
| Task | Timeout | `2 hours` |
| Task | Retry | 1回、60秒後、timeout時もretry |

2026年9月時点ではEnvironment version 6もありますが、このアプリはversion 5で検証済みのため固定します。新しいversionへ上げる場合は、別の検証Jobで全チャンク方式と既存Indexの同期を確認してから変更します。Serverless Notebook taskではdependencyをtask-level Librariesへ置かず、Environment side paneへ登録します。

### 10.3 Evaluation Job

| 設定場所 | 項目 | 値 |
|---|---|---|
| Job | Job名 | `rag-phase-evaluation` |
| Job | Queue | Enabled |
| Job | Max concurrent runs | `1` |
| Job | Job parameter | `eval_run_id`、defaultは空文字 |
| Task | Task name | `evaluate_phases` |
| Task | Path | `jobs/evaluation_job.py` |
| Task | Base parameter | `eval_run_id={{job.parameters.eval_run_id}}` |
| Task Compute | Runtime | Databricks Runtime `18.x-scala2.13` |
| Task Compute | Access mode | `User isolation` |
| Task Compute | Driver／Worker | `Standard_D4s_v5`／`Standard_D4s_v5`×1 |
| Task Compute | Spark environment variable | `MLFLOW_TRACING_SQL_WAREHOUSE_ID=<SQL_WAREHOUSE_ID>` |
| Task Libraries | PyPI | `databricks-sdk==0.135.0`、`databricks-ai-search==0.78` |
| Task | Timeout | `4 hours` |
| Task | Retry | 1回、60秒後、timeout時もretry |

`Standard_D4s_v5`が組織ポリシーにない場合は、講師が許可した同等以上のDriver／Workerを選び、変更内容を記録します。

### 10.4 Index Sync Job

| 設定場所 | 項目 | 値 |
|---|---|---|
| Job | Job名 | `rag-index-sync` |
| Job | Queue | Enabled |
| Job | Max concurrent runs | `1` |
| Task | Task name | `sync_baseline_index` |
| Task | Path | `jobs/index_sync_job.py` |
| Task Compute | Runtime／Access mode | `18.x-scala2.13`／`User isolation` |
| Task Compute | Driver／Worker | `Standard_D4s_v5`／`Standard_D4s_v5`×1 |
| Task Libraries | PyPI | `databricks-sdk==0.135.0` |
| Task | Timeout | `1 hour` |
| Task | Retry | 1回、60秒後、timeout時もretry |

### 10.5 Run as identityへ権限を付ける

各Jobのdetailsで`Run as`を確認します。AppへのResource BindingはJobの`Run as`へ権限を付けません。Workspace管理者は、表示されたidentityへ次を付与します。

3つのJobすべてで、`Run as` identityがGit folder内の4 Notebook sourceを読み取れることも確認します。個人Folder配下で共有できない場合は、講師管理の共有Workspace Folderへ4ファイルだけを配置し、4ファイルの相対位置を保ちます。

| Job | 最小権限の考え方 |
|---|---|
| Data Preparation | Catalog／Schemaの利用、手動作成済みTableのSELECT／MODIFY、必要なVolumeのREAD、既存Indexを同期できる権限、Embedding endpointのCAN QUERY |
| Evaluation | 評価元Table／IndexのSELECT、評価run・result・suggestion TableのMODIFY、回答／採点LLMのCAN QUERY、MLflow ExperimentのCAN EDIT、Tracing用WarehouseのCAN USE |
| Index Sync | baseline Table／IndexのSELECTと、手動作成済みIndexを同期できる所有権または管理権限 |

Schema全体への`CREATE TABLE`や不要な`ALL PRIVILEGES`は付けません。Data Preparation JobはIndexを作成せず、既存Indexへの権限も変更しません。App専用SPの`SELECT`はResource Bindingで付与します。

### 完了確認

- 3つのJobが保存済みである
- Data PreparationとEvaluationのJob parameterとTask base parameterが一致する
- Data PreparationがPerformance optimized Serverless、Environment version 5である
- EvaluationとIndex SyncのRuntime、Library、Timeoutが表どおりである
- QueueがEnabledである
- 3つのJob IDを記入シートへ保存した
- Data Preparation JobにEndpoint／Indexの作成・削除・動的GRANT処理がない

Data PreparationとEvaluationを空の`prep_run_id`／`eval_run_id`で単独実行しません。最初の実行はAppから行います。Index Syncはbaseline Tableへ行がある場合だけ`Run now`で確認できます。

> **スクショポイント SS-14A：Data Preparation Job**
> 撮影箇所：Job graph、Task、Path、Performance optimized、Environment version、parameter。
> 合格表示：Serverless、version 5、dependency、Queue、Max concurrent runs 2。

> **スクショポイント SS-14B：Evaluation Job**
> 撮影箇所：Task、Path、Compute、Libraries、`eval_run_id`、Timeout。
> 合格表示：Taskが保存済みで、Run asが確認できる。

> **スクショポイント SS-14C：Index Sync Job**
> 撮影箇所：Task、Path、Compute、Library、Timeout。
> 合格表示：Taskが保存済み。

## 16. Step 11：Genie CodeでGitHub用設定を確認する

### 目的

GitHub Actionsが「Appだけ」を作成し、既存の基盤を変更しないことを確認します。

### GitHubリポジトリの最低構成

```text
<repository>/
├─ app/
│  ├─ app.yaml
│  ├─ main.py
│  ├─ requirements.txt
│  └─ static/
├─ jobs/
├─ sql/
├─ databricks.yml
└─ .github/
   └─ workflows/
      └─ deploy-databricks-app.yml
```

`app/`の外にあるファイルは、AppのGit sourceから参照できません。App実行に必要なPython、HTML、CSS、依存関係だけを`app/`内へ置きます。このPython Appでは`package.json`を含めません。Databricks Appsは`package.json`があるとNode.jsのinstall／buildも実行するため、ローカルテスト用ファイルが混ざると起動に失敗する可能性があります。

### Genie Codeへ入力するプロンプト

Git folderを開き、Genie Codeへ次を入力します。

```text
このGit folderのRAG精度評価アプリを顧客Workspace向けに確認してください。

要件:
- App sourceは非公開GitHub repositoryのmain branch、source pathはapp
- SQL Warehouse、Unity Catalog、AI Search、Lakeflow Jobs、MLflow Experiment、Model endpointは既存リソースを参照する
- GitHub Actionsやdatabricks.ymlから基盤リソースを新規作成、削除、再作成しない
- AppとJobは手動作成済みのsource Delta Tableへの書き込み、既存AI Search Indexの同期・検索だけを行う
- AI Search Endpoint／Indexの作成・削除、動的IndexへのGRANTを行うコードを含めない
- databricks.ymlのトップレベルresourcesにはAppだけを宣言する
- app.yamlではResource Binding keyをvalueFromで参照する
- 秘密情報、PAT、client secretをsourceへ直書きしない
- 記入シートの非秘密リソース名・IDだけを、決められたBundle変数または設定欄へ反映する
- 初回はAppと専用service principalだけを作成し、bundle runは実行しない
- 変更前に計画を表示する
- commitとpushは実行しない

まず現在の差分と不足項目だけを説明してください。
```

Genie Codeが変更を提案した場合は、1ファイルずつ差分を確認します。レビュー内容に問題がなければ、次の2回目のプロンプトで実際に反映します。

```text
先ほどのレビューを承認します。次の記入シート値を反映してください。

Workspace URL: <WORKSPACE_URL>
GitHub repository: <GITHUB_REPOSITORY_URL>
Git branch: main
App source path: app
Catalog / Schema / Volume: <CATALOG> / <SCHEMA> / <VOLUME_FULL_NAME>
SQL Warehouse ID: <SQL_WAREHOUSE_ID>
AI Search Endpoint / baseline Index: <AI_SEARCH_ENDPOINT> / <BASELINE_INDEX>
Embedding / default LLM / judge LLM: <EMBEDDING_ENDPOINT> / <DEFAULT_LLM_ENDPOINT> / <JUDGE_LLM_ENDPOINT>
Prep / Eval / Index Sync Job ID: <PREP_JOB_ID> / <EVAL_JOB_ID> / <INDEX_SYNC_JOB_ID>
MLflow Experiment path / ID: <EXPERIMENT_PATH> / <EXPERIMENT_ID>

変更対象はdatabricks.yml、app/app.yaml、jobs/job_common.pyと、顧客環境用の設定ファイルだけです。
App service principalのclient IDをJob sourceへ直書きしないでください。
基盤リソースを作成・削除する定義は追加しないでください。
変更後に差分と置換漏れを表示し、commitとpushは実行しないでください。
```

Changed filesで次を確認します。

- `targets.prod.workspace.host`と`root_path`がある
- App resourceの`git_repository.url`、`git_source.branch`、`git_source.source_code_path`が記入値と一致する
- `resources`のトップレベルにはAppだけがあり、Warehouse、Catalog、AI Search、Jobsを新規作成する定義がない
- `app/app.yaml`の`valueFrom` keyとResource Binding keyが一致する
- `jobs/job_common.py`のCatalog、Schema、AI Search Endpoint、MLflow、Warehouseが記入値へ変わっている
- Data Preparation Jobが`<BASELINE_TABLE>`への書き込みと`<BASELINE_INDEX>`の同期だけを行い、Index作成・削除・動的GRANTを行わない
- PAT、client secret、access token、個人の秘密情報がない

### App Resource Binding key

このアプリの論理keyは次の7つです。`app.yaml`と`databricks.yml`の両方で一致させます。

| Binding key | 既存リソース | UI表示／Bundle値 |
|---|---|---|
| `app-warehouse` | SQL Warehouse | Can use／`CAN_USE` |
| `toyota-volume` | Unity Catalog Volume | Can read and write／`WRITE_VOLUME` |
| `baseline-index` | baseline AI Search Index | Can select／`SELECT` |
| `default-llm` | 回答用LLM endpoint | Can query／`CAN_QUERY` |
| `prep-job` | Data Preparation Job | Can manage run／`CAN_MANAGE_RUN` |
| `eval-job` | Evaluation Job | Can manage run／`CAN_MANAGE_RUN` |
| `mlflow-experiment` | MLflow Experiment | Can edit／`CAN_EDIT` |

baseline IndexのBundle表現は`uc_securable`、`securable_type: TABLE`、完全名`<BASELINE_INDEX>`、`permission: SELECT`です。AI Search Endpoint自体はBindingへ追加せず、`VECTOR_SEARCH_ENDPOINT=<AI_SEARCH_ENDPOINT>`というApp runtime variableで渡します。

Index Sync Jobはbaseline同期用の独立Jobです。現在のApp Resource Bindingには含めません。

以降の`7つのResource Binding`は、本編のbaseline構成を指します。Step 13.3で比較用IndexをN件追加した場合は`7 + N件`と読み替え、各追加Indexへ一意なkeyと`SELECT`を設定します。App更新時の全項目更新にも、追加したN件を毎回含めます。

> **スクショポイント SS-15：Genie Codeのレビューと差分**
> 撮影箇所：対象Git folder、入力した要件、変更後のChanged files。
> 赤枠：「基盤リソースを作成・削除しない」と7つのBinding。
> 合格表示：App以外の作成がなく、記入値が反映され、秘密情報がない。

## 17. Step 12：GitHub Actions用OIDCを設定する

このStepはDatabricks Account管理者とGitHub Organization管理者が実施します。

### 目的

client secretやPATをGitHub Actionsへ保存せず、OIDCでDatabricksへ接続します。

### Databricks側

1. デプロイ専用service principalを作成します。
2. 対象Workspaceへ割り当てます。
3. Account Consoleでservice principalを開きます。
4. `Credentials & secrets > Federation policies`を開きます。
5. GitHub Environment `prod`を対象とするpolicyを作成します。

Federation policyのsubjectは次の形です。Organization名、Repository名、Environment名を完全一致させます。

```text
repo:<organization>/<repository>:environment:prod
```

デプロイ専用SPには、新規Appを作成できるWorkspace権限が必要です。既存Appを更新する場合はそのAppの`Can manage`が必要です。7つのResource Bindingを設定するため、対象Warehouse、Volume、Index、LLM endpoint、2 Jobs、Experimentにも管理者が承認した管理権限または所有権が必要です。権限が不足する場合は、SPへ広い管理者権限を付けず、対象リソースだけを追加します。

### GitHub側

1. Repositoryの`Settings > Environments`を開きます。
2. `prod` Environmentを作成します。
3. Environment variablesへ次を登録します。

| Variable | 値 |
|---|---|
| `DATABRICKS_HOST` | `<WORKSPACE_URL>` |
| `DATABRICKS_CLIENT_ID` | デプロイ専用SPのclient ID |
| `APP_NAME` | `<APP_NAME>` |
| `GIT_REPOSITORY_URL` | `<GITHUB_REPOSITORY_URL>` |
| `GIT_BRANCH` | `main` |
| `APP_SOURCE_PATH` | `app` |
| `UC_CATALOG` | `<CATALOG>` |
| `UC_SCHEMA` | `<SCHEMA>` |
| `UC_VOLUME_FULL_NAME` | `<VOLUME_FULL_NAME>` |
| `SQL_WAREHOUSE_ID` | `<SQL_WAREHOUSE_ID>` |
| `DATABRICKS_WORKSPACE_UI_HOST` | `<WORKSPACE_URL>` |
| `VECTOR_SEARCH_ENDPOINT` | `<AI_SEARCH_ENDPOINT>` |
| `BASELINE_INDEX_FULL_NAME` | `<BASELINE_INDEX>` |
| `DEFAULT_LLM_ENDPOINT` | `<DEFAULT_LLM_ENDPOINT>` |
| `MLFLOW_EXPERIMENT_ID` | `<EXPERIMENT_ID>` |
| `MLFLOW_TRACING_SQL_WAREHOUSE_ID` | `<SQL_WAREHOUSE_ID>` |
| `PREP_JOB_ID` | `<PREP_JOB_ID>` |
| `EVAL_JOB_ID` | `<EVAL_JOB_ID>` |

`INDEX_SYNC_JOB_ID`は運用記録として保存しますが、現在の7つのApp Resource Bindingには含めません。

GitHub Actionsには次の権限が必要です。

```yaml
permissions:
  contents: read
  id-token: write
```

deploy Jobには次の設定が必要です。

```yaml
jobs:
  deploy:
    environment: prod
    env:
      DATABRICKS_AUTH_TYPE: github-oidc
      DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}
      DATABRICKS_CLIENT_ID: ${{ vars.DATABRICKS_CLIENT_ID }}
```

このWorkflowは`databricks bundle validate --target prod`、`bundle deploy`、通常実行時の`bundle run`、`apps get`によるRunning確認を順番に行います。client secretをSecretsへ追加する手順はありません。

> **スクショポイント SS-16A：Federation policy**
> 撮影箇所：対象service principal、GitHub provider、repository、Environment名。
> 写さないもの：credential、secret、token。

> **スクショポイント SS-16B：GitHub Environment**
> 撮影箇所：`prod`とVariablesの「名前」。
> 赤枠：`DATABRICKS_HOST`と`DATABRICKS_CLIENT_ID`。
> 値はマスクして撮影する。

## 18. Step 13：Appのひな型を自動作成する

### 目的

App本体とApp専用service principalを先に作り、そのIDを取得します。

### なぜ2回に分けるのか

非公開GitHubリポジトリでは、App専用service principalがGitHubを読み取るためのcredentialを必要とします。そのservice principalはApp作成後に初めて発行されます。

そのため、初回は次だけを実行します。

1. Bundle設定を検証する
2. App本体と7つのResource Bindingを作成する
3. App専用service principalをDatabricksが自動生成する
4. private Gitからの取得とApp起動はまだ行わない

### UI操作

1. GitHubの`Actions`を開きます。
2. `Deploy Databricks App` workflowを開きます。
3. `Run workflow`をクリックします。
4. `bootstrap_only`を`true`にします。
5. branchが`main`であることを確認して実行します。
6. `validate`と`bundle deploy`が成功することを確認します。

`bootstrap_only=true`はこのRepository固有のWorkflow入力です。Databricks標準機能ではありません。この時点でJob IDを含むすべての既存リソース値が登録済みなので、Bundleのvalidateとdeployは実行でき、`bundle run`だけをスキップします。

### App service principalを確認する

1. DatabricksのApp switcherから`Databricks Apps`を開きます。
2. `<APP_NAME>`を開きます。
3. `Authorization`またはApp overviewでservice principalを確認します。
4. client IDを記入シートへ保存します。
5. Appのuser authorizationに`model-serving`が要求されていることを確認します。

> **スクショポイント SS-17A：初回GitHub Action**
> 撮影箇所：bootstrap workflowの成功したStep一覧。
> 赤枠：validate、bundle deploy。
> bundle runが未実行であることも分かるようにする。

> **スクショポイント SS-17B：App専用SP**
> 撮影箇所：App名とAuthorization。
> 赤枠：App専用service principalが作成されたこと。
> client IDは必要部分以外をマスクする。

## 19. Step 14：App専用SPへGit credentialを設定する

### 目的

Appが非公開GitHubリポジトリから`app/`を読み取れるようにします。

### UI操作

1. App overviewを開きます。
2. `Configure Git credential`をクリックします。
3. Providerで`GitHub`を選びます。
4. 組織の手順に従い、対象Repositoryへの読み取り権限を設定します。
5. App overviewへ戻り、Git credentialが設定済みであることを確認します。

この操作にはAppの`Can manage`が必要です。AppのGit credentialと、Step 2の開発者用Git credentialは別物です。App専用SPには対象private Repositoryの読み取りだけを許可します。

### App専用SP IDの扱いを確認する

App専用SPのclient IDは、次のStepでUnity Catalogの親権限とTable権限を設定するときだけ使います。Job sourceへ直書きしません。baseline Indexの`SELECT`はResource Bindingで付与するため、Data Preparation Jobから動的に`GRANT`する必要もありません。App SPのsecretやGit tokenは、どのファイルにも書きません。

> **スクショポイント SS-18：App用Git credential**
> 撮影箇所：App overviewのGit接続状態。
> 赤枠：Provider、Repository、Configured状態。
> 写さないもの：PAT、GitHub token、OAuth code。

## 20. Step 15：AppとJobの権限を設定する

### App専用service principal

7つのResource Bindingを設定すると、DatabricksがApp専用SPへWarehouse、Volume、baseline Index、既定LLM、2 Jobs、Experimentの指定権限を付与します。ここで同じ権限を手動GRANTし直しません。

手動で追加するのは、BindingしていないUnity Catalogの親権限とDelta Tablesだけです。

| 対象 | 手動権限 |
|---|---|
| Catalog／Schema | `USE CATALOG`／`USE SCHEMA` |
| 読み取り専用Table | `SELECT` |
| Appが更新するTable | `SELECT`、`MODIFY` |
| UIで追加選択を許可するLLM endpoint | App専用SPへ`Can query` |

読み取り専用は`toyota_rag_model_catalog`、`toyota_rag_model_defaults`、`toyota_vehicle_master`、`toyota_rag_eval_results`、`toyota_rag_eval_suggestions`です。Appが更新するのはProject、member、document registry、Variant、chat session／message／run、prep run、parsed、evaluation case／runの各Tableです。特に`toyota_index_variants`と`toyota_rag_eval_cases`には`MODIFY`が必要です。

Jobの`Run as` identityの権限はStep 10.5で別に設定済みです。App専用SPとJobの`Run as`を同じものとして扱いません。

### Genie Codeで権限SQLを確認する

```text
この節の権限表をもとに、Catalog、Schema、App service principal client IDを今回の値へ置き換えたGRANT文を作成してください。
7つのResource Bindingで付与するWarehouse、Volume、baseline Index、default LLM、2 Jobs、Experimentの重複GRANTは除外してください。
DROP、REVOKE、ALL PRIVILEGESは追加しないでください。
まず必要なGRANTと理由だけを表で説明し、まだ実行しないでください。
```

説明を確認後、SQL EditorでGRANT文を実行します。

> **スクショポイント SS-19：権限設定**
> 撮影箇所：SQL Editorで親Catalog／SchemaとDelta TableのGRANTが成功した結果。
> 赤枠：App SP、対象Catalog／Schema、最小権限。
> 写さないもの：secret、token、無関係なprincipal。

## 21. Step 16：7つのResource Bindingを自動設定する

### デプロイ前の値を再確認する

GitHub Environmentの`PREP_JOB_ID`、`EVAL_JOB_ID`、`APP_NAME`、`BASELINE_INDEX_FULL_NAME`が記入シートと一致することを確認します。初回bootstrapで作成したApp名を変更しません。

### GitHubへ変更を反映する

1. Git folderのGit画面を開きます。
2. Changed filesを1つずつ確認します。
3. 変更対象が、講師のallowlistにある`app/` runtime files、`jobs/job_common.py`、`databricks.yml`、顧客環境用設定だけであることを確認します。
4. `tests/`、`.venv/`、`node_modules/`、`tmp/`、`output/`、PDF、`package.json`、`package-lock.json`が追加されていないことを確認します。
5. PAT、client secret、access token、不要な個人IDが含まれていないことを確認します。
6. Commit messageへ`Configure customer RAG app resources`と入力します。
7. `Commit & Push`を実行します。

> **スクショポイント SS-20A：Commit前の差分**
> 撮影箇所：Changed files、branch、commit message。
> 赤枠：allowlist内の変更ファイルと`main` branch。
> 写さないもの：credentialとsecret。

### 通常デプロイを実行する

1. GitHubのActionsを開きます。
2. `Deploy Databricks App`を選びます。
3. `bootstrap_only=false`で実行します。
4. 次の処理がすべて成功することを確認します。

```text
Bundle validate
    ↓
App resourceと7 Bindingを更新
    ↓
GitHubのmain / appをデプロイ
    ↓
Appを起動または再起動
    ↓
StatusがRunningになるまで確認
```

App説明だけを部分更新すると、既存Bindingが外れることがあります。このRepositoryでは、App更新時に7つすべてのBindingと`model-serving` scopeを含めることを「全項目更新の安全ルール」としています。これはDatabricksの公式用語ではなく、このRepository固有の運用ルールです。

### App UIで確認する

1. `Databricks Apps > <APP_NAME>`を開きます。
2. App detailsの`Edit`または`Configure`を開き、`App resources` sectionを表示します。独立したタブとは限りません。
3. 7つのResource、key、UI権限（Can use／Can read and write／Can select／Can query／Can manage run／Can edit）を確認します。
4. Git設定がRepository URL、`main`、source code path `app`を指し、native `Auto deploy on push events`がOFFであることを確認します。
5. `Deployments`で最新Deploymentが`Succeeded`であることを確認します。
6. OverviewでAppが`Running`であることを確認します。
7. `Logs`に起動エラーがないことを確認します。
8. User authorizationの要求scopeに`model-serving`、作成後のeffective scopeに`iam.current-user:read`と`iam.access-control:read`があることを確認します。

> **スクショポイント SS-20B：App Resource Binding**
> 撮影箇所：7つのResource、Resource key、Permission。
> 合格表示：本編は7件すべてが表示される。比較用IndexをN件追加した場合は`7 + N件`が表示される。
> 写さないもの：環境変数の値、credential。

> **スクショポイント SS-20C：GitHub Actions**
> 撮影箇所：validate、deploy、run、health checkの成功状態。
> 合格表示：すべてGreen。

> **スクショポイント SS-20D：Appの正常状態**
> 撮影箇所：OverviewのRunning、DeploymentsのSucceeded、App URL。
> 赤枠：Runningと最新commit。
> App URL以外の内部URLは必要に応じてマスクする。

## 22. Step 17：ヘルスチェックを行う

最初にDatabricks標準の状態確認を行います。App overviewが`Running`で、GitHub Actionsの`databricks apps get <APP_NAME>`結果が`app_status.state = RUNNING`なら合格です。

次に、このRAG App独自のヘルスチェックを行います。`/api/health`はDatabricks Apps共通の標準Endpointではありません。

1. App URLを開き、サインインします。
2. URL末尾へ`/api/health`を付けて開きます。
3. HTTP 200が返ることを確認します。
4. JSONの`status=ok`、`databricks_ready=true`、`missing=[]`を確認します。

例：

```text
https://<app-url>/api/health
```

次も確認します。

- SQL Warehouse bindingを解決できる
- Volume bindingを解決できる
- baseline Indexを参照できる
- 回答用LLM endpointを参照できる
- Data Preparation／Evaluation Job IDを参照できる
- MLflow Experiment IDを参照できる

> **スクショポイント SS-21：ヘルスチェック**
> 撮影箇所：App overviewのRunningと、独自EndpointのHTTP 200、`status=ok`、`databricks_ready=true`、`missing=[]`。
> 写さないもの：token、Authorization header、secret形式の環境変数。

## 23. Step 18：RAGアプリを操作する

### 23.1 Projectを作成する

1. Appを開きます。
2. 右上に、現在Databricksへサインインしている自分のメールアドレスが表示されることを確認します。
3. `Projectを作成`をクリックします。
4. Project名と説明を入力します。
5. 作成後、すべての画面で同じProjectが選択されていることを確認します。

### 23.2 PDFを登録する

1. 左メニューの`データ準備`を開きます。
2. Step 5.6のD01～D08をアップロードします。最初に1冊で画面を確認し、問題がなければ残りを登録します。
3. 任意メタデータは、必要な場合だけ開いて入力します。
4. Document Parsingが完了するまで進行状況を確認します。
5. `PDFカタログ`を開き、タイトル、20～30字程度のAI概要、PDFリンクが表示されることを確認します。

裏側では、Volume保存後に`READ_FILES(..., format => 'file')`の`FILE`値を`ai_parse_document`へ渡します。

### 23.3 検索データを作る

1. チャンクサイズ`512`を選びます。
2. 手法`Standard`を選びます。
3. Embedding modelでQwen3 Embedding 0.6Bを選びます。
4. 画面に`使用するIndex: <BASELINE_INDEX>`と表示されることを確認します。
5. `RAG検索データを作成`をクリックします。
6. Data Preparation Jobが`Succeeded`になるまで待ちます。
7. `<BASELINE_INDEX>`のData Ingestが`Idle`、Latest syncが成功になることを確認します。

この操作で、Jobは手動作成済みの`<BASELINE_TABLE>`へチャンクを書き込み、手動作成済みの`<BASELINE_INDEX>`を同期します。新しいEndpoint／Indexは作りません。異なるチャンク設定を選び、対応する既存Indexが未登録の場合は、`この設定は管理者によるIndex準備が必要です`と表示され、処理を開始できないことを確認します。

### 23.4 RAGチャットを確認する

1. `RAGチャット`を開きます。
2. Vector Searchを選んで質問します。
3. 次にHybrid Searchを選んで同じ質問を送ります。
4. PDFリンクとページが表示されることを確認します。
5. 停止ボタンを押すと回答生成が止まることを確認します。

### 23.5 正解付き評価質問を3問登録する

Data Preparation Jobは、画面を試せるように`starter-v1`の質問を3問自動登録します。ただし、事実を捏造しないため期待回答と正解ページは空です。この3問だけではAnswer Correctnessやページ単位の検索指標を正式比較できません。

1. `RAG精度評価`を開きます。
2. `新しい質問を登録`を開きます。
3. 実際のPDFを読み、次の形式で1問ずつ登録します。

| UI項目 | 入力方法 |
|---|---|
| 質問 | PDFだけで答えられる具体的な質問 |
| 期待する回答 | PDFの記載に合わせた短い正解。ハンズオンでは必ず入力 |
| 正解PDF | 根拠が書かれた解析済みPDF |
| 正解ページ | PDF viewerの1始まりの物理ページ。複数は`2,3`のように入力 |
| 評価データ版 | 3問とも`v1.0.0` |
| 用途 | 3問とも`開発用（調整用）` |

このシナリオでは次の3問を登録します。ページ番号はPDF viewerで再確認し、配布版が変更されていた場合は実際のページへ合わせます。

| 質問 | 期待する回答 | 正解PDF | 正解ページ |
|---|---|---|---|
| 2024年式プリウスのE-Fourの型式は何ですか。 | ZVW65です。2WDはZVW60です。 | D01 | 2 |
| 2024年式プリウスのGグレードにSEAは標準、オプション、設定なしのどれですか。 | 設定なしです。 | D03 | 2 |
| ZVW60の救援作業で高電圧を遮断する手順を、待機時間を含めて順番に教えてください。 | READYをOFFにし、電子キーを車両から5 m以上離し、12Vバッテリーのマイナス端子を切り離し、サービスプラグを取り外し、10分間待機します。 | D05 | 3,4 |

4. `＋ 評価質問を追加`を3回実行します。
5. 評価データ版`v1.0.0`、用途`開発用（調整用）`を選びます。
6. 3問すべてに`回答・検索の正解あり`と表示されることを確認します。
7. `正解を確認`を開き、期待回答、PDF、ページをもう一度照合します。

### 23.6 Phase評価を確認する

1. 評価データ版`v1.0.0`、用途`開発用（調整用）`を選びます。
2. `すべて選択`を押し、正解付き3問だけが選択されていることを確認します。
3. Phase 1～5、比較する検索データ、回答用LLM、採点用LLMを選びます。
4. 初回は`繰り返し回数=1`で実行し、全工程の成功を確認します。
5. 検索再現率、検索適合率、検索順位、回答正解率、根拠一致率、引用正解率、レイテンシ、トークン、エラー率を比較します。
6. PhaseごとのLLM改善提案を確認します。
7. MLflow Traceへのリンクを開きます。
8. 時間に余裕があれば繰り返し回数を3へ上げ、同じ3問で再実行します。

> **スクショポイント SS-22A：データ準備**
> 撮影箇所：Project、PDF、チャンク設定、処理状況。
> 合格表示：Document Parsingと、既存baseline Indexへの同期が完了。

> **スクショポイント SS-22B：RAGチャット**
> 撮影箇所：回答、PDFリンク、ページ、検索方式。
> 合格表示：根拠がチャンク文字列ではなくPDFリンクで表示される。

> **スクショポイント SS-22C：RAG精度評価**
> 撮影箇所：正解付き3問、Phase 1～5、主要指標、改善提案。
> 合格表示：3問が`回答・検索の正解あり`で、同じ評価データをPhase比較できる。

> **スクショポイント SS-22D：MLflow Trace**
> 撮影箇所：Trace、Retriever、LLM、Latency。
> 写さないもの：PDF本文の機密情報と入力者の個人情報。

> **スクショポイント SS-22E：既存Indexだけが使われたことの確認**
> 撮影箇所：Data Preparation後の`<BASELINE_INDEX>` Overviewと、同じSchemaのIndex一覧。
> 合格表示：Latest syncが成功し、Step 8終了時と比べてIndex名と件数が増減していない。

## 24. よくある問題

| 症状 | 主な原因 | 確認場所 | 対応 |
|---|---|---|---|
| Git folderを作成できない | 開発者用Git credentialがない | Settings > Linked accounts | GitHubをLinkする |
| AppのGit deployが失敗する | App SP用Git credentialがない | App overview | Configure Git credentialを実行する |
| GitHub ActionsがDatabricksへ接続できない | OIDC policyまたはEnvironment名が不一致 | Federation policy／GitHub Environment | Repository、branch、Environmentを確認する |
| App名がすでに存在すると出る | 別Bundleまたは手動作成Appが同じ名前を所有 | Databricks Apps一覧 | 削除せず講師へ連絡し、既存AppをBundleへbindするか別名を決める |
| Appはあるが起動しない | 初回bootstrap後にbundle runしていない | GitHub Actions | 通常deployを再実行する |
| `valueFrom`を解決できない | Binding keyが不一致 | App resources／app.yaml | 7つのkeyを完全一致させる |
| SQLがPermission deniedになる | App SPまたはJob Run asにUC権限がない | Catalog Permissions／Job Run as | identityごとに最小権限を付与する |
| `ai_parse_document`が実行できない | FILE型、Preview、Region、Warehouse要件が未充足 | SQLエラー／Previews | 管理者へ有効化を依頼する |
| Indexを作成できない | CDFが無効、Primary key不適切 | source Table Properties | CDF=trueと`chunk_id`を確認する |
| Indexが更新されない | Triggered IndexをSyncしていない | Index > Data Ingest | Sync nowを実行する |
| チャンク設定を選ぶと開始できない | 対応するTable／Indexが手動作成・登録されていない | データ準備のIndex表示 | 本編ではStandard／512／Qwen3を選ぶ。追加条件は管理者が先に作成する |
| Data Preparation Jobがすぐ失敗する | 空の`prep_run_id`で単独実行した | Job parameters | Appから処理を開始する |
| Evaluation Jobがすぐ失敗する | 空の`eval_run_id`で単独実行した | Job parameters | Appから評価を開始する |
| LLMが一覧にない | RegionやWorkspaceで利用不可 | Serving／Playground | 表示される対応Endpointへ変更する |
| 検索結果が0件 | source Tableが空、Sync前、Project filter不一致 | Table件数／Index／Project | PDF登録、Variant完了、Index Onlineを確認する |
| 回答正解率が—になる | starter質問に期待回答がない | RAG精度評価 > 正解を確認 | `v1.0.0`へ期待回答、正解PDF、正解ページ付きの3問を登録する |

## 25. ハンズオン終了時の費用確認

1. SQL WarehouseのAuto Stopが有効であることを確認します。
2. 実行中のLakeflow Jobがないことを確認します。
3. AI Search Endpoint／Indexの利用状況を確認します。
4. 不要な高スループット設定やContinuous Syncを残していないことを確認します。
5. GitHub Actionsの不要な再実行を停止します。

リソース削除は、講師またはWorkspace管理者の指示がある場合だけ行ってください。削除すると、別参加者のハンズオンや評価履歴へ影響する可能性があります。

## 26. 最終チェックリスト

- [ ] SQL WarehouseがRunningになり、`SELECT 1`が成功した
- [ ] Catalog、Schema、Managed Volumeを作成した
- [ ] 19個のDelta Tablesが表示され、migrationの6検査値がすべて0になった
- [ ] baseline source TableのCDFがtrueになっている
- [ ] `ai_parse_document`へ`FILE`型を渡して解析できた
- [ ] 利用可能なEmbedding／LLM endpoint名を記録した
- [ ] AI Search EndpointがOnlineになった
- [ ] baseline IndexがOnline、Data IngestがIdleになった
- [ ] App／JobがEndpoint／Indexを作成・削除せず、既存Indexの同期・検索だけを行うことを確認した
- [ ] Data Preparationの前後でAI Search Indexの名前と件数が変わっていない
- [ ] MLflow Experiment path／IDを記録した
- [ ] Genie Codeが基盤リソースを作成・削除しない設定になっている
- [ ] GitHubの開発者用、App用、Actions用認証を区別した
- [ ] AppとApp専用service principalが作成された
- [ ] Lakeflow Jobsを3つ作成し、IDを記録した
- [ ] App SPとJob Run asへ必要な権限を設定した
- [ ] App Resource Bindingが7件ある
- [ ] GitHub Actionsのvalidate、deploy、run、health checkが成功した
- [ ] AppがRunning、最新DeploymentがSucceededになった
- [ ] `/api/health`がHTTP 200、`status=ok`、`databricks_ready=true`、`missing=[]`を返した
- [ ] 期待回答、正解PDF、正解ページ付きの評価質問を3問登録した
- [ ] PDF登録、RAGチャット、Phase 1～5評価、MLflow Traceを確認した

## 27. スクショ一覧

| ID | 撮影する画面 | 合格条件 |
|---|---|---|
| SS-01 | Genie Code開始画面 | Ask first、計画のみ |
| SS-02 | Linked accounts | GitHub接続済み |
| SS-03 | Git folder | repository、main、app/jobs/sql |
| SS-04 | SQL Warehouse | Serverless、X-Large、Running |
| SS-05 | Catalog | Standard catalogとOwner |
| SS-06 | Schema | 正しいbreadcrumb |
| SS-07 | Volume | ManagedとFiles領域 |
| SS-08A | SQL Editor | DDL成功 |
| SS-08B | Table一覧／baseline Table | 19 Table、migration検査0、CDF=true |
| SS-09 | Document Parsing | FILE入力で成功 |
| SS-10A | Serving | 選択モデルが利用可能 |
| SS-10B | Playground | LLM応答成功 |
| SS-11 | AI Search Endpoint | Standard、Online |
| SS-12A | AI Search Index設定 | Hybrid、Triggered |
| SS-12B | Index Overview | Online、Idle、Sync成功 |
| SS-13 | MLflow Experiment | path／ID確認 |
| SS-14A～C | 3つのJobs | Task、Compute、parameter、retryが保存済み |
| SS-15 | Genie Codeレビュー | Appだけを自動作成し、記入値を反映 |
| SS-16A | Federation policy | GitHub OIDC設定済み |
| SS-16B | GitHub Environment | Variable名が揃う |
| SS-17A | bootstrap workflow | validate／deploy成功、runは未実行 |
| SS-17B | App Authorization | App専用SP生成済み |
| SS-18 | App Git credential | private repoへ接続済み |
| SS-19 | Permissions | 最小権限のGRANT成功 |
| SS-20A | Git差分 | secretなし |
| SS-20B | App resources | 7 Binding |
| SS-20C | GitHub Actions | 全Step成功 |
| SS-20D | App overview | Running／Succeeded |
| SS-21 | Overviewと`/api/health` | Running、HTTP 200、status ok、missingなし |
| SS-22A～E | アプリ、MLflow、AI Search | PDF、Chat、正解付き3問、Phase評価、Trace成功、Index件数不変 |

スクリーンショットでは、PAT、client secret、Authorization header、cookie、個人メール、機密PDF本文、他部署のリソースを必ず隠してください。

## 28. 公式ドキュメント

### Genie CodeとGitHub

- [Genie Codeの操作](https://learn.microsoft.com/en-us/azure/databricks/genie-code/navigate-genie-code)
- [Genie Code Agent mode](https://learn.microsoft.com/en-us/azure/databricks/genie-code/agent-mode)
- [Git providerへの接続](https://learn.microsoft.com/en-us/azure/databricks/repos/get-access-tokens-from-git-provider)
- [Git folders](https://learn.microsoft.com/en-us/azure/databricks/repos/git-operations-with-repos)
- [GitHub ActionsからDatabricks Appsをデプロイ](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/cicd-github-actions)
- [GitHub ActionsのOIDC認証](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/provider-github)

### Databricksリソース

- [SQL Warehouseの作成](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/create)
- [Catalogの作成](https://learn.microsoft.com/en-us/azure/databricks/catalogs/create-catalog)
- [Schemaの作成](https://learn.microsoft.com/en-us/azure/databricks/schemas/create-schema)
- [Unity Catalog Volume](https://learn.microsoft.com/en-us/azure/databricks/volumes/)
- [VolumeをUI／SQLで作成](https://learn.microsoft.com/en-us/azure/databricks/volumes/utility-commands)
- [`FILE`型](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/data-types/file-type)
- [`ai_parse_document`](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_parse_document)
- [AI Search Endpoint／Indexの作成](https://learn.microsoft.com/en-us/azure/databricks/ai-search/create-ai-search)
- [Lakeflow Jobsの作成](https://learn.microsoft.com/en-us/azure/databricks/jobs/configure-job)
- [Serverless Jobs](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
- [MLflow Experiment](https://learn.microsoft.com/en-us/azure/databricks/mlflow/experiments)
- [Foundation Model APIsの対応モデル](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/foundation-model-apis/supported-models)

### Databricks Apps

- [Custom Appの作成](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/create-custom-app)
- [Git repositoryからAppをデプロイ](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/deploy)
- [App resources](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/resources)
- [Appの認証](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth)
- [Declarative Automation BundlesのApp resource](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/resources)
