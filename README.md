# Atlas global stock screener

Open [Atlas on GitHub Pages](https://soylee22.github.io/atlas-stock-screener/).

The hosted table reads a public data snapshot. GitHub Actions starts the overnight refresh at 01:23 UK time each day.
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

Select market cards to combine the UK, US, Canada, Japan, South Korea, Taiwan, Germany, Spain, Italy, the Netherlands, Denmark and Sweden.
Main listings only is enabled by default.
Only explicitly confirmed primary listings are eligible, including genuine ordinary share classes.
TradingView's public screener supplies primary-exchange designations using exact venue and ticker mappings.
Prices, financial statements and dividends remain Yahoo data.
Foreign trading lines stay excluded when the primary market is outside the twelve covered markets.
Unknown mappings are excluded until confirmed. Switch the filter off to view them.
OTC quotes, recognised wrappers, preferred securities and other products remain excluded.
Domicile and trading volume do not establish primary status.
The catalogue refreshes nightly and a failed refresh retains the last complete cache.
This uses a provider designation rather than an exchange-certified primary-listing register.
The bundled seed supplies designations for initial builds without an additional network requirement.
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

The universe comes from Yahoo's twelve regional screeners.
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
A push to main deploys the cached data. The first European import runs a bounded financial bootstrap before publishing.
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

Chart navigation: drag to pan, scroll or pinch to zoom, and use the zoom buttons for precise steps. Fit all restores the data extent. Centre cut-offs moves the quadrant intersection to the middle at the current zoom. Maximise chart opens a larger view with the same controls. Restore chart or Escape returns to the page.

The purple Pareto frontier marks observed listings for which no other matching listing is at least as good on both raw metrics and strictly better on one. It respects each axis preference and retains exact duplicate pairs. Frontier only limits plotted stocks and the list. Navigation and zone selection do not change the comparison population. The line joins observed frontier points and does not promise intermediate combinations.

The list defaults to a balanced score: half the preferred X percentile plus half the preferred Y percentile, on a 0 to 100 scale. Order can instead use preferred X, preferred Y or frontier membership first. Balanced score breaks secondary ties, then symbol. Each axis has one Scale menu with Linear, Signed log and Percentile options. Signed log retains zero and negative values. Chart settings, including the viewport, are saved with screens.

### Annual growth and 52-week distance

Revenue and net income offer 1-year fiscal-year growth and 3-year, 5-year and 10-year CAGR. Growth uses nominal reporting currency so current FX movements do not create growth. Dates accompany each metric in stock details and CSV exports.
Yahoo usually returns four annual statements. These support 1-year growth and 3-year CAGR. Longer horizons remain unavailable until the cache holds consecutive years. The cache retains older annual observations as Yahoo windows advance. Changing reporting currency clears incompatible observations. No SEC or other source supplements this history.
A positive starting value is required for percentage growth. One-year net income growth can include a move from profit to loss. Multi-year CAGR requires a non-negative endpoint. Missing years and irregular fiscal spans remain unavailable.
Below 52W high is 100 × (high − latest price) / high in matching local quote units. Zero means at the high. Lower values mean closer. A negative value means above the provider's quoted high. This differs from the existing 52-week price change.
Annual statements load independently of full company profiles across the main-listing universe. Nightly collection allows up to four hours and 30,000 full-profile attempts with two concurrent requests, followed by a 20-minute annual-only batch. Unfetched main companies take priority across all twelve markets. Run `python refresh_data.py --growth-only --seconds 1200 --limit 30000 --workers 4` to advance the queue. Provider failures remain pending and retry after a cooldown. A rate limit stops the batch. Full profile refreshes update and retain the same annual history.
The chart separates sources not fetched yet from unavailable or undefined values. All zone statistics describe the plotted population. Source gaps and invalid growth bases do not become zeroes.

## Stock AI analysis pack

Open a company deep dive and select Create AI analysis pack.
Copy prompt + data for AI puts the full instructions and structured observations on the clipboard.
Copy JSON provides the same pack as JSON. Download JSON and Download CSV provide files.
The preview offers the complete pack, JSON, CSV or the prompt alone.
Use the copy buttons if an embedded browser blocks generated downloads.

The detailed prompt centres analysis on net income, earnings direction, profitability and cash conversion.
It requests dividend history and cover, balance-sheet trends, industry and sector comparisons, valuation scenarios and plots.
Every available cached income statement, balance sheet and cash-flow row is included, annually and quarterly.
Reporting-currency values remain intact. USD values use current cached FX.
Share counts, ratios and per-share figures have separate units.
Missing data, reporting dates, fetch dates and unavailable growth horizons are explicit.

Peer summaries use complete known industry and sector cohorts, independent of active screen filters.
Each metric reports its own coverage. Up to 24 companies nearest by market cap supply individual peer rows.
The labelled ROIC proxy uses after-tax operating income and average book debt plus equity less cash.
It is withheld for financial-sector businesses or invalid inputs. It is distinct from ROE.

Full statement histories load progressively during existing scheduled refreshes.
A three-minute batch retains them across the twelve markets, with a cooldown after provider failures.
Run `python refresh_data.py --statements-only --seconds 180 --limit 60` to advance this queue.
The pack is prepared in the browser. No AI service receives it automatically.

Moving averages use completed Yahoo Close observations, adjusted for share splits but not dividends. Add `200-day SMA distance` or `200-week SMA distance` as a column, filter or quadrant axis. Positive means above the average. Negative means below. Weekly averages use completed weekly closes rather than an approximation from daily averages.

Use **Moving averages** to set the custom window across columns, filters, quadrant axes and exports. Supported windows are 2 to 500 trading sessions or 2 to 260 completed weeks. Price histories load in scheduled batches, so missing history stays unavailable. Custom histories load separately when needed. Standard 200-day and 200-week windows stay fixed.

The quadrant list has its own **Export top list** button. Export the displayed top 100 or all ranked results. The CSV keeps the current ordering, selected zones and frontier restriction. It includes raw values, score, frontier membership and periods. The chart viewport does not restrict this list.

Statement currency verification uses matching dated raw Yahoo timeseries records. Profile currency metadata can differ from statement currency. Conflicting legacy financial totals are withheld until verified. Annual history survives empty profile responses. Empty profile frames do not certify that annual history is unavailable. Annual-only source checks retry stale observations after seven days.

## European markets

Germany, Spain, Italy, the Netherlands, Denmark and Sweden extend the original six markets.
Their Yahoo queries filter equities by positive market cap to reduce secondary-product feeds.
The returned quote can still omit its market cap. These imported universes are not full exchange inventories.
The main-listing filter prefers Xetra over matching Frankfurt quotes and hides recognised Italian foreign-equity tickers.
EUR, DKK and SEK amounts use the existing cached Yahoo FX conversion.
`seed/europe.json` adds the initial public market records without replacing newer cached observations.
The existing refresh queues progressively load company financials, dividends and annual histories across all twelve markets.

### Security classification

Yahoo can give preferred shares and warrants the parent company name.
`listing_types.py` applies market-specific ticker rules and cached Nasdaq security descriptions.
The public seed retains the Nasdaq descriptions and verified issuer classifications for known European preference or savings shares.
Nasdaq directories refresh daily through the existing scheduled workflow. Failed downloads retain the previous complete catalogue.
All financial figures and prices still come from Yahoo. Ordinary share classes and operating REIT or partnership units remain eligible.
Main listings only excludes these securities across table, chart, rankings, peers and exports. Switching it off exposes all cached listings.
Classification remains practical. Unrecognised share classes and primary venues outside the covered markets remain limitations.

## Overnight cloud collection

The scheduled GitHub runner works without a local computer. Two bounded profile batches share a four-hour budget. A halfway cache checkpoint and a final cache save preserve collected records. The published recovery archive restores public histories if the Actions cache is lost. Stock details are compressed without dropping statements or dividend events. Each nightly run refreshes quotes and FX. Weekly profile caching avoids repeatedly fetching companies already collected. The initial collection can take several nights. Yahoo rate limits pause profile requests for five minutes. Collection resumes within the remaining overnight time budget. If that budget expires, completed records remain cached for later nights. Missing provider history remains unavailable.

The Pages site reads a published snapshot. Opening the chart does not start a source scan. Refresh data reloads the latest publication. Data coverage links to the cloud workflow and shows the last completed batch. GitHub can delay scheduled runs. Public repository schedules can be disabled after 60 days without repository activity.

Manual Actions runs accept `enrich_limit` up to 30000 and `refresh_seconds` up to 14400. Smaller values allow a bounded refresh check. Code pushes publish the cached snapshot without the overnight scan.

### Capital returns

ROIC proxy and ROCE are available in columns, filters, quadrant axes, company details and AI packs. ROIC retains the after-tax operating-income method over average debt plus equity less cash. ROCE uses Yahoo EBIT over average total assets less current liabilities. Both need matched reporting currencies and positive opening and closing annual capital balances. Financial-sector businesses are excluded.

The 5Y average fields require five valid consecutive annual ratios ending at the latest FY. They are arithmetic means, not CAGR. A shorter window stays unavailable. The valid-year counts and annual history explain coverage. Yahoo usually returns four annual statements, and the earliest annual ratio also needs its opening balance. Retained source histories can build a longer window over time. Cached rows are recomputed during publication without additional Yahoo requests.

The large stock index and cached price histories are also compressed on Pages. Schema and status remain plain JSON. Compression preserves every observation and prevents the growing catch-up cache from exceeding the site budget. The coverage panel reports actual capital-return availability separately from headline profile counts. Yahoo does not provide a direct ROIC field in the statement feed used here.


### Earnings per share

**Diluted EPS** is an optional Financials column, filter and quadrant axis. It also appears in company details and AI packs. Select it in Columns. The figure sums the latest four consecutive quarterly Diluted EPS observations when complete, otherwise it uses the latest annual figure. Missing values, unknown reporting currency and gaps remain unavailable. Negative earnings and zero EPS are retained.

The table reports USD per share at current cached reporting-currency FX. Its CSV automatically includes the EPS period, reporting currency and statement fetch time. Statement history retains local per-share observations for trends. No extra Yahoo request is made for this metric.

Quarterly EPS can use different diluted weighted-average share counts. Their rolling sum is not a recalculated annual weighted-share ratio. Yahoo split treatment is retained. Different share denominations make absolute EPS unsuitable for comparing business quality or cheapness between companies. Use EPS trends alongside total income and valuation.


## Large-cap scope and weekly Williams %R

The published site contains listings with cached USD market cap at least $20bn. Confirmed primary ordinary listings remain the default. The table, quadrant, exports and peer cohorts use this smaller published population. Quote discovery still covers all twelve markets. The recovery archive keeps smaller companies and their previously collected histories. Automatic financial and technical collection targets confirmed primary companies above the floor. Existing screens receive a visible $20bn starting filter without losing their other filters or moving-average settings. Removing that filter cannot restore smaller companies absent from the published snapshot.

Weekly Williams %R uses 14 consecutive weekly High, Low and Close candles from Yahoo. It calculates `-100 × (highest high − latest close) / (highest high − lowest low)`. Oversold includes -80 and lower. Overbought includes -20 and higher. The Weekly oversold button applies the same numeric filter in both sections. Williams defaults to lower preferred when selected as an axis. Quadrant zones still follow the selected median, quartile or custom targets. Use a custom -80 target for an exact oversold boundary.

Candles include the developing week through the previous completed daily session. A source-date and period label identifies this provisional reading. This differs from an intraday chart and from an indicator restricted to completed weekly candles. Yahoo candles are split adjusted, without dividend adjustment. Missing highs or lows, insufficient history, missing weeks and flat ranges stay unavailable. A low Williams reading describes recent price position and does not establish business value. Source periods accompany CSV and ranked exports.

A separate daily GitHub step refreshes large-cap technical history before financial requests, with a 20-minute budget and a maximum of 1,200 companies. It also runs on publication pushes to backfill a new indicator. Completed data survives provider failures through the existing cache and recovery archive. Williams does not add financial-statement requests.
