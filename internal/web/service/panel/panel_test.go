package panel

import (
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
)

func TestQueueUpdateKeepsFirstRun(t *testing.T) {
	dir := t.TempDir()
	const stableRun int64 = 1735689600123456789
	if got, err := queueUpdate(dir, "stable", stableRun); err != nil || got != stableRun {
		t.Fatalf("enqueue = %d, %v", got, err)
	}
	if _, err := queueUpdate(dir, "dev", stableRun+1); err == nil {
		t.Fatal("second request overwrote active run")
	}
	raw, err := os.ReadFile(filepath.Join(dir, "update-request"))
	if err != nil || string(raw) != "1735689600123456789 stable\n" {
		t.Fatalf("request = %q, %v", raw, err)
	}
}

func TestQueueUpdateHasOneWinner(t *testing.T) {
	dir := t.TempDir()
	var successes atomic.Int32
	var work sync.WaitGroup
	for i := range 8 {
		work.Go(func() {
			if _, err := queueUpdate(dir, "stable", int64(i)+1735689600123456789); err == nil {
				successes.Add(1)
			}
		})
	}
	work.Wait()
	if got := successes.Load(); got != 1 {
		t.Fatalf("successful requests = %d, want 1", got)
	}
}
