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

#include <cstdint>

enum class AicpuLifecycleDetailKind : uint32_t {
    Executor,
    Init,
    LocalSetup,
    SetupWait,
    DiscoveryJoin,
    ConfigWait,
    ReleaseJoin,
    Run,
    Supervise,
    ShutdownWait,
    ExitSignalJoin,
    CompletionJoin,
    Diagnostics,
    TerminalSnapshot,
    TimingPublish,
    Deinit,
    TerminalHandoff,
    CcuReady,
    SwimlaneInit,
    SwimlaneCollect,
    Count,
};

inline constexpr uint32_t AICPU_LIFECYCLE_DETAIL_COUNT = static_cast<uint32_t>(AicpuLifecycleDetailKind::Count);
inline constexpr const char *AICPU_LIFECYCLE_DETAIL_NAMES[] = {
    "executor",         "init",
    "local_setup",      "setup_wait",
    "discovery_join",   "config_wait",
    "release_join",     "run",
    "supervise",        "shutdown_wait",
    "exit_signal_join", "completion_join",
    "diagnostics",      "terminal_snapshot",
    "timing_publish",   "deinit",
    "terminal_handoff", "ccu_ready",
    "swimlane_init",    "swimlane_collect",
};

struct alignas(64) AicpuLifecycleDetail {
    uint64_t start_cycles[AICPU_LIFECYCLE_DETAIL_COUNT];
    uint64_t end_cycles[AICPU_LIFECYCLE_DETAIL_COUNT];
};
