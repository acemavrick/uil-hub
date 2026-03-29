# UIL Results Hub

> Fork of [warithr621/uil-hub](https://github.com/warithr621/uil-hub)

A web app for viewing UIL academic competition results, scraped from Speechwire. Supports all 10 UIL academic contests across all classifications (1A-6A).

## Features

- View district results (single region or all regions) and regional results
- Wildcard team indicator (`*`) — best 2nd-place team per region
- Dynamic year support (auto-discovers available years)
- Concurrent scraping — all districts/regions fetched in parallel
- Science sub-event sorting (overall/bio/chem/phys)
- CS programming score column
- CSV export for individual and team results
- Region-based color coding

## Setup

```bash
git clone https://github.com/acemavrick/uil-hub.git
cd uil-hub
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000

## Usage

1. Select year, classification (1A-6A), view type, and contest
2. Click "Get Results"
3. Wildcard teams are marked with `*` next to their rank
