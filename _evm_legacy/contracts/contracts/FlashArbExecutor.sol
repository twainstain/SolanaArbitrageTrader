// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title FlashArbExecutor
 * @notice Atomic flash-loan arbitrage: borrow from Aave V3, swap on two DEXs, repay, keep profit.
 *
 * Supports two swap interfaces:
 *   - V3 routers (Uniswap V3, PancakeSwap V3, Sushi V3): exactInputSingle
 *   - Solidly-fork routers (Velodrome V2, Aerodrome): swapExactTokensForTokens
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

/// @dev Uniswap V3 SwapRouter (original) — Ethereum, Arbitrum, Optimism.
///      Has `deadline` in the params struct.
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

/// @dev Uniswap V3 SwapRouter02 — Base (and newer deployments).
///      Drops `deadline` from the params struct (uses block.timestamp internally).
interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);
}

/// @dev Velodrome V2 / Aerodrome router interface (Solidly-fork).
interface IVeloRouter {
    struct Route {
        address from;
        address to;
        bool    stable;
        address factory;
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        Route[] calldata routes,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

// ─── Contract ─────────────────────────────────────────────────────────────────

contract FlashArbExecutor is IFlashLoanSimpleReceiver {

    // Swap type constants — packed into uint8 in ArbParams.
    uint8 constant SWAP_V3      = 0;  // SwapRouter (original): Ethereum, Arbitrum, Optimism
    uint8 constant SWAP_VELO    = 1;  // Velodrome V2 / Aerodrome (Solidly-fork)
    uint8 constant SWAP_V3_02   = 2;  // SwapRouter02 (no deadline): Base, newer deployments

    address public immutable owner;
    IPool   public immutable aavePool;

    event ProfitRealized(
        address indexed quoteToken,
        uint256 profit,
        uint256 totalOwed
    );

    /// @dev Packed parameters for the arbitrage route.
    struct ArbParams {
        address baseToken;        // e.g. WETH
        address quoteToken;       // e.g. USDC
        address routerA;          // DEX A swap router (buy side)
        address routerB;          // DEX B swap router (sell side)
        uint24  feeA;             // DEX A pool fee tier (V3: e.g. 3000; Velo: unused)
        uint24  feeB;             // DEX B pool fee tier (V3: e.g. 500;  Velo: unused)
        uint256 amountIn;         // Flash loan amount in quote token
        uint256 minProfit;        // Minimum profit in quote token; reverts if not met
        uint8   swapTypeA;        // 0=V3, 1=Velodrome/Aerodrome
        uint8   swapTypeB;        // 0=V3, 1=Velodrome/Aerodrome
        address factoryA;         // Velo pool factory for DEX A (zero for V3)
        address factoryB;         // Velo pool factory for DEX B (zero for V3)
        bool    stableA;          // Velo: use stable pool for DEX A
        bool    stableB;          // Velo: use stable pool for DEX B
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
     */
    function executeArbitrage(ArbParams calldata params) external onlyOwner {
        aavePool.flashLoanSimple(
            address(this),
            params.quoteToken,
            params.amountIn,
            abi.encode(params),
            0
        );
    }

    // ─── Aave callback (called by Aave during flash loan) ────────────────

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(initiator == address(this), "invalid initiator");

        ArbParams memory arb = abi.decode(params, (ArbParams));
        uint256 balanceBefore = IERC20(arb.quoteToken).balanceOf(address(this));

        // ── Step 1: Buy base token on DEX A (quote → base) ──────────
        IERC20(arb.quoteToken).approve(arb.routerA, arb.amountIn);
        uint256 baseReceived = _swap(
            arb.routerA, arb.swapTypeA,
            arb.quoteToken, arb.baseToken,
            arb.amountIn, arb.feeA,
            arb.factoryA, arb.stableA
        );

        // ── Step 2: Sell base token on DEX B (base → quote) ─────────
        IERC20(arb.baseToken).approve(arb.routerB, baseReceived);
        _swap(
            arb.routerB, arb.swapTypeB,
            arb.baseToken, arb.quoteToken,
            baseReceived, arb.feeB,
            arb.factoryB, arb.stableB
        );

        // ── Step 3: Repay flash loan ────────────────────────────────
        uint256 totalOwed = amount + premium;
        IERC20(asset).approve(address(aavePool), totalOwed);

        // ── Step 4: Check profit and transfer to owner ──────────────
        uint256 balanceAfter = IERC20(arb.quoteToken).balanceOf(address(this));
        uint256 profit = balanceAfter - balanceBefore - totalOwed;

        require(profit >= arb.minProfit, "profit below minimum threshold");
        emit ProfitRealized(arb.quoteToken, profit, totalOwed);

        if (profit > 0) {
            IERC20(arb.quoteToken).transfer(owner, profit);
        }

        return true;
    }

    // ─── Internal swap dispatcher ─────────────────────────────────────────

    function _swap(
        address router,
        uint8   swapType,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24  fee,
        address factory,
        bool    stable
    ) internal returns (uint256 amountOut) {
        if (swapType == SWAP_VELO) {
            return _swapVelo(router, tokenIn, tokenOut, amountIn, factory, stable);
        } else if (swapType == SWAP_V3_02) {
            return _swapV3_02(router, tokenIn, tokenOut, amountIn, fee);
        } else {
            return _swapV3(router, tokenIn, tokenOut, amountIn, fee);
        }
    }

    function _swapV3(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24  fee
    ) internal returns (uint256) {
        return ISwapRouter(router).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn:           tokenIn,
                tokenOut:          tokenOut,
                fee:               fee,
                recipient:         address(this),
                deadline:          block.timestamp,
                amountIn:          amountIn,
                amountOutMinimum:  0,
                sqrtPriceLimitX96: 0
            })
        );
    }

    function _swapV3_02(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24  fee
    ) internal returns (uint256) {
        return ISwapRouter02(router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams({
                tokenIn:           tokenIn,
                tokenOut:          tokenOut,
                fee:               fee,
                recipient:         address(this),
                amountIn:          amountIn,
                amountOutMinimum:  0,
                sqrtPriceLimitX96: 0
            })
        );
    }

    function _swapVelo(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        address factory,
        bool    stable
    ) internal returns (uint256) {
        IVeloRouter.Route[] memory routes = new IVeloRouter.Route[](1);
        routes[0] = IVeloRouter.Route({
            from:    tokenIn,
            to:      tokenOut,
            stable:  stable,
            factory: factory
        });

        uint256[] memory amounts = IVeloRouter(router).swapExactTokensForTokens(
            amountIn,
            0,          // amountOutMin — slippage handled by minProfit check
            routes,
            address(this),
            block.timestamp
        );

        return amounts[amounts.length - 1];
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
