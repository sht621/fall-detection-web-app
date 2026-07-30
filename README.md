# fall-detection-web-app

## 1．アプリ概要

USBカメラ映像から人物の転倒を検知し，ブラウザへ通知するWebアプリケーションです．
カメラ側ではYOLO-Poseによる姿勢推定とルールベース処理で転倒を判定し，検知情報を先にサーバへ送信した後，転倒前後の動画を生成して送信します．
ブラウザでは，検知記録と動画の確認，転倒または誤検知の登録，記録の削除を行います．

## 2．主要ファイル

```text
src/
├── camera/app/
│   ├── main.py            カメラ取得，姿勢推定，転倒検知処理
│   ├── fall_detector.py   BBox横長と胴体傾きによる転倒判定
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

OpenCV画面を表示する場合だけ，現在のローカルユーザーにX11アクセスを許可します．

```bash
xhost +SI:localuser:$(id -un)
```

Docker Composeで起動します．

```bash
docker compose up --build -d
```

ブラウザで次を開きます．

```text
http://<WebサーバのIPアドレス>:8000/monitor
```

終了します．

```bash
docker compose down
```
