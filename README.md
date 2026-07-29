# fall-detection-web-app

## 1．アプリ概要

このリポジトリは，USBカメラ映像を使った転倒検知イベント通知・映像確認Webシステムです．
カメラ側はOpenCVでUSBカメラ映像を取得し，YOLO-Poseで人物領域とキーポイントを取得します．
取得した姿勢情報をルールベース処理へ渡し，転倒条件を満たした場合に検知情報を先にWebサーバへ送信します．
その後，リングバッファから転倒前後の動画を生成し，同じ検知記録に紐づけて送信します．
ブラウザ利用者は通知を受け取り，動画確認，確認結果登録，検知記録と動画の削除を行います．

## 2．主要ファイル

```text
src/
├── camera/app/
│   ├── main.py            カメラ取得，姿勢推定，転倒検知処理
│   ├── fall_detector.py   ルールベースの転倒判定
│   ├── video_buffer.py    リングバッファとMP4生成
│   └── api_client.py      検知情報と動画の送信
└── server/app/
    ├── main.py            FastAPIとREST API
    ├── database.py        SQLiteへの保存
    ├── auth.py            Cookie認証とCSRF検証
    ├── sse.py             SSE通知
    └── static/            ブラウザ画面
```

## 3．使用技術と実装箇所

| 技術 | 用途 | 主な実装ファイル |
| --- | --- | --- |
| REST API | 検知情報，動画，確認結果，削除操作をHTTPで扱う | `src/server/app/main.py`，`src/camera/app/api_client.py`，`src/server/app/static/app.js` |
| Server-Sent Events | 転倒検知と動画準備完了をブラウザへ通知する | `src/server/app/sse.py`，`src/server/app/main.py`，`src/server/app/static/app.js` |
| FastAPI | REST API，SSE，静的ファイル，動画配信を同じサーバで提供する | `src/server/app/main.py` |
| SQLiteとトランザクション | 検知記録と確認履歴を保存し，確認結果更新と履歴追加を同時に反映する | `src/server/app/database.py` |
| HTML5 Video | サーバに保存したMP4動画をブラウザで再生する | `src/server/app/static/monitor.html`，`src/server/app/static/app.js` |
| CookieとCSRF | ログイン状態を保持し，更新や削除操作を保護する | `src/server/app/auth.py`，`src/server/app/main.py`，`src/server/app/static/app.js` |

OpenCV，Ultralytics，PyTorch，HTTPXは，カメラ取得，姿勢推定，GPU利用，HTTP送信のために使用しています．

## 4．主なAPI

| メソッド | パス | 用途 |
| --- | --- | --- |
| PUT | `/api/camera/detections/{event_id}` | 検知情報の登録 |
| PUT | `/api/camera/detections/{event_id}/video` | 動画の登録 |
| GET | `/api/events` | SSE通知 |
| GET | `/api/detections` | 検知記録の取得 |
| GET | `/api/detections/{event_id}/video` | 動画の取得 |
| PATCH | `/api/detections/{event_id}/review` | 確認結果の登録 |
| DELETE | `/api/detections/{event_id}` | 検知記録と動画の削除 |

ブラウザ利用者のログイン状態は署名付きCookieで維持します．
確認結果登録や削除などの更新操作では，Cookieに加えてCSRFトークンを検証します．

## 5．起動方法

提出物に含まれる `.env` の `xxxx` を実行環境用の値へ変更します．
主に変更する環境変数は次のとおりです．

| 変数 | 用途 |
| --- | --- |
| `CAMERA_API_TOKEN` | カメラ側API用Bearerトークン |
| `SESSION_SECRET` | 署名付きCookie用の秘密値 |
| `MONITOR_USERNAME` | ブラウザ利用者のログインユーザー名 |
| `MONITOR_PASSWORD_HASH` | ブラウザ利用者のログインパスワードハッシュ |
| `HOST_CAMERA_DEVICE` | ホスト側のUSBカメラデバイス |
| `SHOW_WINDOW` | OpenCV画面表示の有効または無効 |

`MONITOR_PASSWORD_HASH` は次のコマンドで生成できます．

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

保存ディレクトリを作成します．

```bash
mkdir -p data/camera-output models
```

OpenCV画面を表示する場合だけ，現在のローカルユーザーにX11アクセスを許可します．

```bash
xhost +SI:localuser:$(id -un)
```

Docker Composeで起動します．

```bash
docker compose up --build
```

ブラウザで次を開きます．

```text
http://localhost:8000/monitor
```

終了します．

```bash
docker compose down
```
