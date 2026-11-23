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

#include <gtest/gtest.h>

#include "inner_kernel.h"
#include "runtime_types.h"

uint64_t get_aicore_report_epoch();
#include "lane_loop.h"
#include "host/calendar_image.h"

namespace simpler::percounter {
namespace {

TEST(A6Calendar, PlacesOneMixPairPerClusterAndRejectsAnOversizedVectorCohort) {
    static_assert(PLATFORM_CORES_PER_BLOCKDIM == 2);
    std::vector<TaskInput> tasks(1);
    auto &task = tasks.front();
    task.active_mask = 3;
    task.is_mix = true;
    task.subtask_count = 2;
    task.block_num = 2;
    task.sync_start = true;
    std::vector<CalendarPhase> phases;
    ASSERT_TRUE(plan_calendar_phases(tasks, {}, &phases));
    auto plan = std::make_shared<CalendarPlan>();
    ASSERT_TRUE(build_static_calendar(phases.front().tasks, 2, {}, plan.get()));
    RangeCalendarImage image;
    ASSERT_TRUE(build_range_calendar({{&phases.front(), plan, false}}, {}, tasks.size(), 2, &image));
    EXPECT_EQ(image.load<CalendarRangeHeader>(0).worker_count, 4U);
    EXPECT_FALSE(image.uses_cluster_input_share);

    task.active_mask = 2;
    task.is_mix = false;
    task.subtask_count = 1;
    task.core_type_index = 1;
    task.block_num = 3;
    EXPECT_FALSE(build_static_calendar(tasks, 2, {}, plan.get()));
}

TEST(A6Calendar, BindsReadAndAddToTheSameAlignedWord) {
    static_assert(PLATFORM_CALENDAR_COUNTERS_IN_GM);
    CalendarGmCounter word{};
    const auto address = reinterpret_cast<uint64_t>(&word);
    EXPECT_EQ(calendar_counter_add_command(address), address);
    EXPECT_EQ(percounter_notify_add_address(address), address);
    EXPECT_EQ(calendar_counter_add_command(address + sizeof(uint64_t)), 0U);
}

TEST(A6Calendar, PreservesCompletionCreditsAndDeferBitsInGm) {
    CalendarGmCounter slot{};
    const auto address = reinterpret_cast<uint64_t>(&slot.value);
    const auto command = calendar_counter_add_command(address);
    constexpr uint32_t defer_bit = 1U << 24;
    auto &word = *reinterpret_cast<volatile uint32_t *>(address);

    percounter_calendar_add(command);
    percounter_calendar_set_defer_bit(address, defer_bit);
    percounter_calendar_set_defer_bit(address, defer_bit);
    percounter_calendar_add(command);
    EXPECT_EQ(percounter_notify_query(word), defer_bit | 2U);
}

TEST(A6Calendar, AdmitsAndRetiresWorkersThroughTheGmLifecycleWord) {
    PercounterControl control{};
    CalendarGmCounter gate{};
    auto &word = *reinterpret_cast<volatile uint32_t *>(&gate.value);
    gate.value = 2;
    EXPECT_EQ(percounter_wait_for_admission(&control, 0, word, 0), PercounterAdmission::Start);
    gate.value = 3;
    EXPECT_EQ(percounter_wait_for_admission(&control, 0, word, 2), PercounterAdmission::Retire);
    control.rejected_exit[1] = 3;
    EXPECT_EQ(percounter_wait_for_admission(&control, 1, word, 3), PercounterAdmission::Rejected);
    EXPECT_EQ(control.rejected_exit[1], 0U);
}

}  // namespace
}  // namespace simpler::percounter
