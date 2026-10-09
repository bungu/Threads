# クラウド版の設定

対象は `kimuragyoumuyou` / `asia-northeast1`。既存の Firestore を共有するため、Threads のコレクションは `threads_` で始める。

## PW別Metaキー群と閲覧権限

サーバーは [ip-access.md](ip-access.md) の `META_KEY_GROUPS_JSON` 設定が必要。管理PWごとにMeta App ID別の保存領域を使い、閲覧用PWには `view_groups` の読み取りだけを許可する。既存データは自動移行しない。Gemini共有キーとワーカーのIAM認証は引き継ぐ。


## 現在の状態

2026-10-09に Threads 用 Cloud Run の Web・ワーカー、Secret Manager、5分間隔の Cloud Scheduler を配備した。管理画面は `https://threads-studio-web-154070015396.asia-northeast1.run.app`。Firestore の `(default)` データベースは東京の既存のものを使う。`threads_runtime/global.enabled` は文書がない間 `false` と扱うので、配備だけでは定期投稿しない。Scheduler からワーカーへの試験呼び出しは HTTP 200 を確認した。

同じ Artifact Registry の `cloud-run-source-deploy/threads-studio:20261009-geo` イメージを Web とワーカーで共有する。初回イメージの実測容量は 63,016,017 バイト（約60 MiB）。Cloud Run は両サービスとも 1 vCPU/512 MiB、最小0・最大1、リクエスト課金。Webはパスワードログイン、ワーカーはIAM認証を必須とする。クラウドにはローカルのThreadsトークンと設定をまだ移していない。

Web ログインは全体で15分間に5回まで試行でき、次の試行で15分間ロックする。試行数とロック期限は Firestore の `threads_auth/login_global` に保存するため、インスタンス再起動や送信元IPの変更でも上限を回避できない。ログイン成功時に記録を消去する。ロック中は正しい文字列でもログインできず、既存セッションは引き続き有効。認証用 Firestore が利用できない場合、新規ログインは失敗する。大量の不正アクセスは本人の一時的なログイン停止や利用料金増加を起こし得る。管理者は必要に応じてこの文書を削除してロックを解除できるが、攻撃中は再びロックされる。

