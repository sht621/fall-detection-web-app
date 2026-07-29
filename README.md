# fall-detection-web-app

大学院科目「ネットワークプログラミング特論」の最終課題として作成した、転倒検知イベント通知・映像確認Webシステムです。

USBカメラ映像からYOLO-Poseで人物のBBoxとキーポイントを取得し、簡易的なルールベース処理で転倒を判定します。転倒検知時は、通知を遅らせないために検知情報を先にWebサーバへ送信し、その後リングバッファから転倒前後のMP4動画を生成して送信します。監視者はブラウザで通知を受け取り、動画確認、転倒/誤検知の登録、記録削除を行います。

研究用の高精度な転倒検知モデルではなく、授業課題用にネットワーク通信、SSE通知、動画配信、SQLite保存、認証を一通り確認するための簡易実装です。ROS、WebSocket、Redis、React、Vueは使用していません。

## システム構成

```text
camera container
  USB camera -> OpenCV -> YOLO-Pose -> rule based fall detection
      |             |
      |             +-> local OpenCV preview
      |
      +-- PUT detection JSON ------------+
      +-- PUT MP4 video -----------------+
                                         v
server container: FastAPI + SQLite + SSE + video files
      |
      +-- HTML/CSS/JavaScript + REST + SSE
      v
browser monitor
  notification -> list -> video review -> PATCH review / DELETE record
```

開発時は `camera` と `server` を同じDocker Compose上で動かします。監視画面、REST API、SSE、動画配信は同じFastAPIサーバから配信する同一オリジン構成です。

## ディレクトリ構成

```text
.
├── src/
│   ├── camera/app/
│   │   ├── main.py
│   │   ├── fall_detector.py
│   │   ├── video_buffer.py
│   │   └── api_client.py
│   └── server/app/
│       ├── main.py
│       ├── database.py
│       ├── auth.py
│       ├── sse.py
│       └── static/
│           ├── monitor.html
│           ├── app.js
│           └── style.css
├── Dockerfile.camera
├── Dockerfile.server
├── compose.yaml
├── .env.example
├── .gitignore
└── README.md
```

`data/`、`models/*.pt`、`.env`、キャッシュ類は実行時データまたは環境固有ファイルのため提出対象外です。

## 主要ファイルと役割

| ファイル | 役割 |
| --- | --- |
| `src/camera/app/main.py` | USBカメラ取得、YOLO-Pose推論、ローカル描画、転倒判定呼び出し、イベント送信 |
| `src/camera/app/fall_detector.py` | `NORMAL`、`CANDIDATE`、`FALL_DETECTED`、`COOLDOWN` を持つ簡易転倒判定 |
| `src/camera/app/video_buffer.py` | 元フレームのリングバッファと、転倒前後MP4の生成 |
| `src/camera/app/api_client.py` | HTTPXによるカメラからサーバへのPUT送信 |
| `src/server/app/main.py` | FastAPIアプリ、REST API、SSE、動画保存・配信 |
| `src/server/app/database.py` | SQLiteの初期化、検知記録、確認履歴、削除処理 |
| `src/server/app/auth.py` | ログイン、署名付きCookie、CSRF検証 |
| `src/server/app/sse.py` | 単一Uvicorn worker前提のメモリ上SSE配信 |
| `src/server/app/static/monitor.html` | ログイン画面と監視画面のHTML |
| `src/server/app/static/app.js` | EventSource接続、一覧更新、動画再生、PATCH/DELETE |
| `src/server/app/static/style.css` | 監視画面の表示スタイル |
| `compose.yaml` | `server` と `camera` の起動設定 |
| `Dockerfile.camera` | PyTorch CUDA、Ultralytics、OpenCV GUI、FFmpeg環境 |
| `Dockerfile.server` | FastAPI、Uvicorn、SQLite、multipart受信環境 |

## 使用技術と実装箇所

| 技術 | 用途 | 主な実装ファイル |
| --- | --- | --- |
| REST API | 検知登録、動画登録、一覧取得、確認結果更新、削除 | `src/server/app/main.py`, `src/camera/app/api_client.py`, `src/server/app/static/app.js` |
| Server-Sent Events | 転倒検知と動画準備完了の通知 | `src/server/app/sse.py`, `src/server/app/main.py`, `src/server/app/static/app.js` |
| FastAPI / Uvicorn | Webサーバ、API、静的ファイル、動画配信 | `src/server/app/main.py`, `Dockerfile.server` |
| SQLite | 検知記録と確認履歴の保存 | `src/server/app/database.py` |
| HTML5 Video | MP4動画のブラウザ再生 | `src/server/app/static/monitor.html`, `src/server/app/static/app.js` |
| Cookie / CSRF | ログイン状態管理と状態変更APIの保護 | `src/server/app/auth.py`, `src/server/app/main.py`, `src/server/app/static/app.js` |
| OpenCV | USBカメラ取得、ローカル描画、動画書き出し | `src/camera/app/main.py`, `src/camera/app/video_buffer.py` |
| Ultralytics / PyTorch | YOLO-Pose推論とCUDA利用 | `src/camera/app/main.py`, `Dockerfile.camera` |
| HTTPX | cameraからserverへのHTTP送信 | `src/camera/app/api_client.py` |

