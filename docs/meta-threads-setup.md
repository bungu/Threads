# Metaアプリ作成後のThreads接続

このシステムのローカル画面で即時投稿するまでの設定。Metaの画面名は表示言語や更新で多少変わる。

1. Meta for Developersで作成したアプリを開き、ユースケース「Access the Threads API」を設定する。`threads_basic` と `threads_content_publish` を使える状態にする。
2. Threads API用の **Threads App ID** と **Threads App Secret** を確認する。通常のMeta App ID/Secretとは別の値。プロジェクトの `.env` の `THREADS_APP_ID`、`THREADS_APP_SECRET` に入力する。
3. 開発中のアカウントを使う場合は、アプリのロールで本人のThreadsアカウントを **Threads Tester** に招待する。本人のThreads側で「設定 → アカウント → ウェブサイトのアクセス許可」などにある招待を承認する。
4. ユースケースの設定に **User Token Generator** があれば、承認済みアカウントの **Generate Access Token** を押し、`threads_basic` と `threads_content_publish` を許可してトークンをコピーする。この方法ではローカル画面用のOAuthリダイレクトURLを設定する必要はない。トークンはチャット、Git、URLに貼らない。
5. トークンに対応するThreadsユーザーIDが不明なら、下のコマンドで本人の `id` を取得する。コマンドはトークンをシェル履歴に残さず、APIへはヘッダーで送る。

   ```bash
   read -rsp 'Threads token: ' THREADS_TOKEN; echo
   curl -sS -H "Authorization: Bearer $THREADS_TOKEN" 'https://graph.threads.net/me?fields=id,username'
   unset THREADS_TOKEN
   ```

6. `.env` にGeminiの `GEMINI_PRO_API_KEY` と `GEMINI_API_KEY` も設定する。起動中のローカル画面は次の登録・投稿操作で `.env` を読み直す。`http://127.0.0.1:8765` の「アカウントを追加」に `id` とThreadsユーザーアクセストークンを入力する。画面にプロンプトを入力し、「即時投稿」を押す。

User Token Generatorが表示されない場合はMetaの[公式Threads API Postmanコレクション](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)にある認可コード方式を使う。Metaの[公式サンプル](https://github.com/fbsamples/threads_api/blob/main/README.md)にローカルHTTPSとコールバックURLの設定例がある。ThreadsのOAuthリダイレクトに `localhost` は使えず、HTTPSのローカル用ドメインが必要。`http://127.0.0.1:8765` はこのシステムの管理画面URLであり、OAuthコールバックURLではない。

参考: [Meta公式Threads認可資料](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions/)、[Meta公式トークン更新資料](https://developers.facebook.com/docs/threads/get-started/long-lived-tokens/)。
