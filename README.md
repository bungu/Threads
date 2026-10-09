# Threads自動化

投稿の指示文をGeminiに渡して、X・Threads向けの本文を1件生成します。`post` コマンドでは、その本文をThreadsの1アカウントに1件公開します。Geminiには投稿本文だけを返すよう指示し、CLIも生成時は本文だけを標準出力に表示します。クラウド版の構成は [docs/cloud-setup.md](docs/cloud-setup.md) を参照してください。

クラウド版には本人限定の管理画面、アカウント別の設定保存、Cloud Scheduler 用ワーカー、トークン更新、結果不明時の照合操作を実装しています。既存のCLIはローカル用のままです。管理画面は [Cloud Run](https://threads-studio-web-154070015396.asia-northeast1.run.app) で公開しています。クラウドの定期投稿は初期状態で停止しています。

## PWごとのキー群と閲覧権限

管理PWごとに1つのMetaキー群を割り当て、別の閲覧用PWでは指定した群のアカウントを読み取り専用で表示できる。変更ボタンはグレーで操作不可。`setting.py` の `allowed_ips: ["*"]` は全IP許可、IPリストを指定するとそのIPだけが管理APIを利用できる。クラウド版とローカル版は同じPW・IP権限を使う。Geminiは共通キー。設定と導入方法は [docs/ip-access.md](docs/ip-access.md) を参照。手元で直接実行するCLIにはHTTP接続元がないため、この制限は管理画面APIに適用する。

## ローカル画面で即時投稿

ローカル画面はこのPCだけから開ける。アカウント・設定・実行記録は `.local/` に保存し、定期投稿は実行しない。「即時投稿」は選択中のアカウントへ、画面に現在入力されているプロンプトから1件生成して投稿する。未保存のプロンプトでも使える。

```bash
cd Threads自動化
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-local.txt
cp .env.example .env
```

`.env` に `GEMINI_PRO_API_KEY`、`GEMINI_API_KEY`、`THREADS_APP_ID`、`THREADS_APP_SECRET` を設定する。1アカウント目は `THREADS_USER_ID` と `THREADS_ACCESS_TOKEN` も設定すると、画面を開いたときに自動登録される。登録時にMetaのAPIでユーザーIDと投稿権限を検証する。2アカウント目以降は画面の「アカウントを追加」から入力する。起動後に `.env` を編集した場合は、画面を再読み込みすると読み直す。

起動前に [setting.py](setting.py) の `LOGIN_PASSWORD` に長く推測されにくい文字列を設定する。ファイルがない場合は `cp setting.example.py setting.py` で作る。空欄ではログインできない。このファイルは Git の追跡対象から除外してある。ログイン画面には設定した文字列を入力する。設定を変更した場合はサーバーを再起動し、再ログインする。

```bash
.venv/bin/python -m uvicorn threads_bot.local_app:app --host 127.0.0.1 --port 8765
```

このPCで `.venv` のファイルが省容量化されて読み込めない場合は、復旧済みのローカルSDKを使って次のコマンドで起動する。

```bash
PYTHONPATH=.local_deps PYTHONDONTWRITEBYTECODE=1 python3 -m uvicorn threads_bot.local_app:app --host 127.0.0.1 --port 8765
```

ブラウザで `http://127.0.0.1:8765` を開く。トークンは `.local/meta_<App ID>/studio.sqlite3` に、このPCの利用者だけが読める権限で保存する。旧 `.local/studio.sqlite3` は保持し、自動移行しない。公開結果が不明になったときは再投稿せず、画面の「接続設定」でThreads上の結果を照合する。ローカルで保存した設定はクラウド版には自動同期しない。

Metaアプリを作成した後の権限、テスター、トークン取得は [docs/meta-threads-setup.md](docs/meta-threads-setup.md) を参照する。

## 準備

Python 3.11以上を使います。プロジェクト内の仮想環境を作成して依存を入れます。

```bash
cd Threads自動化
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
```

`.env.example` を `.env` にコピーして、Geminiの2つのAPIキーを設定します。`GEMINI_PRO_API_KEY` は第一候補、`GEMINI_API_KEY` は失敗時のFlash用です。既存の `line_fruit_bot` と同じ `google-genai` の `client.interactions.create()`、同じモデル設定、Pro→Flashの切り替えを使います。`.env` はこのディレクトリ内だけを読み、環境変数に既にある値を優先します。

```text
GEMINI_PRO_API_KEY=...
GEMINI_API_KEY=...
THREADS_ACCESS_TOKEN=
THREADS_USER_ID=
```

Threadsの設定が空でも `generate`、`post --dry-run`、標準テストは使えます。Geminiのキーを使う生成はAPI利用料金が発生する場合があります。

## 生成

```bash
.venv/bin/python -m threads_bot generate --prompt "朝の散歩を続ける工夫を、親しみやすい日本語で短く書いて"
.venv/bin/python -m threads_bot generate --prompt-file prompt.example.txt
.venv/bin/python -m threads_bot post --prompt-file prompt.example.txt --dry-run
```

投稿したい指示をUTF-8ファイルに保存し、`--prompt-file` で渡せます。プロンプトは内容・文体・入れたい情報を自由に指定できます。システム指示ではX・Threads兼用の本文、1件、140文字以内、前置きや説明なしを指定しています。出力が空、140文字超、明らかな見出し付きの場合は投稿を止めます。出力を使う前に内容の正しさを確認してください。

モデル名は `threads_bot/settings.py` で変更できます。初期値は既存Botと同じ `gemini-3.1-pro-preview` と `gemini-3.6-flash` です。利用できない場合は設定を変更してください。

## Threadsへ投稿

Threads用のMetaアプリ、対象アカウントのユーザーアクセストークン、ユーザーIDを準備し、`threads_basic` と `threads_content_publish` の権限を確認してから、`.env` の `THREADS_ACCESS_TOKEN` と `THREADS_USER_ID` を設定します。ローカルCLIでは長期トークンの期限と更新を自身で管理してください。取得・更新手順は [Meta公式Authorization](https://www.postman.com/meta/threads/folder/34203612-e0373e84-de6b-46f1-b90d-3fea76ba6782) を参照してください。クラウド版には期限前の更新処理があります。

```bash
.venv/bin/python -m threads_bot post --prompt-file my_prompt.txt
```

このコマンドはGeminiの生成後、Threadsへ実際に1件投稿します。コンテナ作成、準備状態の確認、公開の順で実行します。成功時は標準出力に本文、標準エラーに実行IDと投稿IDを出します。設定不備はGemini呼び出し前に検出します。

公開リクエストの通信が切れた場合、投稿が既に公開されている可能性があります。終了コード5になった場合や、前回の公開処理中にプロセスが終了した場合は、次の投稿を止めます。

```bash
.venv/bin/python -m threads_bot status
.venv/bin/python -m threads_bot status --run-id <実行ID>
```

Threads上で投稿を確認した後、公開済みなら投稿ID、未公開を確認できたなら未公開の指定で状態を確定します。曖昧な場合は確定せずに照合を続けてください。

```bash
.venv/bin/python -m threads_bot resolve --run-id <実行ID> --post-id <Threads投稿ID>
.venv/bin/python -m threads_bot resolve --run-id <実行ID> --confirmed-not-published
```

実行状態と本文は `.local/runs.sqlite3` に保存します。APIキーとThreadsトークンは保存しません。同一ローカル環境での同時投稿をロックします。

## テスト

```bash
.venv/bin/python -m unittest discover -s tests -v
```

自動テストはGemini・Threadsの実APIを使わず、実投稿しません。Threads認証情報が揃った後の実投稿は自動テストとは別に、`post` を明示的に実行して確認してください。

終了コードは `0` 成功、`2` 設定不備、`3` Gemini生成失敗、`4` Threadsの明確なエラー、`5` 公開結果不明、`1` 実行記録などのエラーです。

実装時点ではMeta Developersの一部ページがHTTP 429で取得できず、[Meta公式PostmanのThreads API](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api) を参照しています。APIが利用できるようになったら、使用するアカウントでコンテナ状態、公開結果、APIバージョン、文字数判定を疎通確認してください。