管理APIの海外アクセス制限は現在 `GEO_ENFORCE=1` で有効。2026年10月版 [DB-IP Country Lite](https://db-ip.com/db/download/ip-to-country-lite) をイメージ内で読み、推定国が日本以外なら HTTP 403 にする。Cloud Run 直通URLの転送ヘッダー末尾を採用する `TRUSTED_XFF_HOPS=1` は、偽装ヘッダーを含む実リクエストと Cloud Logging の送信元IPが一致することで確認した。IPが判定できない場合は通し、上記のログイン制限は引き続き適用する。画面のデータ出典リンクは DB-IP の CC BY 4.0 表示条件に対応する。国データは毎月更新して再ビルドする。VPNや共有プロキシの出口が日本なら通過し、誤判定で日本の利用者を拒否する可能性もある。アプリ側で拒否するため、海外からのリクエスト自体は Cloud Run に到達し、リクエスト費用の抑止にはならない。

手元の CLI でIPの推定国だけ確認できる: `.venv/bin/python -m threads_bot.geo_gate 8.8.8.8`。国の推測であり、住所・都市・利用者本人は特定できない。IP別の20回失敗・1か月停止は採用せず、IP別の解除操作も不要。

クラウドのセッション Cookie は `Secure`・`HttpOnly`・`SameSite=Strict` と `__Host-` 接頭辞を使い、旧 Cookie は再配備後に無効になる。画面の JavaScript は同一オリジンから配信し、Content Security Policy で外部スクリプトを拒否する。ログイン文字列の強度が弱ければ最初の数回の推測は防げないため、長くランダムな文字列への変更は引き続き推奨する。

管理画面の「即時投稿」は選択中のアカウントへ現在入力中のプロンプトから1件投稿する。アカウント別の処理権と実行記録を使い、全体の定期実行が停止中でも明示的な操作で実行する。

旧 LINE の Cloud Run サービス `line-fruit-bot` は手動スケーリング0、Cloud Tasks の `line-browser-jobs` と `line-reply-delay` は一時停止済み。2026-10-05に既存 Artifact Registry `cloud-run-source-deploy` から `line-fruit-bot` イメージの全バージョンを削除し、パッケージ一覧が空であることを確認した。リポジトリ自体はThreads用に残した。削除直後の表示容量は1,036.622 MBで、参照されなくなったレイヤーの回収を待っている。

## 無料枠の条件

Cloud Scheduler は請求アカウント単位で3ジョブ、Secret Manager は有効シークレットバージョン6件、Firestore は日次の無料読み書き上限がある。Artifact Registry と Cloud Build も請求アカウントで枠を共有する。Cloud Run は最小インスタンス0、最大1、リクエスト課金にする。Scheduler ジョブは1件。Gemini 料金は別途許容された。

**無料枠は請求を止める上限ではない。** 既存アプリや他のプロジェクトと枠を共有するため、無料枠外の請求を絶対に発生させない条件をこの構成で保証できない。予算通知も課金停止ではない。配備前に現在の使用量を確認し、この条件の扱いを決める。

### 2026-10-08 配備前の確認

- 請求先は `billingAccounts/0134AA-7C3824-591E13`。紐付くプロジェクトは `kimuragyoumuyou` と他2件。Cloud Run・Scheduler・Secret Manager・Artifact Registry の月間無料枠は、原則としてこの請求先の使用量で判定する。
- 既存の東京 Artifact Registry `cloud-run-source-deploy` は現在 0 MB。他2プロジェクトでもリポジトリ一覧は空、または Artifact Registry API が無効。ただし Threads の完成イメージは未ビルドで、無料保存枠 0.5 GiB 内か未確定。
- `kimuragyoumuyou` の Cloud Scheduler と Secret Manager API は未有効化。他2プロジェクトのこれらの API も有効化一覧になかった。Cloud Run には旧 `line-fruit-bot` が残るが、Threads サービスはない。請求書の当月使用量・Firestore 使用量は API 照会だけでは確定していない。
- Scheduler を5分ごとにすると30日で8,640回、31日で8,928回。Cloud Run の月200万リクエスト無料枠より十分少ない。1 vCPU で単純換算すると、無料 CPU 枠180,000 vCPU秒を単独で使える場合、31日月の1回当たり平均約20.16秒が目安。起動・終了時間、Web利用、既存サービス、請求先の他プロジェクトの利用分も合算されるため、実測前に無料確定とは言えない。
- Firestore は既存 `(default)` データベースを利用し、無料枠は1 GiB保存、1日5万読み取り・2万書き込み。5分ごとの定期処理だけなら、アカウント5件を有効化した場合でも通常日の読み書きはこの枠より少ない見込み。ただし既存アプリの消費と保存量は別途確認が必要。
- Secret Manager は請求先全体で有効バージョン6件・月1万アクセスまで無料。共有設定1件とアカウント1〜5件で1〜6件使う。トークン更新時に旧版と新版が一時的に併存すると7件になり得る。
- Cloud Scheduler は請求先全体で3ジョブ無料。停止中のジョブも数える。現時点で対象プロジェクトの API は無効なので、Threads の1ジョブを作ればこの範囲内の見込み。
- Cloud Run のインターネット送信、Cloud Build のビルド時間、Logging も課金項目になり得る。Cloud Run の通信先とブラウザの所在によって送信量の無料条件が異なる。
- Cloud Billing の予算通知は停止措置ではない。Cloud Run にはプレビューの spend cap があるが、対象は Cloud Run だけで、反映遅延中の超過分は請求される。Google Cloud 全体の請求をゼロに固定する手段にはならない。

### 31日間の利用量モデル

前提は東京リージョン、Web・ワーカー各1 vCPU/512 MiB、最小インスタンス0、リクエスト課金、Scheduler5分ごと。Cloud Runの無料枠180,000 vCPU秒・360,000 GiB秒・200万リクエストと、CPU超過時の参考単価 $0.000024/vCPU秒を用いる。下表の費用はCloud RunのCPUだけで、Gemini・ネットワーク・他サービスの利用料は含まない。請求アカウント内の既存利用が無料枠を消費していないと仮定する。

| 5分ごとの呼び出し1回の平均請求対象時間 | 1アカウント・1日1投稿 | 5アカウント・各1日6投稿 |
| --- | ---: | ---: |
| 2秒 | 約18,786 vCPU秒、$0 | 約45,756 vCPU秒、$0 |
| 5秒 | 約45,570 vCPU秒、$0 | 約72,540 vCPU秒、$0 |
| 10秒 | 約90,210 vCPU秒、$0 | 約117,180 vCPU秒、$0 |
| 15秒 | 約134,850 vCPU秒、$0 | 約161,820 vCPU秒、$0 |
| 20秒 | 約179,490 vCPU秒、$0 | 約206,460 vCPU秒、約$0.64 |

投稿処理を1件30秒と仮定し、各投稿の時間を足した概算。5アカウント同時刻の投稿は同一インスタンス内で並行実行するため、実際のCPU課金時間は表より短くなる場合がある。表の平均請求対象時間には起動・終了時間を含める必要があるが、その値は配備前には未計測。管理画面操作、再試行、他プロジェクトの利用は表に入れていない。投稿処理を除けば、31日月の5分ごとの呼び出しが平均約20.16秒を超えるとCPU枠を使い切る。5アカウント最大投稿の例では、平均約17.04秒が目安。

Firestoreの定期確認は、停止中なら約1読み取り/5分。有効化後は1アカウントで約4読み取り+1書き込み/5分（1日約1,152読み取り・288書き込み）、5アカウントで約12読み取り+1書き込み/5分（1日約3,456読み取り・288書き込み）。成功した投稿1件につき概算16読み取り・11書き込み・1削除を加算しても、最大30投稿/日で約3,936読み取り・618書き込み・30削除/日。既存アプリの使用量を除けば、Firestoreの無料上限内。実行記録は自動削除しないため、保存容量は毎月増える。

Secret Managerは共有設定1件とアカウント別トークン1〜5件で計2〜6有効バージョン。投稿ごとにトークンを1回取得するため、1日1投稿なら月31回、最大30投稿/日なら月930回。共有設定もCloud Runインスタンスの起動時に取得される。毎回新しいワーカーインスタンスが起動する極端な場合でも、共有設定8,928回と最大投稿のトークン930回で計9,858回。ただしWeb側の起動・他サービスの取得分も同じ月1万回の無料枠に合算される。

Artifact Registryは現在0 MBで、保存無料枠は請求アカウント合計0.5 GiB。完成イメージは未ビルドのため容量未確定。仮に保存量が合計1.0 GiBなら、超過0.5 GiBの月額目安は約$0.05。Cloud Buildの無料2,500分/月はデフォルトプールの`e2-standard-2`を使う場合に限るため、ビルド時のマシンタイプを指定する。

2026-10-08のローカル容量調査: このPCにはコンテナビルダーがないため、Dockerfileの完全ビルドは未実施。Docker Hubの`python:3.12-slim` Linux/amd64の公開レイヤー合計は41.26 MiB。当時の`requirements.txt`のLinux/amd64用wheel 53個をこのPCにダウンロードし、コンテナ内と同じ配置に展開してgzip圧縮すると34.14 MiB。アプリ本体約0.2 MiBとメタデータを加えた完成イメージは**約76 MiBの見込み**で、0.5 GiBまで約436 MiBの余裕がある。その後Firebase依存を除去したため現行イメージの容量は再計測していない。Google Cloudへのソース・イメージのアップロード、ビルド、配備は行っていない。実際のレジストリ容量は完成イメージのアップロード後に再確認する。

管理画面のHTMLとJavaScriptは合計約38 KB。月100回表示してもこの2ファイルの応答は約4 MB（圧縮前）で、通常のAPI応答を加えても通信量は小さい見込み。ただし外部への送信は宛先と請求SKUで料金が変わるため、無料とは断定できない。Cloud Loggingはリクエスト8,928件を仮に1件2 KiBとして約17 MiB/月で、保存の無料枠50 GiB/プロジェクトより小さい。

**判定:** 少数アカウントの通常利用なら Cloud Run の無料計算枠内に収まる可能性は高いが、Gemini 以外を絶対に無料にする条件は確認できない。2026-10-09に利用者が少額の超過リスクを了承し、配備した。実際の請求額は請求レポートで確認する。

料金資料: [Scheduler](https://cloud.google.com/scheduler/pricing)、[Secret Manager](https://cloud.google.com/secret-manager/pricing)、[Firestore](https://cloud.google.com/firestore/pricing)、[Artifact Registry](https://cloud.google.com/artifact-registry/pricing)、[Cloud Run](https://cloud.google.com/run/pricing)、[Cloud Build](https://cloud.google.com/build/pricing)、[ネットワーク](https://cloud.google.com/vpc/network-pricing)、[Cloud Logging](https://cloud.google.com/products/observability/pricing)、[Cloud Billing の spend cap](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)。

## 初期情報

管理画面のログイン文字列はローカルの [setting.py](../setting.py) に設定する。配備時はその文字列を既存の共有 Secret Manager 設定の `LOGIN_PASSWORD` に含める。`setting.py` 自体はソースのアップロード対象と Cloud Run イメージから除外し、イメージには空欄のテンプレートだけを入れる。Meta Threads App ID・App Secret、Gemini の Pro/Flash API キーも必要。Threads のアクセストークンは管理画面で入力し、チャット・Git・環境設定ファイルには置かない。

## サービス

`Dockerfile` からイメージを1つビルドし、既存の `cloud-run-source-deploy` リポジトリに置く。その同じイメージから `threads-studio-web` (`APP_MODE=web`) と `threads-studio-worker` (`APP_MODE=worker`) をデプロイする。Artifact Registry リポジトリを2つ作る必要はない。Web は公開URLだが管理APIが署名付きセッション・CSRF を検証する。ワーカーは Cloud Run IAM でも認証必須とし、`/tasks/tick` で Scheduler 専用サービスアカウントの OIDC トークンを再検証する。

両サービスは Secret Manager の `threads-shared` 最新バージョンを `APP_SECRETS_JSON` として受け取る。JSON は `SESSION_COOKIE_SECRET` (32文字以上)、`LOGIN_PASSWORD`、`THREADS_APP_ID`、`THREADS_APP_SECRET`、`GEMINI_PRO_API_KEY`、`GEMINI_API_KEY` を含む。アカウントトークンは事前作成した `threads-<ThreadsユーザーID>` の個別シークレットに保存し、更新時に旧バージョンを破棄する。追加アカウントのシークレットとその権限は管理者が登録前に用意する。5アカウントで Threads 用有効バージョンは計6件になる。他のシークレットが同じ請求アカウントにあれば無料枠を超える。

Web の環境変数: `GOOGLE_CLOUD_PROJECT`、`PUBLIC_ORIGIN`、`APP_MODE`、`APP_SECRETS_JSON`。ワーカー: `GOOGLE_CLOUD_PROJECT`、`WORKER_AUDIENCE` (ワーカーURL)、`SCHEDULER_SERVICE_ACCOUNT`、`APP_MODE`、`APP_SECRETS_JSON`。両方とも最小インスタンス0・最大1・リクエスト課金。ワーカーのHTTPタイムアウトは300秒を目安にする。

Scheduler は `*/5 * * * *`、`Asia/Tokyo`、HTTP POST、OIDC audience をワーカーURLにする。投稿時刻の処理開始には最大約5分の遅れが生じる。Scheduler専用サービスアカウントにはワーカーの `roles/run.invoker` を付与する。

## 導入と復旧

1. 請求アカウントの無料枠残量、Artifact Registry の既存容量を確認する。
2. Secret Manager と Cloud Scheduler API を有効化し、`setting.py` のログイン文字列を設定する。
3. 共有シークレットとサービスアカウントを作り、Firestore と対象シークレットの権限を付ける。
4. Web・ワーカーをデプロイし、Web URLを `PUBLIC_ORIGIN` に設定する。
5. 全体停止のままログイン、登録、保存、再読み込み、接続更新を確認する。
6. Scheduler の応答が `globally_paused` であることを確認する。
7. 指定アカウントの実投稿を本人が照合し、接続設定から全体の定期実行を開始する。

公開結果不明は自動再送しない。接続設定で Threads 上の投稿を照合し、公開済みの投稿IDまたは未公開確認を入力する。認証切れは対象アカウントだけを停止する。ロールバック時は全体停止、Scheduler 停止、Cloud Run 旧リビジョンへの復帰の順で行い、実行記録は残す。

ローカル確認は `.venv/bin/python -m unittest discover -s tests -v`。Cookie は `Secure` 属性なので、ブラウザでのログイン確認は HTTPS 環境で行う。自動テストは実 API・実投稿を使わない。
