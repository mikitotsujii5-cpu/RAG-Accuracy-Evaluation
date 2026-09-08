# Step 5: 評価データと検索チャンクを作る

## 目的

Phase 1〜5へ同じ質問を渡せるProject別の評価Datasetと、最初の比較に使うStandard／512チャンクを作ります。同梱SQLはトヨタ評価シナリオ用です。任意分野のProjectでは、そのPDFに対応する正解付き質問をアプリから別versionとして登録します。

## 構築

```bash
python3 scripts/execute_sql_file.py sql/05_seed_evaluation.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>

python3 scripts/execute_sql_file.py sql/08_seed_starter_evaluation.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>

python3 scripts/execute_sql_file.py sql/06_build_baseline_chunks.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

上の評価SQLはD01〜D08用の互換seedです。任意分野のProjectでは、PDFを解析した後に「RAG精度評価」画面で次を登録します。

1. 質問と任意の期待回答を入力する。
2. 同じProjectの`PARSED`／`READY` PDFを正解として選ぶ。
3. PDFの1始まりの正解ページを指定する。
4. 評価データ版と`development`／`holdout`用途を指定する。
5. 登録後、選択した版・用途の一覧に質問が表示され、今回の評価対象として選択されることを確認する。

同じ版・用途では同じ質問を重複登録しません。PDF、質問、期待回答、正解ページを変更した場合は評価データ版を更新します。これにより、過去runの評価条件を上書きしません。

評価画面は、選択中のProject・評価データ版・用途に属する質問を一覧にし、初回は全件を選択します。質問ごとのcheckbox、「すべて選択」、「選択を解除」で今回使う質問を絞れます。各行では正解登録状態を表示し、「正解を確認」を開くと期待回答、正解PDF、正解ページ、Project認可済みPDFリンクを確認できます。質問追加フォームは初期状態で閉じています。

`sql/08_seed_starter_evaluation.sql`は、解析済みPDFがある既存Projectへ、次の3種類の質問を`starter-v1`／`development`として冪等に登録します。

1. 文書の概要
2. 重要なポイント3つ
3. 主な手順や条件

これは画面をすぐ試すためのサンプルです。正解を捏造しないため、期待回答、正解ページ、relevance judgmentは空のままです。期待回答・期待事実がないケースは`answer_correctness=NULL`とし、平均から除外します。0点へ変換しません。ページ正解がない場合のRetrieval指標も判定対象外です。正式評価へ使う前に、人が正解を付けた評価ケースを追加します。新しいProjectでは、最初のData Preparation Jobが成功したときにも同じ3件を作ります。

このbaselineは再現性を優先した比較用チャンクです。`ai_prep_search`は意味チャンクと文書コンテキストを作る選択肢ですが、256／512／1024を`ai_prep_search`の直接引数だとは扱いません。通常構成で登録されているのはStandard／512 Profileだけです。サイズや手法を比較するときは、管理者が別のDelta Table／AI Search Indexを手動作成し、App／Job両方の許可リストへIndex Profileとして登録します。Data Preparation Jobは物理Table／Indexを作成しません。

汎用化後のData Preparation Jobは、次を選択済みProfile内の各論理Variantへ引き継ぎます。

- 本文、タイトル、URI、物理ページ、Project、文書ID
- `category`、`tags`、`document_date`、`source`、`metadata_json`
- 後方互換の`model`、`model_year`、`document_type`、`vehicle_category`

非車両PDFはトヨタbaselineの論理sliceへ混ぜず、同じ登録済みProfileの共有source Delta Tableへ別の論理Variantとして保存します。行は`project_id`＋`variant_id`で分離し、既存の論理Variantを上書きしないため、評価結果を後から再現できます。

各論理Variantには、作成時の`source_document_ids`も保存します。これはPDF単体の論理削除で、どのVariantが影響を受けるか、どのPDFだけを残した後継Variantを作るかを安全に決めるlineageです。過去のVariantでこの列がない場合は、許可済みProfileのsource Tableを`project_id`＋`variant_id`で絞って`document_id`を確認します。lineageを証明できないactive Variantがあるときは、誤ったデータを検索に残さないためPDF削除を409で拒否します。

## 確認

評価Dataset:

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT dataset_version, dataset_split, COUNT(*) AS case_count, COUNT_IF(is_answerable) AS answerable_count FROM <UC_CATALOG>.rag_accuracy.toyota_rag_eval_cases GROUP BY dataset_version, dataset_split ORDER BY dataset_version, dataset_split"
```

Baselineチャンク:

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SELECT COUNT(*) AS chunk_count, COUNT(DISTINCT chunk_id) AS unique_chunk_count, COUNT(DISTINCT document_id) AS document_count FROM <UC_CATALOG>.rag_accuracy.toyota_chunks_standard_512_v1 WHERE project_id = '<BASELINE_PROJECT_ID>' AND variant_id = 'baseline-standard-512-v1'"
```

Change Data Feed:

```bash
python3 scripts/query_sql.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID> \
  --statement "SHOW TBLPROPERTIES <UC_CATALOG>.rag_accuracy.toyota_chunks_standard_512_v1 ('delta.enableChangeDataFeed')"
