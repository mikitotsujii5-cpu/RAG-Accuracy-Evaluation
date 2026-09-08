# Step 8: MLflowとDatabricks Appを配置する

## 目的

MLflow 3へTraceと評価結果を記録するExperimentを準備し、任意分野のPDFを扱う4画面の「RAG精度評価アプリ」をDatabricks Appsへ配置します。

## 構築

この環境のMLflow Experiment:

| 項目 | 値 |
|---|---|
| path | `/Users/<DATABRICKS_USER_EMAIL>/<APP_NAME>/mlflow-experiment` |
| ID | `<MLFLOW_EXPERIMENT_ID>` |
| SQL Warehouse tag | `<SQL_WAREHOUSE_ID>` |

新規環境でだけAppを作ります。

```bash
databricks apps create <APP_NAME> \
  --json @deployment/app_create.json \
  --profile <DATABRICKS_CLI_PROFILE>

# App説明またはresourceを更新する場合
databricks apps update <APP_NAME> \
  --json @deployment/app_resources_update.json \
  --profile <DATABRICKS_CLI_PROFILE>

# user scopeも更新する場合
databricks apps update <APP_NAME> \
  --json @deployment/app_user_scopes_update.json \
  --profile <DATABRICKS_CLI_PROFILE>
```

2つの更新JSONは、汎用App説明、`model-serving`、7件のresourceをすべて含むfull-safe payloadです。`databricks apps update --description ...`だけを使うと既存resource bindingが外れる事象をこの環境で実測したため、説明／scopeの更新でも必ずfull payloadを送ります。

App SPへUnity Catalogの必要最小権限を付与します。

```bash
python3 scripts/execute_sql_file.py deployment/app_uc_grants.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

既存環境を汎用化する場合は、App sourceを同期する前にmigrationを実行します。

```bash
python3 scripts/execute_sql_file.py sql/07_migrate_generic_document_metadata.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>

python3 scripts/execute_sql_file.py sql/09_document_logical_deletion.sql \
  --profile <DATABRICKS_CLI_PROFILE> \
  --warehouse-id <SQL_WAREHOUSE_ID>
```

最初の出力の`missing_titles`、`null_tag_arrays`、`invalid_metadata_json`、`bad_schema_version`がすべて`0`、PDF削除migrationの`documents_without_lifecycle`、`variants_without_lifecycle`がともに`0`でなければ、同期・deployへ進みません。

Appは`requirements.txt`を使うpip方式です。Workspaceへ同期する際は、`tests/`、`.venv`、cache、`package.json`、`package-lock.json`、`pyproject.toml`、`uv.lock`を除外します。`package.json`と`package-lock.json`はローカルのChat UIテスト専用です。Python＋配布済み静的assetを起動する今回のApp sourceへ含めると、不要な`npm install`が実行されます。

```bash
databricks sync --full \
  --exclude tests \
  --exclude package.json \
  --exclude package-lock.json \
  --exclude pyproject.toml \
  --exclude uv.lock \
  --exclude .venv \
  app \
  /Workspace/Users/<DATABRICKS_USER_EMAIL>/<APP_NAME>/app \
  --profile <DATABRICKS_CLI_PROFILE>

databricks workspace list \
  /Workspace/Users/<DATABRICKS_USER_EMAIL>/<APP_NAME>/app \
  --profile <DATABRICKS_CLI_PROFILE> -o json
```

初回の新しい同期先で上の除外を使ってください。既存の同期先には、過去の同期で除外対象が残る場合があります。Workspace Browserまたは`databricks workspace list`で正確なpathを確認し、`package.json`と`package-lock.json`だけをデプロイ元から削除してから再同期します。Project rootやApp source全体を削除しません。同期後、7つの除外対象が存在しないことと、`requirements.txt`、`app.yaml`、Python source、`static/`が存在することを確認してからdeployします。

```bash
databricks apps deploy <APP_NAME> \
  --source-code-path /Workspace/Users/<DATABRICKS_USER_EMAIL>/<APP_NAME>/app \
  --mode SNAPSHOT \
  --no-wait \
  --profile <DATABRICKS_CLI_PROFILE>
