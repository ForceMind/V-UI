package bufio

import (
	"bytes"
	"io"
	"testing"
)

func TestChunkReaderPacketSessionFinalizer(t *testing.T) {
	reader := NewChunkReader(bytes.NewReader([]byte("retained-cache")), 16384)
	t.Cleanup(reader.ReleaseCacheAfterJoin)
	if _, ok := any(reader).(io.Closer); ok { t.Fatal("ChunkReader must not become io.Closer") }
	var first [1]byte
	if _, err := reader.Read(first[:]); err != nil { t.Fatal(err) }
	cache := reader.cache
	if cache == nil || cache.IsEmpty() { t.Fatal("test did not retain cache") }
	reader.ReleaseCacheAfterJoin()
	if reader.cache != nil || cache.Len() != 0 { t.Fatal("cache not released") }
	reader.ReleaseCacheAfterJoin()
}
