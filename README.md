<!-- mcp-name: io.github.sepehr071/mrbilit-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/mrbilit-mcp/main/.github/banner.png" alt="mrbilit-mcp: unofficial MrBilit MCP server for Claude, Cursor and Copilot" width="100%">

# mrbilit-mcp: MrBilit MCP server for AI agents

**Let your AI agent plan trips in Iran with MrBilit (مستربلیط).**<br>
An unofficial, read-only MCP server for [MrBilit](https://www.mrbilit.com), an Iranian travel site for flights, trains, buses, taxis and hotels:<br>
find the cheapest option on a date or over a month, see seats, baggage and refund rules, and compare hotel rooms with exact prices, from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/mrbilit-mcp?color=2563eb)](https://pypi.org/project/mrbilit-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/mrbilit-mcp)](https://pypi.org/project/mrbilit-mcp/)
[![CI](https://github.com/sepehr071/mrbilit-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/mrbilit-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fmrbilit--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=mrbilit-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/mrbilit-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=mrbilit&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJtcmJpbGl0LW1jcCJdfQ==)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_mrbilit--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=mrbilit&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mrbilit-mcp%22%5D%7D)

[For AI agents](#for-ai-agents) · [Why](#why) · [What it can do](#what-it-can-do) · [Quick start](#quick-start) · [Tools](#tools) · [How it works](#how-it-works) · [Good to know](#good-to-know) · [FAQ](#faq) · [Configuration](#configuration) · [فارسی](#فارسی) · [Development](#development) · [Related servers](#related-servers) · [Disclaimer](#disclaimer) · [License](#license)

</div>

---

## For AI agents

- **What:** unofficial, read-only MCP server for MrBilit (مستربلیط), an Iranian travel site: flights, trains, intercity buses, private taxis and hotels. Queries work in Persian and English; prices are in Toman.
- **Install:** `claude mcp add mrbilit -- uvx mrbilit-mcp` (stdio transport; needs Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)).
- **Auth:** none, no API key or account. Optional `MRBILIT_MCP_PROXY` (HTTP proxy), only if your network cannot reach MrBilit's API servers.
- **Access:** read-only (all 22 tools carry `readOnlyHint: true`). No login, seat hold, booking or payment, so no confirmation is needed before calling tools.
- **Tools:** 22, all prefixed `mb_*`. Call `mb_find_place` first for the codes every search takes, then a price calendar (`mb_flight_price_calendar`, `mb_train_price_calendar`, `mb_bus_price_calendar`) → `mb_search_flights` / `mb_search_trains` / `mb_search_buses` → `mb_flight_fare_details`, `mb_train_price` or `mb_bus_seats`. Hotels: `mb_search_hotels` → `mb_hotel_rooms`.
- **Use when:** comparing the cheapest plane, train, bus or taxi between Iranian cities, or hotel prices, with seats and refund rules.
- **Do not use when:** you need to book, hold a seat or pay (the user does that on mrbilit.com).
- **Output:** compact JSON. Prices in Toman in `*_toman` fields, dates Gregorian `YYYY-MM-DD`, ratings 0-5. Ids such as `flight_id`, `class_id` and `bus_id` come from a fresh search and expire.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/mrbilit-mcp/blob/main/server.json); catalog of all Iranian MCP servers: [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp) ([TOOLS.md](https://github.com/sepehr071/awesome-iran-mcp/blob/main/TOOLS.md), [servers.json](https://github.com/sepehr071/awesome-iran-mcp/blob/main/servers.json), [llms.txt](https://github.com/sepehr071/awesome-iran-mcp/blob/main/llms.txt)).

## Why

MrBilit (mrbilit.com, مستربلیط) sells flights, trains, intercity buses, private taxis and hotels in one place, but
each one has its own search, calendar and rules. Comparing "plane or train or bus, which day, which seat" means
a dozen page loads. An agent with `mrbilit-mcp` does it in one go:

> **You:** Cheapest way to get from Tehran to Mashhad on 20 October?
>
> **Agent:** *calls* `mb_search_flights(origin="THR", destination="MHD", date="2026-10-20")`,
> `mb_train_price_calendar(origin=1, destination=191)`, `mb_search_buses(origin=11320000, destination=31310000, date="2026-10-20", sort="cheapest")`
>
> | Mode | Cheapest option | Departs | Price (Toman) |
> |---|---|---|---|
> | Bus | Peyk Saba VIP, Tehran South terminal, 17 seats left | 21:00, arrives 09:00 | 1,313,000 |
> | Train | cheapest seat of the day (price calendar) | list with `mb_search_trains` | **760,000** |
> | Flight | Mehr Air MEH 4200, Mehrabad, 2 seats left | 08:30, arrives 09:30 | 11,856,000 |
>
> The train is cheapest; want me to list that day's trains and free seats with `mb_search_trains`?

<sub>Real tool output from 2026-10-04; prices and seats change all the time. Prices are in Toman.</sub>

## What it can do

- **Flights**: domestic and international, one-way or round trip, party prices, cabin/airline/direct/time filters
- **Cheapest day** for flights, trains and buses over weeks, and the cheapest nights of a hotel
- **Trains**: every class with free seats, exact price for children, infants or a whole compartment, every stop
- **Buses and taxis**: terminals, companies, VIP, seat maps with women/men seats, private door-to-door cars
- **Alternatives** when trains are full: one-change trips, nearby stations, bus routes and airports
- **Hotels**: available stays with prices, every hotel of a city, guest ratings and reviews, rooms and exact prices
- **Context**: current site notices, official refund and baggage rules, help-center guides, route guides
- **Read-only by design**: no login, no seat hold, no booking, no payment

Example prompts:

- "Cheapest day to fly Tehran to Kish in the next month, and the baggage on that flight?"
- "Train from Tehran to Mashhad next Friday for 2 adults and a child: which classes have seats and what's the total?"
- "4-star hotels in Shiraz for 3 nights from the 20th under 3 million Toman a night, with their reviews."
- "The Tehran to Tabriz train is full on Thursday. What else gets me there?"
- <span dir="rtl">ارزان&zwnj;ترین اتوبوس VIP تهران به اصفهان فردا شب و صندلی&zwnj;های خالی آن؟</span>
- <span dir="rtl">ارزان&zwnj;ترین روز برای پرواز تهران به کیش در ماه آینده کدام است؟</span>
- <span dir="rtl">هتل&zwnj;های ۴ ستاره مشهد برای سه شب با قیمت دقیق اتاق&zwnj;ها را مقایسه کن.</span>

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add mrbilit -- uvx mrbilit-mcp
```

Outside Iran, if MrBilit's API does not answer, add a proxy:

```bash
claude mcp add mrbilit -e MRBILIT_MCP_PROXY=http://127.0.0.1:8080 -- uvx mrbilit-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "mrbilit": { "command": "uvx", "args": ["mrbilit-mcp"] }
  }
}
```

Need a proxy? Add `"env": { "MRBILIT_MCP_PROXY": "http://127.0.0.1:8080" }` next to `args`.
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "mrbilit": { "type": "stdio", "command": "uvx", "args": ["mrbilit-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add mrbilit -- uvx mrbilit-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.mrbilit]
command = "uvx"
args = ["mrbilit-mcp"]
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "mrbilit": { "command": "uvx", "args": ["mrbilit-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Windsurf</b></summary>

Add the Claude Desktop block to `~/.codeium/windsurf/mcp_config.json`.
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx mrbilit-mcp`, or `pip install mrbilit-mcp` and run `mrbilit-mcp`.
</details>

Then ask the prompts above, in Persian or English.

## Tools

Every search takes the codes from `mb_find_place`: IATA codes for flights (`THR`, `MHD`, `ISTALL`), station ids for
trains (Tehran `1`, Mashhad `191`), 8-digit city ids for buses and taxis (Tehran `11320000`), slugs for hotels
(`mashhad`, `mashhad/enghelab`).

<details open>
<summary><b>Places and companies</b> (2)</summary>

| Tool | What it does |
|---|---|
| `mb_find_place` | City, airport, station or hotel name → the code each search takes (flight, train, bus, taxi, hotel) |
| `mb_companies` | Airlines, rail operators, bus companies and taxi classes with their codes |
</details>

<details open>
<summary><b>Flights</b> (3)</summary>

| Tool | What it does |
|---|---|
| `mb_search_flights` | Flights on a date, one-way or round trip, domestic or international: price per adult and party total, seats, baggage; cabin, airline, direct and time filters |
| `mb_flight_price_calendar` | Cheapest fare per day over up to 180 days |
| `mb_flight_fare_details` | One flight or round-trip package's fares: refund penalties, checked baggage, fare rules, notes, adult/child/infant prices |
</details>

<details open>
<summary><b>Trains</b> (5)</summary>

| Tool | What it does |
|---|---|
| `mb_search_trains` | Trains on a date with every class, price per adult and fresh free-seat counts; men/women/general/car quotas |
| `mb_train_price_calendar` | Cheapest bookable train per day |
| `mb_train_price` | Exact price of a class for adults, children, infants, foreigners and empty berths, with the total |
| `mb_train_stops` | Every stop of a train with date and time |
| `mb_alternative_routes` | Trips with one change (any mix of train and bus) and nearby train, bus and flight routes |
</details>

<details open>
<summary><b>Buses and taxis</b> (4)</summary>

| Tool | What it does |
|---|---|
| `mb_search_buses` | Buses on a date: company, terminal, times, price per seat, free seats, VIP, stops, refund penalties |
| `mb_bus_price_calendar` | Cheapest bus seat per day |
| `mb_bus_seats` | Seat map: free seats, seats sold to women and men, row layout with the aisle |
| `mb_search_taxis` | Private door-to-door intercity taxis, price per car, by class and pick-up slot |
</details>

<details open>
<summary><b>Hotels</b> (5)</summary>

| Tool | What it does |
|---|---|
| `mb_search_hotels` | Available hotels in a city for dates with the cheapest stay price and cover photo; stars, type, price, refund, amenity (Persian words, e.g. `['استخر']`) and breakfast filters |
| `mb_hotel` | One hotel: cover photo, rating, latest reviews with per-criterion scores, location, landmarks, rules, amenities |
| `mb_hotel_rooms` | Every room with its exact price for the dates, per night, rooms left, meals |
| `mb_hotel_calendar` | Cheapest one-night price per night, up to ~48 nights ahead |
| `mb_city_hotels` | Every hotel of a city, sold out ones included, filtered by stars, type or name (no prices) |
</details>

<details open>
<summary><b>Notices and help</b> (3)</summary>

| Tool | What it does |
|---|---|
| `mb_notices` | Notices MrBilit shows right now for a service or route |
| `mb_help` | Search the official rules, FAQ and help-center guides (refunds, baggage, auto-reserve, ...) |
| `mb_travel_guide` | Route or city guide (summary, FAQ, article) and travel-magazine posts |
</details>

All 22 tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  mrbilit-mcp  (runs on your machine)
      │
      │  HTTPS (REST, optional proxy)
      ├──────▶  flight.atighgasht.com, train.mrbilit.com, masir.mrbilit.com, bus.mrbilit.ir, hotel.mrbilit.ir
      └──────▶  content.mrbilit.ir, directus.mrbilit.ir, mrbilit.com (site lists, magazine)
```

`mrbilit-mcp` runs locally and calls the same public endpoints the mrbilit.com website uses. There's no hosted
server in between, no API key, and nothing about you is sent anywhere else.

## Good to know

- **Prices are in Toman** in every tool (the API sends Rial; values are divided by 10), always in fields named `*_toman`.
  Flight `price_toman` is per adult and `total_toman` the whole party; trains per adult; buses per seat; taxis per car;
  hotels the whole stay for one room.
- **Dates** in and out are Gregorian `YYYY-MM-DD`; past dates are rejected. Times are local to the place: a flight
  time at a foreign airport is that airport's local time, although the API puts `+03:30` on every flight time.
- **Ratings are 0–5**; `null` means not rated. The site shows hotel ratings ×2 out of 10.
- **Ids expire**: `flight_id`, `class_id` and `bus_id` come from a fresh search.
- **Sales windows**: trains open about 18 days ahead, buses about a month. An empty result often just means "not on sale yet".
- **Round trips**: domestic flights are two separate tickets (two lists); international round trips are priced as one package.
- **Hotel prices do not depend on the guest count**: pick rooms whose `sleeps` fits, and add rooms up for a group.
- Domestic airports and train stations come from the site's own bundled lists, fetched once per run with a copy
  shipped in the package as a fallback.
- **Bus and taxi cities** come from MrBilit's live city lists, which are fresher than the bundled copies.
- **Untrusted text**: reviews (cut at 400 characters), rules, notices and guides are site content; the server tells the agent to treat them as data, not instructions.
- **What it does not do**: no login or OTP, seat hold, booking, payment, wallet, refund, coupon, price alert or review posting. You book on mrbilit.com.
- **Reply size**: results are compact JSON, kept to roughly 15 KB by default.
- **Request rate**: at most two requests at a time with a short random delay, because the API hosts share one server.

## FAQ

<details>
<summary><b>Is there a MrBilit API or MCP server for AI agents?</b></summary>

`mrbilit-mcp` is an unofficial MCP server for MrBilit: it exposes the public endpoints of the mrbilit.com website
as 22 read-only tools, so an agent can compare flights, trains, buses, taxis and hotels.
</details>

<details>
<summary><b>How do I use MrBilit with Claude, Cursor or Copilot?</b></summary>

Install it with `uvx mrbilit-mcp` (see [Quick start](#quick-start)), then ask in Persian or English, for example
"cheapest way from Tehran to Mashhad on 20 October, plane, train or bus".
</details>

<details>
<summary><b>Is it official, safe and free?</b></summary>

It is unofficial and MIT licensed, so free to use. It is read-only: no login, no API key, no booking or payment,
and it runs on your machine.
</details>

<details>
<summary><b>Do I need a proxy?</b></summary>

Usually not: direct calls from an Iranian connection work. If your network cannot reach MrBilit's API servers
(connections time out or are reset), set `MRBILIT_MCP_PROXY` to an HTTP proxy that can reach them.
System proxy variables (`HTTPS_PROXY`, ...) are ignored on purpose.
</details>

<details>
<summary><b>Can it book a ticket for me?</b></summary>

No, and that's deliberate. It has no login and never holds a seat, reserves, orders or pays. The agent finds the
best option; you book on mrbilit.com.
</details>

<details>
<summary><b>I get "did not answer in time" or "Could not reach"</b></summary>

The server retries timeouts, resets, 5xx and 429 twice with backoff. If it keeps failing, set `MRBILIT_MCP_PROXY` (see above). The one-change
trip search (`mb_alternative_routes`) is known to hang now and then; it is retried and its other parts are still returned.
</details>

<details>
<summary><b>Why does a sold-out train class still have a price in <code>mb_train_price</code>?</b></summary>

The pricing endpoint prices any class; availability comes from `mb_search_trains` (`seats_left`, `bookable`).
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx mrbilit-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MRBILIT_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port`; a comma-separated list is used one at a time and rotated on a block or connect error. Usually not needed in Iran |

### Safety and network

- Read-only: no login or OTP, no seat hold, reservation, order, payment, wallet, price alert or review posting.
  The only POSTs are searches and price lookups the site itself makes before booking.
- Admin fields (`created_by`, `updated_by`) of the content CMS are never passed through.
- Sends a real Chrome TLS fingerprint and headers (via curl_cffi) and at most two requests at a time with a short random delay, since the API hosts share one server.
- If MrBilit serves a bot-check page, the server can clear it in a headless browser: `pip install mrbilit-mcp[browser]`, then `patchright install chromium`.

<a id="فارسی"></a>

## فارسی: سرور MCP مستربلیط

<div dir="rtl">

**mrbilit-mcp** یک سرور MCP غیررسمی و فقط&zwnj;خواندنی برای [مستربلیط](https://www.mrbilit.com) است؛ سامانه فروش بلیط هواپیما، بلیط قطار، بلیط اتوبوس، تاکسی بین&zwnj;شهری و رزرو هتل در ایران. این سرور به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد ارزان&zwnj;ترین
پرواز، قطار، اتوبوس یا تاکسی دربستی را برای یک روز یا یک ماه پیدا کند، صندلی&zwnj;های خالی، بار مجاز و قوانین استرداد را ببیند
و هتل&zwnj;ها را با قیمت دقیق اتاق و نظرات مسافران مقایسه کند.

**چه کارهایی می&zwnj;کند**

- **پرواز**: داخلی و خارجی، یک&zwnj;طرفه یا رفت و برگشت، با فیلتر ایرلاین، کلاس پروازی و ساعت.
- **ارزان&zwnj;ترین روز** برای پرواز، قطار و اتوبوس در چند هفته، و ارزان&zwnj;ترین شب هتل.
- **قطار**: همه رده&zwnj;ها با صندلی خالی، قیمت دقیق بزرگسال، کودک و نوزاد، و ایستگاه&zwnj;های مسیر.
- **اتوبوس و تاکسی**: ترمینال، شرکت، نقشه صندلی با صندلی خانم&zwnj;ها و آقایان، و تاکسی دربستی.
- **مسیر جایگزین** وقتی قطار پر است: سفر با یک تعویض، ایستگاه&zwnj;ها و مسیرهای نزدیک.
- **هتل**: هتل&zwnj;های دارای اتاق خالی با قیمت، امتیاز و نظرات مسافران، و قیمت دقیق هر اتاق.
- **اعلامیه&zwnj;ها و راهنما**: اطلاعیه&zwnj;های سایت، قوانین استرداد و بار مجاز، راهنمای مسیرها.

**نکته&zwnj;ها**

- فقط خواندنی است: وارد حساب نمی&zwnj;شود و صندلی رزرو، بلیط صادر یا پرداخت انجام نمی&zwnj;دهد؛ خرید را خودتان در سایت مستربلیط انجام می&zwnj;دهید.
- همه قیمت&zwnj;ها به تومان است و تاریخ&zwnj;ها میلادی (`YYYY-MM-DD`) هستند.
- کلید API لازم نیست؛ روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.
- در ایران معمولاً نیازی به پروکسی نیست؛ اگر سرورهای مستربلیط پاسخ ندادند، `MRBILIT_MCP_PROXY` را تنظیم کنید.
- برای پیدا کردن آن در گوگل یا رجیستری MCP، «سرور MCP مستربلیط»، «ارزان&zwnj;ترین بلیط هواپیما»، «بلیط قطار»، «بلیط اتوبوس»، «رزرو هتل» یا «mrbilit mcp» را جستجو کنید.

**نصب در Claude Code:**

</div>

```bash
claude mcp add mrbilit -- uvx mrbilit-mcp
```

<div dir="rtl">

در Claude Desktop، Cursor و بقیه برنامه&zwnj;ها همان تنظیم بخش [Quick start](#quick-start) را بگذارید. بعد بپرسید: «ارزان&zwnj;ترین راه رفتن از تهران به مشهد در ۲۸ مهر چیست؟»

</div>

## Development

```bash
git clone https://github.com/sepehr071/mrbilit-mcp && cd mrbilit-mcp
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q          # offline, against recorded responses
uv run python -m pytest -m live -q  # real API (set MRBILIT_MCP_PROXY if needed)
uv build
```

Tools live in `src/mrbilit_mcp/places.py`, `flights.py`, `trains.py`, `buses.py`, `hotels.py` and `content.py`; each
is a typed async function with a docstring that tells the agent when to use it. Issues and PRs are welcome,
especially new tools and fixes for API changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Related servers

- [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp): catalog of all Iranian-service MCP servers
- [flytoday-mcp](https://github.com/sepehr071/flytoday-mcp) for flights, hotels, trains and buses on Flytoday
- [jabama-mcp](https://github.com/sepehr071/jabama-mcp) for villas, tours and events on Jabama
- [otaghak-mcp](https://github.com/sepehr071/otaghak-mcp) for villa, cottage and apartment stays on Otaghak

If this saves you clicks, a star helps others find it.

## Disclaimer

Unofficial and not affiliated with or endorsed by MrBilit. It is read-only and uses the public endpoints of the mrbilit.com website,
which can change without notice. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/mrbilit-mcp/blob/main/LICENSE)
