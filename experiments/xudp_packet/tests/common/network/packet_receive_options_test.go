package network

import "testing"

// These tests deliberately do not run in parallel: the companion receive tests
// install a scoped allocation tracker and require joined workers before restore.
func TestVUICompletePacketOptions(t *testing.T) {
	cases := []struct {
		name string
		options ReadWaitOptions
		wire int
	}{
		{"default", ReadWaitOptions{}, 65535},
		{"default_headroom", ReadWaitOptions{FrontHeadroom: 31, RearHeadroom: 351}, 65535},
		{"default_overhead_independent", ReadWaitOptions{FrontHeadroom: 31, RearHeadroom: 351, ReadOverhead: 382}, 65535},
		{"explicit_overhead", ReadWaitOptions{MTU: 1024, ReadOverhead: 17, FrontHeadroom: 31, RearHeadroom: 33}, 1041},
		{"explicit_limit", ReadWaitOptions{MTU: 65535}, 65535},
		{"explicit_clamped_sum", ReadWaitOptions{MTU: 65535, ReadOverhead: 65535, FrontHeadroom: 31, RearHeadroom: 33}, 65535},
		{"maximum_headrooms", ReadWaitOptions{MTU: 1, FrontHeadroom: 65535, RearHeadroom: 65535}, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := tc.options.NewPacketBuffer()
			defer b.Release()
			if b.Start() != tc.options.FrontHeadroom || b.Len() != 0 || b.FreeLen() != tc.wire {
				t.Fatalf("start/len/free = %d/%d/%d, want %d/0/%d", b.Start(), b.Len(), b.FreeLen(), tc.options.FrontHeadroom, tc.wire)
			}
			if b.RawCap() != tc.options.FrontHeadroom+tc.wire+tc.options.RearHeadroom {
				t.Fatalf("storage = %d; headroom and wire budget must be independent", b.RawCap())
			}
			b.Truncate(tc.wire)
			tc.options.PostReturn(b)
			if b.FreeLen() != tc.options.RearHeadroom {
				t.Fatalf("post-return rear headroom = %d, want %d", b.FreeLen(), tc.options.RearHeadroom)
			}
		})
	}
}

func TestVUICompletePacketInvalidOptions(t *testing.T) {
	cases := []struct { name string; options ReadWaitOptions }{
		{"negative_mtu", ReadWaitOptions{MTU: -1}},
		{"oversize_mtu", ReadWaitOptions{MTU: 65536}},
		{"negative_overhead", ReadWaitOptions{ReadOverhead: -1}},
		{"oversize_overhead", ReadWaitOptions{ReadOverhead: 65536}},
		{"negative_front", ReadWaitOptions{FrontHeadroom: -1}},
		{"oversize_front", ReadWaitOptions{FrontHeadroom: 65536}},
		{"negative_rear", ReadWaitOptions{RearHeadroom: -1}},
		{"oversize_rear", ReadWaitOptions{RearHeadroom: 65536}},
		{"overflow_sum", ReadWaitOptions{MTU: int(^uint(0) >> 1), ReadOverhead: 1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			defer func() { if recover() == nil { t.Fatal("invalid options did not panic") } }()
			b := tc.options.NewPacketBuffer()
			b.Release()
		})
	}
}

func TestVUICompletePacketPoolBudget(t *testing.T) {
	// Exercise the real default allocator, not the ownership tracker installed
	// by the separate bufio package. RawCap reports logical storage length;
	// the capacity of the exposed backing slice reveals the actual pool bin.
	o := ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}
	b := o.NewPacketBuffer(); defer b.Release()
	backing := cap(b.FreeBytes()) + b.Start()
	if b.FreeLen() != 65535 || b.RawCap() != 65917 || backing != 73728 {
		t.Fatalf("wire/logical/backing = %d/%d/%d, want 65535/65917/73728", b.FreeLen(), b.RawCap(), backing)
	}
	t.Logf("wire_bytes=65535 logical_slot_bytes=65917 pooled_slot_bytes=73728 batch64_logical_bytes=%d batch64_pooled_bytes=%d", 64*65917, 64*73728)
}
