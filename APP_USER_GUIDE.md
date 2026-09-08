# 「RAG精度評価アプリ」操作ガイド

このガイドは、Databricksを初めて使う方が、任意分野のPDF登録からRAGのPhase比較までを迷わず操作するための説明です。

対象URL: `<APP_URL>`

対象Workspaceは`field-eng-east`（ID `<WORKSPACE_ID>`）です。Databricksへサインインし、このAppの利用権限を持つアカウントで開いてください。

> [!NOTE]
> 現行sourceはasset version `1.4.4`です。deployment `<DEPLOYMENT_ID>`は2026-09-08に`SUCCEEDED`となり、App `RUNNING`、compute `ACTIVE`、health HTTP 200、resource binding 7件を確認しました。Python 264件とChat UI 21件（合計285件）の自動テスト、Python compile 51ファイル、JavaScript構文、JSON検証、およびPDF単体削除の実Workspace E2Eも確認しています。ストリーミングTTFT、未実施のchunk profile、SSO済みブラウザ4画面の手操作は、確認が終わるまで`PENDING`です。

> [!CAUTION]
> 付属のトヨタ車種関連PDFはすべて架空の評価データです。内容を実車の操作、整備、救助、購入判断に使わないでください。これらは同梱シナリオであり、アプリは車両以外のPDFにも使えます。

## 1. Projectを選ぶ

Projectは、同じ目的で使うPDF、検索Index、会話履歴、評価Dataset、評価結果をまとめる箱です。画面上部の`Project`選択は、4つのページすべてに共通です。

初回は次の手順で作成します。

1. 「＋ プロジェクトを作成」を押す。
2. Project名と説明を入力する。
3. 「プロジェクトを作成」を押す。
4. 画面上部に作成したProject名が表示されたことを確認する。

別のデータ群を扱うときは、新しいProjectを作ります。PDF、Variant、会話、評価結果を別Projectへ混ぜないでください。

同梱データの推奨構成は次のとおりです。まず非車両文書で汎用動作を確認し、その後にトヨタ評価シナリオを使ってPhaseを比較できます。

| Project | 登録するPDF | 目的 |
|---|---|---|
| 情報セキュリティ規程 | `generic_information_security_policy_demo.pdf` | 任意分野のPDF登録、検索、引用の確認 |
| Toyota baseline（互換例） | D01〜D08 | 同梱データでPhase 1〜5とチャンク手法を再現 |
| Toyota scan parse比較（互換例） | D09 | D05とのDocument Parsing比較 |

D05とD09は同じ内容です。同じ検索Indexへ同時登録すると重複検索になり、評価が歪むため分離します。

画面右上には、Databricks Appsへログインしている利用者のメールアドレスが表示されます。各ページ上部の「Databricks構成」または「RAG実行」「評価基盤」には、その画面で使う機能名が表示されます。たとえばUnity Catalog Volume、`ai_parse_document`、Lakeflow Jobs、Delta Table、FMAPI、AI Search、MLflow 3、Index Variantです。

Projectを削除するときは、サイドバー下部の「プロジェクトを削除」を押します。OWNERだけが実行でき、確認画面で確定するまで削除されません。データ準備、回答、評価の実行中は、先に完了または停止してください。削除後は通常の画面とAPIから見えなくなりますが、監査・復旧のためPDF、評価結果、共有Table／Indexの実データは管理領域に保持されます。

## 2. サイドバー1「データ準備」

### 2.1 PDFを登録する

1. PDFをドロップするか、アップロード領域を押してファイルを選ぶ。
2. 必要な場合だけ「文書情報を追加」を開き、タイトル、カテゴリ、タグ、文書日付、ソース、追加メタデータを入力する。パネルは最初は閉じており、すべて任意。
3. 「アップロードして解析」を押す。
4. 右側の「RAG検索データの作成状況」でPDF保存と文書解析を確認する。

必須項目はPDFだけです。タイトルを空欄にすると、拡張子を除いたPDFファイル名がタイトルになります。たとえば`情報セキュリティ規程.pdf`は「情報セキュリティ規程」として登録されます。概要を入力しなくても、解析後にFMAPIのLLMが日本語20〜30字の概要を自動生成します。LLMが一時的に使えない場合は、タイトル／ファイル名から同じ長さの概要を作るため、カタログを空欄のままにしません。

任意メタデータの入力上限:

| 項目 | 上限・形式 |
|---|---|
| タイトル | 300文字 |
| カテゴリ | 100文字 |
| タグ | 最大20件、各80文字。画面ではカンマ区切り |
| 文書日付 | `YYYY-MM-DD` |
| ソース | 500文字 |
| 追加メタデータ | 最大20件。項目名80文字、値1000文字 |

追加メタデータでは、共通項目の`title`、`category`、`tags`、`document_date`、`source`と、システム項目の`document_id`、`project_id`、`doc_uri`を項目名に使えません。大文字・小文字だけが違う重複タグや重複項目も登録できません。`model`、`model_year`、`document_type`、`vehicle_category`という名前は汎用の追加項目として使用できます。旧トヨタ互換APIの同名トップレベル項目とは別領域に保存されます。

