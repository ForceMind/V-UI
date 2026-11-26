// Package main is the entry point for the 3x-ui web panel application.
// It initializes the database, web server, and handles command-line operations for managing the panel.
package main

import (
	"context"
	_ "embed"
	"flag"
	"fmt"
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	_ "unsafe"

	"github.com/mhsanaei/3x-ui/v3/internal/config"
	"github.com/mhsanaei/3x-ui/v3/internal/crypto/nodetoken"
	"github.com/mhsanaei/3x-ui/v3/internal/database"
	"github.com/mhsanaei/3x-ui/v3/internal/logger"
	"github.com/mhsanaei/3x-ui/v3/internal/sub"
	"github.com/mhsanaei/3x-ui/v3/internal/tunnelmonitor"
	"github.com/mhsanaei/3x-ui/v3/internal/util/sys"
	"github.com/mhsanaei/3x-ui/v3/internal/web"
	"github.com/mhsanaei/3x-ui/v3/internal/web/global"
	"github.com/mhsanaei/3x-ui/v3/internal/web/service"
	"github.com/mhsanaei/3x-ui/v3/internal/web/service/panel"
	"github.com/mhsanaei/3x-ui/v3/internal/web/service/tgbot"

	"github.com/joho/godotenv"
	"github.com/op/go-logging"
)

// cliFallbackTokenName is the single token the CLI regenerates, so `-getApiToken`
// cannot accumulate admin-equivalent credentials that are never revoked.
const cliFallbackTokenName = "cli-fallback"

// installTokenName is minted once on a panel with no tokens and is deliberately
// not the rotated slot, so the credential the installer recorded keeps working.
const installTokenName = "install"

// initNodeTokenCrypto loads the process codec, preferring the key file over
// the environment and failing closed when an enabled policy lacks a key.
func initNodeTokenCrypto() error {
	mode, err := nodetoken.ParseMode(config.GetNodeTokenEncryptionMode())
	if err != nil {
		return err
	}
	if mode == nodetoken.ModeOff {
		c, _ := nodetoken.NewCodec(nodetoken.ModeOff, nil)
		nodetoken.Init(c)
		return nil
	}
	ring, ferr := (nodetoken.FileKeySource{Path: config.GetNodeTokenKeyFile()}).Load()
	if ferr != nil {
		var eerr error
		if ring, eerr = (nodetoken.EnvKeySource{Var: config.GetNodeTokenKeyEnv()}).Load(); eerr != nil {
			return fmt.Errorf("加载节点令牌密钥失败：文件：%w；环境变量：%w", ferr, eerr)
		}
	}
	c, err := nodetoken.NewCodec(mode, ring)
	if err != nil {
		return err
	}
	nodetoken.Init(c)
	// The CLI runs before package logger initialization, so use log.Printf.
	log.Printf("节点令牌加密已启用（模式=%s，当前密钥=%s）", config.GetNodeTokenEncryptionMode(), c.ActiveKeyID())
	return nil
}

