# Crypto Arbitrage Bot Recommendations

Reviewed and updated on April 12, 2026

This document is a companion to the video notes. It now reflects the **downloaded full video captions and metadata** for:

- Video: [How to create a profitable crypto arbitrage bot in 2026](https://www.youtube.com/watch?v=-PWyM6adiIE&t=39s)
- Channel: Dapp University

Local source files used for this review:

- [video_assets/-PWyM6adiIE/How to create a profitable crypto arbitrage bot in 2026.en.vtt](/Users/tamir.wainstain/src/ArbitrageTrader/video_assets/-PWyM6adiIE/How%20to%20create%20a%20profitable%20crypto%20arbitrage%20bot%20in%202026.en.vtt)
- [video_assets/-PWyM6adiIE/How to create a profitable crypto arbitrage bot in 2026.info.json](/Users/tamir.wainstain/src/ArbitrageTrader/video_assets/-PWyM6adiIE/How%20to%20create%20a%20profitable%20crypto%20arbitrage%20bot%20in%202026.info.json)

## What Changed In This Review

The earlier recommendation file leaned more on general DeFi engineering judgment than on the actual video. After reviewing the downloaded captions, these were the main corrections:

- the video explicitly uses **Uniswap and PancakeSwap** as its example DEX pair
- the video explicitly recommends **Node.js + ethers.js** for the searcher bot
- the video explicitly recommends researching pairs with **Etherscan**, **DexScreener**, **BirdEye**, and **GMGN**
- the video does **not** name a specific flash-loan provider like Aave
- the video emphasizes choosing **two DEXs with similar interfaces**, lots of liquidity, many pairs, and multi-chain presence

So this file now separates:

- what the video explicitly recommends
- what I recommend as the strongest practical implementation path

## What The Video Explicitly Recommends

### 1. Choose One Blockchain First

From the video, the chain should have these properties:

- high activity / high TVL
- many available tokens
- flash-loan support
- EVM compatibility so the same Solidity code can be reused

The speaker uses Ethereum as the reference point and suggests repeating the approach on other EVM-compatible chains later.

### 2. Start With Two DEXs

The video specifically uses:

- **Uniswap**
- **PancakeSwap**

Why those two, according to the speaker:

- they are decentralized exchanges
- their interfaces are similar enough that code can often be reused
- they have deep liquidity
- they have high trading volume
- they support many token pairs
- they exist across multiple networks or have many forks

The video also suggests checking **DeFiLlama forks** to find Uniswap-style clones and similar opportunities.

### 3. Research Token Pairs With Volume

The video’s research suggestions are concrete:

- **Etherscan token tracker**
- **DexScreener**
- **BirdEye**
- **GMGN**

The speaker’s filtering logic is roughly:

- sort tokens by size or volume
- look for tokens traded on multiple exchanges
- prefer active markets
- use **ERC-20 tokens** for this strategy

### 4. Pick A Flash-Loan Provider

The video clearly says to choose a flash-loan provider, but it does **not** specify one by name in the caption text I reviewed.

That matters because any provider recommendation below is my implementation recommendation, not a direct quote from the video.

### 5. Build A Smart Contract

The contract’s role in the video is:

- receive the arbitrage execution request
- borrow with a flash loan
- trade through the selected DEX route
- use router calls to perform swaps
- complete everything atomically in one transaction

The speaker also emphasizes using DEXs whose swap interfaces are similar, because it reduces implementation complexity.

### 6. Build A Searcher Bot

The video is specific here:

- use **Node.js**
- use **ethers.js**
- subscribe to **swap events**
- re-check profitability after a trade event fires
- trigger the smart contract only if fees still allow profit

The video specifically mentions using `contract.on(...)` with ethers.js to listen for swap events.

## My Updated Practical Recommendation

If you want to build a serious first version today, I would combine the video’s guidance with current protocol reality like this:

- one EVM chain
- one Uniswap-style DEX first
- one second DEX with comparable liquidity and interface
- one flash-loan provider
- one off-chain searcher
- one simple executor contract

## DEX Recommendation

### Best Match To The Video: Uniswap + PancakeSwap

If your goal is to follow the video closely, the strongest match is:

- **Uniswap**
- **PancakeSwap**

Why this is now the first recommendation:

- it is what the speaker actually uses as the concrete example
- the video’s logic depends on similar router/function interfaces
- this is a cleaner starting point than mixing very different AMM designs too early

### Practical Extension: Uniswap + Another Uniswap-Style Fork

A slightly more general version of the same idea is:

- **Uniswap**
- another **Uniswap-style fork** on the same chain

Why:

- the code paths are often easier to align
- router integration is simpler
- the video’s “same interface” argument still holds

### When To Use Sushi Or Balancer

I would now treat **Sushi** and **Balancer** as secondary suggestions, not the default first pair.

Use them when:

- you already have the basic route working
- you want wider venue coverage
- you are comfortable handling different routing behavior

That is an engineering recommendation from me, not a video-specific one.

## Flash-Loan Recommendation

### Best Practical Default: Aave V3

Even though the video does not name a provider, my strongest first implementation recommendation is still **Aave V3**.

Why:

- it is broadly deployed
- it has strong liquidity
- it is well documented
- it is a common first choice for flash-loan integrations

But this is now clearly separated from the video itself.

### Alternative: Balancer Vault

Use **Balancer** as a second flash-loan option if:

- your route already touches Balancer pools
- the assets you need are well supported there
- you want to compare funding paths

## Searcher Bot Recommendation

### Follow The Video Closely: Node.js + ethers.js

If your goal is to follow the tutorial structure as closely as possible, use:

- **Node.js**
- **ethers.js**

Why:

- this is exactly what the speaker recommends
- the event-subscription model fits the video’s searcher design
- it matches the router/event examples in the transcript

### If You Prefer Python

If your goal is research and experimentation rather than following the speaker’s stack exactly, Python is still reasonable for:

- simulation
- backtesting
- signal research
- paper trading

But the video itself points much more directly toward a **Node.js searcher**.

## Scanner / Searcher Step

The missing practical piece is the actual **scanner loop** inside the searcher bot.

This is the last step of the system:

- watch the market
- detect a possible spread
- re-price both venues
- subtract fees and gas
- trigger the contract only if the trade is still profitable

### What The Video Implies The Scanner Should Do

From the transcript, the intended searcher flow is:

1. subscribe to swap events on the DEXs you care about
2. when a swap happens, refresh the relevant prices
3. compare both venues for the target token pair
4. calculate whether the spread still covers costs
5. call the smart contract if the route is net profitable

That means the bot is not just polling blindly. It is more of an **event-driven opportunity scanner**.

### Recommended Scanner Responsibilities

For a practical first implementation, the scanner should do these jobs:

- subscribe to `Swap` events on both target DEXs
- keep the list of watched token pairs small
- fetch current buy and sell quotes after each event
- normalize decimals and token ordering
- calculate:
  - gross spread
  - DEX fees
  - flash-loan fee
  - gas estimate
  - slippage buffer
- reject opportunities below threshold
- send only validated opportunities to the executor contract

### Recommended Scanner Output

The scanner should produce an internal opportunity object like:

```json
{
  "pair": "WETH/USDC",
  "buy_dex": "Uniswap",
  "sell_dex": "PancakeSwap",
  "trade_size": 1.0,
  "gross_spread_pct": 0.42,
  "estimated_net_profit": 0.013,
  "gas_cost_estimate": 0.003,
  "flash_loan_fee": 0.0009,
  "is_actionable": true
}
```

### Best First Scanner MVP

To keep the first version realistic, I recommend:

- one chain
- two DEXs
- one or two ERC-20 pairs
- event-driven quote refresh
- strict minimum net-profit threshold
- no auto-scaling or multi-route logic

This keeps the scanner understandable and close to the video’s actual architecture.

### What The Scanner Should Not Do First

Do not start by making the scanner:

- multi-chain
- multi-route across many pools
- prediction-driven
- fully autonomous with broad asset coverage

The first scanner should be a narrow, reliable detector for one simple arbitrage path.

## Token And Pair Recommendation

After reviewing the transcript, the better way to describe pair selection is:

- choose **ERC-20 tokens**
- prioritize tokens with **high trading volume**
- look for tokens traded on **multiple exchanges**
- use public explorers and DEX analytics tools to validate activity

For a practical starting set, I would still begin with liquid majors like:

- WETH / USDC
- WETH / USDT
- WBTC / USDC

That last part is my implementation advice layered on top of the video’s volume-first guidance.

## Chain Recommendation

### Closest To The Video: Start Where Liquidity And Tooling Are Strong

The video’s logic points toward starting where:

- token coverage is strong
- liquidity is deep
- flash loans exist
- EVM tooling is mature

That usually means **Ethereum first** in concept.

### Practical Development Recommendation

For learning and iteration, I still recommend considering:

- **Base**
- **Arbitrum**

But this is a practical engineering tradeoff, not the core recommendation of the video.

## Best First Build Order

After reviewing the transcript, the cleanest sequence is:

1. choose one EVM chain
2. choose two DEXs with similar interfaces
3. choose one ERC-20 pair with real volume
4. choose one flash-loan provider
5. build the smart contract executor
6. build the event-driven scanner / searcher bot

That is much closer to the video’s actual structure than the earlier version of this file.

## Bottom Line

If you want the recommendation that is most faithful to the actual video:

- use **Uniswap + PancakeSwap** or another Uniswap-style pair
- use **Node.js + ethers.js**
- research **ERC-20** pairs with **Etherscan** and **DexScreener-style** tools
- start on a liquid **EVM-compatible** chain
- make the last step an **event-driven scanner/searcher bot**
- treat the flash-loan provider choice as an implementation decision the video leaves open

If you want the recommendation that is most practical for a first serious build:

- keep the same overall structure from the video
- use **Aave V3** for flash loans
- start with **Uniswap + PancakeSwap** or another close Uniswap-style fork pair
- build a small scanner that watches `Swap` events and recalculates net profit before execution
- only expand to Sushi, Balancer, or multi-chain setups after the first path works

## Sources

Video content:

- Downloaded auto-generated caption file in this repo
- Downloaded YouTube metadata file in this repo

Current protocol references:

- Aave overview: [Introduction to Aave](https://aave.com/help/aave-101/introduction-to-aave)
- Uniswap developer docs: [Uniswap Docs](https://docs.uniswap.org/)
- PancakeSwap docs: [PancakeSwap Docs](https://docs.pancakeswap.finance/)
- Balancer docs: [Balancer Docs](https://docs.balancer.fi/)
- Sushi docs: [Sushi Docs](https://docs.sushi.com/)
