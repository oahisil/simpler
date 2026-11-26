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
#include <acl/acl.h>
#include <rt_external_device.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static void check(int rc, const char *name) {
    if (rc != 0) throw std::runtime_error(std::string(name) + " rc=" + std::to_string(rc));
}

static void run(int device, uint32_t blocks, const std::string &group, const std::filesystem::path &out) {
    const bool a5 = std::string(PROBE_PLATFORM) == "a5";
    const uint32_t vectors = blocks * (a5 ? 2 : 1);
    const uint32_t workers = blocks + vectors;
    constexpr uint32_t rounds = 193;  // round 0 is warmup
    if (!blocks || blocks > (a5 ? 32u : 54u) || (group != "gm" && group != "cnt_notify"))
        throw std::invalid_argument("unexpected block count or group");
    if (std::filesystem::exists(out)) throw std::runtime_error("output already exists");
    check(aclrtSetDevice(device), "set device");
    const char *chip = aclrtGetSocName();
    const std::string expected = a5 ? "Ascend950" : "Ascend960";
    if (!chip || std::string(chip).find(expected) != 0) throw std::runtime_error("platform/SOC mismatch");
    int64_t cube_count = 0, vector_count = 0;
    check(aclrtGetDeviceInfo(device, ACL_DEV_ATTR_CUBE_CORE_NUM, &cube_count), "cube count");
    check(aclrtGetDeviceInfo(device, ACL_DEV_ATTR_VECTOR_CORE_NUM, &vector_count), "vector count");
    if (blocks > cube_count || vectors > vector_count) throw std::runtime_error("too few resident cores for SyncAll");
    std::cerr << "soc=" << chip << " device=" << device << " available=" << cube_count << "C+" << vector_count
              << "V launched=" << blocks << "C+" << vectors << "V group=" << group << std::endl;
    aclrtStream stream = nullptr;
    check(aclrtCreateStream(&stream), "create stream");
    void *target = nullptr;
    aclrtCntNotify handle = nullptr;
    rtDevResInfo mapping{0, RT_PROCESS_CP1, RT_RES_TYPE_STARS_CNT_NOTIFY_RECORD, 0, 0};
    if (group == "cnt_notify") {
        check(aclrtCntNotifyCreate(&handle, 0), "aclrtCntNotifyCreate");
        check(aclrtCntNotifyGetId(handle, &mapping.resId), "aclrtCntNotifyGetId");
        uint64_t address = 0;
        uint32_t bytes = 0;
        rtDevResAddrInfo result{&address, &bytes};
        check(rtGetDevResAddress(&mapping, &result), "rtGetDevResAddress(SET/read)");
        if (!address || address % 4 || bytes < 4) throw std::runtime_error("invalid 32-bit SET/read mapping");
        target = reinterpret_cast<void *>(address);
    } else {
        check(aclrtMalloc(&target, 64, ACL_MEM_MALLOC_HUGE_FIRST), "malloc GM");
    }
    const size_t bytes = sizeof(Sample) * workers * rounds;
    void *output = nullptr;
    check(aclrtMalloc(&output, bytes, ACL_MEM_MALLOC_HUGE_FIRST), "malloc records");
    std::vector<Sample> host(workers * rounds);
    std::ofstream csv(out);
    csv.exceptions(std::ios::failbit | std::ios::badbit);
    csv << "scenario,round,slot,core_id,is_vector,begin_tick,end_tick\n";
    for (auto op : {Access::Read, Access::AtomicRead, Access::Write, Access::AtomicWrite}) {
        const bool atomic = op == Access::AtomicRead || op == Access::AtomicWrite;
        if (group == "cnt_notify" && atomic) continue;
        const bool reading = op == Access::Read || op == Access::AtomicRead;
        const std::string mode = (group == "cnt_notify" ? group : (atomic ? "gm_atomic" : "gm_ldst")) +
                                 std::string(reading ? "_read" : "_write");
        check(aclrtMemset(output, bytes, 0xff, bytes), "initialize records");
        launch_probe(
            static_cast<uint32_t *>(target), static_cast<Sample *>(output), blocks, vectors, rounds, op, stream
        );
        check(aclrtSynchronizeStreamWithTimeout(stream, 10000), "sync timed kernel");
        check(aclrtMemcpy(host.data(), bytes, output, bytes, ACL_MEMCPY_DEVICE_TO_HOST), "read samples");
        for (uint32_t r = 0; r < rounds; ++r) {
            uint32_t old_zero = 0;
            for (uint32_t slot = 0; slot < workers; ++slot) {
                const auto &s = host[r * workers + slot];
                if (s.slot != slot || s.round != r || s.is_vector != unsigned(slot >= blocks) || !s.begin ||
                    s.end <= s.begin || (slot == 0 && s.final_value != 1) || s.observed > 1 ||
                    (reading && s.observed != 1))
                    throw std::runtime_error(
                        "invalid worker/value in " + mode + " round=" + std::to_string(r) +
                        " slot=" + std::to_string(slot)
                    );
                old_zero += s.observed == 0;
                csv << mode << ',' << r << ',' << slot << ',' << s.core_id << ',' << s.is_vector << ',' << s.begin
                    << ',' << s.end << '\n';
            }
            if (op == Access::AtomicWrite && old_zero != 1) throw std::runtime_error("invalid exchange history");
        }
        std::cerr << mode << ": validated " << rounds << " rounds x " << workers << " workers\n";
    }
    csv.close();
    check(aclrtFree(output), "free records");
    if (handle) {
        check(rtReleaseDevResAddress(&mapping), "release mapping");
        check(aclrtCntNotifyDestroy(handle), "destroy notification");
    } else {
        check(aclrtFree(target), "free target");
    }
    check(aclrtDestroyStream(stream), "destroy stream");
}

int main(int argc, char **argv) {
    if (argc != 5) {
        std::cerr << "usage: probe DEVICE CUBE_BLOCKS gm|cnt_notify NEW_OUTPUT.csv\n";
        return 2;
    }
    try {
        int device = std::stoi(argv[1]);
        check(aclInit(nullptr), "aclInit");
        try {
            run(device, std::stoul(argv[2]), argv[3], argv[4]);
        } catch (...) {
            aclrtResetDevice(device);
            aclFinalize();
            throw;
        }
        check(aclrtResetDevice(device), "reset device");
        check(aclFinalize(), "finalize");
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "probe failed: " << e.what() << '\n';
        return 1;
    }
}