FastAPI、Uvicorn、OpenCV、Ultralytics、PyTorch、HTTPXは外部ライブラリです。自作部分は、これらを使ったカメラ処理、API設計、DB保存、SSE通知、ブラウザ操作です。

## 処理の流れ

1. `camera` が `CAMERA_DEVICE` のUSBカメラをOpenCVで開く。
2. YOLO-Poseで人物BBoxとキーポイントを取得する。
3. 最大BBoxの人物を転倒判定対象にする。
4. `FallDetector` がBBox横長度や胴体角度を使って簡易判定する。
5. 転倒検知時、`PUT /api/camera/detections/{event_id}` で検知情報を先に送信する。
6. serverはSQLiteの `detections` に記録し、SSEで `fall_detected` を配信する。
7. cameraはリングバッファから転倒前後の元フレームをMP4化する。
8. `PUT /api/camera/detections/{event_id}/video` で動画を後から送信する。
9. serverは動画を保存し、SQLiteの動画状態を `READY` に更新し、SSEで `video_ready` を配信する。
10. ブラウザはEventSourceで通知を受け、一覧を再取得し、HTML5 `video` で動画を再生する。
11. 監視者は `PATCH /api/detections/{event_id}/review` で確認結果を登録する。
12. 必要に応じて `DELETE /api/detections/{event_id}` で記録と保存動画を削除する。

## REST API一覧

| メソッド | パス | 用途 | 主な呼び出し元 |
| --- | --- | --- | --- |
| GET | `/health` | server起動確認 | 手動確認、ヘルスチェック |
| GET | `/monitor` | 監視画面HTML配信 | ブラウザ |
| GET | `/api/events` | SSE接続 | ブラウザ `EventSource` |
| POST | `/api/login` | ブラウザ利用者ログイン | `app.js` |
| POST | `/api/logout` | ログアウト | `app.js` |
| GET | `/api/me` | ログイン状態確認 | `app.js` |
| PUT | `/api/camera/detections/{event_id}` | 検知情報登録 | `camera/app/api_client.py` |
| PUT | `/api/camera/detections/{event_id}/video` | MP4動画アップロード | `camera/app/api_client.py` |
| GET | `/api/detections` | 検知一覧取得 | `app.js` |
| GET | `/api/detections/{event_id}` | 検知詳細取得 | `app.js` |
| GET | `/api/detections/{event_id}/video` | 保存動画取得 | HTML5 `video` |
| PATCH | `/api/detections/{event_id}/review` | `FALL_CONFIRMED` / `NO_FALL` の登録 | `app.js` |
| DELETE | `/api/detections/{event_id}` | 検知記録と保存動画の削除 | `app.js` |

## SSE通知

| イベント名 | 送信タイミング | ブラウザ側の動作 |
| --- | --- | --- |
| `fall_detected` | 新しい検知情報をSQLiteへ保存した後 | 対象イベントを再取得し、一覧を更新する |
| `video_ready` | MP4保存とDB更新が終わった後 | 対象イベントを再取得し、動画再生可能状態へ更新する |

SSEは `src/server/app/sse.py` のメモリ上キューで購読者へ配信します。Redisなどの外部ブローカーは使っていないため、Uvicornは単一worker前提です。ブラウザ側は標準の `EventSource` を使い、接続復帰時には一覧を再取得します。Last-Event-IDを使った高度な再送保証は実装していません。

## SQLite

SQLiteはPython標準ライブラリ `sqlite3` で扱います。初回起動時に `src/server/app/database.py` がテーブルを作成します。

| テーブル | 用途 |
| --- | --- |
| `detections` | 検知ID、カメラID、検知時刻、動画状態、確認状態、動画パスなどを保存 |
| `review_logs` | 確認結果の履歴を保存 |

確認結果を登録する時は、`detections` の現在状態更新と `review_logs` への履歴追加を同じSQLite接続のトランザクション内で行います。

## 認証とCSRF対策

- camera APIは `Authorization: Bearer <CAMERA_API_TOKEN>` を確認する。
- ブラウザ利用者は `/api/login` でログインする。
- ログイン状態は署名付きCookieで保持する。
- イベント一覧、詳細、動画、SSEは未ログイン状態では拒否される。
- PATCH、DELETE、ログアウトでは `X-CSRF-Token` も検証する。
- `/monitor` のHTML自体は配信されるが、未ログイン時はログインフォームだけを表示し、記録や動画は認証済みAPIから取得する。

