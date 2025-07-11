import serial
import time
import requests
import json
import os
import sys
import math

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, date
from collections import defaultdict

from smartcard.System import readers
from smartcard.util import toHexString
import threading
import calendar


# --- Flaskアプリケーションの設定 ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///access_log.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_super_secret_key_here' # 本番環境ではより複雑なキーにすること

# --- 削除操作用のパスワード設定 ---
# !!! 重要: 本番環境ではこのパスワードを環境変数などから読み込むべきです !!!
DELETE_PASSWORD = "your_secure_delete_password" # ここを実際のパスワードに設定してください


db = SQLAlchemy(app)

# --- データベースモデルの定義 ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    idm = db.Column(db.String(160), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)

    def __repr__(self):
        return f'<User {self.name} ({self.idm})>'

class AccessLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), nullable=False)
    
    user = db.relationship('User', backref=db.backref('access_logs', lazy=True))

    def __repr__(self):
        return f'<AccessLog {self.user.name} {self.status} at {self.timestamp}>'

# --- Arduinoへのシリアル通信設定 ---
ARDUINO_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600

ser = None 

def init_serial_connection():
    global ser
    try:
        ser = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        print(f"Serial: Connected to Arduino on {ARDUINO_PORT}")
        send_to_arduino("System Ready", "Place your card")
    except serial.SerialException as e:
        print(f"Serial Error: Could not connect to Arduino on {ARDUINO_PORT}. {e}")
        ser = None

def read_felica_card_idm():
    try:
        available_readers = readers()
        if not available_readers:
            print("NFC Error: No smart card readers found. Is PaSoRi connected and pcscd running?")
            return None

        pasori_reader = next((r for r in available_readers if 'PaSoRi' in str(r) or 'FeliCa' in str(r)), None)

        if not pasori_reader:
            print("NFC Error: PaSoRi reader not found. Please check its name or connection.")
            return None

        connection = pasori_reader.createConnection()
        connection.connect()
        
        idm_apdu = [0xFF, 0xCA, 0x00, 0x00, 0x00]
        response, sw1, sw2 = connection.transmit(idm_apdu)
        
        if sw1 == 0x90 and sw2 == 0x00:
            idm = toHexString(response).replace(" ", "")
            return idm
        else:
            print(f"NFC Error: Failed to get IDm. SW: {hex(sw1)} {hex(sw2)}")
            return None

    except Exception as e:
        return None

def send_to_arduino(line1, line2):
    if ser and ser.is_open:
        data_to_send = f"{line1}\n{line2}\n"
        try:
            ser.write(data_to_send.encode('utf-8'))
            print(f"Arduino Sent: '{data_to_send.strip()}'")
        except serial.SerialException as e:
            print(f"Arduino Send Error: {e}. Attempting to reconnect...")
            ser.close()
            init_serial_connection()
    else:
        print("Arduino Not Connected. Cannot send data. (Please check serial port setup)")

# --- Discord ウェブフックURLを設定 ---
DISCORD_WEBHOOK_URL = "https://discordapp.com/api/webhooks/1393286247258128404/XjqQlaaFHl3Xfa3zLSuMpk97UR_zlX1uYRzBu3XBiyQPbpOH-exNAY98IN44CCd9oFew"

