package xray

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestOnlineUsersQuerySharesConcurrentResults(t *testing.T) {
	for _, empty := range []bool{false, true} {
		var query onlineUsersQuery
		var calls atomic.Int32
		started, release := make(chan struct{}), make(chan struct{})
		fetch := func() ([]OnlineUser, bool, error) {
			if calls.Add(1) == 1 {
				close(started)
			}
			<-release
			if empty {
				return nil, true, nil
			}
			return []OnlineUser{{Email: "test", IPs: []OnlineIP{{IP: "192.0.2.1"}, {IP: "192.0.2.2", LastSeen: 7}}}}, true, nil
		}
		var readers sync.WaitGroup
		readers.Go(func() { _, _, _ = query.read(fetch) })
		<-started
		readers.Go(func() {
			users, supported, err := query.read(fetch)
			if err != nil || !supported || (len(users) == 0) != empty {
				t.Errorf("snapshot %v, supported %v: %v", users, supported, err)
			}
			if !empty && len(users) == 1 && (users[0].IPs[0].LastSeen <= 0 || users[0].IPs[1].LastSeen != 7) {
				t.Errorf("snapshot IP timestamps: %v", users[0].IPs)
			}
		})
		close(release)
		readers.Wait()
		if got := calls.Load(); got != 1 {
			t.Errorf("empty=%v: queried %d times, want 1", empty, got)
		}
	}
}

func TestOnlineUsersQueryRetriesAndIsolatesCore(t *testing.T) {
	var previous, replacement process
	read := func(query *onlineUsersQuery, email string) ([]OnlineUser, bool, error) {
		return query.read(func() ([]OnlineUser, bool, error) {
			return []OnlineUser{{Email: email}}, true, nil
		})
	}
	_, _, _ = read(&previous.onlineUsersQuery, "previous")
	users, _, err := read(&replacement.onlineUsersQuery, "replacement")
	if err != nil || len(users) != 1 || users[0].Email != "replacement" {
		t.Fatalf("returned a snapshot from a previous core: %v, %v", users, err)
	}
	query := &previous.onlineUsersQuery
	query.sampledAt = time.Now().Add(-onlineUsersMaxAge)
	failure := errors.New("temporary failure")
	users, supported, err := query.read(func() ([]OnlineUser, bool, error) {
		return nil, false, failure
	})
	if !errors.Is(err, failure) || supported || len(users) != 0 {
		t.Fatalf("expired sample was used after failure: %v, %v, %v", users, supported, err)
	}
	users, supported, err = read(query, "recovered")
	if err != nil || !supported || len(users) != 1 || users[0].Email != "recovered" {
		t.Fatalf("transient failure was cached: %v, %v, %v", users, supported, err)
	}
}
