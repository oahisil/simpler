#!/usr/bin/env bash
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
set -euo pipefail
platform=${1:?usage: build.sh a5|a6 PTO_ISA_ROOT OUTPUT_DIR}
isa=$(realpath "${2:?missing PTO-ISA checkout}")
out=$(realpath -m "${3:?missing build directory}")
src=$(cd -- "$(dirname -- "$0")" && pwd)
case "$platform" in
    a5) arch=dav-c310 ;;
    a6) arch=dav-920r1 ;;
    *) echo "Unknown platform: $platform" >&2; exit 2 ;;
esac
sdk=${ASCEND_HOME_PATH:?source the platform CANN environment}
cc="$sdk/bin/ccec"
mkdir -p "$out"
"$cc" -xcce -O2 -std=c++17 -fPIC -DREGISTER_BASE --cce-aicore-arch="$arch" \
    -mllvm -cce-aicore-dcci-insert-for-scalar=false -mllvm -cce-aicore-addr-transform \
    -I"$isa/include" -I"$sdk/include" -I"$sdk/pkg_inc" -I"$sdk/pkg_inc/runtime/runtime" \
    -c "$src/kernel.cpp" -o "$out/kernel.o"
"$cc" --cce-fatobj-link -shared "$out/kernel.o" -o "$out/libsame_address.so" -L"$sdk/lib64" -lruntime
"${CXX:-c++}" -O2 -std=c++17 -DPROBE_PLATFORM="\"$platform\"" -I"$sdk/include" -I"$sdk/pkg_inc" -I"$sdk/pkg_inc/runtime" \
    "$src/host.cpp" -L"$out" -lsame_address -Wl,-rpath,'$ORIGIN' -L"$sdk/lib64" -lascendcl -lruntime \
    -o "$out/probe"
