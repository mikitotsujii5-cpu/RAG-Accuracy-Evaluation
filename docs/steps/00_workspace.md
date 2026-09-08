# Step 0: 接続先をfield-eng-eastへ固定する

## 目的

別のDatabricks Workspaceへ誤ってリソースを作らないように、最初に接続先と実行者を確認します。

## 構築

Databricks CLIのprofileを確認します。

```bash
databricks auth describe --profile <DATABRICKS_CLI_PROFILE>
databricks current-user me --profile <DATABRICKS_CLI_PROFILE> -o json
```

認証が未設定の場合だけ、対象hostを明示してログインします。

```bash
databricks auth login \
  --host https://<DATABRICKS_WORKSPACE_HOST> \
  --profile <DATABRICKS_CLI_PROFILE>
```

## 確認

```bash
databricks current-user me --profile <DATABRICKS_CLI_PROFILE> -o json \
  | jq '{id, userName, displayName, active}'
```

合格条件:

- hostが`https://<DATABRICKS_WORKSPACE_HOST>`である。
- Workspace IDが`<WORKSPACE_ID>`である。
- 利用者が意図した構築担当者で、`active`が`true`である。
- SQL Warehouse `<SQL_WAREHOUSE_ID>`を使った読み取りSQLが成功する。

## 失敗時の修正

- hostまたはWorkspace IDが違う場合は、その場で中断します。正しいhostで`auth login`をやり直してください。
- token cacheのエラーが出た場合は、現在のCLIで再ログインします。tokenをREADMEやshell historyへ貼り付けないでください。
- SQLが権限エラーになる場合は、Warehouseの`CAN USE`と対象Catalogの権限を管理者に確認します。

## この環境の実測結果

`SUCCESS`。2026-09-07に再確認し、`<DATABRICKS_CLI_PROFILE>`がfield-eng-eastを指し、Current User APIは`<DATABRICKS_USER_EMAIL>`（principal ID `<DATABRICKS_RESOURCE_ID>`、active）を返しました。後続のStatement Execution APIもWarehouse `<SQL_WAREHOUSE_ID>`で成功しています。

## 公式ドキュメント

- [Databricks CLIの認証](https://docs.databricks.com/aws/en/dev-tools/cli/authentication)
- [SQL Statement Execution API](https://docs.databricks.com/api/workspace/statementexecution)