同梱の非車両PDFには、次の値を入力するとMetadata Filteringも確認しやすくなります。

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

アップロード後、アプリはPDFをProject別のUnity Catalog Volumeへ保存します。その後、裏側で次の経路によりDocument Parsingを行います。

```text
Volume上のPDF
  -> READ_FILES(..., format => 'file')のFILE列
  -> ai_parse_document(version = 2.0)
  -> 解析済みDelta Table
```

`BINARY`列は使用しません。解析は非同期です。別ページへ移動しても処理は続きます。

状態の見方:

| 状態 | 意味 | 次の操作 |
|---|---|---|
| `PARSING` | 解析中 | 完了を待つ |
| `PARSED` | 解析完了 | RAG検索データを作成できる |
| `READY` | 検索準備まで完了 | チャットと評価で使用できる |
| `ERROR`／`FAILED` | 処理失敗 | 原因を直した後、PDFカタログの「再解析」を押す。解決しない場合は管理者へrun IDを伝える |

### 2.2 検索する文書のチャンク化・ベクトル化を設定する

チャンクは、検索するときの文章の単位です。条件を変えるたびに新しいVariantを作るため、過去の比較結果を再現できます。

チャンクサイズ:

- `256`: 短く、ピンポイントな検索向け。断片化しやすい。
- `512`: 精度と文脈のバランスを取りやすい開始点。
- `1024`: 広い文脈を保持しやすい。不要な文章も入りやすい。

画面の「分割方法」:

- 「均等に分割」（Standard）: 一定のtoken上限で安定して分割する。
- 「意味ごとに分割」（Semantic）: 見出しや文の境界を優先する。
- 「親子で分割」（Parent-child）: 短い文章を検索し、前後の広い文脈も回答に渡す。

「ベクトル化モデル」の一覧には、FMAPIで発見したEmbedding候補のうち、このワークスペースで`READY`、利用可能、選択可と確認できたモデルが表示されます。日本語を含む多言語検索に対応するQwen3 Embedding 0.6Bが利用できる場合は、これを既定値として選び、「推奨・日本語対応」と表示します。利用できない場合は検証済みの別モデルへ自動的に切り替えます。fallback先へ「推奨」と表示する場合でも、Qwen3以外のモデルへ「日本語対応」を誤表示しません。モデルを変えると別Indexになるため、同じVariantを上書きしません。

追加スイッチ:

- 不要な文字を除去: 繰り返すヘッダー、フッター、重複を除く。
- 概要・見出しを追加: 文書情報をEmbedding用テキストへ加える。
- 表・図・レイアウトを保持: Document Parsingの構造を検索用チャンクへ反映する。

### 2.3 RAG検索データを作る

1. 対象PDFの解析が`PARSED`または`READY`であることを確認する。
2. 「対象PDF」で、この検索データに含める解析済みPDFを選ぶ。
3. 文章の長さ、分割方法、ベクトル化モデル、追加スイッチを選ぶ。
4. 「RAG検索データを作成」を押す。
5. 画面右の「RAG検索データの作成状況」が完了するまで待つ。
6. 「作成済みの検索データ」に`READY`のデータが表示されることを確認する。

最初は「均等に分割 / 512 / Qwen3 Embedding 0.6B」を推奨します。Qwen3が利用不可の場合だけ、画面で選択されている別の利用可能モデルを使います。比較するときは、一度に一つの条件だけを変えます。

「RAG検索データを作成」の監視は次のように動きます。

- 実行中はボタンが無効になり、同じ設定のJobを重複投入しません。
- Projectごとに監視は1つだけです。別ページや別Projectへ移動して戻った場合や、画面を再読込みした場合も、実行中runを復元します。
- 監視に固定の終了時間はありません。Lakeflow Jobが終端状態になるまで確認を続けます。
- `QUEUED`でも、先行Job待ち、コンピュート起動中、Job登録の再確認中などを画面に表示します。「処理の詳細を開く」が表示された場合は、リンクから実際のrunも確認できます。
- Job受付結果が一時的に不明でも、同じrun IDで自動回復します。再度ボタンを押さず、そのまま待ってください。

処理を速めるため、RAG検索データを作るData Preparation JobはPerformance-optimized ServerlessとStandard Environment v5を使います。独立したVariantは最大2件まで並行実行します。AI SearchのHYBRID Index作成に必要な機能を確実に使えるよう、検証済みの`databricks-sdk==0.135.0`をJob Environmentのdependencyとして固定しています。Notebook taskではtask libraryを使いません。

Performance optimizedは待ち時間を短くする代わりに、Standard performance modeよりDBU使用量が増える場合があります。管理者は速度と`system.billing.usage`の実績を合わせて確認してください。

ただし、画面に見える一連の処理は一つのコンピュートだけで動いていません。PDF解析の`ai_parse_document`は引き続きX-Large Serverless SQL Warehouseで、`READ_FILES(..., format => 'file')`が返す`FILE`値を使います。後続のチャンク化とDelta Table作成はData Preparation Job、Index作成・同期はAI Search managed serviceが担当します。「処理の詳細を開く」で、Serverless Environment起動中、Job処理中、Index同期中のどこにいるかを確認してください。

