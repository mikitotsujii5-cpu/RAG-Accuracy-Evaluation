# 検証記録

このディレクトリには、「作成したはず」ではなく、実際に確認できた状態を記録します。

- [2026-09-06 field-eng-east検証記録](2026-09-06_field-eng-east.md)

## 記録ルール

- `SUCCESS`は、確認command、SQL statement、Job run、deployment、HTTP responseなどの根拠がある場合だけ使います。
- 未実施、実行中、ブラウザサインイン待ちは`PENDING`にします。
- 失敗した最新結果は`FAILED`とし、過去の成功結果で上書きしません。
- 再確認した場合は、公開記録には確認日、期待値、実測値を更新します。run ID／deployment ID／statement IDはGit外の運用台帳で管理します。
- PDF単体の論理削除は、公開記録にはmigrationの2つの0件check、source／Indexの削除PDF行数、DELETE再送、最後のPDFの`EMPTY`、過去のPDF引用応答を匿名化して残します。削除document ID、旧／後継Variant ID、prep／Job run IDはGit外の運用台帳で管理します。
- OAuth token、PAT、authorization header、cookie、秘密情報は保存しません。

各Stepの再現用commandは[Step別構築手順](../steps/README.md)にあります。
