package main

import (
	"fmt"
	"os"
	"time"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

// 事件日志文件
var logFile *os.File

func logf(format string, args ...interface{}) {
	msg := fmt.Sprintf(format, args...)
	fmt.Fprintln(logFile, msg)
	if logFile != nil {
		logFile.Sync()
	}
}

func main() {
	var err error
	logFile, err = os.Create("mouse_events.log")
	if err != nil {
		fmt.Fprintln(os.Stderr, "Cannot create log:", err)
		os.Exit(1)
	}
	defer logFile.Close()

	logf("=== tcell mouse test start ===")
	logf("tcell version: v2.13.10, tview version: v0.42.0")

	app := tview.NewApplication()
	app.EnableMouse(true)

	table := tview.NewTable().SetSelectable(true, false)
	// 填充 20 行
	for i := 0; i < 20; i++ {
		cell := tview.NewTableCell(fmt.Sprintf("Row %d", i))
		table.SetCell(i, 0, cell)
	}
	table.SetTitle(" Mouse Test - inject events and check log ")

	// 设置鼠标捕获，记录所有事件
	app.SetMouseCapture(func(event *tcell.EventMouse, action tview.MouseAction) (*tcell.EventMouse, tview.MouseAction) {
		if event == nil {
			logf("mouseCapture: event=nil action=%v", action)
			return nil, action
		}
		x, y := event.Position()
		btns := event.Buttons()
		logf("mouseCapture: pos=(%d,%d) buttons=0x%x action=%v", x, y, btns, action)
		return event, action
	})

	// 键盘 q 退出
	table.SetInputCapture(func(event *tcell.EventKey) *tcell.EventKey {
		if event.Key() == tcell.KeyRune && event.Rune() == 'q' {
			logf("quit by 'q'")
			app.Stop()
			return nil
		}
		logf("key: %v rune=%q", event.Key(), event.Rune())
		return event
	})

	// 10 秒后自动退出
	go func() {
		time.Sleep(30 * time.Second)
		logf("auto-quit after 30s")
		app.QueueUpdateDraw(func() {
			app.Stop()
		})
	}()

	logf("starting application...")
	if err := app.SetRoot(table, true).Run(); err != nil {
		logf("application error: %v", err)
		os.Exit(1)
	}
	logf("=== test end ===")
}
