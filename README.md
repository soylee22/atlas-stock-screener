# Atlas global stock screener

Open [Atlas on GitHub Pages](https://soylee22.github.io/atlas-stock-screener/).

The hosted table reads a public data snapshot. GitHub Actions publishes updates every four hours.
Quotes and FX refresh daily. Financial profiles and company icons load in bounded batches.
Refresh data reloads the most recent published snapshot. Saved screens and watchlists stay in your browser.

## Run locally

Start or restart it:

```sh
cd atlas-stock-screener
./run.sh --port 8765
```

The service uses Python, FastAPI, yfinance, pandas and SQLite.
Install dependencies from `requirements.txt` if they are absent.
The current build uses yfinance 0.2.65.
The server binds to this Mac's loopback address.
The optional local service supports immediate refreshes and priority company loading.

## Use the table

Select market cards to combine the UK, US, Canada, Japan, South Korea and Taiwan.
Main listings only is enabled by default.
It hides OTC quotes, London secondary venues, recognised depositary wrappers, funds and leveraged products.
Matching company names prefer a home-market counterpart across the complete six-market universe.
When Yahoo home-country data is absent, the busiest recognised exchange listing is retained.
Separate share classes in the chosen market remain visible.
The classification is practical and does not certify an exchange's primary-listing register.
Switch it off to restore all stock listings. The switch is saved with your screen.
Search by company name or Yahoo ticker.
Add numeric, text, availability or missing-value filters for any column.
Numeric filters accept `10B`, `100M`, `250K` and percentages.
Filters combine with AND.
Select column headings to sort actual numbers.
Missing values stay at the end in either direction.

Use Columns to add, remove and reorder metrics.
Save screen stores markets, filters, sorting and columns in this browser.
The star beside a ticker adds it to the browser's watchlist.
Export prepares all matching rows with raw numeric values and period metadata.
Use Download CSV or Copy CSV. Copy CSV works in embedded browsers that block generated file downloads.

Select a company to open its stock deep dive.
Select Explore dividend history for annual dividends per share and individual events.
The history uses every dividend event returned by Yahoo.
The current calendar year is marked YTD.
Use the currency switch for local amounts or current-FX USD comparisons.
Export history downloads annual totals or ex-dividend events.

## Data method

The universe comes from Yahoo's six regional screeners.
Listings are separate from issuers and can include secondary listings and depositary receipts.
Recognised ETFs, warrants and other instruments are excluded from the default stock view.
The coverage panel provides an option to include those records.
Taiwan preferred shares with reported market cap remain in the stock view.

Quotes and FX refresh daily while this service runs.
The refresh button starts a new complete quote scan.
Company financials load in a rolling queue, with visible rows prioritised.
Financial profiles are cached for seven days.
Missing financials remain blank.
The local public-data cache is ignored by Git at `data/screener.sqlite`.

Company icons use the website reported by Yahoo.
Google's favicon service supplies the actual website icon.
Validated PNG, JPEG, WebP, GIF or ICO files are stored in `data/logos/`.
The `logo_assets` table records each domain, file, source and fetch time.
Listings and share classes reuse the same file when their website domain matches.
Visible companies receive priority. Background loading covers known websites and newly fetched profiles.
Icons are cached for 30 days. Failed downloads retry after a day.
Missing websites or icons keep the letter badge. Website icons can differ from TradingView's logo library.

Prices use quote currency and explicit pence factors.
Market cap uses the base quote currency.
Statements use their reporting currency.
Historical financial totals use current cached FX, rather than period-average FX.
Volume is a share count.
Ratios and growth rates are not dollar amounts.

Income and cash flow use four consecutive quarters where available.
The latest fiscal year is the fallback.
Net margin uses income and revenue for the same period.
Free cash flow is operating cash flow less capital expenditure.
FCF growth compares the latest two fiscal years.
Percentage growth is withheld when prior FCF is non-positive.

Dividend totals use local-currency, split-treated Yahoo events.
Growth calculations exclude the incomplete current year.
Special dividends and source gaps can affect the observed growth streak.
The streak does not certify a company's official dividend-growth record.
Event dates are ex-dividend dates, rather than cash payment dates.

## Checks and limitations

Run focused checks:

```sh
python3 -m pytest tests -q
node tests/test_format.mjs
node tests/test_snapshot.mjs
node --check static/app.js
```

The initial live scan reported small differences between Yahoo totals and unique returned records.
The coverage panel shows exact counts and reconciliation gaps for each market.
Full source completeness has not been certified against exchange inventories.
Yahoo silently repeats pages near its 10,000-result limit.
The importer partitions large queries and rescans price boundaries to reduce omissions.
Quotes retain their provider timestamps and exchange delays.
Unsupported currencies remain missing rather than receiving an invented conversion.

Saved screens are local to this browser.
Local data refresh requires the service to remain running. GitHub Pages refreshes through GitHub Actions without a local server.
No login service or system startup job was installed.

## Publish and refresh

The workflow `.github/workflows/pages.yml` deploys the generated `public/` directory.
Enable GitHub Pages with GitHub Actions as the source.
A push to main deploys the cached data without waiting for Yahoo.
Scheduled and manual runs refresh Yahoo within a twelve-minute budget.
Use Run workflow to request an immediate quote scan or change the company batch limit.

The initial `seed/bootstrap.tar.gz` contains only public Yahoo data and company website icons.
Actions caches preserve the SQLite market database and downloaded icons between refreshes.
If Yahoo fails or a scan is incomplete, the previous quote snapshot is retained.
The coverage panel reports source dates, gaps and refresh errors.
The source API is unofficial and Yahoo can throttle requests.
Financial coverage is progressive. Missing metrics remain blank.

Build a portable snapshot locally:

```sh
python3 refresh_data.py --seed-only
python3 build_site.py --output public
```

Custom CSV downloads use a service worker to serve an in-memory file response.
Exports and saved screens are never uploaded or stored by the site.
Site links, data and icons use relative paths for GitHub project Pages.

## Quadrant explorer

Open Quadrant explorer beside Table. It shares the current screen filters and uses every matching listing with both selected metrics available.
Choose any two numeric metrics, axis preferences and linear, signed-log or percentile scales.
Medians are the default cut-offs. Top quartile uses the 75th percentile for higher values and the 25th percentile for lower values. Custom targets accept numeric suffixes such as 1B.
Exact cut-off ties belong to the preferred side. Percentile positions use average ranks for tied values.
The best-fit line uses ordinary least squares in the displayed scales. Its correlation and R² describe the full paired population.
Zone selection hides other points without recalculating the cut-offs or fit. The list shows up to 100 stocks ranked by average preferred percentile.
Click a point or list entry for its deep dive. Chart settings are included in saved screens. Export screen includes both selected axis metrics and all matching listings.
Missing values are excluded from the plot and counted visibly. Dividend growth streaks remain observed Yahoo history.
