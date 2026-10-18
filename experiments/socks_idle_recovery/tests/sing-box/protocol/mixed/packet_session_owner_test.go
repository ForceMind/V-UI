package mixed

import "testing"

func TestOwnerMixedLimiterEightAndJoinedRelease(t *testing.T) {
	h := &Inbound{packetSessionSlots: make(chan struct{}, 8)}
	var releases []func()
	for range 8 { release, ok := h.acquirePacketSessionSlot(); if !ok { t.Fatal("early capacity failure") }; releases = append(releases, release) }
	if _, ok := h.acquirePacketSessionSlot(); ok { t.Fatal("ninth owner admitted") }
	// Slot release is handed exclusively to the owner's postjoin cleanup.
	releases[0](); releases[0]()
	release, ok := h.acquirePacketSessionSlot()
	if !ok { t.Fatal("joined owner slot not reusable") }
	release()
	for _, release := range releases[1:] { release() }
	if len(h.packetSessionSlots) != 0 { t.Fatal("limiter leaked") }
}
