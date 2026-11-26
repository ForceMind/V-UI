package service

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/mhsanaei/3x-ui/v3/internal/database"
	"github.com/mhsanaei/3x-ui/v3/internal/xray"
	"gorm.io/gorm"
)

func TestClientTrafficBatchDeltas(t *testing.T) {
	var db *gorm.DB
	if os.Getenv("XUI_DB_TYPE") == "postgres" {
		setupScaleDB(t)
		db = database.GetDB()
	} else {
		db = initTrafficTestDB(t)
	}
	const active = 305
	rows := make([]xray.ClientTraffic, active+1)
	deltas := make([]*xray.ClientTraffic, 0, len(rows)+1)
	for i := range rows {
		email := fmt.Sprintf("traffic-batch-%04d@example.invalid", i)
		rows[i] = xray.ClientTraffic{Email: email, Up: 100, Down: 200, Enable: true, LastOnline: 1}
		deltas = append(deltas, &xray.ClientTraffic{Email: email, Up: 3, Down: 4})
	}
	rows[0].Up = database.TrafficMax - 1
	rows[0].LastOnline = 9_000_000_000_000
	deltas[active].Up, deltas[active].Down = 0, 0
	deltas = append(deltas, &xray.ClientTraffic{Email: rows[1].Email, Up: 5, Down: 7})
	if err := db.CreateInBatches(&rows, 40).Error; err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if database.IsPostgres() {
			db.Where("email LIKE ?", "traffic-batch-%@example.invalid").Delete(&xray.ClientTraffic{})
		}
	})
	writes := 0
	stop := errors.New("second batch failed")
	failSecond := true
	const callback = "test:client-traffic-batch"
	if err := db.Callback().Raw().Before("gorm:raw").Register(callback, func(tx *gorm.DB) {
		if strings.HasPrefix(tx.Statement.SQL.String(), "WITH deltas(") {
			writes++
			if failSecond && writes == 2 {
				_ = tx.AddError(stop)
			}
		}
	}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Callback().Raw().Remove(callback) })
	apply := func() error {
		return db.Transaction(func(tx *gorm.DB) error { return (&InboundService{}).addClientTraffic(tx, deltas) })
	}
	read := func() []xray.ClientTraffic {
		t.Helper()
		var got []xray.ClientTraffic
		if err := db.Where("email LIKE ?", "traffic-batch-%@example.invalid").Order("email").Find(&got).Error; err != nil {
			t.Fatal(err)
		}
		if len(got) != len(rows) {
			t.Fatalf("account count = %d, want %d", len(got), len(rows))
		}
		return got
	}
	if err := apply(); !errors.Is(err, stop) || writes != 2 {
		t.Fatalf("second batch failure: wrote %d batches, err %v", writes, err)
	}
	for i, got := range read() {
		if got.Up != rows[i].Up || got.Down != rows[i].Down || got.LastOnline != rows[i].LastOnline {
			t.Fatalf("rollback retained a write at row %d: %+v", i, got)
		}
	}
	writes, failSecond = 0, false
	if err := apply(); err != nil || writes != 2 {
		t.Fatalf("batch update: wrote %d batches, err %v", writes, err)
	}
	for i, got := range read() {
		up, down, online := int64(103), int64(204), got.LastOnline > 1
		switch i {
		case 0:
			up, online = database.TrafficMax, got.LastOnline == rows[i].LastOnline
		case 1:
			up, down = 108, 211
		case active:
			up, down, online = 100, 200, got.LastOnline == 1
		}
		if got.Up != up || got.Down != down || !online {
			t.Fatalf("batched counters or last online differ at row %d: %+v", i, got)
		}
	}
}