field-eng-eastのData Preparation Job `<DATA_PREPARATION_JOB_ID>`の本番run `<DATABRICKS_RESOURCE_ID>`では、Setup 4秒、Job実処理154秒、合計159.146秒、アプリからのE2Eは182.4秒でした。3 chunksとREADY Indexを確認しています。同じ入力のClassic runはSetup 382秒、実処理169秒、合計552.328秒だったため、合計を71.18%短縮しました。隔離検証では1 PDFのrun `<DATABRICKS_RESOURCE_ID>`が157.875秒、8 PDFのrun `<DATABRICKS_RESOURCE_ID>`が158.368秒で完了しました。後者は8文書、31 chunks、READY Indexで、どちらもactive Variantを変更していません。`COMPUTE_STARTING`または`ENVIRONMENT_STARTING`中に再度ボタンを押さず、そのまま監視してください。

ServerlessはUnity CatalogとStandard access modeを前提とし、Instance Pool、init script、compute-scoped library、Spark UI、compute event logを使いません。Standard memoryは16 GBで、High memory 32 GBはPreviewです。本番データ量でメモリ不足や互換性問題が起きた場合に備え、管理者は検証済みClassic構成を[`deployment/jobs/data_preparation_job_classic_fallback.json`](deployment/jobs/data_preparation_job_classic_fallback.json)へ保持しています。Instance Poolはidle instanceを常時warmにするAzure VM費用が発生するため採用していません。

## 3. サイドバー2「PDFカタログ」

このページには、現在のProjectに登録されたPDFだけが表示されます。

- 検索欄: タイトル、概要、カテゴリ、タグ、ソースから絞り込む。
- 状態: Ready、Parsed、Parsing、Errorで絞り込む。
- カード／表ボタン: 表示方法を切り替える。
- 「PDFを開く」または「表示」: 認可済みのアプリ内リンクでPDFを開く。
- 「再解析」: 状態が`ERROR`のPDFだけを、同じ登録ファイルからもう一度Document Parsingする。
- 「削除」: OWNERまたはEDITORが、確認画面の後にPDFをRAGの検索対象から外す。

カタログにはタイトル、20〜30字の概要、カテゴリ、タグ、文書日付、ソース、解析状態が表示されます。トヨタ評価シナリオでは、後方互換の車種、年式、文書種別も表示できます。概要が未入力の場合、Document Parsing後に「AI概要」または安全なfallback概要が表示されます。

PDFを直接のVolume URLで開くのではなく、必ずアプリ内リンクを使います。これにより、Projectの閲覧権限を確認した後で文書を表示できます。「PDFを開く」へマウスを重ねる、フォーカスする、または押し始めた時点で先読みします。取得APIは`ETag`、byte `Range`、private cacheに対応し、同じProjectで一度読み込んだ同じPDF・ページはviewer内に保持するため、閉じてすぐ再表示すると再読込を待ちません。Projectを切り替えると保持内容を破棄し、別Projectへ文書を持ち越しません。ローカル実測では初回3,057 ms、同じPDFの再表示296 msでした。値はネットワークやPDFサイズで変わります。

状態が`ERROR`のPDFでは「再解析」が表示されます。原因を直してから1回押してください。アプリは古い解析runを閉じ、新しい`PARSE_ONLY` runを作って、Volume上の同じPDFを`FILE`型の`ai_parse_document`へ渡します。連打しても同時に複数の再解析を開始しません。`PARSED`／`READY`など正常なPDFには再解析ボタンを表示しません。

### 3.1 PDFをRAGの検索対象から削除する

PDFカードまたは表の「削除」は、そのPDFだけを現在のProjectの検索対象から外す操作です。Project全体は削除されません。OWNERまたはEDITORだけが実行できます。

1. 削除したいPDFの「削除」を押す。
2. 確認画面で、影響するVariantが再構築され、過去の会話と評価結果が保持されることを確認する。
3. 「PDFを削除」を押す。処理中はボタンに回転表示が出て、連打は受け付けない。
4. PDFがカタログから消え、影響する検索データが「作成中」になることを確認する。
5. 残るPDFがある場合は、Lakeflow JobとAI Searchの同期が完了し、後継Variantが利用可能になるまで待つ。最後のPDFを削除した場合は「PDFがありません。RAG検索データもありません。」と表示される。

削除は論理削除です。カタログ、新しい検索、新しいVariantの対象からは外れますが、次は監査用に保持されます。

- Unity Catalog Volume上のPDF原本とDocument Parsing結果
- 削除前の会話、実際に使った引用、MLflow Trace
- 削除前の評価ケースと評価結果
- 削除前のDelta TableとAI Search Indexの管理記録

そのため、ProjectのVIEWER以上の権限が続いている間は、削除前の回答に保存されたPDF引用リンクから監査用の原本を開けます。一方、現行画面に復元ボタンはありません。誤削除を避けるため、PDF名とProjectを確認してから実行してください。

