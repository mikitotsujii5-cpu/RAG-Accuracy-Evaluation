# Step 4: `FILE`型でPDFをDocument Parsingする

## 目的

PDFから本文、表、図、ページ、レイアウト情報を抽出します。このシナリオでは、`ai_parse_document`への入力に必ず`FILE`型を使います。`BINARY`列は使いません。

## 構築

最初にD01だけを解析します。

```bash
python3 scripts/execute_sql_file.py sql/04_parse_d01_smoke.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

SQLの重要部分は次のとおりです。

```sql
SELECT
  ai_parse_document(
    f.source_file,
    map(
      'version', '2.0',
      'imageOutputPath', '<Project別のVolume path>',
      'descriptionElementTypes', '*'
    )
  ) AS parsed
FROM (
  SELECT file AS source_file
  FROM READ_FILES('<Volume上のPDF path>', format => 'file')
) AS f;
```

`READ_FILES(..., format => 'file')`の`file`列が`FILE`型です。その値を変換せず`ai_parse_document`へ渡します。D01が合格した後で残りを解析します。

登録済み文書の状態が`ERROR`になった場合、カタログの再解析APIは古いactive `PARSE_ONLY` runを閉じ、新しいrunを作って同じVolume上のPDFをこの`FILE`型経路へ再投入します。正常な文書の再解析はHTTP 409で拒否し、同じPDFを再アップロードしません。

```bash
python3 scripts/parse_documents.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

## 確認

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT COUNT(*) AS parsed_count, COUNT_IF(parsed:metadata:version::STRING = '2.0') AS schema_v2_count, COUNT_IF(to_json(parsed:error_status) IS NOT NULL AND to_json(parsed:error_status) <> 'null') AS parser_error_count, MIN(size(from_json(to_json(parsed:document:pages), 'ARRAY<VARIANT>'))) AS min_pages, MAX(size(from_json(to_json(parsed:document:pages), 'ARRAY<VARIANT>'))) AS max_pages FROM <UC_CATALOG>.rag_accuracy.toyota_parsed_v2"
```

合格条件:

- `parsed_count = 10`、`schema_v2_count = 10`である。
- `parser_error_count = 0`である。
- トヨタ評価用9冊は各5ページ、非車両PDF G01は3ページで、最小ページ数が3、最大ページ数が5である。
- `doc_uri`とregistryのProject／documentが一致する。
- 解析SQLと実行ログの両方で入力列が`FILE`型であり、`BINARY`を経由しない。
- `ERROR`文書だけを再解析でき、画面の多重クリックでも新しいparse runが1件だけ作られる。

## 失敗時の修正

- D01が失敗した場合は残り8冊へ進みません。
- `FILE`型が認識されない場合は、FILE typeをサポートするcomputeを使用します。Notebook computeを使う場合はDatabricks Runtime 18.0以上を確認します。
- `ai_parse_document`の利用可否、Preview設定、対応regionをWorkspace管理者に確認します。
- 100 MB超過、暗号化、破損PDFは対象文書だけを修正し、同じ`document_id`を不用意に別内容で上書きしません。
- 原因を直した登録済みPDFはカタログの「再解析」を使います。正常状態へ戻すためにregistryを直接UPDATEしません。
- `imageOutputPath`はProject／document別に分け、別PDFの画像を混ぜません。

## この環境の実測結果

`SUCCESS`。トヨタ評価用9冊は、Statement `<STATEMENT_ID>`で全冊schema `2.0`、各5ページ、parser error 0件を確認済みです。加えて非車両PDF G01をparse run `<RESOURCE_ID>`で解析し、schema `2.0`、3ページを確認しました。この10件が再現用E2Eの基準コーパスです。

asset `1.4.4`の削除検証後に取得した最新registryは22件で、lifecycleは`ACTIVE=18`／`DELETING=0`／`DELETED=4`です。確認Statementは`<STATEMENT_ID>`です。asset `1.4.3`のfresh削除helper直後に取得した`ACTIVE=19`／`DELETED=3`のStatement `<STATEMENT_ID>`は、過去snapshotとして保持します。parsed 20件、processing `ERROR=0`、論理削除済み2件の解析行保持も過去の20件snapshotであり、最新22件のparsed総数やprocessing内訳として扱いません。実行sourceは`READ_FILES(..., format => 'file').file`を直接`ai_parse_document`へ渡しています。

PDF単体をRAGの検索対象から論理削除しても、削除前の回答と評価の監査のため`toyota_parsed_v2`の解析行は物理削除しません。そのため、進行中コーパスの件数は`toyota_document_registry.lifecycle_status='ACTIVE'`で数え、parsed Tableの総行数と常に一致するとは限りません。

ローカルPDF QAでは9冊45ページをPopplerでrenderし、欠け、重なり、文字化けがないことを目視確認しました。D01〜D08は全ページに抽出可能なテキストがあり、D09は比較目的どおりテキスト層0、ページ画像5件でした。全9冊が5ページ、非暗号化です。

## 公式ドキュメント

- [`ai_parse_document`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_parse_document)
- [`FILE`型](https://docs.databricks.com/aws/en/sql/language-manual/data-types/file-type)
- [`read_files` Table-Valued Function](https://docs.databricks.com/aws/en/sql/language-manual/functions/read_files)
- [ファイルを`FILE`型として取り込む](https://docs.databricks.com/aws/en/ingestion/file)
