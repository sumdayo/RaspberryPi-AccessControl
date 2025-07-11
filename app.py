import serial
import time
import requests
import json
import datetime # 日時情報を取得するために必要
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime # ここでdatetimeクラスをインポートしている

from smartcard.System import readers
from smartcard.util import toHexString
import threading
import os
import sys # sysモジュールをインポート

# --- Flaskアプリケーションの設定 ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///access_log.db' # データベースファイル名
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # SQLAlchemyイベントトラッキングを無効化
db = SQLAlchemy(app)

# --- データベースモデルの定義 ---
# ユーザー情報テーブル
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    idm = db.Column(db.String(160), unique=True, nullable=False) # FeliCaのIDm
    name = db.Column(db.String(80), nullable=False)
    # 必要に応じて、さらにカラムを追加可能 (例: role, department)

    def __repr__(self):
        return f'<User {self.name} ({self.idm})>'

# 入退室履歴テーブル
class AccessLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), nullable=False) # '入室' or '退室'
    
    user = db.relationship('User', backref=db.backref('access_logs', lazy=True))

    def __repr__(self):
        return f'<AccessLog {self.user.name} {self.status} at {self.timestamp}>'

# --- Arduinoへのシリアル通信設定 ---
# Arduinoのシリアルポートを正確に指定してください。
# Raspberry Piでは通常 '/dev/ttyACM0' や '/dev/ttyUSB0' です。
# 'ls /dev/tty*' コマンドで確認できます。
ARDUINO_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600

# グローバル変数としてシリアルポートオブジェクトを保持
ser = None 

# シリアルポートの初期化関数
def init_serial_connection():
    global ser
    try:
        ser = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=1)
        time.sleep(2) # Arduinoのリセット時間を待つ
        print(f"Serial: Connected to Arduino on {ARDUINO_PORT}")
        send_to_arduino("System Ready", "Place your card")
    except serial.SerialException as e:
        print(f"Serial Error: Could not connect to Arduino on {ARDUINO_PORT}. {e}")
        ser = None # 接続失敗時はNoneにする

# --- PaSoRiリーダーとFeliCaカード読み取り関数 ---
def read_felica_card_idm():
    try:
        available_readers = readers()
        # print(f"Available readers: {available_readers}") # デバッグ用

        if not available_readers:
            print("NFC Error: No smart card readers found. Is PaSoRi connected and pcscd running?")
            return None

        # PaSoRiリーダーを選択 (名前でフィルタリング)
        # あなたの環境でのPaSoRiの正確な名前（pcsc_scanで確認）に合わせてください
        pasori_reader = next((r for r in available_readers if 'PaSoRi' in str(r) or 'FeliCa' in str(r)), None)

        if not pasori_reader:
            print("NFC Error: PaSoRi reader not found. Please check its name or connection.")
            return None

        # print(f"Using reader: {pasori_reader}") # デバッグ用

        connection = pasori_reader.createConnection()
        connection.connect()
        
        # IDmを取得するPCSCコマンド
        idm_apdu = [0xFF, 0xCA, 0x00, 0x00, 0x00] # GET DATA (IDm) command
        response, sw1, sw2 = connection.transmit(idm_apdu)
        
        if sw1 == 0x90 and sw2 == 0x00:
            idm = toHexString(response).replace(" ", "")
            return idm
        else:
            print(f"NFC Error: Failed to get IDm. SW: {hex(sw1)} {hex(sw2)}")
            return None

    except Exception as e:
        # カードが離された、または読み取りエラーの場合もここにくるので、エラー表示はデバッグ時のみ推奨
        # print(f"NFC Read Exception: {e}") 
        return None

# --- Arduinoへデータを送信するヘルパー関数 ---
def send_to_arduino(line1, line2):
    if ser and ser.is_open: # serが有効かつ開いていることを確認
        data_to_send = f"{line1}\n{line2}\n"
        try:
            ser.write(data_to_send.encode('utf-8'))
            print(f"Arduino Sent: '{data_to_send.strip()}'")
        except serial.SerialException as e:
            print(f"Arduino Send Error: {e}. Attempting to reconnect...")
            ser.close() # エラー時は一度閉じる
            init_serial_connection() # 再接続を試みる
    else:
        print("Arduino Not Connected. Cannot send data. (Please check serial port setup)")

# --- Discord ウェブフックURLを設定 ---
# あなたが提供したDiscordウェブフックURLをここに設定します
DISCORD_WEBHOOK_URL = "https://discordapp.com/api/webhooks/1393251548645167265/Z2lwsYQtL4J0rim60RyuORA2Kq30FN1Uap7NkC4jxDTHLteSm1CXwW4JsyqBEaJQd1x2" 