裏側では、削除するPDFを含んでいた利用可能なVariantだけを`SUPERSEDED`（検索に使わない旧版）にします。そのVariantに残るPDFがあれば、元と同じチャンク、Embedding、クリーニング設定で後継Variantを作り、削除対象を含まないAI Search Indexへ同期します。削除したPDFだけで作られていたVariantには、空の後継Indexを作りません。最後のPDFならProject状態を`EMPTY`にし、使用中Variantを解除します。

> [!IMPORTANT]
> 削除対象のPDFを正解にした評価ケースも、過去の比較を再現するため残ります。単発の動作確認ではその質問を選択解除できます。一方、PDF削除後のコーパスを正式な継続比較の基準にする場合は、対象外ケースを整理した新しい評価データ版を作り、比較する全Phase／Variantで同じ質問集合を使ってください。

データ準備、回答生成、精度評価、または別のProject更新が実行中の場合は、HTTP 409相当の競合メッセージが表示されます。データ準備と評価は完了または停止を待ち、回答中なら先に「停止」を押してから、もう一度削除してください。App再起動などで開始から30分を超えたまま残った`QUEUED`／`STREAMING`／`CANCEL_REQUESTED`のChat runは、PDF削除前に自動で`ERROR`へ整理されます。対応する途中のassistant messageも`ERROR`へそろえます。30分以内の回答は整理対象ではなく、従来どおり409で保護します。削除APIは`202 Accepted`を返します。同じPDFの削除を誤って再送しても、別の削除を増やさず現在の削除状態を返します。

自動再構築の対象は、削除後に残るPDF 100件までです。100件を超えるProjectでは安全のため削除を受け付けず、管理者への連絡を案内します。

## 4. サイドバー3「RAGチャット」

### 4.1 検索条件を選ぶ

チャット上部の「RAG設定」を押して設定パネルを開き、「Phase（検索設定）」、「検索データ」、「回答モデル」を選びます。設定を閉じると会話欄を広く使えます。回答モデル一覧には、FMAPIで発見し、このワークスペースで`READY`かつ利用可能と確認できた候補が表示されます。2026-09-06の同期時点ではChat候補46件、うちTool Calling対応3件です。endpoint状態は変わるため、一覧は固定値ではありません。

Phaseの意味:

| Phase | 検索方式 | Metadata | Reranking | Query Optimization |
|---|---|---:|---:|---:|
| 1 | Vector Search | OFF | OFF | OFF |
| 2 | Hybrid Search | OFF | OFF | OFF |
| 3 | Hybrid Search | ON | OFF | OFF |
| 4 | Hybrid Search | ON | ON | OFF |
| 5 | Hybrid Search | ON | ON | ON |

正式な比較ではPhase presetを使います。「Custom」は、Vector／Hybridと3つの精度向上機能を自由に試すためのモードです。Customの結果を正式なPhase比較へ混ぜないでください。

Phase 3以降のMetadata Filteringは、質問に書かれた値を現在のProjectの文書registryと照合してから使います。対象は`PARSED`または`READY`の文書だけです。カテゴリ、タグ、文書日付、ソースは値が質問にある場合に候補となり、追加メタデータは項目名と値の両方が質問にある場合だけ候補になります。検証後は文書IDの一覧をAI Searchへ渡します。矛盾、0件、全件一致など安全に絞れない場合は、根拠を隠さないようフィルタなしへ戻ります。

現在の画面では`Deterministic RAG（比較向け）`を使用します。`Agentic RAG`はstreaming adapterの追加確認が終わるまで選択できません。内部にはMLflow `ResponsesAgent`互換層がありますが、画面で無効なモードを完了済みとは扱いません。

### 4.2 質問して根拠を確認する

1. 「＋ 新しい会話」を押す。
2. Phase、検索データ、回答モデルを選ぶ。
3. 質問を入力し「送信」を押す。`Enter`でも送信でき、`Shift`＋`Enter`で改行できる。
4. 回答本文の引用リンクを確認する。
5. 「RAG設定」を開き、「参照PDF」タブで展開クエリ、検索されたPDF、ページを確認する。
6. 回答下部のMLflow Trace linkを開き、検索と回答生成の処理を確認する。

回答に使える引用は、実際に検索したProject内チャンクとサーバー側で照合されます。ただし、画面にもretrieval SSEにもexcerpt／チャンク本文を表示しません。live citationは、検証済みの`document_id`と物理ページを使ってアプリ内PDF URLを組み立てます。LLMが作った任意URLは信用しません。候補をすべて引用として保存せず、最終回答で実際に参照したPDF原文リンクだけを表示します。リンクにはPDFタイトルと物理ページ番号が表示されます。リンクを押し、根拠ページと回答内容が一致するか確認してください。

左側には会話履歴が保存されます。履歴はProjectと利用者の単位で分離されます。Projectを切り替えると、そのProjectの会話だけが表示されます。最近の会話は先読みされ、選択するとキャッシュをすぐ表示します。会話を移動して戻っても、送信前の下書きは会話ごとに保持されます。

