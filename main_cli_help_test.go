package main

import (
	"bytes"
	"flag"
	"strings"
	"testing"
)

func TestCLIFlagUsageChinese(t *testing.T) {
	for _, name := range []string{"run", "migrate-db", "setting"} {
		t.Run(name, func(t *testing.T) {
			cmd := flag.NewFlagSet(name, flag.ContinueOnError)
			var output bytes.Buffer
			cmd.SetOutput(&output)
			cmd.String("webCert", "", "设置面板证书文件路径")
			cmd.Usage = func() { printFlagUsage(cmd) }
			if err := cmd.Parse([]string{"-h"}); err != flag.ErrHelp {
				t.Fatalf("help result = %v, want flag.ErrHelp", err)
			}
			text := output.String()
			for _, want := range []string{"用法：" + name + " [选项]", "-webCert", "设置面板证书文件路径"} {
				if !strings.Contains(text, want) {
					t.Fatalf("help missing %q:\n%s", want, text)
				}
			}
			if strings.Contains(text, "Usage of") {
				t.Fatalf("help still uses the English usage heading:\n%s", text)
			}
		})
	}
}

func TestCLICommandHelpChinese(t *testing.T) {
	text := commandHelp()
	for _, want := range []string{"子命令：", "run", "migrate", "migrate-db", "encrypt-tokens", "setting", "install-awg-kernel", "--dump/--restore", "--dsn", "内核模式"} {
		if !strings.Contains(text, want) {
			t.Fatalf("command help missing %q:\n%s", want, text)
		}
	}
	for _, unwanted := range []string{"Commands:", "run web panel", "set settings", "build and install"} {
		if strings.Contains(text, unwanted) {
			t.Fatalf("command help contains English description %q:\n%s", unwanted, text)
		}
	}
}
