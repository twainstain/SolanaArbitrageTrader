// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";

interface IFlashArbExecutor {
    function owner() external view returns (address);
    function aavePool() external view returns (address);
}

contract DeployFlashArb is Script {
    function run() external {
        address aavePool = vm.envAddress("AAVE_POOL");
        uint256 deployerKey = vm.envUint("EXECUTOR_PRIVATE_KEY");

        vm.startBroadcast(deployerKey);

        // Deploy using CREATE opcode — bytecode loaded from artifact
        bytes memory bytecode = abi.encodePacked(
            vm.getCode("FlashArbExecutor.sol:FlashArbExecutor"),
            abi.encode(aavePool)
        );

        address deployed;
        assembly {
            deployed := create(0, add(bytecode, 0x20), mload(bytecode))
        }
        require(deployed != address(0), "deployment failed");

        vm.stopBroadcast();

        console.log("Deployed FlashArbExecutor to:", deployed);
        console.log("Owner:", IFlashArbExecutor(deployed).owner());
        console.log("Aave Pool:", IFlashArbExecutor(deployed).aavePool());
    }
}