過去の会話を削除するには、会話名の右にある「×」を押し、確認画面で「会話を削除」を選びます。削除すると質問、回答、実行記録が削除され、取り消せません。回答中の会話は削除できないため、先に「停止」を押し、停止完了後にもう一度削除してください。

### 4.3 回答を停止する

回答中は回転する進捗表示と「停止」ボタンが表示され、「送信」ボタンは隠れます。同じ質問を`Enter`で連打しても、処理中は1回だけ送信します。

「停止」を押すと表示中のstreamをすぐ閉じ、入力欄を再び使えるようにします。同時に停止要求をDelta Tableへ保存するため、複数のApp replicaでも状態を確認できます。FMAPIへ渡した処理自体が即時中断できない段階でも、次の処理境界で`CANCELLED`へ確定し、再読込み後の履歴にも「回答を停止しました」と表示します。

## 5. サイドバー4「RAG精度評価」

このページは、同じ質問をPhase 1〜5へ実行し、品質と速度の変化を比較します。

### 5.1 評価質問を確認・登録する

評価は、現在のProjectへ登録したPDFに合う質問を使います。トヨタ評価用の固定質問は互換デモであり、任意分野のProjectへ流用しません。

RAG検索データの初回作成に成功すると、現在のProjectのPDFを使った次のサンプル質問が`starter-v1`／`development`へ3件登録されます。

- この文書の概要
- 重要なポイント3つ
- 主な手順や条件

これらは画面をすぐ試すための質問です。正解を捏造しないため、期待回答と正解ページは空です。期待回答・期待事実がないケースのAnswer Correctnessは`NULL`となり、平均から除外されます。0点として集計しません。Recall、Precision、nDCGなど正解ページを必要とする指標も判定対象外です。正式な精度比較には使わず、人が正解を付けたケースを追加してください。

「評価データ版」と「用途」を選ぶと、そのProject・版・用途に登録済みの質問が一覧表示されます。初めて開いた組み合わせでは全質問が選択済みです。

- 質問左のチェック: 今回の評価に含める質問を個別に切り替える。
- 「すべて選択」／「選択を解除」: 表示中の質問をまとめて切り替える。
- 状態表示: 「回答・検索の正解あり」「検索の正解あり」「正解情報を確認」のどこまで登録済みかを示す。
- 「正解を確認」: 期待する回答、正解PDF、正解ページを表示する。「根拠PDFを開く」から原文ページを確認できる。
- 件数表示: 「全件数中の選択件数」と、選択質問数 × Phase数 × 繰り返し回数から求めた最大試行数を示す。

質問を追加するときだけ「新しい質問を登録」を開きます。このフォームは初期状態で閉じています。

1. 正解に使うPDFが`PARSED`または`READY`であることを確認する。
2. 「新しい質問を登録」を開く。
3. 「質問」を入力する。
4. 必要なら「期待する回答」を入力する。
5. 「正解PDF」と、PDFに表示されている1始まりの「正解ページ」を選ぶ。複数ページは`2,3`のようにカンマで区切る。
6. 「評価データ版」を入力し、「用途」を選ぶ。
7. 「＋ 評価質問を追加」を押し、対応する版・用途の一覧に表示・選択されたことを確認する。

画面で入力する項目:

| 項目 | 必須 | 説明 |
|---|---:|---|
| 質問 | 必須 | RAGへ渡す質問。最大8000文字 |
| 期待する回答 | 任意 | 正解判定で比較したい回答内容 |
| 正解PDF | 必須 | 現在のProjectにある解析済みPDFから選択 |
| 正解ページ | 必須 | PDFの物理ページ番号。1始まり、複数指定可 |
| 評価データ版 | 必須 | 例: `v1.0.0`。英数字から始め、英数字・`.`・`_`・`-`を使用 |
| 用途 | 必須 | `development`は調整用、`holdout`は最終確認用 |

同じ評価データ版・用途へ同じ質問を重複登録できません。PDF、質問、期待回答、正解ページを変更した場合は、過去の結果を再現できるよう評価データ版も更新します。まず`development`へ質問を用意し、調整に使っていない別質問だけを`holdout`へ登録してください。

G01の登録例:

| 質問 | 期待する回答 | 正解ページ |
|---|---|---:|
| CSIRTへの一次報告期限は？ | 検知から30分以内 | 2 |
| 機密情報の標準保管期間は？ | 7年間 | 1 |
| 自動画面ロックは何分？ | 無操作5分 | 3 |

### 5.2 評価を開始する

1. 登録済みの「評価データ版」と「評価データ用途」を選ぶ。
   - `development`: 改善や調整に使う。
   - `holdout`: 調整完了後の最終確認にだけ使う。
