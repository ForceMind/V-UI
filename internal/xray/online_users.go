package xray

import (
	"sync"
	"time"

	"github.com/mhsanaei/3x-ui/v3/internal/logger"
)

// Only nearby polls share a snapshot. Both cron tasks keep their own cadence.
const onlineUsersMaxAge = 2 * time.Second

type onlineUsersQuery struct {
	mu        sync.Mutex
	sampledAt time.Time
	users     []OnlineUser
}

func (q *onlineUsersQuery) read(fetch func() ([]OnlineUser, bool, error)) ([]OnlineUser, bool, error) {
	q.mu.Lock()
	defer q.mu.Unlock()

	now := time.Now()
	if !q.sampledAt.IsZero() && now.Sub(q.sampledAt) < onlineUsersMaxAge {
		return q.users, true, nil
	}
	q.sampledAt = time.Time{}
	q.users = nil
	users, supported, err := fetch()
	if err != nil || !supported {
		return nil, supported, err
	}
	for _, user := range users {
		for i := range user.IPs {
			if user.IPs[i].LastSeen <= 0 {
				user.IPs[i].LastSeen = now.Unix()
			}
		}
	}
	q.sampledAt, q.users = now, users
	return users, true, nil
}

// QueryOnlineUsers shares a read-only online snapshot within one core process.
// It never uses an expired snapshot when a refresh fails.
func (p *Process) QueryOnlineUsers() ([]OnlineUser, bool, error) {
	return p.onlineUsersQuery.read(func() ([]OnlineUser, bool, error) {
		if !p.IsRunning() || p.OnlineAPISupport() == OnlineAPIUnsupported {
			return nil, false, nil
		}
		var api XrayAPI
		if err := api.Init(p.GetAPIPort()); err != nil {
			return nil, false, err
		}
		defer api.Close()

		users, err := api.GetOnlineUsers()
		if IsUnimplementedErr(err) {
			p.SetOnlineAPISupport(OnlineAPIUnsupported)
			logger.Info("xray core does not support the online-stats API; using traffic deltas for online status")
			return nil, false, nil
		}
		if err != nil {
			return nil, false, err
		}
		if p.OnlineAPISupport() == OnlineAPIUnknown {
			p.SetOnlineAPISupport(OnlineAPISupported)
			logger.Info("xray core supports the online-stats API; using connection-based online status and IP limits")
		}
		return users, true, nil
	})
}