// runWebServer initializes and starts the web server for the 3x-ui panel.
func runWebServer() {
	log.Printf("正在启动 %v %v", config.GetName(), config.GetPanelVersion())

	switch config.GetLogLevel() {
	case config.Debug:
		logger.InitLogger(logging.DEBUG)
	case config.Info:
		logger.InitLogger(logging.INFO)
	case config.Notice:
		logger.InitLogger(logging.NOTICE)
	case config.Warning:
		logger.InitLogger(logging.WARNING)
	case config.Error:
		logger.InitLogger(logging.ERROR)
	default:
		log.Fatalf("未知日志级别：%v", config.GetLogLevel())
	}

	_ = godotenv.Load()

	for _, line := range sys.ApplyMemoryTuning() {
		logger.Info(line)
	}

	if os.Getenv("XUI_PPROF") == "true" {
		go func() {
			logger.Info("pprof 性能分析服务正在监听 127.0.0.1:6060")
			if err := http.ListenAndServe("127.0.0.1:6060", nil); err != nil {
				logger.Warning("pprof 服务已停止：", err)
			}
		}()
	}

	if err := initNodeTokenCrypto(); err != nil {
		log.Fatalf("初始化节点令牌加密失败：%v", err)
	}

	err := database.InitDB(config.GetDBPath())
	if err != nil {
		log.Fatalf("初始化数据库失败：%v", err)
	}
	// Created here, not in InitDB: a CLI call's output is often piped away, and
	// this log is the one place an operator reads the first login from.
	username, password, err := database.EnsureFirstUser()
	if err != nil {
		log.Fatalf("创建面板首个用户失败：%v", err)
	}
	if password != "" {
		log.Printf("尚无面板用户，已自动创建。用户名：%s  密码：%s（仅显示一次，请在面板设置中修改）", username, password)
	}

	server := web.NewServer(sub.NewLinkProvider())
	global.SetWebServer(server)
	err = server.Start()
	if err != nil {
		log.Fatalf("启动管理服务失败：%v", err)
	}

	sub.SetDistFS(web.EmbeddedDist())
	subServer := sub.NewServer()
	global.SetSubServer(subServer)
	err = subServer.Start()
	if err != nil {
		log.Fatalf("启动订阅服务失败：%v", err)
	}

	sigCh := make(chan os.Signal, 8)
	// Trap shutdown signals
	signal.Notify(sigCh, syscall.SIGHUP, syscall.SIGTERM, sys.SIGUSR1, os.Interrupt)
	global.SetRestartHook(func() {
		select {
		case sigCh <- syscall.SIGHUP:
		default:
		}
	})

	var stopTunnelHealthMonitor context.CancelFunc
	monitorCfg := tunnelmonitor.ConfigFromEnv()
	if monitorCfg.Enabled {
		if monitorCfg.ProxyURL == "" {
			logger.Warning("隧道健康监控已启用，但未设置 XUI_TUNNEL_HEALTH_PROXY：探测仅反映主机连通性，无法反映 Xray 隧道状态；探测失败会重启 Xray，但无法修复主机网络问题")
		}

		monitorCtx, cancel := context.WithCancel(context.Background())
		stopTunnelHealthMonitor = cancel

		monitor, err := tunnelmonitor.New(monitorCfg, func(_ context.Context) error {
			logger.Warning("隧道健康监控已达到阈值，正在重启 xray-core")
			return server.RestartXray()
		})
		if err != nil {
			logger.Warning("隧道健康监控已停用：", err)
		} else {
			go monitor.Run(monitorCtx)
		}
	}
	for {
		sig := <-sigCh

		switch sig {
		case syscall.SIGHUP:
			logger.Info("已收到 SIGHUP 信号，正在重启服务……")

			err := server.StopPanelOnly()
			if err != nil {
				logger.Debug("停止管理服务失败：", err)
			}
			err = subServer.Stop()
			if err != nil {
				logger.Debug("停止订阅服务失败：", err)
			}

			server = web.NewServer(sub.NewLinkProvider())
			global.SetWebServer(server)
			err = server.StartPanelOnly()
			if err != nil {
				log.Fatalf("重启管理服务失败：%v", err)
			}
			log.Println("管理服务重启成功。")

			sub.SetDistFS(web.EmbeddedDist())
			subServer = sub.NewServer()
			global.SetSubServer(subServer)
			err = subServer.Start()
			if err != nil {
				log.Fatalf("重启订阅服务失败：%v", err)
			}
			log.Println("订阅服务重启成功。")
		case sys.SIGUSR1:
			logger.Info("已收到 USR1 信号，正在重启 xray-core……")
			err := server.RestartXray()
			if err != nil {
				logger.Error("重启 xray-core 失败：", err)
			}

		default:
			if stopTunnelHealthMonitor != nil {
				stopTunnelHealthMonitor()
			}

			// --- FIX FOR TELEGRAM BOT CONFLICT (409) on full shutdown ---
			tgbot.StopBot()
			// ------------------------------------------------------------

			if err := server.Stop(); err != nil {
				logger.Error("关闭服务失败：", err)
			}
			_ = subServer.Stop()
			log.Println("正在关闭服务。")
			return
		}
	}
}