2. 一覧で今回評価する質問を選ぶ。最初は全選択なので、短いsmokeでは不要な質問のチェックを外す。
3. 比較するPhaseを選ぶ。正式比較では1〜5をすべて選ぶ。
4. 「繰り返し回数」を選ぶ。最初の動作確認は1、正式比較は事前に決めた同じ回数を使う。
5. 表示された「最大○試行」が意図した件数か確認する。
6. 「比較する検索データ」を1つ選ぶ。
7. 回答LLMとJudge LLMを選ぶ。
8. 「選択した○問でRAG精度を比較」を押す。
9. 進捗が完了し、Phase比較表とグラフが表示されるまで待つ。

質問を0件にすると開始ボタンは「質問を選択してください」となり、評価を開始できません。APIも1〜1000件の質問IDだけを受け付け、すべてが現在のProject・評価データ版・用途に属することを再確認します。受付後は選択した質問IDを設定とhashへ固定し、Lakeflow Evaluation Jobはその質問だけを全Phaseで評価します。

画面の「評価履歴」には、このProjectで実行した最近の評価runが新しい順に表示されます。日時、評価データ版、用途、Phase、状態を確認し、過去のrunを押すと保存済みのPhase進捗、指標、改善提案を再表示できます。過去結果を見るために同じ評価を再実行する必要はありません。「更新」は履歴一覧を再取得します。

公平な比較のため、1回の評価runでは選択した質問、評価データ版・用途、Variant、回答LLM、Judge LLM、final kを固定します。Phaseの途中で変更しません。同じ結果と比較するときは、良い質問だけを後から選び直さず、同じ質問選択を使います。

### 5.3 指標を読む

| 指標 | 初心者向けの意味 |
|---|---|
| Recall@10 | 正解ページを上位10件の中でどれだけ見つけたか |
| Precision@10 | 上位10件のうち、正解ページがどれだけ含まれるか |
| nDCG@10 | 関連度の高いページを上位へ並べられたか |
| Answer Correctness | 期待する事実を回答に含めたか。期待回答・期待事実がないケースは`NULL`で平均から除外 |
| Groundedness | 回答が検索した根拠に基づいているか |
| Latency p50 | 回答完了までの時間の中央値 |
| Error rate | 全試行のうち失敗した割合 |

品質だけでなくレイテンシも確認します。たとえばPhase 4で順位が改善しても、Rerankingの追加時間が要件を超える場合があります。

TTFTは「最初の文字が届くまでの時間」です。非ストリーミングの品質評価では測れないため、値がない場合に0と解釈しないでください。正式なTTFTは別のストリーミング性能runで測ります。

### 5.4 LLMの改善提案を使う

評価完了後、「精度を上げるための改善提案」にPhase別の診断が表示されます。各カードには次が含まれます。

- どの失敗ケースを根拠にしたか。
- 問題が検索、metadata、Reranking、Query Optimization、chunk、Embedding、prompt、評価データのどこにあるか。
- 具体的な変更案、期待効果、注意点、再評価方法。

advisorにはRecall／Precision／nDCGだけでなく、Answer Correctness、Groundedness、Citation Correctnessと、judgeが各判定に付けたrationaleも渡します。これにより「検索順位は良いが回答が不正確」「回答は正しいが引用ページが弱い」といった違いを提案へ反映できます。未ラベル指標は`NULL`のまま渡し、根拠のない数値を作らせません。

提案は自動生成なので、そのまま採用しません。根拠ケースとMLflow Traceを確認し、一度に一つだけ変更して同じdevelopment Datasetを再実行します。最終候補だけをholdoutで確認します。

評価を止める場合は「評価を停止」を押します。停止済みrunと完了runを同じ集計として扱わないでください。

### 5.5 Enterprise運用で確認する情報

正式な比較結果を共有するときは、回答だけでなく次を一緒に確認します。

- Project: データ、会話、評価結果の分離単位
- 評価データ版と用途: 調整用`development`か、最終確認用`holdout`か
- Index Variant: チャンク、Embedding、解析条件を固定した検索データの版
- Phaseとモデル: 検索方式、精度向上設定、回答LLM、採点LLM
- run状態とTrace ID: 実行中、成功、停止、失敗と、MLflow 3で確認できる検索・回答処理
- PDF原文リンク: 回答の根拠をProject認可後に確認できるリンク

この組み合わせを固定すると、どのデータと設定で結果が変化したかを後から再現できます。画面上部の機能名表示は、問題が起きたときにUnity Catalog、Document Parsing、Lakeflow Jobs、AI Search、FMAPI、MLflowのどこを確認するか判断する手掛かりになります。

## 6. 最短デモ手順

### 6.1 任意分野PDFの動作確認

1. 新しいProject「情報セキュリティ規程」を作る。
2. [`generic_information_security_policy_demo.pdf`](output/pdf/generic_information_security_policy_demo.pdf)を登録する。Metadata Filteringまで確認する場合は上記の任意メタデータを入力する。PDFだけの登録も確認する場合は、重複登録を避けるため別Projectで試す。
3. 「均等に分割 / 512 / Qwen3 Embedding 0.6B」を選び、「RAG検索データを作成」を押す。Qwen3が利用不可の場合は画面の既定候補を使う。
4. RAGチャットで次を質問し、回答とPDFページの引用リンクを確認する。
   - 「CSIRTへの一次報告期限は？」→ 30分以内
   - 「機密情報の標準保管期間は？」→ 7年間
   - 「自動画面ロックは何分？」→ 5分