def send_discord_notification(username, event_type, success=True, details=None):
    if not DISCORD_WEBHOOK_URL:
        print("Discord ウェブフックURLが設定されていません。通知はスキップされます。")
        return

    current_time = datetime.now().strftime("%Y年%m月%d日 %H時%M分%S秒")
    title = ""
    description = ""
    color = 0

    if event_type == '入室':
        title = "アクセスイベント: 入室"
        description = f"✅ {current_time}: **{username}** が **入室** しました。"
        color = 65280
    elif event_type == '退室':
        title = "アクセスイベント: 退室"
        description = f"🚪 {current_time}: **{username}** が **退室** しました。"
        color = 3447003
    elif event_type == 'アクセス試行':
        title = "アクセスイベント: アクセス失敗"
        description = f"❌ {current_time}: **{username}** が **アクセス** に失敗しました。"
        color = 16711680
    elif event_type == 'ユーザー追加':
        title = "ユーザー管理: 新規ユーザー追加"
        description = f"➕ {current_time}: 新しいユーザー **{username}** が追加されました。\nIDm: `{details.get('idm', 'N/A')}`"
        color = 65280
    elif event_type == 'ユーザー更新':
        title = "ユーザー管理: ユーザー情報更新"
        description = f"✏️ {current_time}: ユーザー **{username}** の情報が更新されました。"
        if details:
            if 'old_name' in details and 'new_name' in details and details['old_name'] != details['new_name']:
                description += f"\n名前: `{details['old_name']}` -> `{details['new_name']}`"
            if 'old_idm' in details and 'new_idm' in details and details['old_idm'] != details['new_idm']:
                description += f"\nIDm: `{details['old_idm']}` -> `{details['new_idm']}`"
        color = 16776960
    elif event_type == 'ユーザー削除':
        title = "ユーザー管理: ユーザー削除"
        description = f"🗑️ {current_time}: ユーザー **{username}** が削除されました。"
        color = 16711680
    elif event_type == 'ログ削除':
        title = "ユーザー管理: ログ削除"
        description = f"🧹 {current_time}: ユーザー **{username}** の入退室ログがすべて削除されました。"
        color = 7829367
    else:
        title = "不明なイベント"
        description = f"{current_time}: 不明なイベントが発生しました。"
        color = 7829367

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": [
                    {"name": "ユーザー名", "value": username, "inline": True},
                    {"name": "時刻", "value": current_time, "inline": True},
                    {"name": "結果", "value": "成功" if success else "失敗", "inline": True}
                ],
                "footer": {
                    "text": "Raspberry Pi アクセス制御システム"
                },
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        ]
    }

    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        print(f"Discord 通知を送信しました: {title} - {username}")
    except requests.exceptions.RequestException as e:
        print(f"Discord 通知の送信中にエラーが発生しました: {e}")
        print(f"レスポンス内容: {response.text if 'response' in locals() else 'N/A'}")


# --- カード読み取りと処理のメインループ（別スレッドで実行） ---
def card_reading_loop():
    send_to_arduino("System Starting", "")
    time.sleep(1)
    send_to_arduino("Ready", "Place your card")

    while True:
        idm = read_felica_card_idm()
        if idm:
            with app.app_context():
                user = User.query.filter_by(idm=idm).first()
                if user:
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
                    send_discord_notification(user.name, new_status, success=True)
                else:
                    print(f"Unknown card detected! IDm: {idm}")
                    send_to_arduino("Unknown Card", "Please register")
                    send_discord_notification("不明なユーザー", "アクセス試行", success=False)
            
            time.sleep(5)
            send_to_arduino("Ready", "Place your card")
        time.sleep(0.5)

# --- 滞在時間計算ヘルパー関数 ---
def _calculate_stay_time_for_logs(logs):
    """
    与えられたログリストからユーザーごとの滞在時間を計算します。
    入室と退室がペアになっているセッションのみをカウントします。
    """
    user_sessions_data = defaultdict(lambda: {'name': 'Unknown', 'total_seconds': 0})
    current_entry_times = {} # {user_id: entry_datetime}

    for log in logs:
        user_id = log.user_id
        timestamp = log.timestamp
        status = log.status

        if status == '入室':
            current_entry_times[user_id] = timestamp
        elif status == '退室':
            if user_id in current_entry_times:
                entry_time = current_entry_times.pop(user_id)
                duration = timestamp - entry_time
                user_sessions_data[user_id]['total_seconds'] += duration.total_seconds()
            # else: 対応する入室がない退室ログは無視

    # ユーザー名を取得してデータに追加
    with app.app_context(): # データベース操作のためにコンテキストが必要
        for user_id in user_sessions_data.keys():
            user_obj = User.query.get(user_id)
            if user_obj:
                user_sessions_data[user_id]['name'] = user_obj.name
            else:
                user_sessions_data[user_id]['name'] = f"不明なユーザー (ID:{user_id})"

    # ランキング形式に整形し、秒数を時間:分:秒形式に変換
    ranking_data = []
    for user_id, data in user_sessions_data.items():
        total_seconds = data['total_seconds']
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        formatted_time = f"{hours:02}:{minutes:02}:{seconds:02}"
        ranking_data.append({
            'user_id': user_id,
            'name': data['name'],
            'total_seconds': total_seconds,
            'formatted_time': formatted_time
        })

    # 合計滞在時間で降順にソート
    ranking_data.sort(key=lambda x: x['total_seconds'], reverse=True)
    
    return ranking_data

