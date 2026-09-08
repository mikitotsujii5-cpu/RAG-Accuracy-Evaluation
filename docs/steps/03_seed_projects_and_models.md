# Step 3: Project、PDF、モデルカタログを登録する

## 目的

任意分野のデータ群をProject単位で分離し、PDFと、対象Workspaceで実際に利用できるFMAPIモデルを登録します。トヨタPDFのseedは、同梱評価シナリオを再現するときだけ使用します。

## 構築

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

`sql/02_seed_core.sql`と`sql/03_register_documents.sql`はトヨタ評価シナリオ用です。汎用RAGの通常利用では、画面からProjectを作り、任意のPDFを登録できます。モデルカタログの同期はどちらでも必要です。

PDFは[PDFコーパスREADME](../../output/pdf/README.md)に従って、次のように分離します。

| Project | PDF | 用途 |
|---|---|---|
| `Toyota RAG Baseline` | D01〜D08 | Phase 1〜5の固定評価 |
| `Toyota Scan Parsing` | D09 | D05とのDocument Parsing比較 |

D05とD09は同じ内容です。同じProject／Indexへ混ぜると重複ヒットで評価が歪むため、別Projectのままにします。

非車両文書のsmoke testには、別Projectを作り[`generic_information_security_policy_demo.pdf`](../../output/pdf/generic_information_security_policy_demo.pdf)を登録します。入力例は次のとおりです。

```text
title: 情報セキュリティ規程
category: 社内規程
tags: 情報セキュリティ, 全社員
document_date: 2026-04-01
source: 情報システム部
追加メタデータ:
  版 = 第3版
  機密区分 = 社内公開
```

必須入力はPDFだけです。画面の「文書情報を追加」は初期状態では閉じておき、必要な場合だけ開きます。タイトル未入力時はPDFファイル名から補完し、概要未入力時はDocument Parsing後にFMAPIのTool Calling対応LLMが日本語20〜30字の概要を生成します。LLMが利用できない場合でも登録処理は失敗させず、タイトル／ファイル名から作る短い概要へ安全にfallbackします。汎用メタデータの制約は次のとおりです。

| 項目 | 制約 |
|---|---|
| `title` | 300文字以下 |
| `category` | 100文字以下 |
| `tags` | 20件以下、各80文字以下、空文字・大文字小文字違いの重複なし |
| `document_date` | `YYYY-MM-DD` |
| `source` | 500文字以下 |
| 任意key-value | 20件以下、key 80文字以下、value 1000文字以下 |

任意keyには、共通項目名とsystem項目の`document_id`／`project_id`／`doc_uri`を使えません。`model`／`model_year`／`document_type`／`vehicle_category`という名前は汎用の任意keyとして使用でき、`metadata_json.custom`へ保存されます。旧トヨタ項目は既存クライアントとseedの後方互換としてトップレベルでも受付を続け、`metadata_json.legacy`へ分離しますが、汎用PDFでは不要です。

Embeddingはモデルカタログから単独選択せず、管理者が既存Delta Table／AI Search Indexとともに登録したIndex Profileから決まります。通常構成はStandard／512／Qwen3 Embedding 0.6Bの1件です。このProfileが利用不可なら別のREADY Embeddingへfallbackせず、検索データ準備を利用不可として扱います。別Embeddingを使う場合は対応する物理リソースを手動作成し、App／Job両方の許可リストへProfile登録します。画面の「日本語対応」はQwen3 Embedding 0.6Bだけに表示します。

## 確認

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT p.project_name, COUNT(r.document_id) AS document_count FROM <UC_CATALOG>.rag_accuracy.toyota_rag_projects p LEFT JOIN <UC_CATALOG>.rag_accuracy.toyota_document_registry r ON r.project_id = p.project_id GROUP BY p.project_name ORDER BY p.project_name"
```

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT COUNT(*) AS selectable_ready_models, COUNT_IF(array_contains(capabilities, 'embedding')) AS embedding_models, COUNT_IF(array_contains(capabilities, 'chat')) AS chat_models FROM <UC_CATALOG>.rag_accuracy.toyota_rag_model_catalog WHERE selectable = TRUE AND region_available = TRUE AND endpoint_state = 'READY'"
```

