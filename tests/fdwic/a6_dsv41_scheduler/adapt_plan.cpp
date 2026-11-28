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
#include <algorithm>
#include <array>
#include <fstream>
#include <map>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "record.h"  // NOLINT(build/include_subdir): shared capture format, supplied by the compiler include path.

namespace {
struct Partition {
    std::array<int32_t, 3> captured_kernels;
    int captured_blocks;
    std::vector<uint64_t> scalars;
    int blocks;
};

}  // namespace

int main(int argc, char **argv) {
    try {
        const bool single_vector = argc == 6 && std::string(argv[3]) == "--single-vector";
        if (!single_vector && argc != 3 && !(argc == 4 && std::string(argv[3]) == "--five-block-t0"))
            throw std::runtime_error("Usage: adapt-plan input output [--five-block-t0 | --single-vector first last]");
        // Task IDs in the pinned 15-task SWA match the source capture, including its prefix.
        std::map<uint64_t, Partition> partitions{
            {12, {{-1, 8, -1}, 64, {72, 80}, 54}},     {22, {{16, 17, 17}, 24, {72, 64, 128, 0, 0}, 48}},
            {30, {{-1, 21, -1}, 64, {72, 8, 80}, 54}}, {33, {{22, 23, 23}, 32, {8192, 72, 80, 80}, 54}},
            {34, {{24, 25, 25}, 32, {80, 128}, 54}},
        };
        if (argc == 4) partitions.emplace(8, Partition{{-1, 6, -1}, 20, {80}, 5});
        std::ifstream input(argv[1], std::ios::binary);
        auto records = dsv41::read_records(input);
        auto slice = single_vector ? dsv41::select_slice(records, std::stoull(argv[4]), std::stoull(argv[5])) :
                                     dsv41::select_slice(records, 8, 34);
        std::map<uint64_t, int32_t> targets;
        if (single_vector) {
            partitions.clear();
            uint64_t task;
            int32_t blocks;
            while (std::cin >> task >> blocks) {
                if (blocks <= 0 || !targets.emplace(task, blocks).second)
                    throw std::runtime_error("Invalid MIX partition");
            }
        }
        for (size_t i = slice.begin; i < slice.end; ++i) {
            auto &h = records[i].header;
            if (h.operation != dsv41::Operation::SUBMIT) continue;
            if (single_vector) {
                if (h.kernels[2] < 0) continue;
                const auto target = targets.find(h.task_id);
                if (target == targets.end() || h.kernels[0] < 0 || h.kernels[1] != h.kernels[2])
                    throw std::runtime_error("MIX partition does not match the original capture");
                h.block_num = target->second;
                h.kernels[2] = -1;
                targets.erase(target);
                continue;
            }
            auto it = partitions.find(h.task_id);
            if (it == partitions.end()) continue;
            const auto &part = it->second;
            if (h.block_num != part.captured_blocks || records[i].scalars != part.scalars ||
                !std::equal(part.captured_kernels.begin(), part.captured_kernels.end(), h.kernels))
                throw std::runtime_error(
                    "Unexpected captured shape or kernels for SWA task " + std::to_string(h.task_id)
                );
            h.block_num = part.blocks;
            h.kernels[2] = -1;  // A6 MIX groups have one vector lane.
            partitions.erase(it);
        }
        if (!partitions.empty() || !targets.empty()) throw std::runtime_error("Missing partition records");
        std::ofstream output(argv[2], std::ios::binary);
        dsv41::write(output, dsv41::record_magic);
        dsv41::write(output, uint64_t{sizeof(dsv41::Operand)});
        for (const auto &record : records) {
            dsv41::write(output, record.header);
            for (const auto &operand : record.tensors)
                dsv41::write(output, operand);
            for (const auto scalar : record.scalars)
                dsv41::write(output, scalar);
            for (const auto dependency : record.dependencies)
                dsv41::write(output, dependency);
        }
        output.close();
        if (!output) throw std::runtime_error("Failed to finish converted plan");
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
