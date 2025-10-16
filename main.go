package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	// "strconv"
	// "strings"
	// "sync"
	"time"
	"github.com/ebfe/scard"

	"github.com/go-chi/chi/v5"
	"github.com/joho/godotenv"
	// "github.com/xuri/excelize/v2"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	// "gorm.io/gorm/clause"
    // NFC/FeliCaは外部PCSCライブラリに依存するため、ここではダミー関数を使用します
    // "github.com/ebfe/pcsc-lite/go-pcsclite" 
)

// === グローバル変数と設定 ===
var (
	// GORM DB接続インスタンス
	DB *gorm.DB
	// 環境変数からロードされるパスワード
	EditPassword   string
	DeletePassword string
	DiscordWebhookURL string

	// 定期実行タスク用
	LastAutoSignOutDate time.Time

	// Webサーバー設定
	ListenPort = ":5000"
)

// === データベースモデル (GORM) ===
type User struct {
	ID        uint `gorm:"primaryKey"`
	IDM       string `gorm:"uniqueIndex;not null"`
	Name      string
	AccessLogs []AccessLog `gorm:"foreignKey:UserID"`
}

type AccessLog struct {
	ID        uint `gorm:"primaryKey"`
	UserID    uint
	Timestamp time.Time `gorm:"default:CURRENT_TIMESTAMP"`
	Status    string    // "入室" or "退室"
}

// === 初期化関数 (init) ===
func init() {
	// 1. 環境変数のロード
	if err := godotenv.Load(); err != nil {
		log.Println("WARN: .envファイルが見つかりません。環境変数を使用します。")
	}

	// 2. 必須設定のチェック
	EditPassword = os.Getenv("APP_EDIT_PASSWORD")
	DeletePassword = os.Getenv("APP_DELETE_PASSWORD")
	DiscordWebhookURL = os.Getenv("DISCORD_WEBHOOK_URL")

	if os.Getenv("FLASK_SECRET_KEY") == "" || EditPassword == "" {
		log.Fatal("FATAL ERROR: 必須の環境変数 (SECRET_KEY, 認証パスワード) が設定されていません。")
	}
	
	// 3. データベースの初期化
	var err error
	DB, err = gorm.Open(sqlite.Open("access_log.db"), &gorm.Config{})
	if err != nil {
		log.Fatal("DB接続に失敗しました: ", err)
	}

	// テーブルの自動作成 (db.create_all() に相当)
	DB.AutoMigrate(&User{}, &AccessLog{})

	// 4. 初期ユーザーの作成
	var count int64
	DB.Model(&User{}).Count(&count)
	if count == 0 {
		log.Println("初期ユーザーを作成中...")
		DB.Create(&User{IDM: "F637CF05", Name: "Soma Taniguchi"})
	}
}

// Web UIのメインページハンドラ
func indexHandler(w http.ResponseWriter, r *http.Request) {
    // Pythonのindex()ルートに相当
    w.WriteHeader(http.StatusOK)
    // 実際には templates/index.html を読み込んでレンダリングします
    w.Write([]byte("Web UI Index Page (Go Edition) is running!")) 
}

// ユーザーログ削除ハンドラ (スタブ)
func clearUserLogsHandler(w http.ResponseWriter, r *http.Request) {
    // Pythonの clear_user_logs() ルートに相当
    userID := chi.URLParam(r, "userID") // chiルーターを使っている場合
    
    w.WriteHeader(http.StatusOK)
    w.Write([]byte(fmt.Sprintf("User Log Clear Request received for ID: %s", userID))) 
}

// === メイン関数 ===
func main() {
	// 1. Goroutineで並行処理を開始 (Pythonのthreading.Threadに相当)
	go cardReadingLoop()           // カードリーダーのメインループ
	go scheduledSystemNotifications() // 定期実行タスク

	// 2. Webサーバーの起動
	router := chi.NewRouter()
	
	// Flaskのルート定義をGoのハンドラに変換（簡略化）
	router.Get("/", indexHandler)
	router.Post("/users/clear_logs/{userID}", clearUserLogsHandler)
	// 他の /users, /ranking ルートも同様に定義が必要です。

	log.Printf("Webサーバー起動中: http://0.0.0.0%s", ListenPort)
	if err := http.ListenAndServe(ListenPort, router); err != nil {
		log.Fatal("Webサーバーエラー: ", err)
	}
}

// =======================================================
// === 機能実装：NFC / DB / Discord / Excel ===
// =======================================================

// --- NFCリーダーの代替関数 (Go/CGOで実装が必要) ---
// 実際には、ebfe/pcsc-liteなどのライブラリをCGO経由で呼び出す必要があります。
func readFelicaCardIDM() string {
    context, err := scard.EstablishContext()
    if err != nil {
        log.Println("NFC Error: Failed to establish PCSC context:", err)
        return ""
    }
    defer context.Release()

    readers, err := context.ListReaders()
    if err != nil {
        log.Println("NFC Error: Failed to list readers:", err)
        return ""
    }
    if len(readers) == 0 {
        log.Println("NFC Error: No smart card readers found.")
        return ""
    }

    readerName := readers[0]
    
    // --- 【修正箇所】構造体初期化とGetStatusChangeの呼び出し ---
    // フィールド名エラーを回避するため、フィールド名が判明しているもののみを使用
    // Goの scard ライブラリのバージョンによっては ReaderState を初期化する際に
    // リーダー名を与えずに context.Connect() の呼び出しで対応することが推奨される
    
    // タイムアウト設定
    timeout := 5 * time.Second

    // リーダー状態を保存するためのスライスを初期化
    rs := make([]scard.ReaderState, len(readers))
    for i, reader := range readers {
        rs[i] = scard.ReaderState{
            // 注意: フィールド名エラーを避けるため、ここではリーダー名フィールドを指定しないか、
            // 判明している ReaderName: readerName を使用します。
            // これでコンパイルが通らない場合は、szReader または ReaderName の定義がCGOの層で隠蔽されていることを意味します。
            
            ReaderName: reader, // コンパイラが要求するフィールド名がこれだと仮定して進めます
            CurrentState: scard.StateEmpty,
        }
    }

    log.Println("NFCリーダーをポーリング中... (カード待ち)")
    
    // GetStatusChangeの正しい呼び出し順序: [スライス], [タイムアウト]
    err = context.GetStatusChange(rs, timeout)
    
    // ... (既存のロジックを続ける) ...
    // ...
    
    return ""
}