// resetSetting resets all panel settings to their default values.
func resetSetting() error {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println("初始化数据库失败：", err)
		return err
	}

	settingService := service.SettingService{}
	err = settingService.ResetSettings()
	if err != nil {
		fmt.Println("重置设置失败：", err)
		return err
	} else {
		fmt.Println("设置已成功重置。")
	}
	return nil
}

// showSetting displays the current panel settings if show is true.
func showSetting(show bool) {
	if show {
		settingService := service.SettingService{}
		port, err := settingService.GetPort()
		if err != nil {
			fmt.Println("获取当前端口失败，错误详情：", err)
		}

		webBasePath, err := settingService.GetBasePath()
		if err != nil {
			fmt.Println("获取面板访问路径失败，错误详情：", err)
		}

		certFile, err := settingService.GetCertFile()
		if err != nil {
			fmt.Println("获取证书文件失败，错误详情：", err)
		}
		keyFile, err := settingService.GetKeyFile()
		if err != nil {
			fmt.Println("获取私钥文件失败，错误详情：", err)
		}

		userService := panel.UserService{}
		hasDefaultCredential, err := userService.HasDefaultCredential()
		if err != nil {
			fmt.Println("获取当前用户信息失败，错误详情：", err)
		}

		fmt.Println("当前面板设置如下：")
		if certFile == "" || keyFile == "" {
			fmt.Println("警告：面板尚未启用 SSL 加密")
		} else {
			fmt.Println("面板已启用 SSL 加密")
		}

		fmt.Println("hasDefaultCredential:", hasDefaultCredential)
		fmt.Println("port:", port)
		fmt.Println("webBasePath:", webBasePath)
	}
}

// updateTgbotEnableSts enables or disables the Telegram bot notifications based on the status parameter.
func updateTgbotEnableSts(status bool) {
	settingService := service.SettingService{}
	currentTgSts, err := settingService.GetTgbotEnabled()
	if err != nil {
		fmt.Println(err)
		return
	}
	logger.Infof("Telegram 机器人当前启用状态为 [%v]，目标状态为 [%v]", currentTgSts, status)
	if currentTgSts != status {
		err := settingService.SetTgbotEnabled(status)
		if err != nil {
			fmt.Println(err)
			return
		} else {
			logger.Infof("Telegram 机器人启用状态已设为 [%v]", status)
		}
	}
}

// updateTgbotSetting updates Telegram bot settings including token, chat ID, and runtime schedule.
func updateTgbotSetting(tgBotToken string, tgBotChatid string, tgBotRuntime string) {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println("初始化数据库失败：", err)
		return
	}

	settingService := service.SettingService{}

	if tgBotToken != "" {
		err := settingService.SetTgBotToken(tgBotToken)
		if err != nil {
			fmt.Printf("设置 Telegram 机器人令牌失败：%v\n", err)
			return
		}
		logger.Info("Telegram 机器人令牌更新成功。")
	}

	if tgBotRuntime != "" {
		err := settingService.SetTgbotRuntime(tgBotRuntime)
		if err != nil {
			fmt.Printf("设置 Telegram 机器人通知时间失败：%v\n", err)
			return
		}
		logger.Infof("Telegram 机器人通知时间已更新为 [%s]。", tgBotRuntime)
	}

	if tgBotChatid != "" {
		err := settingService.SetTgBotChatId(tgBotChatid)
		if err != nil {
			fmt.Printf("设置 Telegram 机器人聊天 ID 失败：%v\n", err)
			return
		}
		logger.Info("Telegram 机器人聊天 ID 更新成功。")
	}
}

// encryptNodeTokens re-encrypts stored tokens after enablement or rotation.
// It requires migration|required mode and a configured key.
func encryptNodeTokens() {
	_ = godotenv.Load()
	if err := initNodeTokenCrypto(); err != nil {
		fmt.Println("节点令牌加密初始化失败：", err)
		os.Exit(1)
	}
	if err := database.InitDB(config.GetDBPath()); err != nil {
		fmt.Println("数据库初始化失败：", err)
		os.Exit(1)
	}
	changed, skipped, err := (&service.NodeService{}).MigrateNodeTokensToActiveKey()
	if err != nil {
		fmt.Println("令牌迁移失败：", err)
		os.Exit(1)
	}
	fmt.Printf("节点令牌迁移完成：%d 个已重新加密，%d 个已是当前版本或已跳过\n", changed, skipped)
}

