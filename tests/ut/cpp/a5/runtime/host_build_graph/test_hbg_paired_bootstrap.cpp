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

#include "inner_kernel.h"

#include <gtest/gtest.h>

#include <array>
#include "hbg_scheduler_test_support.h"
#include "aicore/resident_scheduler_impl.h"

volatile uint8_t *sim_get_reg_base() {
    alignas(64) static std::array<uint8_t, SIM_REG_TOTAL_SIZE> regs{};
    return regs.data();
}

TEST(HbgPairedBootstrap, LastVectorReleasesFiftyFourSingleVectorClusters) {
    constexpr uint32_t pairs = 54;
    constexpr uint32_t workers = 2 * pairs;
    scheduler_test::FixtureStorage storage(1, workers, 1, SchedulerSpmdRequirements{workers, pairs, 0, 0});
    scheduler_test::BasicGraphBuffer<1> graph(1);
    graph.executable(0, 0);
    auto *base = storage.scheduler_state->base();
    auto *run = storage.run_control;
    storage.prepare_catalog();
    ASSERT_TRUE(scheduler_init_spmd_task_records(base, storage.layout, 59));
    ASSERT_TRUE(storage.prepare_calls(graph.graph()));
    run->scheduler_timeout_cycles = PLATFORM_PROF_SYS_CNT_FREQ;
    auto *gates = scheduler_state_at<SchedulerBootstrapCluster>(base, storage.layout.bootstrap_clusters_offset);

    // With no MIX task the host has already published INIT for every pair.
    for (uint32_t pair = 0; pair < pairs; ++pair) {
        for (uint32_t worker : {pair, pair + pairs}) {
            auto &context = storage.contexts[worker];
            context.cluster_index = pair;
            context.core_type = static_cast<int32_t>(worker == pair ? CoreType::AIC : CoreType::AIV);
            context.type_rank = pair;
            context.cluster_worker_ids[0] = pair;
            context.cluster_worker_ids[1] = pair + pairs;
            storage.test_graphs[worker] = graph.graph();
            auto *local = storage.local_context(&context);
            SchedulerWorkerStats stats{};
            ASSERT_TRUE((bootstrap_ready_graph<false, false>(graph.graph(), base, local, run, workers, &stats, 0)));
            ASSERT_EQ(gates[pair].state, SCHEDULER_BOOTSTRAP_CLUSTER_DONE);
            const uint64_t arrived = 2 * (pair + (worker == pair ? 0u : 1u));
            EXPECT_EQ(run->bootstrap_arrived_count, arrived);
            EXPECT_EQ(run->bootstrap_complete, arrived == workers);
        }
    }
}

TEST(HbgPairedCatalog, FixedOwnersRequireOnePhysicalWave) {
    SchedulerTaskMetadata task{};
    task.flags = SCHEDULER_TASK_EXECUTABLE | SCHEDULER_TASK_SPMD;
    task.active_mask = LaneMask::aiv0();
    for (unsigned blocks : {53u, 54u, 55u, 108u}) {
        task.logical_block_num = blocks;
        EXPECT_EQ(scheduler_is_exact_owner_wave(task, 54), blocks == 54);
    }
    task.active_mask = LaneMask::all();
    task.logical_block_num = 54;
    EXPECT_FALSE(scheduler_is_exact_owner_wave(task, 54));
    task.active_mask = scheduler_cluster_lanes();
    EXPECT_TRUE(scheduler_is_exact_owner_wave(task, 54));
    for (unsigned cluster = 0; cluster != 54; ++cluster)
        EXPECT_EQ(scheduler_fixed_mix_block(cluster, 54, task.active_mask), cluster);
}