# --- 月間滞在時間計算関数 ---
def calculate_monthly_stay_time(year, month):
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    logs = AccessLog.query.filter(
        AccessLog.timestamp >= start_date,
        AccessLog.timestamp < end_date
    ).order_by(AccessLog.user_id, AccessLog.timestamp).all()
    
    return _calculate_stay_time_for_logs(logs)

# --- 今週の滞在時間計算関数 ---
def calculate_weekly_stay_time():
    today = datetime.now()
    # 週の始まり（月曜日）を計算
    start_of_week = today - timedelta(days=today.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_week = start_of_week + timedelta(days=7)

    logs = AccessLog.query.filter(
        AccessLog.timestamp >= start_of_week,
        AccessLog.timestamp < end_of_week
    ).order_by(AccessLog.user_id, AccessLog.timestamp).all()

    return _calculate_stay_time_for_logs(logs)

# --- 今までの合計滞在時間計算関数 ---
def calculate_total_stay_time():
    logs = AccessLog.query.order_by(AccessLog.user_id, AccessLog.timestamp).all()
    return _calculate_stay_time_for_logs(logs)

# --- カレンダー表示用のアクセスサマリー取得関数 ---
def get_monthly_access_summary(year, month):
    start_date = datetime(year, month, 1).date()
    if month == 12:
        end_date = datetime(year + 1, 1, 1).date()
    else:
        end_date = datetime(year, month + 1, 1).date()

    # 該当月の全てのログを取得
    logs = AccessLog.query.filter(
        AccessLog.timestamp >= start_date,
        AccessLog.timestamp < end_date
    ).order_by(AccessLog.timestamp).all()

    access_summary = defaultdict(set) # 日付ごとにアクセスしたユーザー名を格納
    user_names_cache = {} # ユーザー名をキャッシュ

    with app.app_context():
        for log in logs:
            log_date = log.timestamp.date()
            if log_date >= start_date and log_date < end_date:
                if log.user_id not in user_names_cache:
                    user_obj = User.query.get(log.user_id)
                    if user_obj:
                        user_names_cache[log.user_id] = user_obj.name
                    else:
                        user_names_cache[log.user_id] = f"不明なユーザー (ID:{log.user_id})"
                access_summary[log_date].add(user_names_cache[log.user_id])
    
    # setをlistに変換してソート
    for date_key in access_summary:
        access_summary[date_key] = sorted(list(access_summary[date_key]))

    return access_summary


# --- Webアプリケーションのルート定義 ---

@app.route('/')
def index():
    access_logs = AccessLog.query.order_by(AccessLog.timestamp.desc()).limit(20).all() # 最新20件

    # カレンダー表示用の年と月を取得
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    # クエリパラメータから年と月を取得（カレンダーナビゲーション用）
    calendar_year = request.args.get('calendar_year', type=int, default=current_year)
    calendar_month = request.args.get('calendar_month', type=int, default=current_month)

    # デバッグ用: テンプレートに渡す値を確認
    print(f"DEBUG: Rendering index.html with calendar_year={calendar_year}, calendar_month={calendar_month}")

    # カレンダーデータ
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY) # 日曜日始まり
    month_calendar = cal.monthdatescalendar(calendar_year, calendar_month)
    access_summary = get_monthly_access_summary(calendar_year, calendar_month)

    # 今月のランキング
    monthly_ranking = calculate_monthly_stay_time(current_year, current_month)

    # 今週のランキング
    weekly_ranking = calculate_weekly_stay_time()

    # 今までの合計ランキング
    total_ranking = calculate_total_stay_time()

    return render_template('index.html', 
                           logs=access_logs,
                           monthly_ranking=monthly_ranking,
                           weekly_ranking=weekly_ranking,
                           total_ranking=total_ranking,
                           current_year=current_year, # 今月のランキング表示用
                           current_month=current_month, # 今月のランキング表示用
                           calendar_year=calendar_year, # カレンダー表示用
                           calendar_month=calendar_month, # カレンダー表示用
                           month_calendar=month_calendar, # カレンダーデータ
                           access_summary=access_summary, # 日ごとのアクセスサマリー
                           datetime=datetime # テンプレートでdatetimeオブジェクトを使用するため
                           )