```

実行commandは`app/app.yaml`の`python main.py`です。

### 画面とAPIの契約

- 全画面で同じProjectを使い、PDF、Index Variant、会話、評価Dataset、実行状態をProject単位で切り替えます。
- 右上のユーザー表示は`GET /api/me`を使います。Databricks Apps ingressの認証済みメールを優先し、必要時だけCurrent User APIへfallbackします。forwarded access tokenはApp状態、log、Trace、Tableへ保存しません。
- アップロードの任意項目は閉じたパネルにまとめ、PDFだけで送信できます。`ai_parse_document`完了後、FMAPIで20〜30字の概要を生成します。
- `ERROR`文書だけをカタログから再解析できます。古いactive parse runを閉じ、新しい`PARSE_ONLY` runで同じVolume上のPDFを`FILE`型経路へ再投入します。
- 画面上部の機能表示で、Unity Catalog Volume、`ai_parse_document`、Lakeflow Jobs、Delta Table、FMAPI Embeddings、AI Search、MLflow 3 Trace／Evaluation、Index Variantを、使う画面ごとに確認できます。
- カタログと引用は認可済みPDF content APIを使います。live citationには検証済み`document_id`と物理ページを含め、ブラウザはそこからPDF URLを作ります。`retrieval.completed` SSEと画面にはexcerpt／チャンク本文を含めません。
- PDF content APIはprocess内の短期cache、`ETag`／`If-None-Match`、byte `Range`、private browser cacheに対応します。画面はリンクのhover、focus、pointerdownでprefetchし、同じProject・PDF・pageの読み込み済みiframeを閉じた後も保持して再表示を高速化します。Project切替時は破棄します。
- Project削除はOWNERだけが実行でき、進行中処理があれば画面で止めます。削除後は通常APIから非表示にしますが、監査と復旧のため共有Delta Table、Volume、Indexの実データは管理領域へ保持します。
- PDF単体削除はOWNER／EDITORだけに許可します。カード／表の削除ボタンは確認画面、spinner、連打防止を持ちます。バックエンドはProject mutation lockでupload／Build／Chat／Evaluationとの競合を確認します。開始から30分を超えた孤児Chat runだけはguard付きで`ERROR`へ収束して対応assistant messageも整合し、30分以内のrun、Prep／Evaluation、別mutationは409で保護します。競合がなければ`202 Accepted`を返して影響Variantの後継Build／AI Search同期を開始します。
- PDF削除ではカタログと新規検索から対象を外し、旧Variantを`SUPERSEDED`にします。Volume原本、Document Parsing結果、過去の会話／引用／評価は保持し、過去の引用からはProject認可付きPDFを開けます。最後のPDFならProjectを`EMPTY`にし、空のIndexを作りません。
- 会話削除は本人の会話だけを対象にし、回答中なら停止を要求してHTTP 409を返します。停止完了後に再実行すると、message、run、sessionを子から順に削除します。
- 精度評価画面はProject／評価データ版／用途内の登録済み質問を初回全選択し、個別／一括選択、正解状態、期待回答、正解PDF／ページ、選択件数と最大試行数を表示します。質問追加フォームは初期状態で閉じ、0件では評価を開始できません。Phase 1〜5は全幅の横並びカードにし、狭い画面ではPhase領域を横スクロール、設定・工程・指標群を1列で表示します。
- trialの既定値は1です。開始ボタンは質問、Phase、既存Indexに対応するVariant、回答LLM、採点LLMがそろった場合だけ有効にします。Evaluation作成APIは`evaluation_case_ids`を1〜1000件で受け、Project・version・split所属を全件検証して`config_json`／`config_hash`へ固定します。Jobは選択IDだけを評価し、fieldを持たない旧runは同じ版・用途の全件を処理します。
- 開始要求中からspinnerと進捗カードを表示し、受付、Job起動、Phase評価、指標集計、改善提案、全体／Phase別の完了試行数、割合、経過時間を更新します。経過時間はDeltaのTIMESTAMP文字列をブラウザで解釈せず、status APIのタイムゾーン非依存な`elapsed_seconds`を基準に進め、terminal状態で固定します。Projectを再表示した場合はactive runを復元します。履歴からrunを開く最初のstatus GETは1回25秒でtimeoutし、一時的な失敗ではspinnerを保って最大3回再接続します。通常のpollも同じrunを監視し続けます。精度評価画面はProjectごとの最近の評価runも一覧表示し、過去runのPhase状態、指標、改善提案を再表示します。
- ブラウザは評価開始の応答が途切れた場合も、同じpayloadと`Idempotency-Key`で最大3回再送します。サーバーはProject、利用者、keyから同じ`eval_run_id`を決定し、Phase行を`MERGE`します。Lakeflow Jobも同じ`eval_run_id`をidempotency tokenに使うため、受付応答や`job_run_id`保存が失われても別run／別Jobを増やしません。
- 評価status APIは一つの論理runと一つのLakeflow runの対応を検証し、queue、compute、environment、task、terminal状態を安全な文言へ変換します。一時的なJob受付失敗では30〜300秒のbackoffと`retry_after_ms`を返し、画面はその間隔で監視します。Jobs APIの状態取得が一時失敗した`UNKNOWN`はspinner付きで再確認し、Job ID不正や権限・設定の確定的な拒否は`FAILED`へ収束します。JobがNotebook開始前に失敗／停止しても、評価行を終端状態へ収束します。
- 同一Phaseの同じ制御行が複数見えても、API、履歴、Evaluation JobはPhaseごとに1件へ集約し、進捗や試行数を水増ししません。固定設定が異なる重複行は拒否します。
- 停止APIはDelta Tableへ要求を保存すると同時にLakeflow Jobへ取消を依頼します。画面は停止専用の通信経路で最大3回再確認し、クリック直後にもstatusを更新します。Job登録と停止が競合した場合も同じ`eval_run_id`からJobを回復して取消します。status監視が先にterminal状態を確認した場合は、待機中の停止POSTをabortし、terminal状態と結果表示を維持します。すでに完了していれば409を返して完了状態を維持します。画面は終端状態まで監視します。Job linkは設定済みWorkspaceと一致するURLだけを表示します。
- terminal状態後の結果取得中もspinner、`aria-busy=true`、「結果取得中」、現在の試行番号を表示します。結果APIは1回45秒でtimeoutし、一時エラーを最大3回再試行します。取得に失敗してもrunを削除せず、評価履歴を更新して同じrunを選ぶと保存済み結果を再取得できます。
- `PREP_JOB_ID`と`EVAL_JOB_ID`は、先頭ゼロや空白を含まない正の整数としてApp起動時に検証します。
- 期待回答・期待事実がないケースは`answer_correctness=NULL`として平均から除外します。Phase advisorには検索指標だけでなく回答品質3指標とjudge rationaleを渡します。

## 確認

```bash
databricks experiments get-experiment <MLFLOW_EXPERIMENT_ID> \
  --profile <DATABRICKS_CLI_PROFILE> -o json

