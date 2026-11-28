#include <cassert>
#include "calendar_plan.h"

using namespace simpler::percounter;

static TaskInput task(uint32_t mask, int blocks = 1, bool sync = false) {
    TaskInput input{};
    input.active_mask = mask;
    input.block_num = blocks;
    input.sync_start = sync;
    input.is_mix = __builtin_popcount(mask) > 1;
    input.subtask_count = __builtin_popcount(mask);
    input.core_type_index = mask == 1 ? 0 : 1;
    return input;
}

int main() {
    CalendarPlan plan;
    const uint32_t roles = (1U << PLATFORM_CORES_PER_BLOCKDIM) - 1;
    for (uint32_t pairs : {8U, static_cast<uint32_t>(PLATFORM_MAX_BLOCKDIM)}) {
        auto wide = task(roles, 48);
        assert(build_static_calendar({wide}, pairs, {}, &plan));
        assert(plan.logical_row_by_packed.size() == 48 * PLATFORM_CORES_PER_BLOCKDIM);
        std::vector<CalendarRowCost> measured(plan.logical_row_by_packed.size(), {10, 2});
        assert(build_static_calendar({wide}, pairs, measured, &plan, CalendarBlockOrder::LONGEST_OUTPUT_FIRST));
        assert(build_static_calendar({task(roles, pairs, true)}, pairs, {}, &plan));
        assert(!build_static_calendar({task(roles, pairs + 1, true)}, pairs, {}, &plan));

        int32_t a = 0, branches[] = {1, 2};
        std::vector<TaskInput> diamond{task(1), task(2), task(2), task(1)};
        diamond[1].fanin_ids = diamond[2].fanin_ids = &a;
        diamond[1].fanin_count = diamond[2].fanin_count = 1;
        diamond[3].fanin_ids = branches;
        diamond[3].fanin_count = 2;
        assert(build_static_calendar(diamond, pairs, {}, &plan));
        assert(plan.logical_row_by_packed.size() == 4);
    }
    assert(build_static_calendar({task(7)}, 8, {}, &plan) == (PLATFORM_AIV_CORES_PER_BLOCKDIM == 2));
}
