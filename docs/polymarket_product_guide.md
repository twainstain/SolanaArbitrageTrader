# Polymarket Product Guide

> A practical overview of what Polymarket is, how it works, how people use it,
> and what matters for building or researching around it.

---

## What Polymarket Is

Polymarket is a **prediction market** platform where users trade on the outcome
of real-world events.

Examples:

- Will a candidate win an election?
- Will the Fed cut rates this year?
- Will BTC finish above a certain price by a given date?
- Will a sports team win a specific match?

Instead of betting against a traditional sportsbook or “house,” users trade
with each other in a marketplace. Prices move based on supply and demand and
act like live probabilities.

At a high level:

- a market asks a question about a future event
- users buy and sell outcome shares
- prices range between `$0.00` and `$1.00`
- when the event resolves, winning shares redeem for `$1.00`
- losing shares go to `$0.00`

That makes Polymarket both:

- a consumer product for trading event probabilities
- a developer platform with APIs, SDKs, and market data feeds

---

## Why People Use It

People use Polymarket for a few different reasons:

- to speculate on future events
- to hedge exposure to real-world outcomes
- to express probabilistic views instead of binary opinions
- to monitor market-implied odds for news, politics, sports, and crypto
- to build products on top of prediction market data

The product is attractive because prices update continuously and often reflect
the market’s current consensus faster than static commentary or headlines.

---

## How The Core Product Works

### Markets

A Polymarket market centers on a question with defined resolution rules.

Examples:

- `Will BTC be above $100,000 on June 30?`
- `Will Candidate X win the 2028 election?`

The important parts of a market are:

- the question
- the answer choices, usually `Yes` / `No`
- the market end date
- the resolution source and rules
- the token IDs / market identifiers behind the scenes

The title tells you what the market is about, but the **rules determine how it
resolves**. That is a big deal on Polymarket and one of the first things a user
or builder needs to understand.

### Prices

Prices represent probabilities.

Examples:

- `$0.25` means roughly 25%
- `$0.50` means roughly 50%
- `$0.80` means roughly 80%

If you buy `Yes` at `$0.40` and the market resolves `Yes`, that share becomes
worth `$1.00`. If the market resolves `No`, that share becomes worthless.

### Order Book

Polymarket uses a **central limit order book (CLOB)**.

That means:

- users place bids and asks
- prices are discovered in the market
- trades happen peer-to-peer
- the displayed price comes from the order book, not from Polymarket setting an
  official quote

For trading behavior, this means Polymarket feels more like an exchange than a
traditional betting site.

### Positions

Users can:

- buy `Yes`
- buy `No`
- sell before resolution if liquidity exists
- hold until resolution

You do not need to hold every position until the end. If another trader is
willing to take the other side, you can close early and realize profit or loss.

---

## What Makes Polymarket Different From A Sportsbook Or Casino

Polymarket’s own framing is that it is **not the house**.

In practice, the distinctions are:

- users trade against each other, not against a centralized bookmaker
- prices are market-driven
- users can often enter and exit before resolution
- market data and positions are transparent and programmable
- the platform is designed around blockchain settlement and self-custody

That said, users still face real trading risk:

- bad entry prices
- poor liquidity
- wide spreads
- wrong views on resolution
- operational mistakes

---

## How A User Typically Uses Polymarket

### 1. Browse a market

A user starts by finding a market they care about, such as a politics, crypto,
or sports question.

They should check:

- the current `Yes` / `No` prices
- recent price movement
- the market end time
- the rules and clarification text
- how liquid the order book is

### 2. Choose a side

The user decides whether they think the market price is too high or too low.

Examples:

- buy `Yes` if they think the event is more likely than the market implies
- buy `No` if they think the event is less likely than the market implies

### 3. Enter an order

Users can interact with the order book rather than just clicking a fixed line.

In general:

- a **limit order** says “buy or sell at this price or better”
- a **marketable order** crosses the spread and executes against existing
  liquidity

### 4. Manage the position

Before resolution, a user can usually:

- hold
- reduce
- fully exit
- reverse the view by taking the other side

### 5. Resolve or redeem

Once the event outcome is known and the market is resolved:

- winning shares redeem for `$1.00`
- losing shares go to `$0.00`

---

## Self-Custody And Wallet Model

Polymarket is built around a self-custody model.

That means:

- assets are associated with the user’s wallet
- private keys remain under user control
- smart contracts handle settlement logic
- builders can integrate through APIs and SDKs without Polymarket holding all
  logic in a closed system

For developers and advanced users, this is important because it means:

- authentication is wallet-based
- trading permissions depend on wallet setup and approvals
- balances, allowances, and settlement all matter operationally

---

## Funding And Assets

From a product standpoint, users generally need:

- a supported wallet setup
- funds on Polygon
- the right trading asset balance for purchases
- gas if using an EOA setup

For developers, Polymarket’s docs currently emphasize:

- `USDC.e` on Polygon for trading and settlement flows
- Polygon network setup
- token approvals for trading

The exact wallet flow depends on the wallet type and integration path, so the
product should always be used alongside the current official setup docs.

---

## Resolution And Clarifications

Resolution is one of the most important parts of the product.

Polymarket markets do not resolve based only on the headline question. They
resolve based on **written rules**, a **resolution source**, and a formal
resolution process.

Polymarket’s docs currently describe resolution through the **UMA Optimistic
Oracle** process.

That matters because:

- a market can be obvious to a human but still depend on exact rule wording
- edge cases can matter a lot
- users should read clarification updates if they appear
- builders should store rules, not just titles

Practical takeaway:

- never trade or model a market using only the title
- always read the resolution rules and any additional context

---

## The Main Product Surfaces

From a user and builder perspective, Polymarket has a few major surfaces.