databricks apps get <APP_NAME> \
  --profile <DATABRICKS_CLI_PROFILE> -o json

databricks apps logs <APP_NAME> \
  --profile <DATABRICKS_CLI_PROFILE> --tail-lines 200
```

`apps get`が`SUCCEEDED`／`RUNNING`を返した後、短期tokenをshell変数だけに保持してhealthを確認できます。`set -x`を有効にせず、確認後すぐ変数を破棄します。

```bash
app_access_token=$(databricks auth token <DATABRICKS_CLI_PROFILE> -o json \
  | jq -r '.access_token')

curl -fsS \
  -H "Authorization: Bearer ${app_access_token}" \
  https://<APP_HOST>/api/health

unset app_access_token
```

App URL:

```text
https://<APP_HOST>
```

合格条件:

- deploymentが`SUCCEEDED`、Appが`RUNNING`、computeが`ACTIVE`である。
- build logに`Requirements installed successfully`とdeployment成功がある。
- `/api/health`がHTTP 200、`status=ok`、`databricks_ready=true`を返す。
- resource bindingは7件で、Warehouse、Volume、baseline Index、既定LLM、2 Job、MLflow Experimentを指す。
- App SPに親Catalog／Schema、必要Table、Volume、Indexの最小権限がある。
- `deployment/app_uc_grants.sql`の30 GRANT文がすべて成功し、PDF削除でAppが更新する`toyota_index_variants`にも`SELECT`／`MODIFY`がある。
- user authorizationのeffective scopeに`iam.current-user:read`、`iam.access-control:read`、`model-serving`がある。
- token、authorization headerをlog、Trace、Tableへ保存しない。
- `/api/me`と右上表示がサインイン中ユーザーのメールアドレスで一致する。
- アップロード画面で任意項目が初期状態で閉じており、PDFだけを送信でき、タイトルがファイル名から補完される。
- `category`、`tags`、`document_date`、`source`、追加メタデータを登録し、カタログで確認できる。
- 解析完了後、カタログに20〜30字のAI概要または安全なfallback概要が表示され、空欄のままにならない。
- Qwen3 Embedding 0.6Bが利用可能な環境ではベクトル化モデルの既定値になり、「推奨・日本語対応」と表示される。利用不可時の別fallbackへ「日本語対応」を誤表示しない。
- 非車両PDFからVariantを作り、チャット回答とProject認可済みPDF・ページ引用を確認できる。
- hover／focusによるPDF prefetch、`ETag`、`Range`を確認し、回答画面にチャンク本文が露出しない。
- `ERROR` PDFの再解析を1回だけ開始でき、正常文書では再解析を拒否する。
- retrieval SSEにexcerptがなく、live citationの`document_id`から同じProjectのPDF linkを開ける。
- OWNERのProject削除と本人の会話削除が確認付きで動き、実行中処理との競合を安全に拒否する。
- OWNER／EDITORはPDFを単体で確認付き削除でき、VIEWERの操作は403になる。実行中はspinnerとdisabled状態になり、同じDELETEの再送は後継Jobを重複作成しない。
- 開始から30分超の`QUEUED`／`STREAMING`／`CANCEL_REQUESTED` Chat runだけが削除前に`ERROR`へ収束し、対応する`STREAMING` assistant messageも`ERROR`になる。30分以内のrunは409を維持し、同時に完了した終端状態を上書きしない。
- PDF削除後、旧Variantが一覧／チャット／評価の選択肢から外れ、後継Variantが残存PDFだけでREADYになる。AI Search Indexに削除PDFのchunkがない。最後のPDFなら空表示とProject `EMPTY`を確認する。
- 削除前の回答のPDF引用が引き続き開け、過去の会話、評価結果、PDF原本、解析行が保持される。
- Projectに`starter-v1`のサンプル質問3件があり、正解ラベル未設定であることを画面から識別できる。
- 評価質問一覧の初期全選択、個別選択、すべて選択、選択解除、正解状態／詳細、選択件数／最大試行数が動き、0件では開始できない。
- APIへ渡した質問IDが1〜1000件で同じProject・version・splitに属し、Job結果がその選択IDだけである。IDのない旧runは全件評価できる。
- trialの初期値が1で、質問・Phase・Variant・回答LLM・採点LLMの不足時は開始できない。1問 × 5 Phaseでは最大5試行と表示される。
- 開始クリック直後からspinner、5工程、完了試行数、割合、経過時間、Phase別状態が表示される。狭い画面でもPhase、設定、主要指標が欠けずに操作できる。
- 同じ`Idempotency-Key`とpayloadを再送しても、評価run、Lakeflow Job、公開Phase行が重複しない。受付応答が失われても同じ`eval_run_id`から回復する。
- 再読込またはページ移動後にactiveな評価runが復元され、最初のstatus GETが25秒でtimeoutしても最大3回再接続し、通常pollの一時失敗後も監視を継続する。Notebook開始前のJob失敗／停止も`FAILED`／`CANCELED`へ確定する。
- status APIの`retry_after_ms`が画面の次回確認間隔へ反映され、`UNKNOWN` Job状態ではspinner付きの再確認表示になる。確定的なJob拒否は`FAILED`へ終了する。
- `評価を停止`で永続取消フラグとLakeflow Job取消の両方が実行され、停止APIの一時失敗やJob登録との競合後もterminal状態まで監視される。status監視が先にterminal状態を確認した場合は遅い停止POSTを中止し、結果表示を維持する。完了済みPhaseは停止へ上書きされない。
- Job終端後、指標と改善提案の取得が終わるまでspinnerと「結果取得中」を表示する。結果APIの45秒timeoutと最大3回再試行が動き、失敗後も評価履歴から同じrunを選び直して再取得できる。
- Job URLは同一Workspaceと検証できた場合だけ表示され、providerの生メッセージ、実ID、内部endpoint名を画面へ露出しない。
- 結果画面の主表示が検索再現率、回答正解率、回答時間で、詳細表に検索適合率、検索順位、根拠一致率、引用正解率、TTFT、p50／p95、token、cost、error rateがある。
- 未ラベルケースのAnswer Correctnessは`NULL`／`—`となり、0点として平均へ入らない。
- 評価履歴から過去runを選び、保存済み指標と改善提案を再表示できる。advisorの根拠に回答品質3指標とjudge rationaleが含まれる。
- 非車両Projectで正解付き評価質問を登録し、そのProjectの評価データ版・用途として選択できる。
- Metadata FilteringがProject内の`PARSED`／`READY`文書を照合し、検証済み`document_id`だけをAI Searchへ渡す。
- 同梱トヨタ評価シナリオの既存payloadとPhase 1〜5評価が後方互換で動く。

## 失敗時の修正

- `Error installing packages`は、App logの最初のdependency errorを確認します。依存管理方式を混在させず、今回は`requirements.txt`だけをデプロイ元へ置きます。`npm install`や`Exit handler never called!`が出た場合は、Workspace上の正確なApp sourceから`package.json`と`package-lock.json`だけを削除し、上記exclude付きで同期し直します。
- App説明／scope更新後にresourceが7件未満ならdeployしません。`deployment/app_resources_update.json`または`deployment/app_user_scopes_update.json`のfull-safe payloadを再適用し、`apps get`で7件を確認します。
- `/api/health`の`missing`にresourceが出る場合は、そのbindingまたは権限だけを直します。
- App resource上限へ達する場合は、無関係なresourceを増やさず、現在の7 bindingと個別UC grantsを維持します。
- 動的IndexはData Preparation Jobが作成後に、そのIndexだけApp SPへ`SELECT`を付与します。schema-wide future grantを使いません。
- registry列不足はApp codeで回避せず、Step 2のmigrationへ戻ります。
- 非車両PDF E2Eが失敗した場合、トヨタ評価の既存成功を代替根拠にしません。失敗した工程だけを修正し、同じProjectで登録から引用まで再実行します。
- 評価質問の登録で権限エラーになる場合は、`toyota_rag_eval_cases`にApp service principalの`SELECT`／`MODIFY`があるか確認します。この環境では漏れを検出し、付与後に実環境で再確認しました。
- PDF削除が5xxになる場合は、registryだけでなく`toyota_index_variants`の`SELECT`／`MODIFY`も確認します。Appは旧Variantを`SUPERSEDED`へ更新するため、`SELECT`だけでは足りません。この環境では不足によりHTTP 503となり、`MODIFY`付与後に削除E2Eを再実行して成功しました。
- メールが表示されない場合は、Appのuser authorization scopeとingress header／Current User API fallbackを確認します。token値そのものはlogへ出しません。
- 概要が空の場合は、registryの`summary_status`とbackground parseのlogを確認します。概要生成だけの失敗でPDF登録を失敗扱いにせず、fallback概要を保持します。
- PDF表示が遅い場合は、content APIの`Range`／`ETag`応答と、画面のprefetch linkを確認します。Volumeを公開URLへ変換しません。
- PDF削除が409の場合は、同じProjectのデータ準備、Chat、Evaluation、別の更新が進行中でないか確認します。競合するrunを完了または停止し、新しいPDF行を手動作成せず同じ削除を再実行します。
- 30分を大きく超えたChat runが削除を妨げる場合は、runの`started_at`と状態、対応assistant messageを確認します。現行sourceは削除判定時に孤児runをguard付きで`ERROR`へ収束します。時刻や状態を手動UPDATEせず、App logと条件付きUPDATEの結果を確認します。
- 選択した評価質問以外が実行された場合は、Eval runの`config_json`／`config_hash`と`evaluation_case_ids`を確認します。画面指定のcase IDをJob parameterとして直接信用せず、永続化済み設定のProject・version・split検証を修正します。
- 精度評価が`QUEUED`のまま変わらない場合は、`queue_reason`、`retry_after_ms`、評価Tableの`job_run_id`、対応するLakeflow Jobのlifecycle／result stateを確認します。`JOB_SUBMITTING`／`SUBMISSION_RETRY`中に新しいrunを作らず、同じ`eval_run_id`の回復を待ちます。Jobが終端ならstatus APIのreconcile権限と更新結果を直し、手動で成功へ書き換えません。
- `Jobの状態を再確認しています`が長時間続く場合はJobs API権限と接続を確認します。一時的な`UNKNOWN`を手動で`FAILED`へ変更しません。Job ID、設定、権限の確定的な拒否はAppが`FAILED`へ収束させます。
- 停止後もcomputeが動き続ける場合は、App SPの対象Evaluation Jobに対する`CAN MANAGE RUN`、cancel API、同じ`eval_run_id`の`job_run_id`回復を確認します。永続フラグを消さず、Job側checkpointと後続status pollの取消再試行も確認します。
- Jobは終わったが結果が表示されない場合は、画面の「結果取得中」と再試行番号、結果APIの応答を確認します。45秒timeoutを待たずに評価を再実行せず、最大3回失敗後は評価履歴を更新して同じrunを選び直します。
- PDF削除後の検索が準備できない場合は、返された`preparation_run_id`、Data Preparation Job、後継Variantのsource Table／Indexを確認します。旧`SUPERSEDED` Variantを手動でactiveに戻さず、削除PDFを含まない新しいVariantで回復します。

## この環境の実測結果

現行source asset `1.4.8`はPython 337件とUI 38件、合計375件の自動テストに合格しています。2026-09-09にGitHubの`main/app`から配置し、deployment `SUCCEEDED`、App `RUNNING`、compute `ACTIVE`、health version `1.4.8`／`databricks_ready=true`、resource binding 7件を確認しました。asset `1.4.7`で実行済みだったPhase 1・1問・1回runは、asset `1.4.8`のstatus／results APIで再表示できました。評価実行と再表示のassetを区別します。asset `1.4.5`の既存Index／Chat／Trace／PDF引用、asset `1.4.4`の評価質問選択／PDF削除、asset `1.4.1`の非車両G01は過去の検証履歴です。

| 項目 | 実測 |
|---|---|
| App | `<APP_NAME>`、汎用RAGの説明文 |
| Deployment | GitHub `main/app`のasset `1.4.8`、`SUCCEEDED`。実IDは非掲載 |
| App／compute | `RUNNING`／`ACTIVE` |
| Health | version `1.4.8`、`databricks_ready=true` |
| Resources／scope | binding 7件、`iam.access-control:read`、`iam.current-user:read`、`model-serving` |
| ログインユーザー | `/api/me` HTTP 200、`<DATABRICKS_USER_EMAIL>` |
| App SP権限 | grant 30文成功。`toyota_index_variants`の`MODIFY`はStatement `<STATEMENT_ID>`、`SELECT`＋`MODIFY`の確認は`<STATEMENT_ID>` |
| Source test | asset `1.4.8`、Python 337件、UI 38件、合計375件、差分check成功 |
| App起動 | deployment `SUCCEEDED`、App `RUNNING`、compute `ACTIVE`。旧deploymentのnpm失敗履歴は現行Appと分けて記録 |
| 評価status／results | asset `1.4.7`で実行済みのPhase 1・1問・1回runをasset `1.4.8`のremote APIで取得。`SUCCEEDED`、1／1、774秒、指標1件、改善提案1件 |
| 評価結果 | Recall／Correctness／Groundedness／Citation各1.0、error rate 0、p50 5,479 ms。Lakeflow run total 777.125秒 |
| 評価画面 | `elapsed_seconds=774`を「12分54秒」と表示し、3秒後も固定。Phase横並びと結果画面はローカルSSO代替画面で目視確認 |
| PDF概要 | 20件snapshotで空欄0件、20〜30字違反0件。`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、`USER=1`。最新registry 22件全体の監査値ではない |
| PDF content API | 通常取得200、byte Range 206、ETag再検証304、`private, max-age=300` |
| App SP | client `<APP_SERVICE_PRINCIPAL_ID>`、numeric `<APP_SERVICE_PRINCIPAL_NUMERIC_ID>` |
| MLflow Experiment | `<MLFLOW_EXPERIMENT_ID>`／active |

