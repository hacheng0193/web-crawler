# Search crawler MVP

這是一個零外部依賴的 search-engine crawler MVP：使用 100 個 curated seed URL，預設最多執行 600 秒（10 分鐘），遵守 robots.txt，對同一 host 保持請求間隔，並將 discovered 與 crawled 分開保存。Frontier 使用 per-host priority queue 加 round-robin host scheduler，避免同一個 parent 發現的大量 sibling URL 連續佔滿 worker。

## 執行

```bash
python3 crawler.py
```

執行中 stdout 會即時顯示：

- `[SEED]`：初始 100 個 seed URL
- `[NEW]`：首次發現、加入 frontier 的 URL
- `[FETCHED]`：已處理完成的 URL、HTTP status 與 depth

若只想保留最後的 summary 和檔案輸出，可加上 `--no-live`。

預設參數：

- `100` 個 seed，來源是 `seed_urls.json`
- runtime hard cap：`600` 秒
- `8` 個 worker
- 同一 host 請求間隔：`5` 秒
- 每頁 HTTP timeout：`12` 秒
- 最多記錄 `10000` 頁
- 最大 crawl depth：`3`

第一次快速驗證可把 runtime 改短：

```bash
python3 crawler.py --runtime-seconds 30 --max-pages 100 --output-dir output-smoke
```

## Seed set

除了預設的 100 seeds，已生成三組 nested seed set：

- `seed_urls_300.json`
- `seed_urls_500.json`
- `seed_urls_1000.json`

使用方式，例如 500 seeds 跑 48 小時：

```bash
python3 crawler.py \
  --seed-file seed_urls_500.json \
  --runtime-seconds 172800 \
  --max-pages 500000 \
  --output-dir output-500-48h
```

三組都保留原本 100 筆 curated seeds，後續網域依 Tranco snapshot `GQNVK` 的排名追加，並排除明顯的廣告、追蹤、CDN/DNS infrastructure、成人與 gambling 網域。詳細來源記錄在 `seed_generation_metadata.json`；這些清單是候選 seed pool，仍應透過實際 crawler 統計 HTTP success、robots blocked 與 content quality。

輸出：

- `output/discovered.jsonl`：每發現一個新 URL 就立即 append 一行；可用行數計算 total discovered URLs
- `output/crawled.jsonl`：每次 fetch 完成就立即 append 一行，包含 `request_started_at`（真正發出 HTML request 前的時間）、`fetched_at`、`success`、status、title、depth、parent URL、發現連結數與錯誤
- `output/discovered_pending.json`：執行結束時仍待處理的 discovered dict；已完成 fetch 的 URL 會從這個 pending dict 移除
- `output/crawl_summary.json`：本次執行摘要，直接包含 `total_discovered` 與 `successful_crawled`

`discovered.jsonl` 是永久的 discovery event log，因此即使 URL 後來被移入 crawled，仍保留在檔案中，方便計算 total discovered。`discovered_pending.json` 則代表執行結束時尚未 crawl 的 URL。

每一筆 discovered 與 crawled 記錄都會立即 `flush()`；因此中途停止時，已完成的 fetch 結果仍會保留，不必等到整個 10 分鐘結束。

## 測試

```bash
python3 -m unittest discover -s tests -v
```

## MVP 邊界

目前只抓 HTML，不做 JavaScript rendering、全文索引、canonical tag、sitemap ingestion、跨程序 frontier persistence 或重試隊列。這版的目標是先驗證 seed quality、URL discovery throughput 與 10 分鐘內的可運行性；下一步再接 SQLite/Postgres、內容 tokenizer 與 ranking。