### 1. Consumer trading product

This is the normal market browsing and trading experience:

- browse events and markets
- view prices
- place trades
- track positions
- follow resolution

### 2. Market data product

This is useful for:

- dashboards
- analytics
- monitoring
- research systems
- bots and trading infrastructure

### 3. Developer platform

Polymarket also offers:

- public APIs
- trading APIs
- WebSocket feeds
- official SDKs

That makes it possible to build:

- scanners
- bots
- market analytics tools
- liquidity and trading systems
- research pipelines

### 4. Market maker / liquidity provider workflows

For advanced participants, Polymarket also supports market-making workflows and
liquidity rewards in some contexts.

This is more specialized than normal user trading and matters mostly for:

- quoting strategies
- spread capture
- inventory management
- systematic liquidity provision

---

## The API Stack In Plain English

Polymarket’s docs currently split the platform into several APIs.

### Gamma API

Used for discovery and browsing-style data, such as:

- markets
- events
- tags
- search
- public metadata

Use this when you want to find markets and understand what exists.

### Data API

Used for data-oriented views such as:

- positions
- trades
- activity
- leaderboards
- holder and market analytics

Use this when you want historical or account-style market data.

### CLOB API

Used for:

- order book data
- pricing
- midpoints and spreads
- placing and managing orders

Use this when you need actual trading behavior or real-time book state.

### Bridge API

Used around deposit / transfer workflows.

This matters more for advanced users and integrations than for basic market
browsing.

---

## Authentication In Plain English

Polymarket’s CLOB trading flow uses a two-level model:

- **L1**: wallet/private-key based authentication
- **L2**: API credentials derived from the wallet

In practice:

- public market data can often be read without auth
- trading actions need authenticated flows
- even with API credentials, order signing still matters

For a normal user using the product through the website, much of this is hidden.
For builders, it is central.

---

## How To Think About Trading On Polymarket

A useful mental model is:

- every market price is the current tradable consensus
- a trade is a statement that the consensus is wrong or incomplete
- profit comes from being more correct than the market, or faster than the
  market, or better at execution than the market

There are a few broad styles of participation:

- discretionary trading
- news-driven trading
- event arbitrage / cross-market relative value
- latency-sensitive trading
- market making
- passive observation and analytics

For our purposes, the last three are the most relevant.

---

## Key Risks For Users

Anyone using Polymarket should understand these risks.

### Resolution risk

You can be “conceptually right” and still be wrong if the market resolves based
on rules you did not read carefully.

### Liquidity risk

A displayed midpoint can look attractive, but poor book depth may make real
entry or exit much worse.

### Spread risk

Wide spreads can turn a good thesis into a bad trade.

### Timing risk

Being too early or too late matters, especially in fast-moving markets.

### Operational risk

Wallet setup, approvals, auth flows, and order handling all introduce failure
modes for advanced users and builders.

### Regulatory / availability risk

Access depends on jurisdiction and current platform restrictions.

---

## Geographic Restrictions

As of **April 15, 2026**, Polymarket’s documentation says order placement is
restricted in certain jurisdictions, including the **United States**.

That is important for both product use and bot design:

- not every user can trade
- not every environment is eligible for live order placement
- builders should check geoblocking before attempting order submission

For our project work, this means any live trading assumptions should be treated
as conditional, not guaranteed.

---

## How Polymarket Connects To Our Repo Work

For us, Polymarket matters in two different ways.

### 1. As a product to understand

We need to understand:

- what users are actually doing
- how the market structure works
- what the data means
- where operational risk shows up

### 2. As a platform to integrate with

If we build `polymarket_trader`, we would likely use:

- market discovery from metadata APIs
- live order book data from the CLOB stack
- authenticated trading only if compliance and eligibility are satisfied
- replay and analytics before any live strategy rollout

So the product doc matters because it grounds the engineering in the actual
market mechanics rather than just bot hype.

---

## Suggested First Steps For Anyone New To Polymarket

If someone is trying to understand the product before building around it, a good
sequence is:

1. Read the introductory docs and product overview.
2. Browse a few live markets manually.
3. Study how prices map to probabilities.
4. Read several sets of resolution rules and clarifications.
5. Review the order book behavior and liquidity on active markets.
6. Read the API overview.
7. Only then move into SDKs, auth flows, and trading automation.

---

## Bottom Line

Polymarket is best understood as a **prediction market exchange with
programmable market infrastructure**.

It is not just a place to make binary bets. It is also:

- a probability-discovery mechanism
- a peer-to-peer trading venue
- a structured market data source
- a developer platform for analytics and automation

If we are building around it, the most important things to understand first are:

- market rules
- order book mechanics
- resolution logic
- wallet and auth flows
- geographic restrictions

Those are the foundations that matter before strategy.

---

## References

- [Polymarket 101](https://docs.polymarket.com/polymarket-101)
- [Quickstart](https://docs.polymarket.com/quickstart)
- [Orderbook](https://docs.polymarket.com/trading/orderbook)
- [How Are Prediction Markets Resolved?](https://docs.polymarket.com/polymarket-learn/markets/how-are-markets-resolved)
- [Resolution](https://docs.polymarket.com/concepts/resolution)
- [How Are Markets Clarified?](https://docs.polymarket.com/polymarket-learn/markets/how-are-markets-clarified)
- [API Overview](https://docs.polymarket.com/api-reference)
- [Authentication](https://docs.polymarket.com/api-reference/authentication)
- [Trading Quickstart](https://docs.polymarket.com/trading/quickstart)
- [Getting Started For Market Makers](https://docs.polymarket.com/market-makers/getting-started)
- [Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards)
- [Geographic Restrictions](https://docs.polymarket.com/polymarket-learn/FAQ/geoblocking)