5. G01だけのProjectでは全件一致となるため、Metadata Filteringは安全のためフィルタなしへ戻る。実際の絞り込みを確認する場合は、異なるメタデータを持つ別PDFも同じ検証Projectへ登録し、「機密区分が社内公開の情報セキュリティ文書では、自動画面ロックは何分ですか」のように項目名と値を質問へ含める。
6. 「RAG精度評価」で上の表を参考に正解付き質問を登録し、同じ評価データ版・用途で3問をすべて選んでPhase 1〜5を比較する。

### 6.2 同梱トヨタ評価シナリオ

1. baseline用Projectを選ぶ。
2. D01〜D08がPDFカタログにあり、解析状態が`PARSED`または`READY`であることを確認する。
3. データ準備で「均等に分割 / 512」と利用可能なベクトル化モデルを選び、「RAG検索データを作成」を押す。
4. RAGチャットで同じVariantと同じLLMを選ぶ。
5. Phase 1で「2024年式プリウスのPDA上限速度は何km/hですか。」と質問し、回答と引用を確認する。
6. Phase 3で同じ質問を行い、2023年式や別車種が検索候補から除かれるか確認する。
7. 精度評価でversion、development、使用する質問、Phase 1〜5、Trial 1を選び、smoke評価を実行する。
8. 結果表、グラフ、Phase別の改善提案を確認する。
9. 動作確認後、Trial数と固定条件を決めて正式評価を実行する。

追加の質問例は [PDFコーパスREADME](output/pdf/README.md) にあります。

### この環境で確認済みの例

- Phase 1〜5、development 12問、Trial 1を同一条件で実行し、60／60結果、エラー0、Trace 60件、Phase別のLLM改善提案5件を確認済みです。
- Project「情報セキュリティ規程RAG-20260907-040609」では、登録、検索データ作成、回答、PDF引用、履歴を確認しました。
- RAG検索データ作成はprep run `<RESOURCE_ID>`、Job run `<DATABRICKS_RESOURCE_ID>`、Semantic／512 Variant `<RESOURCE_ID>`で`SUCCEEDED`です。sourceとIndexは各6行、別Project行0、Indexは`READY`です。
- 旧asset `1.4.1`の非車両PDF remote Chat E2Eで、期待回答`30分`との一致、Trace link `<TRACE_ID>`、引用1件、PDFリンクを確認済みです。
- 旧asset `1.4.1`のG01履歴では、Dataset `security-policy-v1`でPhase 1〜5を実行し、5結果、エラー0、Correctness／Groundedness／Citation Correctness各5件、Trace 5件、LLM改善提案5件を確認済みです。
- 旧asset `1.4.1`で開始した未ラベル`starter-v1`だけのeval run `<RESOURCE_ID>`も成功し、Answer Correctnessは0ではなく`—`（保存値`NULL`）になります。
- 旧asset `1.4.1`のトヨタ互換回帰でも期待回答`60`と一致し、Trace `<TRACE_ID>`、引用2件を確認済みです。
- baseline Projectの使用中検索データは`baseline-standard-512-v1`へ復元済みです。
- 旧asset `1.4.1`のremote回帰では、同一requestの保存がuser／assistant各1件であること、停止要求が`run.cancelled`／永続状態`CANCELLED`になることを確認済みです。評価履歴再表示、`ERROR` PDF再解析、PDFリンクだけを返すlive citationは現行sourceでも回帰済みです。
- 現行deployment `<DEPLOYMENT_ID>`では、Project `<RESOURCE_ID>`に残っていた30分超の孤児Chat run 2件とassistant messageを`ERROR`へ整合した後、document `<RESOURCE_ID>`の削除がHTTP 202で完了しました。影響旧Variant 4件から後継Variant 2件を作り、両方を`READY`まで確認しました。source／AI Searchは削除PDF 0件で、保持document `<RESOURCE_ID>`だけを返します。
- 選択評価run `<RESOURCE_ID>`はcase `figure-001`だけを評価し、Job `<DATABRICKS_RESOURCE_ID>`が`TERMINATED`／`SUCCESS`、結果1行、選択外0行、error 0で完了しました。
- 以前のdeploymentでは、freshな使い捨てProject `<RESOURCE_ID>`のPDFを2件から1件へ削除し、削除PDF 0行／検索hit 0、保持PDF 6行／検索hit 1の後継Indexが`READY`になるまでhelperを完走しました。別の検証Project `<RESOURCE_ID>`では最後のPDF削除後に`EMPTY`、利用可能Variant 0、prep run 4→4となり、原本、解析結果、過去引用を保持することを確認しました。
- PDF概要はregistryが20件だった時点の監査で、空欄0件、20〜30字違反0件でした。生成元は`AI_GENERATED=10`、`AI_GENERATED_NORMALIZED=9`、利用者入力の`USER=1`です。その後追加された削除E2E用2件を含む最終registry 22件全体へ、この内訳を外挿しません。

