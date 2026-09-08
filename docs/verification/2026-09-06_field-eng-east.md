# Databricks Workspace 検証記録（匿名化版）

> 公開用リポジトリでは、Workspace ID、Workspace URL、ユーザーのメールアドレス、Warehouse／Job／Experiment／App／Service Principal／Deployment／Statement／Request／Session／Trace／Project／Document／Variantなどの実IDを記載しません。実環境の値は、アクセス制御された運用台帳で管理してください。

## 検証対象

| 項目 | 公開用の表記 |
|---|---|
| Workspace | `<WORKSPACE_NAME>` |
| Workspace ID | `<WORKSPACE_ID>` |
| Host | `https://adb-<WORKSPACE_ID>.<SHARD>.azuredatabricks.net` |
| CLI profile | `<DATABRICKS_CLI_PROFILE>` |
| Catalog／Schema | `<UC_CATALOG>.<UC_SCHEMA>` |
| SQL Warehouse | `<SQL_WAREHOUSE_ID>` |

## 結果サマリー

2026-09-06〜08の検証では、次の項目を確認しました。具体的なIDは公開用記録から除外しています。

| 区分 | 状態 | 匿名化した結果 |
|---|---|---|
| 接続と権限 | `SUCCESS` | 対象Workspaceへ接続し、App専用サービスプリンシパルの最小権限を確認 |
| Source test | `SUCCESS` | Python 264件、Chat UI 21件、Python compile、JavaScript構文、JSON検証に成功 |
| Unity Catalog／PDF | `SUCCESS` | Project分離、PDF registry、論理削除、監査履歴を確認 |
| Document Parsing | `SUCCESS` | `FILE`型を`ai_parse_document`へ渡し、parser errorがないことを確認 |
| PDF render QA | `SUCCESS` | サンプル9冊45ページを目視し、文字化け、欠け、重なりがないことを確認 |
| 評価Dataset | `SUCCESS` | development 12問、holdout 4問を同じ条件でPhase比較に利用 |
| Model catalog | `SUCCESS` | Workspaceで利用可能なEmbedding／Chat endpointだけを選択可能にしたことを確認 |
| AI Search | `SUCCESS` | Endpoint、既存Index、ANN、HYBRID、metadata filter、Rerankingを確認 |
| Lakeflow Jobs | `SUCCESS` | データ準備、評価、Index同期の各Jobが正常終了することを確認 |
| MLflow | `SUCCESS` | `AGENT`配下に`RETRIEVER`、`CHAT_MODEL`、`EVALUATOR`のTrace階層を確認 |
| Databricks App | `SUCCESS` | Deployment、`RUNNING`、compute `ACTIVE`、health HTTP 200、resource binding 7件を確認 |
| App UI | `SUCCESS` | Enter多重送信防止、思考中表示、停止、履歴切替、モバイル幅、console error 0を確認 |
| 評価質問の選択 | `SUCCESS` | 個別・一括選択、0件開始防止、正解情報表示、選択IDだけの評価を確認 |
| PDF viewer | `SUCCESS` | PDF 200、Range 206、ETag再検証、Project切替時の破棄を確認 |
| PDF単体の論理削除 | `SUCCESS` | 影響Variantを残存PDFだけで再構築し、削除PDFの検索hitが0になることを確認 |
| 最後のPDF削除 | `SUCCESS` | Projectが`EMPTY`となり、空Indexを作らず、過去の引用と監査履歴を保持 |
| 未ラベル評価 | `SUCCESS` | Answer Correctnessを`NULL`として集計対象外にし、改善提案を生成 |
| TTFT／未実施profile | `PENDING` | 未実測項目を推測で成功扱いにしない |

## 性能確認の匿名化概要

Data Preparation JobをPerformance-optimized Serverless Environment v5へ移行し、同じ入力を使った単発比較で次を確認しました。

| 項目 | Classic | Serverless | 変化 |
|---|---:|---:|---:|
| Setup | 382秒 | 4秒 | 約98.95%短縮 |
| Job実処理 | 169秒 | 154秒 | 約8.88%短縮 |
| Job合計 | 552.328秒 | 159.146秒 | 約71.19%短縮 |
| App E2E | 518.8秒 | 182.4秒 | 約64.84%短縮 |

これは各1回の実測であり、p50／p95の負荷試験結果ではありません。Performance optimizedはDBU使用量が増える場合があるため、速度と費用を継続して比較してください。

## Phase 1〜5の匿名化比較

development 12問、同じStandard／512 Variant、同じ回答LLM／Judge LLM、Trial 1で比較したsmoke値です。実run IDとTrace IDは公開していません。

| Phase | Recall@10 | Precision@10 | nDCG@10 | Correctness | Groundedness | Citation | E2E p50 | E2E p95 | Total tokens |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.8958 | 0.1833 | 0.7644 | 1.0000 | 1.0000 | 1.0000 | 4,498.8 ms | 9,038.6 ms | 75,010 |
| 2 | 1.0000 | 0.2083 | 0.8519 | 1.0000 | 0.9167 | 0.9167 | 4,038.2 ms | 9,916.4 ms | 71,575 |
| 3 | 0.8681 | 0.1750 | 0.8596 | 1.0000 | 1.0000 | 0.9167 | 4,011.0 ms | 8,861.2 ms | 57,535 |
| 4 | 0.8681 | 0.1750 | 0.8159 | 1.0000 | 1.0000 | 1.0000 | 4,611.0 ms | 8,651.0 ms | 57,511 |
| 5 | 0.8681 | 0.1750 | 0.8393 | 1.0000 | 1.0000 | 1.0000 | 7,983.1 ms | 14,474.9 ms | 59,687 |

このrunは非ストリーミング品質評価です。TTFTは別の性能runで測定し、未測定の場合は`PENDING`とします。

## 再現時の確認方法

1. `<WORKSPACE_ID>`や各リソースIDを、非公開の環境変数またはDatabricks App Resource Bindingから渡します。
2. 実値を含むresource stateはGitへ追加せず、アクセス制御された運用台帳で管理します。
3. `docs/steps/`の順に構築し、各Stepの確認項目が成功してから次へ進みます。
4. 実run ID、Trace ID、Statement IDなどはMLflowまたは運用台帳で確認し、公開Markdownへ転記しません。

関連手順は[Step別構築手順](../steps/README.md)と[アプリ操作ガイド](../../APP_USER_GUIDE.md)を参照してください。
