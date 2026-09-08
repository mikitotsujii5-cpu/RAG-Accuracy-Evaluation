# Step 6: AI Searchを作成して検索を確認する

## 目的

Baseline Delta TableをVector Search／Hybrid Searchで検索できるようにします。IndexはDelta Sync、HYBRID、TRIGGEREDで作ります。

## 構築

同名リソースが存在しないことを先に確認します。

```bash
databricks vector-search-endpoints get-endpoint toyota-rag-search \
  --profile <DATABRICKS_CLI_PROFILE> -o json
```

新規環境でだけ、次を作成します。既存の同名endpoint／Indexがある場合は重複作成せず、設定を比較してください。

このリポジトリでは、作成用request bodyをそのままREST APIへ渡します。

```bash
databricks api post /api/2.0/vector-search/endpoints \
  --json @deployment/ai_search_endpoint.json \
  --profile <DATABRICKS_CLI_PROFILE>

databricks api post /api/2.0/vector-search/indexes \
  --json @deployment/ai_search_index.json \
  --profile <DATABRICKS_CLI_PROFILE>
```

request bodyは[endpoint JSON](../../deployment/ai_search_endpoint.json)と[Index JSON](../../deployment/ai_search_index.json)です。UIやSDKで作成する場合も、作成後の実設定を同じ確認コマンドで検証します。

## 確認

```bash
databricks vector-search-endpoints get-endpoint toyota-rag-search \
  --profile <DATABRICKS_CLI_PROFILE> -o json

databricks vector-search-indexes get-index \
  <UC_CATALOG>.rag_accuracy.toyota_chunks_standard_512_v1_index \
  --profile <DATABRICKS_CLI_PROFILE> -o json
```

```bash
python3 scripts/wait_for_vector_index.py \
  --profile <DATABRICKS_CLI_PROFILE> \
  --index-name <UC_CATALOG>.rag_accuracy.toyota_chunks_standard_512_v1_index \
  --expected-rows <共有source-tableの確認済み総行数>

app/.venv/bin/python scripts/smoke_test_ai_search.py --profile <DATABRICKS_CLI_PROFILE>
```

合格条件:

- endpointは`STANDARD`かつ`ONLINE`である。
- Indexは`DELTA_SYNC`、subtype `HYBRID`、pipeline `TRIGGERED`である。
- sourceは`toyota_chunks_standard_512_v1`、Embedding列は`chunk_to_embed`である。
- `ready=true`で、`indexed_row_count`が共有source Tableの総行数と一致する。fresh bootstrapでbaseline論理sliceしかない時点に限り40行である。
- ANN、HYBRID、metadata filter、Rerankingの各smoke queryが結果を返す。
- Index GET応答で`columns_to_sync`が省略されても、全source列を同期する応答としてsource schemaと検索manifestから必要列を確認できる。`columns_to_sync`または`columns_to_index`が明示された場合は、必要列と完全一致する。
- 登録済みProfileの共有source／Indexには複数Project・Variantの行を格納できる。選択した`project_id`＋`variant_id`で絞ったsource件数とIndex件数が一致し、検索結果へ別Project／別Variantの行が混入しない。
- PDF単体の論理削除後は、削除PDFを含む旧論理Variantを新しい検索に解決しない。後継論理Variantは同じ登録済みProfileを再利用し、source／Indexを`project_id`＋`variant_id`で絞った削除PDF行数がともに0である。

## 失敗時の修正

- 行数不一致は共有Profile全体のsource／Index総数と、対象`project_id`＋`variant_id`の論理slice件数を分け、Change Data Feed、最新pipeline syncの順で確認します。
- Index作成中は再作成せず、readyになるまで待ちます。
- Embedding modelまたはdimensionを変える場合は、管理者が別のDelta Table／AI Search Indexを手動作成し、App／Job両方の許可リストへ新しいProfileとして登録します。同じProfile内の後継論理Variantは既存リソースを再利用します。
- filter列や返却列が不足する場合は、ProfileのIndex作成requestに相当する`columns_to_sync`とsource schemaを確認し、管理者がProfileの物理リソースを修正または再作成してから登録内容を更新します。App／Jobから新しい物理Indexを作って回避しません。Index GET応答は全列同期時に`columns_to_sync`を返さない場合があるため、省略だけでIndex不正と判断しません。一方、GET応答に`columns_to_sync`または`columns_to_index`がある場合は、不足、余分、重複がないことを確認します。
- PDF削除後の同期がREADYにならない場合は、返された`preparation_run_id`、Data Preparation Job、登録済みProfileのsource TableのCDF、pipeline syncを確認します。削除PDFを含む旧`SUPERSEDED`論理Variantをactiveに戻しません。

## この環境の実測結果

`SUCCESS`。

- endpoint `toyota-rag-search`: `STANDARD`／`ONLINE`
- baseline Index: `ready=true`。bootstrap時は40行で、現在もbaseline論理sliceは40行。共有物理Index全体には別Project／別Variantの行が存在できる
- 2026-09-06 smoke: ANN、HYBRID、metadata filter、Rerankingがすべて成功
- smoke時のReranking処理時間: 588 ms。この値は一回の観測であり、性能評価の代表値ではありません。
- 以下のSemantic／512と後継Indexは、物理Indexを動的作成していた旧方式の検証履歴です。現行Appで利用できるProfileを示すものではありません。
- 旧方式の非車両Project Index `<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`: Semantic／512、`READY`、source 6行、Index 6行、別Project行0
- 同Indexを使ったremote Chat: 期待回答`30分`と一致、引用1件、認可済みPDF linkとTrace linkを確認
- asset `1.4.4`のPDF単体削除E2Eでは、Project `<RESOURCE_ID>`からdocument `<RESOURCE_ID>`を論理削除し、document `<RESOURCE_ID>`を保持
- 旧方式の後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`／Index `<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`: `READY`、source 8行、削除PDF行0、保持PDF行8、削除PDFの検索hit 0
- 旧方式の後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`／Index `<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`: `READY`、source 4行、削除PDF行0、保持PDF行4、削除PDFの検索hit 0
- 両方の旧方式の後継Indexを実検索し、保持documentだけが返ることを確認
- asset `1.4.3`のPDF単体削除E2Eでは、後継Variant `<RESOURCE_ID>`／Index `<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`が`READY`、source／Index各6行、削除PDF行／検索hit 0、保持PDF行6／検索hit 1でした。旧Variant `<RESOURCE_ID>`は`SUPERSEDED`です。これは過去のasset `1.4.3`検証履歴です。

## 公式ドキュメント

- [AI Search Indexを作成する](https://docs.databricks.com/aws/en/ai-search/create-ai-search)
- [AI Searchへqueryする](https://docs.databricks.com/aws/en/ai-search/query-ai-search)
- [AI Searchの検索品質ガイド](https://docs.databricks.com/aws/en/ai-search/retrieval-quality)
