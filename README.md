# RaspberryPiとNFCを用いた **入退室管理システム**

このプロジェクトは、**RaspberryPi** と **NFCカードリーダー** を活用した入退室管理システムです。Python の **Flask** フレームワークで Web インターフェースを構築し、ユーザーの入退室状況をリアルタイムで把握し、その履歴を管理します。

---

## 特徴

* **NFCカード認証による入退室**: Suica、Pasmo、おサイフケータイなどの FeliCa 対応カードをリーダーにかざすだけで、入室・退室を自動で記録します。
* **詳細な入退室履歴**: 各ユーザーの入室時刻、退室時刻、滞在時間を SQLite データベースに記録し、後から確認できます。

---

## 動作環境

* **ハードウェア**: Raspberry Pi
* **NFCリーダー**: PaSoRi RC-S380
* **OS**: Raspberry Pi OS

---

# アプリケーションの動作操作方法

アプリケーションの操作は、`systemctl` コマンドを使用して行います。対象のサービス名は `access_app.service` です。

## 起動・停止・再起動

| 操作 | コマンド | 概要 |
| :--- | :--- | :--- |
| **起動 (ON)** | `sudo systemctl start access_app.service` | **gunicorn** プロセスを開始し、Webサーバーを立ち上げます。 |
| **停止 (OFF)** | `sudo systemctl stop access_app.service` | サービスを停止し、ポート **5000** を解放します。 |
| **再起動** | `sudo systemctl restart access_app.service` | コードを修正し、その変更をアプリに反映させる際に使用します。 |

---

## 状態確認とログ監視

### 状態の確認

アプリケーションが正常に動作しているか（`active (running)`）や、エラーで停止していないか（`failed`）を確認します。

```bash
sudo systemctl status access_app.service
