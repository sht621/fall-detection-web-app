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

2. コンテナを起動します。

   ```bash
   docker compose up --build
   ```

3. ブラウザで [http://localhost:8000/monitor](http://localhost:8000/monitor) を開きます。

`SIMULATE_FALL=true` の場合、camera コンテナは起動から約 5 秒後に一度だけダミー転倒を送信します。前後約 5 秒の生成フレームを MP4 としてアップロードするため、画面には先に `CAPTURING`、その後 `READY` が表示されます。

物理 USB カメラを使う段階では、ホストのデバイスと X11 を Compose に追加し、`CAMERA_DEVICE` と `ENABLE_LOCAL_DISPLAY=true` を設定してください。ローカル表示を有効にすると `f` キーでもイベントを発火できます。Compose のデフォルトは、カメラが無い開発 PC でも動くダミーフレームです。

起動を止めるには `docker compose down` を使用します。SQLite と動画はホスト側の `data/` に残ります。

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

## ファイル構成

- `src/camera/app/main.py`: カメラ取得、ダミー発火、動画生成・送信の入口
- `src/camera/app/fall_detector.py`: timestamp ベースの転倒判定状態機械の骨組み
- `src/camera/app/video_buffer.py`: 元フレームだけを保持するリングバッファと MP4 出力
- `src/camera/app/api_client.py`: Bearer 認証付き HTTPX クライアントと簡易再試行
- `src/server/app/main.py`: FastAPI の API、動画保存、SSE 通知
- `src/server/app/database.py`: sqlite3 のテーブル初期化と検知・確認結果の永続化
- `src/server/app/auth.py`: 開発用固定ユーザと将来の認証実装の境界
- `src/server/app/sse.py`: 単一 Uvicorn ワーカー向けの in-process SSE 配信
- `src/server/app/static/`: Vanilla JavaScript の監視画面
- `Dockerfile.camera`, `Dockerfile.server`: ルート直下の各コンテナ実行環境

## 現時点の未実装項目

- YOLO26n-pose からの keypoint/BBox 抽出と実際のルールベース転倒判定
- 複数人物追跡、複数カメラ管理、heartbeat
- セッション Cookie、CSRF、防御を含む本番認証
- 永続再送キュー、SSE 再送保証、複数 Uvicorn ワーカー対応
- H.264/yuv420p 出力、動画内容検証、HTTP Range の明示的な最適化
- HTTPS、顔ぼかし、音声保存、自動動画削除、Web Push
