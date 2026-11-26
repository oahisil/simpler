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
// SWA normalized HC collapse, from pypto-lib ad299c7f (kernels/hc_collapse.cpp).
// The replay passes gamma, premix, input, output and token count in that order.
#include <cstdint>
#include <pto/pto-inst.hpp>
#include "tensor.h"
#include "intrinsic.h"
#include "rmsnorm.hpp"

using namespace pto;
namespace {
template <typename T, int R, int C>
using Vec = Tile<TileType::Vec, T, R, C>;
template <typename T>
__aicore__ inline __gm__ T *data(__gm__ int64_t *args, int i) {
    auto tensor = reinterpret_cast<__gm__ Tensor *>(args[i]);
    return reinterpret_cast<__gm__ T *>(tensor->buffer.addr) + tensor->start_offset;
}
using Input = Tile<TileType::Vec, float, 1, 20480, BLayout::RowMajor, DYNAMIC, 20480>;
using Output = Tile<TileType::Vec, bfloat16_t, 1, 5120, BLayout::RowMajor, DYNAMIC, 5120>;
using Premix = Tile<TileType::Vec, float, 1, 32, BLayout::RowMajor, 1, DYNAMIC>;

__tf__ __aicore__ inline void collapse_norm(
    Output::TileDType __out__ output, Input::TileDType __in__ input, Vec<bfloat16_t, 1, 5120>::TileDType __in__ weight,
    Premix::TileDType __in__ premix, uint16_t row_offset
) {
    auto src = (__ubuf__ float *)__cce_get_tile_ptr(input);
    auto dst = (__ubuf__ bfloat16_t *)__cce_get_tile_ptr(output);
    auto gamma = (__ubuf__ bfloat16_t *)__cce_get_tile_ptr(weight);
    auto mix = (__ubuf__ float *)__cce_get_tile_ptr(premix);
    __VEC_SCOPE__ {
        MaskReg full = pset_b32(PAT_ALL);
        RegTensor<float> p0, p1, p2, p3;
        vlds(p0, mix, row_offset * 4, BRC_B32);
        vlds(p1, mix, row_offset * 4 + 1, BRC_B32);
        vlds(p2, mix, row_offset * 4 + 2, BRC_B32);
        vlds(p3, mix, row_offset * 4 + 3, BRC_B32);
        RegTensor<uint32_t> lane;
        RegTensor<float> partials;
        vci((RegTensor<int32_t> &)lane, 0, INC_ORDER);
        vdup(partials, 0.0f, full, MODE_ZEROING);
        for (uint16_t block = 0; block < 64; ++block) {
            RegTensor<float> folded_square;
            vdup(folded_square, 0.0f, full, MODE_ZEROING);
            uint16_t pieces = block < 16 ? 2 : 1;
            for (uint16_t piece = 0; piece < pieces; ++piece) {
                uint16_t k = block + piece * 64;
                RegTensor<float> x0, x1, x2, x3, a, b, value, square;
                RegTensor<bfloat16_t> rounded;
                vlds(x0, src, k * 64, NORM);
                vlds(x1, src, 5120 + k * 64, NORM);
                vlds(x2, src, 10240 + k * 64, NORM);
                vlds(x3, src, 15360 + k * 64, NORM);
                vmul(x0, x0, p0, full, MODE_ZEROING);
                vmul(x1, x1, p1, full, MODE_ZEROING);
                vmul(x2, x2, p2, full, MODE_ZEROING);
                vmul(x3, x3, p3, full, MODE_ZEROING);
                vadd(a, x0, x1, full, MODE_ZEROING);
                vadd(b, a, x2, full, MODE_ZEROING);
                vadd(value, b, x3, full, MODE_ZEROING);
                vcvt(rounded, value, full, ROUND_R, RS_DISABLE, PART_EVEN);
                vsts(rounded, dst, k * 64, PK_B32, full);
                vcvt(value, rounded, full, PART_EVEN);
                vmul(square, value, value, full, MODE_ZEROING);
                vadd(folded_square, folded_square, square, full, MODE_ZEROING);
            }
            RegTensor<float> partial;
            vcadd(partial, folded_square, full, MODE_ZEROING);
            vdup(partial, partial, full, POS_LOWEST, MODE_ZEROING);
            MaskReg slot;
            vcmps_eq(slot, lane, static_cast<uint32_t>(block), full);
            vsel(partials, partial, partials, slot);
        }
        RegTensor<float> inverse;
        mem_bar(VST_VLD);
        RegTensor<float> total;
        vcadd(total, partials, full, MODE_ZEROING);
        vmuls(total, total, 1.0f / 5120.0f, full, MODE_ZEROING);
        recipe_rms::inverse_root(inverse, total, full);
        vdup(inverse, inverse, full, POS_LOWEST, MODE_ZEROING);
        for (uint16_t k = 0; k < 80; ++k) {
            RegTensor<float> value, gain;
            RegTensor<bfloat16_t> rounded;
            vlds(rounded, dst, k * 64, UNPK_B16);
            vcvt(value, rounded, full, PART_EVEN);
            vlds(rounded, gamma, k * 64, UNPK_B16);
            vcvt(gain, rounded, full, PART_EVEN);
            vmul(value, value, inverse, full, MODE_ZEROING);
            vmul(value, value, gain, full, MODE_ZEROING);
            vcvt(rounded, value, full, ROUND_R, RS_DISABLE, PART_EVEN);
            vsts(rounded, dst, k * 64, PK_B32, full);
        }
    }
}
}  // namespace