旧asset `1.4.1`で実行した非車両Projectのend-to-end履歴:

| 項目 | 実測 |
|---|---|
| Project | `<RESOURCE_ID>`／`情報セキュリティ規程RAG-20260907-040609` |
| PDF／解析 | document `<RESOURCE_ID>`、parse run `<RESOURCE_ID>`、3ページ、タイトル自動補完、汎用metadata保持、旧車両値NULL |
| 検索データ | prep `<RESOURCE_ID>`、Job `<DATABRICKS_RESOURCE_ID>`、Semantic／512 Variant `<RESOURCE_ID>` |
| Index | `<UC_CATALOG>.rag_accuracy.toyota_chunks_v_<RESOURCE_ID>_index`、READY、source／Index各6行、別Project行0 |
| Chat | session `<RESOURCE_ID>`、request `<RESOURCE_ID>`、期待回答`30分`と一致、引用1件、PDF／Trace link成功 |
| Chat Trace | `<TRACE_ID>` |
| Chat controls | 旧asset `1.4.1`のremote E2Eで同一requestの保存がuser／assistant各1件、停止後`run.cancelled`／`CANCELLED`を確認。cancel IDはGit外の運用台帳で管理 |
| 評価 | case `<RESOURCE_ID>`、run `<RESOURCE_ID>`、Dataset `security-policy-v1` |
| Phase 1〜5 | 5結果、エラー0、Correctness／Groundedness／Citation Correctness各5件、Trace 5件、改善提案5件 |