これは授業課題デモ用の簡易認証です。HTTPS証明書構築や本番運用向けの厳密な認証は対象外です。

## 環境変数

`.env.example` を `.env` にコピーし、`xxxx` の値を実行環境用に置き換えてください。`.env` はGit管理対象外、提出対象外です。

```bash
cp .env.example .env
```

主な環境変数:

| 変数 | 用途 |
| --- | --- |
| `CAMERA_API_TOKEN` | camera API用Bearerトークン |
| `SESSION_SECRET` | 署名付きCookie用の秘密値 |
| `MONITOR_USERNAME` | 監視画面ログインユーザー名 |
| `MONITOR_PASSWORD_HASH` | 監視画面ログインパスワードのPBKDF2ハッシュ |
| `CAMERA_DEVICE` | コンテナ内のカメラデバイス |
| `HOST_CAMERA_DEVICE` | ホスト側のカメラデバイス |
| `CAMERA_WIDTH`, `CAMERA_HEIGHT` | 要求するカメラ解像度 |
| `PROCESS_FPS` | camera処理FPS |
| `MODEL_PATH` | YOLO-Poseモデル名またはパス |
| `POSE_DEVICE` | YOLO推論デバイス。GPUなら `0`、CPUなら `cpu` |
| `SHOW_WINDOW` | OpenCVウィンドウ表示の有効/無効 |
| `DATABASE_PATH` | SQLite保存先 |
| `VIDEO_DIR` | server側動画保存先 |

パスワードハッシュは次のように作成できます。入力したパスワードは画面に表示されません。

```bash
python3 - <<'PY'
import base64
import getpass
import hashlib
import secrets

password = getpass.getpass("monitor password: ").encode("utf-8")
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

## Dockerによる起動方法

永続化ディレクトリを作成します。

```bash
mkdir -p data/camera-output models
```

`.env` を作成し、秘密値を置き換えます。

```bash
cp .env.example .env
```

ホストのUID/GIDが1000以外の場合は、`.env` の `HOST_UID` と `HOST_GID` を `id -u` / `id -g` の値に合わせます。USBカメラのグループIDが44以外の場合は、`VIDEO_GID` を `getent group video | cut -d: -f3` の値に合わせます。

X11表示を使う場合は、現在のローカルユーザーだけを許可します。

```bash
xhost +SI:localuser:$(id -un)
```

起動します。

```bash
docker compose up --build
```

ブラウザで次を開きます。

```text
http://localhost:8000/monitor
```

終了します。

```bash
docker compose down
```

X11許可を戻す場合:

```bash
xhost -SI:localuser:$(id -un)
```

ヘッドレス環境では `.env` で `SHOW_WINDOW=false` を指定します。

## 動作確認方法

静的確認:

```bash
python -m compileall src
docker compose config --quiet
```

server確認:

- `docker compose up --build server`
- `GET /health` が `{"status":"ok"}` を返す
- `/monitor` が取得できる
- 未ログインで `/api/detections` が拒否される
- ログイン後、一覧、動画取得、PATCH、DELETEが実行できる
- Bearerなしのcamera APIが拒否される
- 正しいBearerで検知登録と動画アップロードができる
- SQLiteの `detections` と `review_logs` に保存される
- CSRFなしのPATCH/DELETEが拒否される

camera確認:

- `docker compose up --build camera`
- USBカメラが開ける
- YOLO-Poseモデルが読み込まれる
- CUDAが利用される
- BBoxとスケルトンがローカル画面に表示される
- 通常時は緑、転倒検知中の主要人物だけ赤で表示される
- 転倒検知時に検知情報と動画がserverへ送信される
- `q`、`ESC`、`Ctrl+C` で終了できる

YOLOモデルは通常 `MODEL_PATH=yolo26n-pose.pt` としておき、Ultralyticsの初回自動ダウンロードを利用します。オフライン実行したい場合だけ `models/` に重みを置き、`MODEL_PATH=/app/models/yolo26n-pose.pt` に変更します。

## 制限事項

- 転倒判定は簡易ルールであり、カメラ位置や照明に応じた閾値調整が必要です。
- 複数人物追跡や人物ID管理は行いません。
- SSEは単一Uvicorn worker前提で、再送保証はありません。
- HTTPS、Web Push、Redis、WebSocket、本番用認証は実装していません。
- 動画検証、自動削除、永続再送キューは実装していません。

## 提出対象外ファイル

以下は提出ZIPに含めません。

- `.git/`
- `.env`
- `data/`
- `models/*.pt`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- `*.log`
- `.DS_Store`
- `.vscode/`
- `.idea/`

`data/` にはSQLite実データや生成MP4が入ります。`models/*.pt` は外部モデル重みです。どちらも自作ソースコードではありません。
