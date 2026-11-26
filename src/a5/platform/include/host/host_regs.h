/*
 * Copyright (c) PyPTO Contributors.
 * This program is free software, you can redistribute it and/or modify it under the terms and conditions of
 * CANN Open Software License Agreement Version 2.0 (the "License").
 * Please refer to the License for details. You may not use this file except in compliance with the License.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
 * INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
 * See LICENSE in the root of the software repository for the full text of the License.
 * -----------------------------------------------------------------------------------------------------------
 */
/**
 * @file host_regs.h
 * @brief AICore register address retrieval via CANN HAL APIs
 *
 * Provides register base addresses for AICPU to perform MMIO-based
 * task dispatch to AICore cores.
 */

#ifndef PLATFORM_HOST_HOST_REGS_H_
#define PLATFORM_HOST_HOST_REGS_H_

#include "common/platform_config.h"
#include <cstdint>
#include <vector>
#include "common/platform_config.h"

// Forward declaration
class MemoryAllocator;

// =============================================================================
// DAV_3510 Register Mapping Parameters
// =============================================================================

/**
 * Register address stride for sub-cores within an AICore
 * Each sub-core (AIC, AIV_0, AIV_1) has a 1M register space
 */
constexpr uint64_t REG_SUB_CORE_STRIDE = 0x100000ULL;

// HAL maps the C window followed by one window per vector lane.
constexpr uint32_t REG_AICORE_MAP_SIZE = PLATFORM_CORES_PER_BLOCKDIM * REG_SUB_CORE_STRIDE;

/**
 * Initialize AICore register addresses for runtime
 *
 * Retrieves register addresses from HAL, allocates device memory,
 * copies addresses to device, and stores the device pointer in runtime.
 *
 * @param runtime_regs_ptr Pointer to the regs field (e.g., KernelArgs.regs)
 * @param device_id Device ID
 * @param allocator Memory allocator for device memory
 * @return 0 on success, negative on failure
 */
int init_aicore_register_addresses(uint64_t *runtime_regs_ptr, uint64_t device_id, MemoryAllocator &allocator);

#endif  // PLATFORM_HOST_HOST_REGS_H_