```

合格条件:

- Dataset versionは`v1.0.0`で、development 12件、holdout 4件である。
- 非車両ProjectではDataset version `security-policy-v1`に評価質問1件がある。
- 任意分野の評価質問は作成したProjectにだけ属し、正解PDF URIと物理ページがそのProjectのregistryと一致する。
- 評価画面では、現在のProjectに存在する評価データ版・用途だけを選択できる。
- 同じProject・評価データ版・用途の質問は初回に全選択され、個別／一括の選択・解除ができる。選択件数と`質問数 × Phase数 × trial数`の最大試行数が一致し、0件では開始できない。
- Evaluation作成APIは選択した`evaluation_case_ids`を1〜1000件で受け取り、別Project・別version・別splitのID、空ID、重複IDを拒否する。受け付けたIDは`config_json`／`config_hash`へ固定する。
- 解析済みPDFを持つProjectでは`starter-v1`／`development`のサンプル質問が3件あり、期待回答と正解ページを捏造していない。
- 未ラベルのサンプル質問はAnswer Correctnessの分母へ入らず、画面では`—`として表示される。
- 答えられない質問にはqrelがないことを許し、それ以外のqrelはbaseline ProjectのURIと物理ページを指す。
- `project_id='<BASELINE_PROJECT_ID>'`かつ`variant_id='baseline-standard-512-v1'`のbaseline論理sliceは40チャンク、`chunk_id`は40件すべて一意、文書はD01〜D08の8冊だけである。共有Table全体には別Project／別Variantの行が存在してよい。
- Delta Change Data Feedが`true`である。
- UIとqrelsのページは、`ai_parse_document`の0始まり`page_id`に1を足した物理ページ番号である。
- 登録済みProfileのsource schemaに`category`、`tags`、`document_date`、`source`、`metadata_json`があり、新しい論理Variantへ値を引き継げる。
- 選択した論理Variantについて、sourceとIndexの`project_id`＋`variant_id`で絞った件数が一致し、別Project／別Variantの行が検索結果へ混入しない。

## Metadata Filteringの安全な流れ

Phase 3〜5では、質問から得た文字列を任意のAI Search JSON pathへ直接渡しません。

1. 現在のProjectで`PARSED`または`READY`の文書registryだけを読む。
2. `category`、`tags`、`document_date`、`source`、任意key-valueを照合する。
3. 任意key-valueは、質問にkeyとvalueの両方がある場合だけ採用する。
4. 検証済みの文書IDを`document_id IN (...)`としてAI Searchへ渡す。
5. 矛盾、0件、全件一致、100件超など安全に絞れない場合はフィルタなしへ戻す。

この方式により、Project外文書の混入と、未検証の任意JSON pathによる過剰な絞り込みを避けます。

## 失敗時の修正

- qrelのURIがregistryと一致しない場合は、トヨタ互換seedでは先にregistryを直してから評価seedを再実行します。画面登録では、同じProjectの解析済みPDFを選び直します。
- PDF内容を変更した場合は同じDataset versionを使い回さず、文書version／hashとDataset versionを上げます。
- baseline論理sliceへD09または別Projectが入った場合は、そのsliceを同期する前にチャンク作成条件を修正します。
- Change Data Feedが無効の場合は、登録済みProfileの既存source Delta Tableで有効化してから同期します。
- 登録済みProfileに汎用列がない場合は、Step 2のmigration、Profileのsource schema、最新のData Preparation Job sourceを確認します。Jobから別schemaの物理Tableを動的作成して回避しません。
- `source_document_ids`が保存されない場合は、Step 2のPDF論理削除migrationと最新のData Preparation Jobを確認します。利用者がブラウザからsource Table名やlineageを指定できる仕様に変更しません。
- 質問一覧が空の場合は、画面上部のProject、評価データ版、用途の組み合わせを確認します。別Projectのcase IDを直接送って回避せず、正しいProjectへ評価質問を登録します。

## この環境の実測結果

既存トヨタ評価baselineは引き続き`SUCCESS`です。評価Datasetは16件（development 12、holdout 4）、`project_id='<BASELINE_PROJECT_ID>'`かつ`variant_id='baseline-standard-512-v1'`の論理sliceは40チャンク／8文書、Change Data Feedは`true`です。共有Table全体の件数ではありません。1件は意図的なunanswerable、1件は曖昧質問のため、すべてのケースに単一の正解ページがあるとは限りません。

非車両Projectでも`SUCCESS`を確認しました。評価ケース `<RESOURCE_ID>`をDataset `security-policy-v1`へ画面登録しました。固定評価ケースはトヨタbaseline 16件と非車両Project 1件で、これとは別に、解析済み文書を持つ各Projectへ未ラベルの`starter-v1`質問を3件ずつ登録しています。旧動的Index方式ではG01からSemantic／512 Variant `<RESOURCE_ID>`を作成し、汎用列を持つsource 6行、Index 6行、別Project行0を確認しました。この記録は現行UIでSemantic／512 Profileが利用可能であることを示しません。評価run `<RESOURCE_ID>`はPhase 1〜5の5結果、エラー0、Correctness／Groundedness／Citation各5件、Trace 5件、改善提案5件です。

## 公式ドキュメント

- [`ai_prep_search`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_prep_search)
- [AI Searchの検索品質ガイド](https://docs.databricks.com/aws/en/ai-search/retrieval-quality)
- [Delta LakeのChange Data Feed](https://docs.databricks.com/aws/en/delta/delta-change-data-feed)