@app.route('/users', methods=['GET', 'POST'])
def manage_users():
    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id')
        password = request.form.get('password') # パスワードを取得

        if action == 'add':
            idm = request.form['idm'].strip()
            name = request.form['name'].strip()
            if idm and name:
                with app.app_context():
                    existing_user = User.query.filter_by(idm=idm).first()
                    if not existing_user:
                        new_user = User(idm=idm, name=name)
                        db.session.add(new_user)
                        db.session.commit()
                        flash(f'ユーザー "{name}" を追加しました。', 'success')
                        send_discord_notification(name, 'ユーザー追加', success=True, details={'idm': idm})
                    else:
                        flash(f'エラー: IDm "{idm}" は既にユーザー "{existing_user.name}" に登録されています。', 'danger')
            else:
                flash('エラー: IDm と名前は必須です。', 'danger')
        elif action == 'delete' and user_id:
            if password == DELETE_PASSWORD: # パスワードチェック
                with app.app_context():
                    user_to_delete = User.query.get(user_id)
                    if user_to_delete:
                        deleted_name = user_to_delete.name
                        AccessLog.query.filter_by(user_id=user_id).delete()
                        db.session.delete(user_to_delete)
                        db.session.commit()
                        flash(f'ユーザー "{deleted_name}" を削除しました。', 'success')
                        send_discord_notification(deleted_name, 'ユーザー削除', success=True)
                    else:
                        flash('エラー: ユーザーが見つかりませんでした。', 'danger')
            else:
                flash('エラー: パスワードが間違っています。', 'danger')
        # POSTリクエストのどのパスでも最終的にリダイレクトするように変更
        return redirect(url_for('manage_users'))

    # GETリクエストの場合
    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    with app.app_context():
        user = User.query.get_or_404(user_id)

        if request.method == 'POST':
            old_name = user.name
            old_idm = user.idm
            new_name = request.form['name'].strip()
            password = request.form.get('password') # パスワードを取得

            if password == DELETE_PASSWORD: # パスワードチェック
                if new_name:
                    user.name = new_name
                    db.session.commit()
                    flash(f'ユーザー "{old_name}" の情報を更新しました。', 'success')
                    send_discord_notification(new_name, 'ユーザー更新', success=True, 
                                            details={'old_name': old_name, 'new_name': new_name, 'old_idm': old_idm, 'new_idm': user.idm})
                    return redirect(url_for('manage_users'))
                else:
                    flash('エラー: 名前は必須です。', 'danger')
            else:
                flash('エラー: パスワードが間違っています。', 'danger')
        
        return render_template('edit_user.html', user=user)

@app.route('/users/clear_logs/<int:user_id>', methods=['POST'])
def clear_user_logs(user_id):
    password = request.form.get('password') # パスワードを取得
    if password == DELETE_PASSWORD: # パスワードチェック
        with app.app_context():
            user = User.query.get(user_id)
            if user:
                num_deleted = AccessLog.query.filter_by(user_id=user.id).delete()
                db.session.commit()
                flash(f'ユーザー "{user.name}" の入退室ログ {num_deleted} 件を削除しました。', 'success')
                send_discord_notification(user.name, 'ログ削除', success=True, details={'deleted_count': num_deleted})
            else:
                flash('エラー: ログを削除するユーザーが見つかりませんでした。', 'danger')
    else:
        flash('エラー: パスワードが間違っています。', 'danger')
    return redirect(url_for('manage_users'))

@app.route('/ranking')
def show_ranking():
    current_year = datetime.now().year
    current_month = datetime.now().month

    year = request.args.get('year', type=int, default=current_year)
    month = request.args.get('month', type=int, default=current_month)

    if not (1 <= month <= 12):
        month = current_month
    
    with app.app_context():
        ranking = calculate_monthly_stay_time(year, month)
    
    return render_template('ranking.html', ranking=ranking, selected_year=year, selected_month=month, datetime=datetime)


# --- アプリケーション起動時の処理 ---
if __name__ == '__main__':
    init_serial_connection()

    with app.app_context():
        db.create_all()

        if not User.query.first():
            print("Adding initial users...")
            user1 = User(idm="F637CF05", name="Soma Taniguchi")
            db.session.add(user1)
            db.session.commit()
            print("Initial users added.")

    reader_thread = threading.Thread(target=card_reading_loop, daemon=True)
    reader_thread.start()

    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        print(f"Flask App Error: {e}")
        if ser and ser.is_open:
            ser.close()
        sys.exit(1)