実際のデモでは、上の手順に沿って画面上の回答と根拠ページを利用者自身でも確認してください。

## 7. 困ったとき

| 画面の状態 | 確認すること |
|---|---|
| Projectが表示されない | Projectのメンバーに自分が含まれているか |
| 右上にメールアドレスが出ない | Databricks Appsから開いているか、`iam.current-user:read` scopeがあるか。token自体は画面やログへ貼らない |
| PDFアップロードに失敗 | PDF形式、100 MB以下、PDF header、重複ファイルか |
| Parsingが失敗 | FILE type Preview、対応compute、PDF暗号化・破損、run messageを確認する。原因を直した後、PDFカタログの「再解析」を1回押す |
| Embedding／LLMを選べない | endpointがREADYか、AppにCAN QUERYがあるか、model catalogが更新済みか |
| 検索データ作成が0%／`QUEUED`に見える | 画面のqueue説明と「処理の詳細を開く」を確認する。`ENVIRONMENT_STARTING`ならServerless Environmentの準備中、`WAITING_FOR_JOB_CAPACITY`なら先行run待ち。画面を再読込みしても監視は復元されるため、再度ボタンを押さない |
| チャンク作成後も完了しない | AI Search pipeline statusを確認する。Index作成・同期はmanaged service側の処理であり、Serverless Jobの起動が速くても別に待ち時間が発生する |
| VariantがREADYにならない | Prep Job、source TableのCDF、AI Search pipeline status、Embedding dimension |
| チャットできない | READYなVariant、LLM、Project権限、IndexのSELECT権限 |
| 引用リンクを開けない | 文書が同じProjectか、自分にVIEWER権限があるか |
| PDFの初回表示が遅い | PDFサイズとネットワークを確認する。2回目も遅い場合は`ETag`／`Range`応答とviewerの再利用を管理者に確認してもらう |
| PDFを削除できない | OWNER／EDITOR権限があるか、データ準備・回答・評価・別のProject更新が進行中でないか確認する。30分以内の回答runは409で保護されるため、完了または停止後に再実行する。30分超の孤児Chat runが残る場合は削除時に自動で`ERROR`へ整理される |
| PDF削除で5xxになる | 管理者に`toyota_document_registry`と`toyota_index_variants`の`SELECT`／`MODIFY`権限を確認してもらう。連打せず、mutation lockの解除と既存削除requestの状態を確認してから再実行する |
| PDF削除後に検索できない | 後継VariantのLakeflow JobとAI Search同期が完了しているか確認する。最後のPDFを削除した場合は仕様どおり`EMPTY` |
| Project／会話を削除できない | ProjectはOWNERだけ。実行中のデータ準備・回答・評価を完了または停止してから再実行する |
| Metadata Filteringで絞られない | 質問にメタデータ値があるか。追加メタデータは項目名と値の両方があるか。対象文書が`PARSED`／`READY`か |
| 評価質問を追加できない | 正解PDFが同じProjectで解析済みか、ページ番号がPDFの範囲内か、同じ版・用途に同じ質問がないか |
| 評価を開始できない | 質問が1件以上選択されているか、選択した版・用途に属する質問か、Eval Job、検索データ、回答LLM、Judge LLM、AppのCAN MANAGE RUNを確認 |
| 過去の評価結果が見えない | 現在のProjectが合っているか確認し、「評価履歴」の「更新」を押す |
| `RESOURCE_NOT_READY` | 画面の不足項目を管理者へ伝え、対応するApp resourceを設定する |

管理者へ連絡するときは、Project名、画面名、操作時刻、表示されたエラーコード、run IDを伝えてください。token、認証header、Volumeの内部資格情報は貼り付けないでください。

## 8. 管理者向けの確認先

- [Step別構築手順](docs/steps/README.md)
- [field-eng-east検証記録](docs/verification/2026-09-06_field-eng-east.md)
- [`ai_parse_document`公式ドキュメント](https://learn.microsoft.com/azure/databricks/sql/language-manual/functions/ai_parse_document)
- [`FILE`型公式ドキュメント](https://learn.microsoft.com/azure/databricks/sql/language-manual/data-types/file-type)
- [Lakeflow JobsのServerless compute](https://learn.microsoft.com/azure/databricks/jobs/run-serverless-jobs)
- [Serverless Environmentとdependency](https://learn.microsoft.com/azure/databricks/compute/serverless/dependencies)
- [Serverless computeの制約](https://learn.microsoft.com/azure/databricks/compute/serverless/limitations)
- [Environment v5](https://learn.microsoft.com/azure/databricks/release-notes/serverless/environment-version/five)
- [汎用メタデータmigration](sql/07_migrate_generic_document_metadata.sql)
- [PDF論理削除migration](sql/09_document_logical_deletion.sql)
- [AI Search検索品質ガイド](https://docs.databricks.com/aws/en/ai-search/retrieval-quality)
- [Databricks Apps公式ドキュメント](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
