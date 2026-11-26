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

template <unsigned Lanes>
struct SchedulerClusterSsbufRegion;

template <>
struct SchedulerClusterSsbufRegion<3> {
    static constexpr unsigned offset = 2048;
    static constexpr unsigned capacity = 3072;
};

template <>
struct SchedulerClusterSsbufRegion<2> {
    // dav_9201 KFC clears a 32-bit field at 1744, beyond its descriptor.
    static constexpr unsigned offset = 1792;
    static constexpr unsigned capacity = 2048;
    static_assert(offset >= 1748 && offset % 64 == 0);
};