旧asset `1.4.1`で開始した未ラベルstarter質問だけのeval run `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`も`SUCCESS`です。Phase 1、trial 3、error rate 0、Answer Correctness `NULL`、Groundedness `0.66667`、Citation Correctness `1.0`、LLM改善提案1件を確認しました。

旧asset `1.4.1`のトヨタ互換回帰も、session `<RESOURCE_ID>`、request `<RESOURCE_ID>`で期待回答`60`と一致しました。Trace `<TRACE_ID>`、引用2件も確認済みです。

App service principalに`toyota_rag_eval_cases`の`SELECT`／`MODIFY`がないことを旧E2Eで検出し、権限を追加して評価を再実行しました。PDF削除E2Eでは、`toyota_index_variants`の`MODIFY`不足によりHTTP 503となることを検出しました。Appが旧Variantを`SUPERSEDED`へ更新するために必要な`MODIFY`を付与し、`SELECT`と合わせて確認してから、同じ新deploymentでPDF削除を再実行しました。`deployment/app_uc_grants.sql`の30 GRANT文はすべて成功しています。さらに、`apps update --description`だけの更新で7件のresourceが外れる事象を検出したため、full-safe payloadを再適用して7件へ復旧しています。

asset `1.4.4`では、既存Project `<RESOURCE_ID>`に残った30分超の孤児Chat run 2件がPDF削除を妨げる状態を再現しました。現行deploymentへのDELETEはdocument `<RESOURCE_ID>`にHTTP 202を返し、deletion request `<RESOURCE_ID>`を作成してrun 2件と対応assistant messageを`ERROR`へ整合しました。影響旧Variantは4件、残存PDFはdocument `<RESOURCE_ID>`の1件です。

