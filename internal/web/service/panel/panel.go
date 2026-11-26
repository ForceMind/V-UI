package panel

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/mhsanaei/3x-ui/v3/internal/config"
	"github.com/mhsanaei/3x-ui/v3/internal/logger"
	"github.com/mhsanaei/3x-ui/v3/internal/web/global"
	"github.com/mhsanaei/3x-ui/v3/internal/web/service"
)

type PanelService struct{}

type PanelUpdateInfo struct {
	Channel         string `json:"channel"`
	CurrentVersion  string `json:"currentVersion"`
	LatestVersion   string `json:"latestVersion"`
	UpdateAvailable bool   `json:"updateAvailable"`
}

type PanelUpdateStatus struct {
	RunID      string `json:"runId" example:"1735689600123456789"`
	State      string `json:"state" example:"success"`
	ExitCode   int    `json:"exitCode" example:"0"`
	FinishedAt int64  `json:"finishedAt" example:"1735689612"`
}

const releaseManifestBase = "https://raw.githubusercontent.com/ForceMind/V-UI/3x-ui-zh-lite/packages/"

var manifestPattern = regexp.MustCompile(`^v3\.11\.0-zh\.([1-9][0-9]{0,8}) [0-9a-f]{64} [0-9a-f]{40}\n?$`)

func (s *PanelService) RestartPanel(delay time.Duration) error {
	go func() {
		time.Sleep(delay)
		if global.TriggerRestart() {
			return
		}
		if runtime.GOOS == "windows" {
			logger.Error("panel restart: no restart hook registered (SIGHUP unsupported on Windows)")
			return
		}
		p, err := os.FindProcess(syscall.Getpid())
		if err != nil {
			logger.Error("panel restart: FindProcess failed:", err)
			return
		}
		if err := p.Signal(syscall.SIGHUP); err != nil {
			logger.Error("failed to send SIGHUP signal:", err)
		}
	}()
	return nil
}

func configuredDevChannel() bool {
	enabled, err := (&service.SettingService{}).GetDevChannelEnable()
	return err == nil && enabled
}

func channelName(dev bool) string {
	if dev {
		return "dev"
	}
	return "stable"
}

func (s *PanelService) GetUpdateInfo() (*PanelUpdateInfo, error) {
	channel := channelName(configuredDevChannel())
	current := config.GetBaseVersion()
	info := &PanelUpdateInfo{Channel: channel, CurrentVersion: current}
	resp, err := (&service.SettingService{}).NewProxiedHTTPClient(10 * time.Second).Get(releaseManifestBase + channel + ".txt")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return info, nil
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("读取中文发行清单失败，HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 256))
	if err != nil {
		return nil, err
	}
	latest := manifestPattern.FindStringSubmatch(string(body))
	if latest == nil {
		return nil, fmt.Errorf("中文发行清单格式无效")
	}
	info.LatestVersion = strings.Fields(latest[0])[0]
	a, _ := strconv.Atoi(latest[1])
	b, _ := strconv.Atoi(strings.TrimPrefix(current, "3.11.0-zh."))
	info.UpdateAvailable = a > b
	return info, nil
}

// GetUpstreamUpdateInfo supplies version hints for managed official nodes.
func (s *PanelService) GetUpstreamUpdateInfo() (*PanelUpdateInfo, error) {
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet,
		"https://api.github.com/repos/MHSanaei/3x-ui/releases/latest", nil)
	if err != nil {
		return nil, err
	}
	resp, err := (&service.SettingService{}).NewProxiedHTTPClient(10 * time.Second).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("读取官方节点版本失败，HTTP %d", resp.StatusCode)
	}
	var release service.Release
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&release); err != nil {
		return nil, err
	}
	return &PanelUpdateInfo{Channel: "stable", LatestVersion: release.TagName}, nil
}

func (s *PanelService) StartUpdate() (int64, error) {
	return s.StartUpdateChannel(configuredDevChannel())
}

func (s *PanelService) StartUpdateChannel(dev bool) (int64, error) {
	if runtime.GOOS != "linux" || os.Getenv("XUI_ZH_WEB_UPDATE") != "1" {
		return 0, fmt.Errorf("此运行环境请使用终端执行 sudo x-ui update")
	}
	return queueUpdate(config.GetDBFolderPath(), channelName(dev), time.Now().UnixNano())
}

func queueUpdate(dir, channel string, runID int64) (int64, error) {
	f, err := os.CreateTemp(dir, ".update-request-")
	if err != nil {
		return 0, err
	}
	defer os.Remove(f.Name())
	_, writeErr := fmt.Fprintf(f, "%d %s\n", runID, channel)
	closeErr := f.Close()
	if writeErr != nil {
		return 0, writeErr
	}
	if closeErr != nil {
		return 0, closeErr
	}
	if err := os.Link(f.Name(), filepath.Join(dir, "update-request")); err != nil {
		if os.IsExist(err) {
			return 0, fmt.Errorf("面板正在更新，请等待该次更新完成")
		}
		return 0, err
	}
	return runID, nil
}

func (s *PanelService) GetUpdateStatus() *PanelUpdateStatus {
	path := config.GetUpdateStatusFilePath()
	if os.Getenv("XUI_ZH_WEB_UPDATE") == "1" {
		path = "/var/lib/x-ui-zh-updates/status.json"
	}
	data, err := os.ReadFile(path)
	var status PanelUpdateStatus
	if err != nil || json.Unmarshal(data, &status) != nil {
		return &PanelUpdateStatus{State: "pending"}
	}
	if status.State != "success" && status.State != "failed" {
		status.State = "pending"
	}
	return &status
}