合格条件:

- トヨタ評価シナリオをseedした場合、Projectが2件あり、D01〜D08がbaseline、D09がscan Projectにだけ属する。
- registryのURI、SHA-256、サイズがVolume上のPDFと対応する。
- Chat／JudgeのUI候補は、Workspaceで発見でき、`READY`、region利用可、`selectable=true`のモデルだけである。Embeddingは登録済みIndex Profileに含まれるものだけを表示する。
- ChatでTool Callingを必要とする場合は`tool_calling` capabilityも確認する。
- 通常構成ではStandard／512／Qwen3 Embedding 0.6Bの登録済みProfileだけが固定表示される。Profileが利用不可でも、未登録のREADY Embeddingを代替表示しない。
- 汎用smoke Projectでは、PDFだけの登録でタイトルがファイル名から補完される。任意メタデータ付きの登録も確認するときは、同一ProjectのSHA-256重複を避けて別PDFまたは別Projectを使い、`category`、`tags`、`document_date`、`source`、`metadata_json`が保持される。
- 概要を手入力しないPDFは、解析完了後に20〜30字の`AI_GENERATED`／`AI_GENERATED_NORMALIZED`概要、または同じ長さの`FILENAME_FALLBACK`概要を持つ。決定論的に長さを整えたLLM出力は`AI_GENERATED_NORMALIZED`として区別する。
- 汎用PDFの登録で車種master照合を要求されない。

## 失敗時の修正

- PDF欠落またはhash不一致は、対象1冊だけを正しいProject pathへ再配置してregistryを更新します。
- モデル名を手入力で追加しません。`sync_model_catalog.py`を再実行し、endpoint状態と利用権限を再確認します。
- 別Projectの文書が混ざった場合はData Preparation runを開始する前に修正します。Jobは新規Indexを作らず、登録済みProfileを検証・同期します。既に作った誤った論理Variantは正式評価へ使いません。
- 汎用PDFの登録で列不足が出る場合は、Step 2へ戻りmigrationと4つの検証値を確認します。
- メタデータ制約違反は値だけを修正します。制約を緩めたり、任意JSONを未検証のまま保存したりしません。

## この環境の実測結果

`SUCCESS`。既存トヨタ評価環境のProject 2件、PDF 9冊（baseline 8冊、scan 1冊）を維持し、非車両Project `<RESOURCE_ID>`へG01（document `<RESOURCE_ID>`）を登録しました。この時点のProject 3件、PDF 10冊は再現用の基準コーパスです。タイトル自動補完、汎用メタデータの保持、旧車両値NULLを実環境で確認しました。

asset `1.4.4`の削除検証後に取得した最新aggregateは、非`ARCHIVED` Project 11件、文書registry 22件（`ACTIVE=18`／`DELETING=0`／`DELETED=4`）です。確認Statementは`<STATEMENT_ID>`です。asset `1.4.3`のfresh削除helper直後に取得したProject 13件、文書registry 22件のStatement `<STATEMENT_ID>`は、過去snapshotとして保持します。

モデルカタログはStatement `<STATEMENT_ID>`で再同期し、READYかつ選択可能な候補49件（Chat 46件、Embedding 3件）を確認しました。Statement `<STATEMENT_ID>`では、READYでないのに選択可能なモデルは0件です。

概要品質はregistry 20件のsnapshotで再確認し、空欄0件、20〜30字違反0件でした。内訳は`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、利用者入力の`USER=1`です。Statementは`<STATEMENT_ID>`です。この概要監査値は、asset `1.4.3`当時のregistry 22件全体にも、asset `1.4.4`の現在値にも外挿しません。

## 公式ドキュメント

- [Foundation Model APIs](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/)
- [Foundation Model APIsで利用できるモデル](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models)
- [Unity Catalog Volumeへファイルを取り込む](https://docs.databricks.com/aws/en/ingestion/file)
