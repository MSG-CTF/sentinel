// Local functional canary, not a competition challenge or a published GHCR image.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"time"
)

const faultFile = "/tmp/sentinel-functional-fault"

func main() {
	if len(os.Args) == 3 && os.Args[1] == "fault" {
		mode := os.Args[2]
		if mode != "none" && mode != "wrong_result" && mode != "unavailable" && mode != "invalid_json" {
			os.Exit(2)
		}
		if err := os.WriteFile(faultFile, []byte(mode), 0600); err != nil { os.Exit(3) }
		return
	}
	noncePattern := regexp.MustCompile(`^[a-f0-9]{32}$`)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /calculate", func(w http.ResponseWriter, r *http.Request) {
		left, e1 := strconv.Atoi(r.URL.Query().Get("left"))
		right, e2 := strconv.Atoi(r.URL.Query().Get("right"))
		nonce := r.URL.Query().Get("nonce")
		if e1 != nil || e2 != nil || left < 0 || left > 1000 || right < 0 || right > 1000 || !noncePattern.MatchString(nonce) {
			http.Error(w, "invalid input", http.StatusBadRequest); return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		mode, _ := os.ReadFile(faultFile)
		if string(mode) == "unavailable" { w.WriteHeader(503); return }
		if string(mode) == "invalid_json" { _, _ = w.Write([]byte("{")); return }
		result := left + right
		if string(mode) == "wrong_result" { result++ }
		_ = json.NewEncoder(w).Encode(map[string]any{"service": "sentinel-functional-fixture-v1", "nonce": nonce, "result": result})
	})
	server := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 2*time.Second, WriteTimeout: 3*time.Second, IdleTimeout: 5*time.Second, MaxHeaderBytes: 4096}
	log.Fatal(server.ListenAndServe())
}