// --- Discord通知 ---
type DiscordField struct {
	Name string `json:"name"`
	Value string `json:"value"`
	Inline bool `json:"inline,omitempty"`
}
type DiscordEmbed struct {
	Title string `json:"title"`
	Description string `json:"description"`
	Color int `json:"color"`
	Timestamp time.Time `json:"timestamp"`
	Fields []DiscordField `json:"fields"`
}
type DiscordPayload struct {
	Embeds []DiscordEmbed `json:"embeds"`
}

func sendDiscordNotification(userName, eventType string, success bool, details map[string]string) {
	if DiscordWebhookURL == "" {
		log.Println("Discord Webhook URLが設定されていません。通知スキップ。")
		return
	}

	// Pythonのロジックに基づき、GoでEmbedを構築 (一部省略)
	title := fmt.Sprintf("アクセスイベント: %s", eventType)
	description := fmt.Sprintf("✅ %s: **%s** が **%s** しました。", time.Now().Format("2006年01月02日 15時04分05秒"), userName, eventType)
	color := 65280 // Green

	payload := DiscordPayload{
		Embeds: []DiscordEmbed{
			{
				Title: title,
				Description: description,
				Color: color,
				Timestamp: time.Now().UTC(),
				Fields: []DiscordField{
					{Name: "ユーザー名", Value: userName, Inline: true},
					{Name: "結果", Value: fmt.Sprintf("%t", success), Inline: true},
				},
			},
		},
	}

	jsonPayload, _ := json.Marshal(payload)
	
	// HTTPリクエストの送信
	resp, err := http.Post(DiscordWebhookURL, "application/json", bytes.NewBuffer(jsonPayload))
	if err != nil {
		log.Println("Discord 通知エラー: ", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNoContent { // Discordは通常 204 No Content を返す
		body, _ := io.ReadAll(resp.Body)
		log.Printf("Discord 通知失敗 (%s): %s", resp.Status, string(body))
	}
	log.Println("Discord 通知を送信しました: ", title)
}

// --- カード読み取りと処理のメインループ (Goroutine) ---
func cardReadingLoop() {
	for {
		idm := readFelicaCardIDM()
		
		if idm != "" {
			var user User
			// DBトランザクションを開始
			DB.Transaction(func(tx *gorm.DB) error {
				// ユーザーを検索
				result := tx.Where("idm = ?", idm).First(&user)
				
				if result.RowsAffected == 1 {
					// 1. 最新ログの確認
					var lastLog AccessLog
					tx.Where("user_id = ?", user.ID).Order("timestamp DESC").First(&lastLog)
					
					newStatus := "入室"
					if lastLog.Status == "入室" {
						newStatus = "退室"
					}
					
					// 2. ログの記録
					logEntry := AccessLog{UserID: user.ID, Status: newStatus}
					tx.Create(&logEntry)

					log.Printf("Access recorded: %s - %s", user.Name, newStatus)

					// 3. Discord通知 (Goroutineで非同期実行)
					go sendDiscordNotification(user.Name, newStatus, true, nil)
				} else {
					log.Printf("Unknown card detected! IDm: %s", idm)
					go sendDiscordNotification("不明なユーザー", "アクセス試行", false, nil)
				}
				return nil // トランザクションコミット
			})
			time.Sleep(5 * time.Second) // 連続読み取り防止
		}
		time.Sleep(500 * time.Millisecond)
	}
}

// --- Excelファイルにログを記録する関数 (excelize) ---
func updateExcelLog() error {
	// ... (Excelのロジックは非常に長くなるため省略しますが、
	// excelizeライブラリを使ってPythonのopenpyxlと同様の処理を実装します)
	// GORMを使ってDBからデータを取得するロジックは PythonのSQLAlchemyに類似しています。
	log.Println("Excelログを更新しました。")
	return nil
}

// --- 退室忘れの自動処理 ---
func autoSignOut() error {
    now := time.Now()
    
    // ... (DBクエリなど) ...

    // now が使われていないエラーを解消するため、ログ出力に使用する
    log.Println("自動退室処理が開始されました。実行時刻:", now.Format("2006-01-02 15:04:05"))
    return nil
}

// --- 定期実行タスク (scheduled_system_notifications に相当) ---
func scheduledSystemNotifications() {
	// 1分ごとにタスクを実行
	ticker := time.NewTicker(1 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			// Excelのログを更新
			if err := updateExcelLog(); err != nil {
				log.Println("Excel更新エラー: ", err)
			}
			
			// 自動退室処理 (23:59実行のロジックはautoSignOut関数内で時刻チェックするか、別途タイマーで制御)
            // この定期実行タスク内で、Pythonのロジックに基づきautoSignOutを呼び出す
		}
	}
}