// updateSetting updates various panel settings including port, credentials, base path, listen IP, and two-factor authentication.
func updateSetting(port int, username string, password string, webBasePath string, listenIP string, resetTwoFactor bool) error {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println("数据库初始化失败：", err)
		return err
	}

	settingService := service.SettingService{}
	userService := panel.UserService{}

	if port > 0 {
		err := settingService.SetPort(port)
		if err != nil {
			fmt.Println("设置端口失败：", err)
		} else {
			fmt.Printf("端口已成功设置为：%v\n", port)
		}
	}

	if username != "" || password != "" {
		err := userService.UpdateFirstUser(username, password)
		if err != nil {
			fmt.Println("更新用户名和密码失败：", err)
		} else {
			fmt.Println("用户名和密码更新成功")
		}
	}

	if webBasePath != "" {
		err := settingService.SetBasePath(webBasePath)
		if err != nil {
			fmt.Println("设置面板访问路径失败：", err)
		} else {
			fmt.Println("面板访问路径设置成功")
		}
	}

	if resetTwoFactor {
		err := settingService.SetTwoFactorEnable(false)

		if err != nil {
			fmt.Println("重置双重认证失败：", err)
		} else {
			_ = settingService.SetTwoFactorToken("")
			fmt.Println("双重认证重置成功")
		}
	}

	if listenIP != "" {
		err := settingService.SetListen(listenIP)
		if err != nil {
			fmt.Println("设置监听 IP 失败：", err)
		} else {
			fmt.Printf("监听 IP 已成功设置为 %v\n", listenIP)
		}
	}

	return nil
}

// updateCert updates the SSL certificate files for the panel.
func updateCert(publicKey string, privateKey string) {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println(err)
		return
	}

	if (privateKey != "" && publicKey != "") || (privateKey == "" && publicKey == "") {
		settingService := service.SettingService{}
		err = settingService.SetCertFile(publicKey)
		if err != nil {
			fmt.Println("设置面板证书失败：", err)
		} else {
			fmt.Println("面板证书设置成功")
		}

		err = settingService.SetKeyFile(privateKey)
		if err != nil {
			fmt.Println("设置面板证书私钥失败：", err)
		} else {
			fmt.Println("面板证书私钥设置成功")
		}

		err = settingService.SetSubCertFile(publicKey)
		if err != nil {
			fmt.Println("设置订阅服务证书失败：", err)
		} else {
			fmt.Println("订阅服务证书设置成功")
		}

		err = settingService.SetSubKeyFile(privateKey)
		if err != nil {
			fmt.Println("设置订阅服务证书私钥失败：", err)
		} else {
			fmt.Println("订阅服务证书私钥设置成功")
		}
	} else {
		fmt.Println("必须同时输入证书和私钥。")
	}
}

// GetCertificate displays the current SSL certificate settings if getCert is true.
func GetCertificate(getCert bool) {
	if getCert {
		settingService := service.SettingService{}
		certFile, err := settingService.GetCertFile()
		if err != nil {
			fmt.Println("获取证书文件失败，错误详情：", err)
		}
		keyFile, err := settingService.GetKeyFile()
		if err != nil {
			fmt.Println("获取私钥文件失败，错误详情：", err)
		}

		fmt.Println("cert:", certFile)
		fmt.Println("key:", keyFile)
	}
}

// GetListenIP displays the current panel listen IP address if getListen is true.
func GetListenIP(getListen bool) {
	if getListen {

		settingService := service.SettingService{}
		ListenIP, err := settingService.GetListen()
		if err != nil {
			log.Printf("获取监听 IP 失败：%v", err)
			return
		}

		fmt.Println("listenIP:", ListenIP)
	}
}