def send_discord_notification(username, event_type, success=True):
    """
    Discord に入退室通知を送信する関数。
    :param username: アクセスを試みたユーザー名
    :param event_type: '入室' または '退室'
    :param success: アクセスが成功したか否か (True/False)
    """
    if not DISCORD_WEBHOOK_URL:
        print("Discord ウェブフックURLが設定されていません。通知はスキップされます。")
        return

    # 修正: datetime.datetime.now() から datetime.now() に変更
    current_time = datetime.now().strftime("%Y年%m月%d日 %H時%M分%S秒")

    if success:
        message_content = f"✅ {current_time}: **{username}** が **{event_type}** しました。"
        color = 65280  # 緑色 (成功)
    else:
        message_content = f"❌ {current_time}: **{username}** が **{event_type}** に失敗しました。"
        color = 16711680 # 赤色 (失敗)

    # Discord Embed の形式で送信（より見やすくするため）
    payload = {
        "embeds": [
            {
                "title": f"アクセスイベント: {event_type}",
                "description": message_content,
                "color": color,
                "fields": [
                    {"name": "ユーザー名", "value": username, "inline": True},
                    {"name": "時刻", "value": current_time, "inline": True},
                    {"name": "結果", "value": "成功" if success else "失敗", "inline": True}
                ],
                "footer": {
                    "text": "Raspberry Pi アクセス制御システム"
                },
                # 修正: datetime.datetime.utcnow().isoformat() から datetime.utcnow().isoformat() に変更
                "timestamp": datetime.utcnow().isoformat() + "Z" # UTC時間でISOフォーマット
            }
        ]
    }

    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers)
        response.raise_for_status() # HTTPエラーが発生した場合に例外を発生させる
        print(f"Discord 通知を送信しました: {message_content}")
    except requests.exceptions.RequestException as e:
        print(f"Discord 通知の送信中にエラーが発生しました: {e}")
        print(f"レスポンス内容: {response.text if 'response' in locals() else 'N/A'}")


# --- カード読み取りと処理のメインループ（別スレッドで実行） ---
def card_reading_loop():
    send_to_arduino("System Starting", "") # 起動メッセージ
    time.sleep(1)
    send_to_arduino("Ready", "Place your card")

    while True:
        idm = read_felica_card_idm()
        if idm:
            with app.app_context(): # データベース操作はFlaskのコンテキスト内で
                user = User.query.filter_by(idm=idm).first()
                if user:
                    # ユーザーが見つかった場合、入退室を切り替える
                    last_log = AccessLog.query.filter_by(user_id=user.id)\
                                 .order_by(AccessLog.timestamp.desc()).first()
                    
                    if last_log and last_log.status == '入室':
                        new_status = '退室'
                    else:
                        new_status = '入室'
                    
                    log_entry = AccessLog(user_id=user.id, status=new_status)
                    db.session.add(log_entry)
                    db.session.commit()

                    print(f"Access recorded: {user.name} - {new_status}")
                    send_to_arduino(user.name, new_status)
                    # --- Discord通知をここに追加 (成功時) ---
                    send_discord_notification(user.name, new_status, success=True)
                else:
                    # 未登録ユーザー
                    print(f"Unknown card detected! IDm: {idm}")
                    send_to_arduino("Unknown Card", "Please register")
                    # --- Discord通知をここに追加 (未登録ユーザー時) ---
                    send_discord_notification("不明なユーザー", "アクセス試行", success=False)
            
            time.sleep(5) # LCD表示時間 + 冷却期間 (連続読み取り防止)
            send_to_arduino("Ready", "Place your card") # 次の読み取りを促すメッセージ
        time.sleep(0.5) # ポーリング間隔 (カードがなくても一定間隔でチェック)

# --- Webアプリケーションのルート定義 ---

# ホームページ（入退室履歴表示）
@app.route('/')
def index():
    access_logs = AccessLog.query.order_by(AccessLog.timestamp.desc()).limit(20).all() # 最新20件
    return render_template('index.html', logs=access_logs)

# ユーザー管理ページ
@app.route('/users', methods=['GET', 'POST'])
def manage_users():
    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id')

        if action == 'add':
            idm = request.form['idm'].strip() # 前後の空白を削除
            name = request.form['name'].strip()
            if idm and name:
                # 既に登録済みのIDmでないかチェック
                existing_user = User.query.filter_by(idm=idm).first()
                if not existing_user:
                    new_user = User(idm=idm, name=name)
                    db.session.add(new_user)
                    db.session.commit()
                else:
                    print(f"Warning: IDm {idm} already exists for user {existing_user.name}")
        elif action == 'delete' and user_id:
            user_to_delete = User.query.get(user_id)
            if user_to_delete:
                # ユーザーに関連するログも削除（Cascade deleteを設定していない場合）
                AccessLog.query.filter_by(user_id=user_id).delete()
                db.session.delete(user_to_delete)
                db.session.commit()
        return redirect(url_for('manage_users'))

    users = User.query.all()
    return render_template('users.html', users=users)

# --- アプリケーション起動時の処理 ---
if __name__ == '__main__':
    # シリアル接続の初期化を試みる (接続失敗してもアプリは起動)
    init_serial_connection()

    # Flaskアプリケーションコンテキストを手動でプッシュ
    # これにより、db.create_all()などの操作がアプリの起動時に実行可能になる
    with app.app_context():
        db.create_all() # 存在しないテーブルを作成

        # テストユーザー（一度だけ実行）
        # データベースが空の場合のみ実行される
        if not User.query.first():
            print("Adding initial users...")
            user1 = User(idm="F637CF05", name="Soma Taniguchi") # ここをあなたのIDmに合わせる！
            # user2 = User(idm="YOUR_SECOND_IDM_HERE", name="別のユーザー") # 必要なら追加
            db.session.add(user1)
            # db.session.add(user2)
            db.session.commit()
            print("Initial users added.")

    # カード読み取りループを別スレッドで開始
    reader_thread = threading.Thread(target=card_reading_loop, daemon=True)
    reader_thread.start()

    # Flaskアプリを起動（外部からアクセス可能にするためhost='0.0.0.0'）
    # デバッグ中はdebug=TrueでもOKだが、本番運用ではFalse推奨
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        print(f"Flask App Error: {e}")
        # アプリが終了する前にシリアルポートを閉じる
        if ser and ser.is_open:
            ser.close()
        sys.exit(1) # エラー終了
