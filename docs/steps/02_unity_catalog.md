# Step 2: Unity Catalogの土台を作る

## 目的

Project、任意分野のPDF、解析結果、チャット履歴、評価結果を保存するCatalog、Schema、Volume、Delta Tableを作ります。既存のトヨタ評価環境には、汎用文書メタデータとPDF単体の論理削除／Variant後継管理に必要な列を安全に追加します。

## 構築

次のSQLは`IF NOT EXISTS`を使っており、同じ環境へ再実行できます。

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

`CREATE TABLE IF NOT EXISTS`は、すでに存在するTableへ新しい列を追加しません。そのため、既存環境では2つのmigrationを上の順番で必ず実行します。migrationは冪等で、新規環境でも実行してschemaをそろえます。

追加するregistry列:

| 列 | 用途 |
|---|---|
| `category` | 文書の分類 |
| `tags` | 複数の検索タグ |
| `document_date` | 文書日付 |
| `source` | 発行元やURL |
| `metadata_json` | schema version付きの汎用・追加・後方互換メタデータ |

migrationは、空のタイトルをPDFファイル名から補完し、既存の`model`、`model_year`、`document_type`、`vehicle_category`を`metadata_json.legacy`へ保持します。

PDF論理削除migrationは、次を追加します。列を追加するだけで、PDF原本、解析結果、会話、評価結果を削除しません。

| Table | 追加する管理情報 |
|---|---|
| `toyota_rag_projects` | Project単位の更新ロックを示す`mutation_token`、`mutation_type`、`mutation_target_id`、`mutation_started_at` |
| `toyota_document_registry` | `lifecycle_status`、`deletion_request_id`、`deleted_by`、`deleted_at` |
| `toyota_index_variants` | `source_document_ids`、`lifecycle_status`、後継Variant／削除request／理由／日時 |

`toyota_document_registry`の`summary`、`summary_source`、`summary_model_key`、`summary_prompt_version`、`summary_status`は、カタログ用の短い概要と生成状態を記録します。`toyota_rag_model_defaults`は、モデル検出結果とは分離して、Embeddingの推奨値と利用不可時のfallback方針を管理します。

作成先:

| 種類 | 名前 |
|---|---|
| Catalog | `<UC_CATALOG>` |
| Schema | `<UC_CATALOG>.rag_accuracy` |
| Volume | `<UC_CATALOG>.rag_accuracy.documents` |

## 確認

Catalog ExplorerでCatalog、Schema、Volumeを開きます。CLIからはTable一覧も確認できます。

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT table_name, table_type FROM <UC_CATALOG>.information_schema.tables WHERE table_schema = 'rag_accuracy' ORDER BY table_name"
```

migration直後に次も確認します。

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT count_if(title IS NULL OR trim(title) = '') AS missing_titles, count_if(tags IS NULL) AS null_tag_arrays, count_if(metadata_json IS NULL OR get_json_object(metadata_json, '$.schema_version') IS NULL) AS invalid_metadata_json, count_if(get_json_object(metadata_json, '$.schema_version') <> '1.0') AS bad_schema_version FROM <UC_CATALOG>.rag_accuracy.toyota_document_registry"
```

`09_document_logical_deletion.sql`の最後に返る次の2値も保存します。

| 値 | 合格値 |
|---|---:|
| `documents_without_lifecycle` | `0` |
| `variants_without_lifecycle` | `0` |

合格条件:

- Catalog、Schema、Volumeが存在する。
- `sql/01_foundation.sql`に定義した管理Tableがすべて存在する。
- TableがDelta形式で作成されている。
- Project IDを保持するTableでは、後続処理が必ずProject条件を付ける。
- registryに5つの汎用列が存在する。
- registryに概要本文、生成元、生成モデル、prompt版、生成状態が存在する。
- モデルの推奨値がendpoint検出Tableとは別の`toyota_rag_model_defaults`にある。
- `missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`がすべて`0`である。
- Project／registry／VariantにPDF論理削除用の列があり、`documents_without_lifecycle`と`variants_without_lifecycle`がともに`0`である。

## 失敗時の修正

- `PERMISSION_DENIED`の場合は、構築担当者の`USE CATALOG`、`USE SCHEMA`、`CREATE TABLE`、`CREATE VOLUME`を確認します。
- 一部だけ作成済みの場合は、手動削除せず同じSQLを再実行します。
- 同名の既存オブジェクトが別用途の場合は上書きせず、作業を止めて所有者へ確認します。
- migration検証値が1つでも`0`でなければ次へ進みません。該当するregistry行のタイトル、タグ配列、`metadata_json`を確認し、修正後にmigrationと検証SELECTを再実行します。
- PDF削除migrationの2値が`0`でない場合は、Appをデプロイせず対象Tableの`lifecycle_status`を確認します。Tableを手動削除せず、migrationを再実行して同じ2値を確認します。

## この環境の実測結果

`SUCCESS`。既存のCatalog、Schema、Volume、基礎Tableを維持したまま、`sql/07_migrate_generic_document_metadata.sql`を実環境で適用しました。`missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`はすべて0です。

`SUCCESS`。`sql/09_document_logical_deletion.sql`適用後のpost-checkで、`documents_without_lifecycle=0`／`variants_without_lifecycle=0`を確認しました。確認Statementは`<STATEMENT_ID>`です。

既存E2Eの基準時点ではProject 3件、文書10件、解析済み10件で、registry状態は`PARSED=8`、`READY=2`でした。既存のトヨタ評価用9冊に加え、非車両PDF G01で汎用メタデータの保持と旧車両値がNULLであることを確認しました。

asset `1.4.4`の削除検証後に取得した最新aggregateは、非`ARCHIVED` Project 11件、文書registry 22件（`ACTIVE=18`／`DELETING=0`／`DELETED=4`）、Variant 20件（`READY=13`／`SUPERSEDED=7`）、評価ケース44件です。Project mutation lock残存は0件です。確認Statementは`<STATEMENT_ID>`です。

asset `1.4.3`のfresh削除helper直後は、Project 13件、文書registry 22件（`ACTIVE=19`／`DELETED=3`）、Variant 18件（`READY=15`／`SUPERSEDED=3`）、評価ケース44件でした。文書／Variantのlifecycle欠損とProject mutation lock残存は0で、Statementは`<STATEMENT_ID>`です。これは過去snapshotであり、現在値として扱いません。

解析済み20件とprocessing `PARSED=6`／`READY=12`／`DELETED=2`／`ERROR=0`も過去の20件snapshotであり、最新22件の内訳として扱いません。概要監査も同じ20件snapshotで空欄0件、20〜30字違反0件、`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、`USER=1`で、Statementは`<STATEMENT_ID>`です。

## 公式ドキュメント

- [Unity Catalog Volume](https://docs.databricks.com/aws/en/volumes/)
- [Unity Catalogの権限](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/)
- [Delta LakeのChange Data Feed](https://docs.databricks.com/aws/en/delta/delta-change-data-feed)