TEST(HbgPairedCatalog, PrebindsTwoClosedFullPairCalls) {
    constexpr uint32_t pairs = 54;
    constexpr uint32_t workers = 2 * pairs;
    constexpr LaneMask mask{true, true, false};
    scheduler_test::FixtureStorage storage(3, workers, 2 * pairs, SchedulerSpmdRequirements{workers, pairs, 0, 2});
    scheduler_test::BasicGraphBuffer<4> graph(3);
    graph.executable(0, 1);
    graph.mixed(1, mask);
    graph.mixed(2, mask);
    for (unsigned ref = 0; ref < 3; ++ref) {
        auto &task = storage.metadata[ref];
        task.active_mask = ref == 0 ? LaneMask::aiv0() : mask;
        task.kernel_ids[0] = ref == 0 ? UINT16_MAX : 1;
        task.kernel_ids[1] = 1;
        task.logical_block_num = pairs;
        task.flags = SCHEDULER_TASK_EXECUTABLE | SCHEDULER_TASK_FIXED_OWNER | SCHEDULER_TASK_SPMD;
        if (ref != 0) task.flags |= SCHEDULER_TASK_MIX;
    }
    storage.prepare_catalog();
    void *base = storage.scheduler_state->base();
    ASSERT_TRUE(scheduler_init_spmd_task_records(base, storage.layout, 59));
    ASSERT_TRUE(storage.prepare_calls(graph.graph()));
    const std::vector<SchedulerTaskMetadata> tasks(storage.metadata, storage.metadata + 3);
    SchedulerTaskCatalogPlan plan{};
    ASSERT_TRUE(scheduler_plan_task_catalog(tasks, pairs, storage.catalog_begin, &plan));
    SchedulerDependencyPlan dependencies{{{}, {0}, {1}}, {2}, {false, false, false}};
    scheduler_plan_terminal_mix_calls(dependencies, tasks, plan);
    ASSERT_EQ(plan.header.terminal_mix_refs[0], 1u);
    ASSERT_EQ(plan.header.terminal_mix_refs[1], 2u);
    scheduler_init_task_catalog(base, plan);
    auto *summary = scheduler_state_at<SchedulerGraphSummary>(base, storage.layout.graph_summary_offset);
    summary->topology_layout = SchedulerTopologyLayout::COMPACT_LAUNCH;
    for (unsigned worker = 0; worker < workers; ++worker) {
        auto &context = storage.contexts[worker];
        const unsigned cluster = worker % pairs;
        const unsigned lane = worker / pairs;
        context.core_type = static_cast<int32_t>(lane == 0 ? CoreType::AIC : CoreType::AIV);
        context.cluster_index = context.type_rank = cluster;
        context.cluster_worker_ids[0] = cluster;
        context.cluster_worker_ids[1] = pairs + cluster;
        auto *local = storage.local_context(&context);
        ASSERT_TRUE(scheduler_initialize_order(base, local));
        for (unsigned stage = 0; stage < 2; ++stage) {
            EXPECT_EQ(local->terminal_mix_refs[stage], stage + 1);
            uint64_t index = UINT64_MAX;
            ASSERT_TRUE(
                scheduler_prepared_execution_index(base, local->config.prepared, stage + 1, cluster, lane, &index)
            );
            EXPECT_EQ(local->terminal_mix_calls[stage].execution_index, index);
        }
    }
}

class HbgPairedProtocol : public ::testing::TestWithParam<bool> {};

TEST_P(HbgPairedProtocol, RecordsObservedPhysicalAcksAndAnEmptyThirdSlot) {
    constexpr LaneMask mask{true, true, false};
    const bool peer_order = GetParam();
    scheduler_test::FixtureStorage storage(1, 4, 4, SchedulerSpmdRequirements{4, 2, peer_order, 1, peer_order}, true);
    scheduler_test::BasicGraphBuffer<1> graph(1);
    graph.mixed(0, mask);
    auto &meta = storage.metadata[0];
    meta.active_mask = mask;
    meta.kernel_ids[1] = 1;
    meta.logical_block_num = 2;
    meta.flags = SCHEDULER_TASK_EXECUTABLE | SCHEDULER_TASK_MIX | SCHEDULER_TASK_SPMD;
    if (peer_order) meta.flags |= SCHEDULER_TASK_SYNC_START;
    auto *base = storage.scheduler_state->base();
    ASSERT_TRUE(scheduler_init_spmd_task_records(base, storage.layout, 1));

    auto *block = scheduler_state_at<SchedulerProtocolBlockTrace>(base, storage.layout.protocol.blocks_offset);
    for (unsigned lane : {0u, 1u}) {
        auto &context = storage.contexts[lane];
        context.cluster_worker_ids[0] = 0;
        context.cluster_worker_ids[1] = 1;
        auto *local = storage.local_context(&context);
        ASSERT_TRUE(scheduler_protocol_bind(base, local));
        auto &group = local->mix_groups[0];
        group.task_ref = 0;
        group.generation = 7;
        group.block_idx = 0;
        group.active_mask = mask;
        group.requires_peer_order = peer_order;
        scheduler_protocol_cluster_all(base, local, false, 0);
        ASSERT_NE(block->lanes[lane].arrivals_observed.sequence, 0u);
        if (lane == scheduler_mix_accountant_lane(mask)) {
            scheduler_protocol_cluster_all(base, local, true, 0);
            ASSERT_NE(block->owner.releases_observed.sequence, 0u);
            EXPECT_EQ(block->owner.completion_worker, local->worker_id());
        }
        for (unsigned slot = 0; slot < SUBTASK_SLOT_COUNT; ++slot)
            EXPECT_EQ(block->lanes[lane].arrived_seq[slot], slot < 2 && (peer_order || slot == lane) ? 7u : 0u);
    }
    for (unsigned slot = 0; slot < SUBTASK_SLOT_COUNT; ++slot)
        EXPECT_EQ(block->owner.released_seq[slot], slot < 2 ? 7u : 0u);
}

INSTANTIATE_TEST_SUITE_P(HbgPairedLaneOrder, HbgPairedProtocol, ::testing::Bool());
