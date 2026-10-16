package main

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"math"
	"strings"
	"time"
)

var buf bytes.Buffer

func out(h slog.Handler, lvl slog.Level, msg string, as ...slog.Attr) {
	r := slog.NewRecord(time.Time{}, lvl, msg, 0)
	r.AddAttrs(as...)
	if h.Enabled(context.Background(), lvl) {
		buf.Reset()
		h.Handle(context.Background(), r)
		fmt.Print(buf.String())
	} else {
		fmt.Println("(disabled)")
	}
}

func both(f func(json bool) slog.Handler, lvl slog.Level, msg string, as ...slog.Attr) {
	out(f(false), lvl, msg, as...)
	out(f(true), lvl, msg, as...)
}

func mk(opts *slog.HandlerOptions) func(bool) slog.Handler {
	return func(j bool) slog.Handler {
		if j {
			return slog.NewJSONHandler(&buf, opts)
		}
		return slog.NewTextHandler(&buf, opts)
	}
}

func main() {
	for l := -9; l <= 13; l++ {
		fmt.Println(l, slog.Level(l).String())
	}
	for _, s := range []string{"debug", "INFO", "Warn+3", "error-2", "info+x", "bogus", "warn-10"} {
		var l slog.Level
		err := l.UnmarshalText([]byte(s))
		fmt.Println(s, int(l), err)
	}
	plain := mk(nil)
	both(plain, slog.LevelInfo, "hello", slog.String("s", "v"), slog.Int("i", -42), slog.Uint64("u", 7),
		slog.Float64("f", 3.25), slog.Bool("b", true), slog.Duration("d", 1500*time.Millisecond), slog.Any("n", nil))
	strs := []string{"", "a b", "a=b", "q\"x", "tab\tx", "π", "a\u00a0b", "back\\slash", "é", "<&>", "\u2028", "\x01", "\x7f", "\ufffd", "ok"}
	for _, s := range strs {
		both(plain, slog.LevelWarn, s, slog.String(s, s))
	}
	floats := []float64{0, 1, -1.5, 1e20, 1e21, 1.5e-6, 1e-7, 123456789.125, 0.1, 1e100, -2.5e-10}
	for _, f := range floats {
		both(plain, slog.LevelInfo, "f", slog.Float64("x", f))
	}
	durs := []time.Duration{0, 1, 999, 1000, 1500, 1000000, 2500000, time.Second, 90 * time.Second, 3723*time.Second + 5*time.Millisecond, -1500 * time.Millisecond, 26 * time.Hour}
	for _, d := range durs {
		both(plain, slog.LevelInfo, "d", slog.Duration("x", d))
	}
	both(plain, slog.LevelDebug, "skipped")
	both(mk(&slog.HandlerOptions{Level: slog.LevelDebug}), slog.LevelDebug-1, "debug-1")
	both(mk(&slog.HandlerOptions{Level: slog.LevelDebug}), slog.LevelDebug, "dbg", slog.Group("g", slog.Int("a", 1), slog.Group("h", slog.String("b", "x y"))))
	both(plain, slog.LevelError, "groups", slog.Group("empty"), slog.Group("", slog.Int("inl", 1)), slog.Group("g", slog.Group("e")), slog.Int("z", 9))
	both(plain, slog.LevelInfo, "emptyonly", slog.Group("e"))
	// With / WithGroup
	w := func(j bool) slog.Handler {
		h := plain(j).WithAttrs([]slog.Attr{slog.String("app", "kite")}).WithGroup("req").WithAttrs([]slog.Attr{slog.Int("id", 5)}).WithGroup("sub")
		return h
	}
	both(w, slog.LevelInfo, "nested", slog.String("k", "v"))
	both(w, slog.LevelInfo, "nested-empty")
	w2 := func(j bool) slog.Handler { return plain(j).WithGroup("a").WithGroup("b") }
	both(w2, slog.LevelInfo, "nogroup")
	both(w2, slog.LevelInfo, "withgroup", slog.Int("x", 1))
	w3 := func(j bool) slog.Handler { return plain(j).WithGroup("a").WithAttrs([]slog.Attr{slog.Group("e")}).WithAttrs([]slog.Attr{slog.Int("p", 1)}) }
	both(w3, slog.LevelInfo, "w3", slog.Int("x", 2))
	w4 := func(j bool) slog.Handler { return plain(j).WithGroup("a b").WithAttrs([]slog.Attr{slog.Int("c=d", 1)}) }
	both(w4, slog.LevelInfo, "w4", slog.String("e", "f"))
	// ReplaceAttr
	rep := func(groups []string, a slog.Attr) slog.Attr {
		fmt.Println(strings.Join(groups, ".") + "|" + a.Key + "|" + a.Value.Kind().String())
		switch a.Key {
		case "secret":
			return slog.Attr{}
		case slog.LevelKey:
			return slog.String("sev", a.Value.String())
		case slog.MessageKey:
			return slog.String("message", strings.ToUpper(a.Value.String()))
		case "n":
			return slog.Int64("n", a.Value.Any().(int64)*10)
		case "hide":
			return slog.Group("x")
		}
		return a
	}
	r := mk(&slog.HandlerOptions{ReplaceAttr: rep})
	wr := func(j bool) slog.Handler {
		return r(j).WithAttrs([]slog.Attr{slog.Int("n", 1)}).WithGroup("g").WithAttrs([]slog.Attr{slog.String("secret", "s"), slog.Int("n", 2)})
	}
	both(wr, slog.LevelWarn, "rep", slog.String("secret", "x"), slog.Int("n", 3), slog.Group("h", slog.Int("n", 4), slog.String("hide", "y")), slog.String("ok", "y"))
	both(r, slog.LevelInfo, "allgone", slog.String("secret", "x"))
	_ = math.Pi
}
