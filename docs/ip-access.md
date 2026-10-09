# PWとMetaキー群・IP権限

管理画面のログインPWはキー群に1対1で対応する。PWからサーバー側でキー群を選び、
その群のMeta App ID/Secretと保存領域を使う。画面のヘッダーやリクエスト本文から群を切り替えることはできない。
Geminiのキーは共通。予約ワーカーはPWを持たず、IAM/OIDCで認証して各投稿用群を処理する。

`setting.py` の `META_KEY_GROUPS` は次の形式。`allowed_ips` は単一IPのリスト、
または全IP許可の `["*"]`。IP制限とPWによる群の選択は両方必要。

```python
LOGIN_PASSWORD = "長い管理用PW"
VIEW_PASSWORD = "別の長い閲覧用PW"
META_KEY_GROUPS = {
    "fallback": "owner",
    "groups": [
        {
            "id": "owner", "label": "本人用", "access": "signed_in",
            "login_password_env": "LOGIN_PASSWORD",
            "allowed_ips": ["203.0.113.10"],
            "app_id_env": "THREADS_APP_ID",
            "app_secret_env": "THREADS_APP_SECRET",
            "user_id_env": "THREADS_USER_ID",
            "access_token_env": "THREADS_ACCESS_TOKEN",
        },
        {
            "id": "viewer", "label": "閲覧用", "access": "view",
            "login_password": VIEW_PASSWORD,
            "allowed_ips": ["*"],
            "view_groups": ["owner"],
        },
    ],
}
```

投稿用群には別々のPWとMeta App ID/Secretを設定する。閲覧用群はMetaキーを持たず、
`view_groups` で列挙した投稿用群のアカウントを表示する。複数群を参照してもアカウントを混同しないよう、
画面ではキー群名を併記する。閲覧用PWはアカウント・設定・投稿履歴を読めるが、
トークンを受け取れず、接続・投稿・設定保存・結果確定・実行ON/OFFを操作できない。
画面の変更ボタンはグレー表示になり、APIも403を返す。

`setting.py` の初期例は `VIEW_PASSWORD = ""` なので、値を設定するまで閲覧用群は作られない。
実運用のPWやAPIキーはチャットやGitに載せない。設定変更後はアプリを再起動する。
PWの変更はその群の既存ログインセッションを失効させる。群間でPW、App ID、App Secretが重複すると
設定エラー（503）になる。

## IPと保存先

不許可IPからは管理APIを403で拒否する。空・不正なIPリスト、CIDR、`"*"` とIPの混在も
設定エラーになる。Cloud Runは転送ヘッダーの検証済み末尾を使い、ローカルは接続元ソケットを使う。
海外アクセス制限も引き続き適用される。クラウドの予約ワーカーには利用者のIPがないため、
IP判定を掛けずIAM/OIDCで認証する。

ローカルSQLiteは `.local/meta_<App ID>/studio.sqlite3`（ログイン試行記録は `.local/auth.sqlite3`）、クラウドはApp ID別の
FirestoreコレクションとSecret Managerに保存する。閲覧用群に独立のThreadsトークンは作らない。
旧 `.local/studio.sqlite3` は保持し、自動移行しない。

## Cloud Runに渡す設定

実際の `setting.py` と `.env` はコンテナに含めない。`META_KEY_GROUPS_JSON` を
Secret Managerの共有設定に文字列として保存し、Webとワーカーへ渡す。
`.venv/bin/python scripts/export_group_settings.py` で
`.local/ip-access/server-settings.json` に同じ群の設定を書き出せる。
参照先のローカル `.env` のThreadsトークンは、この書き出しで読まない。
追加の `*_env` を使う場合は、その値も共有シークレットに注入する。
`META_KEY_GROUPS_JSON` の値が不正なら、`setting.py` に暗黙には戻らない。
