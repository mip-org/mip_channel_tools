# Channel-shared vcpkg overlay triplet. Shadows vcpkg's builtin
# x64-windows-static-md (same NAME, so a package's `pkg:x64-windows-static-md`
# qualifier and compile.m's VCPKG_TARGET_TRIPLET are unchanged) and adds
# VCPKG_BUILD_TYPE=release plus the MATLAB msvcp140 compatibility define.
#
# Why channel-level (not per-package): vcpkg keys cached binary packages by an
# ABI hash that includes the triplet's content. Pointing every Windows+vcpkg
# package's `vcpkg install ... --overlay-triplets=vcpkg-triplets` at THIS one
# file gives them identical ABIs, so they share the persisted binary cache
# (build-package.yml caches the vcpkg archives dir across runs). A per-package
# copy would risk ABI drift and defeat that reuse.
#
# Release-only: vcpkg builds BOTH debug and release of every port by default,
# but the MEX link only the release libs (built -DCMAKE_BUILD_TYPE=Release), so
# the debug half roughly doubles a cold build for nothing. This only affects
# `vcpkg install`; at consume time CMake's vcpkg toolchain just reads
# installed/<triplet>/lib.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_BUILD_TYPE release)

# _DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR: MSVC >= 14.40 (VS 17.10) makes
# std::mutex's constructor constexpr; objects built that way fail at DLL
# initialization ("Invalid MEX-file ... DLL initialization routine failed")
# when the process already holds a pre-14.40 msvcp140.dll -- and MATLAB
# preloads its own, which is older on every MATLAB the channel tests against.
# Any C++ port with a static std::mutex trips this (pivlab's OpenCV did;
# vtktoolbox and gptoolbox apply the same define in their own CMake stages),
# so cap the requirement here, where it covers every vcpkg-built port. The
# define is the Microsoft-sanctioned workaround and is harmless for C code
# and for processes with a current msvcp140. NOTE: changing this file changes
# every port's vcpkg ABI hash -- existing binary-cache entries are orphaned
# and everything rebuilds once.
set(VCPKG_C_FLAGS "-D_DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR")
set(VCPKG_CXX_FLAGS "-D_DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR")
