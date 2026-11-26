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

#pragma once

#include "aicore_scheduler_state.h"
#include "aicpu/device_time.h"
#include "scheduler/scheduler_lifecycle_detail.h"

class AicpuLifecycleDetailScope {
public:
    AicpuLifecycleDetailScope(Runtime *runtime, int32_t thread, AicpuLifecycleDetailKind kind) :
        index_(static_cast<uint32_t>(kind)) {
        if (thread < 0 || thread >= PLATFORM_MAX_AICPU_THREADS) return;
        auto *context = aicore_scheduler_bootstrap_context(runtime);
        auto *base = aicore_scheduler_state_base(context);
        if (base == nullptr) return;
        auto *traces = scheduler_state_at<AicpuThreadLifecycleTrace>(base, context->aicpu_lifecycle_traces_offset);
        detail_ = reinterpret_cast<AicpuLifecycleDetail *>(traces[thread].detail_address);
        if (detail_ != nullptr) detail_->start_cycles[index_] = get_sys_cnt_aicpu();
    }
    ~AicpuLifecycleDetailScope() { close(); }
    void close() {
        if (detail_ == nullptr) return;
        detail_->end_cycles[index_] = get_sys_cnt_aicpu();
        detail_ = nullptr;
    }
    AicpuLifecycleDetailScope(const AicpuLifecycleDetailScope &) = delete;
    AicpuLifecycleDetailScope &operator=(const AicpuLifecycleDetailScope &) = delete;

private:
    AicpuLifecycleDetail *detail_{nullptr};
    uint32_t index_;
};
