// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title FlashArbExecutor
 * @notice Atomic flash-loan arbitrage: borrow from Aave V3, swap on two DEXs, repay, keep profit.
 *
 * Flow (single transaction):
 *   1. Bot calls executeArbitrage(params)
 *   2. Contract requests a flash loan from Aave V3 Pool (borrows quote asset, e.g. USDC)
 *   3. Aave calls back executeOperation()
 *   4. Inside the callback:
 *      a. Approve & swap quote→base on DEX A (buy cheap)
 *      b. Approve & swap base→quote on DEX B (sell expensive)
 *      c. Repay flash loan + fee
 *      d. Transfer remaining profit to owner
 *   5. If profit < minProfit, the whole transaction reverts
 *
 * ERC-20 operations:
 *   - IERC20.approve(router, amount) — allow DEX router to spend our tokens
 *   - ISwapRouter.exactInputSingle()  — Uniswap V3 / PancakeSwap V3 / Sushi V3 swap
 *   - IERC20.transfer(owner, profit)  — send profit to bot owner
 */

// ─── Interfaces ───────────────────────────────────────────────────────────────

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @dev Aave V3 flash loan pool interface (simplified).
interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

/// @dev Aave V3 flash loan callback interface.
interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

/// @dev Uniswap V3 / PancakeSwap V3 / Sushi V3 swap router (same interface).
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);
}

// ─── Contract ─────────────────────────────────────────────────────────────────

contract FlashArbExecutor is IFlashLoanSimpleReceiver {

    address public immutable owner;
    IPool   public immutable aavePool;

    /// @dev Packed parameters for the arbitrage route.
    struct ArbParams {
        address baseToken;        // e.g. WETH
        address quoteToken;       // e.g. USDC
        address routerA;          // DEX A swap router (buy side)
        address routerB;          // DEX B swap router (sell side)
        uint24  feeA;             // DEX A pool fee tier (e.g. 3000 = 0.30%)
        uint24  feeB;             // DEX B pool fee tier
        uint256 amountIn;         // Flash loan amount in quote token
        uint256 minProfit;        // Minimum profit in quote token; reverts if not met
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(address _aavePool) {
        owner = msg.sender;
        aavePool = IPool(_aavePool);
    }

    // ─── Entry point (called by the Python bot) ───────────────────────────

    /**
     * @notice Trigger an atomic flash-loan arbitrage.
     * @param params Encoded ArbParams struct with route details.
     *
     * The Python bot calls this after detecting a profitable opportunity.
     * The function requests a flash loan; Aave then calls executeOperation().
     */
    function executeArbitrage(ArbParams calldata params) external onlyOwner {
        // Request flash loan of quoteToken from Aave V3.
        // Aave will call executeOperation() with the borrowed funds.
        aavePool.flashLoanSimple(
            address(this),          // receiver
            params.quoteToken,      // asset to borrow
            params.amountIn,        // amount
            abi.encode(params),     // pass route params to the callback
            0                       // referral code
        );
    }

    // ─── Aave callback (called by Aave during flash loan) ────────────────

    /**
     * @notice Called by Aave V3 after the flash loan funds are received.
     * @dev All swaps and repayment must happen inside this function.
     *      If anything fails or profit < minProfit, the tx reverts and
     *      the flash loan is cancelled (no funds lost, only gas).
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,    // Aave's flash loan fee (9 bps)
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(initiator == address(this), "invalid initiator");

        ArbParams memory arb = abi.decode(params, (ArbParams));
        uint256 balanceBefore = IERC20(arb.quoteToken).balanceOf(address(this));

        // ── Step 1: Buy base token on DEX A (quote → base) ──────────
        // Approve DEX A router to spend our quote tokens (e.g. USDC).
        IERC20(arb.quoteToken).approve(arb.routerA, arb.amountIn);

        // Swap: quoteToken → baseToken on DEX A.
        uint256 baseReceived = ISwapRouter(arb.routerA).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn:           arb.quoteToken,
                tokenOut:          arb.baseToken,
                fee:               arb.feeA,
                recipient:         address(this),
                deadline:          block.timestamp,
                amountIn:          arb.amountIn,
                amountOutMinimum:  0,  // slippage handled by minProfit check below
                sqrtPriceLimitX96: 0
            })
        );

        // ── Step 2: Sell base token on DEX B (base → quote) ─────────
        // Approve DEX B router to spend our base tokens (e.g. WETH).
        IERC20(arb.baseToken).approve(arb.routerB, baseReceived);

        // Swap: baseToken → quoteToken on DEX B.
        ISwapRouter(arb.routerB).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn:           arb.baseToken,
                tokenOut:          arb.quoteToken,
                fee:               arb.feeB,
                recipient:         address(this),
                deadline:          block.timestamp,
                amountIn:          baseReceived,
                amountOutMinimum:  0,
                sqrtPriceLimitX96: 0
            })
        );

        // ── Step 3: Repay flash loan ────────────────────────────────
        uint256 totalOwed = amount + premium;
        IERC20(asset).approve(address(aavePool), totalOwed);

        // ── Step 4: Check profit and transfer to owner ──────────────
        uint256 balanceAfter = IERC20(arb.quoteToken).balanceOf(address(this));
        uint256 profit = balanceAfter - balanceBefore - totalOwed;

        require(profit >= arb.minProfit, "profit below minimum threshold");

        // Transfer profit to the bot owner.
        if (profit > 0) {
            IERC20(arb.quoteToken).transfer(owner, profit);
        }

        return true;
    }

    // ─── Emergency withdrawal ─────────────────────────────────────────────

    /// @notice Withdraw any ERC-20 tokens stuck in the contract.
    function withdrawToken(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        if (balance > 0) {
            IERC20(token).transfer(owner, balance);
        }
    }
}
