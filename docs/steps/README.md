# Step別構築手順

このディレクトリは、`field-eng-east`（Workspace ID `<WORKSPACE_ID>`）へ「RAG精度評価アプリ」を構築する手順を、確認しやすい単位へ分けたものです。

必ずStep 0から順番に進めます。各Stepは次の流れです。

1. 「構築」を実行する。
2. 「確認」を実行する。
3. 合格条件を満たした場合だけ次へ進む。
4. 満たさない場合は「失敗時の修正」を行い、同じ確認を再実行する。

状態の意味:

| 状態 | 意味 |
|---|---|
| `SUCCESS` | 記載した確認を実環境またはローカルで実行し、合格した |
| `PENDING` | 未実施または実行中。成功とはみなさない |
| `FAILED` | 実測で失敗した。修正後の再確認が必要 |

## 実行順序

| Step | 内容 | 2026-09-08時点 |
|---:|---|---|
| 0 | [接続先をfield-eng-eastへ固定](00_workspace.md) | `SUCCESS` |
| 1 | [ローカル成果物を検証](01_local_validation.md) | asset `1.4.7`、Python 334件＋UI 37件＝合計371件を`SUCCESS` |
| 2 | [Unity Catalogの土台を作成](02_unity_catalog.md) | 汎用メタデータ／PDF論理削除migrationと0件post-checkを`SUCCESS` |
| 3 | [Project、PDF、モデルカタログを登録](03_seed_projects_and_models.md) | 非車両PDFとモデル再同期を含め`SUCCESS` |
| 4 | [`FILE`型でPDFをDocument Parsing](04_parse_pdfs_with_file_type.md) | G01の3ページ解析を含め`SUCCESS` |
| 5 | [評価データとチャンクを作成](05_evaluation_data_and_chunks.md) | Project／version／split内の質問選択と固定を含め`SUCCESS` |
| 6 | [AI Searchを作成・検索確認](06_ai_search.md) | baseline、非車両動的Index、PDF削除後の後継Indexを`SUCCESS` |
| 7 | [Lakeflow Jobsを配置・実行](07_lakeflow_jobs.md) | 選択case、同一`eval_run_id`のJob冪等化、重複Phase集約をローカルtestで`SUCCESS`。asset `1.4.7`のNotebook再importは`PENDING` |
| 8 | [MLflowとDatabricks Appを配置](08_mlflow_and_app.md) | asset `1.4.5`のremote配置は`SUCCESS`。現行asset `1.4.7`のremote配置は`PENDING` |
| 9 | [アプリをend-to-end確認](09_end_to_end.md) | asset `1.4.5`の既存Index、回答、Trace、PDF引用は`SUCCESS`。asset `1.4.7`の評価受付・停止・結果取得回復を含むremote E2E、SSO済み4画面、TTFT、残り7 profileは`PENDING` |

最新の実測結果は[検証記録](../verification/2026-09-06_field-eng-east.md)を参照してください。詳細な設計理由は[構築・評価手順書](../../DATABRICKS_TOYOTA_RAG_BUILD_GUIDE.md)、画面操作は[アプリ操作ガイド](../../APP_USER_GUIDE.md)にあります。
