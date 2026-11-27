package main

import (
	"encoding/json"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"
)

func main() {
	port := flag.String("port", "21010", "http listen port")
	flag.Parse()

	// 安装目录与可写目录由系统通过环境变量注入；cwd 是 data 目录，不能用来定位安装目录
	installDir := os.Getenv("UGAPP_INSTALL_DIR")
	if installDir == "" {
		installDir, _ = os.Getwd()
	}
	logDir := os.Getenv("UGAPP_LOG_DIR")
	logFile := setupFileLog(logDir)
	if logFile != nil {
		defer logFile.Close()
	}

	// 第一件事就是监听端口：系统只以端口可连作为启动成功的判据
	ln, err := net.Listen("tcp", ":"+*port)
	if err != nil {
		log.Fatalf("listen on :%s failed: %v", *port, err)
	}
	log.Printf("listening on %s, install_dir=%s", ln.Addr().String(), installDir)

	mux := http.NewServeMux()

	mux.HandleFunc("/api/hello", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]any{
			"message": "hello from demoapp backend",
			"time":    time.Now().Format(time.RFC3339),
		})
	})

	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})

	mux.HandleFunc("/diag", func(w http.ResponseWriter, r *http.Request) {
		wd, _ := os.Getwd()
		host, _ := os.Hostname()
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]any{
			"alive":       true,
			"cwd":         wd,
			"install_dir": installDir,
			"data_dir":    os.Getenv("UGAPP_DATA_DIR"),
			"log_dir":     logDir,
			"hostname":    host,
			"goos":        runtime.GOOS,
			"goarch":      runtime.GOARCH,
			"pid":         os.Getpid(),
			"uid":         os.Getuid(),
			"gid":         os.Getgid(),
			"listen":      ln.Addr().String(),
			"time":        time.Now().Format(time.RFC3339),
		})
	})

	// 静态页面在安装目录下（安装目录只读，但可读）
	wwwDir := filepath.Join(installDir, "www")
	if st, err := os.Stat(wwwDir); err == nil && st.IsDir() {
		mux.Handle("/", http.FileServer(http.Dir(wwwDir)))
	} else {
		log.Printf("www dir unavailable: %s (err=%v)", wwwDir, err)
		mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("demoapp backend ready\n"))
		})
	}

	// 收到 SIGTERM 时优雅退出（系统要求 10 秒内退出）
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	idle := make(chan struct{})
	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
		<-sig
		log.Printf("shutting down")
		srv.Close()
		close(idle)
	}()

	if err := srv.Serve(ln); err != nil && err != http.ErrServerClosed {
		log.Fatalf("serve failed: %v", err)
	}
	<-idle
}

// setupFileLog 额外写一份日志到系统可写日志目录，便于排查
func setupFileLog(logDir string) *os.File {
	if logDir == "" {
		return nil
	}
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil
	}
	f, err := os.OpenFile(filepath.Join(logDir, "demoapp.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil
	}
	log.SetOutput(io.MultiWriter(os.Stdout, f))
	return f
}