後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`はREADY、source 8行、削除0／保持8行です。後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`／Variant `<RESOURCE_ID>`もREADY、source 4行、削除0／保持4行です。両AI Search実検索は保持documentだけを返し、削除documentは0件でした。SQL Warehouseで回復UPDATEのno-op構文検証も成功しています。

選択評価run `<RESOURCE_ID>`はcase `figure-001`だけを固定し、Job `<DATABRICKS_RESOURCE_ID>`／task `<DATABRICKS_RESOURCE_ID>`が`TERMINATED`／`SUCCESS`、結果1行、選択外0行、error 0で完了しました。MLflow runは`<RESOURCE_ID>`、確認Statementは`<STATEMENT_ID>`です。

asset `1.4.3`の2件→1件削除E2Eは、新しいProject `<RESOURCE_ID>`で実行しました。初期Variant `<RESOURCE_ID>`からdocument `<RESOURCE_ID>`をDELETE `202`で論理削除し、残存document `<RESOURCE_ID>`だけの後継Variant `<RESOURCE_ID>`を作成しました。後継prep `<RESOURCE_ID>`／Job `<DATABRICKS_RESOURCE_ID>`は`SUCCEEDED`で、Indexは`READY`、source／Index各6行、削除PDF行／検索hit 0、保持PDF行6／検索hit 1です。同じDELETEの再送、削除前引用、後継回答、PDF原本、解析行の保持も確認しました。最後の1件→0件は別Project `<RESOURCE_ID>`だけをEMPTY検証に使い、active Variantなし、READY Variant 0件、prep run数4→4、空の後継Indexなしを確認しました。これは現行assetの証跡ではなく、過去のasset `1.4.3`検証履歴です。

次は今回未実施のため`PENDING`です。

- Microsoft Entra IDへサインイン済みブラウザによるデプロイ済み4画面の手操作。
- ストリーミング性能runによるserver／client TTFT。
- Standard／256とSemantic／512を除く残り7種類のchunk profile。

## 公式ドキュメント

- [Databricks Appsのデプロイ](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
- [Databricks Appsの依存関係](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/dependencies)
- [Databricks Apps resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/apps-resource)
- [Databricks Appsの認証と認可](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
- [MLflow 3 Tracing](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/)