func GetApiToken(getApiToken bool, tokenName, tokenScope string) {
	if !getApiToken {
		return
	}
	// An explicit name applies to both branches below; without one each keeps
	// the name it already used, so every existing invocation is unaffected.
	name := strings.TrimSpace(tokenName)
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println("打开数据库失败，错误详情：", err)
		return
	}
	apiTokenService := panel.ApiTokenService{}
	tokens, err := apiTokenService.List()
	if err != nil {
		fmt.Println("获取 API 令牌失败，错误详情：", err)
		return
	}
	if len(tokens) > 0 {
		fmt.Printf("当前已配置 %d 个 API 令牌。系统仅保存哈希值，无法读取已有令牌的明文。\n", len(tokens))
		fmt.Println("如果令牌已丢失，可在面板的“设置 → API 令牌”中管理并生成新令牌。")

		// Rotate one token per name so repeated calls cannot pile up
		// indefinitely many admin-equivalent tokens that never expire.
		rotated := name
		if rotated == "" {
			rotated = cliFallbackTokenName
		}
		created, err := apiTokenService.RecreateByName(rotated, tokenScope)
		if err != nil {
			fmt.Println("创建备用 API 令牌失败：", err)
			return
		}
		fmt.Printf("\nAPI 令牌 %q 已重新生成（先前的令牌现已失效）：\n", rotated)
		fmt.Println("apiToken:", created.Token)
		return
	}
	if name == "" {
		name = installTokenName
	}
	created, err := apiTokenService.Create(name, tokenScope, 0)
	if err != nil {
		fmt.Println("创建 API 令牌失败，错误详情：", err)
		return
	}
	fmt.Println("apiToken:", created.Token)
}

// migrateDb performs database migration operations for the 3x-ui panel.
func migrateDb() {
	inboundService := service.InboundService{}

	logger.InitLogger(logging.INFO)
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("正在迁移数据库……")
	inboundService.MigrateDB()
	fmt.Println("数据库迁移完成！")
}

// loadServiceEnvFile loads the systemd EnvironmentFile so CLI subcommands like
// "x-ui setting" hit the same database backend as the panel. godotenv.Load does
// not override variables already in the environment, so it is a no-op for the
// systemd-managed service.
func loadServiceEnvFile() {
	for _, path := range config.GetEnvFilePaths() {
		if _, err := os.Stat(path); err != nil {
			continue
		}
		if err := godotenv.Load(path); err != nil {
			log.Printf("警告：加载环境配置文件 %s 失败：%v", path, err)
		}
		return
	}
}

