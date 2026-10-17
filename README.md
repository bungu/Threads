※本システムはCodex（6-Astra,6.1-Sol）を用いて開発しました。

# Threads自動化

指定したプロンプトで、Threadsに自動投稿、定期投稿するシステムです。
投稿の指示文をLLMに渡して、X・Threads向けの本文を生成します。Geminiには投稿本文だけを返すよう指示し、CLIも生成時は本文だけを標準出力に表示します。

## URL
https://threads-studio-web-154070015396.asia-northeast1.run.app

## 準備

プロジェクト内の仮想環境を作成して依存をインストールします。Python 3.11以上が必要です。

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

## テスト

```bash
.venv/bin/python -m unittest discover -s tests -v
```

実装した機能が全て機能しているか開発中に継続的に検証し続けるため、自動テストを採用しています。
テストではGemini・Threadsの実APIを使わず、実投稿しません。

終了コードは `0` 成功、`2` 設定不備、`3` Gemini生成失敗、`4` Threadsの明確なエラー、`5` 公開結果不明、`1` 実行記録などのエラーです。
