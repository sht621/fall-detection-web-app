# fall-detection-web-app

ダミー転倒イベントを使って、カメラから FastAPI/SQLite、SSE、監視画面、確認結果保存までの通信を確認する最小構成です。ROS、WebSocket、Redis は使用しません。

```text
camera container -- REST --> FastAPI + SQLite -- SSE --> browser
       |                         |
       +-- MP4 upload ---------->+-- video storage
```

## 起動方法

1. 開発用の環境変数ファイルと永続化ディレクトリを作成します。

   ```bash
   cp .env.example .env
   mkdir -p data/camera-output
   ```

   ホストの UID/GID が 1000 以外なら、`.env` の `HOST_UID` と `HOST_GID` を `id -u` / `id -g` の値に合わせます。これにより SQLite と動画をホスト側へ root 所有で作らないようにします。
   USB カメラのグループ ID が 44 以外なら、`.env` の `VIDEO_GID` を `getent group video | cut -d: -f3` の値に合わせます。

2. コンテナを起動します。

   ```bash
   docker compose up --build
   ```

3. ブラウザで [http://localhost:8000/monitor](http://localhost:8000/monitor) を開きます。

`SIMULATE_FALL=true` の場合、camera コンテナは起動から約 5 秒後に一度だけダミー転倒を送信します。前後約 5 秒の元フレームを MP4 としてアップロードするため、画面には先に `CAPTURING`、その後 `READY` が表示されます。

camera 側は `CAMERA_DEVICE=/dev/video0` を OpenCV の V4L2 バックエンドで開き、`MODEL_PATH=yolo26n-pose.pt` の YOLO pose モデルで人物の BBox とスケルトンをローカル画面へ描画します。モデルは Ultralytics が初回起動時に自動ダウンロードします。ローカル表示中は `f` キーでもダミーイベントを発火できます。`q` または `ESC` で正常終了します。

複数人物が映った場合、画面上は検出された人物を描画しますが、転倒判定の入力には BBox 面積が最大の1人だけを使います。人物追跡や ID 管理はこの課題では扱いません。

起動を止めるには `docker compose down` を使用します。SQLite と動画はホスト側の `data/` に残ります。

## USB カメラと YOLO pose

モデルは通常、初回起動時に Ultralytics が自動ダウンロードします。提出時にモデルファイルを同梱する必要はありません。

ネットワークが使えない環境で事前配置したい場合だけ、ホスト側の `models/` に置いて `.env` の `MODEL_PATH` を `/app/models/yolo26n-pose.pt` へ変更します。

```bash
mkdir -p models
# 任意: オフライン実行したい場合だけ models/yolo26n-pose.pt を配置
```

ホスト側で USB カメラを確認します。

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
getent group video
```

X11 表示を使う場合は、現在のローカルユーザだけを許可します。

```bash
xhost +SI:localuser:$(id -un)
```

確認後に許可を戻す場合は次を実行します。

```bash
xhost -SI:localuser:$(id -un)
```

camera を起動します。

```bash
docker compose up --build camera
```

バックグラウンドで起動する場合は次を使います。

```bash
docker compose up -d --build camera
docker compose logs -f camera
```

ヘッドレス環境では `.env` で `SHOW_WINDOW=false` を指定します。この場合も USB カメラ取得と YOLO pose 推論ループは実行されますが、`cv2.imshow()` は呼びません。

簡易ルールによる転倒イベント送信を試す場合は `.env` で `FALL_RULES_ENABLED=true` にします。既定では `false` のため、姿勢推定だけではイベントを送信せず、`SIMULATE_FALL=true` または `f` キーで通信確認を行います。

確認する項目:

- USB カメラが開けた
- 実画像が表示された
- YOLO26n-pose が読み込まれた
- 初回起動時に必要ならモデルが自動ダウンロードされた
- PyTorch から CUDA と GPU 名がログに出た
- 人物 BBox とスケルトンが描画された
- 処理 FPS、YOLO 推論時間、検出人数、device、camera ID が画面表示された
- 人物がいない状態でも停止しない
- `q`、`ESC`、`Ctrl+C` で終了できた
- `SHOW_WINDOW=false` でもループが動作した

## 画面と API

- `GET /monitor`: 監視者用の一覧、動画再生、確認画面
- `GET /api/events`: `fall_detected` と `video_ready` を流す SSE
- `PUT /api/camera/detections/{event_id}`: カメラの転倒検知登録
- `PUT /api/camera/detections/{event_id}/video`: MP4 動画の送信
- `GET /api/detections`: 新しい順のイベント一覧
- `GET /api/detections/{event_id}`: イベント詳細
- `GET /api/detections/{event_id}/video`: 保存済み動画
- `PATCH /api/detections/{event_id}/review`: `FALL_CONFIRMED` または `NO_FALL` の登録

カメラ API は `Authorization: Bearer <CAMERA_API_TOKEN>` を必要とします。監視者操作は `AUTH_DISABLED=true` の間、固定の開発ユーザとして保存されます。

動画アップロードは開発用に `MAX_VIDEO_UPLOAD_BYTES=52428800` のサイズ上限を持ちます。動画の中身の厳密な検証は今後の調整対象です。

## 監視画面の認証

開発中は `.env` の `AUTH_DISABLED=true` により、監視者APIは固定の開発ユーザで動作します。ログイン画面、セッションCookie、CSRFを確認する場合は次のようにします。

```bash
python3 - <<'PY'
import base64, hashlib, secrets
password = b"ここに新しいパスワード"
salt = secrets.token_bytes(16)
iterations = 260000
digest = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
print("pbkdf2_sha256:{}:{}:{}".format(
    iterations,
    base64.urlsafe_b64encode(salt).decode().rstrip("="),
    base64.urlsafe_b64encode(digest).decode().rstrip("="),
))
PY
```

`.env` を次のように変更します。

```text
AUTH_DISABLED=false
SESSION_SECRET=32文字以上のランダム文字列
MONITOR_USERNAME=monitor
MONITOR_PASSWORD_HASH=上で生成した値
SESSION_COOKIE_SECURE=false
```

ローカルHTTPで確認する間は `SESSION_COOKIE_SECURE=false` のままにします。HTTPSで公開する場合は `SESSION_COOKIE_SECURE=true` に変更します。

## ファイル構成

- `src/camera/app/main.py`: USB カメラ取得、YOLO pose 推論、BBox/スケルトン描画、ダミー発火の入口
- `src/camera/app/fall_detector.py`: timestamp ベースの簡易転倒判定状態機械
- `src/camera/app/video_buffer.py`: 元フレームだけを保持するリングバッファと FFmpeg pipe による H.264/yuv420p MP4 出力
- `src/camera/app/api_client.py`: Bearer 認証付き HTTPX クライアントと簡易再試行
- `src/server/app/main.py`: FastAPI の API、動画保存、SSE 通知
- `src/server/app/database.py`: sqlite3 のテーブル初期化と検知・確認結果の永続化
- `src/server/app/auth.py`: 開発用固定ユーザ、署名付きセッションCookie、CSRF検証
- `src/server/app/sse.py`: 単一 Uvicorn ワーカー向けの in-process SSE 配信
- `src/server/app/static/`: Vanilla JavaScript の監視画面
- `Dockerfile.camera`, `Dockerfile.server`: ルート直下の各コンテナ実行環境

## 手動確認・調整が必要な項目

- 実カメラ画面で BBox とスケルトンが人物に合っているか確認
- 実際の教室、照明、カメラ角度で `POSE_CONFIDENCE` と `POSE_IMAGE_SIZE` を調整
- 転倒判定を使う場合は `FALL_RULES_ENABLED=true` にし、`FALL_CANDIDATE_SECONDS`、`FALL_BBOX_ASPECT_THRESHOLD`、`FALL_TORSO_ANGLE_THRESHOLD` を実映像で調整
- ダミー発火ではなく実判定で通知する運用に切り替えるタイミングを確認
- X11 表示で `q` / `ESC` による終了操作を実機画面で確認
- 初回起動時にネットワーク経由で `yolo26n-pose.pt` を自動ダウンロードできるか確認。オフライン提出環境では `models/yolo26n-pose.pt` を置き、`MODEL_PATH=/app/models/yolo26n-pose.pt` に変更
- 保存された MP4 が提出先・確認用ブラウザで再生できるか確認
- `AUTH_DISABLED=false` でログイン、確認結果PATCH、ログアウトが期待どおり動くか確認
- HTTPSで公開する場合は `SESSION_COOKIE_SECURE=true` とCookie動作を確認

## 残っている発展項目

- 複数カメラ管理、heartbeat
- 永続再送キュー、SSE 再送保証、複数 Uvicorn ワーカー対応
- HTTPS、音声保存、自動動画削除、Web Push
- OpenCV/Qt のフォント警告整理。現状は表示に致命的な問題がないため後回し