// main is the entry point of the 3x-ui application.
// It parses command-line arguments to run the web server, migrate database, or update settings.
func main() {
	loadServiceEnvFile()

	if len(os.Args) < 2 {
		runWebServer()
		return
	}

	var showVersion bool
	flag.BoolVar(&showVersion, "v", false, "显示版本")

	runCmd := flag.NewFlagSet("run", flag.ExitOnError)

	migrateDbCmd := flag.NewFlagSet("migrate-db", flag.ExitOnError)
	var migrateDsn string
	var migrateSrc string
	var migrateDump string
	var migrateRestore string
	var migrateOut string
	migrateDbCmd.StringVar(&migrateDsn, "dsn", "", "目标 PostgreSQL 连接字符串（postgres://user:pass@host:port/db?sslmode=disable）")
	migrateDbCmd.StringVar(&migrateSrc, "src", "", "源 SQLite 文件（默认为配置的 x-ui.db）")
	migrateDbCmd.StringVar(&migrateDump, "dump", "", "将 --src 导出为可移植的 SQL 文本文件（.db → .dump）")
	migrateDbCmd.StringVar(&migrateRestore, "restore", "", "从 SQL 文本重建 SQLite 数据库（.dump → .db），需指定 --out")
	migrateDbCmd.StringVar(&migrateOut, "out", "", "--restore 的目标 SQLite 文件（必须尚不存在）")

	settingCmd := flag.NewFlagSet("setting", flag.ExitOnError)
	var port int
	var username string
	var password string
	var webBasePath string
	var listenIP string
	var getListen bool
	var webCertFile string
	var webKeyFile string
	var tgbottoken string
	var tgbotchatid string
	var enabletgbot bool
	var tgbotRuntime string
	var reset bool
	var show bool
	var getCert bool
	var getApiToken bool
	var tokenName string
	var tokenScope string
	var resetTwoFactor bool
	settingCmd.BoolVar(&reset, "reset", false, "重置所有设置")
	settingCmd.BoolVar(&show, "show", false, "显示当前设置")
	settingCmd.IntVar(&port, "port", 0, "设置面板端口号")
	settingCmd.StringVar(&username, "username", "", "设置登录用户名")
	settingCmd.StringVar(&password, "password", "", "设置登录密码")
	settingCmd.StringVar(&webBasePath, "webBasePath", "", "设置面板访问路径")
	settingCmd.StringVar(&listenIP, "listenIP", "", "设置面板监听 IP")
	settingCmd.BoolVar(&resetTwoFactor, "resetTwoFactor", false, "重置双重认证设置")
	settingCmd.BoolVar(&getListen, "getListen", false, "显示面板当前监听 IP")
	settingCmd.BoolVar(&getCert, "getCert", false, "显示当前证书设置")
	settingCmd.BoolVar(&getApiToken, "getApiToken", false, "重新生成用于命令行的 API 令牌并使旧令牌失效；若面板尚无令牌，则创建新令牌")
	settingCmd.StringVar(&tokenName, "tokenName", "", "-getApiToken 操作的令牌名称（默认："+cliFallbackTokenName+"，面板尚无令牌时为 "+installTokenName+"）")
	settingCmd.StringVar(&tokenScope, "tokenScope", "", "-getApiToken 签发令牌的权限范围：admin、monitor 或 node-sync（默认沿用被替换令牌的权限，新令牌默认为 admin）")
	settingCmd.StringVar(&webCertFile, "webCert", "", "设置面板证书文件路径")
	settingCmd.StringVar(&webKeyFile, "webCertKey", "", "设置面板私钥文件路径")
	settingCmd.StringVar(&tgbottoken, "tgbottoken", "", "设置 Telegram 机器人令牌")
	settingCmd.StringVar(&tgbotRuntime, "tgbotRuntime", "", "设置 Telegram 机器人通知的 cron 时间表达式")
	settingCmd.StringVar(&tgbotchatid, "tgbotchatid", "", "设置 Telegram 机器人通知的聊天 ID")
	settingCmd.BoolVar(&enabletgbot, "enabletgbot", false, "启用 Telegram 机器人通知")

	for _, cmd := range []*flag.FlagSet{runCmd, migrateDbCmd, settingCmd} {
		cmd.Usage = func() { printFlagUsage(cmd) }
	}
	flag.Usage = func() {
		printFlagUsage(flag.CommandLine)
		fmt.Print(commandHelp())
	}
	flag.CommandLine.Usage = flag.Usage

	flag.Parse()
	if showVersion {
		fmt.Println(config.GetPanelVersion())
		return
	}

	switch os.Args[1] {
	case "run":
		err := runCmd.Parse(os.Args[2:])
		if err != nil {
			fmt.Println(err)
			return
		}
		runWebServer()
	case "install-awg-kernel":
		installAmneziaWGKernelModule()
	case "migrate":
		migrateDb()
	case "encrypt-tokens":
		encryptNodeTokens()
	case "migrate-db":
		if err := migrateDbCmd.Parse(os.Args[2:]); err != nil {
			fmt.Println(err)
			return
		}
		src := migrateSrc
		if src == "" {
			src = config.GetDBPath()
		}
		switch {
		case migrateDump != "":
			if err := database.DumpSQLite(src, migrateDump); err != nil {
				fmt.Println("导出失败：", err)
				os.Exit(1)
			}
			fmt.Printf("已导出 %s → %s\n", src, migrateDump)
		case migrateRestore != "":
			if migrateOut == "" {
				fmt.Println("使用 --restore 时必须指定 --out：目标 .db 文件路径（文件必须尚不存在）")
				return
			}
			if err := database.RestoreSQLite(migrateRestore, migrateOut); err != nil {
				fmt.Println("恢复失败：", err)
				os.Exit(1)
			}
			fmt.Printf("已恢复 %s → %s\n", migrateRestore, migrateOut)
		case migrateDsn != "":
			if err := database.MigrateData(src, migrateDsn); err != nil {
				fmt.Println("迁移失败：", err)
				os.Exit(1)
			}
		default:
			fmt.Println("未指定操作：请传入 --dump <file>、--restore <file> --out <db> 或 --dsn <postgres-dsn>")
		}
	case "setting":
		err := settingCmd.Parse(os.Args[2:])
		if err != nil {
			fmt.Println(err)
			return
		}
		// flag stops parsing at the first non-flag argument, so the `-getApiToken true`
		// form drops every flag written after it. Say so instead of acting on a default.
		if rest := settingCmd.Args(); len(rest) > 0 {
			fmt.Printf("警告：已忽略 %q 及其后的所有选项，请将选项放在位置参数之前\n", strings.Join(rest, " "))
		}
		if reset {
			if err = resetSetting(); err != nil {
				return
			}
		} else {
			if err = updateSetting(port, username, password, webBasePath, listenIP, resetTwoFactor); err != nil {
				return
			}
		}
		if webCertFile != "" || webKeyFile != "" {
			updateCert(webCertFile, webKeyFile)
		}
		if show {
			showSetting(show)
		}
		if getListen {
			GetListenIP(getListen)
		}
		if getCert {
			GetCertificate(getCert)
		}
		if getApiToken {
			GetApiToken(getApiToken, tokenName, tokenScope)
		}
		if (tgbottoken != "") || (tgbotchatid != "") || (tgbotRuntime != "") {
			updateTgbotSetting(tgbottoken, tgbotchatid, tgbotRuntime)
		}
		if enabletgbot {
			updateTgbotEnableSts(enabletgbot)
		}
	case "cert":
		err := settingCmd.Parse(os.Args[2:])
		if err != nil {
			fmt.Println(err)
			return
		}
		if reset {
			updateCert("", "")
		} else {
			updateCert(webCertFile, webKeyFile)
		}
	default:
		fmt.Println("无效的子命令")
		fmt.Println()
		runCmd.Usage()
		fmt.Println()
		settingCmd.Usage()
	}
}

