"""Copy this file to setting.py, then set the management UI password."""

LOGIN_PASSWORD = ""
VIEW_PASSWORD = ""  # 閲覧用PW。設定すると閲覧用キー群が有効になる。

# 両環境共通のキー群設定。META_KEY_GROUPS_JSON があればそちらを優先します。
# Cloud Run には設定を Secret Manager で渡します（setting.py 自体はアップロードしません）。
# allowed_ips は単一IPのリスト、または ["*"]。Geminiキーはこの対応表に含めません。
META_KEY_GROUPS = {
    "fallback": "default",
    "groups": [{
        "id": "default", "label": "本人用", "access": "signed_in",
        "login_password_env": "LOGIN_PASSWORD",
        "allowed_ips": ["*"],  # 指定例: ["203.0.113.10", "2001:db8::1"]
        "app_id_env": "THREADS_APP_ID",
        "app_secret_env": "THREADS_APP_SECRET",
        "user_id_env": "THREADS_USER_ID",
        "access_token_env": "THREADS_ACCESS_TOKEN",
    }, *([{
        "id": "viewer", "label": "閲覧用", "access": "view",
        "login_password": VIEW_PASSWORD,
        "allowed_ips": ["*"], "view_groups": ["default"],
    }] if VIEW_PASSWORD else [])],
}