extern "C" __aicore__ __attribute__((always_inline)) void kernel_entry(__gm__ int64_t *args) {
    set_atomic_none();
    set_mask_norm();
    set_vector_mask(-1, -1);
    constexpr int row_tile = 4;
    const int64_t tokens = args[4];
    auto weight_data = data<bfloat16_t>(args, 0);
    auto mix_data = data<float>(args, 1);
    auto input_data = data<float>(args, 2);
    auto output_data = data<bfloat16_t>(args, 3);
    const int64_t stride = static_cast<int64_t>(get_block_num(args)) * row_tile;
    for (int64_t t0 = static_cast<int64_t>(get_block_idx(args)) * row_tile; t0 < tokens; t0 += stride) {
        int rows = tokens - t0 < row_tile ? static_cast<int>(tokens - t0) : row_tile;
        // Two 80-KiB input slots and two 10-KiB output slots precede gamma and premix.
        Vec<bfloat16_t, 1, 5120> weight;
        Premix mix(rows * 4);
        TASSIGN(weight, 184320);
        TASSIGN(mix, 194560);
        GlobalTensor<bfloat16_t, TileShape2D<bfloat16_t, 1, 5120>, BaseShape2D<bfloat16_t, 1, 5120>> gamma(weight_data);
        using MixShape = TileShape2D<float, 1, DYNAMIC>;
        GlobalTensor<float, MixShape, BaseShape2D<float, 1, 32>> premix(mix_data + t0 * 4, MixShape(1, rows * 4));
        TLOAD(weight, gamma);
        TLOAD(mix, premix);
        using InShape = TileShape2D<float, 1, 20480>;
        using OutShape = TileShape2D<bfloat16_t, 1, 5120>;
        Input first(1);
        TASSIGN(first, 0);
        GlobalTensor<float, InShape, BaseShape2D<float, 1, 20480>> first_source(input_data + t0 * 20480);
        TLOAD(first, first_source);
        set_flag(PIPE_MTE2, PIPE_V, EVENT_ID0);
        set_flag(PIPE_MTE3, PIPE_V, EVENT_ID0);
        set_flag(PIPE_MTE3, PIPE_V, EVENT_ID1);
        // V-to-MTE2 credits protect input reuse; MTE3-to-V credits protect output reuse.
        for (int local = 0; local < rows; ++local) {
            int slot = local % 2;
            event_t current = slot == 0 ? EVENT_ID0 : EVENT_ID1;
            event_t next = slot == 0 ? EVENT_ID1 : EVENT_ID0;
            if (local + 1 < rows) {
                if (local > 0) wait_flag(PIPE_V, PIPE_MTE2, next);
                Input pending(1);
                TASSIGN(pending, (1 - slot) * 81920);
                GlobalTensor<float, InShape, BaseShape2D<float, 1, 20480>> source(
                    input_data + (t0 + local + 1) * 20480
                );
                TLOAD(pending, source);
                set_flag(PIPE_MTE2, PIPE_V, next);
            }
            Input input(1);
            Output output(1);
            TASSIGN(input, slot * 81920);
            TASSIGN(output, 163840 + slot * 10240);
            wait_flag(PIPE_MTE2, PIPE_V, current);
            wait_flag(PIPE_MTE3, PIPE_V, current);
            collapse_norm(output.data(), input.data(), weight.data(), mix.data(), local);
            set_flag(PIPE_V, PIPE_MTE2, current);
            set_flag(PIPE_V, PIPE_MTE3, current);
            wait_flag(PIPE_V, PIPE_MTE3, current);
            GlobalTensor<bfloat16_t, OutShape, BaseShape2D<bfloat16_t, 1, 5120>> target(
                output_data + (t0 + local) * 5120
            );
            TSTORE(target, output);
            set_flag(PIPE_MTE3, PIPE_V, current);
        }
        wait_flag(PIPE_V, PIPE_MTE2, EVENT_ID0);
        if (rows > 1) wait_flag(PIPE_V, PIPE_MTE2, EVENT_ID1);
        wait_flag(PIPE_MTE3, PIPE_V, EVENT_ID0);
        wait_flag(PIPE_MTE3, PIPE_V, EVENT_ID1);
        pipe_barrier(PIPE_ALL);
    }
}