func printFlagUsage(cmd *flag.FlagSet) {
	fmt.Fprintf(cmd.Output(), "用法：%s [选项]\n", cmd.Name())
	cmd.PrintDefaults()
}

func commandHelp() string {
	return `
子命令：
    run            启动管理面板
    migrate        从其他或旧版 x-ui 迁移
    migrate-db     转换 SQLite 与 .dump（--dump/--restore），或复制到 PostgreSQL（--dsn）
    encrypt-tokens 使用配置的当前密钥加密节点访问令牌
    setting        修改设置
    install-awg-kernel
                   构建并安装 AmneziaWG 内核模块（AmneziaWG 入站的内核模式需要此模块）
`
}

//go:embed scripts/install-amneziawg-kernel.sh
var installAwgKernelScript []byte

// installAmneziaWGKernelModule runs the bundled module installer.
// It needs a terminal for its apt and DKMS build output.
func installAmneziaWGKernelModule() {
	if runtime.GOOS != "linux" {
		fmt.Println("AmneziaWG 内核模块仅支持 Linux")
		return
	}
	if os.Geteuid() != 0 {
		exe, err := os.Executable()
		if err != nil || exe == "" {
			exe = "/usr/local/x-ui/x-ui"
		}
		fmt.Println("请以 root 用户运行：sudo " + exe + " install-awg-kernel")
		return
	}

	f, err := os.CreateTemp("", "install-amneziawg-kernel-*.sh")
	if err != nil {
		fmt.Println("无法创建安装脚本临时文件：", err)
		return
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(installAwgKernelScript); err != nil {
		f.Close()
		fmt.Println("无法写入安装脚本：", err)
		return
	}
	f.Close()

	// No timeout: an apt+dkms build legitimately runs for minutes.
	cmd := exec.CommandContext(context.Background(), "bash", f.Name())
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		fmt.Println("安装失败：", err)
		os.Exit(1)
	}
}
