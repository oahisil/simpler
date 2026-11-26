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
#include "probe.h"
#include <pto/pto-inst.hpp>

using pto::SyncCoreType;

template <Access op>
AICORE inline void
measure(__gm__ uint32_t *word, __gm__ Sample *output, uint32_t blocks, uint32_t vectors, uint32_t rounds) {
#ifdef __DAV_VEC__
    const uint32_t slot = blocks + get_block_idx() * get_subblockdim() + get_subblockid();
    constexpr bool vector = true;
#else
    const uint32_t slot = get_block_idx();
    constexpr bool vector = false;
#endif
    constexpr bool reading = op == Access::Read || op == Access::AtomicRead;
    const uint32_t id = get_coreid();
    for (uint32_t round = 0; round < rounds; ++round) {
        if (slot == 0) {
            __builtin_cce_st_dev(uint32_t(reading), word, 0);
            dsb(DSB_DDR);
        }
        pto::SYNCALL<SyncCoreType::Mix>();
        uint64_t begin, end;
        uint32_t value = 1;
        asm volatile("MOV %0, SYS_CNT" : "=l"(begin) : : "memory");
        if constexpr (op == Access::Read) {
            value = __builtin_cce_ld_dev(word, 0);
        } else if constexpr (op == Access::AtomicRead) {
            value = atomicAdd(word, uint32_t{0});
        } else if constexpr (op == Access::Write) {
            __builtin_cce_st_dev(uint32_t{1}, word, 0);
        } else {
            value = atomicExch(word, uint32_t{1});
        }
        if constexpr (!reading) dsb(DSB_DDR);
        // For reads, consume the returned value before sampling the end tick.
        asm volatile("MOV %0, %0\n\tMOV %1, SYS_CNT" : "+l"(value), "=&l"(end) : : "memory");
        pto::SYNCALL<SyncCoreType::Mix>();
        auto *sample = &output[round * (blocks + vectors) + slot];
        sample->begin = begin;
        sample->end = end;
        sample->observed = value;
        sample->final_value = slot == 0 ? __builtin_cce_ld_dev(word, 0) : 0;
        sample->slot = slot;
        sample->round = round;
        sample->core_id = id;
        sample->is_vector = vector;
        dcci(sample, SINGLE_CACHE_LINE, CACHELINE_OUT);
        dsb(DSB_DDR);
    }
}

extern "C" __global__ AICORE void same_address(
    __gm__ uint32_t *word, __gm__ Sample *output, uint32_t blocks, uint32_t vectors, uint32_t rounds, Access op
) {
    switch (op) {
    case Access::Read:
        return measure<Access::Read>(word, output, blocks, vectors, rounds);
    case Access::AtomicRead:
        return measure<Access::AtomicRead>(word, output, blocks, vectors, rounds);
    case Access::Write:
        return measure<Access::Write>(word, output, blocks, vectors, rounds);
    case Access::AtomicWrite:
        return measure<Access::AtomicWrite>(word, output, blocks, vectors, rounds);
    }
}

extern "C" void launch_probe(
    uint32_t *word, Sample *output, uint32_t blocks, uint32_t vectors, uint32_t rounds, Access op, void *stream
) {
    same_address<<<blocks, nullptr, stream>>>(word, output, blocks, vectors, rounds, op);
}
