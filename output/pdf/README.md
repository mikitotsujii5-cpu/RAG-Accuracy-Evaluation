# RAG精度評価デモ PDFコーパス

このディレクトリには、「RAG精度評価アプリ」の検索・回答品質を比較するための架空PDFを収録しています。アプリは任意分野のPDFに対応し、トヨタ車種関連PDFは同梱評価シナリオの1つです。

> [!CAUTION]
> すべて評価用に生成した非公式・架空のデータです。トヨタPDF本文中の車両仕様、型式、速度、装備、操作、救助手順は実在車両の情報ではありません。実車の操作・整備・救助には使用できず、トヨタ自動車株式会社とは関係ありません。

## 使い分け

- `G01`: 車両と無関係な情報セキュリティ規程です。任意分野PDF、汎用メタデータ、タイトル自動補完、引用のsmoke testに使います。
- `D01`-`D08`: 正式なPhase 1-5比較で同じProjectへ登録する基準コーパスです。
- `D09`: `D05`を画像だけのスキャン風PDFへ変換したDocument Parsing比較用Variantです。テキスト層はありません。
- `D05`と`D09`を同じ検索Indexへ同時登録しないでください。同じ内容が重複し、検索評価が歪みます。

推奨Projectは次の3つです。

1. `generic-security-policy`: G01だけを登録し、汎用RAGの登録・検索・引用を確認する。
2. `toyota-rag-baseline`: D01-D08を登録し、Phase 1-5を比較する。
3. `toyota-rag-scan-parse`: D09だけを登録し、D05の解析結果と比較する。

## 汎用RAGのsmoke test

対象PDF: [`generic_information_security_policy_demo.pdf`](generic_information_security_policy_demo.pdf)

アップロードで必須なのはPDFだけです。タイトルを空欄にすると「generic information security policy demo」がファイル名から補完されます。読みやすい表示とMetadata Filteringを試す場合は、次の任意値を入力します。

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

確認質問:

| 質問 | 期待回答 |
|---|---|
| CSIRTへの一次報告期限は？ | 30分以内 |
| 機密情報の標準保管期間は？ | 7年間 |
| 自動画面ロックは何分？ | 5分 |

質問例「機密区分が社内公開の情報セキュリティ規程では、自動画面ロックは何分ですか」には、追加メタデータのkey「機密区分」とvalue「社内公開」の両方が含まれます。ただし、G01だけのProjectでは全件一致となるため、実装は安全のためフィルタなしへ戻ります。実際の絞り込みを確認するには、異なるメタデータを持つ別PDFも同じ検証Projectへ登録します。

## トヨタ評価シナリオの文書一覧

次の`model`、`model_year`、`document_type`、`vehicle_category`は、既存seedと評価Datasetを再現するための後方互換メタデータです。汎用PDFの必須項目ではありません。

| ID | PDF | model | model_year | document_type | vehicle_category | 主な評価目的 |
|---|---|---:|---:|---|---|---|
| D01 | [01_prius_2024_owners_guide_demo.pdf](01_prius_2024_owners_guide_demo.pdf) | Prius | 2024 | `owners_guide` | `passenger_car` | 2024年式の基準値 |
| D02 | [02_prius_2023_owners_guide_demo.pdf](02_prius_2023_owners_guide_demo.pdf) | Prius | 2023 | `owners_guide` | `passenger_car` | 年式が異なる類似文書 |
| D03 | [03_prius_2024_grade_equipment_demo.pdf](03_prius_2024_grade_equipment_demo.pdf) | Prius | 2024 | `equipment_spec` | `passenger_car` | 表、行列、脚注 |
| D04 | [04_prius_2024_safety_operation_demo.pdf](04_prius_2024_safety_operation_demo.pdf) | Prius | 2024 | `safety_operation_guide` | `passenger_car` | センサー図、作動条件、長い操作手順 |
| D05 | [05_prius_2024_emergency_response_demo.pdf](05_prius_2024_emergency_response_demo.pdf) | Prius | 2024 | `emergency_response_guide` | `passenger_car` | 型式、配置図、5段階手順 |
| D06 | [06_prius_2023_2024_change_report_demo.pdf](06_prius_2023_2024_change_report_demo.pdf) | Prius | 2024 | `model_change_report` | `passenger_car` | 新旧比較、複数文書統合 |
| D07 | [07_crown_sport_2024_owners_guide_demo.pdf](07_crown_sport_2024_owners_guide_demo.pdf) | Crown Sport | 2024 | `owners_guide` | `suv` | 同名機能・異なる車種の負例 |
| D08 | [08_toyota_demo_safety_glossary.pdf](08_toyota_demo_safety_glossary.pdf) | Common |  | `glossary` | `all` | 略称展開、Reranking用decoy |
| D09 | [09_prius_2024_emergency_response_scan_demo.pdf](09_prius_2024_emergency_response_scan_demo.pdf) | Prius | 2024 | `emergency_response_scan` | `passenger_car` | 画像PDFのDocument Parsing比較 |

機械取込み用の同じ情報は [toyota_rag_demo_manifest.csv](toyota_rag_demo_manifest.csv) にあります。このmanifestはD01〜D09専用で、G01は汎用UIの手動smoke用です。

## トヨタ評価シナリオのPhase確認質問

| 比較 | 質問例 | 期待する変化 |
|---|---|---|
| Phase 1: Vector | 運転中に先回りして支援する機能は何ですか。 | 意味検索でPDAへ到達する |
| Phase 2: Hybrid | ZVW60のPDAを解除する操作は何ですか。 | `ZVW60`、`PDA`、`cancel switch`の完全一致が効く |
| Phase 3: Metadata | 2024年式プリウスのPDA上限速度は何km/hですか。 | D02とD07を除外し、60 km/hを返す |
| Phase 4: Reranking | 2024年式プリウスでPDAを有効にする操作と作動条件は何ですか。 | 用語集や変更レポートよりD04を上位にする |
| Phase 5: Query Optimization | 新型プリウスでは先読み支援と配光支援が旧型からどう変わりましたか。 | PDA/AHSへ展開し、D01、D02、D06を統合する |
| 表解析 | 2024年式プリウスGでAHSとSEAは利用できますか。 | D03 p2とp4の表・脚注を統合する |
| 図解析 | ZVW60の配置図で①は何を示し、どこにありますか。 | D05 p3のサービスプラグと後席座面下を返す |
| 非回答 | 2024年式プリウスの新車価格とWLTC燃費を教えてください。 | コーパスにないため回答を控える |

固定評価セットは [toyota_rag_eval_seed.jsonl](toyota_rag_eval_seed.jsonl) です。これはトヨタ評価シナリオ専用であり、G01へ流用しません。PDFをVolumeへ登録した後、`filename`を文書registryの実際の`doc_uri`へ結合して評価Delta Tableを作成してください。ページ番号はPDF物理ページの1始まりです。別分野のProjectでは、そのPDFに対応する質問、期待回答、正解URI、正解ページを別Dataset versionとして準備します。

## データ準備比較

- チャンクサイズ: 256 / 512 / 1024
- 手法: Standard / Semantic chunking / Parent-child chunking
- 表・図: D03、D04、D05でレイアウト保持の効果を確認
- スキャン: D05とD09を別Projectまたは別corpus snapshotで比較
- クリーニング: 全ページに繰り返すheader、footer、免責、ページ番号の除去効果を確認

正式比較では、一度作成したD01-D08を変更せず、同じファイルhashと同じ評価JSONLを全Phaseで使ってください。G01や利用者自身のPDFは別Project／別Datasetへ分離し、トヨタbaselineの検索Indexへ混ぜません。
